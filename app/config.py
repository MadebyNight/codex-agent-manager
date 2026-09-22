"""Read Codex's two personal configuration homes without changing them."""
import hashlib
import copy
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import tomlkit

BUILTINS = {
    'default': ('通用代理', 'Default agent.'),
    'worker': ('执行与实现', '''Use for execution and production work.
Typical tasks:
- Implement part of a feature
- Fix tests or bugs
- Split large refactors into independent chunks
Rules:
- Explicitly assign **ownership** of the task (files / responsibility). When the subtask involves code changes, you should clearly specify which files or modules the worker is responsible for. This helps avoid merge conflicts and ensures accountability. For example, you can say "Worker 1 is responsible for updating the authentication module, while Worker 2 will handle the database layer." By defining clear ownership, you can delegate more effectively and reduce coordination overhead.
- Always tell workers they are **not alone in the codebase**, and they should not revert the edits made by others, and they should adjust their implementation to accommodate the changes made by others. This is important because there may be multiple workers making changes in parallel, and they need to be aware of each other's work to avoid conflicts and ensure a cohesive final product.'''),
    'explorer': ('代码探索', '''Use `explorer` for specific codebase questions.
Explorers are fast and authoritative.
They must be used to ask specific, well-scoped questions on the codebase.
Rules:
- In order to avoid redundant work, you should avoid exploring the same problem that explorers have already covered. Typically, you should trust the explorer results without additional verification. You are still allowed to inspect the code yourself to gain the needed context!
- You are encouraged to spawn up multiple explorers in parallel when you have multiple distinct questions to ask about the codebase that can be answered independently. This allows you to get more information faster without waiting for one question to finish before asking the next. While waiting for the explorer results, you can continue working on other local tasks that do not depend on those results. This parallelism is a key advantage of delegation, so use it whenever you have multiple questions to ask.
- Reuse existing explorers for related questions.'''),
}
FIELDS = ('description', 'model', 'model_reasoning_effort', 'sandbox_mode', 'developer_instructions')
EFFORTS = ('', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')
SANDBOXES = ('', 'read-only', 'workspace-write', 'danger-full-access')
NAME = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
RESERVED_NAMES = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}


class ConfigError(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def read_bytes(path):
    return path.read_bytes() if path.is_file() else None


def parse(data, path):
    try:
        return tomlkit.parse(data.decode('utf-8-sig'))
    except Exception as exc:
        # Parse errors can contain credential-bearing source lines.
        raise ConfigError(f'TOML 无法解析：{path.name}（{type(exc).__name__}）') from None


def encode(doc, original=None):
    text = tomlkit.dumps(doc)
    if original and b'\r\n' in original:
        text = text.replace('\r\n', '\n').replace('\n', '\r\n')
    data = text.encode('utf-8')
    return b'\xef\xbb\xbf' + data if original and original.startswith(b'\xef\xbb\xbf') else data


def default_homes():
    native = Path.home() / '.codex'
    orca = Path(os.environ.get('ORCA_CODEX_HOME') or
                str(Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) /
                    'orca/codex-runtime-home/home'))
    candidate = os.environ.get('CODEX_HOME')
    if candidate:
        candidate = Path(candidate).expanduser().resolve()
        # Orca injects CODEX_HOME into its terminals. Do not mistake it for native Codex.
        is_orca = candidate == orca.resolve() or 'codex-runtime-home' in candidate.parts or 'codex-accounts' in candidate.parts
        if not is_orca:
            native = candidate
    return {'native': native.resolve(), 'orca': orca.resolve()}


def merge_config(base, layer):
    result = copy.deepcopy(base)
    for key, value in layer.items():
        result[key] = merge_config(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else copy.deepcopy(value)
    return result


@dataclass
class Role:
    name: str
    doc: object
    path: Path | None
    registered: bool = False


class Home:
    def __init__(self, key, path):
        self.key, self.path = key, path
        self.config_path = path / 'config.toml'
        self.config_bytes = read_bytes(self.config_path)
        if self.config_bytes is None:
            raise ConfigError(f'未找到配置：{self.config_path}')
        self.config = parse(self.config_bytes, self.config_path)
        self.roles = {}
        self.snapshots = {self.config_path: self.config_bytes}
        self.errors = []
        declarations = self.config.get('agents', {})
        for name, entry in declarations.items():
            if not isinstance(entry, dict) or not ('config_file' in entry or 'description' in entry):
                continue
            try:
                filename = entry.get('config_file')
                target = (path / filename).resolve() if filename else None
                if target and not target.is_relative_to(path.resolve()):
                    raise ConfigError(f'{name} 引用了配置目录之外的文件，暂不支持编辑')
                raw = read_bytes(target) if target else None
                if target in self.snapshots:
                    raise ConfigError(f'{name} 与其他配置共享 config_file，暂不支持独立编辑')
                if target and raw is None:
                    raise ConfigError(f'{name} 的 config_file 不存在')
                doc = parse(raw, target) if raw is not None else tomlkit.document()
                doc.setdefault('description', entry.get('description', ''))
                self.add(Role(name, doc, target, True))
                if target:
                    self.snapshots[target] = raw
            except ConfigError as exc:
                self.errors.append(str(exc))
        for target in sorted((path / 'agents').glob('*.toml')):
            if target.resolve() in self.snapshots:
                continue
            raw = target.read_bytes()
            self.snapshots[target] = raw
            try:
                if not target.resolve().is_relative_to(path.resolve()):
                    raise ConfigError(f'{target.name} 是指向目录外的链接，暂不支持编辑')
                doc = parse(raw, target)
                for field in ('name', 'description', 'developer_instructions'):
                    if not isinstance(doc.get(field), str):
                        raise ConfigError(f'{target.name} 缺少字符串字段 {field}')
                self.add(Role(str(doc['name']), doc, target))
            except ConfigError as exc:
                self.errors.append(str(exc))
        for name in BUILTINS:
            self.roles.setdefault(name, Role(name, tomlkit.document(), None))

    def add(self, role):
        if not NAME.fullmatch(role.name):
            raise ConfigError(f'不支持的角色名称：{role.name}')
        if role.name.lower() in self.roles:
            raise ConfigError(f'角色 {role.name} 重复定义，请先在文件中消除冲突')
        self.roles[role.name] = role

    def fingerprint(self):
        files = {str(p): digest(b) for p, b in self.snapshots.items()}
        return digest(json.dumps(files, sort_keys=True).encode())

    def effective(self, doc):
        # Profiles form part of the selected Codex configuration.
        config = self.config.unwrap()
        profile = config.get('profile')
        if profile:
            config = merge_config(config, config.get('profiles', {}).get(profile, {}))
        agents = config.get('agents', {})
        config['model'] = agents.get('default_subagent_model', config.get('model', ''))
        config['model_reasoning_effort'] = agents.get('default_subagent_reasoning_effort',
                                                     config.get('model_reasoning_effort', ''))
        return merge_config(config, doc.unwrap() if hasattr(doc, 'unwrap') else doc)

    def public_role(self, role):
        effective = self.effective(role.doc)
        return {'name': role.name, 'builtin': role.name in BUILTINS,
                'overridden': role.path is not None or role.registered,
                'path': str(role.path) if role.path else None,
                'registered': role.registered,
                'values': {k: str(role.doc.get(k, '')) for k in FIELDS},
                'effective_model': effective.get('model', ''),
                'effective_effort': effective.get('model_reasoning_effort', ''),
                'provider': effective.get('model_provider', 'openai')}
