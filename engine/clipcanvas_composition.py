"""Canonical non-destructive composition timeline shared by preview and export.

Cuts and captions use source seconds. Output banners explicitly use final clip
seconds; source-anchored banners automatically survive subsequent edits.
"""
from __future__ import annotations

import math
from clipcanvas_media import captions_for_timeline, timeline


def normalize_rotation(value) -> float:
    """Store clockwise canvas angles in one stable, finite degree range."""
    if isinstance(value, bool):
        raise ValueError('Угол поворота должен быть конечным числом.')
    try:
        angle = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('Угол поворота должен быть конечным числом.') from error
    if not math.isfinite(angle):
        raise ValueError('Угол поворота должен быть конечным числом.')
    angle = (angle+180) % 360-180
    angle = round(angle, 6)
    return -180.0 if angle >= 180 else 0.0 if not angle else angle


def banner_rotation_filter(label: str, rotation: float, out: str) -> str:
    """Rotate a fitted RGBA box around its centre, retaining transparent corners."""
    angle = normalize_rotation(rotation)
    if not angle:
        return f'[{label}]null[{out}]'
    radians = f'{angle:.6f}*PI/180'
    # Subtract a tiny epsilon to avoid a spurious extra pixel at right angles.
    # A transparent sampling border lets bilinear interpolation soften the image
    # edge. Without it FFmpeg extends opaque edge pixels outside the source box.
    return (f'[{label}]format=rgba,pad=iw+4:ih+4:2:2:color=0x00000000,rotate=angle={radians}:'
            f'ow=ceil(rotw({radians})-0.000001):oh=ceil(roth({radians})-0.000001):'
            f'c=none[{out}]')


def banner_overlay_position(geometry) -> tuple[str, str]:
    """Expanded rotated pixels must retain the original canvas-box centre."""
    box = geometry.model_dump() if hasattr(geometry, 'model_dump') else geometry
    return (f'{box["x"]+box["w"]/2:.6f}-overlay_w/2',
            f'{box["y"]+box["h"]/2:.6f}-overlay_h/2')


