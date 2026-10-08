import json
import subprocess
import tempfile
import unittest
import urllib.request
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import Home
from app.verification import active_check, history, parse_subagent
from app.server import make_server
from tests.test_manager import Fixture


def write_session(home, filename, *, parent='parent-1', role='coder', model='gpt-old', boundary=3):
    folder = home.path / 'sessions' / '2026' / '10' / '08'
    folder.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    lines = [
        {'type': 'session_meta', 'payload': {'id': filename, 'parent_thread_id': parent,
         'agent_role': role, 'timestamp': timestamp, 'subagent_history_start_ordinal': boundary,
         'source': {'subagent': {'thread_spawn': {'parent_thread_id': parent, 'agent_role': role}}}}},
        {'type': 'turn_context', 'payload': {'model': 'gpt-parent', 'effort': 'low', 'turn_id': 'parent-turn'}},
        {'type': 'event_msg', 'payload': {'type': 'thread_settings_applied'}},
        {'type': 'turn_context', 'payload': {'model': model, 'effort': 'high', 'turn_id': 'child-turn'}},
        {'type': 'token_usage_record', 'payload': {'thread_id': filename, 'turn_id': 'child-turn', 'response_id': 'resp-child'}},
    ]
    path = folder / (filename + '.jsonl')
    path.write_text(''.join(json.dumps(line) + '\n' for line in lines), encoding='utf-8')
    return path


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(self.temp.name)
        self.home = Home('native', self.fixture.homes['native'])

    def test_child_model_excludes_inherited_parent_history(self):
        path = write_session(self.home, 'child-1')
        record = parse_subagent(path)
        self.assertEqual((record['model'], record['effort']), ('gpt-old', 'high'))
        self.assertEqual(history(self.home, 'coder')[0]['status'], 'matched')
        self.assertEqual(history(self.home, 'worker'), [])

    def test_mismatch_and_missing_boundary_are_not_reported_as_success(self):
        write_session(self.home, 'child-1', model='gpt-other')
        self.assertEqual(history(self.home, 'coder')[0]['status'], 'mismatch')
        path = write_session(self.home, 'child-2', boundary=None)
        self.assertIsNone(parse_subagent(path)['model'])
        path = write_session(self.home, 'child-3')
        rows = path.read_text(encoding='utf-8').splitlines()
        path.write_text('\n'.join(rows[:-1]) + '\n', encoding='utf-8')
        self.assertIsNone(parse_subagent(path)['model'])

    def test_active_check_correlates_only_new_parent_and_requested_role(self):
        write_session(self.home, 'unrelated', parent='different-parent', model='gpt-other')
        def runner(command, **kwargs):
            self.assertIn('read-only', command)
            self.assertNotIn('--model', command)
            self.assertEqual(kwargs['env']['CODEX_HOME'], str(self.home.path))
            write_session(self.home, 'target', parent='new-parent')
            return subprocess.CompletedProcess(command, 0, '{"type":"thread.started","thread_id":"new-parent"}\n', '')
        result = active_check(self.home, 'coder', runner=runner, executable='codex-test')
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['parent_id'], 'new-parent')

    def test_active_check_without_spawn_is_unknown(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, '{"type":"thread.started","thread_id":"empty-parent"}\n', '')
        result = active_check(self.home, 'coder', runner=runner, executable='codex-test')
        self.assertEqual(result['status'], 'unknown')

    def test_active_check_marks_config_change_during_run_as_stale(self):
        def runner(command, **kwargs):
            write_session(self.home, 'changed-child', parent='changed-parent')
            role_file = self.home.roles['coder'].path
            role_file.write_text(role_file.read_text(encoding='utf-8') + '\n# changed\n', encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '{"type":"thread.started","thread_id":"changed-parent"}\n', '')
        result = active_check(self.home, 'coder', runner=runner, executable='codex-test')
        self.assertEqual(result['status'], 'stale')

    def test_history_endpoint_reports_real_child_model(self):
        write_session(self.home, 'child-api')
        server = make_server(self.fixture.manager, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f'http://127.0.0.1:{server.server_port}/api/verification/history',
                json.dumps({'scope': 'native', 'name': 'coder'}).encode(),
                {'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(request) as response:
                result = json.load(response)
            self.assertEqual(result['homes']['native'][0]['model'], 'gpt-old')
            self.assertEqual(result['homes']['native'][0]['status'], 'matched')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
