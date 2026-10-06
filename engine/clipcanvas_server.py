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
from clipcanvas_encoding import available_encoders, encoding_arguments, select_encoder
from clipcanvas_composition import (composition_timeline, remap_after_cuts, timed_items_for_timeline,
                                   normalize_rotation, banner_rotation_filter, banner_overlay_position)
from clipcanvas_asr import SpeechModel, recognize
from clipcanvas_layout import BannerRules, rotated_bounds
from clipcanvas_pro import (MotionPoint, BannerSchedule, AudioSettings, Framing, DeliveryRules,
                            TextOverlay, effective_payload, motion_expression, disk_estimate)

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
JOB_SIDECARS = {'request':'request.json','cues':'cues.json','reviewed_cues':'reviewed-cues.json',
                'overview':'overview.json','changes':'changes.json','project':'automatic-project.json',
                'batch_items':'batch-items.json'}
PROJECT_SUMMARIES: dict[str, tuple] = {}
BANNER_MEDIA_LOCKS: dict[str, threading.Lock] = {}
LOCK = threading.RLock()
SHUTTING_DOWN = False
SERVER = None
SESSIONS: set[str] = set()
ALLOWED_ORIGINS = {f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}'}
if (ROOT/'allowed-origins.json').exists():
    ALLOWED_ORIGINS.update(json.loads((ROOT/'allowed-origins.json').read_text('utf-8')))
app = FastAPI(title='ClipCanvas', version='3.0.0', docs_url=None, redoc_url=None)


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
    rotation: float = Field(default=0, ge=-360, le=360)
    @model_validator(mode='after')
    def angle(self):
        self.rotation = normalize_rotation(self.rotation)
        return self


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


class ChromaKey(Strict):
    enabled: bool = False
    color: str = Field(default='#00ff00', pattern=r'^#[0-9a-fA-F]{6}$')
    similarity: float = Field(default=.15, ge=.01, le=1)
    blend: float = Field(default=.05, ge=0, le=1)


class Banner(Strict):
    id: str | None = Field(default=None, max_length=200)
    name: str = Field(default='Баннер', max_length=200)
    enabled: bool = False
    start: float = Field(default=0, ge=0)
    end: float = Field(default=10, ge=0)
    asset_id: str | None = None
    timebase: Literal['source', 'output'] = 'source'
    loop: bool = True
    offset: float = Field(default=0, ge=0, le=86400)
    volume: float = Field(default=0, ge=0, le=1)
    speed: float = Field(default=1, ge=.25, le=4)
    chromakey: ChromaKey = Field(default_factory=ChromaKey)
    geometry: Geometry = Field(default_factory=lambda: Geometry(x=0, y=0, w=16, h=16))
    instructions: str = Field(default='', max_length=4000)
    layout_rules: BannerRules = Field(default_factory=BannerRules)
    schedule: BannerSchedule = Field(default_factory=BannerSchedule)
    opacity: float = Field(default=1, ge=0, le=1)
    fade_in: float = Field(default=0, ge=0, le=10)
    fade_out: float = Field(default=0, ge=0, le=10)
    locked: bool = False
    lock_aspect: bool = True
    motion: list[MotionPoint] = Field(default_factory=list, max_length=100)
    @model_validator(mode='after')
    def interval(self):
        self.motion.sort(key=lambda point: point.time)
        if len({point.time for point in self.motion}) != len(self.motion):
            raise ValueError('Ключевые кадры баннера должны иметь разные времена.')
        if self.enabled and (not self.asset_id or self.end <= self.start):
            raise ValueError('Проверьте файл и время показа баннера.')
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


class CaptionAppearance(Strict):
    font: str | None = Field(default=None, min_length=1, max_length=100, pattern=r'^[^,\r\n{}\\]+$')
    size: int | None = Field(default=None, ge=16, le=160)
    color: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$')
    bold: bool | None = None
    italic: bool | None = None
    x: float | None = Field(default=None, ge=0, le=100)
    y: float | None = Field(default=None, ge=0, le=100)
    width: float | None = Field(default=None, ge=10, le=100)
    align: Literal['left', 'center', 'right'] | None = None
    line_spacing: float | None = Field(default=None, ge=.8, le=3)
    outline: float | None = Field(default=None, ge=0, le=12)
    outline_color: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$')
    shadow: float | None = Field(default=None, ge=0, le=20)
    shadow_color: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$')
    background: bool | None = None
    background_color: str | None = Field(default=None, pattern=r'^#[0-9a-fA-F]{6}$')
    background_opacity: float | None = Field(default=None, ge=0, le=1)


class Cue(Strict):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=2000)
    words: list[Word] = Field(default_factory=list, max_length=1000)
    timing_quality: str = 'phrase'
    appearance: CaptionAppearance | None = None
    render_lines: list[str] | None = Field(default=None, max_length=100)
    render_font_size: float | None = Field(default=None, ge=1, le=640)
    render_y_offset: float | None = Field(default=None, ge=-160, le=160)
    @model_validator(mode='after')
    def interval(self):
        if self.end <= self.start:
            raise ValueError('Конец субтитра должен быть позже начала.')
        for word in self.words:
            if word.start < self.start-.02 or word.end > self.end+.02:
                raise ValueError('Время слова выходит за границы фразы.')
        if self.render_lines is not None and (not self.render_lines or any(len(line)>2000 for line in self.render_lines)):
            raise ValueError('Проверьте строки переноса субтитра.')
        return self


class Subtitles(Strict):
    enabled: bool = False
    size: int = Field(default=52, ge=16, le=160)
    color: str = Field(default='#ffffff', pattern=r'^#[0-9a-fA-F]{6}$')
    y: float = Field(default=84, ge=0, le=100)
    x: float = Field(default=50, ge=0, le=100)
    width: float = Field(default=88, ge=10, le=100)
    font: str | None = Field(default=None, min_length=1, max_length=100, pattern=r'^[^,\r\n{}\\]+$')
    bold: bool | None = None
    italic: bool = False
    align: Literal['left', 'center', 'right'] = 'center'
    line_spacing: float = Field(default=1.15, ge=.8, le=3)
    style: Literal['clean', 'word', 'cinema'] = 'clean'
    outline: float = Field(default=2, ge=0, le=12)
    outline_color: str = Field(default='#000000', pattern=r'^#[0-9a-fA-F]{6}$')
    shadow: float = Field(default=0, ge=0, le=20)
    shadow_color: str = Field(default='#000000', pattern=r'^#[0-9a-fA-F]{6}$')
    background: bool = False
    background_color: str = Field(default='#000000', pattern=r'^#[0-9a-fA-F]{6}$')
    background_opacity: float = Field(default=.5, ge=0, le=1)
    highlight_color: str = Field(default='#a3e635', pattern=r'^#[0-9a-fA-F]{6}$')
    cues: list[Cue] = Field(default_factory=list, max_length=100000)
    @model_validator(mode='after')
    def normalize(self):
        self.cues = [Cue.model_validate(c) for c in normalize_cues([x.model_dump() for x in self.cues])]
        return self


