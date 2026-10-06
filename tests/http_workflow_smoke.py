"""Run real editor actions on HTTP at an assigned VPN/LAN IP with isolated data."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from integration_smoke import Client, ROOT, NO_WINDOW, command, fixtures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True, help='Specific non-loopback IP assigned to this PC')
    args = parser.parse_args()
    address = ipaddress.ip_address(args.host)
    if not isinstance(address, ipaddress.IPv4Address) or address.is_loopback or address.is_unspecified:
        parser.error('Use a specific non-loopback IPv4 address assigned to this PC')
    with socket.socket() as sock:
        sock.bind((args.host, 0))
        port = sock.getsockname()[1]
    directory = Path(tempfile.mkdtemp(prefix='http-ui-', dir=ROOT/'.runtime/qa'))
    source, _ = fixtures(directory)
    episodes = directory/'episodes'
    episodes.mkdir()
    shutil.copyfile(source, episodes/source.name)
    banner = directory/'banner.mp4'
    command(['-f','lavfi','-i','color=c=lime:s=160x80:r=24:d=1',
        '-f','lavfi','-i','sine=frequency=1500:sample_rate=48000:duration=1',
        '-vf','drawbox=x=40:y=20:w=80:h=40:color=white:t=fill',
        '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(banner)])
    token = secrets.token_urlsafe(32)
    environment = dict(os.environ)
    environment.update(CLIPCANVAS_DATA=str(directory/'data'), CLIPCANVAS_HOST=args.host,
        CLIPCANVAS_PORT=str(port), CLIPCANVAS_TOKEN=token, CLIPCANVAS_QA_TOKEN=token,
        CLIPCANVAS_AI_CONFIG_FILE=str(directory/'ai-config.json'),
        PYTHONPATH=str(ROOT/'.runtime/deps'), PYTHONDONTWRITEBYTECODE='1')
    client = Client(f'http://{args.host}:{port}')
    client.opener.addheaders = [('Authorization', 'Bearer '+token)]
    with (directory/'server.log').open('wb') as log:
        process = subprocess.Popen([sys.executable,'-B',str(ROOT/'engine/server.py')],cwd=ROOT,
            env=environment,stdout=log,stderr=subprocess.STDOUT,creationflags=NO_WINDOW)
        health = None
        try:
            for _ in range(100):
                try:
                    health = client.request('/api/health', timeout=1)
                    break
                except Exception:
                    if process.poll() is not None:
                        raise AssertionError('QA engine failed; inspect '+str(directory/'server.log'))
                    time.sleep(.1)
            assert health and health['capabilities']['auth_required']
            asset = client.upload(source, 'video')
            media = client.upload(banner, 'banner')
            template = client.request('/api/banner-templates', {'name':'HTTP QA banner', 'asset_id':media['id'],
                'width':640,'height':360,'geometry':{'x':20,'y':20,'w':160,'h':80},'duration':3,
                'instructions':'Сохранить размер, не перекрывать субтитры.'})
            manifest = directory/'manifest.json'
            manifest.write_text(json.dumps({'base':client.base,'directory':str(directory),
                'source_id':asset['id'],'banner_file':str(banner),'episodes':str(episodes),
                'template_id':template['id']},ensure_ascii=False,indent=2),'utf-8')
            node = shutil.which('node') or r'C:\Program Files\nodejs\node.exe'
            browser = subprocess.run([node,str(ROOT/'tests/ui_http_workflow_smoke.cjs'),str(manifest)],
                cwd=ROOT,env=environment,capture_output=True,text=True,encoding='utf-8',
                timeout=180,creationflags=NO_WINDOW)
            (directory/'browser.log').write_text(browser.stdout+browser.stderr,'utf-8')
            if browser.returncode:
                raise AssertionError(browser.stderr or browser.stdout)
            import numpy as np
            pixels=command(['-ss','0.75','-i',str(directory/'confirmed-sample.mp4'),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
            frame=np.frombuffer(pixels,dtype=np.uint8).reshape(360,640,3)
            assert min(frame[180,320])>220, 'Banner must be in exact canvas centre'
            assert frame[50,80,2]>220 and frame[50,80,0]<40, 'Old high position must reveal source'
            sound=np.frombuffer(command(['-i',str(directory/'confirmed-sample.mp4'),'-vn','-ac','1','-ar','48000','-f','f32le','pipe:1']),dtype=np.float32)[12000:84000]
            tone=abs(np.dot(sound,np.exp(-2j*np.pi*1500*np.arange(len(sound))/48000)))/len(sound)
            assert tone>.015, 'Selected banner audio must be audible at original pitch with 2x speed'
            print('PASS: exported exact centre, audible banner at 2x with preserved pitch')
            print(browser.stdout)
            print('Evidence: '+str(directory))
        finally:
            if process.poll() is None:
                try:
                    if health:
                        client.request('/api/shutdown', {'instance_id':health['instance_id']})
                    else:
                        process.terminate()
                    process.wait(timeout=15)
                except Exception:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == '__main__':
    main()
