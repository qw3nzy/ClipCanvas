"""Device fallback, visual boundaries, and client constraints for AI editing."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'engine'), str(ROOT/'.runtime/deps')]
import clipcanvas_ai as ai
import clipcanvas_asr as asr
from clipcanvas_layout import BannerRules, choose_layout, enforce_client_locks, map_region, validated_regions
from clipcanvas_studio import BannerAdaptation


class SpeechDeviceTests(unittest.TestCase):
    def exercise(self, device, hardware, fail=False):
        reports, devices = [], []
        class Model:
            def __init__(self, _name, **options):
                devices.append(options['device'])
                self.device = options['device']
            def transcribe(self, *_args, **_options):
                def segments():
                    if fail and self.device == 'cuda':
                        raise RuntimeError('cuDNN CUDA runtime unavailable')
                    yield 'cue'
                return segments(), SimpleNamespace(language='ru')
        with patch.object(asr, 'hardware_status', return_value=hardware), patch('faster_whisper.WhisperModel', Model):
            result = asr.recognize('audio.wav', 'small', 'auto', list, ROOT/'.runtime/qa',
                                   lambda **values: reports.append(values), device)
        return result, devices, reports

    def test_auto_uses_cuda_when_available(self):
        result, devices, reports = self.exercise('auto', {'cuda_available': True, 'compute_types': ['int8_float16']})
        self.assertEqual(result, (['cue'], 'ru'))
        self.assertEqual(devices, ['cuda'])
        self.assertEqual(reports[-1]['asr_device'], 'cuda')

    def test_fallback_covers_lazy_cuda_execution(self):
        result, devices, reports = self.exercise('auto', {'cuda_available': True}, fail=True)
        self.assertEqual(result[0], ['cue'])
        self.assertEqual(devices, ['cuda', 'cpu'])
        self.assertTrue(reports[-1]['asr_fallback'])

    def test_forced_cuda_reports_error_instead_of_silently_using_cpu(self):
        with self.assertRaisesRegex(RuntimeError, 'NVIDIA'):
            self.exercise('cuda', {'cuda_available': True}, fail=True)

    def test_forced_cpu_stays_on_cpu(self):
        _, devices, _ = self.exercise('cpu', {'cuda_available': True})
        self.assertEqual(devices, ['cpu'])

    def test_device_configuration_and_runtime_reporting(self):
        with self.assertRaises(ValueError):
            ai._validate({**ai.DEFAULTS, 'speech_device': 'unknown'})
        with patch.object(ai, 'status', return_value={'model': 'qa', 'provider': 'ollama', 'running': True}), \
             patch.object(ai, 'request_json', return_value={'models': [{'name': 'qa', 'size': 100, 'size_vram': 80}]}):
            result = ai.runtime_status()
        self.assertEqual(result['execution']['device'], 'cuda')
        self.assertEqual(result['execution']['gpu_percent'], 80)


class VisualBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.context = ai.boundary_context([], 100, scenes=[5], window_seconds=20)
        self.context['frames'] = [{'frame_id': 0, 'seconds': .1}, {'frame_id': 1, 'seconds': 2}]
        self.proposal = dict(type='intro', category='logo', evidence_basis='visual', start_boundary_id=0,
                             end_boundary_id=1, evidence_frame_ids=[0, 1], confidence=.95, reason='Логотип канала')

    def test_silent_intro_needs_real_frames_and_anchored_boundaries(self):
        result = ai.validated_boundaries({'suggestions': [self.proposal]}, self.context)
        self.assertEqual([(p['start'], p['end']) for p in result], [(0, 5)])
        for changes in ({'evidence_frame_ids': [0]}, {'evidence_frame_ids': [999]}, {'end_boundary_id': 999},
                        {'confidence': True}, {'category': 'story'}, {'confidence': .5}):
            self.assertEqual(ai.validated_boundaries({'suggestions': [{**self.proposal, **changes}]}, self.context), [])

    def test_visual_cut_cannot_split_spoken_content(self):
        self.context['dialogue'] = [{'start': 4, 'end': 6, 'text': 'Сюжет', 'cue_id': 0}]
        self.assertEqual(ai.validated_boundaries({'suggestions': [self.proposal]}, self.context), [])

    def test_no_speech_still_calls_vision_when_frames_provided(self):
        provider = lambda *_: {'frames': [{'seconds': .1, 'image': 'jpeg'}, {'seconds': 2, 'image': 'jpeg'}], 'scenes': [5]}
        with patch.object(ai, 'chat', return_value={'suggestions': []}) as chat:
            ai.analyze_boundaries([], 100, ROOT/'.runtime/qa', lambda: None, frame_provider=provider)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(len(chat.call_args.args[0][1]['images']), 2)

    def test_frame_ids_are_local_to_each_edge_and_validated_after_translation(self):
        provider = lambda kind, _start, _end: {'frames': [{'seconds': t, 'image': 'jpeg'} for t in
            ([.1, 2] if kind == 'intro' else [95.1, 97])], 'scenes': [5] if kind == 'intro' else [95]}
        responses = [{'suggestions': [], 'frame_labels': [dict(frame_id=i, category=category, confidence=.95, reason='Заставка')
            for i in range(2)]} for category in ('logo', 'outro_card')]
        with patch.object(ai, 'chat', side_effect=responses):
            result = ai.analyze_boundaries([], 100, ROOT/'.runtime/qa', lambda: None, window_seconds=20, frame_provider=provider)
        self.assertEqual([(p['start'], p['end']) for p in result], [(0, 5), (95, 100)])
        self.assertEqual(result[-1]['frame_evidence'][0]['frame_id'], 2)

    def test_credits_do_not_include_story_before_scene_transition(self):
        context = ai.boundary_context([], 24, scenes=[20], window_seconds=10)
        frames = [{'frame_id': i, 'seconds': t} for i, t in enumerate([14.05, 16, 18, 20, 22, 23.9])]
        context['frames'] = frames
        response = {'frame_labels': [dict(frame_id=i, category='story' if i < 3 else 'credits', confidence=.95, reason='Титры')
                                    for i in range(6)]}
        rows = ai.visual_scene_proposals(response, 'outro', frames, [20], 0, context)
        self.assertEqual([(p['start'], p['end']) for p in ai.validated_boundaries({'suggestions': rows}, context)], [(20, 24)])
        response['frame_labels'][4]['category'] = 'story'
        self.assertEqual(ai.visual_scene_proposals(response, 'outro', frames, [20], 0, context), [])

    def test_sampling_window_does_not_prove_visual_boundary(self):
        frames = self.context['frames']
        response = {'frame_labels': [dict(frame_id=i, category='logo', confidence=.95) for i in range(2)]}
        self.assertEqual(ai.visual_scene_proposals(response, 'intro', frames, [], 0, self.context), [])
        response['frame_labels'][1]['frame_id'] = 0
        self.assertEqual(ai.visual_scene_proposals(response, 'intro', frames, [5], 0, self.context), [])


class BannerLayoutTests(unittest.TestCase):
    def setUp(self):
        self.original = dict(x=100, y=150, w=240, h=80, fit='contain')
        self.requirements = dict(can_adapt=True, lock_size=False, lock_position=False, min_width=0,
                                 min_height=0, max_width=0, max_height=0, zone='any', reason='ТЗ учтено')

    def test_preserves_size_and_avoids_faces_and_captions(self):
        protected = [dict(x=90, y=120, w=260, h=280), dict(x=0, y=450, w=600, h=150)]
        result = choose_layout(self.original, BannerRules(), self.requirements, protected, 600, 600)
        self.assertIsNotNone(result)
        self.assertEqual((result['w'], result['h']), (240, 80))
        self.assertLess(result['y']+result['h'], 120)

    def test_client_minimum_and_zone_are_hard_constraints(self):
        rules = BannerRules(resizable=True, min_width=220, min_height=70, zone='bottom')
        result = choose_layout(self.original, rules, self.requirements, [], 600, 600)
        self.assertGreaterEqual(result['w'], 220)
        self.assertGreaterEqual(result['h'], 70)
        self.assertGreaterEqual(result['y'], 300)
        self.assertIsNone(choose_layout(self.original, rules, {**self.requirements, 'zone': 'top'}, [], 600, 600))

    def test_unclear_or_unsatisfiable_requirements_do_not_modify_banner(self):
        original = copy.deepcopy(self.original)
        self.assertIsNone(choose_layout(self.original, BannerRules(), {**self.requirements, 'can_adapt': False}, [], 600, 600))
        self.assertIsNone(choose_layout(self.original, BannerRules(movable=False), self.requirements,
                                        [dict(x=0, y=0, w=600, h=600)], 600, 600))
        self.assertEqual(self.original, original)
        with self.assertRaises(ValueError):
            choose_layout(self.original, BannerRules(), {}, [], 600, 600)

    def test_minimum_applies_to_visible_image_not_empty_contain_box(self):
        self.assertIsNone(choose_layout(self.original, BannerRules(min_height=70), self.requirements, [],
                                        600, 600, image_aspect=10))

    def test_explicit_client_prohibition_overrides_model_and_resize_checkbox(self):
        requirements = enforce_client_locks(self.requirements, 'Баннер в верхней половине. Размер, изображение и время показа не менять.')
        self.assertTrue(requirements['lock_size'])
        self.assertFalse(requirements['lock_position'])
        result = choose_layout(self.original, BannerRules(resizable=True, zone='top'), requirements, [], 600, 600)
        self.assertEqual((result['w'], result['h']), (240, 80))
        locked = enforce_client_locks(self.requirements, 'Не перемещать баннер. Do not resize.')
        self.assertTrue(locked['lock_position'])
        self.assertTrue(locked['lock_size'])
        self.assertFalse(self.requirements['lock_size'])

    def test_source_regions_follow_letterbox_and_crop(self):
        region = dict(x=25, y=25, w=50, h=50)
        box = map_region(region, 200, 100, dict(x=0, y=0, w=600, h=600, fit='contain'), dict(x=0, y=0, w=600, h=600))
        self.assertEqual(box, dict(x=150, y=225, w=300, h=150))
        with self.assertRaises(ValueError):
            validated_regions({'regions': [{'frame': 999}]}, 6, 200, 100, self.original, 600, 600)

    def test_banner_analysis_rejects_unbounded_windows(self):
        with self.assertRaises(ValueError):
            BannerAdaptation(source_id='video', asset_id='image', banner_id='banner', width=600, height=600,
                             video=self.original, original=self.original, start=0, end=181)


if __name__ == '__main__':
    unittest.main()
