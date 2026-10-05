"""Small deterministic tests for the Yoinks adapter (no network requests)."""
import sys
from pathlib import Path
import io
import json
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / '.runtime' / 'deps'), str(ROOT / 'engine')]
from fastapi import FastAPI
from url_import import (build_choices, validate_public_url, _format_selector,
                        _download_error, DownloaderError, register_url_routes)


def public_resolver(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port))]


class PublicUrlTests(unittest.TestCase):
    def test_accepts_public_https(self):
        self.assertEqual(validate_public_url('https://example.com/video.mp4', public_resolver), 'https://example.com/video.mp4')

    def test_rejects_local_addresses(self):
        for address in ['127.0.0.1', '10.0.0.1', '192.168.1.10', '169.254.169.254', '::1', 'fd00::1']:
            def resolver(host, port, **kwargs):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, port))]
            with self.subTest(address=address), self.assertRaises(ValueError):
                validate_public_url('https://example.com/movie', resolver)

    def test_rejects_mixed_dns_public_and_private(self):
        def resolver(host, port, **kwargs):
            return public_resolver(host, port) + [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))]
        with self.assertRaises(ValueError):
            validate_public_url('https://example.com/movie', resolver)

    def test_rejects_credentials_schemes_and_control_chars(self):
        for value in ['file:///C:/Windows/test', 'ftp://example.com/movie', 'https://user:password@example.com/movie',
                      'https://localhost/movie', 'https://host.local/movie', 'https://example.com:8787/movie',
                      'https://example.com/\nmovie']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_public_url(value, public_resolver)


class ChoicesTests(unittest.TestCase):
    def test_resolution_deduplication_order_and_separate_audio_size(self):
        formats = [{'height': 720, 'vcodec': 'avc1', 'acodec': 'none', 'ext': 'mp4', 'filesize': 1000},
                   {'height': 1080, 'vcodec': 'avc1', 'acodec': 'none', 'ext': 'mp4', 'filesize': 2000},
                   {'height': 720, 'vcodec': 'vp9', 'acodec': 'none', 'ext': 'webm', 'filesize': 800},
                   {'vcodec': 'none', 'acodec': 'aac', 'abr': 128, 'filesize': 100}]
        choices = build_choices({'formats': formats})
        self.assertEqual([item['id'] for item in choices], ['h1080', 'h720'])
        self.assertEqual(choices[0]['estimated_bytes'], 2100)
        self.assertEqual(choices[1]['estimated_bytes'], 1100)

    def test_muxed_audio_not_counted_twice(self):
        choices = build_choices({'formats': [
            {'height': 720, 'vcodec': 'avc1', 'acodec': 'aac', 'filesize': 1000},
            {'vcodec': 'none', 'acodec': 'aac', 'filesize': 100}]})
        self.assertEqual(choices[0]['estimated_bytes'], 1000)

    def test_direct_file_fallback(self):
        self.assertEqual(build_choices({})[0]['id'], 'best')
        self.assertEqual(_format_selector('best'), 'bv*+ba/b')


