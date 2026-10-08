"""Automatic editing is cancellable, evidence-gated and leaves the source project intact."""
from copy import deepcopy
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'engine'), str(ROOT/'.runtime/deps')]
TEST_DATA = tempfile.TemporaryDirectory(prefix='automatic-unit-', dir=ROOT/'.runtime/tmp')
previous_data = os.environ.get('CLIPCANVAS_DATA')
os.environ['CLIPCANVAS_DATA'] = TEST_DATA.name
import clipcanvas_server as server
if previous_data is None:
    os.environ.pop('CLIPCANVAS_DATA', None)
else:
    os.environ['CLIPCANVAS_DATA'] = previous_data

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from clipcanvas_automatic import (Automatic, AutomaticEditor, AutomaticOptions, register_automatic_routes,
                                 safe_boundary_cuts, subtitle_regions)


def payload():
    return {'source_id': 'a'*32, 'canvas': {'width': 640, 'height': 360},
            'video': {'x': 0, 'y': 0, 'w': 640, 'h': 360}, 'background': {},
            'parts': [{'start': 0, 'end': 100}], 'banners': [{
                'id': 'test-banner', 'enabled': True, 'asset_id': 'b'*32, 'start': 20, 'end': 30,
                'geometry': {'x': 10, 'y': 10, 'w': 120, 'h': 40, 'rotation': 15}}]}


class FakeStudio:
    def __init__(self, update):
        self.update = update
        self.layouts = []

    def boundaries_job(self, job_id, request):
        self.update(job_id, boundaries=[{'kind': 'intro', 'start': 0, 'end': 4, 'confidence': .95,
                                       'reason': 'Заставка', 'frame_evidence': [{}, {}]},
                                      {'kind': 'outro', 'start': 96, 'end': 100, 'confidence': .6,
                                       'reason': 'Неуверенно', 'evidence': [{'text': 'Конец'}]}])

    def review_job(self, job_id, request):
        self.update(job_id, changes=[{'index': 0, 'cue': {'start': 20, 'end': 22, 'text': 'Привет!', 'words': []}}])

    def banner_job(self, job_id, request):
        self.layouts.append(request)
        self.update(job_id, geometry={**request.original.model_dump(), 'x': 420, 'y': 24},
                    protected_regions=[p.model_dump() for p in request.protected_regions])


