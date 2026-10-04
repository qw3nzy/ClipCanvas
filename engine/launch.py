"""App-local launcher; save a verifiable identity for one owned server process."""
import atexit
import ipaddress
import json
import os
import runpy
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parent.parent
runtime = root / '.runtime'
runtime.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(runtime / 'deps'))
process_lock = (runtime / 'engine.lock').open('a+b')
if os.name == 'nt':
    import msvcrt
    process_lock.seek(0, 2)
    if process_lock.tell() == 0:
        process_lock.write(b'1')
        process_lock.flush()
    process_lock.seek(0)
    try:
        msvcrt.locking(process_lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit('This application folder already has an engine process.')
atexit.register(process_lock.close)
host = str(ipaddress.ip_address(os.environ.get('CLIPCANVAS_HOST', '127.0.0.1')))
if host in {'0.0.0.0', '::'}:
    raise SystemExit('Use a specific interface address, not a wildcard bind.')
port = int(os.environ.get('CLIPCANVAS_PORT', '8787'))
if not 1024 <= port <= 65535:
    raise SystemExit('CLIPCANVAS_PORT must be between 1024 and 65535.')
if not os.environ.get('CLIPCANVAS_TOKEN'):
    token_path = Path(os.environ.get('LOCALAPPDATA', str(root))) / 'ClipCanvas' / 'access-token.txt'
    if token_path.is_file():
        os.environ['CLIPCANVAS_TOKEN'] = token_path.read_text('ascii').strip()
if not ipaddress.ip_address(host).is_loopback and not os.environ.get('CLIPCANVAS_TOKEN'):
    raise SystemExit('Remote mode requires an access token; launch with Start.ps1.')
instance = os.environ.get('CLIPCANVAS_INSTANCE_ID') or str(uuid.uuid4())
os.environ.update(CLIPCANVAS_HOST=host, CLIPCANVAS_PORT=str(port),
                  CLIPCANVAS_INSTANCE_ID=instance, CLIPCANVAS_APP_ROOT=str(root))
record_path = runtime / 'engine.pid'
record = {'schema': 2, 'pid': os.getpid(), 'instance_id': instance, 'root': str(root),
          'launch_path': str(Path(__file__).resolve()), 'python_path': sys.executable,
          'started_at': datetime.now(timezone.utc).isoformat(), 'host': host, 'port': port}
temporary = record_path.with_suffix('.pid.tmp')
temporary.write_text(json.dumps(record, indent=2), 'utf-8')
os.replace(temporary, record_path)


def clean_identity():
    try:
        saved = json.loads(record_path.read_text('utf-8'))
        if saved.get('instance_id') == instance and saved.get('pid') == os.getpid():
            record_path.unlink()
    except (OSError, ValueError):
        pass


atexit.register(clean_identity)
runpy.run_path(str(root / 'engine' / 'server.py'), run_name='__main__')
