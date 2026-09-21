# -*- coding: utf-8 -*-
"""微信 4.x UI 自动化（发送消息）

微信 4.x 主窗口是完全自绘的（MMUIRenderSubWindow*，不同版本后缀不同），UIA 树不可用，
因此采用坐标模拟：激活窗口 → 点击搜索框 → 输入联系人关键词 → Enter
打开会话 → 点击输入框 → 输入文本 → Enter 发送。

坐标是相对窗口的逻辑坐标（自绘 UI 版本差异可能导致偏移，可调整常量）。
验证通过后，可通过读回数据库确认消息是否发送成功（sender_id=2）。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from wechatauto import rhythm

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.FindWindowW.restype = wintypes.HWND
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.ShowWindow.restype = wintypes.BOOL
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = wintypes.BOOL
_user32.GetDpiForWindow.argtypes = [wintypes.HWND]
_user32.GetDpiForWindow.restype = wintypes.UINT

# WeChat 4.x 主窗口类名
MAIN_WND_CLASS = "Qt51514QWindowIcon"


def _restore_keep_maximize(hwnd: int):
    """取消最小化并显示窗口，同时保留其最大化状态。

    ``ShowWindow(hwnd, SW_RESTORE)`` 会把最大化窗口恢复为普通大小（窗口被
    缩小）。先用 ``GetWindowPlacement`` 记录最大化状态：原为最大化则用
    ``SW_SHOWMAXIMIZED`` 恢复，否则才走 ``SW_RESTORE``。
    """
    if not hwnd or not _user32.IsWindow(hwnd):
        return

    class _POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class _WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.UINT),
            ("flags", wintypes.UINT),
            ("showCmd", wintypes.UINT),
            ("ptMinPosition", _POINT),
            ("ptMaxPosition", _POINT),
            ("rcNormalPosition", wintypes.RECT),
        ]

    wp = _WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(_WINDOWPLACEMENT)
    if _user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
        if wp.showCmd == 3:  # SW_SHOWMAXIMIZED
            _user32.ShowWindow(hwnd, 3)  # 保持/恢复最大化
            return
    _user32.ShowWindow(hwnd, 9)  # SW_RESTORE

INPUT_EVENT = 0x0002
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_RETURN = 0x0D
WM_KEYUP = 0x0101


def _ensure_dpi_aware():
    try:
        _kernel32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        pass


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


def _send_input(scan: int = 0, unicode_char: str = "", flags: int = 0):
    inp = _INPUT()
    inp.type = INPUT_EVENT
    inp.union.ki.wVk = 0
    inp.union.ki.wScan = ord(unicode_char) if unicode_char else scan
    inp.union.ki.dwFlags = flags | KEYEVENTF_UNICODE if unicode_char else flags
    inp.union.ki.time = 0
    inp.union.ki.dwExtraInfo = 0
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def type_text(text: str, delay: float = 0.01):
    """用 Unicode 输入事件键入文本（含中文）

    逐字间隔取 ``max(delay, rhythm.type_gap())``：调用方给的固定值是下限，
    拟人档只会把打字放慢，不会比原先更快。
    """
    for ch in text:
        _send_input(unicode_char=ch, flags=0)
        _send_input(unicode_char=ch, flags=KEYEVENTF_KEYUP)
        time.sleep(max(delay, rhythm.type_gap()))


def press_enter():
    _send_input(scan=VK_RETURN, flags=0)
    _send_input(scan=VK_RETURN, flags=KEYEVENTF_KEYUP)


def click(x: int, y: int):
    rhythm.move_to(_user32, x, y)
    rhythm.nap(0.08)
    _user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    _user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP


def find_main_window():
    """返回主窗口句柄（或 None）。先按类名，失败再按标题兜底（兼容不同版本类名）。"""
    hwnd = _user32.FindWindowW(MAIN_WND_CLASS, None)
    if hwnd:
        return hwnd
    return _user32.FindWindowW(None, "微信") or None


def window_rect(hwnd):
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def dpi_scale(hwnd) -> float:
    return _user32.GetDpiForWindow(hwnd) / 96.0


class WeChatUI:
    """微信 4.x 发送工具（坐标模拟）"""

    # 逻辑坐标（相对窗口左上角），自绘 UI 版本差异可在此调整
    SEARCH_BOX = (140, 60)          # 顶部搜索框
    INPUT_BOX = (0.55, 0.88)        # 输入框（相对窗口宽/高比例）

    def __init__(self, hwnd=None):
        _ensure_dpi_aware()
        self.hwnd = hwnd or find_main_window()
        if not self.hwnd:
            raise RuntimeError("未找到微信主窗口")

    def activate(self):
        _restore_keep_maximize(self.hwnd)
        _user32.SetForegroundWindow(self.hwnd)
        time.sleep(0.5)

    def _pt(self, lx, ly):
        """逻辑坐标 → 屏幕物理坐标"""
        left, top, right, bottom = window_rect(self.hwnd)
        w, h = right - left, bottom - top
        if isinstance(lx, float):
            lx = w * lx
        if isinstance(ly, float):
            ly = h * ly
        return int(left + lx), int(top + ly)

    def open_chat(self, keyword: str):
        """通过顶部搜索框打开会话（搜索 → Enter 选中第一项）"""
        self.activate()
        x, y = self._pt(*self.SEARCH_BOX)
        click(x, y)
        time.sleep(0.6)
        type_text(keyword)
        time.sleep(0.8)
        press_enter()
        time.sleep(1.0)

    def send(self, text: str):
        """向当前打开的会话发送文本"""
        self.activate()
        x, y = self._pt(*self.INPUT_BOX)
        click(x, y)
        rhythm.nap(0.4)
        type_text(text)
        rhythm.gate('send')
        rhythm.nap(0.2)
        press_enter()
        rhythm.nap(0.6)

    def send_to(self, keyword: str, text: str):
        """搜索并打开会话后发送"""
        self.open_chat(keyword)
        self.send(text)
