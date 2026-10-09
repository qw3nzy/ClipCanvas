"""Crop bounds and compatibility of render and saved batch projects."""
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'engine'),str(ROOT/'.runtime/deps')]
from clipcanvas_video import VideoCrop, video_crop_filter
from clipcanvas_server import Render, Geometry, video_layer_filter
from clipcanvas_pro import Framing
from clipcanvas_batch import project_settings


class VideoCropTests(unittest.TestCase):
    def request(self, **extra):
        return Render.model_validate({'source_id':'a'*32,'canvas':{'width':240,'height':300},
            'video':{'x':0,'y':0,'w':240,'h':300},'background':{},'parts':[{'start':0,'end':3}],**extra})

    def test_old_projects_keep_the_entire_source(self):
        request=self.request()
        self.assertFalse(request.video_crop.enabled)
        self.assertEqual(video_crop_filter(request.video_crop),'')
        self.assertEqual(video_crop_filter(VideoCrop(enabled=True)),'')

    def test_rejects_empty_outside_and_nonfinite_rectangles(self):
        for settings in ({'w':0},{'h':.009},{'x':-.01},{'x':.5,'w':.6},
                         {'y':.25,'h':.8},{'w':float('nan')},{'x':float('inf')},{'unexpected':1}):
            with self.subTest(settings=settings),self.assertRaises(ValueError):
                self.request(video_crop=settings)

    def test_disabled_crop_retains_the_editable_region_without_cropping(self):
        crop=VideoCrop(enabled=False,x=.5,w=.5,y=.25,h=.5)
        self.assertEqual(video_crop_filter(crop),'')
        self.assertEqual(crop.w,.5)

    def test_batch_project_preserves_the_region_and_source_focus_coordinates(self):
        crop={'enabled':True,'x':.5,'y':.25,'w':.5,'h':.5}
        request=self.request(video_crop=crop)
        self.assertEqual(project_settings(request.model_dump())['pro']['video_crop'],crop)
        geometry=Geometry(x=0,y=0,w=240,h=300,fit='cover')
        graph=video_layer_filter('source',geometry,Framing(mode='center',x=.625),0,'result',crop=request.video_crop)
        self.assertIn('0.500000000)/0.500000000',graph)


if __name__=='__main__':unittest.main()
