"""Local-only directory selection. Never creates or changes a Codex configuration."""
import json
import os
from pathlib import Path

from .config import ConfigError, Home, default_homes
from .manager import Manager, atomic_write


class Settings:
    def __init__(self, file, detected=None):
        self.file = Path(file)
        self.detected = detected or default_homes()
        self.homes = dict(self.detected)
        self.enabled = set(self.homes)
        self.configured = False
        self.error = None
        if self.file.exists():
            try:
                saved = json.loads(self.file.read_text('utf-8'))
                if set(saved['homes']) != {'native', 'orca'}:
                    raise ValueError()
                self.homes = {k: Path(v).expanduser().resolve() for k,v in saved['homes'].items()}
                self.enabled = set(saved['enabled'])
                if not self.enabled or not self.enabled <= set(self.homes):
                    raise ValueError()
                self.configured = True
            except (ValueError, KeyError, TypeError, OSError):
                self.homes = dict(self.detected)
                self.enabled = set(self.homes)
                self.error = '本机目录设置无法读取，请重新选择并保存。'

    def manager(self):
        return Manager(self.homes, self.file.parent / 'backups', enabled=self.enabled)

    @staticmethod
    def inspect(path, key, native_path=None):
        try:
            home = Home(key, path, native_path if key == 'orca' else None)
            if home.errors:
                return {'ok': False, 'message': '；'.join(home.errors)}
            return {'ok': True, 'message': f'已识别 {len(home.roles)} 个角色', 'roles': len(home.roles)}
        except (ConfigError, OSError) as exc:
            return {'ok': False, 'message': str(exc) if isinstance(exc, ConfigError) else '目录无法读取，请检查路径和权限'}

    def state(self):
        return {'configured': self.configured, 'error': self.error,
                'homes': {k: {'path': str(p), 'enabled': k in self.enabled,
                              'detected_path': str(self.detected[k]), **self.inspect(p,k,self.homes['native'])}
                          for k,p in self.homes.items()}}

    def validate(self, body):
        if not isinstance(body.get('homes'), dict) or set(body['homes']) != {'native','orca'}:
            raise ConfigError('需要分别填写原生 Codex 与 Orca 的目录设置')
        homes, enabled = {}, []
        for key, item in body['homes'].items():
            if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not isinstance(item.get('enabled'), bool):
                raise ConfigError('目录设置格式无效')
            raw = item['path'].strip()
            path = Path(os.path.expandvars(raw)).expanduser()
            if not raw or not path.is_absolute():
                raise ConfigError('请输入配置目录的绝对路径，选择包含 config.toml 的文件夹')
            homes[key] = path.resolve()
            if item['enabled']:
                enabled.append(key)
        checks = {key: self.inspect(path, key, homes['native']) for key, path in homes.items()}
        if not enabled:
            raise ConfigError('请至少启用一套配置')
        if len(enabled) == 2:
            a,b = homes.values()
            if a.is_relative_to(b) or b.is_relative_to(a) or (a/'agents').resolve() == (b/'agents').resolve():
                raise ConfigError('两套配置必须使用独立目录，不能相同、相互嵌套或共享 agents 链接')
        return homes, enabled, checks

    def check(self, body):
        _, enabled, checks = self.validate(body)
        return {'checks': checks, 'valid': all(checks[k]['ok'] for k in enabled)}

    def save(self, manager, body):
        with manager.lock:
            homes, enabled, checks = self.validate(body)
            for key in enabled:
                if not checks[key]['ok']:
                    raise ConfigError(('原生 Codex' if key == 'native' else 'Orca') + '：' + checks[key]['message'])
            payload = {'homes': {k:str(v) for k,v in homes.items()}, 'enabled': enabled}
            atomic_write(self.file, json.dumps(payload,ensure_ascii=False,indent=2).encode())
            self.homes, self.enabled = homes, set(enabled)
            self.configured, self.error = True, None
            manager.change_homes(homes, enabled)
            return self.state()


def choose_directory(initial):
    # Browser directory-upload controls cannot supply a usable local absolute path.
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes('-topmost', True)
            return filedialog.askdirectory(parent=root, title='选择包含 config.toml 的 Codex 配置目录',
                                           initialdir=initial if Path(initial).is_dir() else str(Path.home())) or None
        finally:
            root.destroy()
    except Exception:
        raise ConfigError('当前环境无法打开文件夹选择器，请直接输入完整路径') from None
