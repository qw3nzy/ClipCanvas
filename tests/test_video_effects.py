"""Neutral compatibility and validated video transformations."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'engine'),str(ROOT/'.runtime/deps')]
from clipcanvas_video import VideoEffects, video_effect_filters
from clipcanvas_server import Render, Geometry, video_layer_filter, health, app
from clipcanvas_pro import Framing


class VideoEffectsTests(unittest.TestCase):
    def test_launcher_health_uses_the_actual_application_version(self):
        self.assertEqual(health()['version'],app.version)

    def test_old_render_requests_remain_neutral(self):
        request = Render.model_validate({'source_id':'a'*32,'canvas':{'width':240,'height':300},
            'video':{'x':0,'y':0,'w':240,'h':300},'background':{},'parts':[{'start':0,'end':3}]})
        self.assertEqual(request.video_effects.model_dump(),dict(mirror=False,brightness=1,contrast=1,saturation=1))
        self.assertEqual(video_effect_filters(request.video_effects),'')

    def test_zero_is_supported_and_invalid_numbers_rejected(self):
        self.assertEqual(VideoEffects(brightness=0,contrast=0,saturation=0).brightness,0)
        for key in ('brightness','contrast','saturation'):
            for value in (-.01,2.01,float('nan'),float('inf')):
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                    VideoEffects.model_validate({key:value})
        with self.assertRaises(ValueError):
            VideoEffects.model_validate({'unknown':True})

    def test_crop_is_resolved_before_mirroring_for_both_framing_modes(self):
        geometry=Geometry(x=20,y=30,w=200,h=120,fit='cover')
        for framing in (Framing(),Framing(mode='subject',x=.25)):
            graph=video_layer_filter('source',geometry,framing,0,'result',VideoEffects(mirror=True))
            self.assertLess(graph.index('crop='),graph.index('hflip'))
            self.assertTrue(graph.endswith('[result]'))


if __name__=='__main__':unittest.main()
