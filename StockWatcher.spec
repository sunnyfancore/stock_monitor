# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（纯 Win32 原生版）

程序只用 Python 标准库 + ctypes，不再依赖 Qt / requests。
这里剔除 tkinter、sqlite 等用不到的模块（保留 ssl 供名称搜索使用），成品预计约 8~12MB。
打包：python -m PyInstaller --noconfirm --clean StockWatcher.spec
"""

EXCLUDES = [
    "tkinter", "_tkinter", "unittest", "pydoc", "pydoc_data", "test", "lib2to3",
    # 注意：不能剔除 ssl —— 名称搜索接口 smartbox.gtimg.cn 只支持 https
    "sqlite3", "_sqlite3", "xml", "pyexpat", "_decimal", "decimal",
    "multiprocessing", "concurrent", "asyncio", "distutils", "setuptools",
    "pkg_resources", "lzma", "_lzma", "bz2", "_bz2", "curses", "readline",
    "PySide6", "PyQt5", "PyQt6", "requests", "urllib3", "numpy",
]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=1,
)
a.binaries = [b for b in a.binaries
              if not any(k in b[0].lower() for k in ("sqlite3", "tcl", "tk86"))]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='StockWatcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
