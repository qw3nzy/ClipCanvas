"""ClipCanvas v2: local media, explicit tracks, resumable jobs and inserted ads."""
from __future__ import annotations
import concurrent.futures
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import av
import imageio_ffmpeg
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from clipcanvas_media import captions_for_timeline, normalize_cues, parse_subtitles, subtitle_recommendation, timeline

ROOT = Path(os.environ.get('CLIPCANVAS_APP_ROOT', Path(__file__).resolve().parent.parent)).resolve()
DATA = Path(os.environ.get('CLIPCANVAS_DATA', str(ROOT/'data'))).resolve()
HOST = os.environ.get('CLIPCANVAS_HOST', '127.0.0.1')
PORT = int(os.environ.get('CLIPCANVAS_PORT', '8787'))
TOKEN = os.environ.get('CLIPCANVAS_TOKEN', '')
INSTANCE_ID = os.environ.get('CLIPCANVAS_INSTANCE_ID', uuid.uuid4().hex)
REMOTE = HOST not in {'127.0.0.1', 'localhost', '::1'}
if REMOTE and not TOKEN:
    raise RuntimeError('Для удалённого доступа задайте CLIPCANVAS_TOKEN. Локальный доступ по умолчанию: 127.0.0.1.')
for name in ('uploads', 'jobs', 'models', 'tmp', 'cache', 'projects'):
    (DATA/name).mkdir(parents=True, exist_ok=True)
os.environ['TMP'] = os.environ['TEMP'] = str(DATA/'tmp')
os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
os.environ['HF_HUB_DISABLE_XET'] = '1'
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1)
JOBS: dict[str, dict] = {}
PROCESSES: dict[str, subprocess.Popen] = {}
FUTURES: dict[str, concurrent.futures.Future] = {}
JOB_TYPES: dict[str, tuple] = {}
LOCK = threading.RLock()
SHUTTING_DOWN = False
SERVER = None
SESSIONS: set[str] = set()
ALLOWED_ORIGINS = {f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}'}
if (ROOT/'allowed-origins.json').exists():
    ALLOWED_ORIGINS.update(json.loads((ROOT/'allowed-origins.json').read_text('utf-8')))
app = FastAPI(title='ClipCanvas', version='2.0.0', docs_url=None, redoc_url=None)


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value):
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), 'utf-8')
    os.replace(temporary, path)


def identifier(value: str):
    if not re.fullmatch(r'[a-f0-9]{32}', value or ''):
        raise HTTPException(400, 'Некорректный идентификатор.')
    return value


def authorized(request):
    header = request.headers.get('authorization', '')
    token = header[7:] if header.lower().startswith('bearer ') else ''
    return bool(TOKEN and token and hmac.compare_digest(token, TOKEN)) or request.cookies.get('clipcanvas_session', '') in SESSIONS


@app.middleware('http')
async def access(request: Request, call_next):
    hostname = request.url.hostname or ''
    origin = request.headers.get('origin')
    same_origin = f'{request.url.scheme}://{request.headers.get("host", "")}'
    valid_host = hostname in {'localhost', '127.0.0.1', '::1', HOST}
    if REMOTE and not valid_host:
        try:
            ipaddress.ip_address(hostname)
            valid_host = True
        except ValueError:
            pass
    if not valid_host or (origin and origin not in ALLOWED_ORIGINS and origin != same_origin):
        return JSONResponse({'detail': 'Этот адрес сайта не разрешён для движка.'}, status_code=403)
    protected = request.url.path.startswith('/api/') and request.url.path not in {'/api/health', '/api/auth'}
    if REMOTE and protected and request.method != 'OPTIONS' and not authorized(request):
        return JSONResponse({'detail': 'Требуется токен доступа к этому компьютеру.'}, status_code=401)
    if request.method == 'OPTIONS':
        from fastapi.responses import Response
        response = Response(status_code=204)
    else:
        response = await call_next(request)
    if origin:
        response.headers.update({'Access-Control-Allow-Origin': origin, 'Vary': 'Origin',
            'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization',
            'Access-Control-Allow-Credentials': 'true', 'Access-Control-Allow-Private-Network': 'true'})
    return response


@app.exception_handler(RequestValidationError)
async def invalid_settings(request: Request, error: RequestValidationError):
    messages = []
    for item in error.errors()[:3]:
        field = '.'.join(str(x) for x in item['loc'] if x != 'body')
        messages.append(item['msg'].removeprefix('Value error, ') if item['type'] == 'value_error' else f'Параметр «{field}»: {item["msg"]}.')
    return JSONResponse({'detail': ' '.join(messages)}, status_code=422)


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Geometry(Strict):
    x: float = Field(ge=-15360, le=15360)
    y: float = Field(ge=-15360, le=15360)
    w: float = Field(ge=16, le=15360)
    h: float = Field(ge=16, le=15360)
    fit: Literal['contain', 'cover', 'stretch'] = 'contain'


class Canvas(Strict):
    width: int = Field(ge=240, le=3840)
    height: int = Field(ge=240, le=3840)
    @model_validator(mode='after')
    def even(self):
        if self.width % 2 or self.height % 2:
            raise ValueError('Размеры холста должны быть чётными.')
        return self


class Background(Strict):
    enabled: bool = True
    blur: float = Field(default=24, ge=0, le=80)
    color: str = Field(default='#111827', pattern=r'^#[0-9a-fA-F]{6}$')


class Banner(Strict):
    enabled: bool = False
    start: float = Field(default=0, ge=0)
    end: float = Field(default=10, ge=0)
    asset_id: str | None = None
    geometry: Geometry = Field(default_factory=lambda: Geometry(x=0, y=0, w=16, h=16))
    @model_validator(mode='before')
    @classmethod
    def inactive(cls, values):
        if isinstance(values, dict) and not values.get('enabled', False):
            values = {**values, 'start': 0, 'end': 10, 'asset_id': None,
                      'geometry': {'x':0, 'y':0, 'w':16, 'h':16, 'fit':'contain'}}
        return values
    @model_validator(mode='after')
    def interval(self):
        if self.enabled and (not self.asset_id or self.end <= self.start):
            raise ValueError('Проверьте изображение и время показа баннера.')
        return self


class Word(Strict):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=2000)
    probability: float | None = Field(default=None, ge=0, le=1)
    @model_validator(mode='after')
    def interval(self):
        if self.end <= self.start:
            raise ValueError('Некорректное время слова.')
        return self


class Cue(Strict):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=2000)
    words: list[Word] = Field(default_factory=list, max_length=1000)
    timing_quality: str = 'phrase'
    @model_validator(mode='after')
    def interval(self):
        if self.end <= self.start:
            raise ValueError('Конец субтитра должен быть позже начала.')
        for word in self.words:
            if word.start < self.start-.02 or word.end > self.end+.02:
                raise ValueError('Время слова выходит за границы фразы.')
        return self


