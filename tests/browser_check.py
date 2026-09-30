"""End-to-end UI check. Run python -B -m tests.browser_check [--browser PATH].

All mutations use temporary homes and a local fake Responses endpoint. Screenshots
of the real homes use only GET requests and are final preview artifacts in docs/.
"""
import argparse
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

from app.config import default_homes
from app.connectivity import probe
from app.manager import Manager
from app.server import make_server
from app.settings import Settings
from .test_manager import Fixture


def serve(server):
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    return f'http://127.0.0.1:{server.server_port}', thread


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--browser')
    args=parser.parse_args()
    requests=[]
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            self.send_response(200);self.end_headers()
            self.wfile.write(json.dumps({'data':[{'id':'gpt-api-new',
                'supported_reasoning_levels':[{'effort':'low'},{'effort':'high'}]}]}).encode())
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200);self.end_headers()
            self.wfile.write(json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'OK'}]}]}).encode())
    with tempfile.TemporaryDirectory() as root, sync_playwright() as p:
        provider=ThreadingHTTPServer(('127.0.0.1',0),Provider)
        provider_url,provider_thread=serve(provider)
        f=Fixture(root)
        f.role('native','designer','gemini-example')
        for home in f.homes.values():
            file=home/'config.toml'
            file.write_text(file.read_text('utf-8').replace('http://127.0.0.1:1',provider_url),encoding='utf-8')
        f.manager.tester=probe
        settings=Settings(Path(root)/'local/settings.json',f.homes)
        server=make_server(f.manager,0,settings);url,thread=serve(server)
        browser=p.chromium.launch(headless=True,**({'executable_path':args.browser} if args.browser else {}))
        page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            page.goto(url)
            expect(page.locator('#directory-settings')).to_be_visible()
            expect(page.locator('#directory-native')).to_have_value(str(f.homes['native']))
            page.locator('#check-directories').click()
            expect(page.locator('[data-directory-check="native"]')).to_contain_text('已识别')
            page.locator('#directory-form [type=submit]').click()
            expect(page.locator('#directory-settings')).not_to_be_visible()
            page.reload()
            expect(page.locator('.role-card')).to_have_count(5)
            expect(page.locator('#directory-settings')).not_to_be_visible()
            page.locator('#open-settings').click()
            page.locator('#directory-orca').fill(str(Path(root)/'missing-orca'))
            page.locator('#directory-form [type=submit]').click()
            expect(page.locator('#directory-error')).to_contain_text('未找到配置')
            page.locator('[data-directory-enabled="orca"]').uncheck()
            page.locator('#directory-form [type=submit]').click()
            expect(page.locator('#directory-settings')).not_to_be_visible()
            expect(page.locator('[data-scope="orca"]')).to_be_disabled()
            expect(page.locator('[data-scope="both"]')).to_be_disabled()
            page.locator('#open-settings').click()
            page.route('**/api/settings/browse',lambda route:route.fulfill(json={'path':str(f.homes['orca'])}))
            page.locator('[data-browse-directory="orca"]').click()
            expect(page.locator('#directory-orca')).to_have_value(str(f.homes['orca']))
            page.locator('[data-directory-enabled="orca"]').check()
            page.locator('#directory-form [type=submit]').click()
            expect(page.locator('#directory-settings')).not_to_be_visible()
            expect(page.locator('[data-scope="both"]')).to_be_enabled()
            expect(page.locator('.role-card')).to_have_count(5)
            # A populated input must still expose every known model on click.
            page.locator('[data-role="coder"]').click()
            page.locator('#agent-model').click()
            expect(page.locator('#model-list [role=option]')).to_have_count(4)
            page.get_by_role('option',name='gpt-api-new',exact=True).click()
            expect(page.locator('#agent-model')).to_have_value('gpt-api-new')
            expect(page.locator('#agent-effort option')).to_have_count(3)
            page.locator('#model-toggle').click()
            page.get_by_role('option',name='gemini-example',exact=True).click()
            expect(page.locator('#agent-model')).to_have_value('gemini-example')
            expect(page.locator('#model-list')).to_be_hidden()
            page.locator('#model-toggle').click()
            expect(page.locator('#model-list [role=option]')).to_have_count(4)
            page.locator('#agent-model').fill('gpt-')
            expect(page.locator('#model-list [role=option]')).to_have_count(3)
            page.locator('#agent-model').press('ArrowDown')
            page.locator('#agent-model').press('ArrowDown')
            page.locator('#agent-model').press('Enter')
            expect(page.locator('#agent-model')).to_have_value('gpt-old')
            expect(page.locator('#preview')).not_to_be_visible()
            page.locator('#model-toggle').click()
            page.locator('#agent-model').press('Escape')
            expect(page.locator('#model-list')).to_be_hidden()
            expect(page.locator('#editor')).to_be_visible()
            page.locator('#agent-model').fill('unlisted-provider/model')
            expect(page.locator('.model-empty')).to_be_visible()
            expect(page.locator('#agent-model')).to_have_value('unlisted-provider/model')
            page.locator('#agent-effort').focus()
            expect(page.locator('#model-list')).to_be_hidden()
            page.locator('#editor [data-close="editor"]').first.click()
            page.locator('[data-role="worker"]').click()
            page.locator('#model-toggle').click()
            expect(page.locator('#model-list [role=option]')).to_have_count(3)
            expect(page.get_by_role('option',name='gemini-example',exact=True)).to_have_count(0)
            page.locator('#editor [data-close="editor"]').first.click()
            page.locator('[data-scope="both"]').click()
            expect(page.locator('[data-role="coder"] .tag')).to_have_text('配置有差异')
            page.locator('#add-agent').click()
            page.locator('#agent-name').fill('reviewer')
            page.locator('#agent-model').fill('arbitrary-provider/model-X')
            page.locator('#agent-description').fill('Independent review role')
            page.locator('#agent-instructions').fill('Review the assigned files and return evidence.')
            page.locator('#editor-form [type=submit]').click()
            expect(page.locator('#preview')).to_be_visible()
            expect(page.locator('.diff-file')).to_have_count(2)
            expect(page.locator('#apply-change')).to_be_disabled()
            page.locator('#confirm-change').check()
            expect(page.locator('#apply-change')).to_be_disabled()
            page.locator('#test-model').click()
            expect(page.locator('#test-results .test-result')).to_have_count(2)
            expect(page.locator('#apply-change')).to_be_enabled()
            page.locator('#apply-change').click()
            expect(page.locator('#preview')).not_to_be_visible()
            expect(page.locator('[data-role="reviewer"]')).to_be_visible()
            assert len(requests)==2 and all(r['model']=='arbitrary-provider/model-X' for r in requests)
            assert all((h/'agents/reviewer.toml').exists() for h in f.homes.values())
            # Delete is a distinct second confirmation after the editor action.
            page.locator('[data-role="reviewer"]').click()
            page.locator('#delete-agent').click()
            expect(page.locator('#preview-title')).to_have_text('确认删除子代理')
            expect(page.locator('#apply-change')).to_be_disabled()
            page.locator('#confirm-change').check();page.locator('#apply-change').click()
            expect(page.locator('[data-role="reviewer"]')).to_have_count(0)
            assert not any((h/'agents/reviewer.toml').exists() for h in f.homes.values())
            page.locator('#open-backups').click()
            page.locator('[data-restore]').click()
            expect(page.locator('#preview-title')).to_have_text('恢复上一次配置')
            page.locator('#confirm-change').check();page.locator('#apply-change').click()
            expect(page.locator('[data-role="reviewer"]')).to_be_visible()
            # Built-in GPT validation runs on the server, not just the dropdown.
            page.locator('[data-role="worker"]').click()
            page.locator('#agent-model').fill('gemini-not-allowed')
            page.locator('#editor-form [type=submit]').click()
            expect(page.locator('#editor-errors')).to_contain_text('GPT')
            page.locator('#agent-model').fill('gpt-tested')
            page.locator('#editor-form [type=submit]').click()
            expect(page.locator('#preview')).to_be_visible()
            expect(page.locator('.diff-file')).to_have_count(4)
            page.locator('#test-model').click()
            expect(page.locator('#test-results .test-result')).to_have_count(2)
            page.locator('#confirm-change').check();page.locator('#apply-change').click()
            expect(page.locator('#preview')).not_to_be_visible()
            page.locator('[data-role="worker"]').click()
            expect(page.locator('#delete-agent')).to_have_text('恢复内置默认')
            expect(page.locator('#delete-agent')).to_be_visible()
            page.locator('#delete-agent').click()
            expect(page.locator('#preview-title')).to_have_text('恢复官方内置角色')
            page.locator('#confirm-change').check();page.locator('#apply-change').click()
            expect(page.locator('[data-role="worker"] .tag')).to_have_text('内置默认')
            # Keyboard search and small-screen layout.
            page.keyboard.press('/');expect(page.locator('#search')).to_be_focused()
            page.locator('#search').fill('reviewer');expect(page.locator('.role-card')).to_have_count(1)
            page.locator('#search').fill('')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.locator('[data-role="coder"]').click()
            expect(page.locator('#editor')).to_be_visible()
            page.keyboard.press('Escape');expect(page.locator('#editor')).not_to_be_visible()
            assert not errors, errors
            print('Browser E2E: create both, actual local probe, delete, restore, GPT restriction, built-in override/reset, search, mobile passed.')
            # Read-only real configuration preview: no writes or model requests.
            real=make_server(Manager(default_homes(),Path(root)/'readonly-backups'),0)
            real_url,real_thread=serve(real)
            try:
                page.set_viewport_size({'width':1440,'height':1150})
                page.goto(real_url)
                expect(page.locator('.role-card')).to_have_count(9)
                docs=Path(__file__).resolve().parent.parent/'docs'
                page.screenshot(path=str(docs/'preview-desktop.png'),full_page=True,animations='disabled')
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(docs/'preview-mobile.png'),full_page=True,animations='disabled')
                assert not errors, errors
                print('Read-only real configuration: 9 roles; desktop/mobile previews saved.')
            finally:real.shutdown();real.server_close();real_thread.join()
        finally:
            browser.close()
            server.shutdown();server.server_close();thread.join()
            provider.shutdown();provider.server_close();provider_thread.join()


if __name__=='__main__':main()
