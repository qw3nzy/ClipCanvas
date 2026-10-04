"""Deterministic tests for ClipCanvas timelines, subtitles, and queue cancellation."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'.runtime'/'deps'))
sys.path.insert(0,str(ROOT/'engine'))
TEST_DATA = tempfile.TemporaryDirectory(prefix='clipcanvas-unit-',dir=ROOT/'.runtime'/'tmp')
os.environ['CLIPCANVAS_DATA'] = TEST_DATA.name
os.environ['CLIPCANVAS_HOST'] = '127.0.0.1'
os.environ.pop('CLIPCANVAS_TOKEN',None)
import clipcanvas_server as server
from clipcanvas_media import captions_for_timeline, normalize_cues, parse_subtitles, subtitle_recommendation, timeline


class TimelineTests(unittest.TestCase):
    def test_final_clip_schedule_and_pause(self):
        plan = timeline(10,16,1,2,True,4)
        ads = [s for s in plan if s['kind']=='advert']
        self.assertEqual([a['output_start'] for a in ads],[2,6])
        self.assertEqual([a['source_position'] for a in ads],[12,15])
        self.assertAlmostEqual(sum(s['duration'] for s in plan),8)
        self.assertAlmostEqual(sum(s['duration'] for s in plan if s['kind']=='source'),6)
    def test_overlap_is_rejected(self):
        with self.assertRaises(ValueError):
            timeline(0,6,4,2,True,4)
    def test_caption_crossing_ad_is_split_and_shifted(self):
        cues = [{'start':1,'end':4,'text':'Диалог','words':[]}]
        result = captions_for_timeline(cues,timeline(0,6,1,2,False,60))
        self.assertEqual([(c['start'],c['end']) for c in result],[(1,2),(3,5)])
    def test_middle_is_final_center(self):
        plan = timeline(10,20,3,5,False,60)
        ad = next(s for s in plan if s['kind']=='advert')
        self.assertEqual(ad['output_start']+ad['duration']/2,sum(s['duration'] for s in plan)/2)


class SubtitleTests(unittest.TestCase):
    def test_vtt_and_ass_parse_to_text(self):
        vtt = 'WEBVTT\n\nfirst\n00:01.000 --> 00:03.000 align:middle\n<b>Привет</b> &amp; мир\n'
        self.assertEqual(parse_subtitles(vtt,'.vtt')[0]['text'],'Привет & мир')
        ass = '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\b1}Привет\\Nмир\n'
        self.assertEqual(parse_subtitles(ass,'.ass')[0]['text'],'Привет\nмир')
    def test_overlap_becomes_one_visible_caption(self):
        result = normalize_cues([{'start':0,'end':2,'text':'А'},{'start':1,'end':3,'text':'Б'}])
        self.assertEqual([(c['start'],c['end'],c['text']) for c in result],[(0,1,'А'),(1,2,'А\nБ'),(2,3,'Б')])
        self.assertTrue(all(a['end']<=b['start'] for a,b in zip(result,result[1:])))
    def test_word_times_survive_normalization(self):
        result = normalize_cues([{'start':0,'end':1,'text':'Да','words':[{'start':.1,'end':.5,'text':'Да'}]}])
        self.assertEqual(result[0]['words'][0]['start'],.1)
        cue = server.Cue.model_validate(result[0])
        self.assertEqual(cue.words[0].text,'Да')
    def test_forced_is_not_recommended_as_full(self):
        tracks = [{'index':5,'language':'rus','supported':True,'forced':True,'default':True},
                  {'index':6,'language':'rus','supported':True,'forced':False,'default':False}]
        self.assertEqual(subtitle_recommendation(tracks,'ru'),6)
        self.assertIsNone(subtitle_recommendation(tracks,'en'))
    def test_buba_speed_limit_and_geometry(self):
        with self.assertRaises(ValueError):
            server.Advertising(enabled=True,asset_id='a'*32,profile='buba',speed=1.3)
        self.assertEqual(server.Advertising(enabled=True,asset_id='a'*32,profile='buba',speed=1.2).speed,1.2)
    def test_word_style_highlights_only_current_word_and_keeps_spaces(self):
        cue = server.Cue(start=0,end=2,text='Первое второе!',words=[
            server.Word(start=.1,end=.7,text='Первое'),server.Word(start=1,end=1.8,text='второе!')])
        events = server.word_caption_events(cue,0,2,'#ffffff','#a3e635')
        active = next(text for start,end,text in events if start<=1.4<end)
        self.assertTrue(active.startswith('Первое '))
        self.assertEqual(active.count('\\1c'),2)
        self.assertNotIn('\\kf',active)
        gap = next(text for start,end,text in events if start<=.8<end)
        self.assertEqual(gap,'Первое второе!')


class QueueTests(unittest.TestCase):
    def test_cancel_queued_immediately_frees_slot(self):
        entered,released = threading.Event(),threading.Event()
        request = server.Source(source_id='a'*32)
        def blocked(job_id,request):
            entered.set()
            released.wait(5)
        def immediate(job_id,request):
            pass
        try:
            a = server.create_job(blocked,request,'test-blocked')['id']
            self.assertTrue(entered.wait(2))
            b = server.create_job(immediate,request,'test-immediate')['id']
            c = server.create_job(immediate,request,'test-immediate')['id']
            server.cancel(b)
            self.assertEqual(server.get_job(b)['status'],'cancelled')
            d = server.create_job(immediate,request,'test-immediate')['id']
            self.assertTrue(d)
            persisted = json.loads((server.DATA/'jobs'/b/'job.json').read_text('utf-8'))
            self.assertEqual(persisted['status'],'cancelled')
            self.assertFalse((server.DATA/'jobs'/b/'job.json.tmp').exists())
        finally:
            released.set()
            deadline = time.monotonic()+3
            while server.FUTURES and time.monotonic()<deadline:
                time.sleep(.01)
        self.assertFalse(server.FUTURES)
    def test_retry_retains_partial_progress_and_request(self):
        request = server.Source(source_id='b'*32,audio_stream_index=2)
        visited = []
        def task(job_id,value):
            visited.append(value.audio_stream_index)
        server.register_job_type('test-retry',server.Source,task)
        job_id = server.create_job(task,request,'test-retry')['id']
        def done():
            deadline = time.monotonic()+2
            while server.get_job(job_id)['status'] in {'queued','running'} and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertEqual(server.get_job(job_id)['status'],'done')
        done()
        server.update(job_id,status='error',completed_parts=[{'index':0,'size':123}])
        self.assertEqual(server.retry(job_id)['id'],job_id)
        done()
        self.assertEqual(visited,[2,2])
        self.assertEqual(server.get_job(job_id)['completed_parts'],[{'index':0,'size':123}])


class ApiSafetyTests(unittest.TestCase):
    def test_project_references_protect_assets(self):
        asset_id = uuid.uuid4().hex
        folder = server.DATA/'uploads'/asset_id
        folder.mkdir()
        (folder/'source.bin').write_bytes(b'fixture-not-decoded')
        server.atomic_json(folder/'asset.json',{'id':asset_id,'kind':'video','name':'fixture',
            'stored_name':'source.bin','inventory_version':2,'duration':10,'audio_streams':[],
            'subtitle_streams':[]})
        project = server.save_project(server.Project(name='Test',source_id=asset_id,settings={'subtitles':{'style':'clean'}},timeline=[{'start':0,'end':10}]))
        self.assertEqual(server.get_project(project['id'])['timeline'][0]['end'],10)
        with self.assertRaises(server.HTTPException) as caught:
            server.delete_asset(asset_id)
        self.assertEqual(caught.exception.status_code,409)
        updated = server.save_project(server.Project(id=project['id'],name='Updated',source_id=asset_id))
        self.assertEqual(updated['id'],project['id'])
        server.delete_project(project['id'])
        self.assertEqual(server.delete_asset(asset_id)['status'],'deleted')
    def test_remote_auth_cookie_origin_and_no_query_token(self):
        from fastapi.testclient import TestClient
        previous_remote,previous_token = server.REMOTE,server.TOKEN
        server.REMOTE,server.TOKEN = True,'unit-test-secret-not-public'
        try:
            with TestClient(server.app,base_url='http://127.0.0.1:8787') as client:
                self.assertEqual(client.get('/api/assets').status_code,401)
                self.assertEqual(client.post('/api/auth',json={'token':'wrong'}).status_code,401)
                self.assertEqual(client.post('/api/auth',json={'token':server.TOKEN}).status_code,200)
                self.assertEqual(client.get('/api/assets').status_code,200)
                self.assertEqual(client.get('/api/assets',headers={'Origin':'https://evil.invalid'}).status_code,403)
                self.assertEqual(client.post('/api/logout').status_code,200)
                self.assertEqual(client.get('/api/assets').status_code,401)
                self.assertEqual(client.get('/api/media/'+'a'*32,params={'token':server.TOKEN}).status_code,401)
                self.assertEqual(client.get('/api/assets',headers={'Authorization':'Bearer '+server.TOKEN}).status_code,200)
        finally:
            server.REMOTE,server.TOKEN = previous_remote,previous_token


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        server.POOL.shutdown(wait=True)
        TEST_DATA.cleanup()
