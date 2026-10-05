"""Bounded banner placement: client rules and protected regions are hard constraints."""
from __future__ import annotations

import math
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from clipcanvas_composition import normalize_rotation


class LayoutRect(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    x: float = Field(ge=-15360, le=15360)
    y: float = Field(ge=-15360, le=15360)
    w: float = Field(gt=0, le=15360)
    h: float = Field(gt=0, le=15360)
    fit: Literal['contain', 'cover', 'stretch'] = 'contain'
    rotation: float = 0

    @field_validator('rotation', mode='before')
    @classmethod
    def canonical_rotation(cls, value):
        return normalize_rotation(value)


class BannerRules(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    movable: bool = True
    resizable: bool = False
    min_width: float = Field(default=0, ge=0, le=3840)
    min_height: float = Field(default=0, ge=0, le=3840)
    zone: Literal['any', 'top', 'bottom', 'left', 'right'] = 'any'


BANNER_SCHEMA = {'type': 'object', 'properties': {
    'regions': {'type': 'array', 'maxItems': 48, 'items': {'type': 'object', 'properties': {
        'frame': {'type': 'integer', 'minimum': 0},
        **{key: {'type': 'number', 'minimum': 0, 'maximum': 100} for key in ('x', 'y', 'w', 'h')},
        'kind': {'type': 'string', 'enum': ['face', 'subject', 'text']}},
        'required': ['frame', 'x', 'y', 'w', 'h', 'kind'], 'additionalProperties': False}},
    'requirements': {'type': 'object', 'properties': {
        'can_adapt': {'type': 'boolean'}, 'lock_size': {'type': 'boolean'}, 'lock_position': {'type': 'boolean'},
        'min_width': {'type': 'number', 'minimum': 0, 'maximum': 3840},
        'min_height': {'type': 'number', 'minimum': 0, 'maximum': 3840},
        'max_width': {'type': 'number', 'minimum': 0, 'maximum': 3840},
        'max_height': {'type': 'number', 'minimum': 0, 'maximum': 3840},
        'zone': {'type': 'string', 'enum': ['any', 'top', 'bottom', 'left', 'right']},
        'reason': {'type': 'string', 'maxLength': 500}},
        'required': ['can_adapt', 'lock_size', 'lock_position', 'min_width', 'min_height', 'max_width', 'max_height', 'zone', 'reason'],
        'additionalProperties': False}}, 'required': ['regions', 'requirements'], 'additionalProperties': False}


def enforce_client_locks(requirements, instructions):
    """Explicit prohibitions remain binding even if the model overlooks their meaning."""
    if not isinstance(requirements, dict):
        return requirements
    result = dict(requirements)
    prohibitions = r'\b(?:не\s+(?:менять|изменять|трогать|уменьшать|увеличивать|двигать|перемещать)|нельзя\s+(?:менять|изменять|двигать|перемещать)|запрещ\w*\s+(?:менять|изменять|двигать|перемещать|изменение|перемещение))\b'
    size, position = False, False
    for clause in re.split(r'[.!?;\n]', instructions.casefold()):
        forbidden = re.search(prohibitions, clause)
        size |= bool(forbidden and re.search(r'\b(?:размер\w*|масштаб\w*|ширин\w*|высот\w*)\b', clause))
        position |= bool(forbidden and re.search(r'\b(?:позици\w*|положени\w*|расположени\w*|двигать|перемещать)\b', clause))
        size |= bool(re.search(r'\b(?:фиксированн\w*\s+размер\w*|размер\w*\s+фиксированн\w*)\b', clause))
    size |= bool(re.search(r'\b(?:do not|don.t|never)\s+(?:resize|scale|change\s+(?:the\s+)?size)\b', instructions.casefold()))
    position |= bool(re.search(r'\b(?:do not|don.t|never)\s+(?:move|reposition)\b', instructions.casefold()))
    if size:
        result['lock_size'] = True
    if position:
        result['lock_position'] = True
    if size or position:
        result['reason'] = ('Учтён явный запрет в ТЗ: '+('изменение размера' if size else '')+
                            (' и ' if size and position else '')+('перемещение' if position else '')+
                            '. Разрешённая зона: '+{'any': 'любая', 'top': 'сверху', 'bottom': 'снизу',
                            'left': 'слева', 'right': 'справа'}.get(result.get('zone'), 'по ТЗ')+'.')
    return result


def intersection(a, b):
    x, y = max(a['x'], b['x']), max(a['y'], b['y'])
    w, h = min(a['x']+a['w'], b['x']+b['w'])-x, min(a['y']+a['h'], b['y']+b['h'])-y
    return {'x': x, 'y': y, 'w': w, 'h': h} if w > 0 and h > 0 else None


def rotated_bounds(box):
    """Conservative canvas bounds for a box rotated around its centre."""
    angle = math.radians(normalize_rotation(box.get('rotation', 0)))
    width = abs(box['w']*math.cos(angle))+abs(box['h']*math.sin(angle))
    height = abs(box['w']*math.sin(angle))+abs(box['h']*math.cos(angle))
    return {'x': box['x']+(box['w']-width)/2, 'y': box['y']+(box['h']-height)/2,
            'w': width, 'h': height}


def map_region(region, source_width, source_height, video, canvas):
    sx, sy = video['w']/source_width, video['h']/source_height
    if video.get('fit') != 'stretch':
        sx = sy = min(sx, sy) if video.get('fit', 'contain') == 'contain' else max(sx, sy)
    dx, dy = video['x']+(video['w']-source_width*sx)/2, video['y']+(video['h']-source_height*sy)/2
    box = {'x': dx+region['x']/100*source_width*sx, 'y': dy+region['y']/100*source_height*sy,
           'w': region['w']/100*source_width*sx, 'h': region['h']/100*source_height*sy}
    box = intersection(box, video)
    return intersection(box, canvas) if box else None


def validated_regions(response, frame_count, source_width, source_height, video, width, height):
    rows = response.get('regions') if isinstance(response, dict) else None
    if not isinstance(rows, list):
        raise ValueError('Модель не вернула области кадра для размещения баннера.')
    result = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get('frame')) is not int or not 0 <= row['frame'] < frame_count:
            continue
        if row.get('kind') not in {'face', 'subject', 'text'}:
            continue
        values = [row.get(k) for k in ('x', 'y', 'w', 'h')]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 100 for v in values):
            continue
        if row['w'] <= 0 or row['h'] <= 0 or row['x']+row['w'] > 100.01 or row['y']+row['h'] > 100.01:
            continue
        box = map_region(row, source_width, source_height, video, {'x': 0, 'y': 0, 'w': width, 'h': height})
        if box:
            # Leave a little breathing room around faces and existing text.
            pad = min(width, height)*.015
            result.append({'x': max(0, box['x']-pad), 'y': max(0, box['y']-pad),
                           'w': min(width, box['x']+box['w']+pad)-max(0, box['x']-pad),
                           'h': min(height, box['y']+box['h']+pad)-max(0, box['y']-pad)})
    if rows and not result:
        raise ValueError('Модель вернула некорректные области кадра. Баннер не изменён.')
    return result


