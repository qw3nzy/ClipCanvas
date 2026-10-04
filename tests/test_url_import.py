"""Small deterministic tests for the Yoinks adapter (no network requests)."""
import sys
from pathlib import Path
import socket
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / '.runtime' / 'deps'), str(ROOT / 'engine')]
from url_import import build_choices, validate_public_url, _format_selector


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


if __name__ == '__main__':
    unittest.main()
