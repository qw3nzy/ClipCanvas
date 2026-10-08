"""Shared editing rules for manual clips, delivery profiles and unattended batches."""
from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from clipcanvas_composition import composition_timeline, retained_intervals


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class MotionPoint(Strict):
    time: float = Field(ge=0, le=86400)
    x: float | None = Field(default=None, ge=-15360, le=15360)
    y: float | None = Field(default=None, ge=-15360, le=15360)
    scale: float = Field(default=1, ge=.1, le=4)
    opacity: float = Field(default=1, ge=0, le=1)


class BannerSchedule(Strict):
    mode: Literal['source', 'each', 'start', 'end', 'full', 'interval', 'bubavpn'] = 'source'
    start: float = Field(default=5, ge=0, le=86400)
    duration: float = Field(default=10, ge=.1, le=86400)
    interval: float = Field(default=30, ge=.1, le=86400)
    clips: list[int] = Field(default_factory=list, max_length=1000)
    excluded_occurrences: list[int] = Field(default_factory=list, max_length=1000)
    @model_validator(mode='after')
    def valid(self):
        if any(index < 1 or index > 1000 for index in self.clips):
            raise ValueError('Номера частей: от 1 до 1000.')
        if any(index < 0 or index >= 1000 for index in self.excluded_occurrences):
            raise ValueError('Номера показов: от 0 до 999.')
        if self.mode == 'interval' and self.interval < self.duration:
            raise ValueError('Интервал повторения баннера должен быть не короче показа.')
        self.clips = sorted(set(self.clips))
        self.excluded_occurrences = sorted(set(self.excluded_occurrences))
        return self


class AudioSettings(Strict):
    mute_source: bool = False
    source_volume: float = Field(default=1, ge=0, le=3)
    advert_volume: float = Field(default=1, ge=0, le=3)
    ducking: bool = False
    duck_trigger: Literal['sounding', 'visible'] = 'sounding'
    duck_volume: float = Field(default=.25, ge=0, le=1)
    fade: float = Field(default=0, ge=0, le=5)
    normalize: bool = False
    limiter: bool = True
    target_lufs: float = Field(default=-16, ge=-30, le=-10)


class FocusPoint(Strict):
    time: float = Field(ge=0, le=86400)
    x: float = Field(ge=0, le=1)
    y: float = Field(default=.5, ge=0, le=1)


class Framing(Strict):
    mode: Literal['manual', 'center', 'subject'] = 'manual'
    subject: str = Field(default='Главный говорящий персонаж', max_length=500)
    x: float = Field(default=.5, ge=0, le=1)
    y: float = Field(default=.5, ge=0, le=1)
    interpolation: Literal['linear', 'hold'] = 'linear'
    points: list[FocusPoint] = Field(default_factory=list, max_length=1000)
    @model_validator(mode='after')
    def ordered(self):
        self.points.sort(key=lambda point: point.time)
        if len({point.time for point in self.points}) != len(self.points):
            raise ValueError('Точки кадрирования должны иметь разные времена.')
        return self


class DeliveryRules(Strict):
    platform: Literal['custom', 'tiktok', 'shorts'] = 'custom'
    max_duration: float = Field(default=0, ge=0, le=86400)
    max_size_mb: float = Field(default=0, ge=0, le=10000)
    min_banner_width: float = Field(default=0, ge=0, le=3840)
    min_banner_height: float = Field(default=0, ge=0, le=3840)
    min_banner_seconds: float = Field(default=0, ge=0, le=86400)
    require_banner: bool = False
    require_subtitles: bool = False
    check_safe_zones: bool = False
    safe_top: float = Field(default=.12, ge=0, le=.45)
    safe_bottom: float = Field(default=.20, ge=0, le=.45)
    safe_right: float = Field(default=.17, ge=0, le=.45)
    caption_lines: int = Field(default=2, ge=0, le=4)
    @model_validator(mode='after')
    def platform_limit(self):
        if self.platform == 'shorts' and (not self.max_duration or self.max_duration > 180):
            self.max_duration = 180
        return self


