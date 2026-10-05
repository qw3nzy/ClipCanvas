"""Explicit YouTube cookie import; no browser profiles are read automatically."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

MAX_COOKIE_BYTES = 512 * 1024


def is_youtube(url):
    host = (urlsplit(url or '').hostname or '').lower().rstrip('.')
    return host == 'youtu.be' or host == 'youtube.com' or host.endswith('.youtube.com')


def normalize_cookies(content):
    if len(content) > MAX_COOKIE_BYTES:
        raise ValueError('Файл cookies слишком большой. Экспортируйте только youtube.com.')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeError as error:
        raise ValueError('Нужен текстовый cookies.txt в формате Netscape.') from error
    if not text.startswith(('# Netscape HTTP Cookie File', '# HTTP Cookie File')):
        raise ValueError('Нужен cookies.txt в формате Netscape; JSON не подходит.')
    result = ['# Netscape HTTP Cookie File']
    for line in text.splitlines():
        if not line.strip() or (line.startswith('#') and not line.startswith('#HttpOnly_')):
            continue
        values = line.removeprefix('#HttpOnly_').split('\t')
        if len(values) != 7:
            raise ValueError('Некорректная строка cookies.txt. Экспортируйте файл заново.')
        domain, subdomains, path, secure, expires, name, value = values
        host = domain.lower().lstrip('.')
        if host != 'youtube.com' and not host.endswith('.youtube.com'):
            continue
        if (subdomains not in {'TRUE', 'FALSE'} or secure not in {'TRUE', 'FALSE'}
                or not path.startswith('/') or not expires.isdigit() or not name
                or any(ord(c) < 32 for c in name + value)):
            raise ValueError('Некорректная строка cookies YouTube.')
        if int(expires) and int(expires) <= time.time():
            continue
        result.append(line)
    if len(result) == 1:
        raise ValueError('В файле нет действующих cookies youtube.com. Экспортируйте их после входа в YouTube.')
    return ('\n'.join(result) + '\n').encode('utf-8')


def _crypt(content, decrypt=False):
    """Windows DPAPI binds stored sessions to the current Windows account."""
    if os.name != 'nt':
        return content

    class Blob(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('data', ctypes.c_void_p)]

    buffer = ctypes.create_string_buffer(content)
    incoming = Blob(len(content), ctypes.cast(buffer, ctypes.c_void_p))
    outgoing = Blob()
    function = (ctypes.windll.crypt32.CryptUnprotectData if decrypt
                else ctypes.windll.crypt32.CryptProtectData)
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ValueError('Не удалось открыть сессию YouTube. Подключите cookies заново в этой учётной записи Windows.')
    try:
        return ctypes.string_at(outgoing.data, outgoing.length)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
        ctypes.windll.kernel32.LocalFree(outgoing.data)


def session_path(data):
    return Path(data) / '.private' / 'youtube-session.bin'


def save_session(data, content):
    cleaned = normalize_cookies(content)
    path = session_path(data)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='youtube-save-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(_crypt(cleaned))
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def cookie_arguments(data, url):
    path = session_path(data)
    if not is_youtube(url) or not path.is_file():
        yield []
        return
    content = normalize_cookies(_crypt(path.read_bytes(), decrypt=True))
    # Start.ps1 protects this Windows folder for the current user and SYSTEM.
    private = (Path(os.environ['LOCALAPPDATA']) / 'ClipCanvas'
               if os.name == 'nt' and not os.environ.get('CLIPCANVAS_DATA') else path.parent)
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='youtube-run-', suffix='.cookies', dir=private)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
        yield ['--cookies', temporary]
    finally:
        Path(temporary).unlink(missing_ok=True)


def without_auth_metadata(value):
    """Do not copy cookies or authorization headers into cached media metadata."""
    if isinstance(value, dict):
        return {k: without_auth_metadata(v) for k, v in value.items()
                if k.lower() not in {'cookie', 'cookies', 'authorization', 'proxy-authorization'}}
    if isinstance(value, list):
        return [without_auth_metadata(v) for v in value]
    return value
