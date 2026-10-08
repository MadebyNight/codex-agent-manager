"""Verify the actual release EXE with no Python/Node on its PATH."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
import zipfile

from app import __version__
from .test_manager import Fixture


def main():
    archive=Path(sys.argv[1]).resolve()
    requests=[];model_requests=[]
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            model_requests.append((self.path,self.headers.get('Authorization')))
            self.send_response(200);self.end_headers()
            self.wfile.write(json.dumps({'data':[{'id':'gpt-portable',
                'supported_reasoning_levels':[{'effort':'low'},{'effort':'high'}]}]}).encode())
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200);self.end_headers()
            self.wfile.write(json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'OK'}]}]}).encode())
    with tempfile.TemporaryDirectory(prefix='codex-portable-test-') as temp:
        root=Path(temp)
        with zipfile.ZipFile(archive) as z:
            assert not any('/.local/' in n or '/.venv/' in n or n.endswith('auth.json') for n in z.namelist())
            z.extractall(root/'解压目录 with spaces')
        exe=next((root/'解压目录 with spaces').glob('*/CodexAgentManager.exe'))
        assert (exe.parent/'_internal/web/index.html').is_file()
        fixture=Fixture(root/'模拟用户')
        provider=ThreadingHTTPServer(('127.0.0.1',0),Provider)
        thread=threading.Thread(target=provider.serve_forever,daemon=True);thread.start()
        for home in fixture.homes.values():
            file=home/'config.toml'
            file.write_text(file.read_text('utf-8').replace('127.0.0.1:1/',f'127.0.0.1:{provider.server_port}/'),encoding='utf-8')
        with socket.socket() as s:
            s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        env=os.environ.copy()
        env['PATH']=os.path.join(os.environ['SystemRoot'],'System32')
        env['CODEX_HOME']=str(fixture.homes['native']);env['ORCA_CODEX_HOME']=str(fixture.homes['orca'])
        for key in ('PYTHONHOME','PYTHONPATH','TCL_LIBRARY','TK_LIBRARY'):
            env.pop(key,None)
        url=f'http://127.0.0.1:{port}'
        def api(path,body=None):
            req=Request(url+'/api/'+path, None if body is None else json.dumps(body).encode(),{'Content-Type':'application/json'})
            with urlopen(req,timeout=10) as response:return json.load(response)
        def start():
            process=subprocess.Popen([str(exe),'--port',str(port),'--no-open'],cwd=root,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
            for _ in range(200):
                if process.poll() is not None:
                    raise AssertionError((exe.parent/'.local/server-error.log').read_text('utf-8'))
                try:
                    if api('health')['version']==__version__:return process
                except OSError:pass
                time.sleep(.1)
            process.terminate();process.wait();raise AssertionError('Portable startup timed out')
        process=None
        try:
            process=start()
            with urlopen(url+'/') as response:assert b'Codex Agent Manager' in response.read()
            with urlopen(url+'/icons/folder.svg') as response:assert b'<svg' in response.read()
            assert not api('settings')['configured']
            homes={k:{'path':str(p),'enabled':True} for k,p in fixture.homes.items()}
            assert api('settings/check',{'homes':homes})['valid']
            api('settings/save',{'homes':homes})
            assert api('verification/history',{'scope':'native','name':'coder'})['homes']['native']==[]
            catalog=api('catalog')['homes']
            assert all(catalog[k]['models']==['gpt-portable'] for k in homes)
            assert all(catalog[k]['efforts']['gpt-portable']==['low','high'] for k in homes)
            assert len(model_requests)==2 and all(path=='/v1/models' and auth=='Bearer private-fixture-token' for path,auth in model_requests)
            state=api('state')
            plan=api('preview',{'scope':'both','name':'coder','patch':{'model':'any-vendor/portable-tested'},
                               'revisions':{k:h['revision'] for k,h in state['homes'].items()}})
            assert api('test',{'id':plan['id']})['passed']
            api('apply',{'id':plan['id'],'confirmed':True})
            assert len(requests)==2
            assert all('any-vendor/portable-tested' in (p/'agents/coder.toml').read_text('utf-8') for p in fixture.homes.values())
            fixture.share_builtin_with_orca('worker')
            state=api('state')
            assert not state['homes']['orca']['errors']
            plan=api('preview',{'scope':'both','name':'reviewer','create':True,
                                'patch':{'model':'gpt-new','description':'review','developer_instructions':'Review changes.'},
                                'revisions':{k:h['revision'] for k,h in state['homes'].items()}})
            assert api('test',{'id':plan['id']})['passed']
            api('apply',{'id':plan['id'],'confirmed':True})
            assert all((home/'agents/reviewer.toml').is_file() for home in fixture.homes.values())
            # Duplicate launch reuses the existing instance and exits.
            subprocess.run([str(exe),'--port',str(port),'--no-open'],env=env,timeout=15,check=True)
            subprocess.run([str(exe),'--port',str(port),'--stop','--no-open'],env=env,timeout=15,check=True)
            process.wait(timeout=15);assert process.returncode==0
            process=start()
            assert api('settings')['configured']
            assert len(api('state')['backups'])==2
            api('shutdown',{});process.wait(timeout=15)
            assert process.returncode==0
            error_log=(exe.parent/'.local/server-error.log').read_text('utf-8')
            assert 'Traceback' not in error_log, error_log
            print('Portable EXE passed: clean PATH, Unicode/spaces, static assets, both homes, service model catalogs and reasoning levels, shared Orca role references, settings persistence, actual local model probes, save, duplicate launch, stop, restart.')
        finally:
            if process and process.poll() is None:process.terminate();process.wait()
            provider.shutdown();provider.server_close();thread.join()


if __name__=='__main__':main()
