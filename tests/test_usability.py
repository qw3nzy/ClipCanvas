"""Warnings, non-destructive cleanup, and speech pauses on isolated data."""
import concurrent.futures
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'.runtime/deps'))
sys.path.insert(0, str(ROOT/'engine'))
TEST_DATA = tempfile.TemporaryDirectory(prefix='usability-', dir=ROOT/'.runtime/qa')
os.environ['CLIPCANVAS_DATA'] = TEST_DATA.name
import clipcanvas_server as server
from clipcanvas_media import captions_for_timeline
from clipcanvas_composition import composition_timeline


class WarningTests(unittest.TestCase):
    def request(self, **extra):
        return server.Render.model_validate({'source_id':'a'*32, 'canvas':{'width':240,'height':240},
            'video':{'x':0,'y':0,'w':240,'h':240}, 'background':{'enabled':False},
            'parts':[{'start':0,'end':2}], 'encoder':'cpu', **extra})

    def test_all_delivery_requirements_are_warnings_and_settings_survive(self):
        request = self.request(delivery={'max_duration':1,'require_banner':True,'require_subtitles':True})
        source = {'duration':2, 'audio_streams':[]}
        before = request.model_dump()
        with patch.object(server,'asset', return_value=source):
            server.validate_render(request)
            result = server.studio.preflight(before, (source,None))
        self.assertGreaterEqual(len(result['issues']),3)
        self.assertTrue(all(issue['severity']=='warning' for issue in result['issues']))
        self.assertEqual(request.model_dump(),before)

    def test_outside_banner_and_high_buba_speed_are_renderable(self):
        request = self.request(banners=[{'enabled':True,'asset_id':'b'*32,'geometry':{'x':-300,'y':0,'w':50,'h':50},'end':7}],
            advertising={'enabled':True,'asset_id':'c'*32,'profile':'buba','speed':1.3,'start':1,'repeat':False})
        def asset(value, kind=None):
            return {'duration':2,'audio_streams':[],'width':240,'height':240,'audio':False,'media_type':'image'}
        with patch.object(server,'asset',side_effect=asset), patch.dict(server.studio.c,{'asset':asset}):
            source, ad = server.validate_render(request)
            result = server.studio.preflight(request.model_dump(),(source,ad))
        self.assertTrue(any('1,2' in issue['message'] for issue in result['issues']))
        self.assertTrue(any('обрезается' in issue['message'] for issue in result['issues']))

    def test_all_cut_frames_remain_a_technical_error(self):
        request = self.request(cuts=[{'start':0,'end':2}])
        with patch.object(server,'asset',return_value={'duration':2,'audio_streams':[]}):
            with self.assertRaises(server.HTTPException):
                server.validate_render(request)

    def test_output_duration_check_is_a_warning_and_keeps_the_mp4(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.runtime/qa') as directory:
            data = Path(directory)
            target = data/'jobs'/'target'
            checker = data/'jobs'/'checker'
            target.mkdir(parents=True)
            checker.mkdir(parents=True)
            output = target/'part-01.mp4'
            output.write_bytes(b'mp4')
            (checker/'quality.log').write_text('', 'utf-8')
            captured = {}
            def update(_id, **values):
                captured.update(values)
            with patch.object(server.studio, 'data', data), patch.dict(server.studio.c, {
                'get_job':lambda _id: {'id':'target','files':[{'name':output.name,'duration':1}]},
                'probe':lambda _path: {'duration':2,'width':240,'height':240,'fps':24,'audio':True},
                'update':update, 'run_ffmpeg':lambda *args, **kwargs: None,
            }):
                server.studio.output_job('checker', SimpleNamespace(job_id='target',filename=output.name))
            self.assertTrue(output.is_file())
            self.assertEqual(captured['issues'][0]['severity'],'warning')

    def test_burned_ass_clips_text_at_the_same_word_boundaries_as_srt(self):
        request = self.request(subtitles={'enabled':True, 'cues':[
            {'start':0,'end':3,'text':'удалить сохранить','words':[
                {'start':.1,'end':.8,'text':'удалить'},{'start':2.1,'end':2.8,'text':'сохранить'}]}]})
        with tempfile.TemporaryDirectory(dir=ROOT/'.runtime/qa') as directory:
            path = Path(directory)/'captions.ass'
            server.write_ass(path,request,server.Part(start=2,end=3))
            content = path.read_text('utf-8-sig')
        self.assertIn('сохранить',content)
        self.assertNotIn('удалить',content)
        self.assertEqual(request.subtitles.cues[0].text,'удалить сохранить')

    def test_resume_keeps_the_actual_file_size_warning_without_rendering_again(self):
        request = self.request()
        key = 'f'*32
        issue = {'severity':'warning','part':1,'code':'file_size','message':'MP4 сохранён'}
        output = {'index':0,'name':'part-01.mp4','size':3}
        files = [{'name':'part-01.mp4'}]
        job = {'issues':[issue],'completed_parts':[{'index':0,'size':3,'files':files,'output':output}]}
        with tempfile.TemporaryDirectory(dir=ROOT/'.runtime/qa') as directory:
            data = Path(directory)
            folder = data/'jobs'/key
            folder.mkdir(parents=True)
            (folder/'part-01.mp4').write_bytes(b'mp4')
            with patch.object(server,'DATA',data),patch.object(server,'JOBS',{key:job}), \
                 patch.object(server,'validate_render',return_value=({},None)), \
                 patch.object(server.studio,'preflight',return_value={'issues':[]}), \
                 patch.object(server,'cancelled',return_value=False), \
                 patch.object(server,'update',side_effect=lambda _id,**values: job.update(values)), \
                 patch.object(server,'render_segment') as render:
                server.render_job(key,request)
        render.assert_not_called()
        self.assertEqual(job['issues'],[issue])


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT/'.runtime/qa',prefix='history-')
        data = Path(self.directory.name)
        for name in ('uploads','projects','jobs'):
            (data/name).mkdir()
        history = {'revision':0,'jobs':[],'assets':[],'opened_videos':[{'id':'old'}],'project_operations':[{'id':'old'}]}
        self.jobs = {}
        self.patches = [patch.object(server,'DATA',data),patch.object(server,'HISTORY_PATH',data/'history-state.json'),
                        patch.object(server,'HISTORY',history),patch.object(server,'JOBS',self.jobs)]
        for item in self.patches: item.start()
        self.data = data

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.directory.cleanup()

    def add_asset(self):
        value = 'a'*32
        folder = self.data/'uploads'/value
        folder.mkdir()
        (folder/'source.png').write_bytes(b'fixture')
        server.atomic_json(folder/'asset.json', {'id':value,'kind':'banner','media_type':'image','name':'QA',
                                                'stored_name':'source.png','width':100,'height':40})
        return value

    def test_clear_hides_inventory_but_preserves_project_media_results_and_restart_state(self):
        value = self.add_asset()
        server.save_project(server.Project(name='Сохранить',banner_id=value))
        self.jobs['b'*32] = {'id':'b'*32,'status':'done','type':'render','files':[{'name':'part.mp4','url':'/result'}]}
        result = server.clear_history()
        self.assertEqual(server.list_assets()['assets'],[])
        self.assertEqual(server.list_jobs()['jobs'],[])
        self.assertEqual(server.history_summary()['project_operations'],[])
        self.assertEqual(server.history_summary()['opened_videos'],[])
        self.assertEqual(len(server.list_projects()['projects']),1)
        self.assertTrue(server.asset(value)['path'].exists())
        self.assertEqual(len(server.saved_results()['jobs']),1)
        restored = json.loads(server.HISTORY_PATH.read_text('utf-8'))
        with patch.object(server,'HISTORY',restored):
            self.assertEqual(server.list_assets()['assets'],[])
            self.assertEqual(server.list_jobs()['jobs'],[])
        self.assertEqual(result['revision'],1)
        server.history_event('project_operations', {'id':'new'})
        self.assertEqual(len(server.history_summary()['project_operations']),1)

    def test_running_family_is_protected_and_new_jobs_are_visible(self):
        self.jobs.update({key:{'id':key,'status':status,'type':kind,**fields} for key,status,kind,fields in [
            ('a'*32,'done','batch',{'approval_job':'b'*32}),
            ('b'*32,'running','batch-approval',{'parent_id':'a'*32,'internal':True}),
            ('c'*32,'done','render',{'parent_id':'a'*32,'internal':True}),
            ('d'*32,'error','render',{})]})
        result = server.clear_history()
        self.assertEqual(result['protected_jobs'],3)
        self.assertEqual([job['id'] for job in server.list_jobs()['jobs']],['a'*32])
        self.jobs['e'*32] = {'id':'e'*32,'type':'render','status':'done'}
        self.assertEqual(len(server.list_jobs()['jobs']),2)
        server.history_reveal('jobs','d'*32)
        self.assertFalse(server.history_hidden('jobs','d'*32))

    def test_concurrent_retry_enqueues_only_once(self):
        key = 'f'*32
        self.jobs[key] = {'id':key,'status':'error','type':'preview','request':{'source_id':'a'*32}}
        (self.data/'jobs'/key).mkdir()
        with patch.object(server,'enqueue') as enqueue:
            def retry():
                try: return server.retry(key)
                except server.HTTPException as error: return error.status_code
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: retry(), range(2)))
        self.assertEqual(enqueue.call_count,1)
        self.assertIn(409,results)

    def test_delete_project_preserves_its_media_and_other_projects(self):
        value = self.add_asset()
        project = server.save_project(server.Project(name='Удалить',banner_id=value))
        other = server.save_project(server.Project(name='Оставить',banner_id=value))
        server.delete_project(project['id'])
        self.assertFalse((self.data/'projects'/(project['id']+'.json')).exists())
        self.assertTrue((self.data/'projects'/(other['id']+'.json')).exists())
        self.assertTrue(server.asset(value)['path'].is_file())

    def test_delete_prepared_project_is_blocked_while_approval_is_active(self):
        project = server.save_project(server.Project(name='Нужен пакету'))
        self.jobs.update({
            'a'*32:{'id':'a'*32,'type':'batch','status':'done','approval_job':'b'*32,
                    'batch_items':[{'project_id':project['id']}]},
            'b'*32:{'id':'b'*32,'type':'batch-approval','status':'running','parent_id':'a'*32},
        })
        with self.assertRaises(server.HTTPException) as error:
            server.delete_project(project['id'])
        self.assertEqual(error.exception.status_code,409)
        self.assertTrue((self.data/'projects'/(project['id']+'.json')).is_file())


