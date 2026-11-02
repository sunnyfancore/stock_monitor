# -*- coding: utf-8 -*-
"""盯盘小窗 —— Windows 任务栏盯盘小组件（Python 标准库 + ctypes 调用 Win32 API）

组成：
  1. 任务栏条（Bar）
     - 嵌入：作为 Shell_TrayWnd 的子窗口创建，与任务栏融为一体。
       Win10 缩窄任务按钮区（MSTaskSwWClass）让出位置；Win11 显示在托盘区左侧。
     - 覆盖：透明置顶窗口（嵌入失败时自动使用，也可在菜单切换）
     - 逐像素 Alpha 抗锯齿文字（UpdateLayeredWindow），背景透明
  2. 面板（Panel）：鼠标停留展开；单击条 / 图钉 → 常驻；拖动标题栏移动（位置自动记忆）；搜索添加
  3. 伪装：正常 / 隐私 / 网速伪装；老板键一键隐藏
  4. 联网：未选择联网方式前不发任何请求；直连腾讯（https）或经自建服务器加密中转
  5. 托盘、开机自启、单实例、Explorer 重启自愈、睡眠唤醒刷新、日志

配置：%APPDATA%\\StockWatcher\\stocks.json
"""
import base64
import ctypes
import datetime
import json
import os
import shutil
import sys
import threading
import time
import traceback
from ctypes import byref

import winapi as W
from quotes import (Fetcher, PinError, RelayAuthError, Searcher, looks_like_code, market_key,
                    market_open, normalize, parse_relay, pretty_code, us_eastern)


class RelayNotConfigured(Exception):
    """开启了中转、又不允许直连，但没有可用的连接串：不发任何请求"""

APP_NAME = "StockWatcher"
APP_TITLE = "盯盘小窗"
HOST_CLS = "StockWatcherHost"
BAR_CLS = "StockWatcherBar"
PANEL_CLS = "StockWatcherPanel"

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CFG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
CFG_PATH = os.path.join(CFG_DIR, "stocks.json")
CACHE_PATH = os.path.join(CFG_DIR, "cache.json")     # 最后一次行情，启动时先显示
LEGACY_CFG = os.path.join(BASE_DIR, "stocks.json")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WIN_BUILD = sys.getwindowsversion().build
IS_WIN11 = WIN_BUILD >= 22000

INTERVALS = (1, 2, 3, 5, 10)
ROTATE_INTERVALS = (2, 3, 5, 8, 10, 15, 20, 30, 60)
ROTATE_TOPS = (2, 3, 5, 8, 10)     # 轮播前 N 只；0 = 全部
ALIAS_MAX = 8                      # 别名最多 8 个字
REF_INDEX = {"a": "sh000001", "hk": "r_hkHSI", "us": "usDJI"}   # 节假日识别用的参考指数
DISGUISE_NAMES = ("正常显示", "隐私模式（无名称、无红绿）", "网速监视器伪装")
FAKE_NAMES = ("svchost.exe", "RuntimeBroker.exe", "explorer.exe", "dwm.exe",
              "SearchHost.exe", "System", "ctfmon.exe", "sihost.exe", "taskhostw.exe",
              "audiodg.exe", "spoolsv.exe", "WmiPrvSE.exe", "conhost.exe", "fontdrvhost.exe")

DEFAULT_CFG = {
    "stocks": ["sh600519", "sz300750", "r_hk00700"],
    "pinned": "sh600519",          # 显示在任务栏上的那只
    "interval": 3,
    "disguise": 0,                 # 0 正常 / 1 隐私 / 2 网速伪装
    "bar_mode": "embed",           # embed 原生嵌入 / overlay 悬浮覆盖
    "panel_pinned": False,         # 面板常驻
    "panel_pos": None,             # 常驻面板位置（物理像素）
    "panel_topmost": True,
    "hide_fullscreen": True,
    "boss_key": "Ctrl+Alt+Z",
    "disguise_key": "Ctrl+Alt+X",
    "hide_tray_in_disguise": False,   # 伪装时隐藏托盘图标（默认不隐藏，换成“监视器”图标）
    "bar_rotate": 0,               # 任务栏轮播间隔（秒，1~3600 任意值），0 = 固定显示 pinned 那一只
    "rotate_top": 0,               # 只轮播前 N 只（按自选顺序），0 = 全部
    "bar_name": False,             # 任务栏是否显示名称（固定 / 轮播都适用）
    "aliases": {},                 # 自定义别名 {代码: 别名}，任务栏和面板优先显示
    "updown_color": False,         # 涨红跌绿着色（默认关闭 = 不着色）
    "bar_offset": 0,               # 任务栏条位置微调（物理像素，正数=往托盘方向/右移），拖动条可调
    "net_mode": "",                # 联网方式："" 未选择（不发任何请求）/ "direct" 直连腾讯 / "relay" 加密中转
    "relay_on": False,             # 与 net_mode == "relay" 同步（内部使用）
    "relay_conn": "",              # 中转连接串（DPAPI 按当前 Windows 用户加密后 base64，明文不落盘）
    "relay_allow_direct": False,   # 中转连不上时是否允许直连腾讯（会暴露域名）
    "last_rotate": 0,              # 上次用过的轮播间隔（菜单“轮播”项恢复用）
}

# 自定义消息 / 定时器
WM_APP_QUOTES = W.WM_APP + 1
WM_APP_TRAY = W.WM_APP + 2
WM_APP_SHOW = W.WM_APP + 3
WM_APP_SEARCH = W.WM_APP + 4
T_REFRESH, T_DOCK, T_SETTING, T_RESUME, T_REBUILD = 1, 2, 3, 4, 5
T_ROTATE = 6
T_REDOCK = 7
T_COLLAPSE, T_FLASH, T_SEARCH = 11, 12, 13
HK_BOSS, HK_DISGUISE = 1, 2


# ================================================================ 基础设施
def log(msg):
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        p = os.path.join(CFG_DIR, "log.txt")
        if os.path.exists(p) and os.path.getsize(p) > 200 * 1024:
            os.replace(p, p + ".old")
        with open(p, "a", encoding="utf-8") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + str(msg) + "\n")
    except OSError:
        pass


def load_cfg():
    os.makedirs(CFG_DIR, exist_ok=True)
    if not os.path.exists(CFG_PATH) and os.path.exists(LEGACY_CFG):
        try:
            shutil.copyfile(LEGACY_CFG, CFG_PATH)
        except OSError:
            pass
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            cfg = {}
    except (OSError, ValueError):
        cfg = {}
    had_net_mode = "net_mode" in cfg
    for k, v in DEFAULT_CFG.items():
        cfg.setdefault(k, v)
    # 校验 / 修正
    seen, stocks = set(), []
    for c in cfg["stocks"] if isinstance(cfg["stocks"], list) else []:
        n = normalize(str(c))
        if n and n not in seen:
            seen.add(n)
            stocks.append(n)
    cfg["stocks"] = stocks
    cfg["pinned"] = normalize(str(cfg.get("pinned") or ""))
    if cfg["pinned"] not in stocks:
        cfg["pinned"] = stocks[0] if stocks else ""
    try:
        cfg["interval"] = min(60, max(1, int(cfg["interval"])))
    except (TypeError, ValueError):
        cfg["interval"] = 3
    if type(cfg["disguise"]) is not int or cfg["disguise"] not in (0, 1, 2):   # 1.0 这类也不行
        cfg["disguise"] = 0
    for k in ("boss_key", "disguise_key"):   # 手改配置写错类型时恢复默认，避免菜单打不开
        if not isinstance(cfg[k], str):
            cfg[k] = DEFAULT_CFG[k]
    for k in ("panel_pinned", "panel_topmost", "hide_fullscreen", "hide_tray_in_disguise"):
        v = cfg[k]
        cfg[k] = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes")
    if not had_net_mode:                     # 没选过联网方式：已配好中转的沿用中转，否则等用户选择
        cfg["net_mode"] = "relay" if (cfg.get("relay_on") and cfg.get("relay_conn")) else ""
    if cfg["net_mode"] not in ("", "direct", "relay"):
        cfg["net_mode"] = ""
    cfg["relay_on"] = cfg["net_mode"] == "relay"
    cfg["relay_allow_direct"] = bool(cfg["relay_allow_direct"])
    if not isinstance(cfg["relay_conn"], str):
        cfg["relay_conn"] = ""
    cfg.pop("color_mode", None)              # 旧版三选一设置作废，统一回到默认“不着色”
    cfg["updown_color"] = bool(cfg["updown_color"])
    try:
        cfg["bar_offset"] = max(-4000, min(4000, int(cfg["bar_offset"])))
    except (TypeError, ValueError):
        cfg["bar_offset"] = 0
    for key, hi in (("bar_rotate", 3600), ("rotate_top", 999), ("last_rotate", 3600)):
        try:
            cfg[key] = min(hi, max(0, int(cfg[key])))
        except (TypeError, ValueError):
            cfg[key] = 0
    cfg["bar_name"] = bool(cfg["bar_name"])
    al = cfg["aliases"] if isinstance(cfg["aliases"], dict) else {}
    cfg["aliases"] = {normalize(str(k)): str(v).strip()[:ALIAS_MAX]
                      for k, v in al.items() if normalize(str(k)) in stocks and str(v).strip()}
    if cfg["bar_mode"] not in ("embed", "overlay"):
        cfg["bar_mode"] = "embed"
    pos = cfg.get("panel_pos")
    if not (isinstance(pos, list) and len(pos) == 2 and all(isinstance(v, int) for v in pos)):
        cfg["panel_pos"] = None
    cfg.pop("pos", None)                      # 旧版遗留字段
    cfg.pop("tray_in_disguise", None)
    save_cfg(cfg)
    return cfg


def save_cfg(cfg):
    """原子写入，断电/崩溃不损坏配置"""
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        tmp = CFG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CFG_PATH)
    except OSError as e:
        log(f"保存配置失败: {e!r}")


def load_cache(stocks):
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in stocks and isinstance(v, dict) and "price_s" in v and "pct" in v \
                    and "name" in v and "price" in v and not v.get("estimated"):   # 旧估值不复用
                out[k] = v
    return out


def save_cache(data):
    try:
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)
    except (OSError, TypeError, ValueError):
        pass


def reg_dword(path, name, default=None):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            v, _ = winreg.QueryValueEx(k, name)
            return int(v)
    except (OSError, ValueError, TypeError):
        return default


def parse_hotkey(text):
    """"Ctrl+Alt+Z" → (mods, vk)；无法解析返回 None"""
    mods, vk = 0, None
    for part in str(text or "").replace(" ", "").split("+"):
        p = part.lower()
        if p in ("ctrl", "control"):
            mods |= W.MOD_CONTROL
        elif p == "alt":
            mods |= W.MOD_ALT
        elif p == "shift":
            mods |= W.MOD_SHIFT
        elif p == "win":
            mods |= W.MOD_WIN
        elif len(p) == 1 and p.isalnum():
            vk = ord(p.upper())
        elif p.startswith("f") and p[1:].isdigit() and 1 <= int(p[1:]) <= 24:
            vk = 0x6F + int(p[1:])
        elif p in ("`", "~"):
            vk = 0xC0
        else:
            return None
    if vk is None or not mods:
        return None
    return mods, vk


def mix(c1, c2, t):
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))


# ================================================================ 主题（跟随系统深浅色 / 强调色）
class Theme:
    def __init__(self):
        p = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        self.tb_light = reg_dword(p, "SystemUsesLightTheme", 0) == 1
        self.light = reg_dword(p, "AppsUseLightTheme", 1) == 1
        acc = reg_dword(r"Software\Microsoft\Windows\DWM", "AccentColor", None)
        accent = ((acc & 0xFF, (acc >> 8) & 0xFF, (acc >> 16) & 0xFF)
                  if acc is not None else (0, 103, 192))
        if self.light:
            self.bg, self.hover, self.text = (249, 249, 249), (234, 234, 234), (26, 26, 26)
            self.sub, self.line = (112, 112, 112), (229, 229, 229)
            self.input_bg, self.input_bd = (255, 255, 255), (208, 208, 208)
            self.up, self.down = (214, 40, 40), (16, 140, 64)
            self.accent = accent
        else:
            self.bg, self.hover, self.text = (43, 43, 43), (58, 58, 58), (255, 255, 255)
            self.sub, self.line = (164, 164, 164), (64, 64, 64)
            self.input_bg, self.input_bd = (31, 31, 31), (80, 80, 80)
            self.up, self.down = (255, 102, 102), (76, 217, 130)
            lum = 0.299 * accent[0] + 0.587 * accent[1] + 0.114 * accent[2]
            self.accent = mix(accent, (255, 255, 255), 0.35) if lum < 110 else accent
        # 任务栏文字（按任务栏自身深浅色）
        if self.tb_light:
            self.bar_text, self.bar_sub = (16, 16, 16), (96, 96, 96)
            self.bar_up, self.bar_down = (205, 30, 30), (10, 135, 60)
        else:
            self.bar_text, self.bar_sub = (255, 255, 255), (190, 190, 190)
            self.bar_up, self.bar_down = (255, 105, 105), (90, 225, 140)


# ================================================================ 窗口过程分发
HANDLERS = {}


def _wndproc(hwnd, msg, wp, lp):
    h = HANDLERS.get(hwnd)
    if h is not None:
        try:
            r = h(hwnd, msg, wp, lp)
            if r is not None:
                return r
        except Exception:
            log("窗口过程异常:\n" + traceback.format_exc())
    return W.DefWindowProcW(hwnd, msg, wp, lp)


WNDPROC_REF = W.WNDPROC(_wndproc)       # 必须全局持有，防止被 GC
HINST = W.GetModuleHandleW(None)


def register_class(name, style=0):
    wc = W.WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(W.WNDCLASSEXW)
    wc.style = style
    wc.lpfnWndProc = WNDPROC_REF
    wc.hInstance = HINST
    wc.hCursor = W.LoadCursorW(None, W.IDC_ARROW)
    wc.lpszClassName = name
    if not W.RegisterClassExW(byref(wc)) and ctypes.get_last_error() != 1410:
        log(f"注册窗口类失败 {name}: {ctypes.get_last_error()}")


# ================================================================ GDI 小工具
def fill_rect(hdc, l, t, r, b, color):
    br = W.CreateSolidBrush(W.rgb(color))
    rc = W.RECT(l, t, r, b)
    W.FillRect(hdc, byref(rc), br)
    W.DeleteObject(br)


def fill_round(hdc, l, t, r, b, color, radius):
    br = W.CreateSolidBrush(W.rgb(color))
    ob = W.SelectObject(hdc, br)
    op = W.SelectObject(hdc, W.GetStockObject(W.NULL_PEN))
    W.RoundRect(hdc, l, t, r + 1, b + 1, radius, radius)
    W.SelectObject(hdc, op)
    W.SelectObject(hdc, ob)
    W.DeleteObject(br)


def draw_text(hdc, text, rect, font, color, flags):
    W.SelectObject(hdc, font)
    W.SetTextColor(hdc, W.rgb(color))
    rc = W.RECT(*rect)
    W.DrawTextW(hdc, text, -1, byref(rc), flags | W.DT_NOPREFIX)


def text_size(hdc, text, font):
    W.SelectObject(hdc, font)
    sz = W.SIZE()
    W.GetTextExtentPoint32W(hdc, text, len(text), byref(sz))
    return sz.cx, sz.cy


_FONT_OK = {}


def font_exists(face):
    if face in _FONT_OK:
        return _FONT_OK[face]
    hdc = W.GetDC(None)
    f = W.CreateFontW(-12, 0, 0, 0, 400, 0, 0, 0, W.DEFAULT_CHARSET, 0, 0, 0, 0, face)
    old = W.SelectObject(hdc, f)
    buf = ctypes.create_unicode_buffer(64)
    W.GetTextFaceW(hdc, 64, buf)
    W.SelectObject(hdc, old)
    W.DeleteObject(f)
    W.ReleaseDC(None, hdc)
    ok = buf.value.lower() == face.lower()
    _FONT_OK[face] = ok
    return ok


def first_font(*faces):
    for f in faces:
        if font_exists(f):
            return f
    return faces[-1]


