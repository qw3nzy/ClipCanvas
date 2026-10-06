"""Cancellable automatic editing; every edit is returned as a reversible project copy."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Literal
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from clipcanvas_asr import SpeechModel
from clipcanvas_composition import merged_cuts, retained_intervals
from clipcanvas_layout import rotated_bounds, BannerRules, fixed_center_geometry, intersection
from clipcanvas_studio import BoundaryAnalysis, BannerAdaptation, CaptionReview, formatted_cues
from clipcanvas_pro import BannerSchedule, MotionPoint


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class AutomaticOptions(Strict):
    banners: bool = True
    remove_background: bool = True
    remove_intro: bool = True
    remove_outro: bool = True
    subtitles: bool = False
    correct_subtitles: bool = True
    speech_model: SpeechModel = 'large-v3'
    language: Literal['auto', 'ru', 'en', 'es', 'de', 'fr', 'uk'] = 'auto'
    boundary_confidence: float = Field(default=.9, ge=.9, le=1)


class Automatic(Strict):
    project: dict
    options: AutomaticOptions = Field(default_factory=AutomaticOptions)
    client_snapshot: str | None = Field(default=None, max_length=2000)


class BannerTemplate(Strict):
    name: str = Field(min_length=1, max_length=70)
    asset_id: str
    geometry: dict
    width: int = Field(default=1080, ge=240, le=3840)
    height: int = Field(default=1920, ge=240, le=3840)
    chromakey: dict = Field(default_factory=dict)
    duration: float = Field(default=10, ge=.1, le=86400)
    loop: bool = True
    offset: float = Field(default=0, ge=0, le=86400)
    volume: float = Field(default=0, ge=0, le=1)
    speed: float = Field(default=1, ge=.25, le=4)
    instructions: str = Field(default='', max_length=4000)
    layout_rules: dict = Field(default_factory=dict)
    schedule: BannerSchedule = Field(default_factory=BannerSchedule)
    opacity: float = Field(default=1, ge=0, le=1)
    fade_in: float = Field(default=0, ge=0, le=10)
    fade_out: float = Field(default=0, ge=0, le=10)
    motion: list[MotionPoint] = Field(default_factory=list, max_length=100)
    locked: bool = False
    lock_aspect: bool = True


def safe_boundary_cuts(project, boundaries, options, duration):
    """Accept only evidence-backed results; no selected part may become empty."""
    cuts, report = deepcopy(project.get('cuts', [])), []
    for item in boundaries:
        kind = item.get('kind', item.get('type'))
        if kind not in {'intro', 'outro'} or not getattr(options, 'remove_'+kind):
            continue
        confidence = item.get('confidence', 0)
        start, end = item.get('start'), item.get('end')
        valid = (type(confidence) in (int, float) and math.isfinite(confidence)
                 and options.boundary_confidence <= confidence <= 1
                 and all(type(t) in (int, float) and math.isfinite(t) for t in (start, end))
                 and 0 <= start < end <= duration+.001 and end-start >= .099
                 and bool(item.get('evidence') or len(item.get('frame_evidence', [])) >= 2))
        row = {'step': kind, 'status': 'skipped', 'reason': 'Недостаточно подтверждений для автоматического удаления.',
               'proposal': deepcopy(item)}
        if valid:
            proposed = merged_cuts([*cuts, {'start': start, 'end': min(duration, end)}])
            if all(sum(p['duration'] for p in retained_intervals(part['start'], part['end'], proposed)) >= .099
                   for part in project['parts']):
                cuts = proposed
                row.update(status='applied', reason=item.get('reason') or 'Подтверждены границы заставки.')
            else:
                row['reason'] = 'Удаление оставило бы одну из выбранных частей без видео.'
        report.append(row)
    return cuts, report


def protected_box(geometry):
    """Account for the visible bounds of another rotated banner."""
    box = {key: geometry[key] for key in ('x', 'y', 'w', 'h')}
    angle = math.radians(geometry.get('rotation', 0))
    width = abs(box['w']*math.cos(angle))+abs(box['h']*math.sin(angle))
    height = abs(box['w']*math.sin(angle))+abs(box['h']*math.cos(angle))
    return {'x': box['x']+(box['w']-width)/2, 'y': box['y']+(box['h']-height)/2,
            'w': width, 'h': height}


def subtitle_regions(subtitles, canvas, start, end):
    """Protect centred caption blocks, including per-cue style and measured wrapping."""
    regions = []
    width, height = canvas['width'], canvas['height']
    for cue in subtitles['cues']:
        if cue['start'] >= end or cue['end'] <= start or not cue['text'].strip():
            continue
        style = {**subtitles, **{key: value for key, value in (cue.get('appearance') or {}).items() if value is not None}}
        box_w = width*style['width']/100
        size = max(style['size'], cue.get('render_font_size') or style['size'])
        lines = cue.get('render_lines')
        if not lines or ''.join(''.join(lines).split()) != ''.join(cue['text'].split()):
            lines = cue['text'].splitlines()
            count = sum(max(1, math.ceil(len(line)*size*.7/max(1, box_w))) for line in lines)
        else:
            count = len(lines)
        padding = style['outline']+style['shadow']+size*.18
        box_h = (max(1, count)-1)*style['size']*style['line_spacing']+size*1.4+padding*2
        if style['background'] and style['background_opacity'] > 0:
            box_h = max(box_h, max(1, count)*style['size']*style['line_spacing']+padding*2)
        x, y = width*style['x']/100, height*style['y']/100
        offset = cue.get('render_y_offset') or 0
        left, right = max(0, x-box_w/2-padding), min(width, x+box_w/2+padding)
        top, bottom = max(0, y-box_h/2+min(0, offset)), min(height, y+box_h/2+max(0, offset))
        if right <= left or bottom <= top:
            continue
        region = {'x': left, 'y': top, 'w': right-left, 'h': bottom-top}
        if region not in regions:
            regions.append(region)
    if len(regions) > 80:
        # A busy subtitle layout may be unplaceable; never silently drop protected text.
        left, top = min(r['x'] for r in regions), min(r['y'] for r in regions)
        right, bottom = max(r['x']+r['w'] for r in regions), max(r['y']+r['h'] for r in regions)
        regions = [{'x': left, 'y': top, 'w': right-left, 'h': bottom-top}]
    return regions


class AutomaticEditor:
    def __init__(self, context, studio):
        self.c, self.studio = context, studio

    def check(self, job_id):
        if self.c['cancelled'](job_id):
            raise self.c['Cancelled']()

    def job(self, job_id, request):
        render = self.c['Render'].model_validate(request.project)
        source, _ = self.c['validate_render'](render)
        project, options = render.model_dump(), request.options
        report = []
        self.c['update'](job_id, project=None, files=[], automatic_report=[], automatic_stage='starting')

        def record(step, status, reason, **values):
            report.append({'step': step, 'status': status, 'reason': str(reason), **values})
            self.c['update'](job_id, automatic_report=deepcopy(report))

        def run(step, callback, empty):
            self.check(job_id)
            self.c['update'](job_id, automatic_stage=step, **empty)
            try:
                callback()
                self.check(job_id)
                return self.c['get_job'](job_id)
            except self.c['Cancelled']:
                raise
            except (ValueError, RuntimeError, OSError, HTTPException) as error:
                self.check(job_id)
                record(step, 'skipped', getattr(error, 'detail', str(error)))
                return None

        if options.subtitles:
            if self.c['audio_index'](source, render.audio_stream_index) is None:
                record('subtitles', 'skipped', 'В видео нет звуковой дорожки. Существующие субтитры сохранены.')
            else:
                transcript = self.c['Transcribe'](source_id=render.source_id,
                    audio_stream_index=render.audio_stream_index, model=options.speech_model, language=options.language)
                result = run('subtitles', lambda: self.c['transcribe_job'](job_id, transcript), {'cues': []})
                cues = deepcopy(result.get('cues', [])) if result else []
                if cues:
                    project['subtitles'].update(enabled=True, cues=cues)
                    record('subtitles', 'applied', f'Речь распознана Whisper {options.speech_model}.', count=len(cues))
                    if options.correct_subtitles:
                        review = CaptionReview(source_id=render.source_id, audio_stream_index=render.audio_stream_index,
                                               cues=cues, mode='correct')
                        reviewed = run('subtitle-correction', lambda: self.studio.review_job(job_id, review), {'changes': []})
                        if reviewed:
                            changes = reviewed.get('changes', [])
                            for change in changes:
                                cues[change['index']] = deepcopy(change['cue'])
                            record('subtitle-correction', 'applied', 'AI проверил текст; тайм-коды сохранены.', count=len(changes))
                    project['subtitles']['cues'] = formatted_cues(cues, 32)
                elif result:
                    record('subtitles', 'skipped', 'Речь не найдена. Существующие субтитры сохранены.')

        if options.remove_intro or options.remove_outro:
            boundaries = BoundaryAnalysis(source_id=render.source_id, audio_stream_index=render.audio_stream_index,
                cues=project['subtitles']['cues'], transcribe=True, speech_model=options.speech_model,
                language=options.language, auto_remove=False)
            result = run('boundaries', lambda: self.studio.boundaries_job(job_id, boundaries), {'boundaries': []})
            if result:
                project['cuts'], rows = safe_boundary_cuts(project, result.get('boundaries', []), options, source['duration'])
                for row in rows:
                    record(**row)
                if not rows:
                    record('boundaries', 'skipped', 'Подтверждённые интро или аутро не найдены.')

        banners = [project['banner'], *project['banners']]
        selected = [banner for banner in banners if banner['enabled']]
        if (options.banners or options.remove_background) and not selected:
            record('banners', 'skipped', 'Добавьте готовый баннер из библиотеки или загрузите видеобаннер.')
        for index, banner in enumerate(selected):
            self.check(job_id)
            identity = {'banner_id': banner.get('id'), 'asset_id': banner['asset_id'], 'name': banner['name']}
            if options.remove_background:
                if banner['chromakey']['enabled']:
                    record('background', 'kept', 'Сохранён выбранный цвет удаления фона.', **identity)
                else:
                    from clipcanvas_composition import estimate_banner_key
                    self.c['update'](job_id, automatic_stage='background', message=f'Проверка фона: {banner["name"]}…')
                    try:
                        key = estimate_banner_key(self.c['asset'](banner['asset_id'], 'banner')['path'],
                                                  cancelled=lambda: self.c['cancelled'](job_id))
                        self.check(job_id)
                        if key.get('chromakey'):
                            banner['chromakey'] = key['chromakey']
                        record('background', 'applied' if key.get('chromakey') else 'skipped',
                               key['reason'], confidence=key.get('confidence', 0), **identity)
                    except (ValueError, RuntimeError, OSError) as error:
                        self.check(job_id)
                        record('background', 'skipped', str(error), **identity)
            if not banner.get('locked'):
                centered = fixed_center_geometry(banner['geometry'], BannerRules.model_validate(banner['layout_rules']),
                    project['canvas']['width'], project['canvas']['height'], project['video'], banner['instructions'])
                if centered:
                    banner['geometry'] = centered
                    # Exact positioning excludes movement/scale, retaining opacity keyframes.
                    banner['motion'] = [{'time':point['time'],'opacity':point.get('opacity',1)} for point in banner['motion']]
                    banner['layout_rules']['zone'] = 'center'
                    sub = project['subtitles']
                    protected = [protected_box(other['geometry']) for other in selected if other is not banner]
                    if sub['enabled'] and sub['cues']:
                        protected.extend(subtitle_regions(sub, project['canvas'], 0, source['duration']))
                    overlap = any(intersection(rotated_bounds(centered), rotated_bounds(box)) for box in protected)
                    reason = 'Баннер расположен точно по центру '+('области видео.' if banner['layout_rules'].get('center_reference')=='video' else 'холста.')
                    if overlap:
                        reason += ' Есть пересечение с текстом или другим баннером; центр сохранён по вашему ТЗ, проверьте результат.'
                    record('banner-layout','applied',reason,fixed_position=True,**identity)
                    continue
            if not options.banners:
                continue
            if banner.get('locked'):
                record('banner-layout', 'kept', 'Положение заблокировано пользователем.', **identity)
                continue
            scheduled = banner.get('schedule', {}).get('mode', 'source') != 'source'
            if banner['timebase'] != 'source' and not scheduled:
                record('banner-layout', 'skipped', 'Для анализа кадров переключите привязку баннера на исходник.', **identity)
                continue
            analysis_start, analysis_end = (0, source['duration']) if scheduled else (banner['start'], min(banner['end'], source['duration']))
            spans = retained_intervals(analysis_start, analysis_end, project['cuts'])
            windows = [(start, min(span['end'], start+180)) for span in spans
                       for start in (span['start']+180*i for i in range(math.ceil(span['duration']/180)))]
            if not windows or len(windows) > 24:
                record('banner-layout', 'skipped', 'Не найден проверяемый диапазон до 72 минут для баннера.', **identity)
                continue
            protected = [protected_box(other['geometry']) for other in selected if other is not banner
                         and (other['timebase'] != 'source' or other['start'] < banner['end'] and other['end'] > banner['start'])]
            sub = project['subtitles']
            if sub['enabled'] and sub['cues']:
                protected.extend(subtitle_regions(sub, project['canvas'], analysis_start, analysis_end))
            if len(protected) > 120:
                record('banner-layout', 'skipped', 'Слишком много защищённых областей; положение сохранено.', **identity)
                continue
            proposal = None
            for start, end in windows:
                adaptation = BannerAdaptation(source_id=render.source_id, audio_stream_index=render.audio_stream_index,
                    banner_id=banner.get('id') or f'automatic-{index}', asset_id=banner['asset_id'],
                    width=project['canvas']['width'], height=project['canvas']['height'], video=project['video'],
                    original=banner['geometry'], start=start, end=end, instructions=banner['instructions'],
                    rules=banner['layout_rules'], protected_regions=protected)
                result = run('banner-layout', lambda: self.studio.banner_job(job_id, adaptation), {'geometry': None})
                if not result or not result.get('geometry'):
                    if result:
                        record('banner-layout', 'skipped', result.get('message', 'Свободное место не найдено.'), **identity)
                    proposal = None
                    break
                proposal = deepcopy(result['geometry'])
                protected = deepcopy(result.get('protected_regions', protected))
                if len(protected) > 120:
                    record('banner-layout', 'skipped', 'Слишком много защищённых областей; положение сохранено.', **identity)
                    proposal = None
                    break
            if proposal:
                dx, dy = proposal['x']-banner['geometry']['x'], proposal['y']-banner['geometry']['y']
                for point in banner.get('motion', []):
                    if point.get('x') is not None:
                        point['x'] += dx
                    if point.get('y') is not None:
                        point['y'] += dy
                banner['geometry'].update(proposal)
                record('banner-layout', 'applied', 'Место выбрано с учётом кадров, субтитров и других баннеров.', **identity)

        self.check(job_id)
        finished = self.c['Render'].model_validate(project)
        self.c['validate_render'](finished)
        files = []
        if any(row['step'] == 'subtitles' and row['status'] == 'applied' for row in report):
            self.c['write_srt'](self.c['DATA']/'jobs'/job_id/'subtitles.srt', project['subtitles']['cues'])
            files.append(self.c['file_record'](job_id, 'subtitles.srt'))
        self.c['update'](job_id, project=finished.model_dump(), source_id=render.source_id,
                         client_snapshot=request.client_snapshot, automatic_report=report, automatic_stage='complete',
                         cues=project['subtitles']['cues'], files=files,
                         progress=99, message='Автоматический монтаж готов. Все изменения доступны для отмены.')


def register_automatic_routes(app, context, studio):
    editor = AutomaticEditor(context, studio)

    @app.post('/api/ai/automatic')
    def automatic(request: Automatic):
        try:
            render = context['Render'].model_validate(request.project)
        except ValueError as error:
            raise HTTPException(422, 'Проверьте настройки проекта: '+str(error)) from error
        context['validate_render'](render)
        return context['create_job'](editor.job, request, 'automatic')

    context['register_job_type']('automatic', Automatic, editor.job)
    register_banner_templates(app, context)
    return editor


def register_banner_templates(app, context):
    directory = context['DATA']/'banner-templates'
    directory.mkdir(exist_ok=True)

    def with_media(row):
        media = context['asset'](row['asset_id'], 'banner')
        return {**row, 'media_type': media.get('media_type', 'image'),
                'media_duration': media.get('duration', 0), 'audio':bool(media.get('audio')), 'speed':row.get('speed',1), 'loop': row.get('loop', True),
                'offset': row.get('offset', 0), 'volume': row.get('volume', 0)}

    @app.get('/api/banner-templates')
    def templates():
        result = []
        for path in directory.glob('*.json'):
            try:
                row = json.loads(path.read_text('utf-8'))
                result.append(with_media(row))
            except (OSError, ValueError, KeyError, HTTPException):
                continue
        return {'templates': sorted(result, key=lambda row: row['name'].casefold())}

    @app.post('/api/banner-templates')
    def save_template(request: BannerTemplate):
        return persist_template(request)

    @app.put('/api/banner-templates/{template_id}')
    def update_template(template_id: str, request: BannerTemplate):
        identity = context['identifier'](template_id)
        if not (directory/(identity+'.json')).is_file():
            raise HTTPException(404, 'Шаблон не найден.')
        return persist_template(request, identity)

    def persist_template(request, template_id=None):
        if not request.name.strip():
            raise HTTPException(422, 'Укажите название шаблона.')
        media = context['asset'](request.asset_id, 'banner')
        if media.get('media_type') == 'video' and request.offset >= media.get('duration', 0):
            raise HTTPException(422, 'Начало видеобаннера должно быть внутри его длительности.')
        try:
            banner = context['Banner'].model_validate({'enabled': True, 'asset_id': request.asset_id,
                'start': 0, 'end': request.duration, 'geometry': request.geometry, 'chromakey': request.chromakey,
                'instructions': request.instructions, 'layout_rules': request.layout_rules,
                'loop': request.loop, 'offset': request.offset, 'volume': request.volume, 'speed':request.speed,
                **{key:getattr(request,key) for key in ('schedule','opacity','fade_in','fade_out','motion','locked','lock_aspect')}})
            geometry = banner.geometry
            if geometry.w > request.width*4 or geometry.h > request.height*4:
                raise ValueError('Слой слишком велик для выбранного холста.')
            visible = rotated_bounds(geometry.model_dump())
            if (visible['x']+visible['w'] <= 0 or visible['y']+visible['h'] <= 0
                    or visible['x'] >= request.width or visible['y'] >= request.height):
                raise ValueError('Баннер должен находиться хотя бы частично внутри холста.')
        except ValueError as error:
            raise HTTPException(422, 'Некорректные настройки баннера: '+str(error)) from error
        result = {**request.model_dump(), 'id': template_id or uuid.uuid4().hex, 'name': request.name.strip(),
                  'geometry': banner.geometry.model_dump(), 'chromakey': banner.chromakey.model_dump(),
                  'layout_rules': banner.layout_rules.model_dump()}
        result = with_media(result)
        with context['LOCK']:
            context['atomic_json'](directory/(result['id']+'.json'), result)
        return result

    @app.delete('/api/banner-templates/{template_id}')
    def delete_template(template_id: str):
        with context['LOCK']:
            (directory/(context['identifier'](template_id)+'.json')).unlink(missing_ok=True)
        return {'status': 'deleted'}
