"""Real moving overlay frames, phase across edits, audio mixing and browser media."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'engine'), str(ROOT/'.runtime/deps')]
TEST_DATA = tempfile.TemporaryDirectory(prefix='video-banner-unit-', dir=ROOT/'.runtime/tmp')
previous = os.environ.get('CLIPCANVAS_DATA')
os.environ['CLIPCANVAS_DATA'] = TEST_DATA.name
import clipcanvas_server as server
if previous is None:
    os.environ.pop('CLIPCANVAS_DATA', None)
else:
    os.environ['CLIPCANVAS_DATA'] = previous
import av
import numpy as np
from fastapi.testclient import TestClient


class VideoBannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = Path(TEST_DATA.name)
        (cls.directory/'cache').mkdir(exist_ok=True)
        (cls.directory/'uploads').mkdir(exist_ok=True)
        (cls.directory/'jobs/job').mkdir(parents=True, exist_ok=True)
        cls.source_path = cls.directory/'source.mp4'
        cls.make_movie(cls.source_path, None, 4, 440)
        cls.source = {**server.probe(cls.source_path), 'path': cls.source_path}
        cls.blue_path = cls.directory/'blue.mp4'
        cls.red_path = cls.directory/'red.mp4'
        cls.make_movie(cls.blue_path, (0, 0, 255), 1, 880, audio_duration=2)
        cls.make_movie(cls.red_path, (255, 0, 0), 1, 880)
        cls.blue = cls.record(cls.blue_path)
        cls.red = cls.record(cls.red_path)

    @classmethod
    def record(cls, path):
        return {**server.banner_metadata(path), 'path': path, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    @classmethod
    def make_movie(cls, path, background, duration, frequency, audio_duration=None):
        size = (80, 40) if background else (240, 300)
        frames = []
        for index in range(round(duration*24)):
            frame = np.full((size[1], size[0], 3), background or (60, 60, 60), dtype=np.uint8)
            if background:
                color = (255, 255, 0) if index < 12 else (0, 255, 0)
                frame[10:30, 25:55] = color
            else:
                x = 140+index%65
                frame[190:210, x:x+20] = (255, 255, 255)
            frames.append(frame)
        result = subprocess.run([server.FFMPEG,'-y','-hide_banner','-loglevel','error',
            '-f','rawvideo','-pixel_format','rgb24','-video_size',f'{size[0]}x{size[1]}','-framerate','24','-i','pipe:0',
            '-f','lavfi','-i',f'sine=frequency={frequency}:sample_rate=48000:duration={audio_duration or duration}',
            '-c:v','libx264','-crf','0','-preset','ultrafast','-pix_fmt','yuv444p','-c:a','aac',str(path)],
            input=np.stack(frames).tobytes(),capture_output=True,timeout=30)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace'))

    def setUp(self):
        self.jobs = {'job': {}}
        self.asset = self.blue
        self.patchers = [patch.object(server,'DATA',self.directory), patch.object(server,'JOBS',self.jobs),
                         patch.object(server,'asset',side_effect=lambda _id, kind=None: self.asset)]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def render(self, name, banner=None, start=0, end=4):
        item = {'enabled':True,'asset_id':'b'*32,'start':.5,'end':3.5,'loop':True,
                'geometry':{'x':20,'y':30,'w':80,'h':40},
                'chromakey':{'enabled':True,'color':'#0000ff','similarity':.1,'blend':.02}, **(banner or {})}
        request = server.Render.model_validate({'source_id':'a'*32,'canvas':{'width':240,'height':300},
            'video':{'x':0,'y':0,'w':240,'h':300},'background':{'enabled':False,'color':'#000000'},
            'parts':[{'start':start,'end':end}],'fps':24,'encoder':'cpu','banners':[item]})
        server.render_segment('job',request,self.source,
            {'start':start,'end':end,'duration':end-start,'output_start':0},name,0,100)
        path = self.directory/'jobs/job'/name
        with av.open(str(path)) as container:
            frames = [frame.to_ndarray(format='rgb24') for frame in container.decode(video=0)]
        return path, frames

    def audio(self, path):
        result = subprocess.run([server.FFMPEG,'-v','error','-i',str(path),'-vn','-ac','1','-ar','48000',
            '-f','f32le','pipe:1'],capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0)
        return np.frombuffer(result.stdout,dtype=np.float32)

    def tone(self, signal, frequency):
        # A whole second in the active overlay interval gives integer-frequency bins.
        clip = signal[48000:96000]
        return abs(np.dot(clip,np.exp(-2j*np.pi*frequency*np.arange(len(clip))/48000)))/len(clip)

    def test_looped_moving_key_keeps_main_video_and_default_audio(self):
        path, frames = self.render('muted.mp4')
        self.assertEqual(len(frames),96)
        self.assertGreater(frames[18][50,60,0],220) # yellow first half at .75s
        self.assertLess(frames[30][50,60,0],30) # green second half at1.25s
        self.assertGreater(frames[42][50,60,0],220) # loops at1.75s
        self.assertTrue(np.max(np.abs(frames[30][35,25].astype(int)-60)) < 12, 'Blue key reveals source')
        self.assertFalse(np.array_equal(frames[18][190:210,140:230],frames[30][190:210,140:230]), 'Main source must continue')
        audio = self.audio(path)
        self.assertGreater(self.tone(audio,440),.03)
        self.assertLess(self.tone(audio,880),.002, 'Video overlay is muted by default')

    def test_no_loop_disappears_instead_of_freezing_or_shortening_main(self):
        _, frames = self.render('once.mp4',{'loop':False,'offset':.5})
        self.assertEqual(len(frames),96)
        self.assertLess(frames[18][50,60,0],30) # initial green half
        self.assertGreater(frames[18][50,60,1],220)
        self.assertTrue(np.max(np.abs(frames[30][50,60].astype(int)-60)) < 12)
        self.assertFalse(np.array_equal(frames[50][190:210,140:230],frames[70][190:210,140:230]))

    def test_cut_segment_phase_matches_original_time_with_offset(self):
        _, full = self.render('offset-full.mp4',{'offset':.25})
        _, later = self.render('offset-later.mp4',{'offset':.25},start=1.5,end=3)
        for index in (3,9,15,21,27):
            self.assertTrue(np.max(np.abs(full[36+index][50,60].astype(int)-later[index][50,60])) < 12)

    def test_volume_mixes_without_extending_video_loop_to_longer_audio(self):
        path, frames = self.render('mixed.mp4',{'volume':.5,'offset':.75})
        # Source overlay has 1s video and 2s audio. Visual looping stays at1s with sound on.
        self.assertGreater(frames[27][50,60,0],220) # t1.125: asset1.375=>yellow
        self.assertGreater(frames[51][50,60,0],220) # t2.125 repeats, no blank gap
        self.assertLess(frames[39][50,60,0],30)
        self.assertGreater(frames[39][50,60,1],220)
        audio = self.audio(path)
        self.assertGreater(self.tone(audio,440),.03)
        self.assertGreater(self.tone(audio,880),.015)

    def test_red_background_key_updates_each_frame(self):
        self.asset = self.red
        _, frames = self.render('red.mp4',{'chromakey':{'enabled':True,'color':'#ff0000','similarity':.1,'blend':.02}})
        self.assertTrue(np.max(np.abs(frames[18][35,25].astype(int)-60)) < 12)
        self.assertGreater(frames[18][50,60,0],220)
        self.assertLess(frames[30][50,60,0],30)

    def test_video_poster_changes_with_time_and_proxy_keeps_sound(self):
        first = server.banner_preview(server.BannerPreview(asset_id='b'*32,time=.1,
            chromakey={'enabled':True,'color':'#0000ff'}))
        last = server.banner_preview(server.BannerPreview(asset_id='b'*32,time=.7,
            chromakey={'enabled':True,'color':'#0000ff'}))
        self.assertNotEqual(first['url'],last['url'])
        for info, red in [(first,True),(last,False)]:
            with av.open(str(self.directory/'cache/banners'/info['url'].split('/')[-1])) as container:
                image = next(container.decode(video=0)).to_ndarray(format='rgba')
            self.assertEqual(image[0,0,3],0)
            self.assertEqual(bool(image[20,40,0]>200),red)
        proxy = server.banner_media('b'*32)
        metadata = server.probe(Path(proxy.path))
        self.assertTrue(metadata['audio'])
        self.assertAlmostEqual(metadata['duration'],1,places=1)
        self.assertEqual(proxy.media_type,'video/mp4')

    def test_upload_keeps_movie_and_animated_gif_in_motion(self):
        client = TestClient(server.app,base_url='http://127.0.0.1')
        response = client.post('/api/uploads?kind=banner',files={'file':('banner.mp4',self.blue_path.read_bytes(),'video/mp4')})
        self.assertEqual(response.status_code,200,response.text)
        info = response.json()
        self.assertEqual(info['media_type'],'video')
        self.assertTrue(info['stored_name'].endswith('.mp4'))
        self.assertAlmostEqual(info['duration'],1,places=1)
        gif = self.directory/'banner.gif'
        made = subprocess.run([server.FFMPEG,'-y','-v','error','-i',str(self.blue_path),'-an','-t','1',str(gif)],capture_output=True,timeout=30)
        self.assertEqual(made.returncode,0)
        response = client.post('/api/uploads?kind=banner',files={'file':('banner.gif',gif.read_bytes(),'image/gif')})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['media_type'],'video')
        self.assertGreater(response.json()['duration'],.5)
        self.assertTrue(response.json()['has_alpha'])

    def test_transparent_animation_keeps_alpha_in_browser_and_export(self):
        pixels = np.zeros((4,40,80,4),dtype=np.uint8)
        pixels[:2,10:30,25:55] = (255,0,0,255)
        pixels[2:,10:30,25:55] = (0,255,0,255)
        gif = self.directory/'transparent.gif'
        made = subprocess.run([server.FFMPEG,'-y','-v','error','-f','rawvideo','-pixel_format','rgba',
            '-video_size','80x40','-framerate','4','-i','pipe:0','-filter_complex',
            '[0:v]split[a][b];[a]palettegen=reserve_transparent=1[p];[b][p]paletteuse',str(gif)],
            input=pixels.tobytes(),capture_output=True,timeout=30)
        self.assertEqual(made.returncode,0,made.stderr.decode(errors='replace'))
        self.asset = self.record(gif)
        response = server.banner_media('b'*32)
        self.assertEqual(response.media_type,'video/webm')
        decoded = subprocess.run([server.FFMPEG,'-v','error','-c:v','libvpx-vp9','-i',str(response.path),
            '-frames:v','1','-f','rawvideo','-pixel_format','rgba','-pix_fmt','rgba','pipe:1'],capture_output=True,timeout=30)
        self.assertEqual(decoded.returncode,0,decoded.stderr.decode(errors='replace'))
        image = np.frombuffer(decoded.stdout,dtype=np.uint8).reshape(40,80,4)
        self.assertEqual(image[0,0,3],0)
        self.assertEqual(image[20,40,3],255)
        _, frames = self.render('transparent.mp4',{'chromakey':{'enabled':False}})
        self.assertTrue(np.max(np.abs(frames[18][35,25].astype(int)-60)) < 12)
        self.assertGreater(frames[18][50,60,0],220)
        self.assertGreater(frames[30][50,60,1],220)


if __name__ == '__main__':
    unittest.main()