def make_monitor_icon():
    """伪装用托盘图标：程序绘制的四根柱状图（类似性能/网络监视器），不依赖任何资源文件"""
    n = W.GetSystemMetrics(49) or 16                # SM_CXSMICON，已含 DPI 缩放
    buf = bytearray(n * n * 4)                      # BGRA，全透明
    b, g, r = 215, 140, 30                          # 蓝色 (30,140,215)
    bw = max(2, n // 6)
    gap = max(1, n // 14)
    x0 = (n - (4 * bw + 3 * gap)) // 2
    bottom = n - max(1, n // 8)
    usable = bottom - max(1, n // 8)
    for k, frac in enumerate((0.40, 0.70, 0.50, 1.00)):
        h = max(2, int(usable * frac))
        xs = x0 + k * (bw + gap)
        for y in range(bottom - h, bottom):
            for x in range(xs, xs + bw):
                i = (y * n + x) * 4
                buf[i:i + 4] = bytes((b, g, r, 255))
    bmi = W.BITMAPINFO()
    bh = bmi.bmiHeader
    bh.biSize = ctypes.sizeof(W.BITMAPINFOHEADER)
    bh.biWidth, bh.biHeight, bh.biPlanes, bh.biBitCount = n, -n, 1, 32
    bits = ctypes.c_void_p()
    color = W.CreateDIBSection(None, byref(bmi), W.DIB_RGB_COLORS, byref(bits), None, 0)
    if not color or not bits.value:
        return W.LoadIconW(None, W.IDI_APPLICATION)
    ctypes.memmove(bits.value, bytes(buf), len(buf))
    mbits = (ctypes.c_ubyte * (((n + 15) // 16) * 2 * n))()     # 全 0 掩码（行按 WORD 对齐）
    mask = W.CreateBitmap(n, n, 1, 1, mbits)
    ii = W.ICONINFO(True, 0, 0, mask, color)
    hicon = W.CreateIconIndirect(byref(ii))
    W.DeleteObject(mask)
    W.DeleteObject(color)
    return hicon or W.LoadIconW(None, W.IDI_APPLICATION)


_ALPHA_TABLES = {}


def alpha_table(base):
    t = _ALPHA_TABLES.get(base)
    if t is None:
        t = bytes(max(v, base) for v in range(256))
        _ALPHA_TABLES[base] = t
    return t


def tint_table(v0):
    key = ("tint", v0)
    t = _ALPHA_TABLES.get(key)
    if t is None:
        t = bytes(v0 if v == 0 else v for v in range(256))
        _ALPHA_TABLES[key] = t
    return t


# ================================================================ 行情工作线程
class Worker:
    """常驻单线程 + 长连接；结果通过 PostMessage 交回 UI 线程，UI 线程从不阻塞"""

    def __init__(self, notify_hwnd):
        self.hwnd = notify_hwnd
        self.ev = threading.Event()
        self.lock = threading.Lock()
        self.job = None
        self.result = None
        self.alive = True            # 置 False 后线程在当前请求结束时退出（看门狗重建时用）
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, gen, codes, route):
        """route = (中转配置或 None, 允许直连, 禁止联网)，由 App.net_route() 给出"""
        with self.lock:
            self.job = (gen, list(codes), route)
        self.ev.set()

    def take(self):
        with self.lock:
            r, self.result = self.result, None
        return r

    def _run(self):
        f, cur = None, None
        while True:
            self.ev.wait()
            self.ev.clear()
            if not self.alive:
                break
            with self.lock:
                job, self.job = self.job, None
            if not job:
                continue
            gen, codes, route = job
            relay, allow_direct, blocked = route
            if f is None or route != cur:          # 线路设置变了：重建连接
                if f is not None:
                    f.close()
                f, cur = Fetcher(relay, allow_direct), route
            relay_err = None
            if blocked:                            # 开了中转但没配置好、又不许直连：一个请求都不发
                res, err, line = None, RelayNotConfigured("未设置中转连接串"), "blocked"
            else:
                try:
                    res, err = f.fetch(codes), None
                except Exception as e:
                    res, err = None, e
                line, relay_err = f.route, f.relay_error
            if not self.alive:                     # 已被看门狗替换：结果作废，线程退出
                break
            with self.lock:
                self.result = (gen, res, err, line, relay_err)
            W.PostMessageW(self.hwnd, WM_APP_QUOTES, 0, 0)
        if f is not None:
            f.close()


class SearchWorker:
    """名称搜索线程：只处理最新一次输入（打字过程中的旧请求自动丢弃）"""

    def __init__(self, notify_hwnd):
        self.hwnd = notify_hwnd
        self.ev = threading.Event()
        self.lock = threading.Lock()
        self.job = None
        self.result = None
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, seq, keyword, route):
        with self.lock:
            self.job = (seq, keyword, route)
        self.ev.set()

    def take(self):
        with self.lock:
            r, self.result = self.result, None
        return r

    def _run(self):
        s, cur = None, None
        while True:
            self.ev.wait()
            self.ev.clear()
            with self.lock:
                job, self.job = self.job, None
            if not job:
                continue
            seq, kw, route = job
            relay, allow_direct, blocked = route
            if s is None or route != cur:
                if s is not None:
                    s.close()
                s, cur = Searcher(relay, allow_direct), route
            try:
                if blocked:
                    raise RelayNotConfigured("未设置中转连接串")
                res, err = s.search(kw), None
            except Exception as e:
                res, err = [], e
                log(f"搜索失败：{e!r}")             # 不把搜索词写进日志
            with self.lock:
                self.result = (seq, kw, res, err)
            W.PostMessageW(self.hwnd, WM_APP_SEARCH, 0, 0)


# ================================================================ 任务栏条
class Bar:
    def __init__(self, app):
        self.app = app
        self.hwnd = None
        self.embedded = False
        self.embed_failed = False       # 本次会话嵌入失败过 → 不再尝试
        self._destroying = False
        self.hover = False
        self.tracking = False
        self.rect = (0, 0, 0, 0)        # 屏幕坐标
        self.edge = W.ABE_BOTTOM
        self.want_w = 0
        self.minw = 0
        self.compact = False
        self.lines = []
        self.visible = False
        self.min_hwnd = None            # Win10 任务按钮区
        self.min_full = None
        self.min_expect = None
        self.min_vertical = False
        self._last_diag = None
        self.watch = set()              # 需要监听位置变化的 Explorer 窗口（任务按钮区 / ReBar / 托盘区）
        self._waiting_tb = False
        # 拖动微调位置
        self.dragging = False
        self.press_pt = None            # 按下时的光标屏幕坐标
        self.press_offset = 0
        self.drag_offset = 0

    # ---------- 创建 / 销毁 ----------
    def create(self):
        self.destroy()
        tb = W.FindWindowW("Shell_TrayWnd", None)
        if not tb:
            # 开机自启时任务栏可能还没起来：先不建窗口，等定时器下次再试（不要退回覆盖模式）
            if not self._waiting_tb:
                log("任务栏尚未就绪，等待后再嵌入")
                self._waiting_tb = True
            return
        self._waiting_tb = False
        hwnd = None
        self.embedded = False
        if self.app.cfg["bar_mode"] == "embed" and tb and not self.embed_failed:
            hwnd = W.CreateWindowExW(W.WS_EX_LAYERED | W.WS_EX_NOACTIVATE, BAR_CLS,
                                     "", W.WS_CHILD | W.WS_CLIPSIBLINGS,
                                     0, 0, 1, 1, tb, None, HINST, None)
            if hwnd:
                self.embedded = True
            else:
                log(f"嵌入任务栏失败({ctypes.get_last_error()})，改用覆盖模式")
                self.embed_failed = True
        if not hwnd:
            hwnd = W.CreateWindowExW(W.WS_EX_LAYERED | W.WS_EX_TOOLWINDOW | W.WS_EX_TOPMOST
                                     | W.WS_EX_NOACTIVATE, BAR_CLS, "",
                                     W.WS_POPUP, 0, 0, 1, 1, None, None, HINST, None)
        if not hwnd:
            log(f"创建任务栏条失败: {ctypes.get_last_error()}")
            return
        self.hwnd = hwnd
        HANDLERS[hwnd] = self.proc
        # 新窗口：清掉旧窗口残留的鼠标状态，否则悬停/轮播暂停可能一直卡住
        self.hover = self.tracking = self.dragging = False
        self.press_pt = None
        self.visible = False
        self.minw = 0
        self.rect = (0, 0, 0, 0)
        log("任务栏条已创建：" + ("原生嵌入" if self.embedded else "悬浮覆盖"))
        self.update()
        self.app.apply_visibility()
        self.app.install_loc_hook()

    def destroy(self):
        self._restore_taskband()
        if self.hwnd:
            self._destroying = True
            HANDLERS.pop(self.hwnd, None)
            if W.IsWindow(self.hwnd):
                W.DestroyWindow(self.hwnd)
            self._destroying = False
        self.hwnd = None
        self.visible = False

    def alive(self):
        return bool(self.hwnd) and bool(W.IsWindow(self.hwnd))

    def has_visible_style(self):
        """只看自身 WS_VISIBLE（嵌入时任务栏被隐藏，IsWindowVisible 也会返回 False）"""
        return bool(self.hwnd) and bool(W.GetWindowLongW(self.hwnd, W.GWL_STYLE) & W.WS_VISIBLE)

    def set_visible(self, v):
        if not self.hwnd:
            return
        if v != self.visible or self.has_visible_style() != v:
            self.visible = v
            if v:
                self.place(force_paint=True)   # 先定位、绘制，再显示，避免在角落闪一下
            W.ShowWindow(self.hwnd, W.SW_SHOWNOACTIVATE if v else W.SW_HIDE)

    def reset_width(self):
        self.minw = 0

    # ---------- 内容 ----------
    def _compose(self):
        a, th = self.app, self.app.theme
        fg, sub = th.bar_text, th.bar_sub
        q = a.current_quote()
        m = a.cfg["disguise"]
        label = a.bar_label()            # 开启“显示名称”时的短标识；网速伪装下为空
        if self.edge in (W.ABE_LEFT, W.ABE_RIGHT):
            label = ""                   # 竖向任务栏太窄：不显示名称，避免价格被截掉
        name_p = [(label + " ", fg, "bar_name")] if label else []
        if not q:
            l1 = [("--", fg)]
            l2 = [(label, sub, "bar_name")] if label else [("—", sub)]
        else:
            pct = q["pct"]
            arrow = "↑" if pct > 0 else ("↓" if pct < 0 else "—")
            if m == 0:
                col = a.updown_color(pct, True, fg)     # 开启时价格和涨跌整行着色；关闭则全白
                l1 = [(q["price_s"], col)]
                l2 = ([(label + " ", col, "bar_name")] if label else []) + \
                    [(arrow, col), (f"{abs(pct):.2f}%", col)]
            elif m == 1:
                l1 = [(q["price_s"], fg)]
                l2 = name_p + [(f"{pct:+.2f}", fg)]
            else:
                l1 = [(f"{q['price_s']} K/s", fg)]
                l2 = [(f"{arrow} {abs(pct):.2f} K/s", fg)]
        if self.compact:
            return [("bar_c", l1 + [("  ", fg)] + l2)]
        if m == 2:                       # 网速伪装：两行同字体同字号，和真正的网速小工具一致
            return [("bar_net", l1), ("bar_net", l2)]
        return [("bar1", l1), ("bar2", l2)]

    def _measure(self, lines):
        hdc = W.GetDC(None)
        mem = W.CreateCompatibleDC(hdc)
        out = []
        for fkey, pieces in lines:
            norm, ws, hs, lh = [], [], [], 0
            for p in pieces:
                txt, col = p[0], p[1]
                pf = p[2] if len(p) > 2 else fkey      # 每段可单独指定字体（中文名用雅黑）
                cx, cy = text_size(mem, txt, self.app.fonts[pf])
                norm.append((txt, col, pf))
                ws.append(cx)
                hs.append(cy)
                lh = max(lh, cy)
            out.append((fkey, list(zip(norm, hs)), ws, sum(ws), lh))
        W.DeleteDC(mem)
        W.ReleaseDC(None, hdc)
        return out

    # ---------- 布局 ----------
    def _taskbar(self):
        tb = W.FindWindowW("Shell_TrayWnd", None)
        tbr = W.window_rect(tb)
        abd = W.APPBARDATA()
        abd.cbSize = ctypes.sizeof(W.APPBARDATA)
        edge = W.ABE_BOTTOM
        if W.SHAppBarMessage(W.ABM_GETTASKBARPOS, byref(abd)):
            edge = abd.uEdge
        return tb, tbr, edge

    def update(self):
        """内容或主题变化：重新测量 → 布局 → 绘制"""
        if not self.alive():
            return
        tb, tbr, edge = self._taskbar()
        s = self.app.s
        if tbr:
            vertical = edge in (W.ABE_LEFT, W.ABE_RIGHT)
            self.compact = (not vertical) and (tbr[3] - tbr[1]) < s(38)
        self.lines = self._measure(self._compose())
        cw = max((ln[3] for ln in self.lines), default=0)
        w = max(cw + s(16), s(40))
        if self.embedded and not IS_WIN11:
            # Win10 嵌入：改宽度要让任务按钮区重排，只增不减避免反复挤动
            self.minw = max(self.minw, w)
        else:
            # Win11 / 覆盖模式：右边缘固定贴托盘、文字靠右，宽度直接跟随内容（可缩回）
            self.minw = w
        self.want_w = self.minw
        self.place(force_paint=True)

    def place(self, force_paint=False):
        """按任务栏实时几何计算位置；位置变化才移动/重绘"""
        if not self.alive() or not self.visible:   # 隐藏中不占位（Win10 不去缩任务按钮区）
            return
        s = self.app.s
        tb, tbr, edge = self._taskbar()
        if not tb or not tbr:
            return
        self.edge = edge
        vertical = edge in (W.ABE_LEFT, W.ABE_RIGHT)
        tbw, tbh = tbr[2] - tbr[0], tbr[3] - tbr[1]
        if vertical:
            w, h = max(s(40), tbw - s(4)), s(40)
        else:
            w, h = self.want_w, tbh
        gap = s(4)
        rect = None

        # Win10 原生嵌入：缩窄任务按钮区，让出空间（任务按钮自动重排，不重叠）
        if self.embedded and not IS_WIN11:
            rect = self._shrink_taskband(tb, tbr, vertical, w, h)
        if rect is None:
            tray_h = W.FindWindowExW(tb, None, "TrayNotifyWnd", None)
            self.watch = {h for h in (tray_h, tb) if h}
            tray = W.window_rect(tray_h)
            ok = (tray and tray[2] > tray[0] and tray[3] > tray[1]
                  and tbr[0] <= tray[0] <= tbr[2] and tbr[1] <= tray[1] <= tbr[3])
            off = self.drag_offset if self.dragging else self.app.cfg["bar_offset"]
            if not vertical:
                right = tray[0] - gap if ok else tbr[2] - s(260)
                x, y = right - w + off, tbr[1]
                x = max(tbr[0], min(x, tbr[2] - w))          # 微调后仍限制在任务栏内
                if self.dragging:
                    self.drag_offset = x - (right - w)       # 只记录实际生效的偏移，避免拖回时有“死区”
            else:
                bottom = tray[1] - gap if ok else tbr[3] - s(200)
                x, y = tbr[0] + (tbw - w) // 2, bottom - h + off
                y = max(tbr[1], min(y, tbr[3] - h))
                if self.dragging:
                    self.drag_offset = y - (bottom - h)
            rect = (x, y, x + w, y + h)
            diag = (tbr, tray, bool(ok), edge)
            if self._last_diag is None and not self.dragging:   # 每次启动只记一次贴靠诊断
                self._last_diag = diag
                log(f"贴靠诊断：任务栏={tbr} 托盘区TrayNotifyWnd={tray} 有效={bool(ok)} "
                    f"边={edge} Win11={IS_WIN11} 缩放={self.app.scale:.2f} "
                    f"条宽={w} 微调={self.app.cfg['bar_offset']} → 条={rect}")

        changed = rect != self.rect
        self.rect = rect
        if changed:
            x, y, r, b = rect
            if self.embedded:
                pt = W.POINT(x, y)
                W.ScreenToClient(tb, byref(pt))
                W.SetWindowPos(self.hwnd, W.HWND_TOP, pt.x, pt.y, r - x, b - y,
                               W.SWP_NOACTIVATE)
            else:
                W.SetWindowPos(self.hwnd, W.HWND_TOPMOST, x, y, r - x, b - y,
                               W.SWP_NOACTIVATE)
        if changed or force_paint:
            self.paint()

    def _finish_drag(self):
        self.dragging = False
        self.app.cfg["bar_offset"] = int(self.drag_offset)
        save_cfg(self.app.cfg)
        log(f"任务栏条位置微调为 {self.drag_offset}px")
        self.place(force_paint=True)

    def reset_offset(self):
        self.app.cfg["bar_offset"] = 0
        save_cfg(self.app.cfg)
        self.place(force_paint=True)

    def _shrink_taskband(self, tb, tbr, vertical, w, h):
        rebar = W.FindWindowExW(tb, None, "ReBarWindow32", None)
        mins = W.FindWindowExW(rebar, None, "MSTaskSwWClass", None) if rebar else None
        rr, mr = W.window_rect(rebar), W.window_rect(mins)
        if not (rr and mr and mr[2] > mr[0] and mr[3] > mr[1]):
            return None
        self.min_hwnd, self.min_vertical = mins, vertical
        self.watch = {mins, rebar}
        cur = (mr[3] - mr[1]) if vertical else (mr[2] - mr[0])
        need = h if vertical else w
        # 完整宽度不能取任务按钮区自身的当前宽度（上次异常退出没还原时它已被缩过，会越缩越多），
        # 而是按外层 ReBar 的边界 / 托盘区左缘重新计算 —— 无论之前缩成什么样都能自动纠正
        full = self._band_full(tb, rr, mr, vertical)
        self.min_full = full
        if full - need < 0:
            return None
        off = min(0, self.drag_offset if self.dragging else self.app.cfg["bar_offset"])
        off = max(-(full - need), off)          # 微调只允许往左（离托盘更远），且不越过任务栏起点
        if self.dragging:
            self.drag_offset = off              # 只记录实际生效的偏移
        new = full - need + off
        if cur != new:
            if vertical:
                W.MoveWindow(mins, mr[0] - rr[0], mr[1] - rr[1], mr[2] - mr[0], new, True)
            else:
                W.MoveWindow(mins, mr[0] - rr[0], mr[1] - rr[1], new, mr[3] - mr[1], True)
        self.min_expect = new
        if vertical:
            x, y = tbr[0] + ((tbr[2] - tbr[0]) - w) // 2, mr[1] + new
        else:
            x, y = mr[0] + new, tbr[1]
        rect = (x, y, x + w, y + h)
        diag = ("win10", rr, mr[0], full, need, off)
        if self._last_diag is None and not self.dragging:     # 每次启动只记一次贴靠诊断
            self._last_diag = diag
            log(f"贴靠诊断(Win10)：ReBar={rr} 任务按钮区={mr} 完整宽={full} 当前宽={cur} "
                f"条宽={need} 微调={off} → 条={rect}")
        return rect

    @staticmethod
    def _band_full(tb, rr, mr, vertical):
        """任务按钮区应有的完整长度：从它的起点到 ReBar 末端（再不超过托盘区起点）"""
        tray = W.window_rect(W.FindWindowExW(tb, None, "TrayNotifyWnd", None))
        if vertical:
            end = rr[3]
            if tray and tray[1] > mr[1]:
                end = min(end, tray[1])
            return end - mr[1]
        end = rr[2]
        if tray and tray[0] > mr[0]:
            end = min(end, tray[0])
        return end - mr[0]

    def _restore_taskband(self):
        if self.min_hwnd and W.IsWindow(self.min_hwnd):
            mr = W.window_rect(self.min_hwnd)
            rebar = W.GetParent(self.min_hwnd)
            rr = W.window_rect(rebar)
            tb = W.GetParent(rebar) if rebar else None
            if mr and rr and tb:
                full = self._band_full(tb, rr, mr, self.min_vertical)   # 还原到完整长度
                if self.min_vertical:
                    W.MoveWindow(self.min_hwnd, mr[0] - rr[0], mr[1] - rr[1],
                                 mr[2] - mr[0], full, True)
                else:
                    W.MoveWindow(self.min_hwnd, mr[0] - rr[0], mr[1] - rr[1],
                                 full, mr[3] - mr[1], True)
        self.min_hwnd = self.min_full = self.min_expect = None

    def ensure_top(self):
        if not self.alive() or not self.visible:
            return
        after = W.HWND_TOP if self.embedded else W.HWND_TOPMOST
        W.SetWindowPos(self.hwnd, after, 0, 0, 0, 0,
                       W.SWP_NOMOVE | W.SWP_NOSIZE | W.SWP_NOACTIVATE)

    # ---------- 绘制：逐像素 Alpha ----------
    def _draw(self, hdc, wid, hei, mono):
        total = sum(ln[4] for ln in self.lines)
        y = (hei - total) // 2
        for fkey, pieces, ws, lw, lh in self.lines:
            # 整块文字靠右（贴近托盘），两行之间左对齐
            x = wid - self.app.s(8) - max(ln[3] for ln in self.lines)
            for ((txt, col, pf), ph), pw in zip(pieces, ws):
                W.SelectObject(hdc, self.app.fonts[pf])
                W.SetTextColor(hdc, W.rgb((255, 255, 255) if mono else col))
                W.TextOutW(hdc, x, y + (lh - ph) // 2, txt, len(txt))
                x += pw
            y += lh

    def paint(self):
        if not self.alive() or not self.lines:
            return
        x, y, r, b = self.rect
        wid, hei = r - x, b - y
        if wid <= 0 or hei <= 0:
            return
        sdc = W.GetDC(None)
        mem = W.CreateCompatibleDC(sdc)
        ok = False
        try:
            ok = self._paint_into(sdc, mem, wid, hei)
        finally:                                  # 无论绘制是否异常都释放 GDI 对象，杜绝句柄泄漏
            W.DeleteDC(mem)
            W.ReleaseDC(None, sdc)
        if not ok and self.embedded:
            log(f"嵌入窗口绘制失败({ctypes.get_last_error()})，回退覆盖模式")
            self.embed_failed = True
            self.app.post_rebuild_bar()

    def _paint_into(self, sdc, mem, wid, hei):
        bmi = W.BITMAPINFO()
        bh = bmi.bmiHeader
        bh.biSize = ctypes.sizeof(W.BITMAPINFOHEADER)
        bh.biWidth, bh.biHeight = wid, -hei
        bh.biPlanes, bh.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        hbmp = W.CreateDIBSection(mem, byref(bmi), W.DIB_RGB_COLORS, byref(bits), None, 0)
        if not (hbmp and bits.value):
            if hbmp:
                W.DeleteObject(hbmp)
            return False
        old = W.SelectObject(mem, hbmp)
        try:
            W.SetBkMode(mem, W.TRANSPARENT)
            n = wid * hei * 4
            # 第 1 遍：全部画白字 → 得到覆盖率（Alpha）
            ctypes.memset(bits.value, 0, n)
            self._draw(mem, wid, hei, True)
            cov = ctypes.string_at(bits.value, n)
            # 第 2 遍：按真实颜色画在黑底上 → 天然就是预乘颜色
            ctypes.memset(bits.value, 0, n)
            self._draw(mem, wid, hei, False)
            col = ctypes.string_at(bits.value, n)
            dark_tb = not self.app.theme.tb_light
            if self.hover:
                base = 26 if dark_tb else 18
                out = bytearray(col.translate(tint_table(base)) if dark_tb else col)
            else:
                base = 1            # Alpha=1：肉眼不可见，但能接收鼠标
                out = bytearray(col)
            out[3::4] = cov[1::4].translate(alpha_table(base))
            ctypes.memmove(bits.value, bytes(out), n)
            blend = W.BLENDFUNCTION(0, 0, 255, W.AC_SRC_ALPHA)
            return bool(W.UpdateLayeredWindow(self.hwnd, sdc, None, byref(W.SIZE(wid, hei)), mem,
                                              byref(W.POINT(0, 0)), 0, byref(blend), W.ULW_ALPHA))
        finally:
            W.SelectObject(mem, old)
            W.DeleteObject(hbmp)

    # ---------- 消息 ----------
    def proc(self, hwnd, msg, wp, lp):
        a = self.app
        if msg == W.WM_MOUSEMOVE:
            if not self.tracking:
                tme = W.TRACKMOUSEEVENT(ctypes.sizeof(W.TRACKMOUSEEVENT),
                                        W.TME_LEAVE | W.TME_HOVER, hwnd, 220)
                W.TrackMouseEvent(byref(tme))
                self.tracking = True
            if not self.hover:
                self.hover = True
                self.paint()
            # 按住左键水平拖动 → 微调在任务栏上的位置（超过 5 像素才算拖动，单击不受影响）
            if self.press_pt is not None and W.GetCapture() == hwnd:
                cx, cy = W.cursor_pos()
                vertical = self.edge in (W.ABE_LEFT, W.ABE_RIGHT)
                d = (cy - self.press_pt[1]) if vertical else (cx - self.press_pt[0])
                if not self.dragging and abs(d) >= a.s(5):
                    self.dragging = True
                    if a.panel.mode == "hover":
                        a.panel.hide()
                if self.dragging:
                    self.drag_offset = self.press_offset + d
                    self.place()
            return 0
        if msg == W.WM_MOUSEHOVER:
            if not self.dragging and self.press_pt is None:
                a.panel.show_hover()
            return 0
        if msg == W.WM_MOUSELEAVE:
            self.tracking = False
            if self.hover:
                self.hover = False
                self.paint()
            return 0
        if msg == W.WM_LBUTTONDOWN:
            self.press_pt = W.cursor_pos()
            self.press_offset = a.cfg["bar_offset"]
            self.drag_offset = self.press_offset
            W.SetCapture(hwnd)
            return 0
        if msg == W.WM_LBUTTONUP:
            was_drag = self.dragging
            self.press_pt = None
            if was_drag:
                self._finish_drag()
            if W.GetCapture() == hwnd:
                W.ReleaseCapture()
            if not was_drag:
                a.panel.toggle_pinned()
            return 0
        if msg == W.WM_CAPTURECHANGED:
            self.press_pt = None
            if self.dragging:                 # 拖动中被系统夺走捕获：照样保存
                self._finish_drag()
            return 0
        if msg in (W.WM_RBUTTONDOWN, W.WM_LBUTTONDBLCLK, 0x7B):
            return 0                     # 0x7B=WM_CONTEXTMENU：不要冒泡给任务栏
        if msg == W.WM_RBUTTONUP:
            a.show_menu()
            return 0
        if msg == W.WM_MOUSEACTIVATE:
            return W.MA_NOACTIVATE
        if msg == W.WM_NCHITTEST:
            return W.HTCLIENT
        if msg == W.WM_DESTROY:
            HANDLERS.pop(hwnd, None)
            if not self._destroying and hwnd == self.hwnd:
                log("任务栏条被系统销毁（Explorer 重启？），稍后自动重建")
                self.hwnd = None
                a.post_rebuild_bar()
            return 0
        return None


# ================================================================ 悬浮 / 常驻面板
GLYPH = {"menu": "", "pin": "", "pinned": "", "del": "",
         "mask0": "", "mask1": "", "mask2": ""}
GLYPH_FALLBACK = {"menu": "⋯", "pin": "○", "pinned": "●", "del": "×",
                  "mask0": "◐", "mask1": "◑", "mask2": "◒"}

# 用 chr() 写死码位，避免编辑器/编码把私有区字符弄丢（Segoe Fluent Icons / MDL2 Assets 通用）
GLYPH = {"menu": chr(0xE712), "pin": chr(0xE718), "pinned": chr(0xE840),
         "del": chr(0xE711), "mask0": chr(0xE890), "mask1": chr(0xE72E),
         "mask2": chr(0xE774), "add": chr(0xE710)}
GLYPH_FALLBACK["add"] = "+"
_LEGACY_GLYPH_TEXT = r"""
_UNUSED_GLYPH = {"menu": "", "pin": "", "pinned": "", "del": "",
GLYPH = {"menu": "", "pin": "", "pinned": "", "del": "",
         "mask0": "", "mask1": "", "mask2": ""}
"""  # ↑ 历史残留文本，不参与运行

P_W, P_HDR, P_ROW, P_MAXR = 320, 46, 42, 9


class Panel:
    def __init__(self, app):
        self.app = app
        self.mode = None             # None / 'hover' / 'pinned'
        self.dragging = False
        self._drag_from = None
        # 搜索状态：输入框有内容时，列表区切换为搜索结果
        self.query = ""
        self.results = []
        self.results_for = None      # results 对应的关键字
        self.sel = 0
        self.searching = False
        self.search_err = False
        self.enter_pending = False   # 结果未到就按了回车：结果一到自动添加第一条
        self.net_btns = {}           # “选择联网方式”提示里的两个按钮
        self.input_kind = None       # 非 None：输入框正在输入设置（alias / rotate_top / rotate_sec）
        self.input_code = None
        self.input_prompt = None
        self.hot = None
        self.pressed = None
        self.tracking = False
        self.scroll = 0
        self.err_until = 0
        self.edit_brush = None
        self.w = self.h = 0
        self.btns = {}
        self.list_top = self.list_bottom = 0
        self.input_rc = self.add_rc = (0, 0, 0, 0)
        ex = W.WS_EX_TOOLWINDOW | (W.WS_EX_TOPMOST if app.cfg["panel_topmost"] else 0)
        self.hwnd = W.CreateWindowExW(ex, PANEL_CLS, "", W.WS_POPUP | W.WS_CLIPCHILDREN,
                                      0, 0, 10, 10, None, None, HINST, None)
        HANDLERS[self.hwnd] = self.proc
        self.edit = W.CreateWindowExW(0, "EDIT", "", W.WS_CHILD | W.WS_VISIBLE | W.WS_TABSTOP
                                      | W.ES_AUTOHSCROLL, 0, 0, 10, 10, self.hwnd,
                                      W.HANDLE(100), HINST, None)
        W.SendMessageW(self.edit, W.EM_LIMITTEXT, 300, 0)    # 中转连接串约 140 字符
        if W.DwmSetWindowAttribute:
            v = ctypes.c_int(W.DWMWCP_ROUND)
            W.DwmSetWindowAttribute(self.hwnd, W.DWMWA_WINDOW_CORNER_PREFERENCE,
                                    byref(v), 4)
        self.apply_theme()

    # ---------- 主题 / 字体 ----------
    def apply_theme(self):
        th = self.app.theme
        if self.edit_brush:
            W.DeleteObject(self.edit_brush)
        self.edit_brush = W.CreateSolidBrush(W.rgb(th.input_bg))
        if W.DwmSetWindowAttribute:
            v = ctypes.c_int(0 if th.light else 1)
            W.DwmSetWindowAttribute(self.hwnd, W.DWMWA_USE_IMMERSIVE_DARK_MODE, byref(v), 4)
        if W.SetWindowTheme:
            W.SetWindowTheme(self.edit, "Explorer" if th.light else "DarkMode_CFD", None)
        W.SendMessageW(self.edit, W.WM_SETFONT, self.app.fonts["input"] or 0, 1)
        m = self.app.s(2)
        W.SendMessageW(self.edit, W.EM_SETMARGINS, 3, m | (m << 16))
        self.update_cue()
        self.layout()
        W.InvalidateRect(self.hwnd, None, True)
        W.InvalidateRect(self.edit, None, True)

    def update_cue(self):
        if self.input_kind is not None:          # 正在输入别名：别覆盖别名提示
            return
        m = self.app.cfg["disguise"]
        self._set_cue("搜索添加监视项" if m == 2 else "输入代码或名称搜索" if m == 1
                      else "搜索：名称 / 拼音 / 代码，如 茅台、zgpa、00700")

    def _set_cue(self, cue):
        self._cue = ctypes.create_unicode_buffer(cue)
        W.SendMessageW(self.edit, W.EM_SETCUEBANNER, 1, ctypes.addressof(self._cue))

    def net_prompt(self):
        """是否显示“选择联网方式”提示（未选择、且不在搜索 / 设置输入中）"""
        return self.app.net_unset() and not self.query and self.input_kind is None

    # ---------- 布局 ----------
    def layout(self):
        s = self.app.s
        n = len(self.app.cfg["stocks"])
        vis = min(n, P_MAXR)
        self.scroll = max(0, min(self.scroll, n - vis))
        w = s(P_W)
        self.list_top = s(P_HDR)
        if self.query:
            # 搜索模式：高度取 结果数 与 自选数 的较大值（至少 3 行），打字时面板不来回跳
            rows = max(3, min(len(self.results), P_MAXR), vis)
            list_h = rows * s(P_ROW)
        else:
            list_h = vis * s(P_ROW) if n else s(60)
        self.net_btns = {}
        if self.net_prompt():                     # 首次使用：先选联网方式（选之前不发任何请求）
            list_h = max(list_h, s(124))
            by, bh, bw = self.list_top + s(2) + s(72), s(34), (w - s(12) * 2 - s(10)) // 2
            self.net_btns = {"relay": (s(12), by, s(12) + bw, by + bh),
                             "direct": (w - s(12) - bw, by, w - s(12), by + bh)}
        self.list_bottom = self.list_top + s(2) + list_h + s(4)
        bt = self.list_bottom + s(10)
        bb = bt + s(34)
        addw = s(64)
        self.input_rc = (s(12), bt, w - s(12) - addw - s(8), bb)
        self.add_rc = (w - s(12) - addw, bt, w - s(12), bb)
        h = bb + s(12)
        bs = s(32)
        bt0 = (s(P_HDR) - bs) // 2
        r = w - s(8)
        self.btns = {}
        for name in ("menu", "pin", "mask"):
            self.btns[name] = (r - bs, bt0, r, bt0 + bs)
            r -= bs + s(2)
        fh = self.app.font_px.get("input", s(16))
        eh = fh + s(8)
        il, it, ir, ib = self.input_rc
        W.MoveWindow(self.edit, il + s(8), it + (ib - it - eh) // 2, ir - il - s(16), eh, True)
        resized = (w, h) != (self.w, self.h)
        self.w, self.h = w, h
        if resized and W.IsWindowVisible(self.hwnd):
            if self.mode == "hover":
                x, y = self.anchor_pos()
            else:
                rc = W.window_rect(self.hwnd)
                x, y = self.clamp(rc[0], rc[1]) if rc else self.anchor_pos()
            W.SetWindowPos(self.hwnd, None, x, y, w, h, W.SWP_NOZORDER | W.SWP_NOACTIVATE)
        elif resized:
            W.SetWindowPos(self.hwnd, None, 0, 0, w, h,
                           W.SWP_NOMOVE | W.SWP_NOZORDER | W.SWP_NOACTIVATE)
        W.InvalidateRect(self.hwnd, None, False)

    def _work_area(self, x, y):
        mi = W.monitor_info(W.MonitorFromPoint(W.POINT(x, y), W.MONITOR_DEFAULTTONEAREST))
        if mi:
            r = mi.rcWork
            return r.left, r.top, r.right, r.bottom
        return 0, 0, W.GetSystemMetrics(0), W.GetSystemMetrics(1)

    def clamp(self, x, y):
        l, t, r, b = self._work_area(x + self.w // 2, y + self.app.s(20))
        x = max(l, min(x, r - self.w))
        y = max(t, min(y, b - self.h))
        return x, y

    def anchor_pos(self):
        """贴着任务栏条弹出（Win11 弹出菜单同款位置）"""
        s = self.app.s
        bl, bt, br, bb = self.app.bar.rect
        gap = s(8)
        edge = self.app.bar.edge
        if edge == W.ABE_TOP:
            x, y = br - self.w, bb + gap
        elif edge == W.ABE_LEFT:
            x, y = br + gap, bb - self.h
        elif edge == W.ABE_RIGHT:
            x, y = bl - gap - self.w, bb - self.h
        else:
            x, y = br - self.w, bt - gap - self.h
        l, t, r, b = self._work_area((bl + br) // 2, (bt + bb) // 2)
        x = max(l + gap, min(x, r - self.w - gap))
        y = max(t + gap, min(y, b - self.h - gap))
        return x, y

    # ---------- 显示状态 ----------
    def visible(self):
        return bool(W.IsWindowVisible(self.hwnd))

    def _show_at(self, x, y):
        top = self.mode == "hover" or self.app.cfg["panel_topmost"]
        if not top:      # 先取消置顶，再放到普通窗口最上层，避免被当前窗口挡住
            W.SetWindowPos(self.hwnd, W.HWND_NOTOPMOST, 0, 0, 0, 0,
                           W.SWP_NOMOVE | W.SWP_NOSIZE | W.SWP_NOACTIVATE)
        W.SetWindowPos(self.hwnd, W.HWND_TOPMOST if top else W.HWND_TOP, x, y,
                       self.w, self.h, W.SWP_NOACTIVATE | W.SWP_SHOWWINDOW)

    def show_hover(self):
        if self.mode is not None or self.app.suppressed():
            return
        self.mode = "hover"
        self.layout()
        self._show_at(*self.anchor_pos())
        W.SetTimer(self.hwnd, T_COLLAPSE, 120, None)

    def show_pinned(self, persist=True):
        """persist=False：只临时常驻显示（如首次运行的“选择联网方式”），不改用户的常驻设置"""
        if self.app.suppressed():
            self.mode = "pinned"
            return
        was = self.visible()
        self.mode = "pinned"
        W.KillTimer(self.hwnd, T_COLLAPSE)
        self.layout()
        if was:
            rc = W.window_rect(self.hwnd)
            x, y = rc[0], rc[1]
        elif self.app.cfg["panel_pos"]:
            x, y = self.clamp(*self.app.cfg["panel_pos"])
        else:
            x, y = self.anchor_pos()
        self._show_at(x, y)
        if persist and not self.app.cfg["panel_pinned"]:
            self.app.cfg["panel_pinned"] = True
            save_cfg(self.app.cfg)
        W.InvalidateRect(self.hwnd, None, False)

    def unpin(self):
        """取消常驻：鼠标还在面板上就转为悬浮模式（移开即收起），否则直接收起"""
        self.app.cfg["panel_pinned"] = False
        save_cfg(self.app.cfg)
        if self.mode == "pinned":
            self.mode = "hover"
            W.SetWindowPos(self.hwnd, W.HWND_TOPMOST, 0, 0, 0, 0,
                           W.SWP_NOMOVE | W.SWP_NOSIZE | W.SWP_NOACTIVATE)
            W.SetTimer(self.hwnd, T_COLLAPSE, 120, None)
        W.InvalidateRect(self.hwnd, None, False)

    def hide(self):
        self.end_alias(False)
        if self.query:                          # 收起时清掉搜索，下次弹出显示自选列表
            self._clear_input()
        self.mode = None
        W.KillTimer(self.hwnd, T_COLLAPSE)
        W.ShowWindow(self.hwnd, W.SW_HIDE)
        self.hot = None

    def toggle_pinned(self):
        if self.mode == "pinned":
            self.app.cfg["panel_pinned"] = False
            save_cfg(self.app.cfg)
            self.hide()
        else:
            self.show_pinned()

    def refresh_visibility(self):
        if self.app.suppressed():
            if self.visible():
                W.ShowWindow(self.hwnd, W.SW_HIDE)
            if self.mode == "hover":
                self.mode = None
                W.KillTimer(self.hwnd, T_COLLAPSE)
        elif self.mode == "pinned" and not self.visible():
            self.mode = None
            self.show_pinned(persist=self.app.cfg["panel_pinned"])   # 恢复显示，不改常驻设置

    def ensure_top(self):
        if self.mode == "pinned" and self.app.cfg["panel_topmost"] and self.visible():
            W.SetWindowPos(self.hwnd, W.HWND_TOPMOST, 0, 0, 0, 0,
                           W.SWP_NOMOVE | W.SWP_NOSIZE | W.SWP_NOACTIVATE)

    def set_topmost(self, on):
        if self.visible():
            W.SetWindowPos(self.hwnd, W.HWND_TOPMOST if on else W.HWND_NOTOPMOST, 0, 0, 0, 0,
                           W.SWP_NOMOVE | W.SWP_NOSIZE | W.SWP_NOACTIVATE)

    def _collapse_check(self):
        if self.mode != "hover":
            W.KillTimer(self.hwnd, T_COLLAPSE)
            return
        if self.app.menu_open or self.dragging:
            return
        cx, cy = W.cursor_pos()
        g = self.app.s(10)
        pr = W.window_rect(self.hwnd) or (0, 0, 0, 0)
        br = self.app.bar.rect
        u = (min(pr[0], br[0]) - g, min(pr[1], br[1]) - g,
             max(pr[2], br[2]) + g, max(pr[3], br[3]) + g)
        if u[0] <= cx < u[2] and u[1] <= cy < u[3]:
            return
        if W.GetForegroundWindow() == self.hwnd:     # 正在输入，不收起
            return
        self.hide()

    # ---------- 交互 ----------
    def hit(self, x, y):
        s = self.app.s
        if self.net_prompt():
            for name, (l, t, r, b) in self.net_btns.items():
                if l <= x < r and t <= y < b:
                    return ("net", name)
            if self.list_top <= y < self.list_bottom:   # 提示区其余部分：不能点到下面隐藏的自选行
                return None
        if y < self.list_top:
            for name, (l, t, r, b) in self.btns.items():
                if l <= x < r and t <= y < b:
                    return ("btn", name)
            return ("hdr",)
        if y < self.list_bottom:
            stocks = self.app.cfg["stocks"]
            y0 = self.list_top + s(2)
            if self.query:
                if y >= y0:
                    i = (y - y0) // s(P_ROW)
                    if 0 <= i < len(self.results):
                        return ("res", i)
                return None
            if y >= y0:
                i = (y - y0) // s(P_ROW) + self.scroll
                if 0 <= i < len(stocks) and (i - self.scroll) < P_MAXR:
                    part = "del" if x >= self.w - s(38) else "body"
                    return ("row", i, part)
            return None
        l, t, r, b = self.input_rc
        if l <= x < r and t <= y < b:
            return ("input",)
        l, t, r, b = self.add_rc
        if l <= x < r and t <= y < b:
            return ("add",)
        return None

    def edit_text(self):
        n = W.GetWindowTextLengthW(self.edit)
        buf = ctypes.create_unicode_buffer(n + 2)
        W.GetWindowTextW(self.edit, buf, n + 2)
        return buf.value.strip()

    def _flash_error(self):
        self.err_until = time.time() + 1.0
        W.SetTimer(self.hwnd, T_FLASH, 1000, None)
        W.InvalidateRect(self.hwnd, None, False)

    def _clear_input(self):
        W.SetWindowTextW(self.edit, "")      # 触发 EN_CHANGE → 退出搜索模式

    # ---------- 设置输入（复用底部输入框）：别名 / 轮播数量 / 轮播秒数 ----------
    def begin_alias(self, code):
        self.begin_input("alias", code)

    def begin_input(self, kind, code=None):
        a, c = self.app, self.app.cfg
        if a.suppressed():                       # 老板键 / 全屏隐藏中：面板显示不出来，不进入输入
            return
        self.end_input(False)
        self._clear_input()                    # 先退出搜索模式
        if kind == "alias":
            nm = a.display_name(code)          # 用户主动操作的提示，显示真名
            cur = c["aliases"].get(code, "")
            prompt = f"给「{nm}」起别名：回车保存，留空清除，Esc 取消"
        elif kind == "rotate_top":
            cur = str(c["rotate_top"]) if c["rotate_top"] else ""
            prompt = "轮播前几只（0 = 全部）：回车保存"
        elif kind == "relay":
            cur = ""                           # 连接串含令牌：不回显旧值
            prompt = "粘贴连接串 swr://令牌@域名/路径，回车保存"
        else:
            cur = str(c["bar_rotate"] or c.get("last_rotate") or "")
            prompt = "轮播间隔秒数（1~3600）：回车保存并开启轮播"
        self.input_kind, self.input_code, self.input_prompt = kind, code, prompt
        if not self.visible():
            self.show_hover()
        self._set_cue(prompt)
        # 输入连接串时用圆点掩码显示，防止被旁人 / 录屏看到令牌
        W.SendMessageW(self.edit, W.EM_SETPASSWORDCHAR, 0x25CF if kind == "relay" else 0, 0)
        W.InvalidateRect(self.edit, None, True)
        W.SetWindowTextW(self.edit, cur)
        W.SetForegroundWindow(self.hwnd)       # 面板在前台时不会被自动收起
        W.SetFocus(self.edit)
        W.SendMessageW(self.edit, W.EM_SETSEL, 0, -1)
        self.layout()                          # 隐藏“选择联网方式”提示等，重新排版

    def _parse_input(self, text):
        """校验输入；返回 (ok, 值)"""
        kind = self.input_kind
        if kind == "alias":
            return True, text
        if kind == "relay":
            return parse_relay(text) is not None, text.strip()
        t = text.strip() or "0"
        if not t.isdigit():
            return False, None
        n = int(t)
        if kind == "rotate_top":
            return n <= 999, n
        return 1 <= n <= 3600, n

    def end_input(self, save):
        kind, code = self.input_kind, self.input_code
        if kind is None:
            return True
        text = self.edit_text()
        if save:
            ok, val = self._parse_input(text)
            if not ok:                          # 输入不合法：保持输入状态并闪红
                self._flash_error()
                return False
            if kind == "relay" and not self.app.set_relay_conn(val):
                self._flash_error()             # 保存失败：保留已粘贴的内容，不用重新粘贴
                return False
        self.input_kind = self.input_code = self.input_prompt = None
        self.update_cue()
        W.SetWindowTextW(self.edit, "")
        W.SendMessageW(self.edit, W.EM_SETPASSWORDCHAR, 0, 0)      # 取消掩码
        W.InvalidateRect(self.edit, None, True)
        if save:
            a = self.app
            if kind == "alias":
                if code in a.cfg["stocks"]:
                    a.set_alias(code, val)
            elif kind == "relay":
                pass                            # 已在上面保存
            elif kind == "rotate_top":
                a.set_rotate_top(val)
            else:
                a.cfg["last_rotate"] = val
                a.set_rotate(val)
        self.layout()                          # 取消 / 保存后恢复正常排版（含“选择联网方式”提示）
        return True

    def end_alias(self, save):
        self.end_input(save)

    # ---------- 搜索 ----------
    def on_text_changed(self):
        if self.input_kind is not None:         # 设置输入中：不触发搜索
            return
        q = self.edit_text()
        if q.lower().startswith("swr://"):     # 误把中转连接串贴进搜索框：绝不拿去搜索（会泄露令牌）
            self.begin_input("relay")           # 直接切到连接串输入（掩码显示），保留已粘贴内容
            W.SetWindowTextW(self.edit, q)
            return
        if q == self.query:
            return
        was = bool(self.query)
        self.query = q
        self.sel = 0
        self.enter_pending = False
        self.search_err = False
        W.KillTimer(self.hwnd, T_SEARCH)
        if not q:
            self.results, self.results_for, self.searching = [], None, False
        else:
            self.searching = True
            W.SetTimer(self.hwnd, T_SEARCH, 220, None)     # 防抖：停顿 0.22 秒再搜
        if was != bool(q):
            self.hot = None
        self.layout()

    def on_results(self, kw, results, err):
        if kw != self.query:
            return                            # 已过期的结果
        self.searching = False
        self.search_err = err is not None
        self.results, self.results_for = results, kw
        self.sel = 0
        self.layout()
        if self.enter_pending:
            self.enter_pending = False
            self.submit()

    def move_sel(self, d):
        if self.query and self.results:
            self.sel = (self.sel + d) % len(self.results)
            W.InvalidateRect(self.hwnd, None, False)

    def pick(self, i):
        """添加第 i 条搜索结果"""
        if not (0 <= i < len(self.results)):
            return
        r = self.results[i]
        code = normalize(r["code"])
        if not code:
            return
        self.app.names[code] = r["name"]
        if code in self.app.cfg["stocks"]:
            self.app.set_bar_stock(code)       # 已在自选：直接设为任务栏显示
        else:
            self.app.add_codes([code])
        self._clear_input()

    def submit(self):
        if self.input_kind is not None:
            self.end_alias(True)
            return
        text = self.edit_text()
        if not text:
            return
        if self.results and self.results_for == text:   # 有搜索结果：优先添加选中项
            self.pick(self.sel)
            return
        parts = text.replace("，", ",").replace(",", " ").split()
        if len(parts) > 1 and all(looks_like_code(p) for p in parts):   # 多个代码一次性添加
            if self.app.add_stocks(text):
                self._clear_input()
            else:
                self._flash_error()
            return
        if self.searching and not looks_like_code(text):  # 名称还没搜到：结果一到自动添加
            self.enter_pending = True
            return
        if looks_like_code(text) and self.app.add_stocks(text):
            self._clear_input()
            return
        self._flash_error()

    def on_escape(self):
        if self.input_kind is not None:
            self.end_alias(False)
        elif self.query:
            self._clear_input()
        elif self.mode == "hover":
            self.hide()

    def row_menu(self, i):
        a = self.app
        stocks = a.cfg["stocks"]
        if not (0 <= i < len(stocks)):
            return
        code = stocks[i]
        m = W.CreatePopupMenu()
        W.AppendMenuW(m, W.MF_STRING | (W.MF_CHECKED if code == a.bar_code() else 0),
                      1, "显示到任务栏")
        W.AppendMenuW(m, W.MF_SEPARATOR, 0, None)
        W.AppendMenuW(m, W.MF_STRING | (W.MF_GRAYED if i == 0 else 0), 2, "上移")
        W.AppendMenuW(m, W.MF_STRING | (W.MF_GRAYED if i == len(stocks) - 1 else 0), 3, "下移")
        W.AppendMenuW(m, W.MF_STRING | (W.MF_GRAYED if i == 0 else 0), 4, "移到最前")
        W.AppendMenuW(m, W.MF_STRING, 6, "设置别名…")
        if code in a.cfg["aliases"]:
            W.AppendMenuW(m, W.MF_STRING, 7, f"清除别名「{a.cfg['aliases'][code]}」")
        W.AppendMenuW(m, W.MF_SEPARATOR, 0, None)
        W.AppendMenuW(m, W.MF_STRING, 5, "删除")
        cmd = a.track_menu(m)
        if cmd == 6:
            self.begin_alias(code)
        elif cmd == 7:
            a.set_alias(code, "")
        if cmd == 1:
            a.set_bar_stock(code)
        elif cmd == 2:
            a.move_stock(code, -1)
        elif cmd == 3:
            a.move_stock(code, 1)
        elif cmd == 4:
            a.move_stock(code, -len(stocks))
        elif cmd == 5:
            a.remove_stock(code)

    def on_click(self, h):
        a = self.app
        if h is None:
            return
        if h[0] == "btn":
            if h[1] == "menu":
                l, t, r, b = self.btns["menu"]
                rc = W.window_rect(self.hwnd)
                a.show_menu(rc[0] + l, rc[1] + b)
            elif h[1] == "pin":
                if self.mode == "pinned":
                    self.unpin()
                else:
                    self.show_pinned()
            elif h[1] == "mask":
                a.cycle_disguise()
        elif h[0] == "row":
            code = a.cfg["stocks"][h[1]]
            if h[2] == "del":
                a.remove_stock(code)
            else:
                a.set_bar_stock(code)
        elif h[0] == "res":
            self.pick(h[1])
        elif h[0] == "net":
            a.set_net_mode(h[1])
        elif h[0] == "add":
            self.submit()
        elif h[0] == "input":
            W.SetForegroundWindow(self.hwnd)
            W.SetFocus(self.edit)

    # ---------- 绘制 ----------
    _glyph_ok = {}

    def _glyph(self, key):
        """图标字体里确实有这个字形才用它，否则退回普通符号（杜绝空白按钮）"""
        if self.app.icon_font_ok:
            ok = self._glyph_ok.get(key)
            if ok is None:
                ok = False
                hdc = W.GetDC(None)
                old = W.SelectObject(hdc, self.app.fonts["icon"])
                idx = ctypes.c_ushort()
                if W.GetGlyphIndicesW(hdc, GLYPH[key], 1, byref(idx), 1) != 0xFFFFFFFF:
                    ok = idx.value != 0xFFFF            # 0xFFFF = 字体里没有该字形
                W.SelectObject(hdc, old)
                W.ReleaseDC(None, hdc)
                self._glyph_ok[key] = ok
            if ok:
                return GLYPH[key], self.app.fonts["icon"]
        return GLYPH_FALLBACK[key], self.app.fonts["small"]

    def row_view(self, i, code):
        a, th = self.app, self.app.theme
        q = a.data.get(code)
        m = a.cfg["disguise"]
        if m == 2:
            name = FAKE_NAMES[i % len(FAKE_NAMES)]
            sub = ""
        elif m == 1:
            name, sub = a.cfg["aliases"].get(code) or pretty_code(code), ""
        else:
            alias = a.cfg["aliases"].get(code)
            if alias:                         # 别名在上，代码 · 原名在下，方便对照
                name, sub = alias, f"{pretty_code(code)} · {a.display_name(code)}"
            else:
                name, sub = a.display_name(code), pretty_code(code)
        if not q:
            return name, sub, "--", "--", th.sub
        pct = q["pct"]
        arrow = "↑" if pct > 0 else ("↓" if pct < 0 else "")
        if m == 0:
            col = a.updown_color(pct, False, th.text)
            if q.get("fund"):                  # 场外基金：注明是估值还是哪天的净值
                sub += " · 估值" if q.get("estimated") else \
                    (f" · 净值 {q['date'][5:]}" if q.get("date") else " · 净值")
            return name, sub, q["price_s"], f"{arrow}{abs(pct):.2f}%", col
        if m == 1:
            return name, sub, q["price_s"], f"{pct:+.2f}", th.text
        return name, sub, f"{q['price_s']} K/s", f"{arrow or '·'} {abs(pct):.2f}", th.sub

    def paint(self, hwnd):
        ps = W.PAINTSTRUCT()
        hdc = W.BeginPaint(hwnd, byref(ps))
        try:
            w, h = self.w, self.h
            if w <= 0 or h <= 0:
                return
            mem = W.CreateCompatibleDC(hdc)
            bmp = W.CreateCompatibleBitmap(hdc, w, h)
            old = W.SelectObject(mem, bmp)
            try:
                W.SetBkMode(mem, W.TRANSPARENT)
                self._paint_body(mem, w, h)
                W.BitBlt(hdc, 0, 0, w, h, mem, 0, 0, W.SRCCOPY)
            finally:                     # 无论绘制是否异常都释放 GDI 对象，杜绝句柄泄漏
                W.SelectObject(mem, old)
                W.DeleteObject(bmp)
                W.DeleteDC(mem)
        finally:
            W.EndPaint(hwnd, byref(ps))

    def _paint_net_prompt(self, dc, w, y0):
        th, s, F = self.app.theme, self.app.s, self.app.fonts
        flags = W.DT_CENTER | W.DT_VCENTER | W.DT_SINGLELINE
        draw_text(dc, "请选择联网方式", (s(12), y0 + s(8), w - s(12), y0 + s(36)),
                  F["title"], th.text, flags)
        draw_text(dc, "选择之前不会发出任何网络请求", (s(12), y0 + s(36), w - s(12), y0 + s(60)),
                  F["small"], th.sub, flags)
        for name, label, primary in (("relay", "设置加密中转", True), ("direct", "直连腾讯", False)):
            if name not in self.net_btns:        # 布局尚未更新（下一次 layout 会补上）
                continue
            l, t, r, b = self.net_btns[name]
            hot = self.hot == ("net", name)
            if primary:
                col = mix(th.accent, (255, 255, 255), 0.12) if hot else th.accent
                fill_round(dc, l, t, r, b, col, s(8))
                draw_text(dc, label, (l, t, r, b), F["btn"], (255, 255, 255), flags)
            else:
                fill_round(dc, l, t, r, b, th.input_bd, s(8))
                fill_round(dc, l + 1, t + 1, r - 1, b - 1, th.hover if hot else th.bg, s(7))
                draw_text(dc, label, (l, t, r, b), F["btn"], th.text, flags)

    def _paint_results(self, dc, w, y0):
        a, th, s, F = self.app, self.app.theme, self.app.s, self.app.fonts
        bottom = self.list_bottom - s(4)
        flags_c = W.DT_CENTER | W.DT_VCENTER | W.DT_SINGLELINE | W.DT_END_ELLIPSIS
        if not self.results:
            if a.net_unset():
                tip = "还没选择联网方式：清空输入框后选择"
            elif self.searching or self.results_for != self.query:
                tip = "搜索中…"
            elif self.search_err:
                tip = "搜索服务连接失败，可直接输入代码回车添加"
            elif looks_like_code(self.query):
                tip = f"没有找到「{self.query}」，回车按代码添加"
            else:
                tip = f"没有找到「{self.query}」"
            draw_text(dc, tip, (s(12), y0, w - s(12), bottom), F["small"], th.sub, flags_c)
            return
        rh = s(P_ROW)
        name_l, right = s(20), w - s(14)
        for i, r in enumerate(self.results[:P_MAXR]):
            top, bot = y0 + i * rh, y0 + (i + 1) * rh
            if i == self.sel:
                fill_round(dc, s(6), top + s(1), w - s(6), bot - s(1), th.hover, s(8))
            exists = r["code"] in a.cfg["stocks"]
            mid = (top + bot) // 2
            tag_w = s(64)
            disguised = a.cfg["disguise"] != 0     # 伪装模式：搜索结果只显示代码，不显示名称和市场
            draw_text(dc, pretty_code(r["code"]) if disguised else r["name"],
                      (name_l, top, right - tag_w, mid + s(2)), F["name"], th.text,
                      W.DT_LEFT | W.DT_SINGLELINE | W.DT_END_ELLIPSIS | W.DT_BOTTOM)
            draw_text(dc, "" if disguised else f"{pretty_code(r['code'])} · {r['label']}",
                      (name_l, mid + s(2), right - tag_w, bot), F["code"], th.sub,
                      W.DT_LEFT | W.DT_SINGLELINE | W.DT_TOP | W.DT_END_ELLIPSIS)
            if exists:
                draw_text(dc, "已添加", (right - tag_w, top, right, bot), F["small"], th.sub,
                          W.DT_RIGHT | W.DT_VCENTER | W.DT_SINGLELINE)
            else:
                g, f = self._glyph("add")
                col = th.accent if i == self.sel else th.sub
                draw_text(dc, g, (right - s(28), top, right, bot), f, col,
                          W.DT_CENTER | W.DT_VCENTER | W.DT_SINGLELINE)

    def _paint_body(self, dc, w, h):
        a, th, s, F = self.app, self.app.theme, self.app.s, self.app.fonts
        m = a.cfg["disguise"]
        fill_rect(dc, 0, 0, w, h, th.bg)
        hot = self.hot

        # ---- 标题栏（可拖动）----
        title = ("自选", "数据", "网络监视")[m]
        tw, _ = text_size(dc, title, F["title"])
        draw_text(dc, title, (s(16), 0, s(16) + tw + 2, self.list_top), F["title"], th.text,
                  W.DT_LEFT | W.DT_VCENTER | W.DT_SINGLELINE)
        if self.input_kind is not None:        # 设置输入中：标题旁提示正在设置什么
            short = {"alias": "设置别名", "rotate_top": "轮播数量（0=全部）",
                     "rotate_sec": "轮播秒数（1~3600）",
                     "relay": "粘贴中转连接串"}[self.input_kind]
            sub_text, sub_col = short + " · 回车保存", th.accent
        else:
            sub_text, sub_col = a.status_text(), th.sub
        draw_text(dc, sub_text, (s(16) + tw + s(10), 0, self.btns["mask"][0] - s(4),
                                 self.list_top),
                  F["small"], sub_col, W.DT_LEFT | W.DT_VCENTER | W.DT_SINGLELINE | W.DT_END_ELLIPSIS)
        for name, rc in self.btns.items():
            if hot == ("btn", name):
                fill_round(dc, *rc, th.hover, s(8))
            if name == "pin":
                key = "pinned" if self.mode == "pinned" else "pin"
                color = th.accent if self.mode == "pinned" else th.text
            elif name == "mask":
                key, color = f"mask{m}", (th.accent if m else th.text)
            else:
                key, color = "menu", th.text
            g, f = self._glyph(key)
            draw_text(dc, g, rc, f, color, W.DT_CENTER | W.DT_VCENTER | W.DT_SINGLELINE)

        # ---- 列表 ----
        stocks = a.cfg["stocks"]
        y0 = self.list_top + s(2)
        if self.query:
            self._paint_results(dc, w, y0)
            stocks = []                        # 搜索模式：不画自选列表
        elif self.net_prompt():
            self._paint_net_prompt(dc, w, y0)
            stocks = []                        # 先选联网方式
        elif not stocks:
            draw_text(dc, ("暂无自选，在下方输入名称或代码搜索添加", "暂无数据，在下方搜索添加",
                           "暂无监视项")[m],
                      (0, y0, w, self.list_bottom - s(4)),
                      F["small"], th.sub, W.DT_CENTER | W.DT_VCENTER | W.DT_SINGLELINE)
        vis = min(len(stocks), P_MAXR)
        rh = s(P_ROW)
        name_l = s(20)
        pct_r = w - s(40)
        pct_l = pct_r - s(72)
        price_r = pct_l - s(4)
        price_l = price_r - s(78)
        for j in range(vis):
            i = j + self.scroll
            if i >= len(stocks):
                break
            code = stocks[i]
            top, bot = y0 + j * rh, y0 + (j + 1) * rh
            row_hot = hot is not None and hot[0] == "row" and hot[1] == i
            if row_hot:
                fill_round(dc, s(6), top + s(1), w - s(6), bot - s(1), th.hover, s(8))
            if code == a.bar_code():           # 竖条标记：任务栏此刻显示的那只（轮播时会跟着走）
                cy = (top + bot) // 2
                fill_round(dc, s(8), cy - s(9), s(11), cy + s(9), th.accent, s(3))
            name, sub, price, pct, pcol = self.row_view(i, code)
            if sub:
                mid = (top + bot) // 2
                draw_text(dc, name, (name_l, top, price_l, mid + s(2)), F["name"], th.text,
                          W.DT_LEFT | W.DT_SINGLELINE | W.DT_END_ELLIPSIS | W.DT_BOTTOM)
                draw_text(dc, sub, (name_l, mid + s(2), price_l, bot), F["code"], th.sub,
                          W.DT_LEFT | W.DT_SINGLELINE | W.DT_TOP)
            else:
                draw_text(dc, name, (name_l, top, price_l, bot), F["name"], th.text,
                          W.DT_LEFT | W.DT_VCENTER | W.DT_SINGLELINE | W.DT_END_ELLIPSIS)
            draw_text(dc, price, (price_l, top, price_r, bot), F["num"],
                      pcol if m == 0 and price != "--" else th.text,
                      W.DT_RIGHT | W.DT_VCENTER | W.DT_SINGLELINE)
            draw_text(dc, pct, (pct_l, top, pct_r, bot), F["pct"], pcol,
                      W.DT_RIGHT | W.DT_VCENTER | W.DT_SINGLELINE)
            if row_hot:
                g, f = self._glyph("del")
                dcol = th.up if hot[2] == "del" else th.sub
                draw_text(dc, g, (w - s(38), top, w - s(10), bot), f, dcol,
                          W.DT_CENTER | W.DT_VCENTER | W.DT_SINGLELINE)
        if len(stocks) > P_MAXR:               # 简易滚动条
            track_t, track_b = y0, y0 + vis * rh
            th_h = max(s(16), (track_b - track_t) * vis // len(stocks))
            th_t = track_t + (track_b - track_t - th_h) * self.scroll // (len(stocks) - vis)
            fill_round(dc, w - s(5), th_t, w - s(3), th_t + th_h, th.line, s(2))

        # ---- 底部输入 ----
        fill_rect(dc, s(12), self.list_bottom, w - s(12), self.list_bottom + 1, th.line)
        focused = W.GetFocus() == self.edit
        bd = (th.up if time.time() < self.err_until
              else th.accent if focused else th.input_bd)
        l, t, r, b = self.input_rc
        fill_round(dc, l, t, r, b, bd, s(8))
        fill_round(dc, l + 1, t + 1, r - 1, b - 1 - (1 if focused else 0), th.input_bg, s(7))
        l, t, r, b = self.add_rc
        acol = mix(th.accent, (255, 255, 255), 0.12) if hot == ("add",) else th.accent
        fill_round(dc, l, t, r, b, acol, s(8))
        draw_text(dc, "保存" if self.input_kind is not None else "添加", (l, t, r, b),
                  F["btn"], (255, 255, 255),
                  W.DT_CENTER | W.DT_VCENTER | W.DT_SINGLELINE)

    # ---------- 消息 ----------
    def _client_pt(self):
        cx, cy = W.cursor_pos()
        rc = W.window_rect(self.hwnd) or (0, 0, 0, 0)
        return cx - rc[0], cy - rc[1]

    def proc(self, hwnd, msg, wp, lp):
        a = self.app
        if msg == W.WM_PAINT:
            self.paint(hwnd)
            return 0
        if msg == W.WM_ERASEBKGND:
            return 1
        if msg == W.WM_NCHITTEST:
            x, y = self._client_pt()
            if 0 <= y < self.list_top:
                h = self.hit(x, y)
                if h == ("hdr",):
                    return W.HTCAPTION           # 标题栏原生拖动
            return W.HTCLIENT
        if msg in (W.WM_NCLBUTTONDBLCLK, W.WM_NCRBUTTONDOWN):
            return 0
        if msg == W.WM_NCRBUTTONUP:
            a.show_menu()
            return 0
        if msg == W.WM_ENTERSIZEMOVE:
            self._drag_from = W.window_rect(hwnd)
            self.dragging = True                # 拖动期间暂停自动收起
            return 0
        if msg == W.WM_EXITSIZEMOVE:
            self.dragging = False
            rc = W.window_rect(hwnd)
            if rc and rc != getattr(self, "_drag_from", None):   # 真的移动了才算拖动
                was_hover = self.mode == "hover"
                self.mode = "pinned"            # 拖到哪就常驻在哪
                W.KillTimer(hwnd, T_COLLAPSE)
                a.cfg["panel_pos"] = [rc[0], rc[1]]
                a.cfg["panel_pinned"] = True
                save_cfg(a.cfg)
                if was_hover:
                    self.set_topmost(a.cfg["panel_topmost"])
                W.InvalidateRect(hwnd, None, False)
            return 0
        if msg == W.WM_MOUSEACTIVATE:
            x, y = self._client_pt()
            l, t, r, b = self.input_rc
            if l <= x < r and t <= y < b:
                return W.MA_ACTIVATE
            if self.hit(x, y) == ("net", "relay"):     # 接下来要输入连接串：需要激活面板
                return W.MA_ACTIVATE
            return W.MA_NOACTIVATE
        if msg == W.WM_SETCURSOR:
            if wp == hwnd and W.loword(lp) == W.HTCLIENT and self.hot and \
                    self.hot[0] in ("btn", "row", "res", "add", "net"):
                W.SetCursor(W.LoadCursorW(None, W.IDC_HAND))
                return 1
            return None
        if msg == W.WM_MOUSEMOVE:
            if not self.tracking:
                tme = W.TRACKMOUSEEVENT(ctypes.sizeof(W.TRACKMOUSEEVENT), W.TME_LEAVE, hwnd, 0)
                W.TrackMouseEvent(byref(tme))
                self.tracking = True
            h = self.hit(*W.get_xy(lp))
            if h != self.hot:
                self.hot = h
                if h and h[0] == "res":
                    self.sel = h[1]            # 鼠标悬停即选中，和方向键共用一个高亮
                W.InvalidateRect(hwnd, None, False)
            return 0
        if msg == W.WM_MOUSELEAVE:
            self.tracking = False
            if self.hot is not None:
                self.hot = None
                W.InvalidateRect(hwnd, None, False)
            return 0
        if msg == W.WM_LBUTTONDOWN:
            self.pressed = self.hit(*W.get_xy(lp))
            return 0
        if msg == W.WM_LBUTTONUP:
            h = self.hit(*W.get_xy(lp))
            p, self.pressed = self.pressed, None
            if h is not None and h == p:
                self.on_click(h)
            return 0
        if msg == W.WM_RBUTTONUP:
            h = self.hit(*W.get_xy(lp))
            if h and h[0] == "row":
                self.row_menu(h[1])
            else:
                a.show_menu()
            return 0
        if msg == 0x7B:                          # WM_CONTEXTMENU
            return 0
        if msg == W.WM_MOUSEWHEEL:
            delta = ctypes.c_short(W.hiword(wp)).value
            if self.query:
                self.move_sel(-1 if delta > 0 else 1)
                return 0
            n = len(a.cfg["stocks"])
            if n > P_MAXR:
                self.scroll = max(0, min(n - P_MAXR, self.scroll + (-1 if delta > 0 else 1)))
                W.InvalidateRect(hwnd, None, False)
            return 0
        if msg == W.WM_CTLCOLOREDIT:
            th = a.theme
            W.SetTextColor(wp, W.rgb(th.text))
            W.SetBkColor(wp, W.rgb(th.input_bg))
            return self.edit_brush or 0
        if msg == W.WM_COMMAND:
            code = W.hiword(wp)
            if code in (W.EN_SETFOCUS, W.EN_KILLFOCUS):
                W.InvalidateRect(hwnd, None, False)
            elif code == W.EN_CHANGE:
                self.on_text_changed()
            return 0
        if msg == W.WM_TIMER:
            if wp == T_SEARCH:
                W.KillTimer(hwnd, T_SEARCH)
                if self.query:
                    a.search(self.query)
            elif wp == T_COLLAPSE:
                self._collapse_check()
            elif wp == T_FLASH:
                W.KillTimer(hwnd, T_FLASH)
                W.InvalidateRect(hwnd, None, False)
            return 0
        if msg == W.WM_CLOSE:
            self.toggle_pinned() if self.mode == "pinned" else self.hide()
            return 0
        return None


# ================================================================ 应用
class App:
    def __init__(self):
        self.cfg = load_cfg()
        self.data = load_cache(self.cfg["stocks"])   # 先显示上次的行情，联网后再更新
        self.cache_saved = time.time()
        self.closed_until = {}       # 市场组 → 时间戳：识别到节假日休市后，到期前不再请求
        self._relay = self._load_relay()   # 解密后的中转配置（只在内存里）
        self.net_line = ""           # 最近一次行情实际走的线路：relay / direct / blocked
        self.relay_issue = None      # 中转问题：pin / auth / net / blocked / None
        self.names = {}              # 搜索得到的名称缓存：行情未返回前先显示名称
        self.rot_i = 0               # 轮播位置（在 rot_pool 里的下标）
        self.rot_override = None     # 轮播中临时显示的范围外代码
        self.search_seq = 0
        self.theme = Theme()
        self.dpi = 96
        self.scale = 1.0
        self.fonts = {}
        self.font_px = {}
        self.icon_font_ok = False
        self.boss = False
        self.fs = False
        self.menu_open = False
        self.quitting = False
        self.fetching = False
        self.fetch_started = 0.0
        self.fetch_gen = 0
        self.fail = 0
        self.last_ok = 0.0
        self.hotkey_ok = {}
        self.tray_added = False

        register_class(HOST_CLS)
        register_class(BAR_CLS, W.CS_DBLCLKS)
        register_class(PANEL_CLS, W.CS_DROPSHADOW)
        self.host = W.CreateWindowExW(0, HOST_CLS, "", W.WS_POPUP, 0, 0, 0, 0,   # 不设标题
                                      None, None, HINST, None)
        HANDLERS[self.host] = self.host_proc
        self.msg_taskbar_created = W.RegisterWindowMessageW("TaskbarCreated")
        self.locked = False          # 锁屏中：暂停请求
        self.loc_hook = None         # 监听 Explorer 任务栏布局变化，实时重新贴靠
        self._loc_ref = W.WINEVENTPROC(self._on_loc_event)
        if W.WTSRegisterSessionNotification:
            W.WTSRegisterSessionNotification(self.host, 0)   # 0 = 只关心本会话的锁屏/解锁

        self.load_metrics()
        W.set_menu_dark(not self.theme.light)
        self.icon_app = self._load_icon()
        try:
            self.icon_generic = make_monitor_icon()
        except Exception:
            log("生成伪装图标失败:\n" + traceback.format_exc())
            self.icon_generic = W.LoadIconW(None, W.IDI_APPLICATION)

        self.bar = Bar(self)
        self.panel = Panel(self)
        self.bar.create()
        self.tray_add()
        self.register_hotkeys()
        self._hook_ref = W.WINEVENTPROC(self._on_win_event)
        self.hook = W.SetWinEventHook(W.EVENT_SYSTEM_FOREGROUND, W.EVENT_SYSTEM_FOREGROUND,
                                      None, self._hook_ref, 0, 0,
                                      W.WINEVENT_OUTOFCONTEXT | W.WINEVENT_SKIPOWNPROCESS)
        self.worker = Worker(self.host)
        self.search_worker = SearchWorker(self.host)
        W.SetTimer(self.host, T_DOCK, 1500, None)
        if self.cfg["panel_pinned"] or self.net_unset():   # 未选联网方式：弹出面板让用户先选
            self.panel.show_pinned(persist=self.cfg["panel_pinned"])
        pool = self.rot_pool()
        if self.cfg["pinned"] in pool:
            self.rot_i = pool.index(self.cfg["pinned"])
        self.request_refresh()
        self.restart_rotate()
        log(f"程序启动（Windows build {WIN_BUILD}，缩放 {self.scale:.2f}）")

    # ---------- 度量 / 字体 ----------
    def s(self, v):
        return int(round(v * self.scale))

    def load_metrics(self):
        dpi = 0
        tb = W.FindWindowW("Shell_TrayWnd", None)
        if W.GetDpiForMonitor:
            mon = W.MonitorFromWindow(tb, W.MONITOR_DEFAULTTONEAREST) if tb else \
                W.MonitorFromPoint(W.POINT(0, 0), 1)
            dx, dy = ctypes.c_uint(), ctypes.c_uint()
            if mon and W.GetDpiForMonitor(mon, 0, byref(dx), byref(dy)) == 0:
                dpi = dy.value
        if not dpi:
            hdc = W.GetDC(None)
            dpi = W.GetDeviceCaps(hdc, W.LOGPIXELSY)
            W.ReleaseDC(None, hdc)
        self.dpi = dpi or 96
        self.scale = self.dpi / 96.0
        self.build_fonts()

    def build_fonts(self):
        for f in self.fonts.values():
            if f:
                W.DeleteObject(f)
        ui = first_font("Segoe UI Variable Text", "Segoe UI")
        disp = first_font("Segoe UI Variable Display", "Segoe UI")
        cjk = first_font("Microsoft YaHei UI", "Microsoft YaHei", "SimSun")
        icon = "Segoe Fluent Icons" if font_exists("Segoe Fluent Icons") else "Segoe MDL2 Assets"
        self.icon_font_ok = font_exists(icon)
        spec = {
            # 用户要求：任何模式下都不用粗体，全部常规字重 400
            "bar1": (ui, 10, 400, W.ANTIALIASED_QUALITY),
            "bar2": (ui, 8, 400, W.ANTIALIASED_QUALITY),
            "bar_c": (ui, 9, 400, W.ANTIALIASED_QUALITY),
            "bar_net": (ui, 8.5, 400, W.ANTIALIASED_QUALITY),
            "bar_name": (cjk, 8, 400, W.ANTIALIASED_QUALITY),
            "title": (cjk, 10.5, 400, W.CLEARTYPE_QUALITY),
            "small": (cjk, 8, 400, W.CLEARTYPE_QUALITY),
            "name": (cjk, 9.5, 400, W.CLEARTYPE_QUALITY),
            "code": (ui, 7.5, 400, W.CLEARTYPE_QUALITY),
            "num": (disp, 10, 400, W.CLEARTYPE_QUALITY),
            "pct": (ui, 9, 400, W.CLEARTYPE_QUALITY),
            "input": (cjk, 9, 400, W.CLEARTYPE_QUALITY),
            "btn": (cjk, 9, 400, W.CLEARTYPE_QUALITY),
            "icon": (icon, 10, 400, W.CLEARTYPE_QUALITY),
        }
        self.fonts, self.font_px = {}, {}
        for k, (face, pt, weight, q) in spec.items():
            px = max(1, int(round(pt * self.dpi / 72)))
            self.font_px[k] = px
            self.fonts[k] = W.CreateFontW(-px, 0, 0, 0, weight, 0, 0, 0, W.DEFAULT_CHARSET,
                                          0, 0, q, 0, face)

    def _load_icon(self):
        path = sys.executable if getattr(sys, "frozen", False) else \
            os.path.join(BASE_DIR, "icon.ico")
        if os.path.exists(path):
            small = W.HANDLE()
            if W.ExtractIconExW(path, 0, None, byref(small), 1) and small.value:
                return small.value
        return W.LoadIconW(None, W.IDI_APPLICATION)

    # ---------- 状态 ----------
    def suppressed(self):
        return self.boss or self.fs

    def apply_visibility(self):
        # 嵌入模式下全屏时任务栏自己会让位，不需要隐藏条；覆盖模式则需要
        show_bar = not self.boss and not (self.fs and not self.bar.embedded)
        if not show_bar and self.bar.embedded:
            self.bar._restore_taskband()          # 隐藏时把位置还给任务按钮，不留空白
        self.bar.set_visible(show_bar)
        if show_bar:
            self.bar.place(force_paint=True)      # 重新显示时立即重新让位
        self.panel.refresh_visibility()

    def updown_color(self, pct, on_bar, neutral):
        """开启“涨红跌绿着色”时：涨红、跌绿；关闭或平盘时返回 neutral"""
        if not self.cfg["updown_color"] or not pct:
            return neutral
        th = self.theme
        if on_bar:
            return th.bar_up if pct > 0 else th.bar_down
        return th.up if pct > 0 else th.down

    def menu_label(self, i, code):
        """菜单里显示的股票名：菜单是用户主动打开的，始终显示真名（别名优先），不做伪装"""
        name = self.cfg["aliases"].get(code) or self.display_name(code)
        return f"{name}\t{pretty_code(code)}".replace("&", "&&")

    # ---------- 加密中转 ----------
    def _load_relay(self):
        s = self.cfg.get("relay_conn") or ""
        if not s:
            return None
        try:
            dec = W.dpapi_decrypt(base64.b64decode(s))
        except (ValueError, TypeError):
            dec = None
        if not dec:
            log("中转连接串无法解密（可能换了电脑或 Windows 用户），请重新设置")
            return None
        return parse_relay(dec.decode("utf-8", errors="replace"))

    def set_relay_conn(self, text):
        cfg = parse_relay(text)
        if not cfg:
            return False
        enc = W.dpapi_encrypt(text.strip().encode("utf-8"))
        if enc is None:
            log("DPAPI 加密失败，连接串未保存")
            return False
        self.cfg["relay_conn"] = base64.b64encode(enc).decode("ascii")
        self.cfg["net_mode"] = "relay"
        self.cfg["relay_on"] = True
        self._relay = cfg
        self.relay_issue = None
        save_cfg(self.cfg)
        self.panel.layout()                       # 关掉“选择联网方式”提示
        log(f"已设置加密中转：{cfg['host']}:{cfg['port']}")
        self.request_refresh(force=True)
        return True

    def clear_relay(self):
        # 不改 relay_on：若不许直连，清除后进入“暂停”，绝不悄悄改成直连腾讯
        self.cfg["relay_conn"] = ""
        self._relay = None
        self.relay_issue = None
        save_cfg(self.cfg)
        self.request_refresh(force=True)

    def net_unset(self):
        return self.cfg["net_mode"] == ""

    def set_net_mode(self, mode):
        """切换联网方式：direct 直连 / relay 加密中转（没有连接串时先弹出输入）"""
        if mode == "relay" and not self._relay:
            self.panel.begin_input("relay")       # 保存连接串时会自动切到 relay
            return
        self.cfg["net_mode"] = mode
        self.cfg["relay_on"] = mode == "relay"
        self.relay_issue = None
        save_cfg(self.cfg)
        log("联网方式：" + ("加密中转" if mode == "relay" else "直连腾讯"))
        self.panel.layout()
        self.request_refresh(force=True)

    def net_route(self):
        """(中转配置, 允许直连, 禁止联网)"""
        c = self.cfg
        if c["net_mode"] == "":                   # 还没选联网方式：一个请求都不发
            return (None, False, True)
        if c["net_mode"] == "direct":
            return (None, True, False)
        if not self._relay:
            return (None, c["relay_allow_direct"], not c["relay_allow_direct"])
        return (self._relay, c["relay_allow_direct"], False)

    def _note_route(self, line, err, relay_err):
        """记录线路与中转问题；问题变化时写一次日志"""
        self.net_line = line or ""
        if self.net_unset():                     # 未选联网方式：本来就不联网，不算中转问题
            self.relay_issue = None
            return
        e = relay_err or (err if self.cfg["relay_on"] else None)
        if isinstance(e, PinError):
            issue = "pin"
        elif isinstance(e, RelayAuthError):
            issue = "auth"
        elif isinstance(e, RelayNotConfigured):
            issue = "blocked"
        elif e is not None and self.cfg["relay_on"]:
            issue = "net"
        else:
            issue = None
        if issue != self.relay_issue and issue:
            msg = {"pin": f"中转服务器证书校验失败，疑似被中间人拦截，已拒绝连接：{e}",
                   "auth": "中转令牌被拒绝（令牌错误或服务器已更换令牌）",
                   "blocked": "已开启中转但没有可用连接串，暂停请求",
                   "net": f"中转服务器连接失败：{e!r}"}[issue]
            log(msg)
        self.relay_issue = issue

    def route_tag(self):
        """状态栏里的线路说明；中转出问题时给出明确原因"""
        if self.net_unset():
            return "未选择联网方式，暂不联网"
        if not self.cfg["relay_on"]:
            return ""
        if not self._relay:                       # 开了中转却没有可用连接串（未设置 / 换机无法解密）
            return "中转未设置连接串" + ("，当前直连" if self.cfg["relay_allow_direct"] else "，已暂停")
        issue = self.relay_issue
        if issue == "pin":
            return "中转证书校验失败，疑似被拦截，已拒绝" + ("（当前直连）" if self.net_line == "direct" else "")
        if issue == "auth":
            return "中转令牌错误" + ("（当前直连）" if self.net_line == "direct" else "")
        if issue == "blocked":
            return "中转未设置连接串，已暂停"
        if issue == "net":
            return "中转连不上，已直连" if self.net_line == "direct" else "中转连不上"
        return "加密中转" if self.net_line == "relay" else ""

    def bar_code(self):
        """任务栏此刻显示的代码：轮播模式取轮到的那只，否则取固定的那只"""
        st = self.cfg["stocks"]
        if not st:
            return ""
        if self.cfg["bar_rotate"]:
            if self.rot_override in st:              # 轮播中手动点了范围外的那只：先显示它
                return self.rot_override
            pool = self.rot_pool()
            return pool[self.rot_i % len(pool)]
        return self.cfg["pinned"]

    def rot_pool(self):
        st = self.cfg["stocks"]
        n = self.cfg["rotate_top"]
        return st[:n] if n else st

    def current_quote(self):
        code = self.bar_code()
        return self.data.get(code) if code else None

    def bar_label(self):
        """任务栏上的短标识（由“任务栏显示名称”开关决定，固定 / 轮播都适用）"""
        m = self.cfg["disguise"]
        if m == 2 or not self.cfg["bar_name"]:     # 是否显示名称完全由设置项决定
            return ""
        code = self.bar_code()
        if not code:
            return ""
        alias = self.cfg["aliases"].get(code)
        if alias:                                    # 自定义别名优先（隐私模式也用，名字是自己起的）
            return alias
        if m == 1:                                   # 隐私模式：只显示序号
            return f"#{self.cfg['stocks'].index(code) + 1}"
        if code.startswith("us"):
            return pretty_code(code)
        q = self.data.get(code)
        name = (q["name"] if q else self.names.get(code, pretty_code(code))).replace(" ", "")
        return name[:4]

    # ---------- 轮播 ----------
    def restart_rotate(self):
        W.KillTimer(self.host, T_ROTATE)
        if self.cfg["bar_rotate"] and (len(self.rot_pool()) > 1 or self.rot_override):
            W.SetTimer(self.host, T_ROTATE, self.cfg["bar_rotate"] * 1000, None)

    def on_rotate(self):
        pool = self.rot_pool()
        if self.bar.hover or self.boss or not pool:
            return                                   # 鼠标停在条上时暂停轮播
        if self.rot_override:                        # 临时显示结束，回到轮播序列
            self.rot_override = None
            if len(pool) <= 1:                       # 只剩一只可轮：不必再定时
                W.KillTimer(self.host, T_ROTATE)
        elif len(pool) > 1:
            self.rot_i = (self.rot_i + 1) % len(pool)
        else:
            return
        self.bar.update()
        self.update_tray()
        if self.panel.visible():
            W.InvalidateRect(self.panel.hwnd, None, False)

    def set_rotate(self, sec):
        self.cfg["bar_rotate"] = sec
        pool = self.rot_pool()
        self.rot_override = None
        self.rot_i = pool.index(self.cfg["pinned"]) if self.cfg["pinned"] in pool else 0
        self._changed()

    def set_rotate_top(self, n):
        cur = self.bar_code()
        self.cfg["rotate_top"] = n
        self.rot_override = None
        self._follow_rotation(cur)                   # 尽量停在当前这只，不跳回第一只
        self._changed()                              # 内含 restart_rotate()

    def status_text(self):
        tag = self.route_tag()
        if self.net_unset() or (self.relay_issue in ("pin", "auth", "blocked")
                                and self.net_line != "direct"):
            return tag                                   # 未联网 / 中转出问题且没直连：直接说明原因
        base = self._status_base()
        return f"{base} · {tag}" if tag else base

    def _status_base(self):
        if self.fail >= 3:
            return "连接中断 · 重试中"
        if self.cfg["disguise"] == 2:              # 网速伪装：不出现任何行情字眼
            if not self.last_ok:
                return "连接中…"
            return "实时 · " + datetime.datetime.fromtimestamp(self.last_ok).strftime("%H:%M:%S")
        if not self.last_ok:
            return "上次数据 · 连接中…" if self.data else "加载中…"
        t = datetime.datetime.fromtimestamp(self.last_ok).strftime("%H:%M:%S")
        if self.any_market_open():
            lag = time.time() - self.last_ok
            if lag > max(30, 5 * self.cfg["interval"]):   # 开盘中长时间没拿到新数据
                return f"数据延迟 {int(lag)} 秒 · {t}"
            return f"{self.cfg['interval']}秒 · {t}"
        if self.fund_window():
            return f"休市 · 基金净值30分钟一查 · {t}"
        return f"休市 · 暂停刷新 · {t}"

    def mkt_open(self, code, now):
        """交易时段判断 + 节假日识别结果（识别到当天休市后直接视为收盘）"""
        until = self.closed_until.get(market_key(code))
        if until and time.time() < until:
            return False
        return market_open(code, now)

    def any_market_open(self, now=None):
        now = now or datetime.datetime.now()
        return any(self.mkt_open(c, now) for c in self.cfg["stocks"])

    # 开盘后多久还拿不到当天行情，就判定为节假日休市（A股 9:45 / 港股 10:00 / 美股美东 10:00）
    def _detect_holiday(self, res):
        """用各市场的参考指数判断（不用自选股：停牌/冷门股的行情时间本来就可能是旧的）"""
        now = datetime.datetime.now()
        for k in ("a", "hk", "us"):
            ref = REF_INDEX[k]
            codes = [ref] if res.get(ref) else []
            if not codes or not market_open(ref, now):
                continue
            if k == "us":
                et = us_eastern(now)
                if et.hour * 100 + et.minute < 1000:
                    continue
                today = et.strftime("%Y%m%d")
                until = time.time() + 7 * 3600          # 美股收盘后自然恢复判断
            else:
                hm = now.hour * 100 + now.minute
                if hm < (945 if k == "a" else 1000):
                    continue
                today = now.strftime("%Y%m%d")
                tomorrow = (now + datetime.timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0)
                until = tomorrow.timestamp()
            dates = [res[c].get("date", "") for c in codes]
            if all(d and d < today for d in dates):
                if self.closed_until.get(k, 0) < time.time():
                    label = {"a": "A股", "hk": "港股", "us": "美股"}[k]
                    log(f"识别到今天休市（节假日）：{label}，今天不再请求该市场行情")
                self.closed_until[k] = until

    # ---------- 任务栏布局变化监听（Win10 任务按钮区被 Explorer 还原时 30ms 内重新让位）----------
    def install_loc_hook(self):
        self.remove_loc_hook()
        tb = W.FindWindowW("Shell_TrayWnd", None)
        if not tb:
            return
        pid = W.wt.DWORD()
        W.GetWindowThreadProcessId(tb, byref(pid))
        if pid.value:
            self.loc_hook = W.SetWinEventHook(
                W.EVENT_OBJECT_LOCATIONCHANGE, W.EVENT_OBJECT_LOCATIONCHANGE, None,
                self._loc_ref, pid.value, 0, W.WINEVENT_OUTOFCONTEXT)

    def remove_loc_hook(self):
        if self.loc_hook:
            W.UnhookWinEvent(self.loc_hook)
            self.loc_hook = None

    def _on_loc_event(self, hook, event, hwnd, id_obj, id_child, thread, ms):
        try:
            if id_obj == W.OBJID_WINDOW and hwnd and hwnd in self.bar.watch and not self.quitting:
                W.SetTimer(self.host, T_REDOCK, 30, None)     # 合并短时间内的多次变化
        except Exception:
            pass

    def post_rebuild_bar(self):
        W.SetTimer(self.host, T_REBUILD, 800, None)

    # ---------- 自选操作 ----------
    def _changed(self, refetch=False):
        save_cfg(self.cfg)
        self.bar.reset_width()
        self.bar.update()
        self.panel.layout()
        self.update_tray()
        self.restart_rotate()
        if refetch:
            self.request_refresh(force=True)

    def set_alias(self, code, alias):
        alias = (alias or "").strip()[:ALIAS_MAX]
        if alias:
            self.cfg["aliases"][code] = alias
        else:
            self.cfg["aliases"].pop(code, None)
        self._changed()

    def display_name(self, code):
        """原始名称（行情名 > 搜索缓存名 > 代码）"""
        q = self.data.get(code)
        return q["name"] if q else self.names.get(code, pretty_code(code))

    def set_bar_stock(self, code):
        self.cfg["pinned"] = code
        pool = self.rot_pool()
        if code in pool:                     # 轮播中点选：跳到这只并从它继续轮
            self.rot_i = pool.index(code)
            self.rot_override = None
        elif self.cfg["bar_rotate"]:         # 不在前 N 只里：临时显示一个周期
            self.rot_override = code
        self._changed()

    def remove_stock(self, code):
        if code not in self.cfg["stocks"]:
            return
        cur = self.bar_code()
        self.cfg["stocks"].remove(code)
        self.cfg["aliases"].pop(code, None)
        self.data.pop(code, None)
        if self.cfg["pinned"] == code:
            self.cfg["pinned"] = self.cfg["stocks"][0] if self.cfg["stocks"] else ""
        if self.rot_override == code:
            self.rot_override = None
        self._follow_rotation(cur)
        self.panel.hot = None
        self._changed()

    def _follow_rotation(self, cur):
        """列表变动后让轮播位置仍指向原来那只，避免任务栏突然跳到别的票"""
        pool = self.rot_pool()
        if cur in pool:
            self.rot_i = pool.index(cur)
        elif pool:
            self.rot_i %= len(pool)

    def move_stock(self, code, delta):
        st = self.cfg["stocks"]
        if code not in st:
            return
        cur = self.bar_code()
        i = st.index(code)
        j = max(0, min(len(st) - 1, i + delta))
        st.insert(j, st.pop(i))
        self._follow_rotation(cur)
        self._changed()

    def add_codes(self, codes):
        added = False
        for code in codes:
            if code and code not in self.cfg["stocks"]:
                self.cfg["stocks"].append(code)
                if not self.cfg["pinned"]:
                    self.cfg["pinned"] = code
                added = True
        if added:
            self.panel.scroll = max(0, len(self.cfg["stocks"]) - P_MAXR)   # 滚到新加的那只
            self._changed(refetch=True)
        return added

    def add_stocks(self, text):
        """按代码添加（支持空格/逗号分隔多个）"""
        parts = text.replace("，", ",").replace(",", " ").split()
        return self.add_codes([normalize(p) for p in parts])

    # ---------- 搜索 ----------
    def search(self, keyword):
        if self.net_unset():                     # 未选联网方式：不搜索（面板会提示先选择）
            self.panel.on_results(keyword, [], None)
            return
        self.search_seq += 1
        self.search_worker.submit(self.search_seq, keyword, self.net_route())

    def on_search(self):
        if self.quitting:
            return
        r = self.search_worker.take()
        if not r:
            return
        seq, kw, res, err = r
        if seq != self.search_seq:
            return
        if isinstance(err, (PinError, RelayAuthError, RelayNotConfigured)):
            self._note_route(self.net_line, err, err)
        self.panel.on_results(kw, res, err)

    def set_disguise(self, m):
        self.cfg["disguise"] = m
        if m and self.panel.query:               # 切到伪装：清掉屏幕上的搜索结果（含真实名称）
            self.panel._clear_input()
        self.panel.update_cue()
        self._changed()
        self.sync_tray()

    def tray_wanted(self):
        if self.boss:
            return False
        return self.cfg["disguise"] == 0 or not self.cfg["hide_tray_in_disguise"]

    def sync_tray(self):
        if self.tray_wanted():
            if not self.tray_added:
                self.tray_add()
        else:
            self.tray_del()

    def cycle_disguise(self):
        self.set_disguise((self.cfg["disguise"] + 1) % 3)

    def toggle_boss(self):
        self.boss = not self.boss
        log("老板键：" + ("隐藏" if self.boss else "恢复"))
        if self.boss:
            if self.menu_open:                    # 正开着的右键菜单也要立刻关掉
                W.SendMessageW(self.host, W.WM_CANCELMODE, 0, 0)
            if self.panel.query:                  # 清掉搜索结果，恢复时不再显示
                self.panel._clear_input()
            self.panel.end_input(False)
            if self.panel.mode == "hover":
                self.panel.hide()
            self.tray_del()
        else:
            self.tray_add()
            self.request_refresh(force=True)      # 恢复显示：立刻取最新行情
        self.apply_visibility()

    # ---------- 行情 ----------
    def request_refresh(self, force=False):
        W.KillTimer(self.host, T_REFRESH)
        if self.net_unset():                     # 未选联网方式：不发请求，也不算失败
            self.fetching = False
            self.bar.update()
            self.schedule()
            return
        if self.fetching and not force:
            return
        codes = list(self.cfg["stocks"])
        if not codes:                            # 没有自选：不轮询，添加股票时会立即触发刷新
            self.bar.update()
            W.SetTimer(self.host, T_REFRESH, 1800 * 1000, None)
            return
        # 顺带查各市场的参考指数，用于节假日识别（不显示、不存入自选数据）
        for k in {market_key(c) for c in codes}:
            ref = REF_INDEX.get(k)
            if ref and ref not in codes:
                codes.append(ref)
        self.fetching = True
        self.fetch_started = time.time()
        self.fetch_gen += 1
        self.worker.submit(self.fetch_gen, codes, self.net_route())

    def on_quotes(self):
        if self.quitting:
            return
        r = self.worker.take()
        if not r:
            return
        gen, res, err, line, relay_err = r
        if gen != self.fetch_gen:
            return
        self.fetching = False
        try:
            self._note_route(line, err, relay_err)
            mine = {k: v for k, v in (res or {}).items() if v and k in self.cfg["stocks"]}
            if not mine and not (res and any(res.values())):
                self.fail += 1
                if self.fail == 3:
                    log(f"行情连接异常，进入重试：{err!r}")
            else:
                if self.fail >= 3:
                    log("行情连接恢复")
                self.fail = 0
                self.last_ok = time.time()
                self.data.update(mine)
                self._detect_holiday(res)
                if time.time() - self.cache_saved > 60:      # 最多每分钟落盘一次缓存
                    self.cache_saved = time.time()
                    save_cache({k: v for k, v in self.data.items() if k in self.cfg["stocks"]})
            self.bar.update()
            if self.panel.visible():
                W.InvalidateRect(self.panel.hwnd, None, False)
            self.update_tray()
        finally:
            self.schedule()                      # 无论上面出什么异常，都要重新排下一次刷新

    FUND_WINDOW = (1800, 2330)          # 场外基金净值公布时段（工作日晚上）
    FUND_EVERY = 1800                   # 该时段内每 30 分钟查一次

    def fund_window(self, now=None):
        """自选里有场外基金，且处于工作日晚间净值公布时段"""
        now = now or datetime.datetime.now()
        if now.weekday() >= 5 or not any(c.startswith("jj") for c in self.cfg["stocks"]):
            return False
        hm = now.hour * 100 + now.minute
        return self.FUND_WINDOW[0] <= hm <= self.FUND_WINDOW[1]

    def need_fetch(self):
        """此刻是否值得联网：开盘中 / 基金净值时段 / 还没拿到过数据 / 上次失败"""
        if self.locked or self.boss or self.net_unset():   # 锁屏 / 老板键 / 未选联网方式：不请求
            return False
        return (self.any_market_open() or self.fund_window()
                or not self.last_ok or self.fail > 0)

    def _seconds_to_next_active(self, now):
        """休市时：距离下一个开盘（或基金净值时段）还有几秒，最多 30 分钟后再本地检查一次"""
        base = now.replace(second=0, microsecond=0)
        for k in range(1, 31):
            t = base + datetime.timedelta(minutes=k)
            if self.fund_window(t) or self.any_market_open(t):
                return max(1, int((t - now).total_seconds()))
        return 1800

    def schedule(self):
        now = datetime.datetime.now()
        if self.locked or self.boss or self.net_unset():
            iv = 60                                   # 暂停中：只做本地检查，恢复时会立即刷新
        elif self.any_market_open():
            iv = min(10, 2 + self.fail) if self.fail else self.cfg["interval"]
        elif (not self.last_ok or self.fail) and self.fail < 10:
            iv = min(30, 2 + self.fail * 2)           # 还没拿到过数据 / 失败：慢慢重试直到成功一次
        elif self.fail >= 10:
            iv = min(1800, self._seconds_to_next_active(now))   # 休市且一直连不上：别整夜重试
        elif self.fund_window(now):
            iv = self.FUND_EVERY                      # 基金净值时段：30 分钟一次
        else:
            iv = self._seconds_to_next_active(now)    # 全部休市：不联网，到点再醒
        W.SetTimer(self.host, T_REFRESH, int(iv * 1000), None)

    # ---------- 托盘 ----------
    def _nid(self):
        nid = W.NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(W.NOTIFYICONDATAW)
        nid.hWnd = self.host
        nid.uID = 1
        nid.uFlags = W.NIF_MESSAGE | W.NIF_ICON | W.NIF_TIP
        nid.uCallbackMessage = WM_APP_TRAY
        m = self.cfg["disguise"]
        nid.hIcon = self.icon_app if m == 0 else self.icon_generic
        if m == 0:
            q = self.current_quote()
            tip = APP_TITLE
            if q:
                arrow = "↑" if q["pct"] > 0 else ("↓" if q["pct"] < 0 else "")
                name = self.cfg["aliases"].get(self.bar_code()) or q["name"]   # 与任务栏、面板一致
                tip = f"{name}  {q['price_s']}  {arrow}{abs(q['pct']):.2f}%"
        else:
            tip = ("数据面板", "网络监视器")[m - 1]
        nid.szTip = tip[:127]
        return nid

    def tray_add(self):
        if not self.tray_wanted():
            return
        nid = self._nid()
        W.Shell_NotifyIconW(W.NIM_DELETE, byref(nid))
        self.tray_added = bool(W.Shell_NotifyIconW(W.NIM_ADD, byref(nid)))

    def update_tray(self):
        if self.tray_added and not self.boss:
            nid = self._nid()
            if not W.Shell_NotifyIconW(W.NIM_MODIFY, byref(nid)):
                self.tray_add()

    def tray_del(self):
        if self.tray_added:
            nid = self._nid()
            W.Shell_NotifyIconW(W.NIM_DELETE, byref(nid))
            self.tray_added = False

    # ---------- 热键 ----------
    def register_hotkeys(self):
        for hid, key in ((HK_BOSS, "boss_key"), (HK_DISGUISE, "disguise_key")):
            W.UnregisterHotKey(self.host, hid)
            p = parse_hotkey(self.cfg.get(key))
            ok = bool(p) and bool(W.RegisterHotKey(self.host, hid, p[0] | W.MOD_NOREPEAT, p[1]))
            self.hotkey_ok[hid] = ok
            if p and not ok:
                log(f"热键 {self.cfg.get(key)} 注册失败（可能被占用）")

    # ---------- 菜单 ----------
    def track_menu(self, m, x=None, y=None):
        if x is None:
            x, y = W.cursor_pos()
        self.menu_open = True
        cmd = 0
        try:
            W.SetForegroundWindow(self.host)
            cmd = W.TrackPopupMenu(m, W.TPM_RETURNCMD | W.TPM_RIGHTBUTTON | W.TPM_NONOTIFY,
                                   x, y, 0, self.host, None)
            W.PostMessageW(self.host, W.WM_NULL, 0, 0)
        finally:
            W.DestroyMenu(m)
            self.menu_open = False
        return cmd

    def show_menu(self, x=None, y=None):
        c = self.cfg

        def chk(b):
            return W.MF_CHECKED if b else 0

        m = W.CreatePopupMenu()
        W.AppendMenuW(m, chk(self.panel.mode == "pinned"), 1, "常驻面板")
        W.AppendMenuW(m, chk(c["panel_topmost"]), 2, "面板总在最前")
        W.AppendMenuW(m, W.MF_SEPARATOR, 0, None)

        dm = W.CreatePopupMenu()
        for i, name in enumerate(DISGUISE_NAMES):
            W.AppendMenuW(dm, W.MFT_RADIOCHECK | chk(c["disguise"] == i), 10 + i, name)
        W.AppendMenuW(dm, W.MF_SEPARATOR, 0, None)
        bk = c["boss_key"] + ("" if self.hotkey_ok.get(HK_BOSS) else "（未生效）")
        dk = c["disguise_key"] + ("" if self.hotkey_ok.get(HK_DISGUISE) else "（未生效）")
        # 热键没注册成功时禁用：否则隐藏后只能靠再次运行程序恢复
        W.AppendMenuW(dm, 0 if self.hotkey_ok.get(HK_BOSS) else W.MF_GRAYED, 13,
                      f"老板键：立即隐藏全部\t{bk}")
        W.AppendMenuW(dm, W.MF_GRAYED, 14, f"循环切换伪装\t{dk}")
        W.AppendMenuW(dm, W.MF_SEPARATOR, 0, None)
        W.AppendMenuW(dm, chk(c["hide_tray_in_disguise"]), 15, "伪装时隐藏托盘图标")
        W.AppendMenuW(m, W.MF_POPUP, dm, "伪装")

        im = W.CreatePopupMenu()
        for i, sec in enumerate(INTERVALS):
            W.AppendMenuW(im, W.MFT_RADIOCHECK | chk(c["interval"] == sec), 20 + i, f"{sec} 秒")
        W.AppendMenuW(m, W.MF_POPUP, im, "刷新间隔")

        bm = W.CreatePopupMenu()
        W.AppendMenuW(bm, W.MFT_RADIOCHECK | chk(c["bar_mode"] == "embed"), 30,
                      "原生嵌入任务栏（推荐）" + ("  - 本机不支持，已回退" if self.bar.embed_failed else ""))
        W.AppendMenuW(bm, W.MFT_RADIOCHECK | chk(c["bar_mode"] == "overlay"), 31,
                      "悬浮覆盖（兼容模式）")
        W.AppendMenuW(bm, W.MF_SEPARATOR, 0, None)
        W.AppendMenuW(bm, chk(c["hide_fullscreen"]), 32, "全屏程序时自动隐藏")
        W.AppendMenuW(bm, W.MF_SEPARATOR, 0, None)
        W.AppendMenuW(bm, W.MF_GRAYED, 0, "按住任务栏条左右拖动可微调位置")
        W.AppendMenuW(bm, 0 if c["bar_offset"] else W.MF_GRAYED, 37,
                      f"恢复自动位置（当前微调 {c['bar_offset']:+d}px）" if c["bar_offset"]
                      else "恢复自动位置")

        rm = W.CreatePopupMenu()
        fm = W.CreatePopupMenu()                       # 固定盯一只：直接列出全部自选
        if not c["stocks"]:
            W.AppendMenuW(fm, W.MF_GRAYED, 0, "暂无自选")
        for i, code in enumerate(c["stocks"][:200]):
            W.AppendMenuW(fm, W.MFT_RADIOCHECK |
                          chk(not c["bar_rotate"] and code == c["pinned"]),
                          7000 + i, self.menu_label(i, code))
        W.AppendMenuW(rm, W.MF_POPUP | chk(not c["bar_rotate"]), fm, "固定盯一只")
        W.AppendMenuW(rm, W.MFT_RADIOCHECK | chk(bool(c["bar_rotate"])), 61,
                      f"轮播（每 {c['bar_rotate'] or c.get('last_rotate') or 5} 秒）")
        W.AppendMenuW(rm, W.MF_SEPARATOR, 0, None)
        tm = W.CreatePopupMenu()                       # 轮播间隔
        secs = list(ROTATE_INTERVALS)
        if c["bar_rotate"] and c["bar_rotate"] not in secs:
            secs = sorted(secs + [c["bar_rotate"]])    # 配置文件里自定义的秒数也列出来
        for sec in secs:
            W.AppendMenuW(tm, W.MFT_RADIOCHECK | chk(c["bar_rotate"] == sec), 1000 + sec,
                          f"{sec} 秒")
        W.AppendMenuW(tm, W.MF_SEPARATOR, 0, None)
        W.AppendMenuW(tm, 0, 82, "自定义秒数…")
        W.AppendMenuW(rm, W.MF_POPUP, tm, "轮播间隔")
        nm = W.CreatePopupMenu()                       # 轮播数量
        W.AppendMenuW(nm, W.MFT_RADIOCHECK | chk(not c["rotate_top"]), 80,
                      f"全部自选（{len(c['stocks'])} 只）")
        tops = list(ROTATE_TOPS)
        if c["rotate_top"] and c["rotate_top"] not in tops:
            tops = sorted(tops + [c["rotate_top"]])
        for n in tops:
            W.AppendMenuW(nm, W.MFT_RADIOCHECK | chk(c["rotate_top"] == n), 5000 + n,
                          f"前 {n} 只")
        W.AppendMenuW(nm, W.MF_SEPARATOR, 0, None)
        W.AppendMenuW(nm, 0, 81, "自定义数量…")
        W.AppendMenuW(rm, W.MF_POPUP, nm, "轮播数量")
        W.AppendMenuW(rm, W.MF_SEPARATOR, 0, None)
        W.AppendMenuW(rm, chk(c["bar_name"]), 66,
                      "任务栏显示名称" + ("（网速伪装下不显示）" if c["disguise"] == 2 else ""))
        W.AppendMenuW(m, W.MF_POPUP, rm, "任务栏盯哪只")

        W.AppendMenuW(m, chk(c["updown_color"]), 90, "涨红跌绿着色")
        W.AppendMenuW(m, W.MF_POPUP, bm, "任务栏显示")

        xm = W.CreatePopupMenu()                       # 网络方式：直连 / 加密中转
        rcfg = self._relay
        W.AppendMenuW(xm, W.MFT_RADIOCHECK | chk(c["net_mode"] == "relay"), 100,
                      "加密中转（经自己的服务器，隐藏腾讯域名和查询内容）")
        W.AppendMenuW(xm, W.MFT_RADIOCHECK | chk(c["net_mode"] == "direct"), 104,
                      "直连腾讯（https）")
        W.AppendMenuW(xm, W.MF_SEPARATOR, 0, None)
        if c["net_mode"] == "relay":
            relay_label = f"更换连接串…（当前 {rcfg['host']}）" if rcfg else "设置连接串…"
        else:
            relay_label = "设置连接串并切换到加密中转…"
        W.AppendMenuW(xm, 0, 101, relay_label)
        W.AppendMenuW(xm, chk(c["relay_allow_direct"])
                      | (0 if c["net_mode"] == "relay" else W.MF_GRAYED), 102,
                      "中转连不上时允许直连腾讯（会暴露域名）")
        W.AppendMenuW(xm, 0 if (rcfg or c["relay_conn"]) else W.MF_GRAYED, 103, "清除连接串")
        label = {"relay": "网络方式：加密中转", "direct": "网络方式：直连"}.get(
            c["net_mode"], "网络方式：未选择（暂不联网）")
        W.AppendMenuW(m, W.MF_POPUP, xm, label)

        W.AppendMenuW(m, W.MF_SEPARATOR, 0, None)
        W.AppendMenuW(m, chk(self.autostart_enabled()), 33, "开机自动启动")
        W.AppendMenuW(m, 0, 34, "立即刷新")
        W.AppendMenuW(m, 0, 35, "面板回到默认位置")
        W.AppendMenuW(m, 0, 36, "打开配置目录")
        W.AppendMenuW(m, W.MF_SEPARATOR, 0, None)
        W.AppendMenuW(m, 0, 50, "卸载：清除配置并退出")
        W.AppendMenuW(m, 0, 51, "退出")
        self.on_command(self.track_menu(m, x, y))

    def on_command(self, cmd):
        c = self.cfg
        if not cmd:
            return
        if cmd == 1:
            self.panel.toggle_pinned()
        elif cmd == 2:
            c["panel_topmost"] = not c["panel_topmost"]
            save_cfg(c)
            if self.panel.mode == "pinned":
                self.panel.set_topmost(c["panel_topmost"])
        elif 10 <= cmd <= 12:
            self.set_disguise(cmd - 10)
        elif cmd == 13:
            self.toggle_boss()
        elif cmd == 15:
            c["hide_tray_in_disguise"] = not c["hide_tray_in_disguise"]
            save_cfg(c)
            self.sync_tray()
        elif 20 <= cmd < 20 + len(INTERVALS):
            c["interval"] = INTERVALS[cmd - 20]
            save_cfg(c)
            self.request_refresh()
        elif cmd in (30, 31):
            c["bar_mode"] = "embed" if cmd == 30 else "overlay"
            save_cfg(c)
            self.bar.embed_failed = False
            self.bar.create()
        elif cmd == 32:
            c["hide_fullscreen"] = not c["hide_fullscreen"]
            save_cfg(c)
            self.check_fullscreen()
        elif cmd == 33:
            self.set_autostart(not self.autostart_enabled())
        elif cmd == 34:
            self.request_refresh(force=True)
        elif cmd == 35:
            c["panel_pos"] = None
            save_cfg(c)
            if self.panel.visible():
                W.SetWindowPos(self.panel.hwnd, None, *self.panel.anchor_pos(), 0, 0,
                               W.SWP_NOSIZE | W.SWP_NOZORDER | W.SWP_NOACTIVATE)
        elif cmd == 36:
            W.ShellExecuteW(None, "open", CFG_DIR, None, None, 1)
        elif cmd == 37:
            self.bar.reset_offset()
        elif cmd == 50:
            self.uninstall()
        elif cmd == 51:
            self.quit()
        elif 7000 <= cmd < 7200:                      # 直接选中某只固定盯
            i = cmd - 7000
            if i < len(c["stocks"]):
                if c["bar_rotate"]:
                    c["last_rotate"] = c["bar_rotate"]   # 记住轮播间隔，下次开启沿用
                c["bar_rotate"] = 0
                self.rot_override = None
                self.set_bar_stock(c["stocks"][i])
        elif cmd == 61:
            if not c["bar_rotate"]:
                self.set_rotate(c.get("last_rotate") or 5)
        elif 1000 < cmd <= 1000 + 3600:              # 轮播间隔（选了即开启轮播）
            c["last_rotate"] = cmd - 1000
            self.set_rotate(cmd - 1000)
        elif cmd == 100:
            self.set_net_mode("relay")
        elif cmd == 104:
            self.set_net_mode("direct")
        elif cmd == 101:
            self.panel.begin_input("relay")
        elif cmd == 102:
            c["relay_allow_direct"] = not c["relay_allow_direct"]
            save_cfg(c)
            self.request_refresh(force=True)
        elif cmd == 103:
            self.clear_relay()
        elif cmd == 90:
            c["updown_color"] = not c["updown_color"]
            self._changed()
        elif cmd == 81:
            self.panel.begin_input("rotate_top")
        elif cmd == 82:
            self.panel.begin_input("rotate_sec")
        elif cmd == 80:
            self.set_rotate_top(0)
        elif 5000 < cmd <= 5999:                      # 轮播前 N 只
            self.set_rotate_top(cmd - 5000)
        elif cmd == 66:
            c["bar_name"] = not c["bar_name"]
            self._changed()

    # ---------- 自启 / 卸载 ----------
    def _autostart_value(self):
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pyw):
            pyw = sys.executable
        return f'"{pyw}" "{os.path.abspath(__file__)}"'

    def autostart_enabled(self):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, APP_NAME)
                return True
        except OSError:
            return False

    def set_autostart(self, on):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                if on:
                    winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, self._autostart_value())
                else:
                    try:
                        winreg.DeleteValue(k, APP_NAME)
                    except FileNotFoundError:
                        pass
            log(f"开机自启 -> {on}")
        except OSError as e:
            log(f"开机自启设置失败: {e!r}")

    def uninstall(self):
        r = W.MessageBoxW(self.host, f"将删除配置目录及全部自选数据：\n{CFG_DIR}\n\n确定卸载并退出吗？",
                          f"卸载 {APP_TITLE}", W.MB_YESNO | W.MB_ICONQUESTION | W.MB_TOPMOST)
        if r != W.IDYES:
            return
        self.set_autostart(False)
        self.quit(save=False)
        shutil.rmtree(CFG_DIR, ignore_errors=True)

    def quit(self, save=True):
        if self.quitting:
            return
        self.quitting = True
        log("程序退出")
        if save:
            save_cfg(self.cfg)
            save_cache({k: v for k, v in self.data.items() if k in self.cfg["stocks"]})
        self.tray_del()
        for hid in (HK_BOSS, HK_DISGUISE):
            W.UnregisterHotKey(self.host, hid)
        if self.hook:
            W.UnhookWinEvent(self.hook)
            self.hook = None
        self.remove_loc_hook()
        if W.WTSUnRegisterSessionNotification:
            W.WTSUnRegisterSessionNotification(self.host)
        self.bar.destroy()                      # 内含：还原 Win10 任务按钮区
        W.PostQuitMessage(0)

    # ---------- 系统事件 ----------
    def _on_win_event(self, hook, event, hwnd, id_obj, id_child, thread, ms):
        try:
            if not self.bar.embedded:
                self.bar.ensure_top()
            self.check_fullscreen()
        except Exception:
            log("前台切换回调异常:\n" + traceback.format_exc())

    def check_fullscreen(self):
        fs = False
        if self.cfg["hide_fullscreen"]:
            fg = W.GetForegroundWindow()
            if fg in (self.host, self.panel.hwnd, self.bar.hwnd):
                return
            if fg and fg != W.GetShellWindow():
                cls = W.class_name(fg)
                if cls not in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                               "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow"):
                    r = W.window_rect(fg)
                    mi = W.monitor_info(W.MonitorFromWindow(fg, W.MONITOR_DEFAULTTONEAREST))
                    has_caption = (W.GetWindowLongW(fg, W.GWL_STYLE) & 0x00C00000) == 0x00C00000
                    if r and mi and not has_caption:   # 有标题栏的最大化窗口不算全屏（任务栏自动隐藏时）
                        m = mi.rcMonitor
                        fs = (r[0] <= m.left and r[1] <= m.top and
                              r[2] >= m.right and r[3] >= m.bottom)
        if fs != self.fs:
            self.fs = fs
            self.apply_visibility()

    def on_dock_timer(self):
        if self.quitting:
            return
        if not self.bar.alive():
            self.bar.create()
        else:
            self.bar.place()
            if not self.menu_open:
                self.bar.ensure_top()
                self.panel.ensure_top()
            want = not self.boss and not (self.fs and not self.bar.embedded)
            if want and not self.bar.has_visible_style():
                log("任务栏条不可见，自动恢复")
                self.bar.visible = False
                self.bar.set_visible(True)
        self.check_fullscreen()
        if self.panel.visible() and self.last_ok and self.any_market_open() and \
                time.time() - self.last_ok > max(30, 5 * self.cfg["interval"]):
            W.InvalidateRect(self.panel.hwnd, None, False)   # 让“数据延迟”提示及时出现
        batches = 1 + max(0, len(self.cfg["stocks"]) - 1) // Fetcher.BATCH
        if self.fetching and time.time() - self.fetch_started > 45 + 10 * (batches - 1):
            log("行情线程超时，重建工作线程")
            old = self.worker
            old.alive = False                     # 旧线程完成当前请求后自行退出，不累积
            old.ev.set()
            self.worker = Worker(self.host)
            self.fetching = False
            self.request_refresh()
        if not self.tray_added and not self.boss:
            self.tray_add()

    def reload_settings(self):
        self.theme = Theme()
        old = self.dpi
        self.load_metrics()
        if old != self.dpi:
            self.bar.reset_width()
        W.set_menu_dark(not self.theme.light)
        self.panel.apply_theme()
        self.bar.update()

    def host_proc(self, hwnd, msg, wp, lp):
        if msg == WM_APP_QUOTES:
            self.on_quotes()
            return 0
        if msg == WM_APP_SEARCH:
            self.on_search()
            return 0
        if msg == WM_APP_TRAY:
            ev = W.loword(lp)
            if ev == W.WM_LBUTTONUP:
                self.panel.toggle_pinned()
            elif ev == W.WM_RBUTTONUP:
                self.show_menu()
            return 0
        if msg == WM_APP_SHOW:                      # 再次启动 exe：找回界面
            if self.boss:
                self.toggle_boss()
            self.panel.show_pinned()
            return 0
        if msg == W.WM_HOTKEY:
            if wp == HK_BOSS:
                self.toggle_boss()
            elif wp == HK_DISGUISE:
                self.cycle_disguise()
            return 0
        if msg == W.WM_TIMER:
            if self.quitting:
                return 0
            if wp == T_REFRESH:
                try:
                    if self.need_fetch():
                        self.request_refresh()
                    else:                            # 休市：只做本地时间检查，不发请求
                        W.KillTimer(hwnd, T_REFRESH)
                        self.schedule()
                except Exception:
                    log("刷新调度异常:\n" + traceback.format_exc())
                    W.SetTimer(hwnd, T_REFRESH, 10 * 1000, None)   # 出错也绝不让刷新停摆
            elif wp == T_DOCK:
                self.on_dock_timer()
            elif wp == T_SETTING:
                W.KillTimer(hwnd, T_SETTING)
                self.reload_settings()
            elif wp == T_RESUME:
                W.KillTimer(hwnd, T_RESUME)
                self.bar.update()
                if not self.locked:
                    self.request_refresh(force=True)
            elif wp == T_ROTATE:
                self.on_rotate()
            elif wp == T_REDOCK:
                W.KillTimer(hwnd, T_REDOCK)
                self.bar.place()
            elif wp == T_REBUILD:
                W.KillTimer(hwnd, T_REBUILD)
                self.load_metrics()
                self.panel.apply_theme()        # 字体已重建，编辑框必须换上新字体
                self.bar.create()
            return 0
        if msg in (W.WM_SETTINGCHANGE, W.WM_DISPLAYCHANGE, W.WM_DPICHANGED):
            W.SetTimer(hwnd, T_SETTING, 400, None)   # 防抖：系统会连发很多次
            return None
        if msg == W.WM_WTSSESSION_CHANGE:
            if wp == W.WTS_SESSION_LOCK:
                self.locked = True
                log("锁屏：暂停行情请求")
            elif wp == W.WTS_SESSION_UNLOCK and self.locked:
                self.locked = False
                log("解锁：恢复行情请求")
                self.request_refresh(force=True)
            return 0
        if msg == W.WM_POWERBROADCAST:
            if wp in (W.PBT_APMRESUMEAUTOMATIC, W.PBT_APMRESUMESUSPEND):
                W.SetTimer(hwnd, T_RESUME, 3000, None)
            return 1
        if msg == W.WM_QUERYENDSESSION:
            return 1
        if msg == W.WM_ENDSESSION:
            if wp:
                self.quit()
            return 0
        if self.msg_taskbar_created and msg == self.msg_taskbar_created:
            log("检测到任务栏重建（Explorer 重启）")
            self.tray_added = False
            self.tray_add()
            self.post_rebuild_bar()
            return 0
        return None

    # ---------- 消息循环 ----------
    def run(self):
        msg = W.MSG()
        edit = self.panel.edit
        while True:
            r = W.GetMessageW(byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            if msg.hWnd == edit:
                if msg.message == W.WM_KEYDOWN:
                    if msg.wParam == W.VK_RETURN:
                        self.panel.submit()
                        continue
                    if msg.wParam == W.VK_ESCAPE:
                        self.panel.on_escape()
                        continue
                    if msg.wParam in (W.VK_UP, W.VK_DOWN) and self.panel.query:
                        self.panel.move_sel(-1 if msg.wParam == W.VK_UP else 1)
                        continue
                    if msg.wParam == ord("A") and W.GetKeyState(W.VK_CONTROL) < 0:
                        W.SendMessageW(edit, W.EM_SETSEL, 0, -1)
                        continue
                elif msg.message == W.WM_CHAR and msg.wParam in (1, 13, 27):
                    continue                          # 吞掉回车/ESC/Ctrl+A 的“叮”声
            W.TranslateMessage(byref(msg))
            W.DispatchMessageW(byref(msg))
        if not self.quitting:
            self.quit()


def main():
    W.set_dpi_awareness()
    W.CreateMutexW(None, False, "StockWatcher_SingleInstance")
    if ctypes.get_last_error() == 183:              # 已在运行：唤醒已有实例后退出
        h = W.FindWindowW(HOST_CLS, None)
        if h:
            W.PostMessageW(h, WM_APP_SHOW, 0, 0)
        return
    sys.excepthook = lambda t, v, tb: log("未捕获异常:\n" + "".join(
        traceback.format_exception(t, v, tb)))
    try:
        app = App()
    except Exception:
        log("启动失败:\n" + traceback.format_exc())
        raise
    app.run()


if __name__ == "__main__":
    main()
