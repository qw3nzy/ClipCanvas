"""Install the pinned standalone Windows runtime and local vision model."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import zipfile

from clipcanvas_ai import ROOT, RECOMMENDED_MODEL, ADDRESS, ensure_server, load_config, save_config

VERSION = 'v0.35.1'
SHA256 = 'dc50b9ca7f9023c86525012632cd1615b093d0407987444a7f62ecab617e8e93'


def install():
    model = os.environ.get('CLIPCANVAS_AI_INSTALL_MODEL', RECOMMENDED_MODEL)
    runtime = ROOT/'.runtime'
    runtime.mkdir(exist_ok=True)
    archive = runtime/'ollama-package.zip'
    executable = runtime/'ollama/ollama.exe'
    if not executable.is_file():
        if not archive.is_file():
            print('Downloading Ollama 0.35.1 (1.5 GB)…', flush=True)
            temporary = archive.with_suffix('.download')
            urllib.request.urlretrieve(f'https://github.com/ollama/ollama/releases/download/{VERSION}/ollama-windows-amd64.zip', temporary)
            temporary.replace(archive)
        with archive.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != SHA256:
            raise RuntimeError('Ollama SHA256 mismatch. Remove .runtime/ollama-package.zip and retry.')
        destination = runtime/'ollama'
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                if not (destination/entry.filename).resolve().is_relative_to(destination.resolve()):
                    raise RuntimeError('Unsafe archive path')
            package.extractall(destination)
    config = load_config(private=True)
    ensure_server({**config, 'provider': 'ollama', 'base_url': ADDRESS, 'local': True})
    print('Downloading '+model+' (9B: about 6.6 GB; 4B: about 3.4 GB)…', flush=True)
    subprocess.run([str(executable), 'pull', model], env={**os.environ, 'OLLAMA_HOST': ADDRESS,
        'OLLAMA_MODELS': str(ROOT/'data/models/ollama'), 'OLLAMA_NO_CLOUD': '1'}, check=True)
    if config['provider'] == 'ollama':
        save_config({'model': model})
    print('Local AI ready.', flush=True)


if __name__ == '__main__':
    install()
