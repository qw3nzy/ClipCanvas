"""Shared speech recognition with visible device selection and CUDA fallback."""
from __future__ import annotations

import ctypes
from functools import lru_cache
import gc
import os
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
SpeechModel = Literal['tiny', 'base', 'small', 'medium', 'large-v3', 'turbo']
MODELS = ('tiny', 'base', 'small', 'medium', 'large-v3', 'turbo')
_DLL_HANDLES = []


@lru_cache(maxsize=1)
def hardware_status():
    result = {'cuda_available': False, 'devices': 0, 'compute_types': [], 'error': None}
    try:
        # Ollama already ships CUDA 12 cuBLAS. Keep directory handles alive for
        # lazy CTranslate2/cuDNN loading, without modifying the Windows install.
        if os.name == 'nt':
            directories = [ROOT/'.runtime/ollama/lib/ollama/cuda_v12', ROOT/'.runtime/deps/ctranslate2', ROOT/'.runtime/cuda/bin',
                           ROOT/'.runtime/deps/nvidia/cublas/bin', ROOT/'.runtime/deps/nvidia/cudnn/bin']
            for directory in directories:
                if directory.is_dir():
                    _DLL_HANDLES.append(os.add_dll_directory(str(directory)))
                    os.environ['PATH'] = str(directory)+os.pathsep+os.environ.get('PATH', '')
            for name in ('cublas64_12.dll', 'cublasLt64_12.dll', 'cudnn64_9.dll'):
                _DLL_HANDLES.append(ctypes.WinDLL(name))
        import ctranslate2
        result['devices'] = ctranslate2.get_cuda_device_count()
        if result['devices']:
            result['compute_types'] = sorted(ctranslate2.get_supported_compute_types('cuda'))
            result['cuda_available'] = bool({'float16', 'int8_float16'} & set(result['compute_types']))
        if not result['cuda_available']:
            result['error'] = 'Совместимая NVIDIA CUDA не обнаружена. Доступно распознавание на CPU.'
    except (ImportError, OSError, RuntimeError):
        result['error'] = 'Для Whisper нужны CUDA 12/cuBLAS и cuDNN 9. Проверьте библиотеки NVIDIA; CPU доступен.'
    return result


def status(model_root=None):
    root = Path(model_root or ROOT/'data/models')
    # Inspect only complete cached snapshots; a partly downloaded model is not installed.
    installed = []
    for name in MODELS:
        repository = 'models--mobiuslabsgmbh--faster-whisper-large-v3-turbo' if name == 'turbo' else 'models--Systran--faster-whisper-'+name
        cached = root/repository/'snapshots'
        if cached.is_dir() and any((p/'model.bin').is_file() and (p/'config.json').is_file() for p in cached.iterdir()):
            installed.append(name)
    return {**hardware_status(), 'models': list(MODELS), 'installed_models': installed}


def recognize(audio_path, model_name, language, consume, download_root, report, device='auto'):
    if device not in {'auto', 'cpu', 'cuda'}:
        raise ValueError('Устройство речи: Авто, NVIDIA или CPU.')
    available = hardware_status() if device != 'cpu' else {}
    selected = 'cuda' if device != 'cpu' and available.get('cuda_available') else 'cpu'
    if device == 'cuda' and selected != 'cuda':
        raise RuntimeError(available.get('error') or 'NVIDIA CUDA недоступна. Выберите Авто или CPU.')
    fallback = available.get('error') if device == 'auto' and selected == 'cpu' else None
    from faster_whisper import WhisperModel
    for attempt in range(2):
        model = None
        try:
            label = 'NVIDIA CUDA' if selected == 'cuda' else 'CPU'
            report(asr_device=selected, asr_model=model_name, asr_fallback=fallback, stage='asr',
                   message=f'Whisper {model_name} · {label}: загрузка модели речи…'+(' '+fallback if fallback else ''))
            types = available.get('compute_types', [])
            compute = ('int8_float16' if 'int8_float16' in types else 'float16') if selected == 'cuda' else 'int8'
            model = WhisperModel(model_name, device=selected, compute_type=compute,
                                 cpu_threads=min(8, os.cpu_count() or 4), download_root=str(download_root))
            segments, info = model.transcribe(str(audio_path), language=None if language == 'auto' else language,
                                             beam_size=5, vad_filter=True, word_timestamps=True)
            # Iteration executes the GPU kernels, so fallback must cover consume, too.
            cues = consume(segments)
            return cues, info.language
        except (OSError, RuntimeError) as error:
            gpu_error = any(word in str(error).lower() for word in ('cuda', 'cudnn', 'cublas', 'gpu', 'out of memory'))
            if selected != 'cuda' or not gpu_error:
                raise
            if device == 'cuda':
                raise RuntimeError('Whisper не смог выполнить запрос на NVIDIA. Проверьте CUDA/cuDNN и свободную видеопамять; можно выбрать Авто.') from error
            if attempt:
                raise
            selected, fallback = 'cpu', 'NVIDIA недоступна для этого запроса; продолжаем на CPU.'
        finally:
            if model is not None:
                try:
                    model.model.unload_model()
                except (AttributeError, RuntimeError):
                    pass
                del model
                gc.collect()
