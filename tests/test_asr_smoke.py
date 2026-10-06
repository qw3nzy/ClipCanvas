"""Offline ASR integration smoke. All generated media/jobs live in .runtime/qa."""
from __future__ import annotations
import hashlib
import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Local video containing speech')
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--duration', type=float, default=15)
    parser.add_argument('--audio-stream', type=int, help='Absolute FFmpeg stream index; defaults to first audio')
    parser.add_argument('--language', default='ru')
    parser.add_argument('--model', choices=('tiny','base','small'), default='tiny')
    parser.add_argument('--model-cache', type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    model_cache = (args.model_cache or ROOT/'data'/'models'/('models--Systran--faster-whisper-'+args.model)).resolve()
    if not source.is_file() or not model_cache.is_dir() or args.start < 0 or args.duration <= 0:
        parser.error('Provide an existing source, cached model and a positive speech window')
    qa_root = ROOT/'.runtime'/'qa'
    qa_root.mkdir(exist_ok=True,parents=True)
    directory = Path(tempfile.mkdtemp(prefix='asr-',dir=qa_root))
    data = directory/'data'
    (data/'models').mkdir(parents=True)
    shutil.copytree(model_cache,data/'models'/model_cache.name)
    fixture = directory/'speech-fixture.mp4'
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y',
        '-ss',str(args.start),'-i',str(source),'-t',str(args.duration),'-map','0:v:0','-map',f'0:{args.audio_stream}' if args.audio_stream is not None else '0:a:0','-vf',
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
               CLIPCANVAS_AI_CONFIG_FILE=str(directory/'ai.json'),HF_HUB_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(ROOT/'.runtime'/'deps'))
    env.pop('CLIPCANVAS_TOKEN',None)
    log = (directory/'server.log').open('wb')
    process = subprocess.Popen([sys.executable,'-B',str(ROOT/'engine'/'server.py')],cwd=ROOT,env=env,
        stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    health = None
    evidence = {'qa_directory':str(directory),'offline':True,'source_window':[args.start,args.start+args.duration],
                'source_audio_stream_index':args.audio_stream,'model':args.model,'language':args.language}
    try:
        for _ in range(80):
            try:
                health = request('/api/health')
                break
            except Exception:
                time.sleep(.1)
        assert health and health['status']=='ok' and health['app']=='ClipCanvas','Server did not start'
        boundary = 'ClipCanvasASR'+uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech-fixture.mp4"\r\n'
                'Content-Type: video/mp4\r\n\r\n').encode()+fixture.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
        req = urllib.request.Request(base+'/api/uploads?kind=video',data=body,
            headers={'Content-Type':'multipart/form-data; boundary='+boundary})
        with opener.open(req,timeout=30) as response:
            asset = json.loads(response.read())
        selected = asset['recommended_audio_stream_index']
        assert selected is not None
        payload = {'source_id':asset['id'],'audio_stream_index':selected,'model':args.model,'language':args.language}
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
        assert first['audio_stream_index']==selected and (args.language=='auto' or first['language']==args.language)
        assert first['origin']=='asr' and not first['cached']
        cues = first['cues']
        words = [word for cue in cues for word in cue.get('words',[])]
        assert cues and words,'No speech words were recognized in the selected window'
        assert all(0<=w['start']<w['end']<=asset['duration']+.02 for w in words)
        assert all(c['start']<=w['start']<w['end']<=c['end']+.02 for c in cues for w in c['words'])
        second,cached_took = wait(request('/api/transcribe',payload)['id'])
        assert second['cached'] and second['cues']==cues,'Repeated ASR did not use identical cached cues'
        batch_request={'name':'ASR folder pipeline','items':[{'source_id':asset['id'],'relative_path':'Season/speech.mp4'}],
            'config':{'canvas':{'width':240,'height':300},'video':{'x':0,'y':0,'w':240,'h':300,'fit':'contain'},
                      'background':{'enabled':False},'subtitles':{'size':20,'y':75},'encoder':'cpu','fps':24},
            'options':{'subtitles':'required','speech_model':args.model,'language':args.language,
                       'remove_background':False,'check_outputs':False,'zip':False}}
        batch,batch_took=wait(request('/api/batch',batch_request)['id'])
        row=batch['batch_items'][0]
        assert row['status'] in {'ready','review'} and row.get('transcribe_job'),row
        assert any(name.endswith('.mp4') for name in row['artifacts']) and any(name.endswith('.srt') for name in row['artifacts'])
        encoded = json.dumps(cues,ensure_ascii=False,sort_keys=True).encode()
        evidence.update({'status':'PASS','version':health['version'],'fixture_duration':asset['duration'],
            'selected_uploaded_audio_stream_index':selected,'cue_count':len(cues),'word_count':len(words),
            'first_seconds':round(took,3),'cached_seconds':round(cached_took,3),'cache_identical':True,
            'cues_sha256':hashlib.sha256(encoded).hexdigest(),'jobs':[first['id'],second['id']],
            'batch_id':batch['id'],'batch_seconds':round(batch_took,3),'batch_subtitles_and_mp4':True,
            'detected_language':first['language'],
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
