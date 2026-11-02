# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置

产物：dist/NetMonitor.exe —— 中性的文件名、图标（蓝色柱状图）和版本信息（Network Monitor），
在任务管理器、资源管理器里看不出是盯盘工具。
程序只用 Python 标准库 + ctypes；剔除用不到的模块以减小体积，并打包公共根证书清单。
打包：python -m PyInstaller --noconfirm --clean StockWatcher.spec（需先 pip install pyinstaller certifi）
版本号：环境变量 APP_VERSION（如 1.0.12），不设为 1.0.0
"""
import os
import re
import struct

EXE_NAME = "NetMonitor"
DESCRIPTION = "Network Monitor"

EXCLUDES = [
    "tkinter", "_tkinter", "unittest", "pydoc", "pydoc_data", "test", "lib2to3",
    # 注意：不能剔除 ssl —— 所有请求都是 https
    "sqlite3", "_sqlite3", "xml", "pyexpat", "_decimal", "decimal",
    "multiprocessing", "concurrent", "asyncio", "distutils", "setuptools",
    "pkg_resources", "lzma", "_lzma", "bz2", "_bz2", "curses", "readline",
    "PySide6", "PyQt5", "PyQt6", "requests", "urllib3", "numpy", "pip", "certifi",
]

# ---------------------------------------------------------------- 公共根证书清单
# 中转连接只信任这份清单，公司 / 抓包工具装进系统的根证书无效；缺少时程序会拒绝中转连接
DATAS = []
for _mod in ("certifi", "pip._vendor.certifi"):
    try:
        import importlib
        DATAS.append((importlib.import_module(_mod).where(), "."))
        break
    except Exception:
        pass
if not DATAS:
    raise SystemExit("缺少 cacert.pem：请先 pip install certifi")

os.makedirs(workpath, exist_ok=True)


# ---------------------------------------------------------------- 中性图标（蓝底白色柱状图）
def _icon_image(n):
    bg = (200, 120, 30, 255)          # BGRA：蓝色 (30,120,200)
    fg = (255, 255, 255, 255)
    px = [[(0, 0, 0, 0)] * n for _ in range(n)]
    r = max(2, n // 5)
    for y in range(n):
        for x in range(n):
            dx = max(r - x - 0.5, x + 0.5 - (n - r), 0)
            dy = max(r - y - 0.5, y + 0.5 - (n - r), 0)
            if dx * dx + dy * dy <= r * r:
                px[y][x] = bg
    bw, gap = max(2, n // 7), max(1, n // 12)
    x0 = (n - (4 * bw + 3 * gap)) // 2
    bottom, top = n - max(2, n // 5), max(2, n // 5)
    for k, frac in enumerate((0.40, 0.70, 0.50, 1.00)):
        h = max(2, int((bottom - top) * frac))
        for y in range(bottom - h, bottom):
            for x in range(x0 + k * (bw + gap), x0 + k * (bw + gap) + bw):
                px[y][x] = fg
    rows = b"".join(bytes(c for p in px[y] for c in p) for y in range(n - 1, -1, -1))  # BMP 自下而上
    mask = bytes(((n + 31) // 32 * 4) * n)
    head = struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0, len(rows) + len(mask), 0, 0, 0, 0)
    return head + rows + mask


def _write_icon(path, sizes=(16, 20, 24, 32, 48, 64)):
    imgs = [_icon_image(n) for n in sizes]
    out = struct.pack("<HHH", 0, 1, len(imgs))
    offset = 6 + 16 * len(imgs)
    for n, data in zip(sizes, imgs):
        out += struct.pack("<BBBBHHII", n, n, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open(path, "wb") as f:
        f.write(out + b"".join(imgs))


ICON_PATH = os.path.join(workpath, "netmonitor.ico")
_write_icon(ICON_PATH)

# ---------------------------------------------------------------- 版本信息（文件属性里显示）
_ver = re.findall(r"\d+", os.environ.get("APP_VERSION", "1.0.0"))[:4]
_ver = [int(v) for v in _ver] + [0] * (4 - len(_ver))
_ver_s = ".".join(str(v) for v in _ver[:3])
VERSION_PATH = os.path.join(workpath, "version_info.txt")
with open(VERSION_PATH, "w", encoding="utf-8") as f:
    f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple(_ver)}, prodvers={tuple(_ver)}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', ''),
      StringStruct('FileDescription', '{DESCRIPTION}'),
      StringStruct('FileVersion', '{_ver_s}'),
      StringStruct('InternalName', '{EXE_NAME}'),
      StringStruct('OriginalFilename', '{EXE_NAME}.exe'),
      StringStruct('ProductName', '{DESCRIPTION}'),
      StringStruct('ProductVersion', '{_ver_s}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""")

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
    name=EXE_NAME,
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
    icon=[ICON_PATH],
    version=VERSION_PATH,
)
