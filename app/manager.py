"""Preview, verify, then apply small reversible transactions to Codex configs."""
import base64
import copy
import difflib
import json
import os
import threading
import time
import uuid
from pathlib import Path

import tomlkit

from .config import (BUILTINS, EFFORTS, FIELDS, NAME, RESERVED_NAMES, SANDBOXES, ConfigError, Home,
                     digest, encode, parse, read_bytes)
from .connectivity import connection, probe


def atomic_write(path, data):
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temp.open('wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def pack(data):
    return base64.b64encode(data).decode() if data is not None else None


def unpack(data):
    return base64.b64decode(data) if data is not None else None


class Manager:
    def __init__(self, homes, storage, tester=probe, writer=atomic_write, enabled=None):
        self.homes = {key: Path(value).resolve() for key, value in homes.items()}
        self.storage = Path(storage)
        self.enabled = set(homes) if enabled is None else set(enabled)
        self.tester, self.writer = tester, writer
        self.lock = threading.RLock()
        self.plans = {}
        self.recovery_errors = []
        self.recover()

    def targets(self, scope):
        if scope == 'both':
            if self.enabled != set(self.homes):
                raise ConfigError('请先在目录设置中启用两套配置')
            return list(self.homes)
        if scope in self.homes and scope in self.enabled:
            return [scope]
        raise ConfigError('请选择原生 Codex、Orca 或同时修改两套')

    def load(self, key):
        return Home(key, self.homes[key], self.homes['native'] if key == 'orca' else None)

    def backups(self):
        result = []
        for p in sorted(self.storage.glob('*.json'), reverse=True):
            try:
                item = json.loads(p.read_text('utf-8'))
                if item['status'] == 'applied' and self.matches_homes(item):
                    result.append({'id': p.stem, 'time': item['time'], 'name': item['name'],
                                   'operation': item['operation'], 'scope': item['scope'],
                                   'files': [c['path'] for c in item['changes']]})
            except (ValueError, KeyError):
                continue
        return result

    def state(self):
        with self.lock:
            result = {'homes': {}, 'backups': self.backups(), 'errors': self.recovery_errors}
            for key, path in self.homes.items():
                if key not in self.enabled:
                    result['homes'][key] = {'path': str(path), 'roles': [], 'errors': [], 'revision': None, 'enabled': False}
                    continue
                try:
                    home = self.load(key)
                    result['homes'][key] = {'path': str(path), 'roles': [home.public_role(r) for r in home.roles.values()],
                                            'errors': home.errors, 'revision': home.fingerprint(), 'enabled': True}
                except (ConfigError, OSError) as exc:
                    result['homes'][key] = {'path': str(path), 'roles': [], 'errors': [str(exc) if isinstance(exc, ConfigError) else '目录无法读取，请检查路径和权限'], 'revision': None, 'enabled': True}
            native_roles = {Path(r['path']): r for r in result['homes']['native']['roles'] if r['path']}
            for role in result['homes']['orca']['roles']:
                if role['shared'] and Path(role['path']) in native_roles:
                    native_roles[Path(role['path'])]['shared'] = True
            models = {r['effective_model'] for h in result['homes'].values() for r in h['roles'] if r['effective_model']}
            result['models'] = sorted(models)
            result['efforts'] = list(EFFORTS)
            return result

    def matches_homes(self, item):
        if 'homes' in item:
            return all(key in self.enabled and Path(value).resolve() == self.homes.get(key)
                       for key, value in item['homes'].items())
        return all(c['home'] in self.enabled and Path(c['path']).resolve().is_relative_to(self.homes[c['home']])
                   for c in item['changes'])

    def change_homes(self, homes, enabled):
        # Caller holds the manager lock and has validated/persisted these settings.
        self.homes = homes
        self.enabled = set(enabled)
        self.plans.clear()
        self.recovery_errors.clear()
        self.recover()

    def check_homes(self, keys):
        homes = {key: self.load(key) for key in keys}
        if self.recovery_errors:
            raise ConfigError('存在未恢复的事务，请先查看 .local/backups 中的事务记录')
        if len(self.enabled) == 2 and len(set(self.homes.values())) != len(self.homes):
            raise ConfigError('两套配置指向同一目录，无法独立修改')
        native_agents, orca_agents = [p / 'agents' for p in self.homes.values()]
        if len(self.enabled) == 2 and native_agents.resolve() == orca_agents.resolve():
            raise ConfigError('两套 agents 目录共享链接，无法保证修改范围')
        for home in homes.values():
            if home.errors:
                raise ConfigError('；'.join(home.errors))
        return homes

    def preview(self, request):
        with self.lock:
            scope, name = request.get('scope'), request.get('name', '')
            operation = request.get('operation', 'save')
            if operation not in ('save', 'delete') or not NAME.fullmatch(name):
                raise ConfigError('操作或角色名称无效；名称使用小写字母、数字、下划线或连字符')
            if name in RESERVED_NAMES:
                raise ConfigError('此名称是 Windows 保留文件名，请换一个角色名称')
            keys = self.targets(scope)
            homes = self.check_homes(keys)
            if scope != 'both':
                home = homes[scope]
                role = home.roles.get(name)
                if role and role.path:
                    shared = scope == 'orca' and not role.path.is_relative_to(home.path)
                    if scope == 'native':
                        try:
                            shared = any(peer.path == role.path for peer in self.load('orca').roles.values())
                        except (ConfigError, OSError):
                            pass
                    if shared:
                        raise ConfigError('此角色由原生 Codex 与 Orca 共用文件，请选择同时修改两套')
            else:
                native_role = homes['native'].roles.get(name)
                if native_role and native_role.path and any(
                    peer.name != name and peer.path == native_role.path
                    for peer in homes['orca'].roles.values()
                ):
                    raise ConfigError('两套配置把同一文件用于不同角色，无法同时修改')
            patch = request.get('patch', {})
            if not isinstance(patch, dict) or any(k not in FIELDS or not isinstance(v, str) for k, v in patch.items()):
                raise ConfigError('包含不支持的编辑字段')
            if name in BUILTINS and any(k not in ('model', 'model_reasoning_effort') for k in patch):
                raise ConfigError('官方角色仅支持修改模型与推理强度')
            if patch.get('model_reasoning_effort', '') not in EFFORTS or patch.get('sandbox_mode', '') not in SANDBOXES:
                raise ConfigError('推理强度或权限值无效')
            changes, effective, versions = [], {}, {}
            for key, home in homes.items():
                if request.get('revisions', {}).get(key) != home.fingerprint():
                    raise ConfigError('配置已发生变化，请刷新页面后重新编辑')
                versions[key] = home.fingerprint()
                role = home.roles.get(name)
                if request.get('create') and role:
                    raise ConfigError(f'{key} 已存在 {name}；新增不能覆盖已有角色')
                if not role and operation == 'delete':
                    continue
                doc = copy.deepcopy(role.doc) if role else tomlkit.document()
                if role is None and operation == 'save':
                    template = request.get('template', {})
                    if not isinstance(template, dict) or any(k not in FIELDS or not isinstance(v, str) for k, v in template.items()):
                        raise ConfigError('新增角色的模板字段无效')
                    for field, value in template.items():
                        if value:
                            doc[field] = value
                root = copy.deepcopy(home.config)
                root_changed = False
                target = role.path if role else None
                registered = role.registered if role else False
                shared = bool(target and key == 'orca' and not target.is_relative_to(home.path))
                native_role = homes['native'].roles.get(name) if shared and scope == 'both' else None
                if shared and (not native_role or native_role.path != target):
                    raise ConfigError('Orca 的角色文件与原生配置不一致，无法同时修改')
                if operation == 'delete':
                    if target and not shared:
                        changes.append(self.change(key, target, None))
                    if registered:
                        del root['agents'][name]
                        root_changed = True
                else:
                    if target is None:
                        if name in BUILTINS or registered:
                            # Registered config layers allow model-only overrides without replacing
                            # inherited developer instructions with a required standalone prompt.
                            target = home.path / 'agent-overrides' / f'{name}.toml'
                            if target.exists():
                                raise ConfigError(f'目标文件已存在且未被此角色引用：{target}')
                            root.setdefault('agents', tomlkit.table())
                            root['agents'].setdefault(name, tomlkit.table())
                            declaration = root['agents'][name]
                            declaration.setdefault('description', BUILTINS[name][1] if name in BUILTINS else str(doc.get('description', '')))
                            declaration['config_file'] = f'agent-overrides/{name}.toml'
                            root_changed = True
                        else:
                            target = home.path / 'agents' / f'{name}.toml'
                            if target.exists():
                                raise ConfigError(f'目标文件已存在：{target}')
                            doc['name'] = name
                    for field, value in patch.items():
                        if not value and field in ('model', 'model_reasoning_effort', 'sandbox_mode'):
                            doc.pop(field, None)
                        else:
                            doc[field] = value
                    if name not in BUILTINS:
                        for field in ('description', 'developer_instructions'):
                            if not str(doc.get(field, '')).strip():
                                raise ConfigError(f'{key} 的新角色必须填写描述和提示词')
                    if registered and 'description' in patch:
                        root['agents'][name]['description'] = patch['description']
                        root_changed = True
                    current = home.effective(doc)
                    if doc.get('model_reasoning_effort', '') not in EFFORTS or doc.get('sandbox_mode', '') not in SANDBOXES:
                        raise ConfigError('推理强度或权限值无效')
                    model = str(current.get('model', ''))
                    if name in BUILTINS and not model.lower().startswith('gpt-'):
                        raise ConfigError('官方内置角色及其覆盖只允许 GPT 系列模型，请输入 gpt- 开头的模型 ID')
                    if not model:
                        raise ConfigError('必须配置可供测试的模型')
                    effective[key] = current
                    if not shared:
                        changes.append(self.change(key, target, encode(doc, read_bytes(target))))
                if root_changed:
                    changes.append(self.change(key, home.config_path, encode(root, home.config_bytes)))
            changes = [c for c in changes if c['before'] != c['after']]
            if not changes:
                raise ConfigError('没有需要保存的变更')
            # Shared hard links would silently affect a scope not selected by the user.
            for c in changes:
                if not c['path'].resolve().is_relative_to(self.homes[c['home']]):
                    raise ConfigError('目标文件已指向所选配置范围之外，不能保存')
                if c['path'].exists() and c['path'].stat().st_nlink > 1:
                    raise ConfigError('目标文件存在硬链接，无法独立修改：' + str(c['path']))
            plan = {'scope': scope, 'name': name, 'operation': operation, 'changes': changes,
                    'versions': versions, 'effective': effective, 'proofs': {}, 'created': time.time()}
            return self.store_plan(plan)

    @staticmethod
    def change(key, path, after):
        return {'home': key, 'path': path, 'before': read_bytes(path), 'after': after}

    def store_plan(self, plan):
        self.plans = {k: v for k, v in self.plans.items() if time.time() - v['created'] < 1800}
        key = uuid.uuid4().hex
        self.plans[key] = plan
        changes = []
        for c in plan['changes']:
            # Zero context keeps unrelated provider secrets in config.toml out of the UI.
            before = (c['before'] or b'').decode('utf-8-sig').splitlines()
            after = (c['after'] or b'').decode('utf-8-sig').splitlines()
            diff = '\n'.join(difflib.unified_diff(before, after, fromfile='修改前', tofile='修改后', n=0))
            # Agent files can also contain credentials in unknown fields. Never echo deletions
            # or creations wholesale: preview only the fields this tool manages.
            if c['path'].name != 'config.toml':
                def visible(raw):
                    doc = parse(raw, c['path']) if raw is not None else {}
                    return json.dumps({k: str(doc[k]) for k in ('name', *FIELDS) if k in doc},
                                      ensure_ascii=False, indent=2).splitlines()
                diff = '\n'.join(difflib.unified_diff(visible(c['before']), visible(c['after']),
                                                    fromfile='修改前', tofile='修改后', n=1))
            changes.append({'home': c['home'], 'path': str(c['path']), 'diff': diff,
                            'action': '删除' if c['after'] is None else '新增' if c['before'] is None else '修改'})
        return {'id': key, 'scope': plan['scope'], 'name': plan['name'], 'operation': plan['operation'],
                'changes': changes, 'needs_test': plan['operation'] == 'save'}

    def get_plan(self, key):
        plan = self.plans.get(key)
        if not plan or time.time() - plan['created'] > 1800:
            raise ConfigError('预览已过期，请重新生成')
        return plan

    def verify(self, plan):
        for key, revision in plan['versions'].items():
            if self.load(key).fingerprint() != revision:
                raise ConfigError('文件已被其他程序修改，请重新预览')
        for c in plan['changes']:
            if not c['path'].resolve().is_relative_to(self.homes[c['home']]):
                raise ConfigError('文件链接已改变，请重新检查配置范围')
            if read_bytes(c['path']) != c['before']:
                raise ConfigError('目标文件已改变，请重新预览')

    def test(self, key):
        with self.lock:
            plan = self.get_plan(key)
            self.verify(plan)
            if plan['operation'] != 'save':
                raise ConfigError('此操作无需模型测试')
            work = [(k, self.load(k), config) for k, config in plan['effective'].items()]
            plan['proofs'] = {}
        results = []
        for home_key, home, effective in work:
            try:
                result = self.tester(home, effective)
                with self.lock:
                    plan['proofs'][home_key] = {'fingerprint': result['fingerprint'], 'time': time.time()}
                results.append({'home': home_key, 'ok': True, **{k: v for k, v in result.items() if k != 'fingerprint'}})
            except ConfigError as exc:
                results.append({'home': home_key, 'ok': False, 'message': str(exc)})
        return {'results': results, 'passed': all(r['ok'] for r in results)}

    def apply(self, key, confirmed=False):
        with self.lock:
            plan = self.get_plan(key)
            if not confirmed:
                raise ConfigError('请确认预览中的文件和影响范围')
            self.verify(plan)
            for home_key, effective in plan['effective'].items():
                proof = plan['proofs'].get(home_key)
                fingerprint = connection(self.load(home_key), effective)[3]
                if not proof or proof['fingerprint'] != fingerprint or time.time() - proof['time'] > 600:
                    raise ConfigError('需要在 10 分钟内通过所有目标配置的连通性测试；模型、服务商或认证改变后需重测')
            backup_id = self.transact(plan)
            del self.plans[key]
            return {'ok': True, 'backup_id': backup_id}

    def transact(self, plan):
        self.storage.mkdir(parents=True, exist_ok=True)
        backup_id = time.strftime('%Y%m%d-%H%M%S') + f'-{time.time_ns():020d}-' + uuid.uuid4().hex[:8]
        path = self.storage / (backup_id + '.json')
        item = {'status': 'pending', 'time': time.strftime('%Y-%m-%d %H:%M:%S'),
                'homes': {key: str(self.homes[key]) for key in self.targets(plan['scope'])},
                'name': plan['name'], 'scope': plan['scope'], 'operation': plan['operation'],
                'changes': [{**c, 'path': str(c['path']), 'before': pack(c['before']),
                             'after': pack(c['after'])} for c in plan['changes']]}
        atomic_write(path, json.dumps(item, ensure_ascii=False, indent=2).encode())
        done = []
        try:
            for c in plan['changes']:
                if read_bytes(c['path']) != c['before']:
                    raise ConfigError('写入前文件已改变')
                self.writer(c['path'], c['after'])
                done.append(c)
            item['status'] = 'applied'
            atomic_write(path, json.dumps(item, ensure_ascii=False, indent=2).encode())
        except Exception:
            try:
                for c in reversed(done):
                    if read_bytes(c['path']) != c['after']:
                        raise ConfigError('回滚期间文件被其他程序修改')
                    atomic_write(c['path'], c['before'])
                item['status'] = 'rolled_back'
                atomic_write(path, json.dumps(item, ensure_ascii=False, indent=2).encode())
            except Exception:
                self.recovery_errors.append(f'事务 {backup_id} 未能自动恢复，请保留备份并检查文件')
                raise ConfigError(self.recovery_errors[-1]) from None
            raise ConfigError('写入未完成，本次已写入的变更已回滚') from None
        return backup_id

    def restore_preview(self, backup_id):
        with self.lock:
            if not self.backups() or self.backups()[0]['id'] != backup_id:
                raise ConfigError('仅支持恢复最近一次操作，避免覆盖后续变更')
            item = json.loads((self.storage / (backup_id + '.json')).read_text('utf-8'))
            homes = self.check_homes(self.targets(item['scope']))
            changes = []
            for c in item['changes']:
                path = Path(c['path'])
                if not path.resolve().is_relative_to(self.homes[c['home']]):
                    raise ConfigError('备份路径不属于当前配置范围')
                if read_bytes(path) != unpack(c['after']):
                    raise ConfigError('配置在上次操作后已改变，不能直接恢复')
                changes.append({'home': c['home'], 'path': path, 'before': unpack(c['after']),
                                'after': unpack(c['before'])})
            return self.store_plan({'scope': item['scope'], 'name': item['name'], 'operation': 'restore',
                                    'changes': changes, 'versions': {k: h.fingerprint() for k, h in homes.items()},
                                    'effective': {}, 'proofs': {}, 'created': time.time()})

    def recover(self):
        for path in sorted(self.storage.glob('*.json')):
            try:
                item = json.loads(path.read_text('utf-8'))
                if item['status'] != 'pending' or not self.matches_homes(item):
                    continue
                for c in item['changes']:
                    target = Path(c['path'])
                    if not target.resolve().is_relative_to(self.homes[c['home']]):
                        raise ConfigError('备份路径已改变')
                    if read_bytes(target) not in (unpack(c['before']), unpack(c['after'])):
                        raise ConfigError('文件有后续变更')
                for c in reversed(item['changes']):
                    atomic_write(Path(c['path']), unpack(c['before']))
                item['status'] = 'recovered'
                atomic_write(path, json.dumps(item, ensure_ascii=False, indent=2).encode())
            except Exception:
                self.recovery_errors.append(f'无法恢复事务 {path.stem}，请检查备份')