class DownloadFailureTests(unittest.TestCase):
    def test_bot_challenge_is_not_reported_as_private_video_or_generic_403(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / 'download.log'
            log.write_text('WARNING: HTTP Error 429: Too Many Requests\n'
                           'ERROR: [youtube] Sign in to confirm you are not a bot. HTTP 403\n', 'utf-8')
            error = _download_error(log)
            self.assertEqual(error.reason, 'bot_verification')
            self.assertIn('ПК с движком', str(error))

    def test_only_rejected_media_urls_can_trigger_refresh(self):
        examples = [('ERROR: unable to download video data: HTTP Error 403: Forbidden', 'media_url_rejected'),
                    ('ERROR: unable to download video data: HTTP Error 410: Gone', 'media_url_rejected'),
                    ('ERROR: HTTP Error 429: Too Many Requests', 'rate_limited'),
                    ('ERROR: Private video. Sign in', 'authentication'),
                    ('ERROR: Unable to download webpage: HTTP Error 403: Forbidden', 'forbidden')]
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / 'download.log'
            for message, reason in examples:
                with self.subTest(reason=reason, message=message):
                    log.write_text(message, 'utf-8')
                    self.assertEqual(_download_error(log).reason, reason)

    def test_unknown_errors_do_not_expose_signed_urls(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / 'download.log'
            log.write_text('ERROR: failure at https://cdn.example/video?token=private', 'utf-8')
            self.assertNotIn('private', str(_download_error(log)))


class RefreshDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        self.inspect_id, self.job_id = 'a' * 32, 'b' * 32
        self.inspected = self.data / 'jobs' / self.inspect_id
        self.directory = self.data / 'jobs' / self.job_id
        self.inspected.mkdir(parents=True)
        self.directory.mkdir()
        (self.data / 'uploads').mkdir()
        self.info = {'id': 'demo', 'title': 'Demo', 'formats': [
            {'format_id': '18', 'height': 720, 'vcodec': 'avc1', 'acodec': 'aac',
             'url': 'https://cdn.example/original.mp4'}]}
        (self.inspected / 'link-info.json').write_text(json.dumps(self.info), 'utf-8')
        self.record = {'url': 'https://example.com/watch/demo', 'created': time.time(),
                       'choices': [{'id': 'h720', 'estimated_bytes': 1000}]}
        (self.inspected / 'link-request.json').write_text(json.dumps(self.record), 'utf-8')
        self.commands, self.updates, self.handlers = [], [], {}
        self.is_cancelled = False
        self.cancel_type = type('Cancelled', (Exception,), {})
        self.processes = {}
        services = dict(DATA=self.data, update=lambda job, **values: self.updates.append(values),
                        cancelled=lambda job: self.is_cancelled, create_job=lambda *a: None,
                        FFMPEG='ffmpeg', PROCESSES=self.processes, LOCK=threading.RLock(),
                        Cancelled=self.cancel_type, probe=lambda path: {'duration': 1, 'audio': True},
                        register_job_type=lambda kind, model, handler: self.handlers.update({kind: handler}))
        register_url_routes(FastAPI(), services)
        self.request = {'inspect_job_id': self.inspect_id, 'choice_id': 'h720'}

    def run_steps(self, steps, validator=None):
        steps = iter(steps)

        def popen(command, **kwargs):
            self.commands.append(command)
            kind, content = next(steps)
            is_probe = '--dump-single-json' in command
            self.assertEqual(is_probe, kind == 'probe')
            code = 0
            if kind == 'fail':
                kwargs['stderr'].write('ERROR: ' + content + '\n')
                kwargs['stderr'].flush()
                code, output = 1, ''
            elif is_probe:
                output = json.dumps(content) + '\n'
            else:
                path = self.directory / 'download' / 'source.mp4'
                path.write_bytes(b'fixture')
                output = 'CLIPFILE|' + str(path) + '\n'
            return type('Process', (), {'stdout': io.StringIO(output),
                'poll': lambda process: code, 'wait': lambda process, timeout: code})()

        with patch('url_import.subprocess.Popen', side_effect=popen), \
             patch('url_import.validate_public_url', side_effect=validator or (lambda url: url)):
            self.handlers['url-download'](self.job_id, self.request)

    def test_rejected_cached_link_is_refreshed_and_imported_without_mutating_inspection(self):
        fresh = dict(self.info, title='Fresh title')
        self.run_steps([('fail', 'unable to download video data: HTTP Error 403: Forbidden'),
                        ('probe', fresh), ('download', None)])
        self.assertEqual(len(self.commands), 3)
        self.assertIn(str(self.directory / 'link-refreshed.json'), self.commands[-1])
        self.assertEqual(json.loads((self.inspected / 'link-info.json').read_text('utf-8')), self.info)
        asset = next(update['asset'] for update in self.updates if 'asset' in update)
        self.assertEqual(asset['name'], 'Fresh title')
        self.assertTrue((self.data / 'uploads' / asset['id'] / 'source.mp4').is_file())
        self.assertTrue((self.directory / 'download.log').is_file())
        self.assertTrue((self.directory / 'download-retry.log').is_file())
        self.assertEqual(self.processes, {})

    def test_second_rejection_stops_after_one_refresh(self):
        failure = ('fail', 'unable to download video data: HTTP Error 403: Forbidden')
        with self.assertRaises(DownloaderError):
            self.run_steps([failure, ('probe', self.info), failure])
        self.assertEqual(len(self.commands), 3)

    def test_rate_limit_does_not_send_another_request(self):
        with self.assertRaises(DownloaderError) as caught:
            self.run_steps([('fail', 'HTTP Error 429: Too Many Requests')])
        self.assertEqual(caught.exception.reason, 'rate_limited')
        self.assertEqual(len(self.commands), 1)

    def test_refreshed_media_destinations_are_validated_before_download(self):
        fresh = dict(self.info, formats=[dict(self.info['formats'][0], url='https://localhost/private')])
        def validator(url):
            if 'localhost' in url:
                raise ValueError('Private destination')
            return url
        with self.assertRaisesRegex(ValueError, 'Private destination'):
            self.run_steps([('fail', 'unable to download video data: HTTP Error 403: Forbidden'),
                            ('probe', fresh)], validator)
        self.assertEqual(len(self.commands), 2)

    def test_cancelled_job_does_not_start_a_downloader(self):
        self.is_cancelled = True
        with self.assertRaises(self.cancel_type):
            self.run_steps([])
        self.assertEqual(self.commands, [])


if __name__ == '__main__':
    unittest.main()
