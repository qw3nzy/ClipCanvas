"""Source blur validation, timing, and saved project compatibility."""
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'engine'),str(ROOT/'.runtime/deps')]
from clipcanvas_video import VideoBlur, video_blur_filters
from clipcanvas_server import Render
from clipcanvas_batch import project_settings


class VideoBlurTests(unittest.TestCase):
    def request(self, **extra):
        return Render.model_validate({'source_id':'a'*32,'canvas':{'width':320,'height':240},
            'video':{'x':0,'y':0,'w':320,'h':180},'background':{},'parts':[{'start':0,'end':4}],**extra})

    def test_legacy_requests_do_not_blur_and_batches_keep_regions(self):
        self.assertEqual(self.request().video_blurs,[])
        request=self.request(video_blurs=[{'id':'area','x':.25,'y':.25,'w':.5,'h':.5}])
        self.assertEqual(project_settings(request.model_dump())['pro']['video_blurs'],[request.video_blurs[0].model_dump()])
        self.assertEqual(video_blur_filters('source',[],'result',320,180),['[source]null[result]'])

    def test_invalid_area_time_strength_and_region_limit_are_rejected(self):
        for value in ({'x':.8,'w':.5},{'w':0},{'strength':0},{'strength':101},
                      {'start':2,'end':1},{'start':1,'end':1},{'end':0},{'strength':float('nan')},{'unknown':1}):
            with self.subTest(value=value),self.assertRaises(ValueError):VideoBlur.model_validate(value)
        with self.assertRaises(ValueError):self.request(video_blurs=[{}]*21)

    def test_segment_clock_uses_source_time_and_frozen_frames_do_not_advance_it(self):
        region=VideoBlur(id='area',start=2,end=4,x=.25,y=.25,w=.5,h=.5)
        graph=','.join(video_blur_filters('source',[region],'result',320,180,start=3,duration=2))
        self.assertIn('gte((t+3.000000),2.000000)*lt((t+3.000000),4.000000)',graph)
        frozen=','.join(video_blur_filters('source',[region],'result',320,180,frozen_time=3))
        self.assertIn('gblur=',frozen);self.assertNotIn('enable=',frozen)
        for time in (1,4):
            self.assertEqual(video_blur_filters('source',[region],'result',320,180,frozen_time=time),['[source]null[result]'])
        self.assertEqual(video_blur_filters('source',[region],'result',320,180,start=4,duration=1),['[source]null[result]'])

    def test_disabled_regions_and_overlapping_regions_keep_independent_graphs(self):
        regions=[VideoBlur(id='one'),VideoBlur(id='two',x=.25,w=.5),VideoBlur(enabled=False)]
        graph=','.join(video_blur_filters('source',regions,'result',320,180))
        self.assertEqual(graph.count('gblur='),2)
        self.assertEqual(graph.count('overlay='),2)
        self.assertTrue(graph.endswith('[result]'))


if __name__=='__main__':unittest.main()
