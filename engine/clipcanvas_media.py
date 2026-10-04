"""Pure timeline and caption helpers; importing this module never starts jobs."""
from __future__ import annotations
import html
import math
import re


def seconds(value: str) -> float:
    items = value.strip().replace(',', '.').split(':')
    if not 1 <= len(items) <= 3:
        raise ValueError('Некорректное время субтитров.')
    result = 0.0
    for item in items:
        result = result * 60 + float(item)
    if not math.isfinite(result) or result < 0:
        raise ValueError('Некорректное время субтитров.')
    return result


def plain_text(value: str) -> str:
    value = re.sub(r'\{[^}]*\}', '', value)
    value = value.replace(r'\N', '\n').replace(r'\n', '\n').replace(r'\h', ' ')
    return html.unescape(re.sub(r'<[^>]*>', '', value)).strip()


def parse_subtitles(text: str, extension: str = '.srt') -> list[dict]:
    text = text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    cues = []
    if extension.lower() in {'.ass', '.ssa'} or '[Events]' in text:
        fields = ['layer', 'start', 'end', 'style', 'name', 'marginl', 'marginr', 'marginv', 'effect', 'text']
        in_events = False
        for line in text.splitlines():
            if line.startswith('['):
                in_events = line.lower() == '[events]'
            elif in_events and line.lower().startswith('format:'):
                fields = [x.strip().lower() for x in line.split(':', 1)[1].split(',')]
            elif in_events and line.lower().startswith('dialogue:'):
                row = dict(zip(fields, line.split(':', 1)[1].lstrip().split(',', len(fields)-1)))
                if all(x in row for x in ('start', 'end', 'text')):
                    cues.append({'start': seconds(row['start']), 'end': seconds(row['end']), 'text': plain_text(row['text'])})
    else:
        timing = re.compile(r'^\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d+)\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d+)')
        lines = text.split('\n')
        i = 0
        while i < len(lines):
            found = timing.match(lines[i])
            if not found:
                i += 1
                continue
            start, end = map(seconds, found.groups())
            i += 1
            body = []
            while i < len(lines) and lines[i].strip():
                body.append(lines[i])
                i += 1
            cues.append({'start': start, 'end': end, 'text': plain_text('\n'.join(body))})
    if not cues:
        raise ValueError('В файле не найдены текстовые субтитры SRT, VTT или ASS.')
    return normalize_cues(cues)


def normalize_cues(cues: list[dict], duration: float | None = None) -> list[dict]:
    """Produce one active caption at a time, merging simultaneous dialogue."""
    clean = []
    for cue in cues:
        start, end = float(cue['start']), float(cue['end'])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError('Некорректный интервал субтитров.')
        if duration is not None:
            end = min(end, duration)
        value = str(cue['text']).strip()
        if not value or end <= start:
            continue
        words = []
        for word in cue.get('words', []):
            a, b = max(start, float(word['start'])), min(end, float(word['end']))
            if math.isfinite(a) and math.isfinite(b) and b > a:
                words.append({'start': a, 'end': b, 'text': str(word.get('text', word.get('word', ''))), **({'probability': word['probability']} if word.get('probability') is not None else {})})
        clean.append({'start': start, 'end': end, 'text': value, 'words': words,
                      'timing_quality': cue.get('timing_quality', 'word' if words else 'phrase')})
    if not clean:
        return []
    events = []
    for i, cue in enumerate(clean):
        events.extend([(cue['start'], 1, i), (cue['end'], -1, i)])
    events.sort()
    active, result, previous = set(), [], events[0][0]
    i = 0
    while i < len(events):
        moment = events[i][0]
        if moment > previous and active:
            selected = [clean[n] for n in sorted(active)]
            value = '\n'.join(dict.fromkeys(x['text'] for x in selected))
            words = selected[0]['words'] if len(selected) == 1 else []
            words = [{**w, 'start': max(previous, w['start']), 'end': min(moment, w['end'])}
                     for w in words if w['end'] > previous and w['start'] < moment]
            item = {'start': previous, 'end': moment, 'text': value, 'words': words,
                    'timing_quality': selected[0]['timing_quality'] if len(selected) == 1 else 'phrase'}
            if result and result[-1]['text'] == value and not result[-1]['words'] and not words and abs(result[-1]['end']-previous) < 1e-6:
                result[-1]['end'] = moment
            else:
                result.append(item)
        while i < len(events) and events[i][0] == moment:
            _, sign, number = events[i]
            active.add(number) if sign == 1 else active.discard(number)
            i += 1
        previous = moment
    return result


def timeline(start: float, end: float, ad_duration: float = 0, ad_start: float = 20,
             repeat: bool = True, interval: float = 60) -> list[dict]:
    if end <= start:
        raise ValueError('Некорректный отрезок.')
    if ad_duration and (ad_start >= end-start or ad_start < 0):
        raise ValueError('Реклама должна начинаться до конца каждой части. Уменьшите время вставки или увеличьте часть.')
    if ad_duration and repeat and interval <= ad_duration + .099:
        raise ValueError('Интервал повторения должен быть длиннее рекламы минимум на 0,1 секунды.')
    result, cursor, output, next_ad = [], start, 0.0, ad_start
    while cursor < end-1e-8:
        length = end-cursor if not ad_duration else min(end-cursor, max(0, next_ad-output))
        if length > 1e-8:
            result.append({'kind': 'source', 'start': cursor, 'end': cursor+length,
                           'output_start': output, 'duration': length})
            cursor += length
            output += length
        if cursor >= end-1e-8:
            break
        if len(result) > 2000:
            raise ValueError('Слишком много рекламных вставок в одной части.')
        result.append({'kind': 'advert', 'start': 0, 'end': ad_duration,
                       'output_start': output, 'duration': ad_duration, 'source_position': cursor})
        output += ad_duration
        if repeat:
            next_ad += interval
        else:
            ad_duration = 0
    return result


def captions_for_timeline(cues: list[dict], plan: list[dict]) -> list[dict]:
    result = []
    for segment in plan:
        if segment['kind'] != 'source':
            continue
        shift = segment['output_start']-segment['start']
        for cue in cues:
            a, b = max(cue['start'], segment['start']), min(cue['end'], segment['end'])
            if b <= a:
                continue
            words = [{**w, 'start': max(a, w['start'])+shift, 'end': min(b, w['end'])+shift}
                     for w in cue.get('words', []) if w['end'] > a and w['start'] < b]
            result.append({**cue, 'start': a+shift, 'end': b+shift, 'words': words})
    return result


def subtitle_recommendation(tracks: list[dict], language: str | None) -> int | None:
    candidates = [t for t in tracks if t['supported'] and not t.get('forced')]
    aliases = {'ru': 'rus', 'en': 'eng', 'uk': 'ukr', 'de': 'deu', 'fr': 'fra', 'es': 'spa'}
    lang = aliases.get(language, language)
    matching = [t for t in candidates if t.get('language') == lang]
    if lang and not matching:
        return None
    candidates = matching if matching else candidates
    if not candidates:
        return None
    return max(candidates, key=lambda t: (not t.get('hearing_impaired'), t.get('default', False), -t['index']))['index']
