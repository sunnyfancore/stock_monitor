# -*- coding: utf-8 -*-
"""最小 Win32 ctypes 绑定：只声明本程序用到的 API，全部显式 argtypes / restype（64 位安全）。

不依赖 pywin32 / Qt，纯标准库。命名沿用 Win32 原名，方便对照 MSDN。
"""
import ctypes
from ctypes import wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)


def _opt_dll(name):
    try:
        return ctypes.WinDLL(name)
    except OSError:
        return None


dwmapi = _opt_dll("dwmapi")
uxtheme = _opt_dll("uxtheme")
shcore = _opt_dll("shcore")

HANDLE = ctypes.c_void_p
HWND = wt.HWND
LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
UINT_PTR = ctypes.c_size_t

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, wt.UINT, WPARAM, LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, HANDLE, wt.DWORD, HWND, wt.LONG,
                                  wt.LONG, wt.DWORD, wt.DWORD)

RECT = wt.RECT
POINT = wt.POINT
SIZE = wt.SIZE
MSG = wt.MSG


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", HANDLE), ("hIcon", HANDLE), ("hCursor", HANDLE),
                ("hbrBackground", HANDLE), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", HANDLE)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", HANDLE), ("fErase", wt.BOOL), ("rcPaint", RECT),
                ("fRestore", wt.BOOL), ("fIncUpdate", wt.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("dwFlags", wt.DWORD),
                ("hwndTrack", HWND), ("dwHoverTime", wt.DWORD)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", wt.DWORD)]


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", HWND), ("uCallbackMessage", wt.UINT),
                ("uEdge", wt.UINT), ("rc", RECT), ("lParam", LPARAM)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", HWND), ("uID", wt.UINT),
                ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", HANDLE),
                ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD),
                ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64),
                ("dwInfoFlags", wt.DWORD), ("guidItem", GUID), ("hBalloonIcon", HANDLE)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", HANDLE), ("hbmColor", HANDLE)]


def _f(dll, name, restype, *argtypes):
    fn = getattr(dll, name)
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


P = ctypes.POINTER

# ---------------------------------------------------------------- user32
RegisterClassExW = _f(user32, "RegisterClassExW", wt.ATOM, P(WNDCLASSEXW))
CreateWindowExW = _f(user32, "CreateWindowExW", HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR,
                     wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                     HWND, HANDLE, HANDLE, ctypes.c_void_p)
DefWindowProcW = _f(user32, "DefWindowProcW", LRESULT, HWND, wt.UINT, WPARAM, LPARAM)
DestroyWindow = _f(user32, "DestroyWindow", wt.BOOL, HWND)
ShowWindow = _f(user32, "ShowWindow", wt.BOOL, HWND, ctypes.c_int)
IsWindow = _f(user32, "IsWindow", wt.BOOL, HWND)
IsWindowVisible = _f(user32, "IsWindowVisible", wt.BOOL, HWND)
GetMessageW = _f(user32, "GetMessageW", wt.BOOL, P(MSG), HWND, wt.UINT, wt.UINT)
TranslateMessage = _f(user32, "TranslateMessage", wt.BOOL, P(MSG))
DispatchMessageW = _f(user32, "DispatchMessageW", LRESULT, P(MSG))
PostQuitMessage = _f(user32, "PostQuitMessage", None, ctypes.c_int)
PostMessageW = _f(user32, "PostMessageW", wt.BOOL, HWND, wt.UINT, WPARAM, LPARAM)
SendMessageW = _f(user32, "SendMessageW", LRESULT, HWND, wt.UINT, WPARAM, LPARAM)
SetWindowPos = _f(user32, "SetWindowPos", wt.BOOL, HWND, HWND, ctypes.c_int,
                  ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT)