class ClipOverride(Strict):
    video: Geometry | None = None
    banners: list[Banner] | None = Field(default=None, max_length=100)
    subtitles: CaptionAppearance | None = None
    subtitles_enabled: bool | None = None
    advertising_enabled: bool | None = None
    audio: AudioSettings | None = None
    framing: Framing | None = None
    text_layers: list[TextOverlay] | None = Field(default=None, max_length=50)


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
    schema_version: int = Field(default=2, ge=1, le=2)
    source_id: str
    audio_stream_index: int | None = Field(default=None, ge=0)
    canvas: Canvas
    video: Geometry
    background: Background
    banner: Banner = Field(default_factory=Banner)
    banners: list[Banner] = Field(default_factory=list, max_length=100)
    subtitles: Subtitles = Field(default_factory=Subtitles)
    advertising: Advertising = Field(default_factory=Advertising)
    parts: list[Part] = Field(min_length=1, max_length=1000)
    cuts: list[Part] = Field(default_factory=list, max_length=1000)
    quality: Literal['standard', 'high'] = 'standard'
    encoder: Literal['auto', 'cpu', 'nvidia'] = 'auto'
    fps: Literal[24, 30, 60] = 30
    zip: bool = False
    clip_overrides: dict[str, ClipOverride] = Field(default_factory=dict, max_length=1000)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    framing: Framing = Field(default_factory=Framing)
    delivery: DeliveryRules = Field(default_factory=DeliveryRules)
    text_layers: list[TextOverlay] = Field(default_factory=list, max_length=50)
    @model_validator(mode='after')
    def bounds(self):
        if any(not key.isdecimal() or not 1 <= int(key) <= 1000 for key in self.clip_overrides):
            raise ValueError('Настройки частей должны использовать номера от 1 до 1000.')
        if self.video.rotation or (self.advertising.geometry and self.advertising.geometry.rotation):
            raise ValueError('Поворот доступен для баннеров; поворот видео и рекламного ролика пока не поддерживается.')
        layers = [self.video]
        if self.banner.enabled:
            layers.append(self.banner.geometry)
        layers.extend(banner.geometry for banner in self.banners if banner.enabled)
        if self.advertising.enabled and self.advertising.geometry:
            layers.append(self.advertising.geometry)
        for g in layers:
            if g.w > self.canvas.width*4 or g.h > self.canvas.height*4:
                raise ValueError('Слой слишком велик для выбранного холста.')
            visible = rotated_bounds(g.model_dump())
            if (visible['x']+visible['w'] <= 0 or visible['y']+visible['h'] <= 0
                    or visible['x'] >= self.canvas.width or visible['y'] >= self.canvas.height):
                raise ValueError('Слой должен находиться хотя бы частично внутри холста.')
        return self


class Source(Strict):
    source_id: str
    audio_stream_index: int | None = Field(default=None, ge=0)


class Transcribe(Source):
    language: Literal['auto', 'ru', 'en', 'es', 'de', 'fr', 'uk'] = 'ru'
    model: SpeechModel = 'base'


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
    if result['kind'] == 'banner' and not result.get('media_type'):
        with LOCK:
            result.update(banner_metadata(media))
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
    result = {'status':'ok', 'app':'ClipCanvas', 'version':'3.0.0', 'ffmpeg':Path(FFMPEG).exists(),
            'authenticated':not REMOTE or bool(request and authorized(request)),
            'root':str(ROOT), 'instance_id':INSTANCE_ID, 'pid':os.getpid(),
            'capabilities':{'auth_required':REMOTE, 'audio_tracks':True, 'embedded_subtitles':True,
                'bitmap_ocr':False, 'advertising':True, 'projects':True, 'retry':True,
                'max_parts':1000, 'subtitle_styles':['clean','word','cinema'], 'sample_max_source_duration':None,
                'composition_version':2, 'multiple_banners':True, 'non_destructive_cuts':True,
                'render_encoders':list(available_encoders(FFMPEG)), 'batch':True, 'delivery_profiles':True,
                'banner_schedules':True, 'clip_overrides':True}}
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


def banner_metadata(path):
    """Detect actual motion, including animated GIF/APNG, without flattening it."""
    with av.open(str(path)) as container:
        videos = list(container.streams.video)
        if not videos:
            raise ValueError('В файле баннера нет изображения или видео.')
        stream = videos[0]
        formats = set(container.format.name.split(','))
        pixel_format = stream.codec_context.format.name if stream.codec_context.format else ''
        has_alpha = (bool(formats & {'gif','apng'}) or pixel_format in {'pal8','rgba','bgra','argb','abgr'}
                     or pixel_format.startswith(('yuva','gbrap')) or stream.metadata.get('alpha_mode') == '1')
        video_codec = stream.codec_context.name
        still = any(name.endswith('_pipe') for name in formats) or bool(formats & {'image2', 'image2pipe', 'ico'})
        if formats & {'gif', 'apng'}:
            frames = container.decode(video=0)
            next(frames, None)
            still = next(frames, None) is None
        width, height = stream.codec_context.width, stream.codec_context.height
        if not width or not height:
            frame = next(container.decode(video=0), None)
            if frame is None:
                raise ValueError('Не удалось прочитать баннер.')
            width, height = frame.width, frame.height
    if still:
        return {'media_type': 'image', 'width': width, 'height': height, 'audio': False}
    return {**probe(path), 'media_type': 'video', 'has_alpha': has_alpha, 'video_codec': video_codec}


def banner_decoder_options(source):
    # FFmpeg's native VP9 decoder drops WebM alpha; libvpx exposes its alpha plane.
    return ['-c:v','libvpx-vp9'] if source.get('has_alpha') and source.get('video_codec') == 'vp9' else []


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
                if kind == 'banner' and total > 512*1024*1024:
                    raise ValueError('Видеобаннер должен быть не больше 512 МБ; изображение — не больше 30 МБ.')
                if shutil.disk_usage(DATA).free < len(chunk)+16*1024*1024:
                    raise ValueError('Недостаточно свободного места для загрузки.')
                digest.update(chunk)
                output.write(chunk)
        if not total:
            raise ValueError('Файл пуст.')
        if kind in {'video', 'advert'}:
            meta = await asyncio.to_thread(probe, path)
        else:
            meta = await asyncio.to_thread(banner_metadata, path)
            if meta['media_type'] == 'image':
                if total > 30*1024*1024:
                    raise ValueError('Изображение баннера должно быть не больше 30 МБ.')
                normalized = folder/'banner.png'
                completed = await asyncio.to_thread(subprocess.run,
                    [FFMPEG,'-y','-v','error','-i',str(path),'-frames:v','1',str(normalized)], capture_output=True, timeout=30)
                if completed.returncode:
                    raise ValueError('Изображение не удалось прочитать.')
                if normalized != path:
                    path.unlink()
                path = normalized
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


class BannerPreview(Strict):
    asset_id: str
    chromakey: ChromaKey = Field(default_factory=ChromaKey)
    time: float = Field(default=0, ge=0, le=86400)


