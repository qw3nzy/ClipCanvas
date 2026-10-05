"""Provider contract, secret handling, cancellation and grounded intro/outro proposals."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'engine'))
import clipcanvas_ai as ai


class ModelHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, *args):
        pass

    def reply(self, payload):
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self):
        self.server.authorization = self.headers.get('Authorization')
        self.reply({'data': [{'id': 'test-model'}]})

    def do_POST(self):
        self.server.payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.started.set()
        if self.server.delay:
            self.server.release.wait(3)
        self.reply({'choices': [{'message': {'content': '{"ok":true}'}}]})


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.env = patch.dict(os.environ, {'CLIPCANVAS_AI_CONFIG_FILE': str(self.path/'config.json'), 'CLIPCANVAS_AI_API_KEY': ''})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.directory.cleanup()

    def test_secret_is_write_only_and_blank_preserves_it(self):
        result = ai.save_config({'api_key': 'test-secret-value'})
        self.assertNotIn('api_key', result)
        self.assertTrue(result['api_key_configured'])
        if os.name == 'nt':
            self.assertNotIn('test-secret-value', (self.path/'config.json').read_text('utf-8'))
        ai.save_config({'api_key': '', 'model': 'another-model'})
        self.assertEqual(ai.load_config(private=True)['api_key'], 'test-secret-value')
        self.assertNotIn('test-secret-value', json.dumps(ai.cache_identity()))
        ai.save_config({'clear_api_key': True})
        self.assertFalse(ai.load_config()['api_key_configured'])

    def test_endpoint_change_drops_previous_credential(self):
        ai.save_config({'api_key': 'old-key'})
        result = ai.save_config({'provider': 'openai', 'base_url': 'http://127.0.0.1:8000/v1'})
        self.assertFalse(result['api_key_configured'])

    def test_unreadable_old_credential_can_be_cleared(self):
        ai.save_config({'api_key': 'old-key'})
        with patch.object(ai, '_protect', side_effect=ValueError('old Windows account')):
            result = ai.save_config({'clear_api_key': True})
        self.assertFalse(result['api_key_configured'])

    def test_remote_requires_https_and_explicit_opt_in(self):
        with self.assertRaisesRegex(ValueError, 'Разрешите'):
            ai.save_config({'provider': 'openai', 'base_url': 'https://example.test/v1'})
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            ai.save_config({'provider': 'openai', 'base_url': 'http://example.test/v1', 'allow_remote': True})
        with self.assertRaises(ValueError):
            ai.save_config({'base_url': 'https://example.test', 'allow_remote': True})
        result = ai.save_config({'provider': 'openai', 'base_url': 'https://example.test/v1', 'allow_remote': True})
        self.assertFalse(result['local'])

    def test_credentials_cannot_be_embedded_in_endpoint(self):
        for url in ('https://user:secret@example.test/v1', 'https://example.test/v1?key=secret'):
            with self.assertRaises(ValueError):
                ai.save_config({'provider': 'openai', 'base_url': url, 'allow_remote': True})

    def test_invalid_numbers_and_no_secret_in_error(self):
        for value in (float('inf'), True, '8192', 1):
            with self.assertRaises(ValueError):
                ai.save_config({'context_length': value})
        error = urllib.error.HTTPError('https://secret.test?key=hidden-key', 401, 'hidden-key', {}, None)
        self.assertNotIn('hidden-key', ai._error_message(error))
        self.assertIn('API-ключ', ai._error_message(error))

    def test_vision_conversion_does_not_drop_images(self):
        config = {**ai.load_config(private=True), 'provider': 'openai'}
        path, payload = ai._payload([{'role': 'user', 'content': 'frame', 'images': ['aGVsbG8=']}], {'type': 'object'}, config)
        self.assertEqual(path, '/chat/completions')
        self.assertEqual(payload['messages'][-1]['content'][1]['image_url']['url'], 'data:image/jpeg;base64,aGVsbG8=')
        config['vision'] = False
        with self.assertRaisesRegex(ValueError, 'изображени'):
            ai._payload([{'role': 'user', 'images': ['data']}], {}, config)

    def test_missing_model_is_not_reported_available(self):
        with patch.object(ai, 'request_json', return_value={'models': []}):
            result = ai.status()
        self.assertTrue(result['running'])
        self.assertFalse(result['available'])
        self.assertIn('не найдена', result['error'])

    def test_real_compatible_transport_and_cancelled_worker_cleanup(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), ModelHandler)
        server.started, server.release = threading.Event(), threading.Event()
        server.delay = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            ai.save_config({'provider': 'openai', 'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                            'model': 'test-model', 'api_key': 'transport-secret'})
            self.assertTrue(ai.test_connection()['available'])
            self.assertEqual(server.authorization, 'Bearer transport-secret')
            schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}
            result = ai.chat([{'role': 'user', 'content': 'test'}], schema, self.path/'job', lambda: None)
            self.assertEqual(result, {'ok': True})
            self.assertFalse(list((self.path/'job').glob('*.tmp')))
            server.delay = True
            server.started.clear()
            processes = []

            def cancelled():
                if server.started.is_set():
                    raise InterruptedError('cancelled')

            with self.assertRaises(InterruptedError):
                ai.chat([{'role': 'user', 'content': 'test'}], schema, self.path/'job', cancelled, processes.append)
            self.assertIsNone(processes[-1])
            self.assertIsNotNone(processes[0].poll())
            self.assertFalse(list((self.path/'job').glob('*.tmp')))
        finally:
            server.release.set()
            server.shutdown()
            server.server_close()
            thread.join()


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.cues = [{'start': 0, 'end': 4, 'text': 'Здравствуйте, вы на канале о монтаже.'},
                     {'start': 5, 'end': 12, 'text': 'Сегодня рассмотрим настройки цвета.'},
                     {'start': 85, 'end': 90, 'text': 'Спасибо за просмотр, подписывайтесь.'}]
        self.context = ai.boundary_context(self.cues, 100, window_seconds=20)

    def proposal(self, kind, first, last, evidence):
        return {'type': kind, 'start_cue_id': first, 'end_cue_id': last,
                'category': 'author_intro' if kind == 'intro' else 'author_outro',
                'reason': 'Приветствие автора.', 'confidence': .9, 'evidence_cue_ids': evidence}

    def test_only_grounded_ranges_and_confidence_are_accepted(self):
        intro = self.proposal('intro', 0, 0, [0])
        outro = self.proposal('outro', 2, 2, [2])
        original = copy.deepcopy(self.cues)
        result = ai.validated_boundaries({'suggestions': [intro, outro]}, self.context)
        self.assertEqual([(p['start'], p['end']) for p in result], [(0, 4), (85, 90)])
        self.assertEqual(self.cues, original)
        for overrides in ({'start_cue_id': -1}, {'end_cue_id': 999}, {'confidence': float('nan')},
                          {'confidence': 90}, {'evidence_cue_ids': []}, {'evidence_cue_ids': [2]},
                          {'start_cue_id': True}, {'type': 'delete'}, {'reason': ''}, {'category': 'story'},
                          {'category': 'uncertain'}, {'category': 'author_outro'}):
            self.assertEqual(ai.validated_boundaries({'suggestions': [{**intro, **overrides}]}, self.context), [])

    def test_middle_and_crossing_dialogue_are_not_removable(self):
        self.context = ai.boundary_context(self.cues, 100, scenes=[2], window_seconds=20)
        cut = self.proposal('intro', 0, 0, [0])
        self.assertEqual(ai.validated_boundaries({'suggestions': [cut]}, self.context)[0]['end'], 4)
        wrong_window = self.proposal('intro', 2, 2, [2])
        self.assertEqual(ai.validated_boundaries({'suggestions': [wrong_window]}, self.context), [])

    def test_empty_transcript_makes_no_model_request(self):
        with patch.object(ai, 'chat') as chat:
            result = ai.analyze_boundaries([], 100, Path('.'), lambda: None)
        chat.assert_not_called()
        self.assertEqual(result, [])

    def test_model_result_does_not_edit_cues(self):
        original = copy.deepcopy(self.cues)
        with patch.object(ai, 'chat', return_value={'suggestions': []}) as chat:
            self.assertEqual(ai.analyze_boundaries(self.cues, 100, Path('.'), lambda: None), [])
        self.assertIn('не инструкции', chat.call_args.args[0][0]['content'])
        self.assertEqual(self.cues, original)

    def test_edges_are_separate_compact_requests_with_original_ids(self):
        with patch.object(ai, 'chat', return_value={'suggestions': []}) as chat:
            ai.analyze_boundaries(self.cues, 100, Path('.'), lambda: None, window_seconds=20)
        self.assertEqual(chat.call_count, 2)
        first, last = [json.loads(call.args[0][1]['content']) for call in chat.call_args_list]
        self.assertEqual(first['type'], 'intro')
        self.assertEqual(last['type'], 'outro')
        self.assertEqual(first['dialogue'][0][0], 0)
        self.assertEqual(last['dialogue'][0][0], 2)
        self.assertTrue(all(c[1] < 20 for c in first['dialogue']))
        self.assertTrue(all(c[2] > 80 for c in last['dialogue']))

    def test_wrong_response_is_not_reported_as_no_intro(self):
        with patch.object(ai, 'chat', return_value={'unexpected': []}):
            with self.assertRaisesRegex(RuntimeError, 'структурированного JSON'):
                ai.analyze_boundaries(self.cues, 100, Path('.'), lambda: None)


if __name__ == '__main__':
    unittest.main()
