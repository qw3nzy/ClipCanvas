"""Validate/install app-local dependencies without trusting a stale ready flag."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPS = ROOT / '.runtime' / 'deps'
MARKER = ROOT / '.runtime' / 'dependencies.json'
sys.path.insert(0, str(DEPS))


def fingerprint():
    files = [ROOT / 'engine' / 'requirements.txt']
    lock = ROOT / 'engine' / 'requirements-lock.txt'
    if lock.exists():
        files.append(lock)
    return {'schema': 2, 'python': platform.python_version(),
            'architecture': platform.machine(), 'cache_tag': sys.implementation.cache_tag,
            'requirements': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}


def validate_installed():
    requirements = ROOT / 'engine' / 'requirements-lock.txt'
    if not requirements.exists():
        requirements = ROOT / 'engine' / 'requirements.txt'
    distributions = {d.metadata['Name'].lower().replace('_', '-'): d.version
                     for d in importlib.metadata.distributions(path=[str(DEPS)])}
    for line in requirements.read_text('utf-8-sig').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^ ;]+)', line)
        if not match:
            raise RuntimeError('Expected a pinned dependency: ' + line)
        name, version = match.groups()
        if distributions.get(name.lower().replace('_', '-')) != version:
            raise RuntimeError('Missing or different dependency: ' + name + '==' + version)
    import fastapi, uvicorn, multipart, faster_whisper, av, imageio_ffmpeg, yt_dlp, yt_dlp_ejs
    if not Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file():
        raise RuntimeError('The bundled FFmpeg executable is missing.')
    if not (DEPS / 'bin' / 'deno.exe').is_file():
        raise RuntimeError('The bundled Deno executable is missing.')


def record():
    validate_installed()
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    temporary = MARKER.with_suffix('.tmp')
    temporary.write_text(json.dumps(fingerprint(), indent=2), 'utf-8')
    os.replace(temporary, MARKER)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Read-only validation; no downloads.')
    parser.add_argument('--record', action='store_true', help='Adopt verified installed dependencies; no downloads.')
    parser.add_argument('--pip-wheel', help='Verified pip wheel for a portable interpreter without pip.')
    args = parser.parse_args()
    if args.check:
        try:
            if json.loads(MARKER.read_text('utf-8')) != fingerprint():
                raise RuntimeError('Dependency fingerprint has changed.')
            validate_installed()
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            print('Dependencies need validation: ' + str(exc), file=sys.stderr)
            return 2
        return 0
    if args.record:
        try:
            record()
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            print(str(exc), file=sys.stderr)
            return 3
        print('Existing dependencies verified; no downloads.')
        return 0
    if args.pip_wheel:
        sys.path.insert(0, str(Path(args.pip_wheel).resolve()))
    original_mkdir = os.mkdir
    def inherited_mkdir(path, mode=0o777, *, dir_fd=None):
        return original_mkdir(path, 0o777 if os.name == 'nt' and mode == 0o700 else mode, dir_fd=dir_fd)
    os.mkdir = inherited_mkdir
    requirements = ROOT / 'engine' / 'requirements-lock.txt'
    if not requirements.exists():
        requirements = ROOT / 'engine' / 'requirements.txt'
    sys.argv = ['pip', 'install', '--target', str(DEPS), '--upgrade', '--no-cache-dir',
                '--disable-pip-version-check', '-r', str(requirements)]
    try:
        runpy.run_module('pip', run_name='__main__')
    except SystemExit as exc:
        if exc.code not in (None, 0):
            return int(exc.code)
    record()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