class AutomaticTests(unittest.TestCase):
    def test_explicit_center_skips_model_preserves_audio_speed_and_removes_motion(self):
        original=payload();original['banners'][0].update(instructions='Баннер по центру холста.',volume=.8,speed=2,
            motion=[{'time':0,'x':10,'y':10,'scale':2,'opacity':.5}])
        snapshot=deepcopy(original)
        self.editor.job('job',Automatic(project=original,options=AutomaticOptions(subtitles=False,
            remove_intro=False,remove_outro=False,remove_background=False,banners=True)))
        self.assertEqual(self.studio.layouts,[])
        banner=self.jobs['job']['project']['banners'][0]
        self.assertEqual((banner['geometry']['x'],banner['geometry']['y']),(260,160))
        self.assertEqual((banner['volume'],banner['speed']),(.8,2))
        self.assertTrue(all(point['x'] is None and point['y'] is None and point['scale']==1 for point in banner['motion']))
        self.assertEqual(original,snapshot)
        self.assertTrue(self.jobs['job']['automatic_report'][0]['fixed_position'])

    def test_scheduled_output_banner_is_analyzed_without_losing_schedule(self):
        original=payload()
        original['banners'][0].update(timebase='output', schedule={'mode':'full'},
            motion=[{'time':0,'x':10,'y':10}])
        self.editor.job('job', Automatic(project=original, options=AutomaticOptions(subtitles=False,
            remove_intro=False,remove_outro=False,remove_background=False,banners=True)))
        self.assertEqual((self.studio.layouts[0].start,self.studio.layouts[0].end),(0,100))
        result=self.jobs['job']['project']['banners'][0]
        self.assertEqual(result['schedule']['mode'],'full')
        self.assertEqual((result['motion'][0]['x'],result['motion'][0]['y']),(420,24))

    def test_locked_banner_keeps_position_during_automatic_layout(self):
        original=payload();original['banners'][0]['locked']=True
        self.editor.job('job', Automatic(project=original,options=AutomaticOptions(subtitles=False,
            remove_intro=False,remove_outro=False,remove_background=False,banners=True)))
        self.assertEqual(self.studio.layouts,[])
        self.assertEqual(self.jobs['job']['project']['banners'][0]['geometry']['x'],10)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=ROOT/'.runtime/tmp')
        self.addCleanup(self.folder.cleanup)
        self.data = Path(self.folder.name)
        (self.data/'jobs'/'job').mkdir(parents=True)
        self.jobs = {'job': {}}
        self.cancelled = False
        self.source = {'duration': 100, 'path': 'source.mp4'}
        self.context = {'DATA': self.data, 'Render': server.Render, 'Banner': server.Banner,
            'Transcribe': server.Transcribe, 'Cancelled': server.Cancelled,
            'cancelled': lambda _id: self.cancelled, 'update': self.update,
            'get_job': lambda job_id: deepcopy(self.jobs[job_id]),
            'validate_render': lambda request: (self.source, None),
            'audio_index': lambda source, index: 1, 'asset': self.asset,
            'transcribe_job': self.transcribe, 'write_srt': server.write_srt,
            'file_record': server.file_record, 'LOCK': threading.RLock(),
            'identifier': server.identifier, 'atomic_json': server.atomic_json,
            'register_job_type': lambda *args: None, 'create_job': lambda *args: {'id': 'queued'}}
        self.studio = FakeStudio(self.update)
        self.editor = AutomaticEditor(self.context, self.studio)

    def asset(self, asset_id, kind=None):
        if asset_id == 'a'*32 and kind == 'video':
            return self.source
        if asset_id == 'b'*32 and kind == 'banner':
            return {'path': 'banner.png', 'kind': 'banner'}
        raise HTTPException(404, 'Материал не найден.')

    def update(self, job_id, **values):
        self.jobs[job_id].update(deepcopy(values))

    def transcribe(self, job_id, request):
        self.assertEqual(request.model, 'large-v3')
        self.update(job_id, cues=[{'start': 20, 'end': 22, 'text': 'привет', 'words': []}])

    def test_complete_pipeline_is_atomic_and_preserves_rotation(self):
        original = payload()
        snapshot = deepcopy(original)
        key = {'chromakey': {'enabled': True, 'color': '#0000ff', 'similarity': .08, 'blend': .02},
               'confidence': .98, 'reason': 'Однотонный синий фон'}
        with patch('clipcanvas_composition.estimate_banner_key', return_value=key):
            self.editor.job('job', Automatic(project=original, options=AutomaticOptions(subtitles=True), client_snapshot='revision-1'))
        result = self.jobs['job']['project']
        self.assertEqual(original, snapshot)
        self.assertEqual(result['cuts'], [{'start': 0, 'end': 4}])
        self.assertEqual(result['banners'][0]['geometry']['rotation'], 15)
        self.assertEqual(result['banners'][0]['geometry']['x'], 420)
        self.assertEqual(result['banners'][0]['chromakey']['color'], '#0000ff')
        self.assertEqual(result['subtitles']['cues'][0]['text'], 'Привет!')
        self.assertEqual(self.jobs['job']['client_snapshot'], 'revision-1')
        self.assertTrue(self.studio.layouts[0].protected_regions)
        self.assertIn('Привет!', (self.data/'jobs/job/subtitles.srt').read_text('utf-8'))
        self.assertTrue(any(row['step'] == 'outro' and row['status'] == 'skipped' for row in self.jobs['job']['automatic_report']))

    def test_cancellation_publishes_no_partial_project(self):
        def cancel_after_transcript(job_id, request):
            self.transcribe(job_id, request)
            self.cancelled = True
        self.context['transcribe_job'] = cancel_after_transcript
        with self.assertRaises(server.Cancelled):
            self.editor.job('job', Automatic(project=payload(), options=AutomaticOptions(subtitles=True)))
        self.assertIsNone(self.jobs['job']['project'])

    def test_correction_failure_keeps_recognition_and_explains_failure(self):
        with patch.object(self.studio, 'review_job', side_effect=RuntimeError('Модель не установлена')):
            self.editor.job('job', Automatic(project=payload(), options=AutomaticOptions(subtitles=True,
                banners=False, remove_background=False, remove_intro=False, remove_outro=False)))
        self.assertEqual(self.jobs['job']['project']['subtitles']['cues'][0]['text'], 'привет')
        self.assertTrue(any(row['step'] == 'subtitle-correction' and 'не установлена' in row['reason']
                            for row in self.jobs['job']['automatic_report']))

    def test_failed_boundaries_and_layout_keep_existing_edits(self):
        original = payload()
        original['cuts'] = [{'start': 50, 'end': 55}]
        with patch.object(self.studio, 'boundaries_job', side_effect=ValueError('Нет подтверждений')), \
             patch.object(self.studio, 'banner_job', side_effect=RuntimeError('Модель недоступна')):
            self.editor.job('job', Automatic(project=original, options=AutomaticOptions(remove_background=False)))
        result = self.jobs['job']['project']
        self.assertEqual(result['cuts'], original['cuts'])
        self.assertEqual(result['banners'][0]['geometry']['x'], 10)
        self.assertEqual(len(self.jobs['job']['automatic_report']), 2)

    def test_existing_chroma_key_is_kept(self):
        original = payload()
        original['banners'][0]['chromakey'] = {'enabled': True, 'color': '#ff0000'}
        with patch('clipcanvas_composition.estimate_banner_key') as estimate:
            self.editor.job('job', Automatic(project=original, options=AutomaticOptions(
                banners=False, remove_intro=False, remove_outro=False)))
        estimate.assert_not_called()
        self.assertEqual(self.jobs['job']['project']['banners'][0]['chromakey']['color'], '#ff0000')

    def test_never_deletes_whole_part_or_unproven_content(self):
        project = payload()
        project['parts'] = [{'start': 0, 'end': 4}]
        boundaries = [{'kind': 'intro', 'start': 0, 'end': 4, 'confidence': .99, 'evidence': [{}]},
                      {'kind': 'outro', 'start': 90, 'end': 100, 'confidence': .99, 'evidence': []}]
        cuts, report = safe_boundary_cuts(project, boundaries, AutomaticOptions(), 100)
        self.assertEqual(cuts, [])
        self.assertEqual([row['status'] for row in report], ['skipped', 'skipped'])

    def test_caption_protection_covers_both_sides_of_custom_vertical_center(self):
        subtitles = server.Subtitles(enabled=True, cues=[{'start': 20, 'end': 22, 'text': 'Один\nДва',
            'appearance': {'x': 30, 'y': 50, 'size': 40, 'line_spacing': 1.5},
            'render_lines': ['Один', 'Два'], 'render_y_offset': 8}]).model_dump()
        regions = subtitle_regions(subtitles, {'width': 640, 'height': 360}, 20, 30)
        self.assertEqual(len(regions), 1)
        self.assertLess(regions[0]['y'], 180-50)
        self.assertGreater(regions[0]['y']+regions[0]['h'], 180+50)
        self.assertEqual(subtitle_regions(subtitles, {'width': 640, 'height': 360}, 30, 40), [])
        subtitles['cues'][0].update(text='Один', render_lines=['Один'])
        subtitles['cues'][0]['appearance'].update(background=True, line_spacing=3)
        background = subtitle_regions(subtitles, {'width': 640, 'height': 360}, 20, 30)[0]
        self.assertLess(background['y'], 180-60)
        self.assertGreater(background['y']+background['h'], 180+60)

    def test_render_accepts_visible_rotated_layer_beyond_unrotated_edge(self):
        original = payload()
        original['banners'][0]['geometry'] = {'x': -100, 'y': 80, 'w': 80, 'h': 200, 'rotation': 90}
        request = server.Render.model_validate(original)
        self.assertEqual(request.banners[0].geometry.rotation, 90)
        original['banners'][0]['geometry'] = {'x': -70, 'y': 80, 'w': 80, 'h': 20, 'rotation': 90}
        self.assertEqual(server.Render.model_validate(original).banners[0].geometry.x, -70)

    def test_real_server_export_preserves_rotation_key_and_audio(self):
        import av
        import numpy as np
        source_path, banner_path = self.data/'source.mp4', self.data/'banner.png'
        made = subprocess.run([server.FFMPEG, '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'lavfi', '-i', 'color=c=black:s=240x300:r=24:d=1', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000:duration=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-shortest', str(source_path)], capture_output=True, timeout=30)
        self.assertEqual(made.returncode, 0, made.stderr.decode(errors='replace'))
        pixels = np.full((40, 80, 4), [0, 0, 255, 255], dtype=np.uint8)
        pixels[10:30, 25:55] = [255, 0, 0, 255]
        pixels[:8, :8] = [255, 255, 255, 0]
        def chunk(kind, value):
            return struct.pack('>I', len(value))+kind+value+struct.pack('>I', zlib.crc32(kind+value))
        banner_path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB', 80, 40, 8, 6, 0, 0, 0))+
            chunk(b'IDAT', zlib.compress(b''.join(b'\0'+row.tobytes() for row in pixels)))+chunk(b'IEND', b''))
        request = server.Render.model_validate({'source_id': 'a'*32, 'canvas': {'width': 240, 'height': 300},
            'video': {'x': 0, 'y': 0, 'w': 240, 'h': 300}, 'background': {'enabled': False, 'color': '#000000'},
            'parts': [{'start': 0, 'end': 1}], 'fps': 24, 'encoder': 'cpu', 'banners': [{
                'enabled': True, 'asset_id': 'b'*32, 'start': 0, 'end': 1,
                'geometry': {'x': 60, 'y': 90, 'w': 80, 'h': 40, 'rotation': 90},
                'chromakey': {'enabled': True, 'color': '#0000ff', 'similarity': .1, 'blend': .02}}]})
        source = {**server.probe(source_path), 'path': source_path}
        with patch.object(server, 'DATA', self.data), patch.object(server, 'JOBS', self.jobs), \
             patch.object(server, 'asset', return_value={'path': banner_path}):
            server.render_segment('job', request, source,
                {'start': 0, 'end': 1, 'duration': 1, 'output_start': 0}, 'test.mp4', 0, 100)
        with av.open(str(self.data/'jobs/job/test.mp4')) as container:
            self.assertEqual(len(container.streams.audio), 1)
            frames = list(container.decode(video=0))
        self.assertEqual(len(frames), 24)
        image = frames[12].to_ndarray(format='rgb24')
        self.assertGreater(image[110, 100, 0], 220)
        self.assertGreater(image[122, 100, 0], 180, 'Rotated red strip extends vertically')
        self.assertLess(int(image[110, 113].max()), 20, 'Blue key reveals underlying video')
        self.assertLess(int(image[75, 100].max()), 20, 'Expanded rotated corners stay transparent')

    def test_template_round_trip_and_asset_kind_validation(self):
        app = FastAPI()
        register_automatic_routes(app, self.context, self.studio)
        client = TestClient(app)
        template = {'name': ' Синий баннер ', 'asset_id': 'b'*32, 'width': 640, 'height': 360,
                    'geometry': payload()['banners'][0]['geometry'], 'chromakey': {'enabled': True, 'color': '#0000ff'}}
        response = client.post('/api/banner-templates', json=template)
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved['name'], 'Синий баннер')
        self.assertEqual(saved['geometry']['rotation'], 15)
        self.assertEqual(client.get('/api/banner-templates').json()['templates'], [saved])
        self.assertEqual(client.post('/api/banner-templates', json={**template, 'asset_id': 'a'*32}).status_code, 404)
        self.assertEqual(client.post('/api/banner-templates', json={**template, 'name': ' '}).status_code, 422)
        self.assertEqual(client.delete('/api/banner-templates/'+saved['id']).status_code, 200)
        self.assertEqual(client.get('/api/banner-templates').json()['templates'], [])

    def test_video_template_preserves_playback_and_validates_offset(self):
        original = self.context['asset']
        self.context['asset'] = lambda asset_id, kind=None: ({**original(asset_id, kind),
            'media_type': 'video', 'duration': 2.0} if asset_id == 'b'*32 else original(asset_id, kind))
        app = FastAPI()
        register_automatic_routes(app, self.context, self.studio)
        client = TestClient(app)
        template = {'name': 'Motion banner', 'asset_id': 'b'*32, 'width': 640, 'height': 360,
                    'geometry': payload()['banners'][0]['geometry'], 'offset': .75, 'volume': .35, 'loop': False, 'speed':2}
        response = client.post('/api/banner-templates', json=template)
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual((saved['media_type'], saved['media_duration']), ('video', 2.0))
        self.assertEqual((saved['offset'], saved['volume'], saved['loop']), (.75, .35, False))
        self.assertEqual(saved['speed'],2)
        self.assertEqual(client.get('/api/banner-templates').json()['templates'], [saved])
        response=client.put('/api/banner-templates/'+saved['id'],json={**template,'volume':1,'speed':.5})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['id'],saved['id'])
        self.assertEqual((response.json()['volume'],response.json()['speed']),(1,.5))
        self.assertEqual(len(client.get('/api/banner-templates').json()['templates']),1)
        self.assertEqual(client.put('/api/banner-templates/'+'f'*32,json=template).status_code,404)
        self.assertEqual(client.post('/api/banner-templates',json={**template,'speed':5}).status_code,422)
        self.assertEqual(client.post('/api/banner-templates', json={**template, 'offset': 2}).status_code, 422)

    def test_automatic_api_rejects_invalid_project_before_queue(self):
        app = FastAPI()
        register_automatic_routes(app, self.context, self.studio)
        client = TestClient(app)
        self.assertEqual(client.post('/api/ai/automatic', json={'project': {}}).status_code, 422)
        response = client.post('/api/ai/automatic', json={'project': payload(), 'client_snapshot': '123'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'id': 'queued'})


if __name__ == '__main__':
    unittest.main()