class SpeechTimingTests(unittest.TestCase):
    def test_pause_and_sentence_split_do_not_leave_caption_in_silence(self):
        def word(start,end,text): return SimpleNamespace(start=start,end=end,word=text,probability=.95)
        words = [word(.1,.3,' Привет'),word(.31,.6,' мир.'),word(1.4,1.8,' После'),word(1.82,2.2,' паузы')]
        segment = SimpleNamespace(start=0,end=3,text='',words=words)
        with patch.object(server,'cancelled',return_value=False),patch.object(server,'update'):
            cues = server.group_words([segment],'test',3)
        self.assertEqual([(cue['start'],cue['end']) for cue in cues],[(.1,.6),(1.4,2.2)])
        self.assertEqual([cue['text'] for cue in cues],['Привет мир.','После паузы'])
        self.assertTrue(all(cue['manual'] is False for cue in cues))

    def test_cut_and_advert_remove_deleted_words_and_shift_remaining_words(self):
        cue = {'start':0,'end':4,'text':'один удалить три четыре','words':[
            {'start':.1,'end':.6,'text':'один'},{'start':1.1,'end':1.6,'text':'удалить'},
            {'start':2.1,'end':2.6,'text':'три'},{'start':3.1,'end':3.6,'text':'четыре'}]}
        plan = composition_timeline(0,4,[{'start':1,'end':2}],1,1,False,60)
        cues = captions_for_timeline([cue],plan)
        self.assertNotIn('удалить',' '.join(row['text'] for row in cues))
        words = [word for row in cues for word in row['words']]
        self.assertEqual([round(word['start'],1) for word in words],[.1,2.1,3.1])


if __name__ == '__main__':
    unittest.main()
