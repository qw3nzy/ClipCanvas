"""Multi-frame automatic background checks using actual MP4 and GIF fixtures."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'engine'))
sys.path.insert(0, str(ROOT/'.runtime'/'deps'))

import imageio_ffmpeg
import numpy as np
from clipcanvas_composition import estimate_banner_key


class VideoBannerKeyTests(unittest.TestCase):
    def setUp(self):
        qa = ROOT/'.runtime'/'qa'
        qa.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix='video-banner-key-', dir=qa)
        self.addCleanup(self.directory.cleanup)

    def fixture(self, suffix='mp4', changing=False, uncertain=False):
        frames = np.full((20, 64, 96, 3), [0, 240, 0], dtype=np.uint8)
        if changing:
            # Identical first and last colours must not hide the blue middle.
            frames[7:14] = [0, 0, 240]
        if uncertain:
            frames[7:14] = np.random.default_rng(42).integers(0, 256, (7, 64, 96, 3), dtype=np.uint8)
        for index, pixels in enumerate(frames):
            x = 22+index % 12
            pixels[18:46, x:x+36] = [240, 25, 45]
        path = Path(self.directory.name)/('banner.'+suffix)
        command = [imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-hide_banner', '-loglevel', 'error',
                   '-filter_complex_threads', '1', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                   '-video_size', '96x64', '-framerate', '5', '-i', 'pipe:0']
        if suffix == 'gif':
            command += ['-filter_complex', '[0:v]split[a][b];[a]palettegen[p];[b][p]paletteuse', '-loop', '0']
        else:
            command += ['-c:v', 'libx264', '-crf', '0', '-pix_fmt', 'yuv444p']
        result = subprocess.run([*command, str(path)], input=frames.tobytes(), capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        return path

    def test_video_and_gif_keep_a_consistent_background_across_motion(self):
        for suffix in ['mp4', 'gif']:
            with self.subTest(media=suffix):
                path = self.fixture(suffix)
                before = path.read_bytes()
                result = estimate_banner_key(path)
                self.assertIsNotNone(result['chromakey'], result)
                self.assertGreaterEqual(result['sampled_frames'], 5)
                self.assertGreater(int(result['chromakey']['color'][3:5], 16), 220)
                self.assertLess(int(result['chromakey']['color'][1:3], 16), 20)
                self.assertGreaterEqual(result['confidence'], .92)
                self.assertEqual(path.read_bytes(), before)

    def test_video_and_gif_refuse_a_different_background_in_the_middle(self):
        for suffix in ['mp4', 'gif']:
            with self.subTest(media=suffix):
                result = estimate_banner_key(self.fixture(suffix, changing=True))
                self.assertIsNone(result['chromakey'], result)
                self.assertGreaterEqual(result['sampled_frames'], 5)
                self.assertIn('между кадрами', result['reason'])

    def test_a_single_uncertain_sample_prevents_keying_the_whole_video(self):
        result = estimate_banner_key(self.fixture(uncertain=True))
        self.assertIsNone(result['chromakey'])
        self.assertGreaterEqual(result['sampled_frames'], 5)

    def test_cancellation_interrupts_decoding_without_modifying_asset(self):
        path = self.fixture()
        before = path.read_bytes()
        calls = 0

        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 6

        with self.assertRaises(InterruptedError):
            estimate_banner_key(path, cancelled=cancelled)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(calls, 6)

    def test_optional_duration_keeps_the_existing_call_contract(self):
        path = self.fixture()
        self.assertEqual(estimate_banner_key(path), estimate_banner_key(path, duration=4))

    def test_webm_alpha_metadata_is_preserved_even_if_native_decoder_drops_alpha(self):
        path = Path(self.directory.name)/'transparent.webm'
        frames = np.full((5, 64, 96, 4), [0, 240, 0, 0], dtype=np.uint8)
        frames[:, 18:46, 22:58] = [240, 25, 45, 255]
        command = [imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-hide_banner', '-loglevel', 'error',
                   '-f', 'rawvideo', '-pixel_format', 'rgba', '-video_size', '96x64', '-framerate', '5',
                   '-i', 'pipe:0', '-c:v', 'libvpx-vp9', '-pix_fmt', 'yuva420p', '-lossless', '1', str(path)]
        completed = subprocess.run(command, input=frames.tobytes(), capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode('utf-8', errors='replace'))
        result = estimate_banner_key(path)
        self.assertIsNone(result['chromakey'])
        self.assertIn('канал прозрачности', result['reason'])


if __name__ == '__main__':
    unittest.main()
