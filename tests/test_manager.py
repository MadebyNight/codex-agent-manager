import copy
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import tomlkit

from app.config import ConfigError, Home, default_homes
from app.connectivity import connection, probe
from app.manager import Manager, atomic_write
from app.server import make_server


class Fixture:
    def __init__(self, root):
        self.homes = {k: Path(root) / k for k in ('native', 'orca')}
        for key, home in self.homes.items():
            (home / 'agents').mkdir(parents=True)
            (home / 'config.toml').write_text(
                '# keep root\nmodel = "gpt-parent"\nmodel_reasoning_effort = "medium"\n'
                'model_provider = "test"\n[model_providers.test]\nbase_url = "http://127.0.0.1:1/v1"\n'
                'wire_api = "responses"\nrequires_openai_auth = true\n', encoding='utf-8')
            (home / 'auth.json').write_text('{"OPENAI_API_KEY":"private-fixture-token"}', encoding='utf-8')
            self.role(key, 'coder', 'gpt-old', 'high' if key == 'native' else 'low')
        self.manager = Manager(self.homes, Path(root) / 'backups', tester=self.fake_probe)

    def role(self, key, name, model='gpt-old', effort='medium', extra=''):
        path = self.homes[key] / 'agents' / (name + '.toml')
        path.write_text(f'# preserve this comment\nname = "{name}"\ndescription = "fixture role"\n'
                        f'model = "{model}" # model comment\nmodel_reasoning_effort = "{effort}"\n'
                        'developer_instructions = """\n保留提示词\nsecond line\n"""\n'
                        'sandbox_mode = "read-only"\n' + extra, encoding='utf-8')
        return path

    @staticmethod
    def fake_probe(home, effective):
        return {'fingerprint': connection(home, effective)[3], 'model': effective['model'],
                'latency_ms': 1, 'message': 'fixture'}

    def request(self, **kw):
        state = self.manager.state()
        return {'scope': 'native', 'name': 'coder', 'operation': 'save', 'patch': {'model':'gpt-new'},
                'revisions':{k:h['revision'] for k,h in state['homes'].items()}, **kw}

    def apply(self, request, test=True):
        preview = self.manager.preview(request)
        if test and preview['needs_test']:
            self.manager.test(preview['id'])
        return self.manager.apply(preview['id'], True)


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = Fixture(self.temp.name)
        self.m = self.f.manager

    def read(self, scope='native', name='coder'):
        return tomlkit.parse((self.f.homes[scope] / 'agents' / f'{name}.toml').read_text('utf-8'))

    def test_native_home_ignores_orca_codex_home(self):
        with patch.dict(os.environ, {'CODEX_HOME':'C:/wrong', 'ORCA_CODEX_HOME':str(self.f.homes['orca'])}):
            self.assertEqual(default_homes()['native'], (Path.home()/'.codex').resolve())
            self.assertEqual(default_homes()['orca'], self.f.homes['orca'])

    def test_builtin_and_custom_classification_and_no_secrets(self):
        state = self.m.state()
        roles = {r['name']:r for r in state['homes']['native']['roles']}
        self.assertEqual({r['name'] for r in roles.values() if r['builtin']}, {'default','worker','explorer'})
        self.assertFalse(roles['coder']['builtin'])
        self.assertNotIn('private-fixture-token', json.dumps(state))

    def test_native_only_preserves_other_home_comments_prompts_and_unknown(self):
        path = self.f.role('native','coder', extra='service_tier = "fast"\n')
        before_orca = (self.f.homes['orca']/'agents/coder.toml').read_bytes()
        before_prompt = self.read()['developer_instructions']
        self.f.apply(self.f.request())
        after = path.read_text('utf-8')
        self.assertIn('# preserve this comment',after)
        self.assertIn('# model comment',after)
        self.assertEqual(self.read()['developer_instructions'],before_prompt)
        self.assertEqual(self.read()['service_tier'],'fast')
        self.assertEqual((self.f.homes['orca']/'agents/coder.toml').read_bytes(),before_orca)

    def test_orca_only_does_not_change_native(self):
        before = (self.f.homes['native']/'agents/coder.toml').read_bytes()
        self.f.apply(self.f.request(scope='orca'))
        self.assertEqual((self.f.homes['native']/'agents/coder.toml').read_bytes(),before)
        self.assertEqual(self.read('orca')['model'],'gpt-new')

    def test_both_preserves_untouched_differences(self):
        self.f.apply(self.f.request(scope='both'))
        self.assertEqual(self.read()['model'],'gpt-new')
        self.assertEqual(self.read('orca')['model'],'gpt-new')
        self.assertEqual(self.read()['model_reasoning_effort'],'high')
        self.assertEqual(self.read('orca')['model_reasoning_effort'],'low')

    def test_custom_arbitrary_model_allowed_after_test(self):
        self.f.apply(self.f.request(patch={'model':'vendor/Anything:0.7-beta'}))
        self.assertEqual(self.read()['model'],'vendor/Anything:0.7-beta')

    def test_untested_or_failed_test_cannot_save(self):
        preview = self.m.preview(self.f.request())
        with self.assertRaisesRegex(ConfigError,'连通性'):
            self.m.apply(preview['id'],True)
        self.m.tester = lambda *_: (_ for _ in ()).throw(ConfigError('failure'))
        self.assertFalse(self.m.test(preview['id'])['passed'])
        with self.assertRaises(ConfigError):
            self.m.apply(preview['id'],True)

    def test_both_requires_both_successful(self):
        preview = self.m.preview(self.f.request(scope='both'))
        def tester(home, config):
            if home.key == 'orca': raise ConfigError('orca fails')
            return self.f.fake_probe(home,config)
        self.m.tester=tester
        self.assertFalse(self.m.test(preview['id'])['passed'])
        with self.assertRaises(ConfigError):self.m.apply(preview['id'],True)
        self.assertEqual(self.read()['model'],'gpt-old')

    def test_auth_change_invalidates_test(self):
        preview = self.m.preview(self.f.request())
        self.m.test(preview['id'])
        (self.f.homes['native']/'auth.json').write_text('{"OPENAI_API_KEY":"changed"}',encoding='utf-8')
        with self.assertRaisesRegex(ConfigError,'认证'):self.m.apply(preview['id'],True)

    def test_file_change_blocks_preview_and_apply(self):
        request=self.f.request();preview=self.m.preview(request)
        self.m.test(preview['id'])
        self.f.role('native','coder','gpt-external')
        with self.assertRaises(ConfigError):self.m.apply(preview['id'],True)
        with self.assertRaises(ConfigError):self.m.preview(request)
        self.assertEqual(self.read()['model'],'gpt-external')

    def test_new_unrelated_role_also_invalidates_stale_view(self):
        request=self.f.request();self.f.role('native','new_role')
        with self.assertRaises(ConfigError):self.m.preview(request)

    def test_create_delete_and_restore_custom_both(self):
        request=self.f.request(scope='both',name='reviewer',create=True,patch={
            'model':'gemini-example','description':'review','developer_instructions':'Read carefully.'})
        self.f.apply(request)
        self.assertTrue(all((h/'agents/reviewer.toml').is_file() for h in self.f.homes.values()))
        result=self.f.apply(self.f.request(scope='both',name='reviewer',operation='delete',patch={}),False)
        self.assertFalse(any((h/'agents/reviewer.toml').exists() for h in self.f.homes.values()))
        preview=self.m.restore_preview(result['backup_id']);self.m.apply(preview['id'],True)
        self.assertTrue(all((h/'agents/reviewer.toml').is_file() for h in self.f.homes.values()))

    def test_both_missing_custom_uses_template_without_overwriting_existing(self):
        self.f.role('native','solo',extra='service_tier = "fast"\n')
        values=self.m.state()['homes']['native']['roles']
        template=next(r['values'] for r in values if r['name']=='solo')
        template['model']='any-model'
        self.f.apply(self.f.request(scope='both',name='solo',patch={'model':'any-model'},template=template))
        self.assertEqual(self.read('orca','solo')['model'],'any-model')
        self.assertEqual(self.read('native','solo')['service_tier'],'fast')

    def test_builtin_only_gpt_and_no_prompt_changes(self):
        with self.assertRaisesRegex(ConfigError,'GPT'):
            self.m.preview(self.f.request(name='worker',patch={'model':'gemini-x'}))
        with self.assertRaisesRegex(ConfigError,'仅支持'):
            self.m.preview(self.f.request(name='worker',patch={'developer_instructions':'override'}))

    def test_new_builtin_override_keeps_inherited_prompt_and_restores(self):
        self.f.apply(self.f.request(name='worker'))
        home=Home('native',self.f.homes['native'])
        self.assertTrue(home.roles['worker'].registered)
        self.assertNotIn('developer_instructions',home.roles['worker'].doc)
        self.assertEqual(home.roles['worker'].doc['model'],'gpt-new')
        self.f.apply(self.f.request(name='worker',operation='delete',patch={}),False)
        home=Home('native',self.f.homes['native'])
        self.assertFalse(home.roles['worker'].registered)
        self.assertIsNone(home.roles['worker'].path)
        self.assertNotIn('worker',home.config.get('agents',{}))

    def test_existing_builtin_non_gpt_retained_until_edit(self):
        path=self.f.role('native','explorer','gemini-existing')
        before=path.read_bytes();state=self.m.state()
        self.assertTrue(next(r for r in state['homes']['native']['roles'] if r['name']=='explorer')['overridden'])
        self.assertEqual(path.read_bytes(),before)
        with self.assertRaisesRegex(ConfigError,'GPT'):
            self.m.preview(self.f.request(name='explorer',patch={'model_reasoning_effort':'low'}))
        self.f.apply(self.f.request(name='explorer'))
        self.assertEqual(self.read(name='explorer')['model'],'gpt-new')

    def test_create_collision_or_invalid_name_blocked(self):
        with self.assertRaises(ConfigError):self.m.preview(self.f.request(create=True))
        for name in ['../escape','UPPER','a/b','', 'CON','con','nul','com1']:
            with self.subTest(name=name),self.assertRaises(ConfigError):self.m.preview(self.f.request(name=name))

    def test_new_requires_description_and_prompt(self):
        with self.assertRaises(ConfigError):self.m.preview(self.f.request(name='new',create=True))

    def test_restore_refuses_external_changes(self):
        result=self.f.apply(self.f.request())
        self.f.role('native','coder','gpt-external')
        with self.assertRaises(ConfigError):self.m.restore_preview(result['backup_id'])

    def test_second_write_failure_rolls_back_first(self):
        old={k:(p/'agents/coder.toml').read_bytes() for k,p in self.f.homes.items()}
        calls=[]
        def writer(path,data):
            calls.append(path)
            if len(calls)==2:raise OSError('simulate disk failure')
            atomic_write(path,data)
        self.m.writer=writer
        with self.assertRaisesRegex(ConfigError,'回滚'):self.f.apply(self.f.request(scope='both'))
        for k,p in self.f.homes.items():self.assertEqual((p/'agents/coder.toml').read_bytes(),old[k])

    def test_bom_and_crlf_preserved(self):
        path=self.f.homes['native']/'agents/coder.toml'
        path.write_bytes(b'\xef\xbb\xbf'+path.read_bytes().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'))
        self.f.apply(self.f.request())
        data=path.read_bytes();self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n',data.replace(b'\r\n',b''))

    def test_malformed_file_blocks_writes_without_leaking_source(self):
        (self.f.homes['native']/'agents/bad.toml').write_text('key = "secret-unclosed',encoding='utf-8')
        state=self.m.state();self.assertTrue(state['homes']['native']['errors'])
        self.assertNotIn('secret-unclosed',json.dumps(state))
        with self.assertRaises(ConfigError):self.m.preview(self.f.request())

    def test_delete_preview_does_not_expose_unknown_secrets(self):
        self.f.role('native','coder',extra='[model_providers.special]\nexperimental_bearer_token = "hidden-token"\n')
        preview=self.m.preview(self.f.request(operation='delete',patch={}))
        self.assertNotIn('hidden-token',json.dumps(preview))

    def test_legacy_config_file_preserves_unrelated_root(self):
        path=self.f.homes['native']/'config.toml'
        path.write_text(path.read_text('utf-8')+'\n[agents.legacy]\ndescription = "legacy role"\nconfig_file = "legacy.toml"\n',encoding='utf-8')
        (self.f.homes['native']/'legacy.toml').write_text('model="gpt-before"\n',encoding='utf-8')
        self.f.apply(self.f.request(name='legacy',patch={'model':'gpt-new','developer_instructions':'legacy instructions'}))
        self.assertIn('# keep root',path.read_text('utf-8'))
        self.assertEqual(Home('native',self.f.homes['native']).roles['legacy'].doc['model'],'gpt-new')

    def test_confirmation_is_required(self):
        preview=self.m.preview(self.f.request(operation='delete',patch={}))
        with self.assertRaises(ConfigError):self.m.apply(preview['id'],False)

    def test_description_only_registration_gets_layer_without_duplicate(self):
        path=self.f.homes['native']/'config.toml'
        path.write_text(path.read_text('utf-8')+'\n[agents.legacy]\ndescription="legacy description"\n'
                        'nickname_candidates=["Keep"]\n',encoding='utf-8')
        self.f.apply(self.f.request(name='legacy',patch={'model':'gpt-new','developer_instructions':'Keep scope.'}))
        home=Home('native',self.f.homes['native'])
        self.assertFalse(home.errors)
        self.assertEqual(home.config['agents']['legacy']['nickname_candidates'],['Keep'])
        self.assertEqual(home.config['agents']['legacy']['description'],'legacy description')
        self.assertEqual(home.roles['legacy'].doc['model'],'gpt-new')

    def test_interrupted_transaction_is_recovered_on_restart(self):
        old={k:(p/'agents/coder.toml').read_bytes() for k,p in self.f.homes.items()}
        calls=[]
        def crash(path,data):
            calls.append(path)
            if len(calls)==2:raise SystemExit('simulated termination')
            atomic_write(path,data)
        self.m.writer=crash
        with self.assertRaises(SystemExit):self.f.apply(self.f.request(scope='both'))
        self.assertEqual(self.read()['model'],'gpt-new')
        recovered=Manager(self.f.homes,self.m.storage)
        self.assertFalse(recovered.recovery_errors)
        for k,p in self.f.homes.items():self.assertEqual((p/'agents/coder.toml').read_bytes(),old[k])

    def test_same_config_file_for_two_roles_is_blocked(self):
        path=self.f.homes['native']/'config.toml'
        path.write_text(path.read_text('utf-8')+'\n[agents.one]\nconfig_file="shared.toml"\n'
                        '[agents.two]\nconfig_file="shared.toml"\n',encoding='utf-8')
        (self.f.homes['native']/'shared.toml').write_text('model="gpt-x"\n',encoding='utf-8')
        with self.assertRaisesRegex(ConfigError,'共享'):self.m.preview(self.f.request())

    def test_profile_and_agent_provider_headers_merge(self):
        path=self.f.homes['native']/'config.toml'
        text=path.read_text('utf-8')
        path.write_text('profile="local"\n'+text+'\n[model_providers.test.http_headers]\nX-Root="root"\n'
                        '[profiles.local.model_providers.test.http_headers]\nX-Profile="profile"\n',encoding='utf-8')
        self.f.role('native','coder',extra='[model_providers.test.http_headers]\nX-Role="role"\n')
        home=Home('native',self.f.homes['native'])
        config=home.effective(home.roles['coder'].doc)
        provider=config['model_providers']['test']
        self.assertEqual(provider['http_headers'],{'X-Root':'root','X-Profile':'profile','X-Role':'role'})
        self.assertIn('base_url',provider)

    def test_test_proof_expiry(self):
        preview=self.m.preview(self.f.request());self.m.test(preview['id'])
        self.m.plans[preview['id']]['proofs']['native']['time']-=601
        with self.assertRaisesRegex(ConfigError,'10 分钟'):self.m.apply(preview['id'],True)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.f=Fixture(self.temp.name)
        self.payload={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'OK'}]}]}
        self.status=200;self.received=[]
        parent=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                parent.received.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length']))),dict(self.headers)))
                self.send_response(parent.status);self.end_headers();self.wfile.write(json.dumps(parent.payload).encode())
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        path=self.f.homes['native']/'config.toml'
        path.write_text(path.read_text('utf-8').replace('127.0.0.1:1/',f'127.0.0.1:{self.server.server_port}/'),encoding='utf-8')
        self.home=Home('native',self.f.homes['native'])
        self.config=self.home.effective(self.home.roles['coder'].doc)

    def test_real_responses_probe_with_reasoning_and_auth(self):
        result=probe(self.home,self.config)
        self.assertIn('fingerprint',result)
        path,body,headers=self.received[0]
        self.assertEqual(path,'/v1/responses')
        self.assertEqual(body['reasoning'],{'effort':'high'})
        self.assertEqual(headers['Authorization'],'Bearer private-fixture-token')

    def test_http_200_empty_output_not_success(self):
        self.payload={'status':'completed','output':[]}
        with self.assertRaisesRegex(ConfigError,'未生成文本'):probe(self.home,self.config)

    def test_non_responses_payload_is_rejected(self):
        for payload in [[], {'output':None}, {'choices':[{'message':{'content':'OK'}}]}]:
            with self.subTest(payload=payload):
                self.payload=payload
                with self.assertRaisesRegex(ConfigError,'格式'):probe(self.home,self.config)

    def test_api_error_body_does_not_leak(self):
        self.status=401;self.payload={'error':'private-fixture-token'}
        with self.assertRaises(ConfigError) as cm:probe(self.home,self.config)
        self.assertNotIn('private-fixture-token',str(cm.exception))
        self.assertIn('401',str(cm.exception))

    def test_provider_env_key_headers_query_and_custom_role_override(self):
        provider=self.config['model_providers']['test']
        provider.update(env_key='FIXTURE_API_KEY',env_http_headers={'X-Extra':'FIXTURE_HEADER'},query_params={'tenant':'test'})
        with patch.dict(os.environ,{'FIXTURE_API_KEY':'from-env','FIXTURE_HEADER':'extra'}):
            probe(self.home,self.config)
        path,body,headers=self.received[0]
        self.assertEqual(path,'/v1/responses?tenant=test')
        self.assertEqual(headers['Authorization'],'Bearer from-env')
        self.assertEqual(headers['X-Extra'],'extra')

    def test_chatgpt_oauth_is_not_misreported_as_api_key(self):
        (self.home.path/'auth.json').write_text('{"tokens":{"access_token":"oauth"}}',encoding='utf-8')
        with patch.dict(os.environ,{},clear=True),self.assertRaisesRegex(ConfigError,'OAuth'):connection(self.home,self.config)


class HTTPTests(unittest.TestCase):
    def test_local_entry_point_state_preview_static_and_origin(self):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);server=make_server(f.manager,0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{server.server_port}'
            try:
                with urllib.request.urlopen(url+'/api/state') as response:
                    state=json.load(response);self.assertEqual(len(state['homes']),2)
                with urllib.request.urlopen(url+'/') as response:
                    self.assertIn('Codex Agent Manager',response.read().decode())
                req=urllib.request.Request(url+'/api/preview',json.dumps(f.request()).encode(),{'Content-Type':'application/json'})
                with urllib.request.urlopen(req) as response:
                    self.assertTrue(json.load(response)['needs_test'])
                req.add_header('Origin','https://unrelated.test')
                with self.assertRaises(urllib.error.HTTPError) as cm:urllib.request.urlopen(req)
                self.assertEqual(cm.exception.code,403)
                cm.exception.close()
            finally:server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':unittest.main()
