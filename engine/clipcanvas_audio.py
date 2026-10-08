"""Audio-only inventory and clip-relative soundtrack timing."""
import math
import av
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SoundtrackSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    enabled: bool = False
    asset_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    volume: float = Field(default=1, ge=0, le=1)
    start: float = Field(default=0, ge=0, le=86400)
    offset: float = Field(default=0, ge=0, le=86400)
    loop: bool = False
    pause_for_ads: bool = True

    @model_validator(mode='after')
    def required(self):
        if self.enabled and not self.asset_id:
            raise ValueError('Загрузите файл для своей звуковой дорожки.')
        return self


def probe_audio(path):
    with av.open(str(path)) as container:
        streams = list(container.streams.audio)
        if not streams:
            raise ValueError('В файле нет звуковой дорожки.')
        selected = next((s for s in streams if int(s.disposition.value) & 1), streams[0])
        duration = float(selected.duration*selected.time_base) if selected.duration else 0
        if not duration:
            duration = container.duration/av.time_base if container.duration else 0
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('Не удалось определить длительность звука.')
        audio = [{'index':s.index,'codec':s.codec_context.name,'channels':s.codec_context.channels,
            'language':s.metadata.get('language','und'),'default':bool(int(s.disposition.value)&1),
            'start_time':float(s.start_time*s.time_base) if s.start_time is not None else 0,
            'time_base':str(s.time_base)} for s in streams]
        return {'media_type':'audio','duration':duration,'audio':True,'audio_streams':audio,
            'recommended_audio_stream_index':selected.index,'subtitle_streams':[],
            'container_start_time':container.start_time/av.time_base if container.start_time is not None else 0,
            'inventory_version':2}


def soundtrack_elapsed(settings, plan, clock):
    elapsed = clock-settings.start
    if settings.pause_for_ads:
        elapsed -= sum(max(0,min(clock,s['output_start']+s['duration'])-max(settings.start,s['output_start']))
            for s in plan if s['kind'] == 'advert')
    return elapsed


def soundtrack_window(settings, duration, segment):
    if not settings.enabled or settings.volume == 0 or (settings.pause_for_ads and segment['kind'] == 'advert'):
        return None
    elapsed = segment.get('soundtrack_elapsed',segment['output_start']-settings.start)
    delay = max(0,-elapsed)
    position = settings.offset+max(0,elapsed)
    if settings.loop:
        position %= duration
    length = segment['duration']-delay
    if not settings.loop:
        length = min(length,duration-position)
    if length <= .000001:
        return None
    return {'position':position,'delay':delay,'duration':length}