MoveWindow = _f(user32, "MoveWindow", wt.BOOL, HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, wt.BOOL)
GetWindowRect = _f(user32, "GetWindowRect", wt.BOOL, HWND, P(RECT))
GetClientRect = _f(user32, "GetClientRect", wt.BOOL, HWND, P(RECT))
InvalidateRect = _f(user32, "InvalidateRect", wt.BOOL, HWND, ctypes.c_void_p, wt.BOOL)
BeginPaint = _f(user32, "BeginPaint", HANDLE, HWND, P(PAINTSTRUCT))
EndPaint = _f(user32, "EndPaint", wt.BOOL, HWND, P(PAINTSTRUCT))
GetDC = _f(user32, "GetDC", HANDLE, HWND)
ReleaseDC = _f(user32, "ReleaseDC", ctypes.c_int, HWND, HANDLE)
SetTimer = _f(user32, "SetTimer", UINT_PTR, HWND, UINT_PTR, wt.UINT, ctypes.c_void_p)
KillTimer = _f(user32, "KillTimer", wt.BOOL, HWND, UINT_PTR)
GetCursorPos = _f(user32, "GetCursorPos", wt.BOOL, P(POINT))
FindWindowW = _f(user32, "FindWindowW", HWND, wt.LPCWSTR, wt.LPCWSTR)
FindWindowExW = _f(user32, "FindWindowExW", HWND, HWND, HWND, wt.LPCWSTR, wt.LPCWSTR)
TrackMouseEvent = _f(user32, "TrackMouseEvent", wt.BOOL, P(TRACKMOUSEEVENT))
UpdateLayeredWindow = _f(user32, "UpdateLayeredWindow", wt.BOOL, HWND, HANDLE,
                         P(POINT), P(SIZE), HANDLE, P(POINT), wt.DWORD,
                         P(BLENDFUNCTION), wt.DWORD)
LoadCursorW = _f(user32, "LoadCursorW", HANDLE, HANDLE, ctypes.c_void_p)
LoadIconW = _f(user32, "LoadIconW", HANDLE, HANDLE, ctypes.c_void_p)
DestroyIcon = _f(user32, "DestroyIcon", wt.BOOL, HANDLE)
CreateIconIndirect = _f(user32, "CreateIconIndirect", HANDLE, ctypes.POINTER(ICONINFO))
SetCursor = _f(user32, "SetCursor", HANDLE, HANDLE)
CreatePopupMenu = _f(user32, "CreatePopupMenu", HANDLE)
AppendMenuW = _f(user32, "AppendMenuW", wt.BOOL, HANDLE, wt.UINT, UINT_PTR, wt.LPCWSTR)
TrackPopupMenu = _f(user32, "TrackPopupMenu", ctypes.c_int, HANDLE, wt.UINT,
                    ctypes.c_int, ctypes.c_int, ctypes.c_int, HWND, ctypes.c_void_p)