class TextOverlay(Strict):
    id: str = Field(default='', max_length=100)
    text: str = Field(min_length=1, max_length=2000)
    enabled: bool = True
    start: float = Field(default=0, ge=0)
    end: float = Field(default=10, gt=0)
    timebase: Literal['source', 'output'] = 'output'
    x: float = Field(default=50, ge=0, le=100)
    y: float = Field(default=15, ge=0, le=100)
    size: int = Field(default=48, ge=16, le=160)
    color: str = Field(default='#ffffff', pattern=r'^#[0-9a-fA-F]{6}$')
    font: str = Field(default='Arial', max_length=100, pattern=r'^[^,\r\n{}\\]+$')
    bold: bool = True
    background: bool = False
    @model_validator(mode='after')
    def interval(self):
        if self.end <= self.start:
            raise ValueError('Конец текстового слоя должен быть позже начала.')
        return self


def interpolate(points, time, key, default, hold=False):
    if not points:
        return default
    values = []
    previous = default
    for point in sorted(points, key=lambda row: row['time']):
        previous = point.get(key) if point.get(key) is not None else previous
        values.append((point['time'], previous))
    if time <= values[0][0]:
        return values[0][1]
    for (a, first), (b, second) in zip(values, values[1:]):
        if time < b:
            return first if hold else first+(second-first)*(time-a)/(b-a)
    return values[-1][1]


def motion_expression(points, key, default, clock='t', hold=False):
    """Bounded numbers only: user strings never become FFmpeg expressions."""
    values, previous = [], default
    for point in sorted(points, key=lambda row: row['time']):
        previous = point.get(key) if point.get(key) is not None else previous
        values.append((float(point['time']), float(previous)))
    if not values:
        return f'{default:.6f}'
    result = f'{values[-1][1]:.6f}'
    for (a, first), (b, second) in reversed(list(zip(values, values[1:]))):
        value = f'{first:.6f}' if hold else f'({first:.6f}+({second-first:.6f})*(({clock})-{a:.6f})/{b-a:.6f})'
        result = f'if(lt(({clock}),{b:.6f}),{value},{result})'
    return f'if(lte(({clock}),{values[0][0]:.6f}),{values[0][1]:.6f},{result})'


def bubavpn_starts(duration, banner_duration):
    """Keep the first full animation centered; distribute extra full-minute shows."""
    length = min(duration, banner_duration)
    count = min(1000, max(1, math.floor(duration/60)))
    centers = [duration/2]
    for _ in range(count-1):
        edges = [length/2, *sorted(centers), duration-length/2]
        gap = max(zip(edges, edges[1:]), key=lambda pair: (pair[1]-pair[0], pair[0]))
        centers.append(sum(gap)/2)
    return [center-length/2 for center in centers], length


def scheduled_banners(banners, duration, clip_index):
    result = []
    for original in banners:
        if not original.get('enabled'):
            continue
        rule = BannerSchedule.model_validate(original.get('schedule') or {})
        if rule.clips and clip_index not in rule.clips:
            continue
        if rule.mode == 'source':
            if 0 not in rule.excluded_occurrences:
                result.append(deepcopy(original))
            continue
        if rule.mode == 'bubavpn':
            starts, length = bubavpn_starts(duration, rule.duration)
        elif rule.mode == 'full':
            starts, length = [0], duration
        elif rule.mode == 'end':
            starts, length = [max(0, duration-rule.start-rule.duration)], rule.duration
        else:
            start = 0 if rule.mode == 'start' else rule.start
            starts = [start] if rule.mode != 'interval' else [start+i*rule.interval for i in range(min(1000, max(0, math.ceil((duration-start)/rule.interval))))]
            length = rule.duration
        for occurrence, start in enumerate(starts):
            if occurrence in rule.excluded_occurrences:
                continue
            end = min(duration, start+length)
            if end <= start:
                continue
            banner = deepcopy(original)
            banner.update(start=start, end=end, timebase='output', schedule={'mode': 'source'},
                          id=f'{original.get("id") or "banner"}-clip-{clip_index}-{occurrence}')
            result.append(banner)
    return result


