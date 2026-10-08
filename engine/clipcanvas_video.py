"""Source-only video adjustments shared by rendering and saved projects."""
from pydantic import BaseModel, ConfigDict, Field


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