class Subtitles(Strict):
    enabled: bool = False
    size: int = Field(default=52, ge=16, le=160)
    color: str = Field(default='#ffffff', pattern=r'^#[0-9a-fA-F]{6}$')
    y: float = Field(default=84, ge=5, le=95)
    style: Literal['clean', 'word', 'cinema'] = 'clean'
    outline: float = Field(default=2, ge=0, le=12)
    background: bool = False
    highlight_color: str = Field(default='#a3e635', pattern=r'^#[0-9a-fA-F]{6}$')
    cues: list[Cue] = Field(default_factory=list, max_length=100000)
    @model_validator(mode='before')
    @classmethod
    def inactive(cls, values):
        if isinstance(values, dict) and not values.get('enabled', False):
            values = {**values, 'size': 52, 'color': '#ffffff', 'y': 84, 'cues': []}
        return values
    @model_validator(mode='after')
    def normalize(self):
        self.cues = [Cue.model_validate(c) for c in normalize_cues([x.model_dump() for x in self.cues])]
        return self


class Part(Strict):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    @model_validator(mode='after')
    def interval(self):
        if self.end-self.start < .099:
            raise ValueError('Отрезок должен быть не короче 0,1 секунды.')
        return self


class Advertising(Strict):
    enabled: bool = False
    asset_id: str | None = None
    start: float = Field(default=20, ge=0)
    repeat: bool = True
    interval: float = Field(default=60, gt=0)
    speed: float = Field(default=1, ge=1, le=1.5)
    geometry: Geometry | None = None
    audio_stream_index: int | None = Field(default=None, ge=0)
    profile: Literal['floatup', 'buba', 'custom'] = 'floatup'
    placement: Literal['time', 'middle'] = 'time'
    chromakey: dict = Field(default_factory=lambda: {'enabled':False,'color':'#00ff00','similarity':.15,'blend':.05})
    duration: float | None = Field(default=None, gt=0)
    @model_validator(mode='after')
    def required(self):
        if self.enabled and not self.asset_id:
            raise ValueError('Загрузите рекламный видеоролик.')
        key = self.chromakey
        if set(key)-{'enabled','color','similarity','blend'}:
            raise ValueError('Неизвестный параметр хромакея.')
        color = key.get('color','#00ff00')
        if not isinstance(color,str) or not re.fullmatch(r'#[a-fA-F0-9]{6}',color):
            raise ValueError('Цвет хромакея должен иметь формат #00ff00.')
        for name,default,minimum in [('similarity',.15,.01),('blend',.05,0)]:
            value = key.get(name,default)
            if not isinstance(value,(int,float)) or not math.isfinite(value) or not minimum <= value <= 1:
                raise ValueError('Параметры хромакея должны быть в диапазоне 0–1.')
        if self.enabled and self.profile=='buba' and self.speed>1.2:
            raise ValueError('Профиль BUBA допускает скорость рекламы не выше 1,2.')
        return self


class Render(Strict):
    source_id: str
    audio_stream_index: int | None = Field(default=None, ge=0)
    canvas: Canvas
    video: Geometry
    background: Background
    banner: Banner = Field(default_factory=Banner)
    subtitles: Subtitles = Field(default_factory=Subtitles)
    advertising: Advertising = Field(default_factory=Advertising)
    parts: list[Part] = Field(min_length=1, max_length=1000)
    quality: Literal['standard', 'high'] = 'standard'
    fps: Literal[24, 30, 60] = 30
    zip: bool = False
    @model_validator(mode='after')
    def bounds(self):
        layers = [self.video]
        if self.banner.enabled:
            layers.append(self.banner.geometry)
        if self.advertising.enabled and self.advertising.geometry:
            layers.append(self.advertising.geometry)
        for g in layers:
            if g.w > self.canvas.width*4 or g.h > self.canvas.height*4:
                raise ValueError('Слой слишком велик для выбранного холста.')
            if g.x+g.w <= 0 or g.y+g.h <= 0 or g.x >= self.canvas.width or g.y >= self.canvas.height:
                raise ValueError('Слой должен находиться хотя бы частично внутри холста.')
        return self


class Source(Strict):
    source_id: str
    audio_stream_index: int | None = Field(default=None, ge=0)


class Transcribe(Source):
    language: Literal['auto', 'ru', 'en', 'es', 'de', 'fr', 'uk'] = 'ru'
    model: Literal['tiny', 'base', 'small'] = 'base'


class Extract(Source):
    subtitle_stream_index: int = Field(ge=0)


class Project(Strict):
    id: str | None = None
    name: str = Field(default='Новый проект', min_length=1, max_length=200)
    source_id: str | None = None
    advert_id: str | None = None
    banner_id: str | None = None
    settings: dict = Field(default_factory=dict)
    timeline: list = Field(default_factory=list, max_length=1000)


def probe(path: Path):
    with av.open(str(path)) as container:
        videos = [s for s in container.streams.video if not (int(s.disposition.value) & 1024)]
        if not videos:
            raise ValueError('В файле нет видеодорожки.')
        stream = videos[0]
        duration = float(stream.duration*stream.time_base) if stream.duration else 0
        if not duration and stream.metadata.get('DURATION'):
            from clipcanvas_media import seconds
            duration = seconds(stream.metadata['DURATION'])
        if not duration:
            duration = container.duration/av.time_base if container.duration else 0
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('Не удалось определить длительность видео.')
        w, h = stream.codec_context.width, stream.codec_context.height
        w = round(w*float(stream.sample_aspect_ratio or 1))
        info = subprocess.run([FFMPEG, '-hide_banner', '-i', str(path)], capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=30)
        rotation = re.search(r'rotation of\s+(-?[\d.]+)', info.stderr)
        angle = float(rotation.group(1)) if rotation else float(stream.metadata.get('rotate', '0'))
        if abs(round(angle)) % 180 == 90:
            w, h = h, w
        audio, subtitles = [], []
        for s in container.streams:
            flags = int(s.disposition.value)
            common = {'index': s.index, 'codec': s.codec_context.name,
                      'language': s.metadata.get('language', 'und'), 'default': bool(flags & 1),
                      'start_time': float(s.start_time*s.time_base) if s.start_time is not None else 0,
                      'time_base': str(s.time_base)}
            if s.type == 'audio':
                audio.append({**common, 'channels': s.codec_context.channels})
            elif s.type == 'subtitle':
                supported = s.codec_context.name in {'srt', 'subrip', 'ass', 'ssa', 'webvtt', 'mov_text', 'text'}
                subtitles.append({**common, 'forced': bool(flags & 64), 'hearing_impaired': bool(flags & 128),
                                  'supported': supported, 'type': 'text' if supported else 'bitmap',
                                  'unsupported_reason': None if supported else 'Нужен OCR; в этой версии он не реализован.'})
        preferred_audio = next((s for s in audio if s['default']), audio[0] if audio else None)
        return {'width': w, 'height': h, 'duration': duration, 'audio': bool(audio),
                'fps':float(stream.average_rate) if stream.average_rate else None,
                'video_stream_index': stream.index, 'video_start_time': float(stream.start_time*stream.time_base) if stream.start_time is not None else 0,
                'container_start_time': container.start_time/av.time_base if container.start_time is not None else 0,
                'audio_streams': audio, 'subtitle_streams': subtitles,
                'recommended_audio_stream_index': preferred_audio['index'] if preferred_audio else None,
                'recommended_subtitle_stream_index': subtitle_recommendation(subtitles, preferred_audio['language'] if preferred_audio else None),
                'inventory_version': 2}


