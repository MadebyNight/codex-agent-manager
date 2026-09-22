import argparse
import json
import mimetypes
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .config import ConfigError, default_homes
from .manager import Manager

ROOT = Path(__file__).resolve().parent.parent


def make_server(manager, port=8765):
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
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/api/state':
                return self.reply(200, manager.state())
            if path == '/api/health':
                return self.reply(200, {'app': 'codex-agent-manager'})
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
                if route == '/api/preview':
                    result = manager.preview(body)
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
    args = parser.parse_args()
    manager = Manager(default_homes(), ROOT / '.local/backups')
    server = make_server(manager, args.port)
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'Codex Agent Manager: {url}', flush=True)
    if args.open:
        threading.Timer(.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
