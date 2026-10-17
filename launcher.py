"""Desktop entry point. Freeze with PyInstaller; binds only to loopback."""
import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from backend.api import create_app


def main():
    parser = argparse.ArgumentParser(description='ArkPark2 本地存档修改器')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    frozen = getattr(sys, 'frozen', False)
    base = Path(sys.executable).parent if frozen else Path(__file__).resolve().parent
    resources = Path(getattr(sys, '_MEIPASS', base))
    data_dir = args.data_dir or base / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(data_dir/'server.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')])
    # Named Windows mutex prevents two EXE instances from writing the same data directory.
    mutex = None
    if os.name == 'nt':
        import ctypes
        import hashlib
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        mutex = kernel.CreateMutexW(None, False, 'Local\\ArkParkEditor-' + hashlib.sha256(str(data_dir.resolve()).casefold().encode()).hexdigest())
        if ctypes.get_last_error() == 183:
            url_file = data_dir / 'server-url.txt'
            if url_file.exists() and not args.no_browser:
                url = url_file.read_text(encoding='utf-8').strip()
                if url.startswith('http://127.0.0.1:'):
                    webbrowser.open(url)
            return
    web_dir = resources / 'frontend' / 'dist'
    if not web_dir.is_dir():
        raise RuntimeError('网页未构建，请先运行 build.ps1')
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', args.port))
    sock.listen(128)
    port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    (data_dir / 'server-url.txt').write_text(url, encoding='utf-8')
    app = create_app(data_dir, web_dir, shutdown=lambda: setattr(server, 'should_exit', True))
    def open_when_ready():
        for _ in range(100):
            try:
                with urllib.request.urlopen(url+'/api/status', timeout=.5):
                    pass
                if not args.no_browser:
                    webbrowser.open(url)
                return
            except OSError:
                time.sleep(.1)
    threading.Thread(target=open_when_ready, daemon=True).start()
    config = uvicorn.Config(app, host='127.0.0.1', port=port, log_config=None)
    server = uvicorn.Server(config)
    server.run(sockets=[sock])
    if mutex:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(mutex)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        logging.exception('启动失败')
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), 'ArkPark2 修改器启动失败', 0x10)
        raise
