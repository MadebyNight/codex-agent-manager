"""Windowless entry point for the portable Windows build."""
import sys
from pathlib import Path


def run():
    if getattr(sys, 'frozen', False):
        logs = Path(sys.executable).resolve().parent / '.local'
        logs.mkdir(parents=True, exist_ok=True)
        sys.stdout = (logs / 'server.log').open('a', encoding='utf-8', buffering=1)
        sys.stderr = (logs / 'server-error.log').open('a', encoding='utf-8', buffering=1)
    try:
        from app.server import main
        main()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        if '--no-open' not in sys.argv:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror('Codex Agent Manager', f'面板启动失败：{exc}\n请检查解压目录是否可写、端口是否被占用。', parent=root)
            root.destroy()
        sys.exit(1)


if __name__ == '__main__':
    run()