def asset(asset_id: str, kind: str | None = None):
    path = DATA/'uploads'/identifier(asset_id)/'asset.json'
    if not path.exists():
        raise HTTPException(404, 'Файл не найден. Загрузите его повторно.')
    result = json.loads(path.read_text('utf-8'))
    if kind and result['kind'] != kind:
        raise HTTPException(400, 'Выбран неподходящий тип файла.')
    media = path.parent/result['stored_name']
    if not media.is_file() or media.resolve().parent != path.parent.resolve():
        raise HTTPException(404, 'Исходный файл отсутствует.')
    if result['kind'] in {'video', 'advert'} and result.get('inventory_version') != 2:
        with LOCK:
            result.update(probe(media))
            atomic_json(path, result)
    return {**result, 'path': media}


def public_asset(value):
    return {k:v for k,v in value.items() if k != 'path'}


def audio_index(source, selected):
    if selected is None:
        return source.get('recommended_audio_stream_index')
    if selected not in {s['index'] for s in source['audio_streams']}:
        raise HTTPException(400, 'Выбранная звуковая дорожка отсутствует в этом файле.')
    return selected


@app.get('/api/health')
def health(request: Request = None):
    result = {'status':'ok', 'app':'ClipCanvas', 'version':'2.0.0', 'ffmpeg':Path(FFMPEG).exists(),
            'root':str(ROOT), 'instance_id':INSTANCE_ID, 'pid':os.getpid(),
            'capabilities':{'auth_required':REMOTE, 'audio_tracks':True, 'embedded_subtitles':True,
                'bitmap_ocr':False, 'advertising':True, 'projects':True, 'retry':True,
                'max_parts':1000, 'subtitle_styles':['clean','word','cinema'], 'sample_max_source_duration':30}}
    if REMOTE and not (request and authorized(request)):
        result.pop('root',None)
        result.pop('pid',None)
    return result


@app.post('/api/auth')
async def authenticate(request: Request):
    value = await request.json()
    if TOKEN and not hmac.compare_digest(str(value.get('token', '')), TOKEN):
        raise HTTPException(401, 'Неверный токен доступа.')
    session = secrets.token_urlsafe(32)
    SESSIONS.add(session)
    response = JSONResponse({'status':'ok'})
    response.set_cookie('clipcanvas_session', session, httponly=True, samesite='strict', secure=request.url.scheme=='https')
    return response


@app.post('/api/logout')
def logout(request: Request):
    SESSIONS.discard(request.cookies.get('clipcanvas_session',''))
    response = JSONResponse({'status':'ok'})
    response.delete_cookie('clipcanvas_session')
    return response


@app.post('/api/uploads')
async def upload(kind: Literal['video', 'banner', 'advert'], file: UploadFile = File(...)):
    import asyncio
    asset_id = uuid.uuid4().hex
    folder = DATA/'uploads'/asset_id
    folder.mkdir()
    extension = Path(file.filename or '').suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,10}', extension):
        extension = '.bin'
    path = folder/('source'+extension)
    try:
        total, digest = 0, hashlib.sha256()
        with path.open('wb') as output:
            while chunk := await file.read(4*1024*1024):
                total += len(chunk)
                if kind == 'banner' and total > 30*1024*1024:
                    raise ValueError('Баннер должен быть не больше 30 МБ.')
                if shutil.disk_usage(DATA).free < len(chunk)+16*1024*1024:
                    raise ValueError('Недостаточно свободного места для загрузки.')
                digest.update(chunk)
                output.write(chunk)
        if not total:
            raise ValueError('Файл пуст.')
        if kind in {'video', 'advert'}:
            meta = await asyncio.to_thread(probe, path)
        else:
            normalized = folder/'banner.png'
            completed = await asyncio.to_thread(subprocess.run,
                [FFMPEG,'-y','-v','error','-i',str(path),'-frames:v','1',str(normalized)], capture_output=True, timeout=30)
            if completed.returncode:
                raise ValueError('Изображение не удалось прочитать.')
            if normalized != path:
                path.unlink()
            path, meta = normalized, {}
        result = {'id':asset_id, 'kind':kind, 'name':file.filename or 'file', 'stored_name':path.name,
                  'sha256':digest.hexdigest(), 'size':total, 'created_at':now(), **meta}
        atomic_json(folder/'asset.json', result)
        return result
    except Exception as error:
        shutil.rmtree(folder)
        raise HTTPException(400, f'Не удалось открыть файл: {error}') from error
    finally:
        await file.close()


@app.get('/api/assets')
def list_assets():
    values = []
    for path in (DATA/'uploads').glob('*/asset.json'):
        try:
            values.append(public_asset(asset(path.parent.name)))
        except (HTTPException, ValueError, OSError):
            continue
    return {'assets':sorted(values, key=lambda a:a.get('created_at',''), reverse=True)}


@app.get('/api/assets/{asset_id}')
def get_asset(asset_id: str):
    return public_asset(asset(asset_id))


@app.get('/api/media/{asset_id}')
def media(asset_id: str):
    value = asset(asset_id)
    return FileResponse(value['path'])


def persist(job_id):
    with LOCK:
        atomic_json(DATA/'jobs'/job_id/'job.json', JOBS[job_id])


def update(job_id, **values):
    with LOCK:
        JOBS[job_id].update(values, updated_at=now())
        persist(job_id)


def cancelled(job_id):
    with LOCK:
        return SHUTTING_DOWN or JOBS[job_id].get('cancel_requested', False)


class Cancelled(Exception):
    pass


def enqueue(job_id, task, request):
    future = None
    def run():
        try:
            if cancelled(job_id):
                raise Cancelled()
            update(job_id, status='running')
            task(job_id, request)
            if cancelled(job_id):
                raise Cancelled()
            update(job_id, status='done', progress=100, message='Готово')
        except Cancelled:
            update(job_id, status='cancelled', message='Обработка остановлена. Готовые части сохранены; можно продолжить.')
        except Exception as error:
            update(job_id, status='error', error=str(error), message=str(error))
        finally:
            with LOCK:
                process = PROCESSES.pop(job_id, None)
                if process and process.poll() is None:
                    terminate_process(process)
                if FUTURES.get(job_id) is future:
                    FUTURES.pop(job_id, None)
            clean_job_temporary_files(job_id)
    with LOCK:
        future = POOL.submit(run)
        FUTURES[job_id] = future


