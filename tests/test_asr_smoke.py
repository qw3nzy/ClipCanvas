"""Offline ASR integration smoke. All generated media/jobs live in .runtime/qa."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'.runtime'/'deps'))
import av
import imageio_ffmpeg


def main():
    source_id = '20eeafcf11db429bb0e6c530155c09a9'
    metadata_path = ROOT/'data'/'uploads'/source_id/'asset.json'
    metadata = json.loads(metadata_path.read_text('utf-8'))
    source = metadata_path.parent/metadata['stored_name']
    model_cache = ROOT/'data'/'models'/'models--Systran--faster-whisper-tiny'
    qa_root = ROOT/'.runtime'/'qa'
    qa_root.mkdir(exist_ok=True,parents=True)
    directory = Path(tempfile.mkdtemp(prefix='asr-',dir=qa_root))
    data = directory/'data'
    (data/'models').mkdir(parents=True)
    shutil.copytree(model_cache,data/'models'/model_cache.name)
    fixture = directory/'speech-fixture.mp4'
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y',
        '-ss','60','-i',str(source),'-t','15','-map','0:0','-map','0:1','-vf',
        'scale=256:144:force_original_aspect_ratio=decrease:force_divisible_by=2',
        '-c:v','libx264','-preset','ultrafast','-crf','35','-c:a','aac','-movflags','+faststart',str(fixture)],
        capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode==0,result.stderr.decode('utf-8',errors='replace')[-1000:]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path,body=None):
        encoded = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base+path,data=encoded,headers={'Content-Type':'application/json'} if encoded else {})
        with opener.open(req,timeout=30) as response:
            return json.loads(response.read())
    env = dict(os.environ)
    env.update(CLIPCANVAS_DATA=str(data),CLIPCANVAS_HOST='127.0.0.1',CLIPCANVAS_PORT=str(port),
               HF_HUB_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(ROOT/'.runtime'/'deps'))
    env.pop('CLIPCANVAS_TOKEN',None)
    log = (directory/'server.log').open('wb')
    process = subprocess.Popen([sys.executable,'-B',str(ROOT/'engine'/'server.py')],cwd=ROOT,env=env,
        stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    health = None
    evidence = {'qa_directory':str(directory),'offline':True,'source_window':[60,75],
                'source_audio_stream_index':1,'model':'tiny','language':'ru'}
    try:
        for _ in range(80):
            try:
                health = request('/api/health')
                break
            except Exception:
                time.sleep(.1)
        assert health and health['version']=='2.0.0','Server did not start'
        boundary = 'ClipCanvasASR'+uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech-fixture.mp4"\r\n'
                'Content-Type: video/mp4\r\n\r\n').encode()+fixture.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
        req = urllib.request.Request(base+'/api/uploads?kind=video',data=body,
            headers={'Content-Type':'multipart/form-data; boundary='+boundary})
        with opener.open(req,timeout=30) as response:
            asset = json.loads(response.read())
        selected = asset['recommended_audio_stream_index']
        assert selected is not None
        payload = {'source_id':asset['id'],'audio_stream_index':selected,'model':'tiny','language':'ru'}
        def wait(job_id):
            started = time.monotonic()
            while time.monotonic()-started<180:
                job = request('/api/jobs/'+job_id)
                if job['status'] in {'done','error','cancelled'}:
                    assert job['status']=='done',job.get('error',job['status'])
                    return job,time.monotonic()-started
                time.sleep(.15)
            raise AssertionError('ASR timed out')
        first,took = wait(request('/api/transcribe',payload)['id'])
        assert first['audio_stream_index']==selected and first['language']=='ru'
        assert first['origin']=='asr' and not first['cached']
        cues = first['cues']
        words = [word for cue in cues for word in cue.get('words',[])]
        assert cues and words,'No speech words were recognized in the selected window'
        assert all(0<=w['start']<w['end']<=asset['duration']+.02 for w in words)
        assert all(c['start']<=w['start']<w['end']<=c['end']+.02 for c in cues for w in c['words'])
        second,cached_took = wait(request('/api/transcribe',payload)['id'])
        assert second['cached'] and second['cues']==cues,'Repeated ASR did not use identical cached cues'
        encoded = json.dumps(cues,ensure_ascii=False,sort_keys=True).encode()
        evidence.update({'status':'PASS','version':health['version'],'fixture_duration':asset['duration'],
            'selected_uploaded_audio_stream_index':selected,'cue_count':len(cues),'word_count':len(words),
            'first_seconds':round(took,3),'cached_seconds':round(cached_took,3),'cache_identical':True,
            'cues_sha256':hashlib.sha256(encoded).hexdigest(),'jobs':[first['id'],second['id']],
            'speech_wav_removed':not (data/'jobs'/first['id']/'speech.wav').exists()})
        assert evidence['speech_wav_removed']
        (directory/'result.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),'utf-8')
        print(json.dumps(evidence,ensure_ascii=False))
    finally:
        if health and process.poll() is None:
            try:
                request('/api/shutdown',{'instance_id':health['instance_id']})
                process.wait(timeout=12)
            except Exception:
                if process.poll() is None:
                    subprocess.run([str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32'/'taskkill.exe'),
                        '/PID',str(process.pid),'/T','/F'],capture_output=True)
                    process.wait(timeout=8)
        log.close()
        assert process.poll() is not None,'Isolated server remains running'


if __name__=='__main__':
    main()
