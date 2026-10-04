"""Encoder detection must reflect driver availability, with a usable CPU fallback."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine'))
from clipcanvas_encoding import available_encoders, select_encoder


class EncoderTests(unittest.TestCase):
    def tearDown(self):
        available_encoders.cache_clear()

    def test_compiled_encoder_without_working_gpu_falls_back(self):
        with patch('clipcanvas_encoding.subprocess.run', return_value=subprocess.CompletedProcess([], 1)):
            self.assertEqual(select_encoder('auto', available_encoders('ffmpeg')), 'cpu')
            with self.assertRaises(ValueError):
                select_encoder('nvidia', available_encoders('ffmpeg'))

    def test_working_gpu_is_probed_once_and_selected(self):
        with patch('clipcanvas_encoding.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(select_encoder('auto', available_encoders('ffmpeg')), 'nvidia')
            self.assertEqual(select_encoder('cpu', available_encoders('ffmpeg')), 'cpu')
            run.assert_called_once()

    def test_probe_timeout_leaves_cpu_available(self):
        with patch('clipcanvas_encoding.subprocess.run', side_effect=subprocess.TimeoutExpired('ffmpeg', 4)):
            self.assertEqual(available_encoders('ffmpeg'), ('cpu',))


if __name__ == '__main__':
    unittest.main()