def create_job(task, request, kind=None, cached=None):
    with LOCK:
        if SHUTTING_DOWN:
            raise HTTPException(503, 'Программа завершает работу.')
        active = sum(j['status'] in {'queued','running'} for j in JOBS.values())
        if active >= 3 and cached is None:
            raise HTTPException(429, 'Очередь занята. Дождитесь завершения обработки.')
        job_id = uuid.uuid4().hex
        (DATA/'jobs'/job_id).mkdir()
        JOBS[job_id] = {'id':job_id,'type':kind or task.__name__,'status':'queued','progress':0,'message':'В очереди…',
                       'files':[],'outputs':[],'completed_parts':[],'cancel_requested':False,
                       'request':request.model_dump() if hasattr(request,'model_dump') else dict(request),'created_at':now(),'updated_at':now()}
        persist(job_id)
        if cached is not None:
            write_srt(DATA/'jobs'/job_id/'subtitles.srt', cached['cues'])
            update(job_id, **cached, cached=True, status='done', progress=100, message='Готово: использован кэш',
                   files=[file_record(job_id,'subtitles.srt')])
        else:
            enqueue(job_id, task, request)
    return {'id':job_id}


def register_job_type(name, model, task):
    JOB_TYPES[name] = (model,task)


def clean_job_temporary_files(job_id):
    directory = DATA/'jobs'/identifier(job_id)
    for pattern in ('segment-*.mp4','*.freeze.png','*.ass','concat-*.txt','*.zip.tmp','speech.wav'):
        for path in directory.glob(pattern):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    job = JOBS[job_id]
    if job['status'] in {'cancelled','error'}:
        completed = {f['name'] for part in job.get('completed_parts',[]) for f in part.get('files',[])}
        for path in directory.glob('part-*.mp4'):
            if path.name not in completed:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass


