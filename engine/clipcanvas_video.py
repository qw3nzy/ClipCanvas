"""Source-only video adjustments shared by rendering and saved projects."""
import uuid
from pydantic import BaseModel, ConfigDict, Field, model_validator


class VideoCrop(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    enabled: bool = False
    x: float = Field(default=0, ge=0, le=.99)
    y: float = Field(default=0, ge=0, le=.99)
    w: float = Field(default=1, ge=.01, le=1)
    h: float = Field(default=1, ge=.01, le=1)

    @model_validator(mode='after')
    def bounds(self):
        if self.x+self.w > 1.000000001 or self.y+self.h > 1.000000001:
            raise ValueError('Область кадрирования должна находиться внутри исходного видео.')
        return self


def video_crop_filter(crop: VideoCrop):
    if not crop.enabled or (crop.x == 0 and crop.y == 0 and crop.w == 1 and crop.h == 1):
        return ''
    return (f"crop=w='max(2,floor(iw*{crop.w:.9f}))':h='max(2,floor(ih*{crop.h:.9f}))':"
            f"x='min(iw-ow,floor(iw*{crop.x:.9f}))':y='min(ih-oh,floor(ih*{crop.y:.9f}))':exact=1")


class VideoBlur(VideoCrop):
    enabled: bool = True
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, max_length=100)
    name: str = Field(default='Область размытия', max_length=70)
    strength: float = Field(default=50, ge=1, le=100)
    start: float = Field(default=0, ge=0, le=86400)
    end: float | None = Field(default=None, gt=0, le=86400)

    @model_validator(mode='after')
    def interval(self):
        if self.end is not None and self.end <= self.start:
            raise ValueError('Конец размытия должен быть позже начала.')
        return self


def video_blur_filters(label, regions, out, width, height, *, start=0, duration=None, frozen_time=None):
    """Blur source rectangles before cropping; timing stays on the source clock."""
    active = [region for region in regions if region.enabled]
    if frozen_time is not None:
        active = [r for r in active if r.start <= frozen_time and (r.end is None or frozen_time < r.end)]
    elif duration is not None:
        active = [r for r in active if r.start < start+duration and (r.end is None or r.end > start)]
    if not active:
        return [f'[{label}]null[{out}]']
    filters = [f'[{label}]format=rgba[{out}raw]']
    previous = out+'raw'
    for index, region in enumerate(active):
        prefix = f'{out}{index}'
        filters.append(f'[{previous}]split=2[{prefix}base][{prefix}patch]')
        crop = video_crop_filter(region)
        sigma = max(.1, region.strength*.001*min(width, height))
        filters.append(f'[{prefix}patch]'+(crop+',' if crop else '')+
                       f'gblur=sigma={sigma:.6f}:steps=3[{prefix}blur]')
        clock = f'(t+{start:.6f})'
        enabled = f'gte({clock},{region.start:.6f})'
        if region.end is not None:
            enabled += f'*lt({clock},{region.end:.6f})'
        timing = f":enable='{enabled}'" if frozen_time is None else ''
        target = out if index == len(active)-1 else prefix+'result'
        filters.append(f'[{prefix}base][{prefix}blur]overlay='
            f"x='min(main_w-overlay_w,floor(main_w*{region.x:.9f}))':"
            f"y='min(main_h-overlay_h,floor(main_h*{region.y:.9f}))':"
            f'format=auto:shortest=1{timing}[{target}]')
        previous = target
    return filters


class VideoEffects(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    mirror: bool = False
    brightness: float = Field(default=1, ge=0, le=2)
    contrast: float = Field(default=1, ge=0, le=2)
    saturation: float = Field(default=1, ge=0, le=2)


def video_effect_filters(effects: VideoEffects):
    filters = ['hflip'] if effects.mirror else []
    # Match Canvas brightness/contrast/saturate in RGB, including clipping
    # between stages. Neutral projects skip conversions and retain old output.
    if effects.brightness != 1 or effects.contrast != 1 or effects.saturation != 1:
        filters.append('format=rgba')
    for expression in ([f'clip(val*{effects.brightness:.6f},0,255)'] if effects.brightness != 1 else []) + (
            [f'clip((val-127.5)*{effects.contrast:.6f}+127.5,0,255)'] if effects.contrast != 1 else []):
        filters.append('lutrgb='+':'.join(f"{channel}='{expression}'" for channel in 'rgb'))
    if effects.saturation != 1:
        amount = effects.saturation
        weights = (.213, .715, .072)
        coefficients = []
        for row, output in enumerate('rgb'):
            for column, source in enumerate('rgb'):
                value = weights[column]*(1-amount)+(amount if row == column else 0)
                coefficients.append(f'{output}{source}={value:.6f}')
        filters.append('colorchannelmixer='+':'.join(coefficients))
    return ','.join(filters)