def choose_layout(original, rules, requirements, protected, width, height, image_aspect=None):
    required = BANNER_SCHEMA['properties']['requirements']['required']
    if not isinstance(requirements, dict) or any(key not in requirements for key in required):
        raise ValueError('Модель не проверила ТЗ баннера. Размещение не применено.')
    for key in ('can_adapt', 'lock_size', 'lock_position'):
        if type(requirements[key]) is not bool:
            raise ValueError('Некорректные ограничения ТЗ баннера.')
    for key in ('min_width', 'min_height', 'max_width', 'max_height'):
        value = requirements[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 3840:
            raise ValueError('Некорректные размеры в ТЗ баннера.')
    zone = rules.zone
    requested_zone = requirements['zone']
    if requested_zone not in {'any', 'top', 'bottom', 'left', 'right'}:
        raise ValueError('Некорректная зона в ТЗ баннера.')
    if not requirements['can_adapt'] or (zone != 'any' and requested_zone not in {'any', zone}):
        return None
    if zone == 'any':
        zone = requested_zone
    movable, resizable = rules.movable and not requirements['lock_position'], rules.resizable and not requirements['lock_size']
    min_w = max(rules.min_width, requirements['min_width'])
    min_h = max(rules.min_height, requirements['min_height'])
    max_w, max_h = requirements['max_width'] or width, requirements['max_height'] or height
    sizes = [(original['w'], original['h'])]
    if resizable:
        # Scale the existing box uniformly: contain-fit preserves the banner image.
        floor = max(min_w/original['w'], min_h/original['h'], min(120, width*.15)/original['w'], min(36, height*.06)/original['h'])
        sizes += [(original['w']*factor, original['h']*factor) for factor in (1, .85, .7, .55) if factor >= floor]
        if floor < 1:
            sizes.append((original['w']*floor, original['h']*floor))
    candidates = []
    rotation = normalize_rotation(original.get('rotation', 0))
    for w, h in sizes:
        extent = rotated_bounds({'x': 0, 'y': 0, 'w': w, 'h': h, 'rotation': rotation})
        if (w < min_w-.001 or h < min_h-.001 or w > max_w+.001 or h > max_h+.001
                or extent['w'] > width+.001 or extent['h'] > height+.001):
            continue
        if image_aspect and original.get('fit', 'contain') == 'contain':
            visible_w = min(w, h*image_aspect)
            visible_h = visible_w/image_aspect
            if visible_w < min_w-.001 or visible_h < min_h-.001:
                continue
        dx, dy = -extent['x'], -extent['y']
        mx = min(width*.04, max(0, (width-extent['w'])/2))
        my = min(height*.04, max(0, (height-extent['h'])/2))
        positions = [(original['x'], original['y'])]
        if movable:
            positions += [(x, y) for x in (mx+dx, (width-w)/2, width-w-mx-dx)
                          for y in (my+dy, (height-h)/2, height-h-my-dy)]
            # Edge positions between corners help narrow banners fit around faces.
            positions += [(x, y) for x in (mx+dx, width-w-mx-dx)
                          for y in ((height-extent['h'])*.25+dy, (height-extent['h'])*.75+dy)]
        for x, y in positions:
            box = {'x': x, 'y': y, 'w': w, 'h': h, 'fit': original.get('fit', 'contain'), 'rotation': rotation}
            bounds = rotated_bounds(box)
            if bounds['x'] < -.001 or bounds['y'] < -.001 or bounds['x']+bounds['w'] > width+.001 or bounds['y']+bounds['h'] > height+.001:
                continue
            if (zone == 'top' and bounds['y']+bounds['h'] > height/2) or (zone == 'bottom' and bounds['y'] < height/2):
                continue
            if (zone == 'left' and bounds['x']+bounds['w'] > width/2) or (zone == 'right' and bounds['x'] < width/2):
                continue
            if any(intersection(bounds, rotated_bounds(region)) for region in protected):
                continue
            movement = math.hypot(x-original['x'], y-original['y'])/math.hypot(width, height)
            centrality = 1-min(1, math.hypot(x+w/2-width/2, y+h/2-height/2)/(math.hypot(width, height)/2))
            score = .2*movement+.15*centrality+.6*w*h/(width*height)
            candidates.append((score, box))
    if not candidates:
        return None
    return {key: round(value, 3) if isinstance(value, (int, float)) else value for key, value in min(candidates, key=lambda pair: pair[0])[1].items()}
