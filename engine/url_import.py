"""ClipCanvas URL import, adapted from Yoinks' MIT-licensed download core.

Source: pablostanley/yoinks, commit 6f03720813c22f907d3fb9ba98567f2169446b82.
See third_party/yoinks/LICENSE and THIRD_PARTY_NOTICES.md.
The terminal interface is replaced by the existing persistent job API.
"""
from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

YOINKS_COMMIT = '6f03720813c22f907d3fb9ba98567f2169446b82'
MAX_INFO_BYTES = 24 * 1024 * 1024


class InspectLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=8, max_length=4096)


class DownloadLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    inspect_job_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    choice_id: str = Field(pattern=r'^(best|h[1-9][0-9]{1,4})$')


def validate_public_url(url: str, resolver=socket.getaddrinfo) -> str:
    """Reject local files, credentials and private-network destinations."""
    value = url.strip()
    if any(ord(c) < 32 for c in value):
        raise ValueError('В ссылке недопустимые символы.')
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('Нужна полная ссылка http:// или https:// на видео.')
    if parsed.username is not None or parsed.password is not None:
        raise ValueError('Ссылки с логином или паролем не поддерживаются.')
    host = parsed.hostname.rstrip('.').lower()
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('Импорт предназначен для общедоступных интернет-ссылок.')
    try:
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError as error:
        raise ValueError('Некорректный порт в ссылке.') from error
    if port not in {80, 443}:
        raise ValueError('Для импорта разрешены обычные HTTP/HTTPS-порты.')
    try:
        addresses = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ValueError('Не удалось найти сайт. Проверьте ссылку и подключение.') from error
    if not addresses or any(not ipaddress.ip_address(item[4][0].split('%')[0]).is_global for item in addresses):
        raise ValueError('Ссылки на локальные и служебные адреса не поддерживаются.')
    return value


def _bytes_label(size):
    if not size or size <= 0:
        return ''
    units = ['Б', 'КБ', 'МБ', 'ГБ']
    number = float(size)
    unit = 0
    while number >= 1024 and unit < 3:
        number /= 1024
        unit += 1
    return f'{number:.1f} {units[unit]}'


def build_choices(info):
    """Yoinks resolution selection and size estimation, adapted for video editing."""
    formats = info.get('formats') or []
    audio = [f for f in formats if f.get('acodec') not in (None, 'none') and f.get('vcodec') in (None, 'none')]
    best_audio = max(audio, key=lambda f: f.get('abr') or f.get('tbr') or 0, default={})
    audio_size = best_audio.get('filesize') or best_audio.get('filesize_approx') or 0
    videos = [f for f in formats if f.get('vcodec') not in (None, 'none') and f.get('height')]
    heights = sorted({int(f['height']) for f in videos if 0 < float(f['height']) <= 16384}, reverse=True)[:8]
    choices = []
    for height in heights:
        def score(f):
            return (f.get('tbr') or 0) + (10000 if f.get('ext') == 'mp4' else 0) + (5000 if str(f.get('vcodec', '')).startswith('avc') else 0)
        best = max((f for f in videos if int(f['height']) == height), key=score)
        size = (best.get('filesize') or best.get('filesize_approx') or 0) + (audio_size if best.get('acodec') in (None, 'none') else 0)
        choices.append({'id': f'h{height}', 'height': height, 'estimated_bytes': size or None,
                        'label': f'{height}p' + (f' · примерно {_bytes_label(size)}' if size else '')})
    return choices or [{'id': 'best', 'height': None, 'estimated_bytes': info.get('filesize') or info.get('filesize_approx'), 'label': 'Лучшее доступное качество'}]


def _format_selector(choice_id):
    if choice_id == 'best':
        return 'bv*+ba/b'
    height = int(choice_id[1:])
    return f'bv*[height={height}]+ba/b[height={height}]/bv*[height<={height}]+ba/b'


def _runtime_arguments():
    bundled = Path(__file__).resolve().parents[1] / '.runtime' / 'deps' / 'bin' / ('deno.exe' if os.name == 'nt' else 'deno')
    if bundled.is_file():
        return ['--js-runtimes', 'deno:' + str(bundled)]
    spec = importlib.util.find_spec('deno')
    if spec and spec.origin:
        folder = Path(spec.origin).parent
        binary = next(folder.rglob('deno.exe'), None) if os.name == 'nt' else next(folder.rglob('deno'), None)
        if binary and binary.is_file():
            return ['--js-runtimes', 'deno:' + str(binary)]
    node = shutil.which('node')
    return ['--js-runtimes', 'node:' + node] if node else []


