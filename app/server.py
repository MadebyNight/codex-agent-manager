import argparse
import json
import mimetypes
import sys
import threading
import urllib.request
import urllib.error
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .config import ConfigError
from . import __version__
from .settings import Settings, choose_directory

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else ROOT


def make_server(manager, port=8765, settings=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, data, kind='application/json; charset=utf-8'):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/api/state':
                return self.reply(200, manager.state())
            if path == '/api/catalog':
                return self.reply(200, manager.catalog())
            if path == '/api/health':
                return self.reply(200, {'app': 'codex-agent-manager', 'version': __version__})
            if path == '/api/settings':
                return self.reply(200, settings.state() if settings else {'configured': True, 'homes': {}})
            target = ROOT / 'web' / ('index.html' if path == '/' else path.lstrip('/'))
            if not target.resolve().is_relative_to((ROOT / 'web').resolve()) or not target.is_file():
                return self.reply(404, {'error': '页面不存在'})
            content_type = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            if target.suffix == '.js':
                content_type = 'text/javascript'
            self.reply(200, target.read_bytes(), content_type + '; charset=utf-8')

        def do_POST(self):
            # Local browser only; a random website should not write local configuration.
            origin = self.headers.get('Origin')
            if origin and origin != f'http://{self.headers.get("Host")}':
                return self.reply(403, {'error': '仅允许本地面板操作'})
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length < 1_000_000:
                    raise ConfigError('请求大小无效')
                body = json.loads(self.rfile.read(length))
                route = urlsplit(self.path).path
                if route == '/api/shutdown':
                    self.reply(200, {'ok': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                elif route.startswith('/api/settings/'):
                    if settings is None:
                        raise ConfigError('此服务未启用目录设置')
                    if route == '/api/settings/check':
                        result = settings.check(body)
                    elif route == '/api/settings/save':
                        result = settings.save(manager, body)
                    elif route == '/api/settings/browse':
                        result = {'path': choose_directory(str(body.get('path', '')))}
                    else:
                        raise ConfigError('目录设置接口不存在')
                elif route == '/api/preview':
                    result = manager.preview(body)
                elif route == '/api/verification/history':
                    result = manager.verification_history(body['scope'], body['name'])
                elif route == '/api/verification/run':
                    result = manager.verify_role(body['scope'], body['name'])
                elif route == '/api/test':
                    result = manager.test(body['id'])
                elif route == '/api/apply':
                    result = manager.apply(body['id'], body.get('confirmed') is True)
                elif route == '/api/restore-preview':
                    result = manager.restore_preview(body['id'])
                else:
                    return self.reply(404, {'error': '接口不存在'})
                self.reply(200, result)
            except (ConfigError, KeyError, ValueError, TypeError) as exc:
                self.reply(400, {'error': str(exc) if isinstance(exc, ConfigError) else '请求格式无效'})
            except Exception as exc:
                print(f'Local operation failed: {type(exc).__name__}', flush=True)
                self.reply(500, {'error': '本地操作失败，请检查文件权限及配置格式'})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description='Codex Agent Manager — local configuration UI')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--no-open', action='store_true', help='Do not open the browser')
    parser.add_argument('--stop', action='store_true', help='Stop the local panel on the selected port')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args()
    url = f'http://127.0.0.1:{args.port}'
    existing = None
    try:
        with urllib.request.urlopen(url + '/api/health', timeout=1) as response:
            existing = json.load(response)
    except (OSError, ValueError):
        pass
    if existing and existing.get('app') == 'codex-agent-manager':
        if args.stop:
            request = urllib.request.Request(url + '/api/shutdown', data=b'{}', headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request, timeout=3):
                pass
        elif not args.no_open:
            webbrowser.open(url)
        return
    if args.stop:
        return
    settings = Settings(DATA_ROOT / '.local/settings.json')
    manager = settings.manager()
    server = make_server(manager, args.port, settings)
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'Codex Agent Manager: {url}', flush=True)
    if (args.open or getattr(sys, 'frozen', False)) and not args.no_open:
        threading.Timer(.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
