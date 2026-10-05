"""Private, cancellable Ollama calls. Media never leaves the loopback interface."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

MODEL = 'qwen3.5:4b'
ADDRESS = 'http://127.0.0.1:11435'
ROOT = Path(__file__).resolve().parent.parent
START_LOCK = threading.Lock()


def request_json(path, payload=None, timeout=5):
    request = urllib.request.Request(ADDRESS+path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json'})
    # OS proxies must never receive frames or dialogue from a local movie.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def status():
    try:
        models = request_json('/api/tags').get('models', [])
        model = next((m for m in models if m['name'] == MODEL), None)
        return {'available': bool(model), 'running': True, 'model': MODEL,
                'size': model.get('size') if model else None, 'digest': model.get('digest') if model else None, 'local': True}
    except (OSError, ValueError):
        return {'available': False, 'running': False, 'model': MODEL, 'local': True,
                'installed': (ROOT/'.runtime/ollama/ollama.exe').is_file()}


def ensure_server():
    with START_LOCK:
        if status()['running']:
            return
        executable = ROOT/'.runtime/ollama/ollama.exe'
        if not executable.is_file():
            raise RuntimeError('Локальная модель не установлена. Запустите Setup-AI.cmd.')
        env = {**os.environ, 'OLLAMA_HOST': ADDRESS, 'OLLAMA_MODELS': str(ROOT/'data/models/ollama'),
               'OLLAMA_NO_CLOUD': '1', 'OLLAMA_CONTEXT_LENGTH': '8192',
               'OLLAMA_NUM_PARALLEL': '1', 'OLLAMA_MAX_LOADED_MODELS': '1'}
        with (ROOT/'.runtime/ollama-service.log').open('ab') as log:
            subprocess.Popen([str(executable), 'serve'], env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for _ in range(100):
            if status()['running']:
                return
            time.sleep(.1)
        raise RuntimeError('Ollama не запустился. Проверьте .runtime/ollama-service.log.')


def unload():
    try:
        request_json('/api/generate', {'model': MODEL, 'keep_alive': 0}, timeout=15)
    except (OSError, ValueError):
        pass


def chat(messages, schema, directory, check_cancel, process_changed=lambda p: None):
    ensure_server()
    if not status()['available']:
        raise RuntimeError('Скачайте Qwen3.5-4B командой Setup-AI.cmd, затем повторите анализ.')
    payload = {'model': MODEL, 'messages': messages, 'format': schema, 'stream': False,
               'think': False, 'keep_alive': '2m',
               'options': {'num_ctx': 8192, 'num_predict': 2400, 'temperature': .15, 'seed': 42}}
    incoming, outgoing = directory/'ai-request.tmp', directory/'ai-response.tmp'
    incoming.write_text(json.dumps(payload), 'utf-8')
    outgoing.unlink(missing_ok=True)
    process = subprocess.Popen([sys.executable, '-B', str(Path(__file__)), str(incoming), str(outgoing)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    process_changed(process)
    try:
        while process.poll() is None:
            check_cancel()
            time.sleep(.15)
        check_cancel()
        result = json.loads(outgoing.read_text('utf-8')) if outgoing.exists() else {}
        if process.returncode or 'error' in result:
            raise RuntimeError('Ошибка локальной модели: '+result.get('error', 'процесс завершился без ответа'))
        text = result.get('message', {}).get('content', '')
        try:
            return json.loads(text)
        except ValueError as error:
            raise RuntimeError('Модель вернула некорректный JSON. Повторите анализ меньшего диапазона.') from error
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process_changed(None)
        incoming.unlink(missing_ok=True)
        outgoing.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        response = request_json('/api/chat', json.loads(Path(sys.argv[1]).read_text('utf-8')), timeout=600)
    except Exception as error:
        response = {'error': str(error)}
    Path(sys.argv[2]).write_text(json.dumps(response), 'utf-8')
