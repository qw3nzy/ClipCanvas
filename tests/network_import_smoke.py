"""Opt-in real-network Yoinks adapter smoke using a public W3C trailer."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from integration_smoke import Client, ROOT, NO_WINDOW


def main():
    folder = Path(tempfile.mkdtemp(prefix='links-', dir=ROOT / '.runtime' / 'qa'))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, CLIPCANVAS_DATA=str(folder / 'data'), CLIPCANVAS_PORT=str(port),
               CLIPCANVAS_HOST='127.0.0.1', PYTHONPATH=str(ROOT / '.runtime' / 'deps'))
    env.pop('CLIPCANVAS_TOKEN', None)
    log = (folder / 'server.log').open('wb')
    server = subprocess.Popen([sys.executable, '-B', str(ROOT / 'engine' / 'server.py')],
                              cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                              creationflags=NO_WINDOW)
    client = Client(f'http://127.0.0.1:{port}')
    try:
        for _ in range(60):
            try:
                client.request('/api/health', timeout=1)
                break
            except (OSError, AssertionError):
                time.sleep(.25)
        status = client.request('/api/links/status')
        assert status['available'], status
        inspection = client.wait(client.request('/api/links/inspect', {
            'url': 'https://media.w3.org/2010/05/sintel/trailer.mp4'})['id'], timeout=180)
        assert inspection['info']['title'] and inspection['choices'], inspection
        print('Public link inspection PASS: ' + str(inspection['choices']), flush=True)
        result = client.wait(client.request('/api/links/download', {
            'inspect_job_id': inspection['id'], 'choice_id': inspection['choices'][-1]['id']
        })['id'], timeout=240)
        asset = result['asset']
        assert asset['duration'] > 1 and asset['audio_streams'], asset
        assert client.request('/api/assets/' + asset['id'])['id'] == asset['id']
        report = {'base': client.base, 'directory': str(folder), 'status': status,
                  'inspection': inspection, 'asset': asset}
        (folder / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
        print('Public link download + playable library asset PASS: ' + str(asset['duration']), flush=True)
        print('Evidence: ' + str(folder), flush=True)
    finally:
        server.terminate()
        server.wait(timeout=15)
        log.close()


if __name__ == '__main__':
    main()
