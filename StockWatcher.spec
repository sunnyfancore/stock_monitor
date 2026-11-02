# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置

程序只用 Python 标准库 + ctypes；这里剔除用不到的模块以减小体积，并打包公共根证书清单。
打包：python -m PyInstaller --noconfirm --clean StockWatcher.spec（需先 pip install pyinstaller certifi）
"""

EXCLUDES = [
    "tkinter", "_tkinter", "unittest", "pydoc", "pydoc_data", "test", "lib2to3",
    # 注意：不能剔除 ssl —— 所有请求都是 https
    "sqlite3", "_sqlite3", "xml", "pyexpat", "_decimal", "decimal",
    "multiprocessing", "concurrent", "asyncio", "distutils", "setuptools",
    "pkg_resources", "lzma", "_lzma", "bz2", "_bz2", "curses", "readline",
    "PySide6", "PyQt5", "PyQt6", "requests", "urllib3", "numpy", "pip", "certifi",
]

# 打包公共根证书清单（Mozilla / certifi 的 cacert.pem）：
# 中转连接只信任这份清单，公司 / 抓包工具装进系统的根证书无效
DATAS = []
for _mod in ("certifi", "pip._vendor.certifi"):
    try:
        import importlib
        DATAS.append((importlib.import_module(_mod).where(), "."))
        break
    except Exception:
        pass
if not DATAS:
    print("警告：没找到 cacert.pem（pip install certifi），中转将退回系统证书库校验")

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=DATAS,
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
