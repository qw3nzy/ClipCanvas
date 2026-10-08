"""Isolated loopback fixture for Windows launcher ownership/lifecycle tests."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
fixture = root / '.runtime' / 'qa-launcher-fixture'
fixture.mkdir(exist_ok=True)
(fixture / 'engine').mkdir(exist_ok=True)
(fixture / '.runtime' / 'deps').mkdir(parents=True, exist_ok=True)
for name in ('Start.ps1', 'Stop.ps1', 'engine/launch.py'):
    shutil.copy2(root / name, fixture / name)
start_fixture = fixture / 'Start.ps1'
start_fixture.write_text(start_fixture.read_text('utf-8').replace(
    '    if (-not $pythonPath) {', "    if (-not $pythonPath) { throw 'QA fixture forbids downloads.'"), 'utf-8')
(fixture / 'engine' / 'install.py').write_text('raise SystemExit(0)\n', 'utf-8')
(fixture / 'engine' / 'server.py').write_text('''import json, os, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
root=Path(__file__).resolve().parent.parent
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],creationflags=subprocess.CREATE_NO_WINDOW)
(root/'.runtime'/'child.pid').write_text(str(child.pid))
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        data={'app':'ClipCanvas','version':'3.1.6','root':str(root),'instance_id':os.environ['CLIPCANVAS_INSTANCE_ID'],'pid':os.getpid()}
        self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(json.dumps(data).encode())
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))))
        good=(self.headers.get('Authorization')=='Bearer '+os.environ['CLIPCANVAS_TOKEN'] and body.get('instance_id')==os.environ['CLIPCANVAS_INSTANCE_ID'])
        fail=(root/'.runtime'/'fail-shutdown').exists()
        self.send_response(200 if good and not fail else 503); self.end_headers(); self.wfile.write(b'{}')
        if good and not fail: threading.Thread(target=server.shutdown,daemon=True).start()
server=ThreadingHTTPServer((os.environ['CLIPCANVAS_HOST'],int(os.environ['CLIPCANVAS_PORT'])),Handler)
server.serve_forever()
server.server_close()
''', 'utf-8')

def run(script, *args, expected=0):
    stamp=str(time.time_ns())
    out_path=fixture/'.runtime'/('qa-'+stamp+'.out')
    err_path=fixture/'.runtime'/('qa-'+stamp+'.err')
    with out_path.open('w') as out, err_path.open('w') as err:
        result = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(fixture/script),*args],
                                stdout=out,stderr=err,timeout=45,creationflags=subprocess.CREATE_NO_WINDOW)
    result.stdout=out_path.read_text(errors='replace')
    result.stderr=err_path.read_text(errors='replace')
    if result.returncode != expected:
        raise AssertionError(f'{script} returned {result.returncode}: {result.stdout}\n{result.stderr}')
    return result.stdout

def alive(pid):
    result = subprocess.run(['powershell.exe','-NoProfile','-Command',f'if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{exit 0}} else {{exit 1}}'],
                            capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
    return result.returncode == 0

for fail_shutdown in (False,True):
    flag=fixture/'.runtime'/'fail-shutdown'
    if fail_shutdown: flag.write_text('1')
    else: flag.unlink(missing_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=str(sock.getsockname()[1])
    run('Start.ps1','-NoBrowser','-Port',port)
    saved=json.loads((fixture/'.runtime'/'engine.pid').read_text())
    child=int((fixture/'.runtime'/'child.pid').read_text())
    assert alive(saved['pid']) and alive(child)
    assert 'already running' in run('Start.ps1','-NoBrowser','-Port',port)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); other_port=str(sock.getsockname()[1])
    run('Start.ps1','-NoBrowser','-Port',other_port,expected=1)
    assert alive(saved['pid'])
    run('Stop.ps1')
    assert not alive(saved['pid']) and not alive(child)
    assert not (fixture/'.runtime'/'engine.pid').exists()
    print('PASS: '+('forced fallback' if fail_shutdown else 'graceful shutdown')+'; owned descendants stopped; duplicate/bind conflict rejected',flush=True)

unrelated=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],creationflags=subprocess.CREATE_NO_WINDOW)
try:
    (fixture/'.runtime'/'engine.pid').write_text(str(unrelated.pid))
    run('Stop.ps1',expected=1)
    assert alive(unrelated.pid)
    print('PASS: unrelated saved PID was preserved')
finally:
    unrelated.terminate(); unrelated.wait(timeout=10)
    (fixture/'.runtime'/'engine.pid').unlink(missing_ok=True)
for address in ('0.0.0.0','::','not-an-ip'):
    run('Start.ps1','-NoBrowser','-BindAddress',address,expected=1)
print('PASS: wildcard and invalid binds rejected before launch')
