"""Banner transforms and conservative background estimation, including FFmpeg pixels."""
import io
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'engine'))
sys.path.insert(0, str(ROOT/'.runtime'/'deps'))

import av
import imageio_ffmpeg
import numpy as np
from clipcanvas_composition import (banner_overlay_position, banner_rotation_filter,
                                   estimate_banner_key, normalize_rotation)
from clipcanvas_layout import BannerRules, LayoutRect, choose_layout, intersection, rotated_bounds


def write_png(path, pixels):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload))+kind+payload+struct.pack('>I', zlib.crc32(kind+payload))
    height, width = pixels.shape[:2]
    raw = b''.join(b'\0'+row.tobytes() for row in pixels)
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))+
                     chunk(b'IDAT', zlib.compress(raw))+chunk(b'IEND', b''))


class BannerRotationTests(unittest.TestCase):
    def test_normalizes_wrapped_angles_and_rejects_invalid_values(self):
        for value, expected in [(0, 0), (360, 0), (450, 90), (-450, -90), (180, -180),
                                (179.9999999, -180), (720.25, .25)]:
            self.assertEqual(normalize_rotation(value), expected)
            self.assertEqual(LayoutRect(x=0, y=0, w=80, h=40, rotation=value).rotation, expected)
        for value in [math.nan, math.inf, -math.inf, True, None, 'invalid']:
            with self.assertRaises(ValueError):
                normalize_rotation(value)

    def test_layout_retains_angle_and_avoids_rotated_overlap(self):
        original = dict(x=100, y=100, w=160, h=40, fit='contain', rotation=45)
        requirements = dict(can_adapt=True, lock_size=False, lock_position=False, min_width=0,
                            min_height=0, max_width=0, max_height=0, zone='any', reason='')
        # This region misses the unrotated rectangle, but intersects its rotated top.
        protected = [dict(x=160, y=60, w=40, h=20)]
        self.assertIsNone(choose_layout(original, BannerRules(movable=False), requirements, protected, 400, 400))
        result = choose_layout(original, BannerRules(), requirements, protected, 400, 400)
        self.assertIsNotNone(result)
        self.assertEqual((result['rotation'], result['w'], result['h']), (45, 160, 40))
        bounds = rotated_bounds(result)
        self.assertGreaterEqual(bounds['x'], -.001)
        self.assertGreaterEqual(bounds['y'], -.001)
        self.assertLessEqual(bounds['x']+bounds['w'], 400.001)
        self.assertLessEqual(bounds['y']+bounds['h'], 400.001)
        self.assertIsNone(intersection(bounds, protected[0]))

    def test_rotated_bounds_must_fit_inside_requested_zone(self):
        requirements = dict(can_adapt=True, lock_size=False, lock_position=False, min_width=0,
                            min_height=0, max_width=0, max_height=0, zone='top', reason='')
        result = choose_layout(dict(x=60, y=40, w=120, h=40, rotation=90), BannerRules(),
                               requirements, [], 240, 240)
        self.assertIsNotNone(result)
        bounds = rotated_bounds(result)
        self.assertLessEqual(bounds['y']+bounds['h'], 120.001)

    def test_real_ffmpeg_rotates_clockwise_about_geometry_center(self):
        pixels = np.full((40, 80, 4), [255, 0, 0, 255], dtype=np.uint8)
        pixels[:, 60:] = [0, 0, 255, 255]
        geometry = dict(x=40, y=60, w=80, h=40, rotation=90)
        x, y = banner_overlay_position(geometry)
        qa = ROOT/'.runtime'/'qa'
        qa.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='banner-rotation-', dir=qa) as directory:
            output = Path(directory)/'rotated.mp4'
            graph = (banner_rotation_filter('0:v', geometry['rotation'], 'banner')+
                     f';[1:v][banner]overlay=x={x}:y={y}:shortest=1:format=auto[out]')
            command = [imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-hide_banner', '-loglevel', 'error',
                       '-filter_complex_threads', '1', '-f', 'rawvideo', '-pixel_format', 'rgba',
                       '-video_size', '80x40', '-framerate', '1', '-i', 'pipe:0', '-f', 'lavfi',
                       '-i', 'color=c=black:s=160x160:r=1:d=1', '-filter_complex', graph,
                       '-map', '[out]', '-frames:v', '1', '-c:v', 'libx264', '-crf', '0',
                       '-pix_fmt', 'yuv444p', str(output)]
            result = subprocess.run(command, input=pixels.tobytes(), capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
            with av.open(str(output)) as container:
                image = next(container.decode(video=0)).to_ndarray(format='rgb24')
            self.assertGreater(image[50, 80, 0], 240, 'left red section must rotate to the top')
            self.assertGreater(image[110, 80, 2], 240, 'right blue section must rotate to the bottom')
            self.assertLess(image[80, 50].max(), 10, 'old unrotated left edge must remain background')
            self.assertLess(image[30, 80].max(), 10, 'rotated top must not shift from the original centre')
            nonblack = np.any(image > 30, axis=2)
            ys, xs = np.where(nonblack)
            self.assertAlmostEqual((xs.min()+xs.max())/2, 79.5, delta=1)
            self.assertAlmostEqual((ys.min()+ys.max())/2, 79.5, delta=1)

    def test_oblique_rotation_expands_without_clipping_and_keeps_transparent_corners(self):
        pixels = np.full((40, 80, 4), [255, 0, 0, 255], dtype=np.uint8)
        command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
                   '-filter_complex_threads', '1', '-f', 'rawvideo', '-pixel_format', 'rgba',
                   '-video_size', '80x40', '-framerate', '1', '-i', 'pipe:0', '-filter_complex',
                   banner_rotation_filter('0:v', 45, 'out'), '-map', '[out]', '-frames:v', '1',
                   '-c:v', 'png', '-f', 'image2pipe', 'pipe:1']
        result = subprocess.run(command, input=pixels.tobytes(), capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        with av.open(io.BytesIO(result.stdout)) as container:
            image = next(container.decode(video=0)).to_ndarray(format='rgba')
        self.assertEqual(image.shape[:2], (91, 91))
        self.assertEqual(image[45, 45, 3], 255)
        self.assertTrue(all(image[y, x, 3] == 0 for y, x in [(0, 0), (0, 90), (90, 0), (90, 90)]))
        self.assertTrue(np.any((image[:, :, 3] > 0) & (image[:, :, 3] < 255)), 'edges need alpha interpolation')
        self.assertAlmostEqual(float(image[:, :, 3].sum())/255, 80*40, delta=100)


class BannerBackgroundTests(unittest.TestCase):
    def setUp(self):
        qa = ROOT/'.runtime'/'qa'
        qa.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix='banner-background-', dir=qa)
        self.addCleanup(self.directory.cleanup)

    def estimate(self, pixels):
        path = Path(self.directory.name)/'banner.png'
        write_png(path, pixels)
        original = path.read_bytes()
        result = estimate_banner_key(path)
        self.assertEqual(path.read_bytes(), original, 'auto key must be non-destructive')
        return result

    def test_detects_green_blue_red_and_white_backgrounds(self):
        for color in [(0, 255, 0), (0, 0, 255), (255, 0, 0), (255, 255, 255)]:
            with self.subTest(color=color):
                pixels = np.full((80, 120, 4), [*color, 255], dtype=np.uint8)
                pixels[20:60, 25:95] = [60, 30, 90, 255]
                result = self.estimate(pixels)
                self.assertEqual(result['chromakey']['color'], '#'+''.join(f'{c:02x}' for c in color))
                self.assertGreaterEqual(result['confidence'], .92)
                self.assertTrue(result['chromakey']['enabled'])

    def test_mild_compression_noise_is_accepted(self):
        rng = np.random.default_rng(55)
        pixels = np.full((80, 120, 4), [20, 235, 20, 255], dtype=np.uint8)
        noise = rng.integers(-3, 4, size=(80, 120, 3))
        pixels[:, :, :3] = np.clip(pixels[:, :, :3].astype(int)+noise, 0, 255)
        pixels[20:60, 25:95] = [180, 30, 90, 255]
        self.assertIsNotNone(self.estimate(pixels)['chromakey'])

    def test_transparent_edges_photographic_and_solid_images_are_declined(self):
        transparent = np.full((80, 120, 4), [0, 255, 0, 0], dtype=np.uint8)
        transparent[20:60, 25:95] = [255, 0, 0, 255]
        noise = np.random.default_rng(32).integers(0, 256, (80, 120, 4), dtype=np.uint8)
        noise[:, :, 3] = 255
        flat = np.full((80, 120, 4), [0, 255, 0, 255], dtype=np.uint8)
        for pixels in [transparent, noise, flat]:
            self.assertIsNone(self.estimate(pixels)['chromakey'])

    def test_conflicting_corners_and_gradient_are_declined(self):
        split = np.full((80, 120, 4), [255, 0, 0, 255], dtype=np.uint8)
        split[:, :60] = [0, 0, 255, 255]
        gradient = split.copy()
        gradient[:, :, 0] = np.arange(120)*2
        gradient[:, :, 1] = 0
        gradient[:, :, 2] = 0
        for pixels in [split, gradient]:
            self.assertIsNone(self.estimate(pixels)['chromakey'])


if __name__ == '__main__':
    unittest.main()