@app.post('/api/banners/preview')
def banner_preview(request: BannerPreview):
    source = asset(request.asset_id, 'banner')
    motion = source.get('media_type') == 'video'
    if not motion and not request.chromakey.enabled:
        return {'url':f'/api/media/{request.asset_id}', 'cached':True}
    moment = round(min(request.time, max(0, source.get('duration', 0)-1/(source.get('fps') or 30))), 3) if motion else 0
    signature = hashlib.sha256(json.dumps({'source':source['sha256'], 'key':request.chromakey.model_dump(),
                                          'time':moment, 'version':3},sort_keys=True).encode()).hexdigest()
    directory = DATA/'cache'/'banners'
    directory.mkdir(exist_ok=True)
    path = directory/(signature+'.png')
    cached = path.is_file()
    if not cached:
        temporary = directory/(signature+'.'+uuid.uuid4().hex+'.png')
        try:
            graph = ['[0:v:0]scale=iw*sar:ih,setsar=1[posterraw]']
            graph += banner_key_filters('posterraw',request.chromakey,'keyed','preview') if request.chromakey.enabled else ['[posterraw]format=rgba[keyed]']
            result = subprocess.run([FFMPEG,'-y','-hide_banner','-loglevel','error',
                *(['-ss',str(moment)] if motion else []), *banner_decoder_options(source), '-i',str(source['path']),
                '-filter_complex',';'.join(graph), '-map','[keyed]','-frames:v','1',str(temporary)], capture_output=True, timeout=30,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:
                raise HTTPException(400,'Не удалось подготовить кадр баннера. Проверьте файл и параметры цвета.')
            os.replace(temporary,path)
        except subprocess.TimeoutExpired as error:
            raise HTTPException(408,'Подготовка баннера заняла слишком много времени. Уменьшите размер изображения.') from error
        finally:
            temporary.unlink(missing_ok=True)
    return {'url':f'/api/banners/preview/{signature}.png', 'cached':cached, 'time':moment}


@app.get('/api/banners/media/{asset_id}')
def banner_media(asset_id: str):
    source = asset(asset_id, 'banner')
    if source.get('media_type') != 'video':
        return FileResponse(source['path'])
    alpha = source.get('has_alpha', False)
    extension = '.webm' if alpha else '.mp4'
    signature = hashlib.sha256(('banner-browser-v3:'+source['sha256']).encode()).hexdigest()
    directory = DATA/'cache'/'banner-media'
    directory.mkdir(exist_ok=True)
    path = directory/(signature+extension)
    with LOCK:
        conversion_lock = BANNER_MEDIA_LOCKS.setdefault(signature, threading.Lock())
    # Serialize just this asset; rendering/cancellation keeps its own queue and lock.
    if not conversion_lock.acquire(timeout=185):
        raise HTTPException(408, 'Видеобаннер ещё подготавливается. Повторите попытку.')
    try:
        if not path.is_file():
            temporary = directory/(signature+'.'+uuid.uuid4().hex+extension)
            try:
                audio = source.get('recommended_audio_stream_index')
                codecs = (['-c:v','libvpx-vp9','-lossless','1','-deadline','realtime','-cpu-used','4',
                           '-pix_fmt','yuva420p','-auto-alt-ref','0','-c:a','libopus'] if alpha else
                          ['-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart'])
                process = subprocess.Popen([FFMPEG,'-y','-hide_banner','-loglevel','error',*banner_decoder_options(source),'-i',str(source['path']),
                    '-map',f'0:{source["video_stream_index"]}', *(['-map',f'0:{audio}'] if audio is not None else ['-an']),
                    '-vf','scale=iw*sar:ih,setsar=1,scale=w=min(1280\\,iw):h=min(1280\\,ih):force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1',
                    '-t',str(source['duration']),*codecs,'-ac','2','-ar','48000','-b:a','160k',str(temporary)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                deadline = time.monotonic()+180
                try:
                    while process.poll() is None:
                        if SHUTTING_DOWN:
                            raise HTTPException(503, 'Программа завершает работу.')
                        if time.monotonic() >= deadline:
                            raise HTTPException(408, 'Подготовка видеобаннера заняла слишком много времени. Используйте более короткое видео.')
                        time.sleep(.1)
                    if process.returncode or not temporary.is_file():
                        raise HTTPException(400, 'Не удалось подготовить видеобаннер для браузера.')
                    os.replace(temporary, path)
                finally:
                    if process.poll() is None:
                        terminate_process(process)
            finally:
                temporary.unlink(missing_ok=True)
    finally:
        conversion_lock.release()
    return FileResponse(path, media_type='video/webm' if alpha else 'video/mp4')


def banner_audio_file(job_id, source):
    """Loop a separate audio stream exactly at video duration, even if source audio is longer."""
    selected = source['recommended_audio_stream_index']
    signature = hashlib.sha256(f'banner-audio-v1:{source["sha256"]}:{selected}:{source["duration"]}'.encode()).hexdigest()
    directory = DATA/'cache'/'banner-audio'
    directory.mkdir(exist_ok=True)
    path = directory/(signature+'.wav')
    with LOCK:
        conversion_lock = BANNER_MEDIA_LOCKS.setdefault(signature, threading.Lock())
    while not conversion_lock.acquire(timeout=.1):
        if cancelled(job_id):
            raise Cancelled()
    try:
        if not path.is_file():
            track = next(t for t in source['audio_streams'] if t['index'] == selected)
            offset = track['start_time']-source.get('video_start_time',0)
            audio_filter = 'asetpts=PTS-STARTPTS'
            if offset > 0:
                audio_filter += f',adelay={round(offset*1000)}:all=1'
            elif offset < 0:
                audio_filter += f',atrim=start={-offset:.6f},asetpts=PTS-STARTPTS'
            audio_filter += f',apad=whole_dur={source["duration"]:.6f},atrim=duration={source["duration"]:.6f}'
            temporary = directory/(signature+'.'+uuid.uuid4().hex+'.wav')
            try:
                run_ffmpeg(job_id,['-i',str(source['path']),'-map',f'0:{selected}','-vn','-af',audio_filter,
                    '-ac','2','-ar','48000','-c:a','pcm_s16le',str(temporary)], source['duration'], share=0, log_name='banner-audio.log')
                os.replace(temporary,path)
            finally:
                temporary.unlink(missing_ok=True)
    finally:
        conversion_lock.release()
    return path


@app.get('/api/banners/preview/{filename}')
def banner_preview_file(filename: str):
    if not re.fullmatch(r'[a-f0-9]{64}\.png',filename):
        raise HTTPException(404,'Предпросмотр баннера не найден.')
    path = DATA/'cache'/'banners'/filename
    if not path.is_file():
        raise HTTPException(404,'Предпросмотр баннера отсутствует. Обновите параметры.')
    return FileResponse(path,media_type='image/png')


def persist(job_id):
    with LOCK:
        directory = DATA/'jobs'/job_id
        job = JOBS[job_id]
        # Large payloads have their own files; progress updates only write small metadata.
        for key, filename in JOB_SIDECARS.items():
            if key in job and not (directory/filename).exists():
                atomic_json(directory/filename, job[key])
        atomic_json(directory/'job.json', {k:v for k,v in job.items() if k not in JOB_SIDECARS})


def update(job_id, **values):
    with LOCK:
        for key, filename in JOB_SIDECARS.items():
            if key in values:
                atomic_json(DATA/'jobs'/job_id/filename, values[key])
        JOBS[job_id].update(values, updated_at=now())
        persist(job_id)


def cancelled(job_id):
    with LOCK:
        job = JOBS[job_id]
        return SHUTTING_DOWN or job.get('cancel_requested', False) or bool(job.get('parent_id') and cancelled(job['parent_id']))


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


def create_job(task, request, kind=None, cached=None, parent_id=None):
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
        if parent_id:
            JOBS[job_id].update(parent_id=identifier(parent_id), internal=True)
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


def inline_job(parent_id, task, request, kind, existing=None, on_created=None):
    """Run a batch stage in the current worker; never enqueue behind its parent."""
    if cancelled(parent_id):
        raise Cancelled()
    with LOCK:
        job_id = existing or uuid.uuid4().hex
        if existing:
            job = JOBS[job_id]
            if job['request'] != request.model_dump():
                raise ValueError('Настройки продолжения не совпадают с сохранённой задачей.')
        else:
            (DATA/'jobs'/job_id).mkdir()
            JOBS[job_id] = {'id':job_id, 'type':kind, 'parent_id':parent_id, 'internal':True,
                'files':[], 'outputs':[], 'completed_parts':[], 'request':request.model_dump(),
                'created_at':now(), 'updated_at':now()}
        update(job_id, parent_id=parent_id, status='running', progress=0, cancel_requested=False, error=None, message='Обработка серии…')
    if on_created:
        on_created(job_id)
    try:
        task(job_id, request)
        if cancelled(job_id):
            raise Cancelled()
        update(job_id, status='done', progress=100, message='Готово')
    except Cancelled:
        update(job_id, status='cancelled', message='Остановлено; можно продолжить.')
        raise
    except Exception as error:
        update(job_id, status='error', error=str(error), message=str(error))
        raise
    finally:
        clean_job_temporary_files(job_id)
    return get_job(job_id)


def clean_job_temporary_files(job_id):
    directory = DATA/'jobs'/identifier(job_id)
    for pattern in ('segment-*.mp4','*.freeze.png','*.ass','concat-*.txt','*.zip.tmp','speech.wav',
                    'features.tmp','wave.tmp','ai-*.tmp','ai-frame-*.jpg'):
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


class FFmpegError(RuntimeError):
    pass


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
            raise FFmpegError(f'Ошибка FFmpeg ({log_name}). '+detail)


def even(n):
    return max(16,round(n/2)*2)


def layer_filter(label, geometry, out):
    w,h = even(geometry.w),even(geometry.h)
    if geometry.fit == 'stretch':
        return f'[{label}]scale={w}:{h},setsar=1,format=rgba[{out}]'
    if geometry.fit == 'cover':
        return f'[{label}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=rgba[{out}]'
    return f'[{label}]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1,format=rgba,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x00000000[{out}]'


def video_layer_filter(label, geometry, framing, start, out):
    if geometry.fit != 'cover' or framing.mode == 'manual':
        return layer_filter(label, geometry, out)
    points = [point.model_dump() for point in framing.points]
    clock = f'(t+{start:.6f})'
    fx = motion_expression(points, 'x', framing.x, clock, framing.interpolation == 'hold')
    fy = motion_expression(points, 'y', framing.y, clock, framing.interpolation == 'hold')
    w, h = even(geometry.w), even(geometry.h)
    return (f'[{label}]scale={w}:{h}:force_original_aspect_ratio=increase,'
            f"crop={w}:{h}:x='max(0,min(iw-ow,iw*({fx})-ow/2))':"
            f"y='max(0,min(ih-oh,ih*({fy})-oh/2))',setsar=1,format=rgba[{out}]")


def moving_banner_filter(label, banner, origin, out):
    points = [point.model_dump() for point in banner.motion]
    clock = f'(t+{origin-banner.start:.6f})'
    scale = motion_expression(points, 'scale', 1, clock)
    filters = [f'[{label}]scale=w=\'max(2,trunc(iw*({scale})/2)*2)\':h=\'max(2,trunc(ih*({scale})/2)*2)\':eval=frame'] if points else [f'[{label}]null']
    if banner.opacity != 1 or points or banner.fade_in or banner.fade_out:
        alpha_clock = f'(T+{origin-banner.start:.6f})'
        alpha = motion_expression(points, 'opacity', 1, alpha_clock)
        alpha = f'{banner.opacity:.6f}*({alpha})'
        if banner.fade_in:
            alpha += f'*min(1,max(0,({alpha_clock})/{banner.fade_in:.6f}))'
        if banner.fade_out:
            alpha += f'*min(1,max(0,({banner.end-banner.start:.6f}-({alpha_clock}))/{banner.fade_out:.6f}))'
        filters.append(f"format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*({alpha})'")
    return ','.join(filters)+f'[{out}]'


def write_text_ass(path, request, segment, is_ad):
    events = []
    for text in request.text_layers:
        if not text.enabled or (is_ad and text.timebase == 'source'):
            continue
        origin = segment['output_start'] if text.timebase == 'output' else segment['start']
        a, b = max(0, text.start-origin), min(segment['duration'], text.end-origin)
        if b <= a:
            continue
        tags = (r'{\an5\pos('+f'{request.canvas.width*text.x/100:.3f},{request.canvas.height*text.y/100:.3f}'+
                r')\fn'+text.font+r'\fs'+str(text.size)+r'\b'+('1' if text.bold else '0')+
                r'\1c'+ass_color(text.color)+r'&\bord2\shad0}')
        if text.background:
            tags = tags[:-1]+r'\3c&H000000&\bord6}'
        events.append(f'Dialogue: 2,{ass_time(a)},{ass_time(b)},Default,,0,0,0,,{tags}{ass_text(text.text)}\n')
    if not events:
        return False
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {request.canvas.width}
PlayResY: {request.canvas.height}
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,5,20,20,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    path.write_text(header+''.join(events), 'utf-8-sig')
    return True


def banner_key_filters(label, key, out, prefix):
    """Same alpha-preserving key for cached preview and final composition."""
    return [
        f'[{label}]format=rgba,split=3[{prefix}image][{prefix}original][{prefix}color]',
        f'[{prefix}original]alphaextract[{prefix}alpha]',
        f'[{prefix}color]colorkey=0x{key.color.lstrip("#")}:{key.similarity}:{key.blend},alphaextract[{prefix}mask]',
        f'[{prefix}alpha][{prefix}mask]blend=all_mode=multiply[{prefix}combined]',
        f'[{prefix}image][{prefix}combined]alphamerge[{out}]']


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


def write_ass_legacy(path, request, part):
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


def caption_appearance(sub, cue):
    values = {name: getattr(sub, name) for name in CaptionAppearance.model_fields}
    values['font'] = values['font'] or ('Georgia' if sub.style == 'cinema' else 'Arial')
    if values['bold'] is None:
        values['bold'] = sub.style != 'cinema'
    if cue.appearance:
        values.update(cue.appearance.model_dump(exclude_none=True))
    return values


def write_ass(path, request, part):
    """Render explicit preview line breaks and canonical canvas coordinates.

    Old projects keep libass' original automatic wrapping until the editor has
    supplied measured lines. Explicit lines avoid browser/libass wrap drift.
    """
    c, sub = request.canvas, request.subtitles
    extended = {'font','bold','italic','x','width','align','line_spacing','outline_color',
                'shadow','shadow_color','background_color','background_opacity'}
    defaults = Subtitles()
    if not any(cue.render_lines or cue.appearance for cue in sub.cues) and all(getattr(sub,key)==getattr(defaults,key) for key in extended):
        return write_ass_legacy(path, request, part)
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {c.width}
PlayResY: {c.height}
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,52,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,5,0,0,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    events = []
    for cue in sub.cues:
        start, end = max(cue.start, part.start), min(cue.end, part.end)
        if start >= end or not cue.text.strip():
            continue
        style = caption_appearance(sub, cue)
        lines = cue.render_lines or cue.text.splitlines()
        # Never render stale cached wrapping after a text edit.
        if re.sub(r'\s+', '', ''.join(lines)) != re.sub(r'\s+', '', cue.text):
            lines = cue.text.splitlines()
        size, line_height = style['size'], style['size']*style['line_spacing']
        x, y, width = c.width*style['x']/100, c.height*style['y']/100, c.width*style['width']/100
        alignment = {'left':4, 'center':5, 'right':6}[style['align']]
        text_x = x + ({'left':-1, 'center':0, 'right':1}[style['align']])*width/2
        if style['background'] and style['background_opacity'] > 0:
            pad = size*.18
            left, top, box_width, box_height = x-width/2-pad, y-len(lines)*line_height/2-pad, width+2*pad, len(lines)*line_height+2*pad
            alpha = round((1-style['background_opacity'])*255)
            tags = r'{\an7\pos('+f'{left:.3f},{top:.3f}'+r')\p1\bord0\shad0\1c'+ass_color(style['background_color'])+r'&\1a&H'+f'{alpha:02X}'+r'&}'
            drawing = f'm 0 0 l {box_width:.3f} 0 {box_width:.3f} {box_height:.3f} 0 {box_height:.3f}'
            events.append(f'Dialogue: 0,{ass_time(start-part.start)},{ass_time(end-part.start)},Default,,0,0,0,,{tags}{drawing}\n')
        for index, line in enumerate(lines):
            # CSS font-size is an em size; ASS uses the font's ascent+descent.
            # Canvas supplies measured conversion and middle-baseline correction
            # alongside explicit lines so both renderers place the same glyphs.
            line_y = y+(index-(len(lines)-1)/2)*line_height+(cue.render_y_offset or 0)
            font_size = cue.render_font_size if cue.render_font_size is not None else size
            tags = (r'{\an'+str(alignment)+r'\q2\pos('+f'{text_x:.3f},{line_y:.3f}'+r')\fn'+style['font']+
                    r'\fs'+str(font_size)+r'\b'+('1' if style['bold'] else '0')+r'\i'+('1' if style['italic'] else '0')+
                    r'\1c'+ass_color(style['color'])+r'&\3c'+ass_color(style['outline_color'])+r'&\4c'+ass_color(style['shadow_color'])+
                    r'&\bord'+str(style['outline'])+r'\shad'+str(style['shadow'])+'}')
            line_cue = cue.model_copy(update={'text': line, 'words': [word for word in cue.words if word.text.strip() in line]})
            pieces = word_caption_events(line_cue,start,end,style['color'],sub.highlight_color) if sub.style == 'word' else [(start,end,ass_text(line))]
            for a, b, text in pieces:
                events.append(f'Dialogue: 1,{ass_time(a-part.start)},{ass_time(b-part.start)},Default,,0,0,0,,{tags}{text}\n')
    path.write_text(header+''.join(events), 'utf-8-sig')


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


def audio_tempo_filter(speed):
    """Chain atempo within its accurate 0.5–2 range; preserve the audio pitch."""
    if speed == 1:
        return ''
    factors = []
    while speed < .5:
        factors.append(.5)
        speed /= .5
    while speed > 2:
        factors.append(2)
        speed /= 2
    factors.append(speed)
    return ','.join(f'atempo={factor:.6f}' for factor in factors)+','


def render_segment(job_id,request,source,segment,name,progress_offset,progress_share,is_ad=False,paused_source=None,encoder='cpu'):
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
    banners = []
    for banner in [request.banner, *request.banners]:
        if not banner.enabled or (is_ad and banner.timebase == 'source'):
            continue
        origin = segment['output_start'] if banner.timebase == 'output' else start
        a, z = max(0, banner.start-origin), min(duration, banner.end-origin)
        if z <= a:
            continue
        record = asset(banner.asset_id, 'banner')
        motion = record.get('media_type') == 'video'
        position = 0
        if motion:
            elapsed = banner.offset+max(0, origin-banner.start)*banner.speed
            if banner.loop:
                position = elapsed % record['duration']
            else:
                if elapsed >= record['duration']:
                    continue
                position = elapsed
                z = min(z, a+(record['duration']-position)/banner.speed)
            media_seek = position+max(0,record.get('video_start_time',0)-record.get('container_start_time',0))
            args += [*(['-stream_loop','-1'] if banner.loop else []),'-ss',f'{media_seek:.6f}',
                     *banner_decoder_options(record), '-i',str(record['path'])]
        else:
            args += ['-loop','1','-framerate',str(request.fps),'-i',str(record['path'])]
        video_input, audio_input = next_input, None
        next_input += 1
        if motion and banner.volume > 0 and record.get('recommended_audio_stream_index') is not None:
            audio_path = banner_audio_file(job_id, record)
            audio_input = next_input
            next_input += 1
            args += [*(['-stream_loop','-1'] if banner.loop else []),'-ss',f'{position:.6f}','-i',str(audio_path)]
        banners.append((video_input, banner, a, z, record, audio_input))
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
        from clipcanvas_pro import interpolate
        focus_time = segment.get('source_position', start)
        focus_points = [point.model_dump() for point in request.framing.points]
        frozen_framing = request.framing.model_copy(update={'points':[],
            'x':interpolate(focus_points, focus_time, 'x', request.framing.x, request.framing.interpolation == 'hold'),
            'y':interpolate(focus_points, focus_time, 'y', request.framing.y, request.framing.interpolation == 'hold')})
        filters += [video_layer_filter('fmain',original_geometry,frozen_framing,focus_time,'frozenforeground'),
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
    filters += [video_layer_filter('main',geometry,request.framing,start,'foreground') if not is_ad else layer_filter('main',geometry,'foreground'),f'[bg][foreground]overlay=x={round(geometry.x)}:y={round(geometry.y)}:shortest=1[composite]']
    last = 'composite'
    banner_sounds = []
    for number, (input_index, banner, a, z, record, audio_input) in enumerate(banners):
        b, key = banner.geometry, banner.chromakey
        label, composed = f'banner{number}', f'bannercomposite{number}'
        raw = f'bannertimed{number}'
        filters.append(f'[{input_index}:v:0]trim=duration={(z-a)*banner.speed:.6f},setpts=(PTS-STARTPTS)/{banner.speed:.6f}+{a:.6f}/TB,'
                       f'scale=iw*sar:ih,setsar=1[{raw}]')
        if key.enabled:
            # colorkey replaces alpha, so multiply its mask by the original
            # alpha channel before merging. Transparent uploads stay transparent.
            keyed = f'bannerkey{number}'
            filters.extend(banner_key_filters(raw,key,keyed,f'bkey{number}'))
            raw = keyed
        fitted = f'bannerfitted{number}'
        moved = f'bannermoved{number}'
        origin = segment['output_start'] if banner.timebase == 'output' else start
        points = [point.model_dump() for point in banner.motion]
        clock = f'(t+{origin-banner.start:.6f})'
        scale = motion_expression(points, 'scale', 1, clock)
        x = motion_expression(points, 'x', b.x, clock)+f'+{b.w:.6f}*({scale})/2-overlay_w/2'
        y = motion_expression(points, 'y', b.y, clock)+f'+{b.h:.6f}*({scale})/2-overlay_h/2'
        filters += [layer_filter(raw,b,fitted), moving_banner_filter(fitted,banner,origin,moved), banner_rotation_filter(moved,b.rotation,label),
            f"[{last}][{label}]overlay=x='{x}':y='{y}':enable='gte(t,{a:.6f})*lt(t,{z:.6f})':eof_action=pass:repeatlast=0:shortest=0[{composed}]"]
        last = composed
        if audio_input is not None:
            sound = f'bannersound{number}'
            tempo = audio_tempo_filter(banner.speed)
            filters.append(f'[{audio_input}:a:0]asetpts=PTS-STARTPTS,aresample=48000,'+tempo+
                f'atrim=duration={z-a:.6f},volume={banner.volume:.6f},'
                f'adelay={round(a*1000)}:all=1,apad=whole_dur={duration:.6f},'
                f'atrim=duration={duration:.6f}[{sound}]')
            banner_sounds.append(sound)
    if not is_ad and request.subtitles.enabled and request.subtitles.cues:
        ass_name = name.replace('.mp4','.ass')
        write_ass(directory/ass_name,request,Part.model_construct(start=start,end=segment['end']))
        # Native DirectWrite/fontconfig finds fonts without scanning every Windows .fon.
        filters += [f'[{last}]ass=filename={ass_name}[captioned]']
        last = 'captioned'
    text_name = name.replace('.mp4', '.text.ass')
    if write_text_ass(directory/text_name, request, segment, is_ad):
        filters.append(f'[{last}]ass=filename={text_name}[texted]')
        last = 'texted'
    # Pad after conversion to the output frame rate. Padding before fps can lose
    # the final resampled frames when a seek reaches a lower-fps source's EOF.
    filters += [f'[{last}]fps={request.fps}:start_time=0,tpad=stop_mode=clone:stop_duration={duration:.6f},'
                f'trim=duration={duration:.6f},setpts=N/({request.fps}*TB),setsar=1,format=yuv420p[out]']
    delay = 0
    if selected is not None:
        track = next(t for t in source['audio_streams'] if t['index']==selected)
        delay = max(0,track['start_time']-source.get('video_start_time',0)-start)/speed
    audio_filter = f'[{audio_label}]asetpts=PTS-STARTPTS,aresample=48000'
    if speed != 1:
        audio_filter += f',atempo={speed}'
    if delay > .001:
        audio_filter += f',adelay={round(delay*1000)}:all=1'
    volume = request.audio.advert_volume if is_ad else request.audio.source_volume
    volume_expression = f'{volume:.6f}'
    if not is_ad and request.audio.ducking:
        active = '+'.join(f'gte(t,{a:.6f})*lt(t,{z:.6f})' for _, banner, a, z, _, audio_input in banners if audio_input is not None)
        if active:
            volume_expression += f'*if(gt({active},0),{request.audio.duck_volume:.6f},1)'
    audio_filter += f",volume='{volume_expression}':eval=frame"
    audio_filter += f',apad=whole_dur={duration:.6f},atrim=duration={duration:.6f}['+('mainsound' if banner_sounds else 'sound')+']'
    filters.append(audio_filter)
    if banner_sounds:
        filters.append('[mainsound]'+''.join(f'[{sound}]' for sound in banner_sounds)+
                       f'amix=inputs={len(banner_sounds)+1}:duration=first:dropout_transition=0:normalize=0[sound]')
    audio_output = 'sound'
    if request.audio.fade:
        fade, out_start = request.audio.fade, segment['output_start']
        total = segment.get('clip_duration', duration)
        envelope = f'min(1,max(0,(t+{out_start:.6f})/{fade:.6f}))*min(1,max(0,({total-out_start:.6f}-t)/{fade:.6f}))'
        filters.append(f"[sound]volume='{envelope}':eval=frame[fadedsound]")
        audio_output = 'fadedsound'
    if request.audio.limiter:
        filters.append(f'[{audio_output}]alimiter=limit=0.95:level=false:latency=true,atrim=duration={duration:.6f}[limitedsound]')
        audio_output = 'limitedsound'
    args += ['-filter_complex_threads',str(min(8,os.cpu_count() or 4)),'-filter_complex',';'.join(filters),'-map','[out]','-map',f'[{audio_output}]',
             '-t',f'{duration:.6f}','-r',str(request.fps),'-fps_mode','cfr',*encoding_arguments(encoder,request.quality),
             '-c:a','aac','-ac','2','-ar','48000','-b:a','192k','-movflags','+faststart',name]
    run_ffmpeg(job_id,args,duration,progress_offset,progress_share,name.replace('.mp4','.log'))
    metadata = output_metadata(directory/name)
    if metadata.get('frames',0) < max(1,math.floor(duration*request.fps)-1):
        raise RuntimeError(f'Неполный видеосегмент {name}: проверьте исходную видеодорожку.')
    if freeze_file:
        freeze_file.unlink(missing_ok=True)


def resolved_request(request, part, index, ad=None):
    payload, plan = effective_payload(request.model_dump(), part.model_dump(), index, ad['duration'] if ad else 0)
    return Render.model_validate(payload), plan


def validate_render(request):
    try:
        select_encoder(request.encoder, available_encoders(FFMPEG) if request.encoder != 'cpu' else ('cpu',))
    except ValueError as error:
        raise HTTPException(400,str(error)) from error
    source = asset(request.source_id,'video')
    audio_index(source,request.audio_stream_index)
    if any(p.end > source['duration']+.05 for p in request.parts):
        raise HTTPException(400,'Конец отрезка выходит за длительность видеодорожки.')
    if any(cut.end > source['duration']+.05 for cut in request.cuts):
        raise HTTPException(400,'Удаляемый участок выходит за длительность видеодорожки.')
    for banner in [request.banner, *request.banners]:
        if banner.enabled:
            asset(banner.asset_id,'banner')
            if banner is not request.banner and banner.schedule.mode == 'source' and banner.timebase == 'source' and banner.end > source['duration']+.05:
                raise HTTPException(400,'Время баннера выходит за длительность исходного видео.')
    ad_needed = request.advertising.enabled or any(override.advertising_enabled for override in request.clip_overrides.values())
    ad = asset(request.advertising.asset_id,'advert') if ad_needed else None
    if ad:
        audio_index(ad,request.advertising.audio_stream_index)
    seconds, resolved = 0, []
    for index, part in enumerate(request.parts, 1):
        try:
            config, plan = resolved_request(request, part, index, ad)
            for banner in config.banners:
                asset(banner.asset_id, 'banner')
            duration = sum(segment['duration'] for segment in plan)
            seconds += duration
            resolved.append((config.model_dump(), plan))
            if config.delivery.max_duration and duration > config.delivery.max_duration+.001:
                raise ValueError(f'Часть {index} превышает лимит итоговой длительности. Пересчитайте нарезку.')
        except ValueError as error:
            raise HTTPException(400,str(error)) from error
    from clipcanvas_pro import delivery_issues
    if any(request.delivery.model_dump()[key] for key in ('require_banner','require_subtitles','min_banner_width','min_banner_height','min_banner_seconds')):
        errors = [issue['message'] for issue in delivery_issues(request.model_dump(), resolved, asset) if issue['severity'] == 'error']
        if errors:
            raise HTTPException(400, 'Не выполнены требования: '+' '.join(errors[:5]))
    if shutil.disk_usage(DATA).free < disk_estimate(request.model_dump(), seconds)['required_bytes']:
        raise HTTPException(400,'Недостаточно свободного места для экспорта.')
    return source,ad


def render_timeline(request,part,ad):
    middle = request.advertising.placement=='middle'
    return composition_timeline(part.start,part.end,[cut.model_dump() for cut in request.cuts],
                    ad['duration']/request.advertising.speed if ad else 0, request.advertising.start,
                    request.advertising.repeat,request.advertising.interval,middle)


@app.post('/api/composition/resolve')
def resolve_composition(request: Render):
    source, ad = validate_render(request)
    cues = [cue.model_dump() for cue in request.subtitles.cues]
    banners = [banner.model_dump() for banner in [request.banner, *request.banners] if banner.enabled]
    parts = []
    for index, part in enumerate(request.parts, 1):
        config, plan = resolved_request(request, part, index, ad)
        cues = [cue.model_dump() for cue in config.subtitles.cues]
        banners = [banner.model_dump() for banner in config.banners]
        parts.append({'source_start':part.start, 'source_end':part.end,
                      'duration':sum(segment['duration'] for segment in plan), 'timeline':plan,
                      'cues':captions_for_timeline(cues,plan), 'banners':timed_items_for_timeline(banners,plan),
                      'video':config.video.model_dump(), 'framing':config.framing.model_dump(),
                      'text_layers':timed_items_for_timeline([layer.model_dump() for layer in config.text_layers if layer.enabled],plan)})
    return {'schema_version':2, 'parts':parts}


class RemapRequest(Strict):
    items: list[dict] = Field(default_factory=list, max_length=100000)
    cuts: list[Part] = Field(default_factory=list, max_length=1000)


@app.post('/api/composition/remap')
def remap_composition(request: RemapRequest):
    try:
        return {'items':remap_after_cuts(request.items,[cut.model_dump() for cut in request.cuts])}
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(400,'Не удалось согласовать интервалы: '+str(error)) from error


def render_job(job_id,request):
    source,ad = validate_render(request)
    encoder = select_encoder(request.encoder, available_encoders(FFMPEG) if request.encoder != 'cpu' else ('cpu',))
    update(job_id,render_encoder=encoder)
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
        config, plan = resolved_request(request, part, i+1, ad)
        clip_ad = ad if config.advertising.enabled else None
        cues = [cue.model_dump() for cue in config.subtitles.cues]
        total_duration = sum(s['duration'] for s in plan)
        while True:
            fragments = []
            encoder_label = 'NVIDIA' if encoder=='nvidia' else 'процессоре'
            update(job_id,message=f'Рендер части {i+1} из {len(request.parts)} на {encoder_label}')
            try:
                for n,segment in enumerate(plan):
                    fragment = f'segment-{i+1:04d}-{n+1:04d}.mp4' if len(plan)>1 else name
                    render_segment(job_id,config,clip_ad if segment['kind']=='advert' else source,{**segment, 'clip_duration':total_duration},fragment,
                        i*95/len(request.parts)+95/len(request.parts)*segment['output_start']/total_duration,
                        95/len(request.parts)*segment['duration']/total_duration,segment['kind']=='advert',source,encoder)
                    fragments.append(fragment)
                break
            except FFmpegError as error:
                if encoder!='nvidia' or request.encoder!='auto':
                    raise
                # Rebuild the entire current part: mixed encoder headers cannot be safely concatenated.
                encoder = 'cpu'
                update(job_id,render_encoder=encoder,encoder_fallback='NVIDIA → процессор',
                       encoder_fallback_reason=str(error),progress=i*95/len(request.parts))
        if len(fragments)>1:
            concat_name = f'concat-{i+1:04d}.txt'
            (directory/concat_name).write_text(''.join(f"file '{f}'\n" for f in fragments),'utf-8')
            run_ffmpeg(job_id,['-f','concat','-safe','1','-i',concat_name,'-map','0:v:0','-map','0:a:0','-c','copy',
                              '-t',f'{total_duration:.6f}','-movflags','+faststart',name],total_duration,
                       (i+.95)*95/len(request.parts),.05*95/len(request.parts),f'part-{i+1:02d}-concat.log')
        if config.audio.normalize:
            normalized = name.replace('.mp4', '.normalized.mp4')
            run_ffmpeg(job_id, ['-i', name, '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'copy',
                '-af', f'loudnorm=I={config.audio.target_lufs}:TP=-1.5:LRA=11', '-c:a', 'aac', '-ar', '48000',
                '-t', f'{total_duration:.6f}', '-movflags', '+faststart', normalized], total_duration,
                i*95/len(request.parts), 0, f'part-{i+1:02d}-normalize.log')
            os.replace(directory/normalized, directory/name)
        metadata = output_metadata(directory/name)
        if config.delivery.max_size_mb and metadata['size'] > config.delivery.max_size_mb*1024*1024:
            raise RuntimeError(f'Часть {i+1}: размер файла превышает заданный лимит.')
        if abs(metadata['duration']-total_duration) > max(.25,1/request.fps*len(plan)*2):
            raise RuntimeError(f'Часть {i+1}: длительность результата не совпадает с монтажной шкалой.')
        part_files = [file_record(job_id,name,**metadata)]
        final_cues = captions_for_timeline(cues,plan)
        if config.subtitles.enabled and final_cues:
            srt_name = f'part-{i+1:02d}.srt'
            write_srt(directory/srt_name,final_cues)
            part_files.append(file_record(job_id,srt_name))
        output = {'index':i,'name':name,'source_start':part.start,'source_end':part.end,
                  'planned_duration':total_duration,**metadata,'timeline':plan,'encoder':encoder}
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
    if len(request.parts)!=1:
        raise HTTPException(400,'Проверочный экспорт: выберите ровно один клип или один интервал.')
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
        device = JOBS.get(job_id, {}).get('asr_device', 'cpu')
        model_name = JOBS.get(job_id, {}).get('asr_model', '')
        label = 'NVIDIA' if device == 'cuda' else 'CPU'
        update(job_id,progress=min(96,15+81*segment.end/max(.1,duration)),
               message=f'Whisper {model_name} · {label}: распознано {segment.end:.0f} из {duration:.0f} секунд')
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
    import clipcanvas_ai as ai
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
        cues,language = recognize(directory/'speech.wav', request.model, request.language,
            lambda segments: group_words(segments,job_id,source['duration']), DATA/'models',
            lambda **values: update(job_id, **values), ai.load_config()['speech_device'])
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


def job_summary(job):
    keys = ('id','type','status','progress','message','error','created_at','updated_at','files','automatic_stage')
    result = {k:v for k,v in job.items() if k in keys}
    result['files'] = [f for f in job.get('files',[]) if not f.get('internal')]
    result['retryable'] = bool(job.get('request'))
    return result


@app.get('/api/jobs')
def list_jobs(summary: bool = False):
    with LOCK:
        values = [job_summary(j) if summary else dict(j) for j in JOBS.values() if not j.get('internal')]
        return {'jobs':sorted(values,key=lambda j:j.get('created_at',''),reverse=True)}


@app.get('/api/jobs/{job_id}')
def get_job(job_id: str, brief: bool = False):
    identifier(job_id)
    with LOCK:
        if job_id not in JOBS:
            raise HTTPException(404,'Задача не найдена.')
        job = JOBS[job_id]
        return job_summary(job) if brief and job['status'] in {'queued','running'} else dict(job)


@app.post('/api/jobs/{job_id}/cancel')
def cancel(job_id: str):
    get_job(job_id)
    with LOCK:
        job = JOBS[job_id]
        descendants = {job_id}
        while True:
            children = {key for key, row in JOBS.items() if row.get('parent_id') in descendants}
            if children <= descendants:
                break
            descendants.update(children)
        active = {child for child in descendants if JOBS[child]['status'] in {'queued','running'}}
        if not active:
            return {'status':job['status']}
        update(job_id, cancel_requested=True)
        for child in active:
            update(child, cancel_requested=True, message='Остановка…')
            future = FUTURES.get(child)
            if future and future.cancel():
                FUTURES.pop(child,None)
                update(child,status='cancelled',message='Задача удалена из очереди.')
        for child in descendants:
            process = PROCESSES.get(child)
            if process and process.poll() is None:
                terminate_process(process)
    return {'status':JOBS[job_id]['status']}


@app.post('/api/jobs/{job_id}/retry')
def retry(job_id: str):
    job = get_job(job_id)
    if job.get('internal'):
        raise HTTPException(409,'Повторите этап через выбранные элементы его пакета.')
    if job['status'] in {'queued','running'}:
        raise HTTPException(409,'Задача уже находится в очереди.')
    if job['type'] == 'batch' and any(job.get(key) and JOBS.get(job[key], {}).get('status') in {'queued','running'} for key in ('approval_job','archive_job')):
        raise HTTPException(409,'Дождитесь обработки выбранных элементов пакета.')
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
def list_projects(summary: bool = False):
    values = []
    for path in (DATA/'projects').glob('*.json'):
        try:
            fingerprint = (path.stat().st_mtime_ns, path.stat().st_size)
            previous = PROJECT_SUMMARIES.get(path.stem)
            if summary and previous and previous[0] == fingerprint:
                values.append(previous[1])
                continue
            project = json.loads(path.read_text('utf-8'))
            brief = {k:v for k,v in project.items() if k in {'id','name','source_id','created_at','updated_at'}}
            PROJECT_SUMMARIES[path.stem] = (fingerprint, brief)
            values.append(brief if summary else project)
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
        if job.get('internal'):
            raise HTTPException(409,'Этап принадлежит пакету. Удалите весь пакет из истории.')
        if any(job.get(key) and JOBS.get(job[key], {}).get('status') in {'queued','running'} for key in ('approval_job','archive_job')):
            raise HTTPException(409,'Сначала остановите обработку выбранных элементов.')
        if job['type'] == 'batch':
            batch_folder = (DATA/'batches'/identifier(job_id)).resolve()
            if batch_folder.parent != (DATA/'batches').resolve():
                raise HTTPException(400,'Некорректный путь пакета.')
            if batch_folder.exists():
                shutil.rmtree(batch_folder)
            descendants = {job_id, *[job[key] for key in ('approval_job','archive_job') if job.get(key)]}
            while True:
                children = {key for key,row in JOBS.items() if row.get('parent_id') in descendants}
                if children <= descendants:
                    break
                descendants.update(children)
            for child in descendants-{job_id}:
                child_folder = (DATA/'jobs'/identifier(child)).resolve()
                if child_folder.parent != (DATA/'jobs').resolve():
                    raise HTTPException(400,'Некорректный путь этапа.')
                if child_folder.exists():
                    shutil.rmtree(child_folder)
                JOBS.pop(child,None)
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
        for path in (DATA/'banner-templates').glob('*.json'):
            try:
                if json.loads(path.read_text('utf-8')).get('asset_id') == asset_id:
                    raise HTTPException(409,'Баннер используется шаблоном. Сначала удалите шаблон из библиотеки.')
            except (OSError, ValueError):
                continue
        for path in (DATA/'delivery-profiles').glob('*.json'):
            try:
                if asset_id in json.dumps(json.loads(path.read_text('utf-8'))):
                    raise HTTPException(409,'Файл используется профилем заказчика. Сначала удалите профиль.')
            except (OSError,ValueError):
                continue
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
        for key, filename in JOB_SIDECARS.items():
            if key not in job and (saved.parent/filename).is_file():
                job[key] = json.loads((saved.parent/filename).read_text('utf-8'))
        job.setdefault('created_at',datetime.fromtimestamp(saved.stat().st_mtime,timezone.utc).isoformat())
        job.setdefault('completed_parts',[])
        job.setdefault('outputs',[])
        if job['status'] in {'running','queued'}:
            job.update(status='error',error='Программа была закрыта. Нажмите «Продолжить», готовые части сохранятся.',message='Обработка прервана; можно продолжить.')
            atomic_json(saved,{k:v for k,v in job.items() if k not in JOB_SIDECARS or not (saved.parent/JOB_SIDECARS[k]).is_file()})
        JOBS[job['id']] = job
    except (ValueError,KeyError,OSError,HTTPException):
        continue
from url_import import register_url_routes
register_url_routes(app,{'DATA':DATA,'create_job':create_job,'update':update,'cancelled':cancelled,
    'probe':probe,'PROCESSES':PROCESSES,'LOCK':LOCK,'Cancelled':Cancelled,'FFMPEG':FFMPEG,
    'terminate_process':terminate_process,'register_job_type':register_job_type})
from clipcanvas_studio import register_studio_routes
studio = register_studio_routes(app, {name: globals()[name] for name in (
    'ROOT','DATA','create_job','update','cancelled','Cancelled','LOCK','PROCESSES','JOBS',
    'asset','audio_index','cache_path','cached_result','run_ffmpeg','file_record','atomic_json',
    'extract_job','Extract','get_job','group_words','Render','Geometry','validate_render','render_timeline','probe',
    'identifier','register_job_type','resolved_request')})
from clipcanvas_automatic import register_automatic_routes
automatic = register_automatic_routes(app, {name: globals()[name] for name in (
    'DATA','Render','Banner','Transcribe','transcribe_job','validate_render','asset','audio_index',
    'create_job','update','cancelled','Cancelled','get_job','register_job_type','identifier','atomic_json','LOCK',
    'write_srt','file_record')}, studio)
from clipcanvas_batch import register_batch_routes
batch = register_batch_routes(app, {name: globals()[name] for name in (
    'ROOT','DATA','Render','Source','Extract','Transcribe','asset','probe','audio_index','create_job','inline_job',
    'update','cancelled','Cancelled','get_job','register_job_type','identifier','atomic_json','LOCK','JOBS',
    'render_job','resolved_request','run_ffmpeg','file_record','save_project','extract_job','transcribe_job',
    'validate_render','write_srt','now')}, studio, automatic)
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
