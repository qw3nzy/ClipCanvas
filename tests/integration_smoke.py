"""Exercise real media processing against an isolated ClipCanvas data directory.

Run with the app's Python: python -B tests/integration_smoke.py [--keep-server]
No user uploads, jobs, or models are changed. All fixtures live in .runtime/qa.
"""
from __future__ import annotations

import argparse
import array
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.runtime' / 'deps'))
import av

FFMPEG = next((ROOT / '.runtime' / 'deps' / 'imageio_ffmpeg' / 'binaries').glob('*.exe'))
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def command(args):
    result = subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-y', *args],
                            capture_output=True, creationflags=NO_WINDOW)
    if result.returncode:
        raise AssertionError(result.stderr.decode('utf-8', errors='replace')[-3000:])
    return result.stdout


def fixtures(directory):
    forced = directory / 'forced.ru.srt'
    forced.write_text('1\n00:00:00,100 --> 00:00:00,600\nВывеска\n', encoding='utf-8')
    full = directory / 'full.ru.srt'
    full.write_text('1\n00:00:00,500 --> 00:00:05,500\nКонтрольная реплика\n\n'
                    '2\n00:00:06,000 --> 00:00:07,200\nВторая фраза\n', encoding='utf-8')
    source = directory / 'source.mkv'
    command(['-f', 'lavfi', '-i', 'color=c=blue:s=256x144:r=24:d=8',
             '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=8',
             '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000:duration=8',
             '-i', str(forced), '-i', str(full), '-map', '0:v', '-map', '1:a', '-map', '2:a',
             '-map', '3:s', '-map', '4:s', '-c:v', 'libx264', '-preset', 'ultrafast',
             '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-c:s', 'srt',
             '-metadata:s:a:0', 'language=rus', '-metadata:s:a:1', 'language=eng',
             '-disposition:a:0', 'default', '-disposition:a:1', '0',
             '-metadata:s:s:0', 'language=rus', '-metadata:s:s:0', 'title=Только надписи',
             '-metadata:s:s:1', 'language=rus', '-metadata:s:s:1', 'title=Полные диалоги',
             '-disposition:s:0', 'default+forced', '-disposition:s:1', '0', str(source)])
    advert = directory / 'advert.mp4'
    command(['-f', 'lavfi', '-i', 'color=c=green:s=256x144:r=24:d=1',
             '-f', 'lavfi', '-i', 'sine=frequency=1320:sample_rate=48000:duration=1',
             '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
             '-c:a', 'aac', '-shortest', str(advert)])
    return source, advert


