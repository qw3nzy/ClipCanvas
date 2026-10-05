"""Session isolation, secret storage and explicit cookie import regression checks."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / '.runtime/deps'), str(ROOT / 'engine')]
from fastapi import FastAPI
from fastapi.testclient import TestClient
from url_import import register_url_routes
from youtube_session import (MAX_COOKIE_BYTES, cookie_arguments, normalize_cookies,
                             save_session, session_path, without_auth_metadata)

COOKIES = (b'# Netscape HTTP Cookie File\n'
           b'#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tfixture-session\n'
           b'.example.com\tTRUE\t/\tTRUE\t2147483647\tPRIVATE\tunrelated-account\n')


class CookieSessionTests(unittest.TestCase):
    def test_only_youtube_cookies_are_retained(self):
        result = normalize_cookies(COOKIES)
        self.assertIn(b'fixture-session', result)
        self.assertNotIn(b'unrelated-account', result)
        self.assertIn(b'#HttpOnly_.youtube.com', result)

    def test_invalid_expired_and_oversized_files_are_rejected(self):
        for value in [b'{"cookies":[]}', b'x' * (MAX_COOKIE_BYTES + 1),
                      COOKIES.replace(b'2147483647', b'1'),
                      COOKIES.replace(b'.youtube.com', b'.youtube.com.evil.test')]:
            with self.subTest(value=value[:40]), self.assertRaises(ValueError):
                normalize_cookies(value)

    def test_session_snapshot_is_scoped_and_removed_after_failure(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, CLIPCANVAS_DATA='qa'):
            data = Path(temporary)
            save_session(data, COOKIES)
            if os.name == 'nt':
                self.assertNotIn(b'fixture-session', session_path(data).read_bytes())
            with cookie_arguments(data, 'https://example.com/video') as args:
                self.assertEqual(args, [])
            with cookie_arguments(data, 'https://youtube.com.evil.test/video') as args:
                self.assertEqual(args, [])
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                with cookie_arguments(data, 'https://youtu.be/abc') as args:
                    temporary_cookie = Path(args[1])
                    self.assertEqual(args[0], '--cookies')
                    self.assertIn(b'fixture-session', temporary_cookie.read_bytes())
                    raise RuntimeError('cancelled')
            self.assertFalse(temporary_cookie.exists())
            self.assertTrue(session_path(data).exists())

    def test_media_metadata_cannot_persist_session_headers(self):
        value = {'title': 'Video', 'cookies': 'secret', 'formats': [
            {'url': 'https://cdn.example/video', 'http_headers': {'Cookie': 'secret',
             'Authorization': 'secret', 'User-Agent': 'client'}, 'cookies': 'secret'}]}
        result = without_auth_metadata(value)
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(result['formats'][0]['http_headers'], {'User-Agent': 'client'})
        self.assertEqual(value['cookies'], 'secret')

    def test_api_import_status_delete_and_invalid_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            app = FastAPI()
            register_url_routes(app, dict(DATA=data, FFMPEG='ffmpeg', update=lambda *a, **k: None,
                cancelled=lambda job: False, create_job=lambda *a: None, LOCK=threading.RLock(),
                PROCESSES={}, Cancelled=RuntimeError))
            with TestClient(app) as client:
                endpoint = '/api/links/youtube-session'
                self.assertFalse(client.get(endpoint).json()['connected'])
                response = client.post(endpoint, files={'file': ('cookies.txt', COOKIES, 'text/plain')})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'connected': True})
                before = session_path(data).read_bytes()
                response = client.post(endpoint, files={'file': ('cookies.txt', b'invalid', 'text/plain')})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(session_path(data).read_bytes(), before)
                self.assertNotIn('fixture-session', client.get(endpoint).text)
                self.assertFalse(client.delete(endpoint).json()['connected'])
                self.assertFalse(session_path(data).exists())


if __name__ == '__main__':
    unittest.main()