DestroyMenu = _f(user32, "DestroyMenu", wt.BOOL, HANDLE)
SetForegroundWindow = _f(user32, "SetForegroundWindow", wt.BOOL, HWND)
GetForegroundWindow = _f(user32, "GetForegroundWindow", HWND)
RegisterWindowMessageW = _f(user32, "RegisterWindowMessageW", wt.UINT, wt.LPCWSTR)
RegisterHotKey = _f(user32, "RegisterHotKey", wt.BOOL, HWND, ctypes.c_int, wt.UINT, wt.UINT)
UnregisterHotKey = _f(user32, "UnregisterHotKey", wt.BOOL, HWND, ctypes.c_int)
MonitorFromPoint = _f(user32, "MonitorFromPoint", HANDLE, POINT, wt.DWORD)
MonitorFromWindow = _f(user32, "MonitorFromWindow", HANDLE, HWND, wt.DWORD)
GetMonitorInfoW = _f(user32, "GetMonitorInfoW", wt.BOOL, HANDLE, P(MONITORINFO))
SetWindowTextW = _f(user32, "SetWindowTextW", wt.BOOL, HWND, wt.LPCWSTR)
GetWindowTextW = _f(user32, "GetWindowTextW", ctypes.c_int, HWND, wt.LPWSTR, ctypes.c_int)
GetWindowTextLengthW = _f(user32, "GetWindowTextLengthW", ctypes.c_int, HWND)
SetFocus = _f(user32, "SetFocus", HWND, HWND)
GetFocus = _f(user32, "GetFocus", HWND)
GetSystemMetrics = _f(user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
ScreenToClient = _f(user32, "ScreenToClient", wt.BOOL, HWND, P(POINT))
GetClassNameW = _f(user32, "GetClassNameW", ctypes.c_int, HWND, wt.LPWSTR, ctypes.c_int)
GetParent = _f(user32, "GetParent", HWND, HWND)
SetWinEventHook = _f(user32, "SetWinEventHook", HANDLE, wt.DWORD, wt.DWORD, HANDLE,
                     WINEVENTPROC, wt.DWORD, wt.DWORD, wt.DWORD)
UnhookWinEvent = _f(user32, "UnhookWinEvent", wt.BOOL, HANDLE)
FillRect = _f(user32, "FillRect", ctypes.c_int, HANDLE, P(RECT), HANDLE)
DrawTextW = _f(user32, "DrawTextW", ctypes.c_int, HANDLE, wt.LPCWSTR, ctypes.c_int,
               P(RECT), wt.UINT)
MessageBoxW = _f(user32, "MessageBoxW", ctypes.c_int, HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT)
GetShellWindow = _f(user32, "GetShellWindow", HWND)
GetWindowThreadProcessId = _f(user32, "GetWindowThreadProcessId", wt.DWORD, HWND,
                              ctypes.POINTER(wt.DWORD))
SetCapture = _f(user32, "SetCapture", HWND, HWND)
ReleaseCapture = _f(user32, "ReleaseCapture", wt.BOOL)
GetCapture = _f(user32, "GetCapture", HWND)
GetKeyState = _f(user32, "GetKeyState", ctypes.c_short, ctypes.c_int)

# ---------------------------------------------------------------- gdi32
CreateCompatibleDC = _f(gdi32, "CreateCompatibleDC", HANDLE, HANDLE)
DeleteDC = _f(gdi32, "DeleteDC", wt.BOOL, HANDLE)
CreateDIBSection = _f(gdi32, "CreateDIBSection", HANDLE, HANDLE, P(BITMAPINFO), wt.UINT,
                      P(ctypes.c_void_p), HANDLE, wt.DWORD)
CreateBitmap = _f(gdi32, "CreateBitmap", HANDLE, ctypes.c_int, ctypes.c_int, wt.UINT,
                  wt.UINT, ctypes.c_void_p)
CreateCompatibleBitmap = _f(gdi32, "CreateCompatibleBitmap", HANDLE, HANDLE,
                            ctypes.c_int, ctypes.c_int)
SelectObject = _f(gdi32, "SelectObject", HANDLE, HANDLE, HANDLE)
DeleteObject = _f(gdi32, "DeleteObject", wt.BOOL, HANDLE)
CreateFontW = _f(gdi32, "CreateFontW", HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                 ctypes.c_int, ctypes.c_int, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
                 wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPCWSTR)
SetBkMode = _f(gdi32, "SetBkMode", ctypes.c_int, HANDLE, ctypes.c_int)
SetTextColor = _f(gdi32, "SetTextColor", wt.DWORD, HANDLE, wt.DWORD)
SetBkColor = _f(gdi32, "SetBkColor", wt.DWORD, HANDLE, wt.DWORD)
CreateSolidBrush = _f(gdi32, "CreateSolidBrush", HANDLE, wt.DWORD)
RoundRect = _f(gdi32, "RoundRect", wt.BOOL, HANDLE, ctypes.c_int, ctypes.c_int,
               ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int)
GetStockObject = _f(gdi32, "GetStockObject", HANDLE, ctypes.c_int)
BitBlt = _f(gdi32, "BitBlt", wt.BOOL, HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, HANDLE, ctypes.c_int, ctypes.c_int, wt.DWORD)
TextOutW = _f(gdi32, "TextOutW", wt.BOOL, HANDLE, ctypes.c_int, ctypes.c_int,
              wt.LPCWSTR, ctypes.c_int)
GetTextExtentPoint32W = _f(gdi32, "GetTextExtentPoint32W", wt.BOOL, HANDLE, wt.LPCWSTR,
                           ctypes.c_int, P(SIZE))
GetTextFaceW = _f(gdi32, "GetTextFaceW", ctypes.c_int, HANDLE, ctypes.c_int, wt.LPWSTR)
GetGlyphIndicesW = _f(gdi32, "GetGlyphIndicesW", wt.DWORD, HANDLE, wt.LPCWSTR, ctypes.c_int,
                      ctypes.POINTER(wt.WORD), wt.DWORD)
GetDeviceCaps = _f(gdi32, "GetDeviceCaps", ctypes.c_int, HANDLE, ctypes.c_int)
GetPixel = _f(gdi32, "GetPixel", wt.DWORD, HANDLE, ctypes.c_int, ctypes.c_int)

# ---------------------------------------------------------------- shell32 / kernel32
Shell_NotifyIconW = _f(shell32, "Shell_NotifyIconW", wt.BOOL, wt.DWORD, P(NOTIFYICONDATAW))
SHAppBarMessage = _f(shell32, "SHAppBarMessage", UINT_PTR, wt.DWORD, P(APPBARDATA))
ExtractIconExW = _f(shell32, "ExtractIconExW", wt.UINT, wt.LPCWSTR, ctypes.c_int,
                    P(HANDLE), P(HANDLE), wt.UINT)
ShellExecuteW = _f(shell32, "ShellExecuteW", HANDLE, HWND, wt.LPCWSTR, wt.LPCWSTR,
                   wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int)
CreateMutexW = _f(kernel32, "CreateMutexW", HANDLE, ctypes.c_void_p, wt.BOOL, wt.LPCWSTR)
GetModuleHandleW = _f(kernel32, "GetModuleHandleW", HANDLE, wt.LPCWSTR)

# ---------------------------------------------------------------- 可选 DLL
DwmSetWindowAttribute = (_f(dwmapi, "DwmSetWindowAttribute", ctypes.c_long, HWND,
                            wt.DWORD, ctypes.c_void_p, wt.DWORD) if dwmapi else None)
SetWindowTheme = (_f(uxtheme, "SetWindowTheme", ctypes.c_long, HWND, wt.LPCWSTR,
                     wt.LPCWSTR) if uxtheme else None)
GetDpiForMonitor = None
if shcore is not None:
    try:
        GetDpiForMonitor = _f(shcore, "GetDpiForMonitor", ctypes.c_long, HANDLE,
                              ctypes.c_int, P(wt.UINT), P(wt.UINT))
    except AttributeError:
        pass


def set_dpi_awareness():
    """Per-Monitor V2 → V1 → System，逐级兜底"""
    try:
        f = user32.SetProcessDpiAwarenessContext
        f.restype, f.argtypes = wt.BOOL, [ctypes.c_void_p]
        if f(ctypes.c_void_p(-4)):
            return
    except AttributeError:
        pass
    try:
        if shcore is not None and shcore.SetProcessDpiAwareness(2) == 0:
            return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except AttributeError:
        pass


def set_menu_dark(dark):
    """让 TrackPopupMenu 弹出的原生菜单跟随深色模式（uxtheme 未公开序号 135/136，Win10 1903+）"""
    if uxtheme is None:
        return
    try:
        spam = uxtheme[135]
        spam.restype, spam.argtypes = ctypes.c_int, [ctypes.c_int]
        spam(2 if dark else 3)              # 2=ForceDark 3=ForceLight
        flush = uxtheme[136]
        flush.restype, flush.argtypes = None, []
        flush()
    except Exception:
        pass


# ---------------------------------------------------------------- 常量
WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_CLIPCHILDREN = 0x02000000
WS_CLIPSIBLINGS = 0x04000000
WS_TABSTOP = 0x00010000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
ES_AUTOHSCROLL = 0x0080
CS_DBLCLKS = 0x0008
CS_DROPSHADOW = 0x00020000

SW_HIDE, SW_SHOWNOACTIVATE, SW_SHOW = 0, 4, 5
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER = 0x1, 0x2, 0x4
SWP_NOACTIVATE, SWP_SHOWWINDOW, SWP_NOOWNERZORDER = 0x10, 0x40, 0x200
HWND_TOPMOST = HWND(-1)
HWND_NOTOPMOST = HWND(-2)
HWND_TOP = HWND(0)

WM_CREATE, WM_DESTROY, WM_SIZE, WM_ACTIVATE = 0x1, 0x2, 0x5, 0x6
WM_PAINT, WM_CLOSE, WM_QUERYENDSESSION, WM_ERASEBKGND = 0xF, 0x10, 0x11, 0x14
WM_ENDSESSION, WM_SETTINGCHANGE, WM_SETCURSOR, WM_MOUSEACTIVATE = 0x16, 0x1A, 0x20, 0x21
WM_SETFONT, WM_DISPLAYCHANGE, WM_NCHITTEST = 0x30, 0x7E, 0x84
WM_NCLBUTTONDBLCLK, WM_NCRBUTTONDOWN, WM_NCRBUTTONUP = 0xA3, 0xA4, 0xA5
WM_KEYDOWN, WM_CHAR, WM_COMMAND, WM_TIMER = 0x100, 0x102, 0x111, 0x113
WM_CTLCOLOREDIT = 0x133
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x200, 0x201, 0x202
WM_LBUTTONDBLCLK, WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MOUSEWHEEL = 0x203, 0x204, 0x205, 0x20A
WM_POWERBROADCAST, WM_ENTERSIZEMOVE, WM_EXITSIZEMOVE = 0x218, 0x231, 0x232
WM_MOUSEHOVER, WM_MOUSELEAVE, WM_HOTKEY, WM_NULL = 0x2A1, 0x2A3, 0x312, 0x0
WM_CAPTURECHANGED = 0x0215
WM_CANCELMODE = 0x001F
WM_DPICHANGED = 0x02E0
WM_APP = 0x8000

EM_SETSEL, EM_LIMITTEXT, EM_SETMARGINS, EM_SETCUEBANNER = 0xB1, 0xC5, 0xD3, 0x1501
EM_SETPASSWORDCHAR = 0xCC
EN_SETFOCUS, EN_KILLFOCUS, EN_CHANGE = 0x100, 0x200, 0x300

HTCLIENT, HTCAPTION = 1, 2
MA_ACTIVATE, MA_NOACTIVATE = 1, 3
TME_HOVER, TME_LEAVE = 0x1, 0x2

VK_RETURN, VK_ESCAPE, VK_CONTROL = 0x0D, 0x1B, 0x11
VK_UP, VK_DOWN = 0x26, 0x28

IDC_ARROW, IDC_HAND, IDI_APPLICATION = 32512, 32649, 32512

MF_STRING, MF_GRAYED, MF_CHECKED, MF_POPUP = 0x0, 0x1, 0x8, 0x10
MF_SEPARATOR, MFT_RADIOCHECK = 0x800, 0x200
TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x2, 0x80, 0x100

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4

ABM_GETTASKBARPOS = 5
ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM = 0, 1, 2, 3

MONITOR_DEFAULTTONEAREST = 2
MONITORINFOF_PRIMARY = 1

DT_LEFT, DT_CENTER, DT_RIGHT, DT_VCENTER = 0x0, 0x1, 0x2, 0x4
DT_TOP, DT_BOTTOM = 0x0, 0x8
GWL_STYLE = -16
GetWindowLongW = _f(user32, "GetWindowLongW", wt.LONG, HWND, ctypes.c_int)
DT_SINGLELINE, DT_NOPREFIX, DT_END_ELLIPSIS = 0x20, 0x800, 0x8000

TRANSPARENT = 1
NULL_PEN = 8
SRCCOPY = 0x00CC0020
ULW_ALPHA = 0x2
AC_SRC_ALPHA = 0x1
DIB_RGB_COLORS = 0
LOGPIXELSY = 90
DEFAULT_CHARSET = 1
ANTIALIASED_QUALITY, CLEARTYPE_QUALITY = 4, 5

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
OBJID_WINDOW = 0
WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK, WTS_SESSION_UNLOCK = 0x7, 0x8

# ---------------------------------------------------------------- DPAPI（按当前 Windows 用户加密）
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


crypt32 = _opt_dll("crypt32")
CRYPTPROTECT_UI_FORBIDDEN = 0x1
if crypt32 is not None:
    CryptProtectData = _f(crypt32, "CryptProtectData", wt.BOOL, ctypes.POINTER(DATA_BLOB),
                          wt.LPCWSTR, ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                          ctypes.c_void_p, wt.DWORD, ctypes.POINTER(DATA_BLOB))
    CryptUnprotectData = _f(crypt32, "CryptUnprotectData", wt.BOOL, ctypes.POINTER(DATA_BLOB),
                            ctypes.c_void_p, ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                            ctypes.c_void_p, wt.DWORD, ctypes.POINTER(DATA_BLOB))
LocalFree = _f(kernel32, "LocalFree", HANDLE, HANDLE)
_DPAPI_ENTROPY = b"StockWatcher.relay.v1"


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf


def dpapi_encrypt(data):
    """用当前 Windows 用户的 DPAPI 加密；换电脑 / 换用户都解不开。失败返回 None"""
    if crypt32 is None:
        return None
    src, _k1 = _blob(data)
    ent, _k2 = _blob(_DPAPI_ENTROPY)
    out = DATA_BLOB()
    if not CryptProtectData(ctypes.byref(src), "StockWatcher", ctypes.byref(ent), None, None,
                            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def dpapi_decrypt(data):
    if crypt32 is None or not data:
        return None
    src, _k1 = _blob(data)
    ent, _k2 = _blob(_DPAPI_ENTROPY)
    out = DATA_BLOB()
    if not CryptUnprotectData(ctypes.byref(src), None, ctypes.byref(ent), None, None,
                              CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.memset(out.pbData, 0, out.cbData)          # 明文用完即清零再释放
        LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


wtsapi32 = _opt_dll("wtsapi32")
WTSRegisterSessionNotification = (_f(wtsapi32, "WTSRegisterSessionNotification", wt.BOOL,
                                     HWND, wt.DWORD) if wtsapi32 else None)
WTSUnRegisterSessionNotification = (_f(wtsapi32, "WTSUnRegisterSessionNotification", wt.BOOL,
                                       HWND) if wtsapi32 else None)
WINEVENT_OUTOFCONTEXT, WINEVENT_SKIPOWNPROCESS = 0x0, 0x2

PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC = 0x7, 0x12

MB_YESNO, MB_ICONQUESTION, MB_TOPMOST, IDYES = 0x4, 0x20, 0x40000, 6

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


# ---------------------------------------------------------------- 小工具
def rgb(c):
    """(r,g,b) → COLORREF"""
    return c[0] | (c[1] << 8) | (c[2] << 16)


def loword(v):
    return v & 0xFFFF


def hiword(v):
    return (v >> 16) & 0xFFFF


def get_xy(lp):
    """lParam → 有符号 (x, y)，多显示器负坐标安全"""
    return ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value


def window_rect(hwnd):
    r = RECT()
    if hwnd and GetWindowRect(hwnd, ctypes.byref(r)):
        return (r.left, r.top, r.right, r.bottom)
    return None


def cursor_pos():
    p = POINT()
    GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def class_name(hwnd):
    buf = ctypes.create_unicode_buffer(128)
    GetClassNameW(hwnd, buf, 128)
    return buf.value


def monitor_info(hmon):
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if hmon and GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return mi
    return None