class Client:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, data=None, method=None, timeout=45):
        encoded = json.dumps(data, ensure_ascii=False).encode('utf-8') if data is not None else None
        req = urllib.request.Request(self.base + path, data=encoded, method=method,
                                     headers={'Content-Type': 'application/json'} if encoded else {})
        try:
            with self.opener.open(req, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise AssertionError(f'{req.method} {path}: HTTP {exc.code}: '
                                 + exc.read().decode('utf-8', errors='replace')) from exc

    def upload(self, path, kind):
        boundary = 'ClipCanvasQA' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
        body += path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
        req = urllib.request.Request(self.base + '/api/uploads?kind=' + kind, data=body,
                                     headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        with self.opener.open(req, timeout=60) as response:
            return json.loads(response.read())

    def wait(self, job_id, timeout=150):
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            job = self.request('/api/jobs/' + job_id)
            if job['status'] in {'done', 'error', 'cancelled'}:
                assert job['status'] == 'done', job
                return job
            time.sleep(.25)
        raise AssertionError('Job timed out: ' + job_id)

    def download(self, url, path):
        with self.opener.open(self.base + url, timeout=60) as response:
            path.write_bytes(response.read())


def tone_energy(path, second, frequency):
    pcm = command(['-ss', str(second), '-i', str(path), '-t', '0.25', '-map', '0:a:0',
                   '-ac', '1', '-ar', '16000', '-f', 's16le', 'pipe:1'])
    samples = array.array('h', pcm)
    real = sum(sample * math.cos(2 * math.pi * frequency * i / 16000) for i, sample in enumerate(samples))
    imag = sum(sample * math.sin(2 * math.pi * frequency * i / 16000) for i, sample in enumerate(samples))
    return real * real + imag * imag


def center_pixel(path, second):
    pixels = command(['-ss', str(second), '-i', str(path), '-frames:v', '1',
                      '-vf', 'crop=2:2:iw/2:ih/2', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'])
    assert len(pixels) >= 3
    return tuple(pixels[:3])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--keep-server', action='store_true')
    parser.add_argument('--encoder', choices=['auto', 'cpu', 'nvidia'], default='auto')
    args = parser.parse_args()
    qa_root = ROOT / '.runtime' / 'qa'
    qa_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='integration-', dir=qa_root))
    data = directory / 'data'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    source_path, advert_path = fixtures(directory)
    environment = dict(os.environ)
    environment.update(CLIPCANVAS_DATA=str(data), CLIPCANVAS_HOST='127.0.0.1',
                       CLIPCANVAS_PORT=str(port), PYTHONPATH=str(ROOT / '.runtime' / 'deps'),
                       CLIPCANVAS_AI_CONFIG_FILE=str(directory / 'ai-config.json'),
                       PYTHONDONTWRITEBYTECODE='1')
    environment.pop('CLIPCANVAS_TOKEN', None)
    log = (directory / 'server.log').open('wb')
    process = subprocess.Popen([sys.executable, '-B', str(ROOT / 'engine' / 'server.py')],
                               cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=NO_WINDOW)
    client = Client(base)
    passed = []
    success = False
    try:
        for _ in range(80):
            try:
                health = client.request('/api/health', timeout=1)
                break
            except (OSError, AssertionError):
                if process.poll() is not None:
                    raise AssertionError((directory / 'server.log').read_text('utf-8', errors='replace'))
                time.sleep(.2)
        else:
            raise AssertionError('Isolated server did not start.')
        passed.append('isolated server health')

        source = client.upload(source_path, 'video')
        advert = client.upload(advert_path, 'advert')
        assert len(source['audio_streams']) == 2, source
        assert len(source['subtitle_streams']) == 2, source
        forced = next(s for s in source['subtitle_streams'] if s['forced'])
        full = next(s for s in source['subtitle_streams'] if not s['forced'])
        assert source['recommended_subtitle_stream_index'] == full['index'], source
        passed.append('audio/subtitle inventory and full-subtitle recommendation')

        extract_payload = {'source_id': source['id'], 'subtitle_stream_index': full['index']}
        extraction = client.wait(client.request('/api/subtitles/extract', extract_payload)['id'])
        cues = extraction['cues']
        assert len(cues) == 2 and 'Контрольная' in cues[0]['text'], cues
        passed.append('embedded Cyrillic SRT extraction')
        repeat = client.wait(client.request('/api/subtitles/extract', extract_payload)['id'])
        assert repeat['cues'] == cues
        passed.append('repeat extraction returns identical cues')

        selected_audio = source['audio_streams'][1]['index']
        payload = {
            'source_id': source['id'], 'audio_stream_index': selected_audio,
            'canvas': {'width': 240, 'height': 426},
            'video': {'x': 0, 'y': 145, 'w': 240, 'h': 135, 'fit': 'contain'},
            'background': {'enabled': False, 'blur': 0, 'color': '#081222'},
            'banner': {'enabled': False},
            'subtitles': {'enabled': True, 'size': 24, 'color': '#ffffff', 'y': 85,
                          'style': 'clean', 'outline': 2, 'background': False, 'cues': cues},
            'advertising': {'enabled': True, 'asset_id': advert['id'], 'start': 2,
                            'repeat': True, 'interval': 4, 'speed': 1,
                            'geometry': {'x': 0, 'y': 145, 'w': 240, 'h': 135, 'fit': 'contain'}},
            'parts': [{'start': 0, 'end': 6}], 'quality': 'standard', 'encoder': args.encoder, 'fps': 24, 'zip': False,
        }
        rendered = client.wait(client.request('/api/render', payload)['id'])
        expected_encoder = args.encoder if args.encoder != 'auto' else (
            'nvidia' if 'nvidia' in health['capabilities']['render_encoders'] else 'cpu')
        assert rendered['outputs'][0]['encoder'] == expected_encoder, rendered
        passed.append('requested H.264 encoder used: ' + expected_encoder)
        assert not any(f['name'].endswith('.zip') for f in rendered['files']), rendered
        movie = next(f for f in rendered['files'] if f['name'].endswith('.mp4'))
        output = directory / 'rendered.mp4'
        client.download(movie['url'], output)
        with av.open(str(output)) as container:
            duration = container.duration / av.time_base
            assert abs(duration - 8) < .15, duration
            assert container.streams.video[0].width == 240
            assert container.streams.video[0].height == 426
            assert container.streams.audio
            frames = list(container.decode(video=0))
            assert abs(len(frames) - 192) <= 1, len(frames)
            transitions = []
            for frame in frames:
                red, green, blue = frame.to_ndarray(format='rgb24')[212, 120]
                color = 'green' if int(green) > int(blue) * 2 else 'blue'
                if not transitions or color != transitions[-1][1]:
                    transitions.append((float(frame.pts * frame.time_base), color))
            assert [color for _, color in transitions] == ['blue', 'green', 'blue', 'green', 'blue'], transitions
            for (actual, _), expected in zip(transitions, [0, 2, 3, 6, 7]):
                assert abs(actual - expected) < 1 / 24 + .025, transitions
        passed.append('repeated video ads extend source time correctly; MP4 dimensions/duration')
        passed.append('all 192 frames and ad transitions verified within one frame')

        for second, expected in [(1, 880), (2.3, 1320), (3.5, 880), (6.3, 1320), (7.4, 880)]:
            energies = {hz: tone_energy(output, second, hz) for hz in [440, 880, 1320]}
            assert energies[expected] > 15 * max(value for hz, value in energies.items() if hz != expected), (second, energies)
        passed.append('explicit audio selection and exclusive ad audio; source audio resumes')
        before = center_pixel(output, 1)
        during = center_pixel(output, 2.3)
        after = center_pixel(output, 3.5)
        assert before[2] > 150 and after[2] > 150, (before, after)
        assert during[1] > 50 and during[1] > during[2] * 2, during
        passed.append('actual ad frames appear at the intended final-timeline position')

        subtitle = next(f for f in rendered['files'] if f['name'].endswith('.srt'))
        exported_srt = directory / 'rendered.srt'
        client.download(subtitle['url'], exported_srt)
        content = exported_srt.read_text('utf-8-sig')
        assert '00:00:02,000' in content and '00:00:03,000' in content, content
        assert '00:00:06,000' in content and '00:00:07,000' in content, content
        passed.append('subtitle cues split and shifted around both ad insertions')

        clean_payload = dict(payload, advertising={'enabled': False}, parts=[{'start': 0, 'end': 1}], zip=True)
        clean = client.wait(client.request('/api/render', clean_payload)['id'])
        assert any(f['name'].endswith('.zip') for f in clean['files'])
        passed.append('clean export and optional ZIP')

        project_data = {'name': 'QA project', 'source_id': source['id'], 'advert_id': advert['id'],
                        'settings': payload, 'timeline': payload['parts']}
        project = client.request('/api/projects', project_data)
        project_id = project.get('id') or project.get('project', {}).get('id')
        assert project_id, project
        loaded = client.request('/api/projects/' + project_id)
        assert loaded['source_id'] == source['id'] and loaded['settings']['fps'] == 24, loaded
        passed.append('complete project persists settings and asset references')

        manifest = {'base': base, 'pid': process.pid, 'directory': str(directory),
                    'source_fixture': str(source_path), 'advert_fixture': str(advert_path),
                    'source_id': source['id'], 'advert_id': advert['id'], 'job_id': rendered['id'],
                    'project_id': project_id, 'passed': passed, 'health': health}
        (directory / 'result.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf-8')
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        success = True
    finally:
        if not (args.keep_server and success):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log.close()
        if not success:
            print('QA evidence directory: ' + str(directory), file=sys.stderr)
            print('Passed before failure: ' + ', '.join(passed), file=sys.stderr)


if __name__ == '__main__':
    main()