def _safe_error(log_path):
    try:
        with log_path.open('rb') as stream:
            stream.seek(max(0, log_path.stat().st_size - 5000))
            lines = stream.read().decode('utf-8', errors='replace').splitlines()
        errors = [line for line in lines if 'ERROR:' in line]
        detail = (errors[-1] if errors else '\n'.join(lines[-3:]))[-1200:]
        lowered = detail.lower()
        if '403' in lowered or 'cloudflare' in lowered:
            return 'Сайт отклонил автоматическую загрузку. Можно загрузить имеющийся у вас видеофайл с компьютера.'
        if '429' in lowered or 'too many requests' in lowered:
            return 'Сайт временно ограничил число запросов. Попробуйте позднее.'
        if any(marker in lowered for marker in ('sign in', 'log in', 'login required', 'private video')):
            return 'Источник требует входа или доступен не всем. Автоматический вход в аккаунт не поддерживается.'
        if 'unsupported url' in lowered:
            return 'Этот адрес не поддерживается загрузчиком. Используйте ссылку на отдельное видео или локальный файл.'
        return re.sub(r'https?://\S+', '[ссылка]', detail)
    except OSError:
        return ''


def register_url_routes(app, services):
    data = Path(services['DATA'])
    update = services['update']
    cancelled = services['cancelled']
    create_job = services['create_job']
    ffmpeg = str(services['FFMPEG'])

    def require_downloader():
        if importlib.util.find_spec('yt_dlp') is None:
            raise HTTPException(503, 'Модуль импорта ещё не установлен. Перезапустите «Запустить.cmd».')

    def stop_process(process):
        if process.poll() is not None:
            return
        if services.get('terminate_process'):
            services['terminate_process'](process)
            return
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=10)
        else:
            process.terminate()

    def run_downloader(job_id, args, *, collect=False):
        directory = data / 'jobs' / job_id
        log_path = directory / 'download.log'
        env = dict(os.environ)
        # Also works with the embeddable Python, which ignores PYTHONPATH.
        deps = str(Path(__file__).resolve().parents[1] / '.runtime' / 'deps')
        code = 'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("yt_dlp",run_name="__main__")'
        command = [sys.executable, '-B', '-c', code, deps, '--ignore-config', '--no-playlist', '--no-warnings',
                   '--socket-timeout', '20', '--retries', '2', '--fragment-retries', '2', '--no-cache-dir',
                   *_runtime_arguments(), *args]
        output = []
        size = 0
        last_update = 0.0
        with log_path.open('w', encoding='utf-8') as log:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, text=True,
                                       encoding='utf-8', errors='replace', env=env,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            with services['LOCK']:
                services['PROCESSES'][job_id] = process
            try:
                for line in process.stdout:
                    if cancelled(job_id):
                        stop_process(process)
                        raise services['Cancelled']()
                    if collect:
                        size += len(line.encode('utf-8'))
                        if size > MAX_INFO_BYTES:
                            stop_process(process)
                            raise RuntimeError('Слишком большой ответ сайта. Используйте ссылку на одно видео.')
                        output.append(line)
                    elif line.startswith('CLIPPROGRESS|'):
                        if time.monotonic() - last_update < .4:
                            continue
                        last_update = time.monotonic()
                        values = line.strip().split('|')
                        try:
                            done = float(values[1])
                            total = next(float(v) for v in values[2:4] if v not in {'NA', 'None', ''})
                            progress = min(94, 5 + 89 * done / max(1, total))
                            update(job_id, progress=progress, message=f'Скачивание: {_bytes_label(done)} из {_bytes_label(total)}')
                        except (ValueError, StopIteration, IndexError):
                            update(job_id, message='Скачивание видео…')
                        if shutil.disk_usage(directory).free < 64 * 1024 * 1024:
                            stop_process(process)
                            raise RuntimeError('На диске закончилось свободное место.')
                    elif line.startswith('CLIPFILE|'):
                        output.append(line.strip()[9:])
                    elif '[Merger]' in line:
                        update(job_id, progress=95, message='Объединение видео и звука…')
                code = process.wait(timeout=30)
                if cancelled(job_id):
                    raise services['Cancelled']()
                if code:
                    raise RuntimeError('Не удалось получить видео. ' + _safe_error(log_path))
                return ''.join(output) if collect else output
            finally:
                stop_process(process)
                with services['LOCK']:
                    if services['PROCESSES'].get(job_id) is process:
                        services['PROCESSES'].pop(job_id, None)

    def inspect_job(job_id, request):
        request = request.model_dump() if hasattr(request, 'model_dump') else request
        url = request['url']
        update(job_id, message='Yoinks: поиск доступного качества…', progress=3)
        url = validate_public_url(url)
        raw = run_downloader(job_id, ['--dump-single-json', '--skip-download', '--', url], collect=True)
        info = json.loads(raw)
        if info.get('_type') in {'playlist', 'multi_video'} or info.get('entries') is not None:
            raise RuntimeError('Выберите ссылку на одно видео, а не на подборку.')
        if info.get('is_live') or info.get('live_status') == 'is_live':
            raise RuntimeError('Прямые эфиры пока не поддерживаются. Нужна завершённая запись.')
        if info.get('has_drm'):
            raise RuntimeError('Защищённое DRM-видео не поддерживается.')
        if info.get('vcodec') == 'none' and not any(f.get('vcodec') not in (None, 'none') for f in info.get('formats', [])):
            raise RuntimeError('В ссылке нет видеодорожки.')
        directory = data / 'jobs' / job_id
        (directory / 'link-info.json').write_text(raw, encoding='utf-8')
        choices = build_choices(info)
        record = {'url': url, 'created': time.time(), 'choices': choices}
        (directory / 'link-request.json').write_text(json.dumps(record, ensure_ascii=False), 'utf-8')
        summary = {'title': str(info.get('title') or 'Видео по ссылке')[:500], 'duration': info.get('duration'),
                   'uploader': str(info.get('uploader') or '')[:300], 'webpage_url': url,
                   'extractor': str(info.get('extractor_key') or info.get('extractor') or '')[:100]}
        update(job_id, info=summary, choices=choices, message='Выберите качество для импорта.', progress=100)

    def download_job(job_id, request):
        request = request.model_dump() if hasattr(request, 'model_dump') else request
        inspect_id, choice_id = request['inspect_job_id'], request['choice_id']
        inspected = data / 'jobs' / inspect_id
        record = json.loads((inspected / 'link-request.json').read_text('utf-8'))
        choice = next((c for c in record['choices'] if c['id'] == choice_id), None)
        if choice is None:
            raise ValueError('Качество отсутствует в результатах проверки ссылки.')
        url = validate_public_url(record['url'])
        directory = data / 'jobs' / job_id
        target = directory / 'download'
        target.mkdir(exist_ok=True)
        estimated = choice.get('estimated_bytes') or 0
        if shutil.disk_usage(target).free < estimated * 2 + 128 * 1024 * 1024:
            raise RuntimeError('Недостаточно места для скачивания и объединения дорожек.')
        update(job_id, message='Yoinks: скачивание выбранного видео…', progress=3)
        cache = inspected / 'link-info.json'
        info = json.loads(cache.read_text('utf-8'))
        # Validate media destinations before yt-dlp downloads the cached formats.
        hosts = set()
        for item in [info, *(info.get('formats') or [])]:
            media_url = item.get('url')
            if isinstance(media_url, str) and media_url.startswith(('https://', 'http://')):
                hostname = urlsplit(media_url).hostname
                if hostname not in hosts:
                    validate_public_url(media_url)
                    hosts.add(hostname)
        inputs = ['--load-info-json', str(cache)] if time.time() - record['created'] < 900 else ['--', url]
        args = ['-f', _format_selector(choice_id), '--merge-output-format', 'mp4', '--ffmpeg-location', ffmpeg,
                '--max-filesize', os.environ.get('CLIPCANVAS_MAX_DOWNLOAD_SIZE', '16G'),
                '--newline', '--no-quiet', '--progress', '--no-simulate',
                '--progress-template', 'download:CLIPPROGRESS|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s',
                '--print', 'after_move:CLIPFILE|%(filepath)s', '-o', str(target / 'source.%(ext)s'), *inputs]
        paths = run_downloader(job_id, args)
        if not paths:
            raise RuntimeError('Сайт не вернул готовый видеофайл.')
        downloaded = Path(paths[-1]).resolve()
        if not downloaded.is_relative_to(target.resolve()) or not downloaded.is_file():
            raise RuntimeError('Некорректный путь скачанного файла.')
        update(job_id, progress=97, message='Добавление видео в библиотеку…')
        metadata = services['probe'](downloaded)
        asset_id = uuid.uuid4().hex
        folder = data / 'uploads' / asset_id
        folder.mkdir()
        stored = 'source' + downloaded.suffix.lower()
        downloaded.replace(folder / stored)
        asset = {'id': asset_id, 'kind': 'video', 'name': str(info.get('title') or 'Видео по ссылке')[:200],
                 'stored_name': stored, 'source_url': url, 'importer': 'yoinks', **metadata}
        (folder / 'asset.json').write_text(json.dumps(asset, ensure_ascii=False), 'utf-8')
        update(job_id, asset=asset, message='Видео добавлено. Можно перейти к монтажу.', progress=100)

    @app.get('/api/links/status')
    def link_status():
        return {'available': importlib.util.find_spec('yt_dlp') is not None, 'engine': 'yoinks',
                'version': '0.3.1-adapter', 'source_commit': YOINKS_COMMIT}

    @app.post('/api/links/inspect')
    def inspect_link(request: InspectLink):
        require_downloader()
        try:
            url = validate_public_url(request.url)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return create_job(inspect_job, {'url': url}, 'url-inspect')

    @app.post('/api/links/download')
    def download_link(request: DownloadLink):
        require_downloader()
        inspected = data / 'jobs' / request.inspect_job_id
        record_path = inspected / 'link-request.json'
        if not record_path.is_file():
            raise HTTPException(404, 'Сначала проверьте ссылку заново.')
        record = json.loads(record_path.read_text('utf-8'))
        if request.choice_id not in {c['id'] for c in record.get('choices', [])}:
            raise HTTPException(400, 'Выберите доступное качество из списка.')
        return create_job(download_job, request.model_dump(), 'url-download')

    if services.get('register_job_type'):
        services['register_job_type']('url-inspect', InspectLink, inspect_job)
        services['register_job_type']('url-download', DownloadLink, download_job)
