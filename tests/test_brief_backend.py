"""Exact user positioning and bounded vision analysis, including truncated answers."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'engine'),str(ROOT/'.runtime/deps')]
import clipcanvas_ai as ai
from clipcanvas_layout import BannerRules, explicitly_centered, fixed_center_geometry, choose_layout
from clipcanvas_studio import analyze_banner_batches, Studio, BannerAdaptation

REQUIREMENTS = dict(can_adapt=True,lock_size=False,lock_position=False,min_width=0,min_height=0,
                    max_width=0,max_height=0,zone='any',reason='ТЗ учтено')


class ExactCenterTests(unittest.TestCase):
    def test_canvas_and_video_center_preserve_size_rotation_and_original(self):
        original=dict(x=54,y=260,w=810,h=325.4,rotation=0,fit='contain')
        snapshot=deepcopy(original)
        box=fixed_center_geometry(original,BannerRules(),1080,1920,instructions='Баннер по центру.')
        self.assertEqual((box['x'],box['y']),(135,797.3))
        video=dict(x=100,y=400,w=880,h=500)
        box=fixed_center_geometry(original,BannerRules(zone='center',center_reference='video'),1080,1920,video)
        self.assertAlmostEqual(box['x']+box['w']/2,540)
        self.assertAlmostEqual(box['y']+box['h']/2,650)
        self.assertEqual(original,snapshot)

    def test_text_inside_banner_and_negative_request_do_not_move_layer(self):
        for text in ('Не ставить по центру.','Не по центру','Текст по центру баннера.','Логотип в центре изображения.','never place center'):
            with self.subTest(text=text):
                self.assertFalse(explicitly_centered(text))
        self.assertTrue(explicitly_centered('Сохранить размер. Баннер по центру холста.'))
        original=dict(x=0,y=0,w=80,h=40)
        self.assertIsNone(fixed_center_geometry(original,BannerRules(movable=False,zone='center'),640,360))
        self.assertIsNone(fixed_center_geometry(original,BannerRules(),640,360,instructions='Баннер по центру. Не перемещать баннер.'))

    def test_center_does_not_wander_to_avoid_protected_regions_or_ignore_lock(self):
        original=dict(x=0,y=0,w=80,h=40)
        self.assertEqual(choose_layout(original,BannerRules(zone='center'),REQUIREMENTS,[],640,360)['y'],160)
        self.assertIsNone(choose_layout(original,BannerRules(zone='center'),REQUIREMENTS,[dict(x=250,y=140,w=140,h=80)],640,360))
        self.assertIsNone(choose_layout(original,BannerRules(zone='center',movable=False),REQUIREMENTS,[],640,360))
        with self.assertRaisesRegex(ValueError,'не помещается'):
            fixed_center_geometry(dict(x=0,y=0,w=620,h=300,rotation=45),BannerRules(zone='center'),640,360)

    def test_exact_position_does_not_require_a_model_or_decode_banner(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(dir=ROOT/'.runtime/tmp') as directory:
            update=Mock()
            studio=Studio({'DATA':Path(directory),'asset':lambda *args:{'path':'missing.mp4'},'update':update})
            request=BannerAdaptation(source_id='source',asset_id='banner',banner_id='layer',width=640,height=360,
                video=dict(x=0,y=0,w=640,h=360),original=dict(x=10,y=10,w=160,h=80),start=0,end=2,
                rules={'zone':'center'},protected_regions=[dict(x=250,y=100,w=150,h=150)])
            studio.banner_job('job',request)
            self.assertEqual(update.call_args.kwargs['geometry']['y'],140)
            self.assertTrue(update.call_args.kwargs['fixed_position'])
            self.assertIn('пересечение',update.call_args.kwargs['message'])

    def test_exact_center_preserves_minimum_size_and_explicit_zone_constraints(self):
        original=dict(x=0,y=0,w=80,h=40)
        with self.assertRaisesRegex(ValueError,'минимума'):
            fixed_center_geometry(original,BannerRules(zone='center',min_width=120),640,360)
        with self.assertRaisesRegex(ValueError,'другая зона'):
            fixed_center_geometry(original,BannerRules(zone='top'),640,360,instructions='Баннер по центру.')


class BannerBatchTests(unittest.TestCase):
    def setUp(self):
        self.frames=[{'seconds':i,'image':str(i)} for i in range(6)]
        self.calls=[]

    def model(self,messages,schema):
        images=messages[-1]['images'];self.calls.append((images,deepcopy(schema)))
        result={'regions':[dict(frame=i,x=10,y=10,w=10,h=10,kind='face') for i in range(len(images))]}
        if 'requirements' in schema['properties']:result['requirements']=dict(REQUIREMENTS)
        return result

    def test_two_frame_requests_merge_global_indices_and_request_requirements_once(self):
        result,sizes=analyze_banner_batches(self.frames,self.model,{},'system',lambda:None)
        self.assertEqual(sizes,[2,2,2])
        self.assertEqual([r['frame'] for r in result['regions']],list(range(6)))
        self.assertEqual(sum('requirements' in schema['properties'] for _,schema in self.calls),1)
        self.assertTrue(all(schema['properties']['regions']['maxItems']==12 for _,schema in self.calls))

    def test_truncated_first_and_later_batch_retry_each_frame_without_duplicates(self):
        notice=Mock()
        def model(messages,schema):
            if len(messages[-1]['images'])==2:raise ai.ModelResponseLimitError('limit')
            return self.model(messages,schema)
        result,sizes=analyze_banner_batches(self.frames,model,{},'system',lambda:None,notice)
        self.assertEqual(sizes,[2,1,1,2,1,1,2,1,1])
        self.assertEqual([r['frame'] for r in result['regions']],list(range(6)))
        self.assertEqual(notice.call_count,3)

    def test_single_frame_limit_invalid_index_and_cancellation_are_not_applied(self):
        with self.assertRaises(ai.ModelResponseLimitError):
            analyze_banner_batches(self.frames,Mock(side_effect=ai.ModelResponseLimitError('limit')),{},'',lambda:None)
        with self.assertRaisesRegex(ValueError,'индекс'):
            analyze_banner_batches(self.frames,lambda *args:{'requirements':REQUIREMENTS,'regions':[{'frame':99}]},{},'',lambda:None)
        check=Mock(side_effect=[None,RuntimeError('cancelled')])
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            analyze_banner_batches(self.frames,self.model,{},'',check)
        self.assertEqual(len(self.calls),1)


if __name__ == '__main__':
    unittest.main()
