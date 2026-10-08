"""Read Codex's local subagent records and run an optional real spawn check."""
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from .config import ConfigError


def session_files(home, limit=300):
    root = home.path / 'sessions'
    if not root.is_dir():
        return []
    try:
        files = root.rglob('*.jsonl')
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    except OSError:
        return []


def parse_subagent(path):
    """Use only contexts after the child's inherited-history boundary."""
    try:
        with path.open('r', encoding='utf-8') as stream:
            first = json.loads(next(stream))
            if first.get('type') != 'session_meta':
                return None
            meta = first.get('payload') or {}
            source = meta.get('source') or {}
            if not isinstance(source, dict) or not isinstance(source.get('subagent'), dict):
                return None
            boundary = meta.get('subagent_history_start_ordinal')
            parent = meta.get('parent_thread_id') or source['subagent'].get('thread_spawn', {}).get('parent_thread_id')
            record = {'id': meta.get('id'), 'parent_id': parent,
                      'role': meta.get('agent_role') or source['subagent'].get('thread_spawn', {}).get('agent_role'),
                      'time': meta.get('timestamp'), 'model': None, 'effort': None}
            if not isinstance(boundary, int) or boundary < 1:
                return record
            contexts, answered_turns = {}, set()
            for ordinal, line in enumerate(stream, start=1):
                if ordinal < boundary:
                    continue
                if '"turn_context"' not in line and '"token_usage_record"' not in line:
                    continue
                item = json.loads(line)
                payload = item.get('payload') or {}
                if item.get('type') == 'turn_context' and payload.get('turn_id'):
                    contexts[payload['turn_id']] = (payload.get('model'), payload.get('effort') or '')
                elif item.get('type') == 'token_usage_record' and payload.get('thread_id') == record['id'] and payload.get('response_id'):
                    answered_turns.add(payload.get('turn_id'))
            used = [contexts[turn] for turn in answered_turns if turn in contexts]
            models = {model for model, _ in used if isinstance(model, str) and model}
            efforts = {effort for _, effort in used}
            if len(models) == 1:
                record['model'] = models.pop()
            if len(efforts) == 1:
                record['effort'] = efforts.pop()
            return record
    except (OSError, ValueError, StopIteration, TypeError):
        return None


def expected_role(home, name):
    role = home.roles.get(name)
    if role is None:
        raise ConfigError(f'未找到子代理角色：{name}')
    effective = home.effective(role.doc)
    modified = max(p.stat().st_mtime for p in (home.config_path, role.path) if p and p.is_file())
    return effective.get('model') or '', effective.get('model_reasoning_effort') or '', modified


def configuration_unchanged(home, name):
    paths = (home.config_path, home.roles[name].path)
    try:
        return all(path is None or path.read_bytes() == home.snapshots.get(path) for path in paths)
    except OSError:
        return False


def compare(record, home, name):
    model, effort, modified = expected_role(home, name)
    result = {**record, 'configured_model': model, 'configured_effort': effort}
    if record['role'] != name or not record['model']:
        result['status'] = 'unknown'
        return result
    try:
        called = datetime.fromisoformat(record['time'].replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError):
        called = 0
    if modified > called + 1:
        result['status'] = 'stale'
    elif record['model'] != model or (effort and record['effort'] != effort):
        result['status'] = 'mismatch'
    elif effort and not record['effort']:
        result['status'] = 'unknown'
    else:
        result['status'] = 'matched'
    return result


def history(home, name=None, limit=20):
    records = []
    for path in session_files(home):
        record = parse_subagent(path)
        if not record or not record['role'] or (name and record['role'] != name):
            continue
        if record['role'] in home.roles:
            records.append(compare(record, home, record['role']))
        else:
            records.append({**record, 'configured_model': '', 'configured_effort': '', 'status': 'stale'})
        if len(records) >= limit:
            break
    return records


def active_check(home, name, runner=subprocess.run, executable=None):
    model, effort, _ = expected_role(home, name)
    base_result = {'role': name, 'configured_model': model, 'configured_effort': effort}
    if not configuration_unchanged(home, name):
        raise ConfigError('配置在验收开始前已变化，请刷新面板后重试')
    executable = executable or shutil.which('codex.cmd' if os.name == 'nt' else 'codex')
    if not executable:
        raise ConfigError('未找到 Codex CLI，请先安装并确保 codex 在 PATH 中')
    prompt = (f'Use the configured {name} subagent exactly once. Ask it to reply with OK only. '
              'Wait for it to finish, then reply with OK. Do not read or change files. '
              'Do not specify a model or reasoning effort when spawning it.')
    env = {**os.environ, 'CODEX_HOME': str(home.path)}
    with tempfile.TemporaryDirectory(prefix='codex-agent-check-') as cwd:
        try:
            completed = runner([executable, 'exec', '--json', '--skip-git-repo-check',
                                '--sandbox', 'read-only', '--cd', cwd, prompt],
                               cwd=cwd, env=env, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=180)
        except subprocess.TimeoutExpired:
            return {**base_result, 'status': 'unknown', 'message': 'Codex 验收超时；无法确认是否创建了子代理'}
        except OSError:
            return {**base_result, 'status': 'unknown', 'message': '无法启动 Codex CLI'}
    parent_id = None
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'thread.started':
            parent_id = event.get('thread_id')
            break
    if not parent_id:
        return {**base_result, 'status': 'unknown', 'message': 'Codex 未返回会话 ID；无法关联子代理记录'}
    for path in session_files(home):
        record = parse_subagent(path)
        if record and record['parent_id'] == parent_id and record['role'] == name:
            result = compare(record, home, name)
            if not configuration_unchanged(home, name):
                result['status'] = 'stale'
            result['message'] = {'matched': '已创建子代理，模型与配置一致',
                                 'mismatch': '已创建子代理，但实际模型与配置不符',
                                 'stale': '验收期间配置发生变化，无法比较',
                                 'unknown': '已创建子代理，但记录不足以确认模型'}[result['status']]
            return result
    return {**base_result, 'status': 'unknown', 'parent_id': parent_id,
            'message': '未找到指定角色的子代理记录；可能未创建或未保存会话记录' if completed.returncode == 0
                       else 'Codex 会话运行失败，且未找到指定角色的子代理记录'}
