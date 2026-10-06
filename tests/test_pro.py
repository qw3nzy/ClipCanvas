"""Final-length cuts, repeat schedules, motion and safe folder names."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'engine'), str(ROOT/'.runtime/deps')]
from clipcanvas_pro import (BannerSchedule, DeliveryRules, scheduled_banners, split_for_delivery,
                            safe_relative_path, interpolate, motion_expression, disk_estimate,
                            effective_payload, delivery_issues, split_by_count)


class DeliveryTests(unittest.TestCase):
    def banner(self, mode='each'):
        return {'id':'logo','name':'Logo','enabled':True,'asset_id':'a'*32,'start':90,'end':100,
                'geometry':{'x':20,'y':80,'w':100,'h':40,'fit':'contain'},
                'schedule':{'mode':mode,'start':2,'duration':3,'interval':6}, 'loop':True}

    def test_schedule_repeats_in_every_clip_without_changing_source(self):
        banner = self.banner()
        before = deepcopy(banner)
        for index in (1, 2, 9):
            resolved = scheduled_banners([banner], 10, index)
            self.assertEqual([(b['start'],b['end'],b['timebase']) for b in resolved], [(2,5,'output')])
            self.assertIn(f'clip-{index}', resolved[0]['id'])
        self.assertEqual(banner, before)

    def test_full_end_and_repeat_schedules_clip_to_duration(self):
        self.assertEqual([(b['start'],b['end']) for b in scheduled_banners([self.banner('full')], 7, 1)], [(0,7)])
        self.assertEqual([(b['start'],b['end']) for b in scheduled_banners([self.banner('end')], 10, 1)], [(5,8)])
        self.assertEqual([(b['start'],b['end']) for b in scheduled_banners([self.banner('interval')], 10, 1)], [(2,5),(8,10)])
        banner = self.banner();banner['schedule']['clips']=[2]
        self.assertEqual(scheduled_banners([banner],10,1), [])

    def test_overlapping_repeat_is_rejected(self):
        with self.assertRaises(ValueError):
            BannerSchedule(mode='interval', duration=5, interval=2)

    def test_final_length_includes_ads_and_deleted_source(self):
        from clipcanvas_composition import composition_timeline
        ad={'enabled':True,'start':1,'repeat':True,'interval':3,'speed':1}
        cuts=[{'start':3,'end':4}]
        parts=split_for_delivery(10,4,cuts,ad,1,fps=30)
        self.assertEqual(parts[0]['start'],0)
        self.assertEqual(parts[-1]['end'],10)
        for first, second in zip(parts,parts[1:]):
            self.assertAlmostEqual(first['end'],second['start'])
        for part in parts:
            plan=composition_timeline(part['start'],part['end'],cuts,1,1,True,3)
            self.assertLessEqual(sum(s['duration'] for s in plan),4+.00001)

    def test_sentence_end_is_preferred_without_exceeding_budget(self):
        parts=split_for_delivery(10,4,cues=[{'start':0,'end':3.5}],fps=30)
        self.assertEqual(parts[0]['end'],3.5)

    def test_count_uses_retained_footage_and_produces_exact_number(self):
        cuts=[{'start':2,'end':6}]
        parts=split_by_count(10,3,cuts)
        self.assertEqual(len(parts),3)
        from clipcanvas_composition import retained_intervals
        self.assertTrue(all(abs(sum(s['duration'] for s in retained_intervals(p['start'],p['end'],cuts))-2)<.001 for p in parts))

    def test_disabled_subtitles_do_not_satisfy_mandatory_rule(self):
        payload={'canvas':{'width':240,'height':300},'delivery':{'require_subtitles':True}}
        config={'banners':[],'subtitles':{'enabled':False,'cues':[{'start':0,'end':3,'text':'Hello'}]}}
        plan=[{'kind':'source','start':0,'end':3,'duration':3,'output_start':0}]
        self.assertTrue(any(i['severity']=='error' for i in delivery_issues(payload,[(config,plan)],lambda _id:{})))

    def test_safe_relative_paths_reject_windows_traversal_and_devices(self):
        self.assertEqual(safe_relative_path('Сезон 1\\Серия 2.mkv'),'Сезон 1/Серия 2.mkv')
        for name in ('../secret','D:/file','a/../../b','/root','CON.mp4','season/NUL','a//b','a:stream','a/file.','a/\0b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_relative_path(name)

    def test_motion_interpolates_and_holds_at_edges(self):
        points=[{'time':1,'x':20},{'time':3,'x':100}]
        self.assertEqual(interpolate(points,0,'x',0),20)
        self.assertEqual(interpolate(points,2,'x',0),60)
        self.assertEqual(interpolate(points,4,'x',0),100)
        self.assertEqual(interpolate(points,2,'x',0,True),20)
        self.assertIn('if(',motion_expression(points,'x',0))

    def test_zip_disk_estimate_includes_temp_and_archive(self):
        payload={'canvas':{'width':1080,'height':1920},'fps':30}
        first=disk_estimate(payload,60)
        second=disk_estimate(payload,60,zip_output=True)
        self.assertEqual(second['required_bytes']-first['required_bytes'],first['output_bytes'])

    def test_clip_override_does_not_affect_other_clips(self):
        payload={'source_id':'a'*32,'canvas':{'width':240,'height':300},'video':{'x':0,'y':0,'w':240,'h':300},
                 'advertising':{'enabled':False},'subtitles':{'enabled':False,'cues':[]},'banners':[self.banner()],
                 'clip_overrides':{'2':{'video':{'x':10,'y':10,'w':200,'h':200},'subtitles':{'size':36}}}}
        before=deepcopy(payload)
        first,_=effective_payload(payload,{'start':0,'end':10},1)
        second,_=effective_payload(payload,{'start':10,'end':20},2)
        self.assertEqual(first['video']['x'],0)
        self.assertEqual(second['video']['x'],10)
        self.assertEqual(payload,before)

    def test_partial_caption_override_ignores_unset_fields(self):
        payload={'canvas':{'width':240,'height':300},'video':{'x':0,'y':0,'w':240,'h':300},
            'advertising':{'enabled':False},'banners':[],
            'subtitles':{'enabled':True,'size':28,'style':'clean','cues':[]},
            'clip_overrides':{'1':{'subtitles':{'size':36,'style':None,'color':None}}}}
        result,_=effective_payload(payload,{'start':0,'end':2},1)
        self.assertEqual(result['subtitles']['style'],'clean')
        self.assertEqual(result['subtitles']['size'],36)
        self.assertNotIn('color',result['subtitles'])

    def test_short_limit_and_required_banners(self):
        self.assertEqual(DeliveryRules(platform='shorts').max_duration,180)
        payload={'canvas':{'width':240,'height':300},'delivery':{'require_banner':True}}
        config={'banners':[],'subtitles':{'enabled':False,'cues':[]}}
        plan=[{'kind':'source','start':0,'end':3,'duration':3,'output_start':0}]
        issues=delivery_issues(payload,[(config,plan)],lambda _id:{})
        self.assertEqual(issues[0]['severity'],'error')


if __name__ == '__main__':
    unittest.main()
