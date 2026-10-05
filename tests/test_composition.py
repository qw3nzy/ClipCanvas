"""Regression checks for canonical cuts, timing, styling and render parity."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'engine'))
sys.path.insert(0, str(ROOT/'.runtime'/'deps'))
from clipcanvas_composition import composition_timeline, remap_after_cuts, timed_items_for_timeline
from clipcanvas_media import captions_for_timeline, normalize_cues, timeline


class CutTimelineTests(unittest.TestCase):
    def setUp(self):
        self.cuts = [{'start':0,'end':1},{'start':3,'end':4},{'start':7,'end':8}]
        self.plan = composition_timeline(0,8,self.cuts)

    def test_source_is_retained_and_crossing_caption_is_split_with_words(self):
        cue = {'start':.5,'end':5.5,'text':'Проверка границ',
               'words':[{'start':2.5,'end':4.5,'text':'границ'}],
               'appearance':{'color':'#ff0000'},'render_lines':['Проверка','границ']}
        result = captions_for_timeline([cue],self.plan)
        self.assertEqual([(x['start'],x['end']) for x in result],[(0,2),(2,3.5)])
        self.assertEqual([(x['words'][0]['start'],x['words'][0]['end']) for x in result],[(1.5,2),(2,2.5)])
        self.assertEqual(result[1]['appearance'],cue['appearance'])
        self.assertEqual(cue['start'],.5, 'editing must never mutate the source anchor')
        self.assertEqual([(x['start'],x['end']) for x in self.plan],[(1,3),(4,7)])

    def test_banner_crossing_deleted_section_has_output_coordinates(self):
        source = {'id':'brand','start':2,'end':6,'timebase':'source','asset_id':'a'*32}
        result = timed_items_for_timeline([source],self.plan)
        self.assertEqual([(x['start'],x['end']) for x in result],[(1,2),(2,4)])
        self.assertTrue(all(x['timebase']=='output' for x in result))
        self.assertEqual(source['start'],2)

    def test_output_banner_keeps_explicit_final_interval(self):
        result = timed_items_for_timeline([{'start':1.25,'end':20,'timebase':'output'}],self.plan)
        self.assertEqual((result[0]['start'],result[0]['end']),(1.25,5))

    def test_overlapping_cuts_are_merged_and_empty_results_rejected(self):
        plan = composition_timeline(0,10,[{'start':1,'end':4},{'start':3,'end':6}])
        self.assertEqual([(x['start'],x['end']) for x in plan],[(0,1),(6,10)])
        with self.assertRaises(ValueError):
            composition_timeline(0,5,[{'start':0,'end':5}])

    def test_legacy_ad_timeline_is_preserved_without_cuts(self):
        self.assertEqual(composition_timeline(10,16,[],1,2,True,4),timeline(10,16,1,2,True,4))

    def test_ads_are_inserted_after_cuts_and_cues_skip_ads(self):
        plan = composition_timeline(0,8,self.cuts,1,2,False,60)
        ad = next(item for item in plan if item['kind']=='advert')
        self.assertEqual((ad['output_start'],ad['source_position']),(2,4))
        cues = captions_for_timeline([{'start':2,'end':6,'text':'А'}],plan)
        self.assertEqual([(x['start'],x['end']) for x in cues],[(1,2),(3,5)])

    def test_external_output_remap_splits_crossings_and_removes_inside(self):
        items = [{'start':2,'end':6,'text':'Баннер'}, {'start':3.1,'end':3.8,'text':'Удалён'}]
        result = remap_after_cuts(items,self.cuts)
        self.assertEqual([(x['start'],x['end'],x['text']) for x in result],[(1,2,'Баннер'),(2,4,'Баннер')])

    def test_normalization_preserves_per_caption_style_and_wrap(self):
        result = normalize_cues([
            {'start':0,'end':1,'text':'Текст','appearance':{'color':'#ff0000'},'render_lines':['Текст']},
            {'start':1,'end':2,'text':'Текст','appearance':{'color':'#00ff00'},'render_lines':['Текст']}])
        self.assertEqual(len(result),2)
        self.assertEqual(result[1]['appearance']['color'],'#00ff00')
        self.assertEqual(result[0]['render_lines'],['Текст'])


class RenderContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='composition-unit-',dir=ROOT/'.runtime'/'qa')
        os.environ.setdefault('CLIPCANVAS_DATA',cls.directory.name)
        import clipcanvas_server
        cls.server = clipcanvas_server

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def request(self, **changes):
        values = {'source_id':'a'*32,'canvas':{'width':640,'height':360},
                  'video':{'x':0,'y':0,'w':640,'h':360},'background':{'enabled':False},
                  'parts':[{'start':0,'end':8}], 'cuts':[{'start':3,'end':4}]}
        return self.server.Render.model_validate({**values, **changes})

    def test_trial_and_full_export_use_the_same_worker_and_request(self):
        request = self.request()
        with patch.object(self.server,'validate_render',return_value=({},None)), \
             patch.object(self.server,'create_job',return_value={'id':'job'}) as create:
            self.server.sample(request)
            self.server.render(request)
        self.assertEqual(create.call_args_list[0].args[:2],create.call_args_list[1].args[:2])
        self.assertIs(create.call_args_list[0].args[0],self.server.render_job)

    def test_trial_rejects_multiple_parts_without_starting_job(self):
        with patch.object(self.server,'create_job') as create:
            with self.assertRaises(self.server.HTTPException):
                self.server.sample(self.request(parts=[{'start':0,'end':2},{'start':2,'end':4}]))
        create.assert_not_called()

    def test_disabled_layers_retain_editable_settings(self):
        request = self.request(subtitles={'enabled':False,'x':30,'cues':[{'start':0,'end':1,'text':'Сохранить'}]},
                               banners=[{'name':'Имя из редактора.png','enabled':False,'start':1,'end':4,'asset_id':'b'*32}])
        self.assertEqual(request.subtitles.cues[0].text,'Сохранить')
        self.assertEqual(request.banners[0].start,1)
        self.assertEqual(request.banners[0].name,'Имя из редактора.png')
        restored = self.server.Render.model_validate(json.loads(request.model_dump_json()))
        self.assertEqual(restored.model_dump(),request.model_dump())

    def test_explicit_lines_and_overrides_are_written_without_auto_wrap(self):
        request = self.request(subtitles={'enabled':True,'size':40,'x':50,'y':50,'width':80,
            'cues':[{'start':0,'end':2,'text':'Первая вторая','render_lines':['Первая','вторая'],
                     'appearance':{'x':25,'color':'#00ff00','line_spacing':1.5,'background':True,'background_opacity':.25}}]})
        path = Path(self.directory.name)/'checked.ass'
        self.server.write_ass(path,request,request.parts[0])
        value = path.read_text('utf-8-sig')
        self.assertIn(r'\q2\pos(160.000,150.000)',value)
        self.assertIn(r'\q2\pos(160.000,210.000)',value)
        self.assertIn(r'\1a&HBF&',value)
        self.assertIn(r'\1c&H0000ff00&',value)
        self.assertEqual(value.count('Dialogue:'),3)

    def test_chroma_preview_and_export_share_alpha_preserving_filter(self):
        key = self.server.ChromaKey(enabled=True)
        graph = ';'.join(self.server.banner_key_filters('0:v',key,'out','t'))
        self.assertIn('alphaextract',graph)
        self.assertIn('blend=all_mode=multiply',graph)
        self.assertIn('alphamerge[out]',graph)

    def test_browser_font_metrics_convert_ass_size_without_changing_line_spacing(self):
        request = self.request(subtitles={'enabled':True,'size':20,'x':50,'y':50,'line_spacing':1.4,
            'cues':[{'start':0,'end':2,'text':'Первая вторая','render_lines':['Первая','вторая'],
                     'render_font_size':22.34375,'render_y_offset':-1.41796875}]})
        path = Path(self.directory.name)/'font-metrics.ass'
        self.server.write_ass(path,request,request.parts[0])
        value = path.read_text('utf-8-sig')
        self.assertIn(r'\fs22.34375\b1',value)
        self.assertIn(r'\pos(320.000,164.582)',value)
        self.assertIn(r'\pos(320.000,192.582)',value)


if __name__ == '__main__':
    unittest.main()
