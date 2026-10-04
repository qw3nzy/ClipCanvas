"""H.264 encoder selection; probe the actual GPU rather than the build flags."""
import functools
import os
import subprocess


@functools.lru_cache(maxsize=1)
def available_encoders(ffmpeg):
    command = [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error',
               '-f', 'lavfi', '-i', 'color=c=black:s=256x256:r=30',
               '-frames:v', '2', '-an', *encoding_arguments('nvidia', 'standard'),
               '-f', 'null', '-']
    try:
        result = subprocess.run(command, capture_output=True, timeout=4,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode == 0:
            return ('cpu', 'nvidia')
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ('cpu',)


def select_encoder(requested, available):
    if requested == 'auto':
        return 'nvidia' if 'nvidia' in available else 'cpu'
    if requested not in available:
        raise ValueError('Кодирование NVIDIA недоступно. Выберите «Авто» или «Процессор».')
    return requested


def encoding_arguments(encoder, quality):
    high = quality == 'high'
    if encoder == 'nvidia':
        return ['-c:v', 'h264_nvenc', '-preset', 'p6' if high else 'p4',
                '-tune', 'hq', '-rc', 'vbr', '-cq', '18' if high else '23',
                '-b:v', '0', '-profile:v', 'high', '-bf', '3']
    return ['-c:v', 'libx264', '-preset', 'medium' if high else 'veryfast',
            '-crf', '18' if high else '23', '-threads', str(min(8, os.cpu_count() or 4))]
