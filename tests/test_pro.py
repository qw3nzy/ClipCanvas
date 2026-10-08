"""Final-length cuts, repeat schedules, motion and safe folder names."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'engine'), str(ROOT/'.runtime/deps')]
from clipcanvas_pro import (AudioSettings, BannerSchedule, DeliveryRules, scheduled_banners, split_for_delivery,
                            safe_relative_path, interpolate, motion_expression, disk_estimate,
                            effective_payload, delivery_issues, split_by_count)


class DeliveryTests(unittest.TestCase):
    def test_ducking_trigger_preserves_legacy_and_accepts_silent_banner_mode(self):
        self.assertEqual(AudioSettings(ducking=True).duck_trigger,'sounding')
        self.assertEqual(AudioSettings(ducking=True,duck_trigger='visible',duck_volume=0).duck_volume,0)
        with self.assertRaises(ValueError):
            AudioSettings(duck_trigger='unknown')

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

    def test_bubavpn_center_and_full_minutes_follow_final_clip_duration(self):
        banner=self.banner('bubavpn');banner['schedule']['duration']=5
        for duration,count in ((3,1),(60,1),(119.999,1),(120,2),(179.999,2),(180,3),(240,4)):
            shows=scheduled_banners([banner],duration,1)
            self.assertEqual(len(shows),count)
            self.assertAlmostEqual((shows[0]['start']+shows[0]['end'])/2,duration/2)
            self.assertTrue(all(0<=show['start']<show['end']<=duration for show in shows))
            ordered=sorted(shows,key=lambda show:show['start'])
            self.assertTrue(all(a['end']<=b['start'] for a,b in zip(ordered,ordered[1:])))
        banner['schedule']['excluded_occurrences']=[0]
        self.assertEqual(len(scheduled_banners([banner],180,1)),2)

    def test_overlapping_repeat_is_rejected(self):
        with self.assertRaises(ValueError):
            BannerSchedule(mode='interval', duration=5, interval=2)

    def test_deleted_occurrence_keeps_other_repeats_and_is_validated(self):
        banner = self.banner('interval')
        banner['schedule'].update(start=5,duration=2,interval=8,excluded_occurrences=[1])
        self.assertEqual([(b['start'],b['end']) for b in scheduled_banners([banner],24,2)],[(5,7),(21,23)])
        with self.assertRaises(ValueError):
            BannerSchedule(excluded_occurrences=[-1])
        banner['schedule'] = {'mode':'source','excluded_occurrences':[0]}
        self.assertEqual(scheduled_banners([banner],24,2),[])

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
        self.assertTrue(any(i['severity']=='warning' for i in delivery_issues(payload,[(config,plan)],lambda _id:{})))

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

    def test_clip_banner_time_overrides_schedule_without_freezing_shared_geometry(self):
        payload = {'advertising':{'enabled':False},'subtitles':{'enabled':False,'cues':[]},
                   'banners':[self.banner()], 'clip_overrides':{'2':{'banner_schedules':{
                       'logo':{'mode':'interval','start':1,'duration':2,'interval':4}}}}}
        before = deepcopy(payload)
        first,_ = effective_payload(payload,{'start':0,'end':10},1)
        second,_ = effective_payload(payload,{'start':10,'end':20},2)
        self.assertEqual([(b['start'],b['end']) for b in first['banners']],[(2,5)])
        self.assertEqual([(b['start'],b['end']) for b in second['banners']],[(1,3),(5,7),(9,10)])
        self.assertEqual(payload,before)
        payload['banners'][0]['geometry']['x'] = 75
        second,_ = effective_payload(payload,{'start':10,'end':20},2)
        self.assertTrue(all(b['geometry']['x'] == 75 for b in second['banners']))

    def test_local_banner_schedule_is_validated_and_keeps_existing_clip_overrides(self):
        from clipcanvas_server import ClipOverride
        override = ClipOverride.model_validate({'banner_schedules':{'logo':{
            'mode':'each','start':0,'duration':5}},'subtitles_enabled':False})
        self.assertEqual(override.banner_schedules['logo'].duration,5)
        self.assertFalse(override.subtitles_enabled)
        with self.assertRaises(ValueError):
            ClipOverride.model_validate({'banner_schedules':{'logo':{
                'mode':'interval','duration':5,'interval':2}}})

    def test_clip_removal_covers_legacy_and_copied_banners_without_changing_other_clip(self):
        first = self.banner(); second = self.banner(); second['id'] = 'other'
        payload = {'advertising':{'enabled':False},'subtitles':{'enabled':False,'cues':[]},
                   'banner':first,'banners':[second], 'clip_overrides':{'2':{
                       'banners':[second], 'hidden_banner_ids':['other','logo']}}}
        one,_ = effective_payload(payload,{'start':0,'end':10},1)
        two,_ = effective_payload(payload,{'start':10,'end':20},2)
        self.assertEqual(len(one['banners']),2)
        self.assertEqual(two['banners'],[])

    def test_empty_clip_is_not_repopulated_from_shared_or_future_banners(self):
        payload = {'advertising':{'enabled':False},'subtitles':{'enabled':False,'cues':[]},
                   'banners':[self.banner()], 'clip_overrides':{'2':{'banners_enabled':False}}}
        from clipcanvas_server import ClipOverride
        self.assertFalse(ClipOverride.model_validate(payload['clip_overrides']['2']).banners_enabled)
        result,_ = effective_payload(payload,{'start':10,'end':20},2)
        self.assertEqual(result['banners'],[])
        more = self.banner(); more['id'] = 'future'; payload['banners'].append(more)
        result,_ = effective_payload(payload,{'start':10,'end':20},2)
        self.assertEqual(result['banners'],[])

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
        self.assertEqual(issues[0]['severity'],'warning')


if __name__ == '__main__':
    unittest.main()
