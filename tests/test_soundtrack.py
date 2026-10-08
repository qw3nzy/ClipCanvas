"""Audio inventory, replacement compatibility, and clip soundtrack clocks."""
from pathlib import Path
import sys
import tempfile
import unittest
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'engine'),str(ROOT/'.runtime/deps')]
from clipcanvas_audio import SoundtrackSettings,probe_audio,soundtrack_elapsed,soundtrack_window
from clipcanvas_pro import AudioSettings,effective_payload


class SoundtrackTests(unittest.TestCase):
    def test_audio_only_wav_is_accepted_without_video(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.runtime/qa') as directory:
            path=Path(directory)/'synthetic.wav'
            with wave.open(str(path),'wb') as output:
                output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(b'\0\0'*20000)
            inventory=probe_audio(path)
            self.assertEqual(inventory['media_type'],'audio')
            self.assertAlmostEqual(inventory['duration'],1.25)
            self.assertEqual(inventory['recommended_audio_stream_index'],0)

    def test_defaults_are_neutral_and_enabled_track_requires_a_file(self):
        self.assertFalse(AudioSettings().mute_source)
        self.assertFalse(SoundtrackSettings().enabled)
        for payload in ({'enabled':True},{'volume':1.01},{'start':-1},{'offset':float('nan')},{'asset_id':'../file'}):
            with self.subTest(payload=payload),self.assertRaises(ValueError):SoundtrackSettings.model_validate(payload)

    def test_start_offset_loop_and_end_padding(self):
        settings=SoundtrackSettings(enabled=True,asset_id='a'*32,start=.5,offset=1)
        segment={'kind':'source','output_start':0,'duration':2}
        self.assertEqual(soundtrack_window(settings,3,segment),{'position':1,'delay':.5,'duration':1.5})
        self.assertIsNone(soundtrack_window(settings,3,{**segment,'output_start':3}))
        settings.loop=True
        self.assertEqual(soundtrack_window(settings,3,{**segment,'output_start':3})['position'],.5)

    def test_ad_pause_retains_file_position_and_final_clip_start(self):
        plan=[{'kind':'source','output_start':0,'duration':2},{'kind':'advert','output_start':2,'duration':1},
              {'kind':'source','output_start':3,'duration':2}]
        settings=SoundtrackSettings(enabled=True,asset_id='a'*32,start=.5)
        self.assertEqual(soundtrack_elapsed(settings,plan,3),1.5)
        self.assertIsNone(soundtrack_window(settings,10,plan[1]))
        settings.start=2.5
        self.assertEqual(soundtrack_elapsed(settings,plan,3),0)
        settings.pause_for_ads=False
        self.assertEqual(soundtrack_elapsed(settings,plan,3),.5)

    def test_global_source_mute_survives_existing_clip_audio_override(self):
        payload={'audio':{'mute_source':True,'source_volume':.8},'advertising':{'enabled':False},
            'subtitles':{},'clip_overrides':{'1':{'audio':{'source_volume':.5,'mute_source':False}}}}
        result,_=effective_payload(payload,{'start':0,'end':2},1)
        self.assertTrue(result['audio']['mute_source'])
        self.assertEqual(result['audio']['source_volume'],.5)
        self.assertFalse(payload['clip_overrides']['1']['audio']['mute_source'])


if __name__=='__main__':unittest.main()