def terminate_process(process):
    """Kill a verified live owned Popen and its descendants before its parent."""
    if process.poll() is not None:
        return
    if os.name == 'nt':
        executable = Path(os.environ.get('SystemRoot','C:/Windows'))/'System32'/'taskkill.exe'
        try:
            subprocess.run([str(executable),'/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=8)
        except (OSError,subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_ffmpeg(job_id, arguments, duration, offset=0, share=100, log_name='ffmpeg.log'):
    directory = DATA/'jobs'/job_id
    if cancelled(job_id):
        raise Cancelled()
    with (directory/log_name).open('w',encoding='utf-8') as log:
        process = subprocess.Popen([FFMPEG,'-y','-hide_banner','-nostdin','-progress','pipe:1','-nostats',*arguments],
            stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8',errors='replace',cwd=directory)
        with LOCK:
            PROCESSES[job_id] = process
            if cancelled(job_id):
                terminate_process(process)
        try:
            for line in process.stdout:
                if cancelled(job_id) and process.poll() is None:
                    terminate_process(process)
                if line.startswith('out_time_us='):
                    try:
                        sec = int(line.split('=',1)[1])/1e6
                        update(job_id, progress=min(99, offset+share*min(1,max(0,sec)/max(.1,duration))))
                    except ValueError:
                        pass
            code = process.wait()
        finally:
            process.stdout.close()
            if process.poll() is None:
                terminate_process(process)
            with LOCK:
                if PROCESSES.get(job_id) is process:
                    PROCESSES.pop(job_id,None)
        if cancelled(job_id):
            raise Cancelled()
        if code:
            detail = (directory/log_name).read_text('utf-8',errors='replace')[-1800:]
            raise RuntimeError(f'Ошибка FFmpeg ({log_name}). '+detail)


def even(n):
    return max(16,round(n/2)*2)


def layer_filter(label, geometry, out):
    w,h = even(geometry.w),even(geometry.h)
    if geometry.fit == 'stretch':
        return f'[{label}]scale={w}:{h},setsar=1,format=rgba[{out}]'
    if geometry.fit == 'cover':
        return f'[{label}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=rgba[{out}]'
    return f'[{label}]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1,format=rgba,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x00000000[{out}]'


def ass_time(seconds):
    cs = max(0,round(seconds*100))
    return f'{cs//360000}:{cs//6000%60:02d}:{cs//100%60:02d}.{cs%100:02d}'


def ass_color(value):
    rgb = value.lstrip('#')
    return '&H00'+rgb[4:6]+rgb[2:4]+rgb[0:2]


def ass_text(value):
    return value.replace('\\','／').replace('{','(').replace('}',')').replace('\r','').replace('\n',r'\N')


def word_caption_events(cue,start,end,normal,highlight):
    """Highlight only the currently spoken word, preserving the edited sentence."""
    spans,cursor = [],0
    for word in cue.words:
        token = word.text.strip()
        if not token:
            continue
        found = cue.text.find(token,cursor)
        if found < 0:
            return [(start,end,ass_text(cue.text))]
        spans.append((found,found+len(token),word.start,word.end))
        cursor = found+len(token)
    if not spans:
        return [(start,end,ass_text(cue.text))]
    boundaries = sorted({start,end,*[max(start,min(end,t)) for span in spans for t in span[2:]]})
    result = []
    for a,b in zip(boundaries,boundaries[1:]):
        if b-a < .005:
            continue
        moment = (a+b)/2
        pieces,cursor = [],0
        for left,right,word_start,word_end in spans:
            pieces.append(ass_text(cue.text[cursor:left]))
            token = ass_text(cue.text[left:right])
            if word_start <= moment < word_end:
                token = r'{\1c'+ass_color(highlight)+'&}'+token+r'{\1c'+ass_color(normal)+'&}'
            pieces.append(token)
            cursor = right
        pieces.append(ass_text(cue.text[cursor:]))
        result.append((a,b,''.join(pieces)))
    return result


def write_ass(path, request, part):
    c,sub = request.canvas,request.subtitles
    primary = ass_color(sub.color)
    secondary = ass_color(sub.color)
    bold = 0 if sub.style == 'cinema' else -1
    font = 'Georgia' if sub.style == 'cinema' else 'Arial'
    border = 3 if sub.background else 1
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {c.width}
PlayResY: {c.height}
WrapStyle: 0
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{sub.size},{primary},{secondary},&H00000000,&H80000000,{bold},0,0,0,100,100,0,0,{border},{sub.outline},0,5,{round(c.width*.06)},{round(c.width*.06)},0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    events = []
    for cue in sub.cues:
        start,end = max(cue.start,part.start),min(cue.end,part.end)
        if start >= end or not cue.text.strip():
            continue
        pieces = word_caption_events(cue,start,end,sub.color,sub.highlight_color) if sub.style=='word' else [(start,end,ass_text(cue.text))]
        position = r'{\pos('+f'{c.width/2:.1f},{c.height*sub.y/100:.1f}'+')}'
        for a,b,text in pieces:
            events.append(f'Dialogue: 0,{ass_time(a-part.start)},{ass_time(b-part.start)},Default,,0,0,0,,{position}{text}\n')
    path.write_text(header+''.join(events),'utf-8-sig')


def srt_time(seconds):
    ms = max(0,round(seconds*1000))
    return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}'


def write_srt(path,cues):
    path.write_text('\n'.join(f'{i+1}\n{srt_time(c["start"])} --> {srt_time(c["end"])}\n{c["text"]}\n' for i,c in enumerate(cues)),'utf-8-sig')


def file_record(job_id,name,**metadata):
    return {'name':name,'url':f'/api/jobs/{job_id}/files/{name}',**metadata}


def output_metadata(path):
    with av.open(str(path)) as container:
        video = container.streams.video[0]
        return {'duration':container.duration/av.time_base if container.duration else 0,
                'width':video.codec_context.width,'height':video.codec_context.height,
                'fps':float(video.average_rate) if video.average_rate else None,'frames':video.frames,
                'size':path.stat().st_size}


def render_segment(job_id,request,source,segment,name,progress_offset,progress_share,is_ad=False,paused_source=None):
    directory = DATA/'jobs'/job_id
    duration = segment['duration']
    speed = request.advertising.speed if is_ad else 1
    start = 0 if is_ad else segment['start']
    seek = start+max(0,source.get('video_start_time',0)-source.get('container_start_time',0))
    args = ['-ss',f'{seek:.6f}','-i',str(source['path'])]
    selected = audio_index(source,request.advertising.audio_stream_index if is_ad else request.audio_stream_index)
    next_input = 1
    if selected is None:
        args += ['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
        audio_label = '1:a:0'
        next_input = 2
    else:
        audio_label = f'0:{selected}'
    frozen_input = None
    freeze_file = None
    if is_ad and paused_source:
        frozen_input = next_input
        last_frame = max(0,paused_source['duration']-1/(paused_source.get('fps') or 30))
        frozen_seek = min(segment['source_position'],last_frame)+max(0,paused_source.get('video_start_time',0)-paused_source.get('container_start_time',0))
        freeze_file = directory/name.replace('.mp4','.freeze.png')
        run_ffmpeg(job_id,['-ss',f'{frozen_seek:.6f}','-i',str(paused_source['path']),'-map',
            f'0:{paused_source["video_stream_index"]}','-vf','scale=iw*sar:ih,setsar=1',
            '-frames:v','1','-update','1',freeze_file.name],.1,progress_offset,0,name.replace('.mp4','-freeze.log'))
        if not freeze_file.is_file():
            raise RuntimeError('Не удалось получить кадр для рекламной вставки.')
        args += ['-loop','1','-framerate',str(request.fps),'-i',str(freeze_file)]
        next_input += 1
    banner = None
    if not is_ad and request.banner.enabled and request.banner.start < segment['end'] and request.banner.end > start:
        banner = asset(request.banner.asset_id,'banner')
        args += ['-loop','1','-i',str(banner['path'])]
    c,bg = request.canvas,request.background
    geometry = request.advertising.geometry or Geometry(x=0,y=0,w=c.width,h=c.height,fit='contain') if is_ad else request.video
    video_label = f'0:{source["video_stream_index"]}'
    filters = []
    base = f'[{video_label}]setpts=(PTS-STARTPTS)/{speed},scale=iw*sar:ih,setsar=1'
    if frozen_input is not None:
        filters.append(base+'[mainraw]')
        frozen_base = f'[{frozen_input}:v:0]setpts=PTS-STARTPTS,scale=iw*sar:ih,setsar=1'
        if bg.enabled:
            blur = round(bg.blur)
            bw,bh = c.width+blur*4,c.height+blur*4
            filters += [frozen_base+',split=2[fmain][fbgsource]',
                f'[fbgsource]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh}'+
                (f',gblur=sigma={blur}' if blur else '')+f',crop={c.width}:{c.height}:{blur*2}:{blur*2}[bgbase]']
        else:
            filters += [frozen_base+'[fmain]',f'color=c={bg.color}:s={c.width}x{c.height}:r={request.fps}:d={duration:.6f}[bgbase]']
        original_geometry = request.video
        filters += [layer_filter('fmain',original_geometry,'frozenforeground'),
            f'[bgbase][frozenforeground]overlay=x={round(original_geometry.x)}:y={round(original_geometry.y)}:shortest=1[bg]']
    elif bg.enabled:
        blur = round(bg.blur)
        bw,bh = c.width+blur*4,c.height+blur*4
        filters += [base+',split=2[mainraw][bgsource]',f'[bgsource]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh}'+(f',gblur=sigma={blur}' if blur else '')+f',crop={c.width}:{c.height}:{blur*2}:{blur*2}[bg]']
    else:
        filters += [base+'[mainraw]',f'color=c={bg.color}:s={c.width}x{c.height}:r={request.fps}:d={duration:.6f}[bg]']
    key = request.advertising.chromakey
    if is_ad and key.get('enabled',False):
        filters.append(f'[mainraw]format=rgba,colorkey=0x{key.get("color","#00ff00").lstrip("#")}:{key.get("similarity",.15)}:{key.get("blend",.05)}[main]')
    else:
        filters.append('[mainraw]null[main]')
    filters += [layer_filter('main',geometry,'foreground'),f'[bg][foreground]overlay=x={round(geometry.x)}:y={round(geometry.y)}:shortest=1[composite]']
    last = 'composite'
    if banner:
        b = request.banner.geometry
        a,z = max(0,request.banner.start-start),min(duration,request.banner.end-start)
        filters += [layer_filter(f'{next_input}:v:0',b,'banner'),f"[composite][banner]overlay=x={round(b.x)}:y={round(b.y)}:enable='gte(t,{a:.6f})*lt(t,{z:.6f})':shortest=1[advert]"]
        last = 'advert'
    if not is_ad and request.subtitles.enabled and request.subtitles.cues:
        ass_name = name.replace('.mp4','.ass')
        write_ass(directory/ass_name,request,Part.model_construct(start=start,end=segment['end']))
        # Native DirectWrite/fontconfig finds fonts without scanning every Windows .fon.
        filters += [f'[{last}]ass=filename={ass_name}[captioned]']
        last = 'captioned'
    filters += [f'[{last}]tpad=stop_mode=clone:stop_duration={duration:.6f},fps={request.fps}:start_time=0,'
                f'trim=duration={duration:.6f},setpts=N/({request.fps}*TB),format=yuv420p[out]']
    delay = 0
    if selected is not None:
        track = next(t for t in source['audio_streams'] if t['index']==selected)
        delay = max(0,track['start_time']-source.get('video_start_time',0)-start)/speed
    audio_filter = f'[{audio_label}]asetpts=PTS-STARTPTS,aresample=48000'
    if speed != 1:
        audio_filter += f',atempo={speed}'
    if delay > .001:
        audio_filter += f',adelay={round(delay*1000)}:all=1'
    audio_filter += f',apad=whole_dur={duration:.6f},atrim=duration={duration:.6f}[sound]'
    filters.append(audio_filter)
    args += ['-filter_complex_threads','2','-filter_complex',';'.join(filters),'-map','[out]','-map','[sound]',
             '-t',f'{duration:.6f}','-c:v','libx264','-preset','veryfast' if request.quality=='standard' else 'medium',
             '-crf','23' if request.quality=='standard' else '18','-threads',str(min(8,os.cpu_count() or 4)),
             '-c:a','aac','-ac','2','-ar','48000','-b:a','192k','-movflags','+faststart',name]
    run_ffmpeg(job_id,args,duration,progress_offset,progress_share,name.replace('.mp4','.log'))
    metadata = output_metadata(directory/name)
    if metadata.get('frames',0) < max(1,math.floor(duration*request.fps)-1):
        raise RuntimeError(f'Неполный видеосегмент {name}: проверьте исходную видеодорожку.')
    if freeze_file:
        freeze_file.unlink(missing_ok=True)


def validate_render(request):
    source = asset(request.source_id,'video')
    audio_index(source,request.audio_stream_index)
    if any(p.end > source['duration']+.05 for p in request.parts):
        raise HTTPException(400,'Конец отрезка выходит за длительность видеодорожки.')
    if request.banner.enabled:
        asset(request.banner.asset_id,'banner')
    ad = asset(request.advertising.asset_id,'advert') if request.advertising.enabled else None
    if ad:
        audio_index(ad,request.advertising.audio_stream_index)
        for part in request.parts:
            try:
                render_timeline(request,part,ad)
            except ValueError as error:
                raise HTTPException(400,str(error)) from error
    if shutil.disk_usage(DATA).free < 32*1024*1024:
        raise HTTPException(400,'Недостаточно свободного места для экспорта.')
    return source,ad


def render_timeline(request,part,ad):
    middle = request.advertising.placement=='middle'
    return timeline(part.start,part.end,ad['duration']/request.advertising.speed if ad else 0,
                    (part.end-part.start)/2 if middle else request.advertising.start,
                    False if middle else request.advertising.repeat,request.advertising.interval)


def render_job(job_id,request):
    source,ad = validate_render(request)
    directory = DATA/'jobs'/job_id
    files,outputs,completed = [],[],[]
    previous = {x['index']:x for x in JOBS[job_id].get('completed_parts',[])}
    cues = [x.model_dump() for x in request.subtitles.cues]
    for i,part in enumerate(request.parts):
        if cancelled(job_id):
            raise Cancelled()
        name = f'part-{i+1:02d}.mp4'
        existing = previous.get(i)
        if existing and all((directory/f['name']).is_file() for f in existing.get('files',[])) and (directory/name).is_file() and (directory/name).stat().st_size == existing.get('size'):
            files.extend(existing['files'])
            outputs.append(existing['output'])
            completed.append(existing)
            update(job_id,files=files.copy(),outputs=outputs.copy(),completed_parts=completed.copy(),progress=(i+1)*95/len(request.parts),message=f'Часть {i+1} уже готова — пропущена')
            continue
        plan = render_timeline(request,part,ad)
        total_duration = sum(s['duration'] for s in plan)
        fragments = []
        update(job_id,message=f'Рендер части {i+1} из {len(request.parts)}')
        for n,segment in enumerate(plan):
            fragment = f'segment-{i+1:04d}-{n+1:04d}.mp4' if len(plan)>1 else name
            render_segment(job_id,request,ad if segment['kind']=='advert' else source,segment,fragment,
                i*95/len(request.parts)+95/len(request.parts)*segment['output_start']/total_duration,
                95/len(request.parts)*segment['duration']/total_duration,segment['kind']=='advert',source)
            fragments.append(fragment)
        if len(fragments)>1:
            concat_name = f'concat-{i+1:04d}.txt'
            (directory/concat_name).write_text(''.join(f"file '{f}'\n" for f in fragments),'utf-8')
            run_ffmpeg(job_id,['-f','concat','-safe','1','-i',concat_name,'-map','0:v:0','-map','0:a:0','-c','copy',
                              '-t',f'{total_duration:.6f}','-movflags','+faststart',name],total_duration,
                       (i+.95)*95/len(request.parts),.05*95/len(request.parts),f'part-{i+1:02d}-concat.log')
        metadata = output_metadata(directory/name)
        if abs(metadata['duration']-total_duration) > max(.25,1/request.fps*len(plan)*2):
            raise RuntimeError(f'Часть {i+1}: длительность результата не совпадает с монтажной шкалой.')
        part_files = [file_record(job_id,name,**metadata)]
        final_cues = captions_for_timeline(cues,plan)
        if request.subtitles.enabled and final_cues:
            srt_name = f'part-{i+1:02d}.srt'
            write_srt(directory/srt_name,final_cues)
            part_files.append(file_record(job_id,srt_name))
        output = {'index':i,'name':name,'source_start':part.start,'source_end':part.end,
                  'planned_duration':total_duration,**metadata,'timeline':plan}
        complete = {'index':i,'size':metadata['size'],'files':part_files,'output':output}
        completed.append(complete)
        files.extend(part_files)
        outputs.append(output)
        update(job_id,files=files.copy(),outputs=outputs.copy(),completed_parts=completed.copy())
        for fragment in fragments:
            if fragment != name:
                (directory/fragment).unlink(missing_ok=True)
        for temporary in directory.glob(f'segment-{i+1:04d}-*.ass'):
            temporary.unlink(missing_ok=True)
        (directory/f'concat-{i+1:04d}.txt').unlink(missing_ok=True)
    if request.zip:
        update(job_id,message='Сборка архива…',progress=97)
        temporary = directory/'all-parts.zip.tmp'
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
            for item in files:
                if cancelled(job_id):
                    raise Cancelled()
                archive.write(directory/item['name'],arcname=item['name'])
        os.replace(temporary,directory/'all-parts.zip')
        files = [file_record(job_id,'all-parts.zip'),*files]
    update(job_id,files=files,outputs=outputs)


@app.post('/api/render')
def render(request: Render):
    validate_render(request)
    return create_job(render_job,request,'render')


@app.post('/api/sample')
def sample(request: Render):
    if len(request.parts)!=1 or request.parts[0].end-request.parts[0].start>30.05:
        raise HTTPException(400,'Проверочный экспорт: одна исходная часть длительностью до 30 секунд.')
    validate_render(request)
    return create_job(render_job,request,'sample')


def preview_job(job_id,request):
    source = asset(request.source_id,'video')
    selected = audio_index(source,request.audio_stream_index)
    update(job_id,message='Подготовка совместимого предпросмотра…')
    args = ['-i',str(source['path']),'-map',f'0:{source["video_stream_index"]}']
    if selected is not None:
        args += ['-map',f'0:{selected}']
    args += ['-vf','scale=iw*sar:ih,setsar=1,scale=960:960:force_original_aspect_ratio=decrease:force_divisible_by=2',
             '-c:v','libx264','-preset','ultrafast','-crf','28','-threads',str(min(8,os.cpu_count() or 4)),
             '-c:a','aac','-movflags','+faststart','preview.mp4']
    run_ffmpeg(job_id,args,source['duration'])
    update(job_id,files=[file_record(job_id,'preview.mp4',**output_metadata(DATA/'jobs'/job_id/'preview.mp4'))],audio_stream_index=selected)


@app.post('/api/preview')
def preview(request: Source):
    audio_index(asset(request.source_id,'video'),request.audio_stream_index)
    return create_job(preview_job,request,'preview')


def cache_path(kind,source,**parameters):
    value = json.dumps({'version':2,'source':source.get('sha256',source['id']),**parameters},sort_keys=True)
    return DATA/'cache'/(kind+'-'+hashlib.sha256(value.encode()).hexdigest()+'.json')


def cached_result(path):
    try:
        return json.loads(path.read_text('utf-8')) if path.exists() else None
    except (ValueError,OSError):
        return None


def group_words(segments,job_id,duration):
    cues = []
    for segment in segments:
        if cancelled(job_id):
            raise Cancelled()
        update(job_id,progress=min(96,15+81*segment.end/max(.1,duration)),message=f'Распознано {segment.end:.0f} из {duration:.0f} секунд')
        words = list(segment.words or [])
        if not words:
            if segment.end>segment.start and segment.text.strip():
                cues.append({'start':segment.start,'end':segment.end,'text':segment.text.strip(),'words':[],'timing_quality':'phrase'})
            continue
        group = []
        def append_group():
            if group:
                cues.append({'start':group[0].start,'end':max(group[-1].end,group[0].start+.01),
                             'text':''.join(w.word for w in group).strip(),
                             'words':[{'start':w.start,'end':max(w.end,w.start+.001),'text':w.word,
                                       'probability':w.probability} for w in group], 'timing_quality':'asr'})
        for word in words:
            if group and (len(group)>=6 or len(''.join(w.word for w in group))+len(word.word)>42 or word.end-group[0].start>3):
                append_group()
                group = []
            group.append(word)
        append_group()
    return normalize_cues(cues,duration)


def transcribe_job(job_id,request):
    from faster_whisper import WhisperModel
    source = asset(request.source_id,'video')
    selected = audio_index(source,request.audio_stream_index)
    cache = cache_path('asr',source,audio=selected,language=request.language,model=request.model)
    previous = cached_result(cache)
    if previous:
        cues,language = previous['cues'],previous['language']
    else:
        directory = DATA/'jobs'/job_id
        update(job_id,message='Извлечение выбранной звуковой дорожки…')
        track = next(t for t in source['audio_streams'] if t['index']==selected)
        offset = track['start_time']-source.get('video_start_time',0)
        af = 'asetpts=PTS-STARTPTS'
        if offset < 0:
            af += f',atrim=start={-offset},asetpts=PTS-STARTPTS'
        elif offset > 0:
            af += f',adelay={round(offset*1000)}:all=1'
        run_ffmpeg(job_id,['-i',str(source['path']),'-map',f'0:{selected}','-vn','-af',af,'-t',str(source['duration']),'-ar','16000','-ac','1','speech.wav'],source['duration'],share=5)
        if cancelled(job_id):
            raise Cancelled()
        update(job_id,progress=7,message='Загрузка модели речи; при первом использовании нужен интернет…')
        model = WhisperModel(request.model,device='cpu',compute_type='int8',cpu_threads=min(8,os.cpu_count() or 4),download_root=str(DATA/'models'))
        if cancelled(job_id):
            raise Cancelled()
        update(job_id,progress=15,message='Распознавание речи…')
        segments,info = model.transcribe(str(directory/'speech.wav'),language=None if request.language=='auto' else request.language,beam_size=5,vad_filter=True,word_timestamps=True)
        cues,language = group_words(segments,job_id,source['duration']),info.language
        atomic_json(cache,{'cues':cues,'language':language,'origin':'asr','audio_stream_index':selected})
        (directory/'speech.wav').unlink(missing_ok=True)
    write_srt(DATA/'jobs'/job_id/'subtitles.srt',cues)
    update(job_id,cues=cues,language=language,origin='asr',audio_stream_index=selected,cached=bool(previous),files=[file_record(job_id,'subtitles.srt')])


@app.post('/api/transcribe')
def transcribe(request: Transcribe):
    source = asset(request.source_id,'video')
    selected = audio_index(source,request.audio_stream_index)
    if selected is None:
        raise HTTPException(400,'В этом видео нет звуковой дорожки.')
    previous = cached_result(cache_path('asr',source,audio=selected,language=request.language,model=request.model))
    return create_job(transcribe_job,request,'transcribe',previous)


def extract_job(job_id,request):
    source = asset(request.source_id,'video')
    directory = DATA/'jobs'/job_id
    track = next(t for t in source['subtitle_streams'] if t['index']==request.subtitle_stream_index)
    cache = cache_path('subtitles',source,track=request.subtitle_stream_index)
    previous = cached_result(cache)
    if previous:
        cues = previous['cues']
    else:
        update(job_id,message='Извлечение готовой текстовой дорожки…')
        run_ffmpeg(job_id,['-i',str(source['path']),'-map',f'0:{request.subtitle_stream_index}','-c:s','srt','extracted.srt'],source['duration'])
        cues = parse_subtitles((directory/'extracted.srt').read_text('utf-8-sig'))
        origin = source.get('video_start_time',0)-source.get('container_start_time',0)
        if origin:
            cues = [{**c,'start':max(0,c['start']-origin),'end':c['end']-origin} for c in cues if c['end']>origin]
        cues = normalize_cues(cues,source['duration'])
        atomic_json(cache,{'cues':cues,'language':track['language'],'origin':'embedded','subtitle_stream_index':track['index']})
    write_srt(directory/'subtitles.srt',cues)
    update(job_id,cues=cues,language=track['language'],origin='embedded',subtitle_stream_index=track['index'],
           forced=track['forced'],hearing_impaired=track['hearing_impaired'],cached=bool(previous),
           files=[file_record(job_id,'subtitles.srt')])


@app.post('/api/subtitles/extract')
def extract(request: Extract):
    source = asset(request.source_id,'video')
    audio_index(source,request.audio_stream_index)
    track = next((t for t in source['subtitle_streams'] if t['index']==request.subtitle_stream_index),None)
    if not track:
        raise HTTPException(400,'Выбранная дорожка субтитров отсутствует.')
    if not track['supported']:
        raise HTTPException(422,'Эта дорожка содержит изображения PGS/VobSub. OCR в этой версии не реализован; используйте текстовый SRT или распознавание речи.')
    previous = cached_result(cache_path('subtitles',source,track=request.subtitle_stream_index))
    return create_job(extract_job,request,'extract',previous)


@app.post('/api/subtitles/import')
async def import_subtitles(source_id: str | None = None,file: UploadFile = File(...)):
    try:
        raw = await file.read(10*1024*1024+1)
        if len(raw)>10*1024*1024:
            raise ValueError('Файл субтитров должен быть не больше 10 МБ.')
        if raw.startswith((b'\xff\xfe',b'\xfe\xff')):
            text = raw.decode('utf-16')
        else:
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('cp1251')
        cues = parse_subtitles(text,Path(file.filename or 'subtitles.srt').suffix)
        if source_id:
            cues = normalize_cues(cues,asset(source_id,'video')['duration'])
        return {'cues':cues,'language':None,'origin':'sidecar','word_timing':False}
    except (ValueError,UnicodeError) as error:
        raise HTTPException(400,str(error)) from error
    finally:
        await file.close()


@app.get('/api/jobs')
def list_jobs():
    with LOCK:
        return {'jobs':sorted([dict(j) for j in JOBS.values()],key=lambda j:j.get('created_at',''),reverse=True)}


@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    identifier(job_id)
    with LOCK:
        if job_id not in JOBS:
            raise HTTPException(404,'Задача не найдена.')
        return dict(JOBS[job_id])


@app.post('/api/jobs/{job_id}/cancel')
def cancel(job_id: str):
    get_job(job_id)
    with LOCK:
        job = JOBS[job_id]
        if job['status'] not in {'queued','running'}:
            return {'status':job['status']}
        update(job_id,cancel_requested=True,message='Остановка…')
        future = FUTURES.get(job_id)
        if future and future.cancel():
            FUTURES.pop(job_id,None)
            update(job_id,status='cancelled',message='Задача удалена из очереди.')
        process = PROCESSES.get(job_id)
        if process and process.poll() is None:
            terminate_process(process)
    return {'status':JOBS[job_id]['status']}


@app.post('/api/jobs/{job_id}/retry')
def retry(job_id: str):
    job = get_job(job_id)
    if job['status'] in {'queued','running'}:
        raise HTTPException(409,'Задача уже находится в очереди.')
    models = {**JOB_TYPES,'render':(Render,render_job),'sample':(Render,render_job),'preview':(Source,preview_job),
              'transcribe':(Transcribe,transcribe_job),'extract':(Extract,extract_job)}
    if job.get('type') not in models or not job.get('request'):
        raise HTTPException(400,'У этой старой задачи не сохранены настройки. Создайте новый экспорт.')
    model,task = models[job['type']]
    request = model.model_validate(job['request'])
    if isinstance(request,Render):
        validate_render(request)
    with LOCK:
        if sum(j['status'] in {'queued','running'} for j in JOBS.values())>=3:
            raise HTTPException(429,'Очередь занята.')
        update(job_id,status='queued',cancel_requested=False,error=None,message='Продолжение в очереди…')
        enqueue(job_id,task,request)
    return {'id':job_id}


@app.get('/api/jobs/{job_id}/files/{filename}')
def download(job_id: str,filename: str):
    job = get_job(job_id)
    if filename not in {f['name'] for f in job['files']}:
        raise HTTPException(404,'Файл не найден.')
    path = DATA/'jobs'/job_id/filename
    if path.resolve().parent != (DATA/'jobs'/job_id).resolve() or not path.is_file():
        raise HTTPException(404,'Файл результата отсутствует.')
    return FileResponse(path,filename=filename)


@app.post('/api/projects')
def save_project(request: Project):
    for key in ('source_id','advert_id','banner_id'):
        value = getattr(request,key)
        if value:
            asset(value,{'source_id':'video','advert_id':'advert','banner_id':'banner'}[key])
    project_id = identifier(request.id) if request.id else uuid.uuid4().hex
    path = DATA/'projects'/(project_id+'.json')
    previous = json.loads(path.read_text('utf-8')) if path.exists() else {}
    result = {**request.model_dump(),'id':project_id,'created_at':previous.get('created_at',now()),'updated_at':now()}
    with LOCK:
        atomic_json(path,result)
    return result


@app.get('/api/projects')
def list_projects():
    values = []
    for path in (DATA/'projects').glob('*.json'):
        try:
            values.append(json.loads(path.read_text('utf-8')))
        except (ValueError,OSError):
            pass
    return {'projects':sorted(values,key=lambda p:p.get('updated_at',''),reverse=True)}


@app.get('/api/projects/{project_id}')
def get_project(project_id: str):
    path = DATA/'projects'/(identifier(project_id)+'.json')
    if not path.exists():
        raise HTTPException(404,'Проект не найден.')
    return json.loads(path.read_text('utf-8'))


@app.delete('/api/projects/{project_id}')
def delete_project(project_id: str):
    path = DATA/'projects'/(identifier(project_id)+'.json')
    path.unlink(missing_ok=True)
    return {'status':'deleted'}


@app.delete('/api/jobs/{job_id}')
def delete_job(job_id: str):
    job = get_job(job_id)
    with LOCK:
        if job['status'] in {'queued','running'}:
            raise HTTPException(409,'Сначала остановите задачу.')
        folder = (DATA/'jobs'/identifier(job_id)).resolve()
        if folder.parent != (DATA/'jobs').resolve():
            raise HTTPException(400,'Некорректный путь.')
        shutil.rmtree(folder)
        JOBS.pop(job_id,None)
    return {'status':'deleted'}


@app.delete('/api/assets/{asset_id}')
def delete_asset(asset_id: str):
    asset(asset_id)
    with LOCK:
        for job in JOBS.values():
            if job['status'] in {'queued','running'} and asset_id in json.dumps(job.get('request',{})):
                raise HTTPException(409,'Файл используется активной задачей.')
        for project in list_projects()['projects']:
            if asset_id in json.dumps(project):
                raise HTTPException(409,'Файл используется сохранённым проектом. Сначала удалите проект.')
        folder = (DATA/'uploads'/identifier(asset_id)).resolve()
        if folder.parent != (DATA/'uploads').resolve():
            raise HTTPException(400,'Некорректный путь.')
        shutil.rmtree(folder)
    return {'status':'deleted'}


def stop_all():
    global SHUTTING_DOWN
    with LOCK:
        SHUTTING_DOWN = True
        for job_id in list(FUTURES):
            cancel(job_id)
        for process in list(PROCESSES.values()):
            if process.poll() is None:
                terminate_process(process)
    if SERVER:
        SERVER.should_exit = True


@app.post('/api/shutdown')
async def shutdown(request: Request):
    value = await request.json()
    if value.get('instance_id') != INSTANCE_ID:
        raise HTTPException(409,'Это другая запущенная копия программы.')
    if TOKEN and not authorized(request):
        raise HTTPException(401,'Для остановки требуется токен этой копии.')
    threading.Timer(.3,stop_all).start()
    return {'status':'stopping','instance_id':INSTANCE_ID}


for saved in (DATA/'jobs').glob('*/job.json'):
    try:
        job = json.loads(saved.read_text('utf-8'))
        identifier(job['id'])
        job.setdefault('created_at',datetime.fromtimestamp(saved.stat().st_mtime,timezone.utc).isoformat())
        job.setdefault('completed_parts',[])
        job.setdefault('outputs',[])
        if job['status'] in {'running','queued'}:
            job.update(status='error',error='Программа была закрыта. Нажмите «Продолжить», готовые части сохранятся.',message='Обработка прервана; можно продолжить.')
            atomic_json(saved,job)
        JOBS[job['id']] = job
    except (ValueError,KeyError,OSError,HTTPException):
        continue
from url_import import register_url_routes
register_url_routes(app,{'DATA':DATA,'create_job':create_job,'update':update,'cancelled':cancelled,
    'probe':probe,'PROCESSES':PROCESSES,'LOCK':LOCK,'Cancelled':Cancelled,'FFMPEG':FFMPEG,
    'terminate_process':terminate_process,'register_job_type':register_job_type})
app.mount('/',StaticFiles(directory=str(ROOT/'web'),html=True),name='web')


def serve():
    global SERVER
    import uvicorn
    print(f'ClipCanvas: http://{HOST}:{PORT}',flush=True)
    SERVER = uvicorn.Server(uvicorn.Config(app,host=HOST,port=PORT,log_level='warning'))
    try:
        SERVER.run()
    finally:
        stop_all()
        POOL.shutdown(wait=False,cancel_futures=True)