def effective_payload(payload, part, index, ad_duration=0):
    result = deepcopy(payload)
    override = result.get('clip_overrides', {}).get(str(index), {})
    for key in ('video', 'banners', 'audio', 'framing', 'text_layers'):
        if override.get(key) is not None:
            result[key] = deepcopy(override[key])
    if payload.get('audio', {}).get('mute_source'):
        result['audio']['mute_source'] = True
    if override.get('subtitles'):
        result['subtitles'].update({key:value for key,value in override['subtitles'].items() if value is not None})
        for cue in result['subtitles'].get('cues', []):
            for key in ('render_lines', 'render_font_size', 'render_y_offset'):
                cue.pop(key, None)
    if override.get('subtitles_enabled') is not None:
        result['subtitles']['enabled'] = override['subtitles_enabled']
    if override.get('advertising_enabled') is not None:
        result['advertising']['enabled'] = override['advertising_enabled']
    ad = result['advertising']
    plan = composition_timeline(part['start'], part['end'], result.get('cuts', []),
        ad_duration/ad.get('speed', 1) if ad['enabled'] else 0, ad.get('start', 20),
        ad.get('repeat', True), ad.get('interval', 60), ad.get('placement') == 'middle')
    duration = sum(segment['duration'] for segment in plan)
    hidden = set(override.get('hidden_banner_ids', []))
    originals = [] if override.get('banners_enabled') is False else [
        banner for banner in [result.get('banner', {}), *result.get('banners', [])]
        if banner.get('id') not in hidden]
    for banner in originals:
        schedule = override.get('banner_schedules', {}).get(banner.get('id'))
        if schedule is not None:
            banner['schedule'] = deepcopy(schedule)
    result['banner'] = {'enabled': False}
    result['banners'] = scheduled_banners(originals, duration, index)
    result['parts'], result['clip_overrides'] = [part], {}
    return result, plan


def split_for_delivery(duration, target, cuts=None, advertising=None, ad_duration=0, cues=None, fps=30):
    """Choose source ranges by final duration, including cuts and inserted adverts."""
    if not math.isfinite(duration) or duration < .1 or not math.isfinite(target) or target <= .1:
        raise ValueError('Укажите допустимую длительность готовой части больше 0,1 с.')
    ad = advertising or {'enabled': False}
    budget = target-1/fps
    def length(start, end):
        try:
            plan = composition_timeline(start, end, cuts or [], ad_duration/ad.get('speed', 1) if ad.get('enabled') else 0,
                ad.get('start', 20), ad.get('repeat', True), ad.get('interval', 60), ad.get('placement') == 'middle')
            return sum(segment['duration'] for segment in plan)
        except ValueError:
            return 0
    parts, start = [], 0.0
    while start < duration-.099:
        if not retained_intervals(start, duration, cuts or []):
            break
        if length(start, duration) <= budget:
            end = duration
        else:
            low, high = start, duration
            for _ in range(48):
                middle = (low+high)/2
                if length(start, middle) <= budget:
                    low = middle
                else:
                    high = middle
            end = math.floor(low*fps)/fps
            candidates = [cue['end'] for cue in cues or [] if start+.5 < cue['end'] <= end and end-cue['end'] <= min(4, target*.15)]
            if candidates:
                end = max(candidates)
        minimum = max(.1, ad.get('start', 20)+.1) if ad.get('enabled') and ad.get('placement') != 'middle' else .1
        remaining = sum(span['duration'] for span in retained_intervals(end, duration, cuts or []))
        if end < duration and remaining < minimum:
            # Leave enough source footage for the final clip's advertising point.
            low, high = start, end
            for _ in range(48):
                middle = (low+high)/2
                tail = sum(span['duration'] for span in retained_intervals(middle, duration, cuts or []))
                if tail >= minimum:
                    low = middle
                else:
                    high = middle
            end = math.floor(low*fps)/fps
        if end-start < .099 or not .099 <= length(start, end) <= target+.001:
            raise ValueError('Реклама не помещается в заданную длину. Увеличьте лимит или сократите вставку.')
        parts.append({'start': round(start, 6), 'end': round(end, 6)})
        if len(parts) > 1000:
            raise ValueError('Получается больше 1000 частей.')
        start = end
    return parts