def _estimate_banner_frame_key(pixels) -> dict:
    """Assess one decoded RGBA frame without modifying its pixels."""
    import numpy as np

    uncertain = {'chromakey': None, 'confidence': 0.0,
                 'reason': 'Однотонный фон не определён уверенно. Подберите цвет пипеткой.'}
    height, width = pixels.shape[:2]
    band = max(1, min(4, width//32, height//32))
    border = np.concatenate((pixels[:band].reshape(-1, 4), pixels[-band:].reshape(-1, 4),
                             pixels[band:-band, :band].reshape(-1, 4),
                             pixels[band:-band, -band:].reshape(-1, 4)))
    # Transparent PNGs already have a cutout; a guessed key could erase its art.
    if np.mean(border[:, 3] < 250) > .03:
        return {**uncertain, 'reason': 'У баннера уже есть прозрачный край. Исходная прозрачность сохранена.'}
    rgb = border[:, :3].astype(float)
    patch = max(2, min(width, height)//16)
    corners = [pixels[y:y+patch, x:x+patch].reshape(-1, 4)
               for y in (0, height-patch) for x in (0, width-patch)]
    candidates = [np.median(corner[corner[:, 3] >= 250, :3], axis=0)
                  for corner in corners if np.mean(corner[:, 3] >= 250) >= .97]
    if not candidates:
        return uncertain
    normalizer = 255*math.sqrt(3)
    distance = lambda samples, color: np.linalg.norm(samples-color, axis=-1)/normalizer
    candidate = max(candidates, key=lambda color: float(np.mean(distance(rgb, color) <= .035)))
    support = distance(rgb, candidate) <= .035
    coverage = float(np.mean(support))
    agreed_corners = sum(float(np.mean(distance(corner[:, :3].astype(float), candidate) <= .035)) >= .9
                         for corner in corners)
    if coverage < .92 or agreed_corners < 3:
        return uncertain
    color = np.rint(np.median(rgb[support], axis=0)).astype(int)
    residual = distance(rgb[support], color)
    tolerance = min(.065, max(.012, float(np.quantile(residual, .99))+.004))
    blend = .012
    opaque = pixels[:, :, 3] >= 250
    foreground = distance(pixels[:, :, :3].astype(float), color) > tolerance+blend
    visible_foreground = float(np.mean(foreground[opaque])) if np.any(opaque) else 0
    if visible_foreground < .015:
        return {**uncertain, 'reason': 'Баннер почти однотонный: автоматическое удаление могло бы стереть изображение.'}
    if visible_foreground > .9:
        return uncertain
    return {'chromakey': {'enabled': True, 'color': '#'+''.join(f'{channel:02x}' for channel in color),
                          'similarity': round(tolerance, 4), 'blend': blend},
            'confidence': round(coverage, 3),
            'reason': 'Однотонный фон определён по совпадению цвета на краях и в углах изображения.'}


def estimate_banner_key(path, *, duration=None, cancelled=None) -> dict:
    """Conservatively estimate an image, GIF or video banner's solid background.

    Videos need consistent edge colours on several frames across their duration.
    The optional cancellation checkpoint can return true or raise to stop work.
    Assets are read-only; uncertain samples never apply a partial key.
    """
    import av
    import numpy as np

    uncertain = {'chromakey': None, 'confidence': 0.0, 'sampled_frames': 0,
                 'reason': 'Не удалось проверить фон баннера. Подберите цвет пипеткой.'}
    results, seen = [], set()

    def check():
        if cancelled and cancelled():
            raise InterruptedError('Проверка фона остановлена.')

    def assess(frame):
        check()
        identity = frame.pts
        if identity is not None and identity in seen:
            return
        if min(frame.width, frame.height) < 8:
            raise ValueError('Изображение слишком маленькое для определения фона.')
        scale = min(1, 320/max(frame.width, frame.height))
        pixels = frame.reformat(width=max(8, round(frame.width*scale)),
                               height=max(8, round(frame.height*scale)), format='rgba').to_ndarray()
        results.append(_estimate_banner_frame_key(pixels))
        if identity is not None:
            seen.add(identity)

    check()
    try:
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            # Native VP8/VP9 decoders may omit WebM's auxiliary alpha plane.
            # Never infer a key from those incomplete RGB pixels.
            if str(stream.metadata.get('alpha_mode', '0')) == '1':
                return {**uncertain, 'reason': 'У видеобаннера есть канал прозрачности. Исходная прозрачность сохранена.'}
            decoder = container.decode(video=0)
            first = next(decoder)
            assess(first)
            second = next(decoder, None)
            if second is not None:
                assess(second)
                known_duration = float(duration or 0)
                if not math.isfinite(known_duration) or known_duration <= 0:
                    known_duration = float(stream.duration*stream.time_base) if stream.duration else 0
                if not math.isfinite(known_duration) or known_duration <= 0:
                    known_duration = float(container.duration/av.time_base) if container.duration else 0
                if not math.isfinite(known_duration) or known_duration <= 0:
                    return {**uncertain, 'sampled_frames': len(results),
                            'reason': 'Не удалось определить длительность анимации для проверки всего фона.'}
                origin = float(first.time or 0)
                for fraction in (.2, .4, .6, .8, .98):
                    check()
                    target = origin+known_duration*fraction
                    container.seek(int(target/stream.time_base), stream=stream, backward=True)
                    frame = None
                    for decoded in container.decode(video=0):
                        check()
                        frame = decoded
                        if decoded.time is not None and float(decoded.time) >= target-.001:
                            break
                    if frame is None:
                        return {**uncertain, 'sampled_frames': len(results),
                                'reason': 'Не удалось проверить все выбранные кадры анимации. Фон сохранён.'}
                    assess(frame)
    except InterruptedError:
        raise
    except (OSError, ValueError, StopIteration, IndexError):
        return {**uncertain, 'sampled_frames': len(results)}
    for result in results:
        if not result['chromakey']:
            return {**result, 'sampled_frames': len(results)}
    if len(results) == 1:
        return {**results[0], 'sampled_frames': 1}
    colors = np.array([[int(result['chromakey']['color'][i:i+2], 16) for i in (1, 3, 5)]
                       for result in results], dtype=float)
    color = np.rint(np.median(colors, axis=0)).astype(int)
    deviations = np.linalg.norm(colors-color, axis=1)/(255*math.sqrt(3))
    tolerance = max(result['chromakey']['similarity']+float(delta)
                    for result, delta in zip(results, deviations))
    if float(deviations.max()) > .025 or tolerance > .065:
        return {**uncertain, 'sampled_frames': len(results),
                'reason': 'Цвет фона меняется между кадрами. Автоматическое удаление не применено.'}
    return {'chromakey': {'enabled': True, 'color': '#'+''.join(f'{channel:02x}' for channel in color),
                          'similarity': round(tolerance, 4), 'blend': .012},
            'confidence': min(result['confidence'] for result in results), 'sampled_frames': len(results),
            'reason': f'Однотонный фон совпадает на {len(results)} выборочных кадрах анимации.'}


def merged_cuts(cuts: list[dict]) -> list[dict]:
    result = []
    for cut in sorted(cuts, key=lambda item: float(item['start'])):
        start, end = float(cut['start']), float(cut['end'])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError('Некорректные границы удаляемого участка.')
        if result and start <= result[-1]['end']:
            result[-1]['end'] = max(result[-1]['end'], end)
        else:
            result.append({'start': start, 'end': end})
    return result


def retained_intervals(start: float, end: float, cuts: list[dict]) -> list[dict]:
    result, cursor, output = [], start, 0.0
    for cut in merged_cuts(cuts):
        if cut['end'] <= cursor or cut['start'] >= end:
            continue
        if cut['start'] > cursor:
            stop = min(end, cut['start'])
            result.append({'kind': 'source', 'start': cursor, 'end': stop,
                           'output_start': output, 'duration': stop-cursor})
            output += stop-cursor
        cursor = max(cursor, cut['end'])
    if cursor < end:
        result.append({'kind': 'source', 'start': cursor, 'end': end,
                       'output_start': output, 'duration': end-cursor})
    return result


def composition_timeline(start: float, end: float, cuts: list[dict],
                         ad_duration: float = 0, ad_start: float = 20,
                         repeat: bool = True, interval: float = 60,
                         middle: bool = False) -> list[dict]:
    kept = retained_intervals(start, end, cuts)
    duration = sum(item['duration'] for item in kept)
    if duration < .099:
        raise ValueError('После удаления участков в выбранной части осталось меньше 0,1 секунды.')
    virtual = timeline(0, duration, ad_duration, duration/2 if middle else ad_start,
                       False if middle else repeat, interval)
    result = []
    for segment in virtual:
        if segment['kind'] == 'advert':
            position = segment['source_position']
            owner = next((item for item in kept if item['output_start']+item['duration'] > position+1e-8), kept[-1])
            result.append({**segment, 'source_position': owner['start']+max(0, position-owner['output_start'])})
            continue
        for item in kept:
            a = max(segment['start'], item['output_start'])
            b = min(segment['end'], item['output_start']+item['duration'])
            if b <= a+1e-8:
                continue
            result.append({'kind': 'source', 'start': item['start']+a-item['output_start'],
                           'end': item['start']+b-item['output_start'],
                           'output_start': segment['output_start']+a-segment['start'],
                           'duration': b-a})
    return result


def timed_items_for_timeline(items: list[dict], plan: list[dict]) -> list[dict]:
    """Clip/split source anchors at edits and translate to final clip seconds."""
    result = []
    duration = sum(segment['duration'] for segment in plan)
    for item in items:
        if item.get('enabled', True) is False:
            continue
        if item.get('timebase', 'source') == 'output':
            a, b = max(0, item['start']), min(duration, item['end'])
            if b > a:
                result.append({**item, 'start': a, 'end': b, 'timebase': 'output'})
        else:
            for mapped in captions_for_timeline([item], plan):
                mapped.pop('words', None)
                result.append({**mapped, 'timebase': 'output'})
    return result


def remap_after_cuts(items: list[dict], cuts: list[dict]) -> list[dict]:
    """Pure helper for external clients that keep items in an edited timebase."""
    for item in items:
        a, b = float(item['start']), float(item['end'])
        if not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a:
            raise ValueError('Начало элемента должно быть раньше конца и не меньше нуля.')
    end = max([float(item['end']) for item in items]+[0])
    if not end:
        return []
    plan = retained_intervals(0, end, cuts)
    return captions_for_timeline(items, plan)
