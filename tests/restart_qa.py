"""Restart a verified isolated QA engine, retaining its persisted data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse

from integration_smoke import Client, ROOT, NO_WINDOW


def restart(manifest_path):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text('utf-8'))
    directory = Path(manifest['directory']).resolve()
    assert directory.is_relative_to((ROOT/'.runtime/qa').resolve()) and directory != (ROOT/'.runtime/qa').resolve()
    address = urlparse(manifest['base'])
    assert address.hostname == '127.0.0.1' and address.port
    client = Client(manifest['base'])
    old = client.request('/api/health')
    assert old['pid'] == manifest['pid'] and old['instance_id'] == manifest['health']['instance_id']
    client.request('/api/shutdown', {'instance_id':old['instance_id']})
    for _ in range(100):
        try:
            client.request('/api/health', timeout=1)
        except Exception:
            break
        time.sleep(.1)
    else:
        raise AssertionError('Previous QA server did not stop')
    environment = dict(os.environ)
    environment.update(CLIPCANVAS_DATA=str(directory/'data'), CLIPCANVAS_HOST='127.0.0.1',
        CLIPCANVAS_PORT=str(address.port), CLIPCANVAS_AI_CONFIG_FILE=str(directory/'ai-config.json'),
        PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT/'.runtime/deps'))
    environment.pop('CLIPCANVAS_TOKEN', None)
    with (directory/'server.log').open('ab') as log:
        process = subprocess.Popen([sys.executable,'-B',str(ROOT/'engine/server.py')],cwd=ROOT,env=environment,
            stdout=log,stderr=subprocess.STDOUT,creationflags=NO_WINDOW)
    for _ in range(150):
        try:
            health = client.request('/api/health', timeout=1)
            assert health['pid'] == process.pid and health['instance_id'] != old['instance_id']
            manifest.update(pid=process.pid, health=health)
            manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2), 'utf-8')
            return manifest
        except Exception:
            if process.poll() is not None:
                raise AssertionError('QA engine failed to restart; inspect server.log')
            time.sleep(.1)
    process.terminate()
    process.wait(timeout=10)
    raise AssertionError('Restart timed out')


if __name__ == '__main__':
    print(json.dumps(restart(sys.argv[1]),ensure_ascii=False))