def split_by_count(duration, count, cuts=None, cues=None):
    """Split retained footage into an exact number of nonempty source ranges."""
    spans = retained_intervals(0, duration, cuts or [])
    total = sum(span['duration'] for span in spans)
    if count < 1 or count > 1000 or total/count < .1:
        raise ValueError('Для такого числа частей недостаточно оставшегося видео.')
    def source_time(position):
        for span in spans:
            if position <= span['duration']:
                return span['start']+position
            position -= span['duration']
        return duration
    def retained(position):
        return sum(max(0, min(position, span['end'])-span['start']) for span in spans)
    boundaries = [0.0]
    for index in range(1, count):
        ideal = source_time(total*index/count)
        candidates = [cue['end'] for cue in cues or [] if abs(cue['end']-ideal) <= min(4, total/count*.15)
                      and retained(cue['end'])-retained(boundaries[-1]) >= .1
                      and total-retained(cue['end']) >= (count-index)*.1]
        boundaries.append(min(candidates, key=lambda value:abs(value-ideal)) if candidates else ideal)
    boundaries.append(duration)
    return [{'start':round(a, 6), 'end':round(b, 6)} for a,b in zip(boundaries,boundaries[1:])]


def safe_relative_path(value):
    value = str(value).replace('\\', '/')
    pieces = value.split('/')
    if not value or len(value) > 1000 or value.startswith('/') or any(piece in ('', '.', '..') for piece in pieces):
        raise ValueError('Путь внутри папки должен быть относительным без «..».')
    if any(re.search(r'[\x00-\x1f<>:"|?*]', piece) or piece.endswith((' ', '.')) for piece in pieces):
        raise ValueError('В пути есть недопустимые символы.')
    if any(re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', piece, re.I) for piece in pieces):
        raise ValueError('В пути есть зарезервированное имя Windows.')
    return '/'.join(pieces)


def delivery_issues(payload, parts, asset_info):
    from clipcanvas_composition import timed_items_for_timeline
    from clipcanvas_automatic import protected_box, subtitle_regions
    from clipcanvas_layout import rotated_bounds
    rules = DeliveryRules.model_validate(payload.get('delivery') or {})
    width, height = payload['canvas']['width'], payload['canvas']['height']
    issues = []
    for index, (config, plan) in enumerate(parts, 1):
        rules = DeliveryRules.model_validate(config.get('delivery') or payload.get('delivery') or {})
        duration = sum(segment['duration'] for segment in plan)
        def warn(message, severity='warning', **extra):
            issues.append({'part': index, 'severity': 'warning', 'message': message, **extra})
        if rules.max_duration and duration > rules.max_duration+.001:
            warn(f'Итоговая длительность {duration:.2f} с превышает лимит {rules.max_duration:g} с.', 'error')
        if rules.max_size_mb:
            estimated = disk_estimate({**payload, **config}, duration)['output_bytes']/1024**2
            if estimated > rules.max_size_mb:
                warn(f'Оценка размера {estimated:.2f} МБ выше лимита {rules.max_size_mb:g} МБ; точный размер проверим после экспорта.')
        visible = [banner for banner in timed_items_for_timeline(config['banners'], plan)
                   if banner.get('opacity', 1) > 0 and (not banner.get('motion') or any(point.get('opacity', 1) > 0 for point in banner['motion']))]
        if rules.require_banner and not visible:
            warn('В этой части отсутствует обязательный баннер.', 'error')
        if rules.require_subtitles and (not config['subtitles']['enabled'] or not any(c['text'].strip() and c['start'] < s['end'] and c['end'] > s['start'] for c in config['subtitles'].get('cues', []) for s in plan if s['kind'] == 'source')):
            warn('В этой части отсутствуют обязательные субтитры.', 'error')
        boxes = []
        for banner in visible:
            meta = asset_info(banner['asset_id'])
            seconds = banner['end']-banner['start']
            if meta.get('media_type') == 'video' and not banner.get('loop', True):
                seconds = min(seconds, max(0, meta['duration']-banner.get('offset', 0))/banner.get('speed', 1))
            if seconds+.01 < rules.min_banner_seconds:
                warn(f'Баннер «{banner["name"]}» виден только {seconds:.2f} с.', 'error')
            geometry = banner['geometry']
            box = rotated_bounds(geometry)
            points = banner.get('motion', [])
            if points:
                moments = {0, banner['end']-banner['start'], *[point['time'] for point in points if point['time'] <= banner['end']-banner['start']]}
                positions = [rotated_bounds({**geometry,
                    'x':interpolate(points, moment, 'x', geometry['x']), 'y':interpolate(points, moment, 'y', geometry['y']),
                    'w':geometry['w']*interpolate(points, moment, 'scale', 1),
                    'h':geometry['h']*interpolate(points, moment, 'scale', 1)}) for moment in moments]
                left, top = min(p['x'] for p in positions), min(p['y'] for p in positions)
                box = {'x':left,'y':top,'w':max(p['x']+p['w'] for p in positions)-left,
                       'h':max(p['y']+p['h'] for p in positions)-top}
            if box['x'] < -.5 or box['y'] < -.5 or box['x']+box['w'] > width+.5 or box['y']+box['h'] > height+.5:
                warn(f'Баннер «{banner["name"]}» обрезается краем кадра.')
            scale = min(geometry['w']/meta['width'], geometry['h']/meta['height']) if geometry.get('fit') == 'contain' else None
            bw, bh = (meta['width']*scale, meta['height']*scale) if scale else (geometry['w'], geometry['h'])
            minimum_scale = min(interpolate(points, moment, 'scale', 1) for moment in moments) if points else 1
            bw, bh = bw*minimum_scale, bh*minimum_scale
            if bw+.01 < rules.min_banner_width or bh+.01 < rules.min_banner_height:
                warn(f'Видимый размер баннера «{banner["name"]}» меньше требований.', 'error')
            if rules.check_safe_zones and (box['y'] < height*rules.safe_top or box['y']+box['h'] > height*(1-rules.safe_bottom) or box['x']+box['w'] > width*(1-rules.safe_right)):
                warn(f'Баннер «{banner["name"]}» пересекает интерфейс платформы.')
            boxes.append((banner, box))
        def overlaps(a, b):
            return min(a['x']+a['w'], b['x']+b['w']) > max(a['x'], b['x']) and min(a['y']+a['h'], b['y']+b['h']) > max(a['y'], b['y'])
        for a, (banner, box) in enumerate(boxes):
            for other, other_box in boxes[a+1:]:
                if banner['start'] < other['end'] and other['start'] < banner['end'] and overlaps(box, other_box):
                    warn(f'Баннеры «{banner["name"]}» и «{other["name"]}» перекрываются.')
            if config['subtitles']['enabled']:
                # Output spans are translated back to source by the resolved plan.
                regions = []
                for segment in plan:
                    if segment['kind'] == 'source' and banner['start'] < segment['output_start']+segment['duration'] and banner['end'] > segment['output_start']:
                        regions.extend(subtitle_regions(config['subtitles'], config['canvas'], segment['start'], segment['end']))
                if any(overlaps(box, region) for region in regions):
                    warn(f'Баннер «{banner["name"]}» перекрывает субтитры.')
    return issues


def disk_estimate(payload, seconds, *, zip_output=None):
    pixels = payload['canvas']['width']*payload['canvas']['height']
    bitrate = (6_000_000 if payload.get('quality') == 'high' else 3_000_000)*max(.3, pixels/(1080*1920))
    bitrate *= max(1, payload.get('fps', 30)/30)
    output = math.ceil(seconds*(bitrate+192000)/8)
    include_zip = payload.get('zip', False) if zip_output is None else zip_output
    return {'output_bytes': output, 'temporary_bytes': output,
            'required_bytes': output*(3 if include_zip else 2)+64*1024*1024,
            'estimate': True}
