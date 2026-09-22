"""Build a Windows x64 portable ZIP; never include local config or credentials."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import __version__


def main():
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise SystemExit('Build this release with 64-bit Python on Windows x64.')
    destination = ROOT / 'dist'
    destination.mkdir(exist_ok=True)
    name = f'codex-agent-manager-v{__version__}-windows-x64'
    # A venv created from Conda does not expose Library/bin to PyInstaller.
    binaries = []
    conda_bin = Path(sys.base_prefix) / 'Library/bin'
    if conda_bin.is_dir():
        for dll in ('libmpdec-4.dll', 'libcrypto-3-x64.dll', 'libssl-3-x64.dll',
                    'liblzma.dll', 'libbz2.dll', 'tk86t.dll', 'tcl86t.dll', 'ffi.dll', 'zlib.dll'):
            binaries.extend(['--add-binary', f'{conda_bin / dll}:.'])
    with tempfile.TemporaryDirectory(prefix='codex-manager-build-') as temp:
        temp = Path(temp)
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed',
                        '--name', 'CodexAgentManager', '--distpath', str(temp/'dist'),
                        '--workpath', str(temp/'build'), '--specpath', str(temp),
                        '--paths', str(ROOT), '--add-data', f'{ROOT / "web"}:web',
                        *binaries, str(ROOT/'launcher.py')], cwd=ROOT, check=True)
        bundle = temp/'dist/CodexAgentManager'
        shutil.copyfile(ROOT/'README.md', bundle/'README.md')
        (bundle/'docs').mkdir()
        for screenshot in (ROOT/'docs').glob('preview-*.png'):
            shutil.copyfile(screenshot, bundle/'docs'/screenshot.name)
        shutil.copyfile(ROOT/'LICENSE', bundle/'LICENSE')
        shutil.copyfile(ROOT/'THIRD_PARTY_NOTICES.md', bundle/'THIRD_PARTY_NOTICES.md')
        notices = bundle/'licenses'
        shutil.copytree(ROOT/'licenses', notices)
        shutil.copyfile(ROOT/'web/icons/LICENSE', notices/'Lucide-LICENSE')
        for dependency in ('tomlkit', 'pyinstaller'):
            distribution = importlib.metadata.distribution(dependency)
            for file in distribution.files or []:
                if file.name.lower().startswith(('license', 'copying')) and '.dist-info' in str(file):
                    shutil.copyfile(distribution.locate_file(file), notices/f'{dependency}-{file.name}')
        python_license = next((Path(sys.base_prefix)/f for f in ('LICENSE_PYTHON.txt','LICENSE.txt') if (Path(sys.base_prefix)/f).exists()), None)
        if python_license:
            shutil.copyfile(python_license, notices/'Python-LICENSE.txt')
        for candidate in (Path(sys.base_prefix)/'tcl', Path(sys.base_prefix)/'Library/lib'):
            if candidate.exists():
                for file in candidate.glob('*/license.terms'):
                    shutil.copyfile(file, notices/f'{file.parent.name}-license.terms')
        (bundle/'停止面板.cmd').write_text('@echo off\n"%~dp0CodexAgentManager.exe" --stop\n',encoding='ascii')
        (bundle/'开始使用.txt').write_text('Codex Agent Manager '+__version__+'\n\n'
            '1. 完整解压 ZIP 到可写目录，保留 _internal 文件夹。\n'
            '2. 双击 CodexAgentManager.exe，浏览器会自动打开。\n'
            '3. 确认原生 Codex / Orca 配置目录，未安装的一套取消启用。\n'
            '4. 选择角色、修改模型、预览并测试，通过后保存。\n\n'
            '无需安装 Python、Node.js 或依赖。请先在本机安装并配置 Codex / Orca。\n'
            '关闭浏览器不会退出后台；双击“停止面板.cmd”退出。\n'
            '本机设置和备份保存在 .local。更新时停止旧版，保留 .local，再使用新版文件。\n'
            '下载与说明：https://github.com/MadebyNight/codex-agent-manager/releases\n',encoding='utf-8-sig')
        (bundle/'build-info.json').write_text(json.dumps({'version':__version__, 'python':platform.python_version(),
            'architecture':platform.machine(), 'pyinstaller':importlib.metadata.version('pyinstaller')},indent=2),encoding='utf-8')
        archive = destination/(name+'.zip')
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as output:
            for file in sorted(bundle.rglob('*')):
                if file.is_file():
                    output.write(file, Path(name)/file.relative_to(bundle))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (destination/'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n',encoding='ascii')
        print(f'Release: {archive}\nSHA256: {digest}',flush=True)


if __name__ == '__main__':
    main()
