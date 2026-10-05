"""Regression checks for local AI boundaries and non-destructive editing."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'.runtime/deps'))
sys.path.insert(0, str(ROOT/'engine'))
from clipcanvas_studio import (Search, CaptionReview, validated_clips, distinct_clips, windows,
    formatted_cues, corrected_cue, template_settings, reading_issues, anchors_for)


class HighlightTests(unittest.TestCase):
    def test_bad_model_timestamps_are_never_used(self):
        response = {'clips': [dict(start_id=0, end_id=2, title='Valid', reason='Scene', score=80),
                              dict(start_id=1, end_id=99, title='Invented', reason='No anchor', score=99),
                              dict(start_id=2, end_id=0, title='Reversed', reason='Invalid', score=99),
                              ]}
        self.assertEqual([(c['start'], c['end']) for c in validated_clips(response, [10, 20, 40], 20, 40)], [(10, 40)])
        self.assertEqual(validated_clips(response, [10, 20, 40], 31, 40), [])

    def test_evidence_includes_the_punchline_and_cannot_escape_window(self):
        response = {'clips': [dict(start_id=0, end_id=1, title='Joke', reason='Answer', score=90, evidence_cue_ids=[0, 1])]}
        cues = [dict(start=1, end=3, text='Setup'), dict(start=3.5, end=6.5, text='Punchline')]
        result = validated_clips(response, [0, 3, 8], 2, 7, cues)
        self.assertEqual(result[0]['end'], 6.5)
        self.assertEqual(len(result[0]['evidence']), 2)
        self.assertEqual(validated_clips(response, [0, 3, 5], 2, 7, cues), [])

    def test_window_overlap_covers_a_scene_crossing_every_boundary(self):
        spans = list(windows(100, 700, 60))
        for start in range(100, 640):
            self.assertTrue(any(a <= start and b >= start+60 for a, b in spans))
        self.assertEqual(spans[0][0], 100)
        self.assertEqual(spans[-1][1], 700)

    def test_duplicates_from_overlapping_windows_are_removed(self):
        clips = [dict(start=10, end=50, score=70), dict(start=12, end=49, score=90), dict(start=100, end=130, score=60)]
        self.assertEqual([c['start'] for c in distinct_clips(clips, 8)], [12, 100])

    def test_anchors_cannot_escape_requested_window(self):
        anchors = anchors_for(10, 20, [dict(start=2, end=15), dict(start=18, end=25)], [5, 19, 22])
        self.assertEqual(min(anchors), 10)
        self.assertEqual(max(anchors), 20)
        self.assertIn(15, anchors)
        self.assertIn(19, anchors)
        with self.assertRaises(ValueError):
            Search(source_id='x', start=20, end=10)


class CaptionReviewTests(unittest.TestCase):
    def test_formatting_does_not_invent_word_times(self):
        cue = dict(start=4, end=8, text='Одна довольно длинная фраза без точных меток слов.', words=[])
        result = formatted_cues([cue], 16)
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]['start'], result[0]['end']), (4, 8))
        self.assertEqual(result[0]['words'], [])
        self.assertEqual(' '.join(result[0]['text'].split()), cue['text'])

    def test_word_timing_survives_punctuation_but_not_rewriting(self):
        cue = dict(start=0, end=2, text='привет мир', words=[dict(start=0, end=1, text='привет'), dict(start=1, end=2, text='мир')])
        result = corrected_cue(cue, 'Привет, мир!')
        self.assertEqual([w['text'] for w in result['words']], ['Привет,', 'мир!'])
        self.assertEqual([w['start'] for w in result['words']], [0, 1])
        self.assertEqual(corrected_cue(cue, 'Добрый вечер')['words'], [])
        self.assertEqual(cue['text'], 'привет мир')

    def test_review_rejects_invalid_words(self):
        with self.assertRaises(ValueError):
            CaptionReview(source_id='x', cues=[dict(start=0, end=2, text='x', words=[dict(start=0, end=5, text='x')])])

    def test_readability_check_identifies_overlap_and_speed(self):
        issues = reading_issues([dict(start=0, end=1, text='Очень длинная реплика для одной секунды'), dict(start=.5, end=2, text='Да')])
        self.assertTrue(any(i['severity'] == 'error' for i in issues))
        self.assertTrue(any('быстро' in i['message'] for i in issues))


class TemplateTests(unittest.TestCase):
    def test_template_cannot_copy_user_content_or_asset_ids(self):
        value = {'sourceId': 'secret', 'ranges': [1], 'subtitles': {'cues': [{'text': 'private'}], 'enabled': True, 'size': 52},
                 'activeJob': 'job', 'geometry': {'video': {'w': 1080}, 'source_id': 'secret'}}
        result = template_settings(value)
        self.assertEqual(result, {'subtitles': {'size': 52}, 'geometry': {'video': {'w': 1080}}})
        self.assertIn('cues', value['subtitles'])


class PersistenceTests(unittest.TestCase):
    def test_progress_does_not_rewrite_captions_or_request(self):
        import tempfile
        import clipcanvas_server as server
        job_id = 'a'*32
        with tempfile.TemporaryDirectory(dir=ROOT/'.runtime/qa') as temporary:
            data = Path(temporary)
            directory = data/'jobs'/job_id
            directory.mkdir(parents=True)
            job = {'id': job_id, 'status': 'running', 'request': {'source_id': 'source'}, 'cues': [{'start': 0, 'end': 1, 'text': 'text'}]}
            with patch.object(server, 'DATA', data), patch.object(server, 'JOBS', {job_id: job}):
                server.persist(job_id)
                first = (directory/'cues.json').stat().st_mtime_ns
                with patch.object(server, 'atomic_json', wraps=server.atomic_json) as write:
                    server.update(job_id, progress=50)
                    self.assertEqual([call.args[0].name for call in write.call_args_list], ['job.json'])
                self.assertEqual(first, (directory/'cues.json').stat().st_mtime_ns)
                self.assertNotIn('cues', json.loads((directory/'job.json').read_text('utf-8')))
                self.assertEqual(server.get_job(job_id)['cues'], job['cues'])


if __name__ == '__main__':
    unittest.main()
