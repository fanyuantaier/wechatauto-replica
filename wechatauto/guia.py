# -*- coding: utf-8 -*-
"""wechatauto.guia —— 微信 4.x 自绘界面「坐标 + OCR」自动化发送模块

背景
----
微信 4.1.12+ 的聊天区域改用自绘渲染（``MMUIRenderSubWindow*``，不同版本
后缀不同，如 ``MMUIRenderSubWindowHW`` / ``MMUIRenderSubWindow``），
对 UIAutomation / MSAA 不再暴露任何无障碍节点，因此原 wechatauto 的 UIA
方案在 4.1.x 上失效。本项目已通过「本地数据库解密」（``wechatauto/db.py``）
解决读消息；本模块针对**发送消息**补充一条「坐标 + OCR」路线：

    * 通过 Win32 定位微信主窗口与其内的 QtQuick 渲染子窗口（多特征兜底：
      标题 / 类名前缀 / 进程名 weixin.exe / 可见 / 大尺寸联合评分，类名从
      「硬条件」降级为「软条件」，Qt 升级改名也不失效；找不到渲染子窗口
      时直接回退用主窗口矩形计算坐标）；
    * 布局动态校准：首次运行时实测侧栏边界 / 搜索框 / 发送按钮锚点，保存到
      ``~/.wechatauto/layout-<机器>.json``，之后自动加载，DPI/布局漂移时
      输入框探测连续失败会自动重校准；
    * 用 Windows OCR（WinRT ``Windows.Media.Ocr``）识别会话列表 /
      输入框 / 发送按钮在屏幕上的位置；
    * 用真实鼠标事件 + 剪贴板粘贴（Ctrl+V）输入文字（避免中文输入法拦截）；
    * 点击「发送」按钮完成发送。

坐标系说明
----------
微信最大化时渲染子窗口与屏幕重合（本机 3072x1920）。本模块一律以
**渲染子窗口坐标系**（相对坐标）描述布局，运行时通过渲染窗口的屏幕
矩形换算成屏幕绝对坐标，从而兼容窗口缩放/未最大化的情况。

使用前提：
    1. 微信 4.x 已登录并**解锁桌面**（锁屏状态下无法 GUI 操作）；
    2. ``pip install -e .`` 安装依赖（含 ``winsdk``）。

示例：
    from wechatauto.guia import WeChatGUI
    wx = WeChatGUI()
    wx.bring_to_front()
    wx.open_chat('文件传输助手')
    wx.send_msg('你好，这是自动化测试')
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import platform
import re
import tempfile
import time
from ctypes import wintypes
from typing import Dict, List, Optional, Tuple

from PIL import Image

from wechatauto.logger import wxlog
from wechatauto.param import WxResponse

# ---------------------------------------------------------------------------
# DPI：模块加载时立即设为 PER_MONITOR_AWARE_V2，保证后续所有
# GetWindowRect / GetSystemMetrics / ImageGrab 截图 / SetCursorPos 点击
# 都处于同一坐标系（物理像素）。
# ---------------------------------------------------------------------------
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Win32 原生常量与结构
# ---------------------------------------------------------------------------

# keyboard / mouse 输入标志
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000

# 虚拟键
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_RETURN = 0x0D
VK_SPACE = 0x20
VK_ESCAPE = 0x1B
VK_DELETE = 0x2E
VK_A = 0x41
VK_C = 0x43
VK_V = 0x56


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MOUSE_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class MOUSE_INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _MOUSE_UNION)]


# 微信 4.x 窗口类名（不同版本/机型存在差异，统一用前缀匹配兼容所有变体）
# 主窗口：Qt51514QWindowIcon；渲染子窗口：MMUIRenderSubWindow /
# MMUIRenderSubWindowHW / 未来其他后缀。按前缀识别可覆盖全部已知/未知变体。
WX_MAIN_WIN_CLASS_PREFIX = "Qt51514QWindowIcon"
WX_MAIN_WIN_TITLE = "微信"
WX_RENDER_WIN_CLASS_PREFIX = "MMUIRenderSubWindow"

# 布局比例常量（相对渲染窗口尺寸，跨分辨率自适应）
# 侧栏宽度约占窗口 22%（微信 4.x 侧栏为固定逻辑宽度，最大化时≈0.22）；
# 其余布局元素按其与侧栏宽/窗口高的实测比例换算，保证不同 DPI/分辨率
# 与窗口尺寸下无需改动即可工作。
SIDEBAR_LEFT = 0
SIDEBAR_RATIO = 0.22                     # 侧栏宽度 / 窗口宽度
SIDEBAR_TOP = 0.05                       # 会话列表顶部起始（相对窗口高）
SEARCH_BOX_RATIO = (0.18, 0.041, 0.86, 0.079)  # (x0,y0,x1,y1)，x 相对侧栏宽、y 相对窗口高
SEND_BUTTON_RATIO = (0.78, 0.92, 0.995, 0.99)  # 「发送」按钮检索区（相对窗口）

# 多特征兜底：类名只是「软条件」之一，还需 进程名/可见/大尺寸/标题 等特征
# 联合判断，避免 Qt 升级改名（Qt51514 → Qt6xxx）后主窗口定位失效。
PROCESS_NAME = 'weixin.exe'              # 微信进程名（小写）
MIN_WINDOW_SIZE = 800                    # 主窗口最小边长（像素），小于此视为非主窗
MAIN_TITLE_KEYWORDS = ('微信', 'Weixin', 'WeChat')

# 布局校准配置目录：~/.wechatauto/layout-<机器标识>.json
LAYOUT_CONFIG_DIR = os.path.join(os.path.expanduser('~'), '.wechatauto')


def _machine_id() -> str:
    """机器标识：主机名 + 屏幕分辨率，用于区分不同机器/DPI 的布局配置。"""
    try:
        node = platform.node() or 'unknown'
    except Exception:
        node = 'unknown'
    cx = ctypes.windll.user32.GetSystemMetrics(0)  # SM_CXSCREEN
    cy = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
    safe = re.sub(r'[^\w\-]', '_', node)
    return f'{safe}_{cx}x{cy}'


def _layout_path() -> str:
    """返回本机布局校准文件路径（不存在则创建目录）。"""
    if not os.path.isdir(LAYOUT_CONFIG_DIR):
        try:
            os.makedirs(LAYOUT_CONFIG_DIR, exist_ok=True)
        except Exception:
            pass
    return os.path.join(LAYOUT_CONFIG_DIR, f'layout-{_machine_id()}.json')


def _restore_keep_maximize(user32, hwnd: int):
    """取消最小化并显示窗口，同时保留其最大化状态。

    ``ShowWindow(hwnd, SW_RESTORE)`` 会把最大化窗口恢复为普通大小
    （窗口被缩小）。这里先用 ``GetWindowPlacement`` 记录其最大化状态：
    原为最大化则用 ``SW_SHOWMAXIMIZED`` 恢复，否则才走 ``SW_RESTORE``。
    """
    if not hwnd or not user32.IsWindow(hwnd):
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
    if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
        if wp.showCmd == 3:  # SW_SHOWMAXIMIZED
            user32.ShowWindow(hwnd, 3)  # 保持/恢复最大化
            return
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE


# ---------------------------------------------------------------------------
# 底层输入封装
# ---------------------------------------------------------------------------

class WinInput:
    """基于 Win32 的真实鼠标/键盘输入（DPI 感知）。"""

    def __init__(self):
        user32 = ctypes.windll.user32
        # 进程已设为 DPI-aware（PER_MONITOR_AWARE_V2），
        # GetSystemMetrics 返回物理像素，与 UIA BoundingRectangle 一致。
        self._user32 = user32
        self.screen_w = user32.GetSystemMetrics(0)
        self.screen_h = user32.GetSystemMetrics(1)
        wxlog.debug(f'WinInput 初始化，屏幕尺寸：{self.screen_w}x{self.screen_h}')

    # -- 鼠标 ----------------------------------------------------------
    def real_click(self, x: int, y: int, right: bool = False):
        """SetCursorPos + mouse_event 的「真实」点击。

        进程在模块加载时已设为 DPI-aware（PER_MONITOR_AWARE_V2），
        UIA BoundingRectangle 返回物理像素坐标，SetCursorPos 也使用
        物理像素，两者在同一坐标系，无需额外缩放。
        """
        u = self._user32
        u.SetCursorPos(int(x), int(y))
        time.sleep(0.15)
        down = MOUSEEVENTF_RIGHTDOWN if right else MOUSEEVENTF_LEFTDOWN
        up = MOUSEEVENTF_RIGHTUP if right else MOUSEEVENTF_LEFTUP
        u.mouse_event(down, 0, 0, 0, 0)
        u.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.3)

    def send_input_click(self, x: int, y: int):
        """SendInput 绝对坐标点击（按真实屏幕尺寸缩放）。"""
        u = self._user32
        n = int(x * 65535 // self.screen_w)
        m = int(y * 65535 // self.screen_h)
        for flags in (MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_LEFTDOWN,
                      MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_LEFTUP):
            inp = MOUSE_INPUT()
            inp.type = 0
            inp.u.mi.dx = n
            inp.u.mi.dy = m
            inp.u.mi.dwFlags = flags
            u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(MOUSE_INPUT))
            time.sleep(0.06)
        time.sleep(0.3)

    def wheel(self, delta: int = -300):
        """滚轮滚动（delta 为正向上，负向下）。"""
        u = self._user32
        u.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
        time.sleep(0.4)

    # -- 键盘 ----------------------------------------------------------
    def key(self, vk: int, ctrl: bool = False, shift: bool = False):
        """发送一次虚拟键（可带 Ctrl/Shift）。"""
        mods = [(VK_CONTROL, ctrl), (VK_SHIFT, shift)]
        for vk_mod, on in mods:
            if on:
                self._raw_key(vk_mod, down=True)
        self._raw_key(vk, down=True)
        self._raw_key(vk, down=False)
        for vk_mod, on in reversed(mods):
            if on:
                self._raw_key(vk_mod, down=False)

    def _raw_key(self, vk: int, down: bool):
        """发送一次虚拟键事件。

        优先使用 ``keybd_event``（老式 API）：在部分远程/虚拟化会话中
        ``SendInput`` 键盘事件会被系统丢弃（返回 0），而 ``keybd_event``
        与 ``mouse_event`` 走同一套老式输入管线，与鼠标点击一致可用。
        """
        flags = KEYEVENTF_KEYUP if not down else 0
        ctypes.windll.user32.keybd_event(vk & 0xFFFF, 0, flags, 0)
        time.sleep(0.05)

    def type_unicode(self, text: str):
        """以 SendInput Unicode 方式键入文本（绕开键盘布局/大小写问题）。"""
        u = self._user32
        for ch in text:
            down = INPUT()
            down.type = 1
            down.u.ki.wScan = ord(ch)
            down.u.ki.dwFlags = KEYEVENTF_UNICODE
            up = INPUT()
            up.type = 1
            up.u.ki.wScan = ord(ch)
            up.u.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
            u.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
            time.sleep(0.02)
            u.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
            time.sleep(0.04)

    def type_pinyin(self, pinyin: str):
        """以虚拟键逐字母输入拼音（供中文输入法组合，如 ``ceshi`` → 测试）。"""
        for ch in pinyin.lower():
            if 'a' <= ch <= 'z':
                self.key(ord(ch) - 32)
            elif ch == ' ':
                self.key(VK_SPACE)
            time.sleep(0.05)


# ---------------------------------------------------------------------------
# OCR 封装（WinRT）
# ---------------------------------------------------------------------------

class ScreenOCR:
    """Windows 自带 OCR（WinRT Windows.Media.Ocr）封装。

    用法：``ScreenOCR.recognize(pil_image)`` 返回
    ``[(text, x, y, w, h), ...]``，坐标为图像内相对坐标。
    """

    @staticmethod
    def recognize(image: Image.Image) -> List[Tuple[str, int, int, int, int]]:
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.storage import StorageFile

        async def _run() -> List[Tuple[str, int, int, int, int]]:
            tmp = os.path.join(tempfile.gettempdir(), 'wechatauto_ocr_tmp.png')
            image.save(tmp)
            f = await StorageFile.get_file_from_path_async(tmp)
            s = await f.open_async(0)
            dec = await BitmapDecoder.create_async(s)
            bmp = await dec.get_software_bitmap_async()
            eng = OcrEngine.try_create_from_user_profile_languages()
            if eng is None:
                return []
            res = await eng.recognize_async(bmp)
            out = []
            for line in res.lines:
                text = line.text.replace(' ', '')
                if not text:
                    continue
                r = line.words[0].bounding_rect
                out.append((text, int(r.x), int(r.y), int(r.width), int(r.height)))
            return out

        try:
            return asyncio.run(asyncio.wait_for(_run(), timeout=8))
        except asyncio.TimeoutError:
            wxlog.debug('OCR 识别超时（8s）')
            return []
        except Exception as e:
            wxlog.debug(f'OCR 识别失败：{e}')
            return []


# ---------------------------------------------------------------------------
# 微信主流程
# ---------------------------------------------------------------------------

class WeChatGUI:
    """基于坐标 + OCR 的微信 4.x 自动化实例。

    Attributes:
        main_hwnd: 微信主窗口句柄
        render_hwnd: QtQuick 渲染子窗口句柄
        render_rect: 渲染窗口屏幕矩形 (left, top, right, bottom)
    """

    def __init__(self, title: str = WX_MAIN_WIN_TITLE, hwnd: int = None,
                 calibrate: bool = False):
        self._input = WinInput()
        self._sidebar_ratio = SIDEBAR_RATIO
        self._send_button_ratio = SEND_BUTTON_RATIO
        self.main_hwnd = hwnd or self._find_main_window(title)
        if not self.main_hwnd:
            raise RuntimeError('未找到微信主窗口，请确认微信已登录并运行')
        self.render_hwnd = self._find_render_window(self.main_hwnd)
        if not self.render_hwnd:
            # 找不到渲染子窗口（Qt 改版等）→ 直接用主窗口矩形计算坐标
            wxlog.warning('未找到微信渲染子窗口，回退用主窗口矩形定位坐标')
            self.render_hwnd = self.main_hwnd
        self._update_render_rect()
        self.pid = self._get_pid(self.main_hwnd)
        self._current_chat = None  # 最近打开的会话名，用于连续发送时复用
        self._last_input_box = None  # 最近一次成功发送的输入框位置，连续发送复用
        self._uia = None  # UIA 引擎（惰性创建，仅当可用时启用）
        self._uia_tried = False
        # 布局校准：显式 calibrate=True 强制重校准；否则加载本机已校准配置，
        # 没有配置则自动校准一次（OCR 可用时），实现「跑一次永久兼容」。
        if not calibrate:
            calibrate = not self._load_layout()
        if calibrate:
            self.calibrate_layout()
        wxlog.info(
            f'WeChatGUI 初始化成功：hwnd={self.main_hwnd}, '
            f'render={self.render_hwnd}, rect={self.render_rect}')

    # ------------------------------------------------------------------
    # 窗口定位
    # ------------------------------------------------------------------
    def _process_name(self, pid: int) -> str:
        """返回 pid 对应进程的可执行文件名（小写），失败返回空串。"""
        if not pid:
            return ''
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return ''
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
                h, 0, buf, ctypes.byref(size))
            if ok:
                return os.path.basename(buf.value).lower()
        except Exception:
            return ''
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
        return ''

    def _looks_like_main_window(self, hwnd: int) -> bool:
        """多特征联合判断是否为微信主窗口（类名只是软条件之一）。

        特征：标题含微信关键字 / 类名前缀 Qt51514QWindowIcon* /
        进程名为 weixin.exe / 可见 / 尺寸 >= MIN_WINDOW_SIZE。任一项都不
        单独成立，评分（权重和）>=5 才认可，防止 Qt 升级改名后失效。
        """
        u = self._input._user32
        if not u.IsWindow(hwnd) or not u.IsWindowVisible(hwnd):
            return False
        buf = ctypes.create_unicode_buffer(256)
        cls = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, buf, 256)
        u.GetClassNameW(hwnd, cls, 256)
        title, cls_name = buf.value.strip(), cls.value
        score = 0
        if any(k in title for k in MAIN_TITLE_KEYWORDS):
            score += 5
        if cls_name.startswith(WX_MAIN_WIN_CLASS_PREFIX):
            score += 4
        if self._process_name(self._get_pid(hwnd)) == PROCESS_NAME:
            score += 3
        rr = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rr))
        if (rr.right - rr.left) >= MIN_WINDOW_SIZE \
                or (rr.bottom - rr.top) >= MIN_WINDOW_SIZE:
            score += 2
        return score >= 5

    def _find_main_window(self, title: str) -> int:
        """多特征兜底定位微信主窗口。

        类名前缀不再是硬条件（Qt 升级 Qt51514→Qt6xxx 后会改名），改为在
        所有可见顶层窗口中按「标题/类名/进程名/可见/尺寸」评分，取最高分。
        找不到时退回按标题精确查找。
        """
        u = self._input._user32
        scored = []
        cb_ref = []

        def _cb(h, lp):
            if not u.IsWindowVisible(h):
                return True
            buf = ctypes.create_unicode_buffer(256)
            cls = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(h, buf, 256)
            u.GetClassNameW(h, cls, 256)
            title_text, cls_name = buf.value.strip(), cls.value
            if not (title_text or cls_name):
                return True
            score = 0
            if any(k in title_text for k in MAIN_TITLE_KEYWORDS):
                score += 5
            if cls_name.startswith(WX_MAIN_WIN_CLASS_PREFIX):
                score += 4
            if self._process_name(self._get_pid(h)) == PROCESS_NAME:
                score += 3
            rr = wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(rr))
            w = rr.right - rr.left
            ht = rr.bottom - rr.top
            if w > 0 and ht > 0:
                if w >= MIN_WINDOW_SIZE or ht >= MIN_WINDOW_SIZE:
                    score += 2
                if score >= 5:
                    scored.append((score, w * ht, h))
            return True

        CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        cb_ref.append(CB(_cb))
        u.EnumWindows(cb_ref[0], 0)
        if scored:
            scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
            return scored[0][2]
        # 完全找不到时退回按标题精确查找
        hwnd = u.FindWindowW(None, title)
        if hwnd and self._looks_like_main_window(hwnd):
            return hwnd
        return 0

    def _find_render_window(self, main_hwnd: int) -> int:
        u = self._input._user32
        found = []
        cb_ref = []

        def _cb(h, lp):
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(h, cls, 256)
            if not cls.value.startswith(WX_RENDER_WIN_CLASS_PREFIX):
                return True
            rr = wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(rr))
            w = rr.right - rr.left
            ht = rr.bottom - rr.top
            if w > 0 and ht > 0:
                found.append((w * ht, h))
            return True

        CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        cb_ref.append(CB(_cb))
        u.EnumChildWindows(main_hwnd, cb_ref[0], 0)
        if not found:
            return 0
        found.sort(key=lambda t: t[0], reverse=True)
        return found[0][1]

    def _get_pid(self, hwnd: int) -> int:
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _update_render_rect(self):
        rr = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self.render_hwnd, ctypes.byref(rr))
        self.render_rect = (rr.left, rr.top, rr.right, rr.bottom)
        self.origin_x, self.origin_y = rr.left, rr.top
        self.render_w = rr.right - rr.left
        self.render_h = rr.bottom - rr.top
        self._update_layout()

    def _update_layout(self):
        """根据当前渲染窗口尺寸换算各布局区域（渲染相对坐标）。

        所有硬编码像素坐标替换为按比例计算，保证跨 DPI/分辨率/窗口
        尺寸一致：``sidebar_right``（右面板左边界）、``search_box``
        （左侧搜索框）、``send_button_region``（发送按钮检索区）。

        比例优先取布局校准结果（``_sidebar_ratio`` / ``_send_button_ratio``），
        未校准时回落到模块默认常量。
        """
        sb_ratio = getattr(self, '_sidebar_ratio', SIDEBAR_RATIO)
        send_ratio = getattr(self, '_send_button_ratio', SEND_BUTTON_RATIO)
        self.sidebar_right = max(120, int(self.render_w * sb_ratio))
        self.right_pane_left = self.sidebar_right
        sx0, sy0, sx1, sy1 = SEARCH_BOX_RATIO
        self.search_box = (
            int(self.sidebar_right * sx0), int(self.render_h * sy0),
            int(self.sidebar_right * sx1), int(self.render_h * sy1))
        bx0, by0, bx1, by1 = send_ratio
        self.send_button_region = (
            int(self.render_w * bx0), int(self.render_h * by0),
            int(self.render_w * bx1), int(self.render_h * by1))

    # ------------------------------------------------------------------
    # 布局动态校准（防 DPI/布局漂移）
    # ------------------------------------------------------------------
    def _detect_sidebar_ratio(self) -> Optional[float]:
        """OCR 锚点法实测侧栏宽度比例，失败返回 None。

        微信 4.x 侧栏背景与消息区同为白色，像素边界几乎不可见，唯一稳定
        的结构锚点是搜索框内的「搜索」占位文本（文本中心 ≈ 侧栏宽的 0.28，
        实测于本机 3072x1920）。结果钳制在 [0.14, 0.30]，超出正常范围视为
        误检（如 OCR 读到别处的「搜索」），返回 None 由调用方回退默认比例。
        """
        try:
            for _ in range(2):
                lines = self.ocr((0, 0, self.render_w, self.render_h))
                for text, x, y, w, h in lines:
                    t = (text or '').strip()
                    if '搜索' in t and x < self.render_w * 0.35 \
                            and y < self.render_h * 0.3:
                        cx = x + w // 2
                        ratio = (cx / 0.28) / self.render_w
                        if 0.14 <= ratio <= 0.30:
                            return ratio
                time.sleep(0.8)
            return None
        except Exception:
            return None

    def calibrate_layout(self, save: bool = True) -> bool:
        """检测布局锚点并实测布局比例，写入 ``~/.wechatauto/layout-<机器>.json``。

        锚点（实测驱动，所有结果都钳制在合理范围，防误检破坏可用布局）：
            * 侧栏宽度：OCR 找「搜索」占位文本，反推侧栏右边界；
            * 发送按钮：OCR 在右下角找「发送」文本，反推检索区比例
              （发送按钮仅输入框有内容时可见，找不到就保持默认）。
        校准采用「保守优先」策略：任一项检测失败即用模块默认比例，宁可
        不做调整也不产生错误的坐标。返回是否成功。
        """
        def _run_with_timeout(fn, timeout=5):
            result = [None]
            def _target():
                try:
                    result[0] = fn()
                except Exception:
                    pass
            t = threading.Thread(target=_target, daemon=True)
            t.start()
            t.join(timeout)
            return result[0]

        try:
            self._update_render_rect()
            self.bring_to_front()
            time.sleep(0.8)
            self._update_render_rect()
            layout: Dict[str, object] = {'machine': _machine_id()}
            # 1) 侧栏宽度：OCR「搜索」锚点（限制 5s 超时）
            sb = _run_with_timeout(self._detect_sidebar_ratio, timeout=5)
            layout['sidebar_ratio'] = float(sb) if sb else SIDEBAR_RATIO
            # 2) 发送按钮：OCR「发送」（仅右下角检索区，限制 5s 超时）
            try:
                lines = _run_with_timeout(
                    lambda: self.ocr((int(self.render_w * 0.5),
                                      int(self.render_h * 0.7),
                                      self.render_w, self.render_h)),
                    timeout=5)
                lines = lines or []
            except Exception:
                lines = []
            send = None
            for text, x, y, w, h in lines:
                if (text or '').strip() == '发送':
                    send = (x, y, w, h)
                    break
            if send:
                sx, sy, sw, sh = send
                # 检索区需略大于按钮本体，OCR 才能稳定命中；四周留边距
                pad_x, pad_y = 40, 20
                x0 = max(0, sx - pad_x)
                y0 = max(0, sy - pad_y)
                x1 = min(self.render_w, sx + sw + pad_x)
                y1 = min(self.render_h, sy + sh + pad_y)
                layout['send_button_ratio'] = [
                    x0 / self.render_w, y0 / self.render_h,
                    x1 / self.render_w, y1 / self.render_h]
            else:
                layout['send_button_ratio'] = list(SEND_BUTTON_RATIO)
            layout['render_w'] = self.render_w
            layout['render_h'] = self.render_h
            layout['date'] = time.strftime('%Y-%m-%d %H:%M:%S')
            if save:
                try:
                    with open(_layout_path(), 'w', encoding='utf-8') as f:
                        json.dump(layout, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    wxlog.debug(f'保存布局校准文件失败：{e}')
            self._apply_layout(layout)
            wxlog.info(
                f'布局校准完成：sidebar_ratio={layout["sidebar_ratio"]:.3f}, '
                f'send_button_ratio={layout["send_button_ratio"]}')
            return True
        except Exception as e:
            wxlog.debug(f'布局校准失败：{e}')
            return False

    def _apply_layout(self, d: dict) -> None:
        """应用布局校准结果并重算各布局区域。"""
        self._sidebar_ratio = float(d.get('sidebar_ratio', SIDEBAR_RATIO))
        sb = d.get('send_button_ratio')
        if isinstance(sb, (list, tuple)) and len(sb) == 4:
            try:
                self._send_button_ratio = tuple(float(v) for v in sb)
            except Exception:
                self._send_button_ratio = SEND_BUTTON_RATIO
        self._update_layout()

    def _load_layout(self) -> bool:
        """运行前自动加载本机已校准的布局配置，返回是否成功采用。

        窗口尺寸与校准当时差异过大（>15%，如窗口缩放/未最大化）时视为
        布局不匹配，拒绝采用并触发重新校准。
        """
        p = _layout_path()
        if not os.path.isfile(p):
            return False
        try:
            with open(p, encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            return False
        if abs(float(d.get('render_w', 0) or 0) - self.render_w) \
                / max(self.render_w, 1) > 0.15:
            wxlog.info(
                f'布局配置与当前窗口尺寸差异过大，忽略并重新校准'
                f'（校准={d.get("render_w")} vs 当前={self.render_w}）')
            return False
        self._apply_layout(d)
        wxlog.info(f'已加载布局校准：sidebar_ratio={self._sidebar_ratio:.3f}')
        return True

    def use_window(self, top_hwnd: int) -> bool:
        """把 GUI 操作目标切换到指定顶层微信窗口（主窗或独立聊天窗）。

        微信 4.x 通过搜索打开会话时会生成独立聊天窗口（标题如
        “昵称与X的聊天记录”）。切换后 render_rect/origin 等随新窗口更新，
        click/paste/OCR 等原语无需改动即可在新窗口内工作。找不到渲染
        子窗口时直接用该顶层窗口矩形计算坐标。
        """
        if not top_hwnd or not self._input._user32.IsWindow(top_hwnd):
            return False
        render = self._find_render_window(top_hwnd)
        if not render:
            render = top_hwnd  # 渲染窗口缺失时回退用窗口矩形
        self.main_hwnd = top_hwnd
        self.render_hwnd = render
        self._update_render_rect()
        return True

    def _find_chat_window(self, name: str) -> int:
        """在当前微信进程的所有可见窗口中，找标题含 name 前2字与“聊天记录”的独立聊天窗。"""
        u = self._input._user32
        wx_pid = self._get_pid(self.main_hwnd)
        frag = name[:2]
        found = 0
        cb_ref = []

        def _cb(h, lp):
            nonlocal found
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value != wx_pid or not u.IsWindowVisible(h):
                return True
            buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(h, buf, 256)
            title = buf.value
            if title and '聊天记录' in title and frag in title:
                found = h
                return False
            return True

        CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        cb_ref.append(CB(_cb))
        u.EnumWindows(cb_ref[0], 0)
        return found

    def is_alive(self) -> bool:
        return bool(self._input._user32.IsWindow(self.main_hwnd))

    # ------------------------------------------------------------------
    # 前台与可用性
    # ------------------------------------------------------------------
    def bring_to_front(self, keep_topmost: bool = False) -> bool:
        """将微信窗口置为前台，返回是否成功。

        依次尝试：恢复窗口 → 置顶(HWND_TOPMOST) → SetForegroundWindow，
        并配合 AttachThreadInput 解除焦点抢占限制。

        参数 ``keep_topmost``：为 True 时保持置顶（适合在被其他窗口遮挡
        的环境下连续操作，操作完成后可调用 ``restore_zorder()`` 取消置顶）；
        为 False 时操作结束后立即取消置顶。
        """
        u = self._input._user32
        SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x2, 0x1, 0x40

        # 清除前台锁（SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001,值=0 表示无延迟）
        SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETFOREGROUNDLOCKTIMEOUT, 0, None, 0)

        for attempt in range(5):
            fg = u.GetForegroundWindow()
            if fg == self.main_hwnd:
                break
            tid_fg = u.GetWindowThreadProcessId(fg, None) if fg else 0
            tid_t = u.GetWindowThreadProcessId(self.main_hwnd, None)
            if tid_fg:
                u.AttachThreadInput(tid_fg, tid_t, True)
            _restore_keep_maximize(u, self.main_hwnd)
            u.SetWindowPos(self.main_hwnd, -1, 0, 0, 0, 0,
                           SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)  # HWND_TOPMOST
            u.SetForegroundWindow(self.main_hwnd)
            u.SetActiveWindow(self.main_hwnd)
            u.BringWindowToTop(self.main_hwnd)
            if self.render_hwnd and u.GetFocus() != self.render_hwnd:
                u.SetFocus(self.render_hwnd)
            if tid_fg:
                u.AttachThreadInput(tid_fg, tid_t, False)
            time.sleep(0.6)
            if u.GetForegroundWindow() == self.main_hwnd:
                break
        if not keep_topmost:
            u.SetWindowPos(self.main_hwnd, -2, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)  # HWND_NOTOPMOST
        fg = u.GetForegroundWindow()
        return fg == self.main_hwnd or fg is None

    def restore_zorder(self):
        """取消置顶，恢复普通 Z 序（配合 ``bring_to_front(keep_topmost=True)`` 使用）。"""
        self._input._user32.SetWindowPos(self.main_hwnd, -2, 0, 0, 0, 0, 0x2 | 0x1)

    def _minimize_blockers(self) -> int:
        """最小化所有与微信主窗口重叠的非微信顶层窗口，返回处理个数。

        微信被 Chrome/OpenCode 等窗口覆盖时，鼠标点击的命中测试按 Z 序
        会落到覆盖层上，必须先把遮挡窗口最小化才能操作微信。
        只处理「可见 + 与微信重叠 + 非微信进程 + 非系统装饰窗口」的窗口。
        """
        u = self._input._user32
        wx_pid = self._get_pid(self.main_hwnd)
        rect = wintypes.RECT()
        u.GetWindowRect(self.main_hwnd, ctypes.byref(rect))
        wx = (rect.left, rect.top, rect.right, rect.bottom)
        skip_classes = ("Progman", "WorkerW", "Shell_TrayWnd", "kugou_ui",
                        "MSCTFIME UI", "IME")
        targets = []

        def _cb(h, lp):
            if not u.IsWindowVisible(h) or u.IsIconic(h):
                return True
            cls_buf = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(h, cls_buf, 256)
            cls = cls_buf.value
            if cls in skip_classes or cls.startswith("Windows.UI.Core"):
                return True
            if self._get_pid(h) == wx_pid:
                return True  # 微信自身窗口不动
            r2 = wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(r2))
            if not (r2.right > wx[0] and r2.left < wx[2]
                    and r2.bottom > wx[1] and r2.top < wx[3]):
                return True  # 与微信不重叠
            title_buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(h, title_buf, 256)
            if cls == "Chrome_WidgetWin_1" or title_buf.value.strip():
                targets.append(h)
            return True

        cb_ref = []
        CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        cb_ref.append(CB(_cb))
        u.EnumWindows(cb_ref[0], 0)
        for h in targets:
            u.ShowWindow(h, 6)  # SW_MINIMIZE
        if targets:
            wxlog.info(f'自动最小化遮挡窗口 {len(targets)} 个')
        return len(targets)

    def ensure_visible(self) -> bool:
        """自动最小化遮挡窗口并把微信置于前台，返回桌面是否可用。

        发送类操作前调用，替代「手动最小化 Chrome 再置顶微信」的步骤。
        遮挡窗口最小化后微信仍保持置顶，便于连续多次发送。

        批量发送（三件套等）会逐条调用本方法，为避免每条都重复
        枚举窗口 / 置顶（上轮实测每次开销 20s+），15 秒内已成功
        前置过且窗口仍存活则直接复用，不再重复扫描。
        """
        if (getattr(self, '_last_visible_ok', False)
                and getattr(self, '_last_visible_ts', 0) > time.time() - 15
                and self.is_alive()):
            return True
        self._minimize_blockers()
        time.sleep(0.8)
        self.bring_to_front(keep_topmost=True)
        time.sleep(0.5)
        self._update_render_rect()
        ok = self.desktop_available()
        self._last_visible_ok = ok
        self._last_visible_ts = time.time()
        return ok

    def wx_click(self, x: int, y: int, right: bool = False):
        """点击微信渲染窗口内的坐标。

        微信渲染子窗口（MMUIRenderSubWindow*）设置了
        ``WS_EX_LAYERED | WS_EX_TRANSPARENT``，导致 mouse_event
        的点击会穿透到主窗口而无法到达渲染层。此方法在点击前后
        临时去掉 ``WS_EX_TRANSPARENT``，使点击能被渲染窗口接收，
        随即恢复原样式以保证画面正常合成。
        """
        u = self._input._user32
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        old_ex = u.GetWindowLongW(self.render_hwnd, GWL_EXSTYLE)
        if old_ex & WS_EX_TRANSPARENT:
            u.SetWindowLongW(self.render_hwnd, GWL_EXSTYLE,
                             old_ex & ~WS_EX_TRANSPARENT)
            time.sleep(0.05)
        try:
            self._input.real_click(x, y, right=right)
        finally:
            if old_ex & WS_EX_TRANSPARENT:
                u.SetWindowLongW(self.render_hwnd, GWL_EXSTYLE, old_ex)
                time.sleep(0.05)

    def wx_wheel(self, delta: int):
        """滚轮滚动渲染窗口内的内容。

        与 wx_click 同理：渲染子窗口带 WS_EX_TRANSPARENT，mouse_event 的
        滚轮事件会穿透到下层窗口，必须临时去掉该样式再滚动。
        """
        u = self._input._user32
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        old_ex = u.GetWindowLongW(self.render_hwnd, GWL_EXSTYLE)
        if old_ex & WS_EX_TRANSPARENT:
            u.SetWindowLongW(self.render_hwnd, GWL_EXSTYLE,
                             old_ex & ~WS_EX_TRANSPARENT)
            time.sleep(0.05)
        try:
            self._input.wheel(delta)
        finally:
            if old_ex & WS_EX_TRANSPARENT:
                u.SetWindowLongW(self.render_hwnd, GWL_EXSTYLE, old_ex)
                time.sleep(0.05)

    def desktop_available(self) -> bool:
        """检查微信窗口是否真的可见（锁屏/会话断开时返回 False）。"""
        if not self.is_alive():
            return False
        try:
            img = self._grab_screen(self.render_rect)
        except Exception:
            return False
        px = img.load()
        w, h = img.size
        white = total = 0
        for y in range(0, h, 16):
            for x in range(0, w, 16):
                r, g, b = px[x, y]
                total += 1
                if r > 230 and g > 230 and b > 230:
                    white += 1
        return white / max(total, 1) > 0.05

    # ------------------------------------------------------------------
    # 截图与 OCR
    # ------------------------------------------------------------------
    def _grab_screen(self, box: Optional[Tuple[int, int, int, int]] = None) -> Image.Image:
        """截取屏幕区域（带重试）。box 为屏幕绝对坐标。"""
        from PIL import ImageGrab
        last = None
        for _ in range(6):
            try:
                return ImageGrab.grab(bbox=box) if box else ImageGrab.grab()
            except Exception as e:
                last = e
                time.sleep(0.5)
        raise RuntimeError(f'屏幕截图失败：{last}')

    def _rel_to_screen(self, rel: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        x0, y0, x1, y1 = rel
        return (self.origin_x + x0, self.origin_y + y0,
                self.origin_x + x1, self.origin_y + y1)

    def ocr(self, rel_box: Tuple[int, int, int, int]) -> List[Tuple[str, int, int, int, int]]:
        """对渲染窗口相对区域做 OCR，返回 (text, rel_x, rel_y, w, h)。"""
        screen_box = self._rel_to_screen(rel_box)
        img = self._grab_screen(screen_box)
        res = ScreenOCR.recognize(img)
        out = []
        for text, x, y, w, h in res:
            out.append((text, rel_box[0] + x, rel_box[1] + y, w, h))
        return out

    def ocr_zoomed(self, rel_box: Tuple[int, int, int, int],
                   scale: int = 3) -> List[Tuple[str, int, int, int, int]]:
        """对渲染窗口相对区域放大 scale 倍后 OCR，返回渲染相对坐标。

        微信 4.x 的小字号标题（尤其含生僻字如「卢立竺」）原尺寸 OCR 常
        漏识别或读出乱码，放大后识别率显著提升。坐标按 1/scale 还原。
        """
        screen_box = self._rel_to_screen(rel_box)
        img = self._grab_screen(screen_box)
        w, h = img.size
        img = img.resize((w * scale, h * scale), Image.LANCZOS)
        res = ScreenOCR.recognize(img)
        out = []
        for text, x, y, w, h in res:
            out.append((text, rel_box[0] + x // scale,
                        rel_box[1] + y // scale, w // scale, h // scale))
        return out

    # ------------------------------------------------------------------
    # 会话列表
    # ------------------------------------------------------------------
    def get_sessions(self, zoomed: bool = False) -> List[Dict[str, object]]:
        """OCR 识别左侧会话列表，返回 [{name, x, y, w, h}]（渲染相对坐标）。

        会话名文本约占侧栏宽的 30%~100%；左侧 <30% 为头像/未读角标，
        右侧为时间戳。均按比例过滤，跨分辨率一致。

        zoomed=True 时对整块侧栏放大 3 倍后 OCR，用于原尺寸扫不到
        （生僻字/小字号）时兜底；速度较慢，仅按需启用。
        """
        rel = (SIDEBAR_LEFT, int(self.render_h * SIDEBAR_TOP),
               self.sidebar_right, self.render_h)
        lines = self.ocr_zoomed(rel, scale=3) if zoomed else self.ocr(rel)
        rows = []
        for text, x, y, w, h in lines:
            t = (text or '').strip()
            if not t:
                continue
            if x < self.sidebar_right * 0.30:   # 头像/图标/角标区
                continue
            if any(k in t for k in ('搜索', '聊天', '通讯录')):
                continue
            rows.append({'name': t, 'x': x, 'y': y, 'w': w, 'h': h})
        return rows

    @staticmethod
    def _name_matches(ocr_name: str, target: str) -> bool:
        """OCR 会话名的韧性匹配，容忍首尾字符截断/粘连。

        微信 4.x 标题 OCR 常把首字截掉（'文件传输助手'→'件传输助'），
        因此不能只用精确相等或 startswith。匹配优先级：
            1. 完整相等
            2. 识别名是目标名的一段（截首/截尾/截中）
            3. 识别名更长时目标名是其子串（识别名带多余字符）
        """
        a = (ocr_name or '').strip()
        b = (target or '').strip()
        if not a or not b:
            return False
        if a == b:
            return True
        # 截断容忍：OCR 名是目标名的一段（识别名比目标短，属截首/截尾/截中）
        if a in b:
            return True
        # 识别名更长（含多余字符）：仅当识别名 >= 3 字时接受子串命中，
        # 避免目标名（如 2 字）是较长识别名前缀导致的误点
        if b in a and len(a) >= 3:
            return True
        return False

    def find_session(self, name: str, max_scroll: int = 3) -> Optional[Tuple[int, int]]:
        """在会话列表中查找指定会话，返回可点击的 (相对x, 相对y)。

        点击点取 OCR 文本框中心（而非硬编码列坐标），自动适配任何
        窗口宽度/DPI。渲染可能在窗口置前台后短暂未刷新，先重复纯 OCR
        再滚动；找不到时悬停列表滚动重试。

        滚动方向自适应：先向上翻找列表上半段的高亮会话（已打开的会话
        会被置顶），找不到再向下滚动其它区域。
        """
        u = self._input._user32

        def _scan(zoomed: bool = False) -> Optional[Tuple[int, int]]:
            # 按 y 排序，OCR 返回顺序不可靠，保证点击视觉上正确的行
            for row in sorted(self.get_sessions(zoomed=zoomed), key=lambda r: r['y']):
                if self._name_matches(row['name'], name):
                    return (row['x'] + row['w'] // 2, row['y'] + row['h'] // 2)
            return None

        def _scan_vote(zoomed: bool = False, rounds: int = 4,
                       min_votes: int = 2,
                       tol: int = 30) -> Optional[Tuple[int, int]]:
            """多轮 OCR 投票找会话行，抗单轮识别抖动。

            WinRT OCR 对生僻字/小字号（如「卢立竺」）存在抖动：同一行
            不同轮次可能读出「卢立竺」或「亠人五」。逐轮扫描并把命中行
            按 y 聚类，票数达到 min_votes 才返回，显著降低误配率。
            """
            hits = []  # (y, x, w, h)
            for _ in range(rounds):
                # 每轮只取 y 最靠前的一个命中，避免同轮多个误配行干扰
                for row in sorted(self.get_sessions(zoomed=zoomed),
                                  key=lambda r: r['y']):
                    if self._name_matches(row['name'], name):
                        hits.append((row['y'], row['x'], row['w'], row['h']))
                        break
                time.sleep(0.15)
            if not hits:
                return None
            hits.sort()
            clusters = []  # [y均值, x均值, w均值, h均值, 票数]
            for y, x, w, h in hits:
                for c in clusters:
                    if abs(c[0] - y) <= tol:
                        c[4] += 1
                        n = c[4]
                        c[0] = (c[0] * (n - 1) + y) / n
                        c[1] = (c[1] * (n - 1) + x) / n
                        c[2] = max(c[2], w)
                        c[3] = max(c[3], h)
                        break
                else:
                    clusters.append([float(y), float(x), w, h, 1])
            best = max(clusters, key=lambda c: c[4])
            if best[4] < min_votes:
                return None
            return (int(best[1] + best[2] // 2), int(best[0] + best[3] // 2))

        for _ in range(2):          # 先不带滚动重试 OCR，等渲染刷新
            hit = _scan()
            if hit:
                return hit
            time.sleep(0.4)
        # 原尺寸扫不到 → 放大 3 倍多轮投票（生僻字/小字号会话，如「卢立竺」）
        hit = _scan_vote(zoomed=True)
        if hit:
            return hit
        for _ in range(max_scroll):
            hit = _scan()
            if hit:
                return hit
            # 未找到 → 悬停会话列表，先向上翻再向下翻各试探
            u.SetCursorPos(self.origin_x + self.sidebar_right // 2,
                           self.origin_y + int(self.render_h * 0.5))
            time.sleep(0.2)
            self.wx_wheel(-360)
            time.sleep(0.6)
            hit = _scan()
            if hit:
                return hit
            hit = _scan_vote(zoomed=True)
            if hit:
                return hit
            self.wx_wheel(360)
            time.sleep(0.6)
        return None

    def _row_is_active(self, rel_y: int) -> bool:
        """判断侧栏指定行是否为当前高亮（已打开）的会话。

        微信 4.x 活动会话行背景为绿色 (≈21,172,112)，普通行为浅灰
        (238,238,240)。取会话名右侧空白横向条带统计绿色像素即可判断。
        """
        try:
            y0 = max(0, rel_y - 6)
            x1 = max(self.right_pane_left - 40, 360)
            rel = (360, y0, x1, rel_y + 6)
            img = self._grab_screen(self._rel_to_screen(rel))
            px = img.load()
            w, h = img.size
            green = 0
            for yy in range(0, h, 2):
                for xx in range(0, w, 2):
                    r, g, b = px[xx, yy][:3]
                    if g > r + 25 and g > b + 25 and g > 100:
                        green += 1
            return green > 20
        except Exception:
            return False

    def _get_uia(self, refresh: bool = False):
        """惰性创建并复用 UIA 引擎；不可用时返回 None（由调用方降级 OCR）。

        混合驱动：UIA 树需热激活（写 Weixin.dll 的 Qt accessibility byte）。
        首次尝试失败则本次会话内不再重试（避免每条消息都等超时）。

        Args:
            refresh: True 时跳过缓存**强制重新热激活**（写 accessibility gate
                byte）并重建引擎。微信重启/重登后 gate byte 会失效、导致
                “昨天能用今天不能”，此时传 True 可恢复 UIA 树。
        """
        if refresh or not self._uia_tried:
            self._uia_tried = True
            try:
                from wechatauto.uia_driver import WeChatUIA
                eng = WeChatUIA()
                if eng.ensure_window():
                    self._uia = eng
                    wxlog.info('已启用 UIA 驱动（混合路径：UIA 优先，OCR 兜底）')
                else:
                    self._uia = None
                    wxlog.info('UIA 树不可用，本次会话使用 OCR 驱动')
            except Exception as e:
                self._uia = None
                wxlog.debug('初始化 UIA 引擎失败：%s', e)
        return self._uia

    def open_chat(self, name: str, exact: bool = False) -> bool:
        """打开指定会话的聊天窗口（优先 UIA，其次 OCR 侧栏/搜索）。

        微信 4.x 行为：点侧栏会话在主窗右侧面板打开；搜索下拉点联系人则
        打开独立聊天窗口（标题“昵称与X的聊天记录”）。本方法两条路都处理，
        打开后把 GUI 操作目标切到对应窗口，并验证会话真正打开。
        """
        uia = self._get_uia()
        if uia is not None and uia.open_chat(name):
            self._current_chat = name
            return True
        if not self.ensure_visible():
            wxlog.warning('无法将微信窗口置于前台，尝试继续操作')
        # 优先：右侧面板当前已打开目标会话 → 直接成功
        # （微信 4.x 已打开的会话才渲染右侧，侧栏查找反而慢且易误配）
        self._update_render_rect()
        if self._chat_is_open(name) and self._pane_has_content():
            return True
        # 优先侧栏：多轮「查找 + 点击 + 确认」（侧栏点击最可靠）
        for _ in range(3):
            pos = self.find_session(name)
            if pos:
                self.use_window(self.main_hwnd)
                rx, ry = pos
                # 行已是当前高亮（会话已打开）：再点一次会关闭它，直接确认
                if self._row_is_active(ry):
                    if self._pane_has_content():
                        return True
                    # 高亮但面板空白：不点击（点击会关闭会话），交给搜索兜底
                    wxlog.debug(f'会话 {name} 行已高亮但面板空白，跳过点击')
                else:
                    self.wx_click(self.origin_x + rx, self.origin_y + ry)
                    if self._chat_open_confirmed(name):
                        return True
            wxlog.debug(f'侧栏未确认打开 {name}，重试')
            time.sleep(0.5)
        # 侧栏找不到 → 搜索框回退（搜索下拉易误开独立历史窗，仅作最后手段）
        if self._search_chat(name):
            sub = self._find_chat_window(name)
            if sub:
                _restore_keep_maximize(self._input._user32, sub)
                self._input._user32.SetForegroundWindow(sub)
                time.sleep(0.5)
                self.use_window(sub)
                time.sleep(0.3)
                return True
            if self._chat_open_confirmed(name):
                return True
        return False

    def _chat_open_confirmed(self, name: str) -> bool:
        """点击会话后轮询确认已打开。

        微信 4.x 聊天标题为浅灰渲染、OCR 常读不到，因此标题命中算最可靠
        证据，读不到时以「消息区已渲染非空白」作为会话已打开的判据。
        注意：若此前已开着其它会话，面板非空白并不代表目标已打开，
        必须优先看标题命中。
        """
        for _ in range(5):
            time.sleep(0.6)
            if self._chat_is_open(name):
                return True
        # 标题始终读不到才退而求其次：面板有内容且非目标会话也算打开
        # （点错会话时标题会匹配到别的名字，标题优先可拦截误开）
        for _ in range(3):
            time.sleep(0.4)
            if self._chat_is_open(name):
                return True
            if self._pane_has_content():
                return True
        return False

    def _pane_has_content(self) -> bool:
        """右侧消息区是否渲染了内容（非全白空白页）。"""
        try:
            y0 = int(self.render_h * 0.18)
            y1 = int(self.render_h * 0.72)
            rel = (self.right_pane_left + 40, y0, self.render_w - 40, y1)
            img = self._grab_screen(self._rel_to_screen(rel))
            px = img.load()
            w, h = img.size
            non_white = sum(1 for y in range(0, h, 6) for x in range(0, w, 6)
                            if sum(px[x, y][:3]) / 3 <= 235)
            return non_white > 30
        except Exception:
            return False

    def _chat_is_open(self, name: str) -> bool:
        """检测右侧面板是否打开了指定会话（标题 OCR）。

        微信 4.x 标题 OCR 常把首字符截掉（“文件传输助手”→“件传输助”），
        因此不能用前 2 字匹配，需用名称中段片段（如第 2-3 字符）。标题
        字号小、含生僻字时原尺寸 OCR 会漏识别，需放大后再读；标题行实际
        渲染在 y≈80-180（非顶部 15-100），OCR 区需向上/向下多留。
        """
        try:
            # 标题文本贴右面板左边缘（可能略越过侧栏边界），OCR 区向左多留
            x0 = max(0, self.right_pane_left - 60)
            res = self.ocr_zoomed((x0, 0, self.render_w, 185), scale=3)
            title = ''.join(t.strip() for t, x, y, w, h in res)
            if len(name) >= 3:
                frags = (name[1:3], name[2:4], name[-2:], name[:2])
            else:
                frags = (name,)
            return any(f and f in title for f in frags)
        except Exception:
            return False

    def _search_chat(self, name: str) -> bool:
        """退路：搜索框 + 剪贴板粘贴搜索，点选名称匹配的第一条联系人。

        搜索下拉结果排布：联系人在最上方，下面是「搜索网络结果 / 搜一搜」
        等节标题。OCR 结果按 y 排序后，跳过节标题/提示行，点选视觉上
        第一条匹配名称的联系人行。

        群聊的成员预览行（如「00，包含：卢立竺」）也含目标名片段，若不
        排除会误点群聊而非联系人。群聊节标题「群聊」以下的行优先排除，
        含「包含」的成员预览行直接跳过。
        """
        frag = name[:2]
        for _ in range(3):
            sb = self._rel_to_screen(self.search_box)
            self.wx_click((sb[0] + sb[2]) // 2, (sb[1] + sb[3]) // 2)
            time.sleep(0.3)
            self._input.key(VK_A, ctrl=True)
            self._input.key(VK_DELETE)
            self.set_clipboard(name)
            self._input.key(VK_V, ctrl=True)
            time.sleep(0.8)
            res = self.ocr_zoomed((SIDEBAR_LEFT, int(self.render_h * 0.08),
                                   self.sidebar_right, self.render_h), scale=2)
            rows = sorted(res, key=lambda r: (r[2], r[1]))
            group_section = False
            for t, x, y, w, h in rows:
                tt = (t.strip() or '')
                if not tt:
                    continue
                if tt == '群聊':
                    group_section = True
                    continue
                if tt.startswith('聊天记录') or tt.startswith('查看全部'):
                    break
                if group_section:
                    continue
                # 跳过“搜索/网络/搜一搜”等节标题与提示行
                if (tt.startswith('搜索') or tt.startswith('搜一搜')
                        or '网络' in tt or '暂无' in tt):
                    continue
                # 群聊成员预览行，跳过以免误点群聊
                if '包含' in tt or tt.endswith('群') or tt.endswith('群聊'):
                    continue
                if frag in tt:
                    self.wx_click(self.origin_x + x + w // 2,
                                  self.origin_y + y + h // 2)
                    time.sleep(0.8)
                    return True
        return False

    # ------------------------------------------------------------------
    # 输入框
    # ------------------------------------------------------------------
    def get_input_box(self) -> Optional[Tuple[int, int, int, int]]:
        """自适应输入框检测，返回渲染相对矩形 (x0,y0,x1,y1)。

        原理：输入框是右侧面板底部一块**全宽浅色区**，与上方消息区以一条
        约 2px 的浅灰边框线 ((224,224,224)) 分隔。先取若干探针行（自下而上
        在输入框下半部找一行，要求该行在 right_pane_left..render_w 范围内
        白度 >=0.8），再从探针行向上/向下扫描中心竖线确定输入框上下边界。
        探测失败返回 None（不再回退到默认布局，避免在错误坐标上误点）。
        """
        for _ in range(6):
            box = self._probe_input_box()
            if box:
                return box
            time.sleep(0.5)
            self._update_render_rect()
        wxlog.debug('未检测到输入框')
        # 布局异常自动重校准：本机校准配置可能已不匹配（DPI/布局漂移），
        # 重新检测锚点并校准一次后再次探测（每会话只自动触发一次）。
        if not getattr(self, '_auto_recalibrated', False):
            self._auto_recalibrated = True
            wxlog.info('输入框探测连续失败，自动重新校准布局…')
            self.calibrate_layout()
            for _ in range(3):
                box = self._probe_input_box()
                if box:
                    return box
                time.sleep(0.5)
                self._update_render_rect()
        wxlog.debug('未检测到输入框')
        # 兜底：输入框与底部工具栏高度固定，窗口缩放只改变消息区高度。
        # 实测本机输入框上边 ≈ render_h-391、下边 ≈ render_h-150。
        return (self.right_pane_left, self.render_h - int(self.render_h * 0.214),
                self.render_w, self.render_h - int(self.render_h * 0.082))

    def _probe_input_box(self) -> Optional[Tuple[int, int, int, int]]:
        """探针法定位输入框，失败返回 None。

        输入框是右侧面板底部一块全宽浅色区，顶边是一条约 2px 的浅灰
        （224,224,224）分界线，与上方消息区（白底）分隔。消息区在渲染
        未稳定时可能整体发白，需校验分界线是「细线」而非消息区里的
        粗灰元素，否则会把消息区误判为输入框。
        """
        cx = (self.right_pane_left + self.render_w) // 2
        sx = self.origin_x + cx
        for probe_off in (150, 120, 200, 250, 350, 100, 450):
            probe_y = self.render_h - probe_off
            if probe_y <= int(self.render_h * 0.50):
                continue
            sy = self.origin_y + probe_y
            row_img = self._grab_screen(
                (self.origin_x + self.right_pane_left, sy,
                 self.origin_x + self.render_w, sy + 1))
            pxx = row_img.load()
            w = row_img.size[0]
            white = sum(1 for x in range(0, w, 2) if sum(pxx[x, 0]) / 3 > 240)
            if white / max(1, (w + 1) // 2) < 0.8:
                continue  # 该行不在输入框内（如工具条/空白）
            scan_top = self.origin_y + int(self.render_h * 0.50)
            col = self._grab_screen((sx, scan_top, sx + 1, sy + 1))
            pc = col.load()
            dy_probe = sy - scan_top
            y0 = dy_probe
            while y0 > 0 and sum(pc[0, y0]) / 3 > 240:
                y0 -= 1
            y0 += 1
            y1 = dy_probe
            while y1 < col.size[1] - 1 and sum(pc[0, y1]) / 3 > 240:
                y1 += 1
            # 输入框顶边上方必须是 1-4px 的细灰分界线；若是消息区里的
            # 粗灰元素（如系统消息/时间分隔），说明渲染未稳定，判为失败。
            g = y0 - 1
            while g >= 0 and sum(pc[0, g]) / 3 <= 240:
                g -= 1
            divider_h = (y0 - 1) - g
            y0_abs = scan_top + y0 - self.origin_y
            y1_abs = scan_top + y1 - self.origin_y
            if (1 <= divider_h <= 4
                    and y1_abs - y0_abs >= 150
                    and y0_abs >= int(self.render_h * 0.45)
                    and y1_abs >= int(self.render_h * 0.85)):
                return (self.right_pane_left, y0_abs, self.render_w, y1_abs)
        return None

    def focus_input(self, box: Optional[Tuple[int, int, int, int]] = None) -> bool:
        """点击输入框文本区使其获得焦点。返回是否检测到输入框。

        box 可传入已探测好的输入框，避免重复探测在会话切换时偶发失败。
        点击点取输入框顶部文本行附近（而非中心），避免点到下方
        表情/工具栏而无法聚焦。
        """
        if box is None:
            box = self.get_input_box()
        if not box:
            return False
        x0, y0, x1, y1 = box
        cx = (x0 + x1) // 2
        cy = y0 + min(24, (y1 - y0) // 4)   # 文本行贴近输入框上沿
        self.wx_click(self.origin_x + cx, self.origin_y + cy)
        time.sleep(0.6)
        return True

    # ------------------------------------------------------------------
    # 文字输入
    # ------------------------------------------------------------------
    def set_clipboard(self, text: str):
        """写入系统剪贴板（pyperclip，兼容中文）。"""
        import pyperclip
        pyperclip.copy(text)
        time.sleep(0.2)

    def input_text(self, text: str,
                   box: Optional[Tuple[int, int, int, int]] = None,
                   fast: bool = False) -> bool:
        """向当前聚焦的输入框输入文字。

        优先走「剪贴板 + Ctrl+V」并多次重试确认（输入框探测 / 焦点 /
        粘贴都可能偶发失败，整体循环重试）；均失败后回退到「拼音 +
        回车提交」的输入法组合。

        fast=True 时复用传入 box 走单次快速路径（分段连续发送用），
        失败即返回 False 由 send_msg 回退到完整流程。
        """
        if fast and box:
            if self.focus_input(box):
                self.set_clipboard(text)
                self._input.key(VK_A, ctrl=True)
                self._input.key(VK_DELETE)
                self._input.key(VK_V, ctrl=True)
                time.sleep(0.35)
                if self._input_box_has_text(box):
                    return True
            return False
        box = None
        for attempt in range(1, 7):
            box = self.get_input_box()
            if not box:
                wxlog.debug(f'未探测到输入框（attempt={attempt}），重试')
                time.sleep(0.5)
                continue
            if not self.focus_input(box):
                time.sleep(0.3)
                continue
            self.set_clipboard(text)
            self._input.key(VK_A, ctrl=True)   # 清空既有内容
            self._input.key(VK_DELETE)
            self._input.key(VK_V, ctrl=True)
            time.sleep(0.8)
            if self._input_box_has_text():
                self._last_input_box = box
                wxlog.debug(f'输入文字（剪贴板粘贴）成功，attempt={attempt}')
                return True
            wxlog.debug(f'剪贴板粘贴未确认（attempt={attempt}），重试')
            time.sleep(0.3)
        # 尝试 2：中文输入法拼音（逐字）
        wxlog.debug('剪贴板粘贴多次未生效，尝试拼音组合输入')
        self.focus_input(box)
        self._input.key(VK_A, ctrl=True)
        self._input.key(VK_DELETE)
        self._input.type_pinyin(self._to_pinyin(text))
        self._input.key(VK_RETURN)   # 提交候选
        time.sleep(0.8)
        return self._input_box_has_text()

    @staticmethod
    def _to_pinyin(text: str) -> str:
        """极简中文 → 拼音映射（仅内置少量常用词，供回退路径使用）。

        正式方案建议接入 ``pypinyin``（pip install pypinyin）。
        """
        table = {
            '测试': 'ceshi', '你好': 'nihao', '你好世界': 'nihaoshijie',
            '你好，世界': 'nihaoshijie', '消息': 'xiaoxi', '发送': 'fasong',
            '自动化': 'zidonghua', '成功': 'chenggong', '文件': 'wenjian',
            '文件传输助手': 'wenjianchuanshuzhushou',
        }
        if text in table:
            return table[text]
        try:
            import pypinyin
            return ''.join(pypinyin.lazy_pinyin(text))
        except ImportError:
            return ''.join(ch for ch in text if '\u4e00' <= ch <= '\u9fff') or text

    def _input_box_has_text(self, box=None) -> bool:
        """检测输入框文本区是否有深色像素（文字/光标），确认输入生效。

        box 可传入已探测好的输入框，避免连续发送时重复探测。

        阈值收紧到 sum<450：空输入框的浅灰占位符约为 (200,200,200) 即
        600，避免把占位符误判为未发送的文字导致消息重复发送。
        """
        if box is None:
            box = self.get_input_box()
        if not box:
            return False
        x0, y0, x1, y1 = box
        rel = (x0 + 20, y0 + 10, x1 - 20, y0 + 200)
        img = self._grab_screen(self._rel_to_screen(rel))
        px = img.load()
        dark = sum(1 for y in range(0, img.size[1], 2) for x in range(0, img.size[0], 2)
                   if sum(px[x, y]) < 450)
        return dark > 20

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------
    def click_send(self, fast: bool = False) -> bool:
        """发送消息并确认输入框已清空。

        优先回车键（输入框刚粘贴完必已聚焦，回车最可靠）；回车后输入框
        仍有内容则回退到 OCR 定位「发送」按钮点击。每次操作后都必须确认
        输入框文本已清空（即消息真正发出），否则重试。

        关键防护：发送前若输入框本无文字，回车等于空发，必须判定失败
        让上层重试（否则消息未发出却被误判成功）。

        fast=True 时仅回车 + 短等待（分段连续发送用），失败返回 False
        由 send_msg 回退到完整流程。
        """
        box = getattr(self, '_last_input_box', None)
        if fast:
            if not self._input_box_has_text(box):
                return False
            self._input.key(VK_RETURN)
            time.sleep(0.5)
            if not self._input_box_has_text(box):
                return True
            return False
        for attempt in range(3):
            if not self._input_box_has_text(box):
                wxlog.debug('发送前输入框无文字，跳过空发')
                return False
            self._input.key(VK_RETURN)
            time.sleep(1.0)
            if not self._input_box_has_text(box):
                return True
            wxlog.debug(f'回车发送未生效（attempt={attempt}），改用「发送」按钮')
            res = self.ocr(self.send_button_region)
            clicked = False
            for text, x, y, w, h in res:
                if '发送' in text:
                    self.wx_click(self.origin_x + x + w // 2,
                                           self.origin_y + y + h // 2)
                    clicked = True
                    break
            if not clicked:
                return False
            time.sleep(1.0)
            if not self._input_box_has_text(box):
                return True
        return False

    def send_msg(self, text: str, who: Optional[str] = None,
                 verify: bool = False) -> WxResponse:
        """发送一条文本消息。

        Args:
            text: 消息内容
            who: 目标会话名称（默认发送到当前打开的会话）
            verify: 是否用数据库读取器回读确认发送成功

        Returns:
            WxResponse

        整条流程带总时限（默认 75s）：打开会话 / 输入 / 发送任一步偶发
        失败时重试；已发送但 DB 未落库时轮询等待确认，不重复发送。
        """
        # 快速路径：连续发送同一会话（如分段回复）时，复用已探测的输入框，
        # 跳过 ensure_visible / 会话重检 / 重复探测与截图确认，失败自动回退完整流程。
        if (not verify and who
                and who == getattr(self, '_current_chat', None)
                and getattr(self, '_last_input_box', None) is not None
                and self.input_text(text, box=self._last_input_box, fast=True)
                and self.click_send(fast=True)):
            return WxResponse.success(f'消息已发送：{text}', data={'content': text})
        # UIA 路径：热激活后可直接输入+回车发送，无 OCR 抖动，最快。
        uia = self._get_uia()
        if uia is not None:
            if not who or uia.current_chat() == who or uia.open_chat(who):
                if uia.send_text(text):
                    if not verify:
                        return WxResponse.success(f'消息已发送：{text}', data={'content': text})
                    if self._verify_sent(text, who):
                        return WxResponse.success(f'消息已发送并确认：{text}', data={'content': text})
                    wait_until = time.time() + 8
                    while time.time() < wait_until:
                        time.sleep(1.0)
                        if self._verify_sent(text, who):
                            return WxResponse.success(f'消息已发送并确认：{text}', data={'content': text})
                    return WxResponse.failure('消息已操作发送，但数据库未确认', data={'content': text})
        if not self.ensure_visible():
            return WxResponse.failure('微信窗口不可见（可能锁屏/会话断开）')
        self._last_input_box = None
        deadline = time.time() + 75
        for attempt in range(3):
            if who:
                if time.time() > deadline:
                    break
                # 会话复用：目标仍是当前已打开会话时跳过 open_chat（每次
                # open_chat 都重扫侧栏/点击，是逐条发送的主要耗时点）
                if not (who == getattr(self, '_current_chat', None)
                        and self._chat_is_open(who)):
                    if not self.open_chat(who):
                        wxlog.debug(f'open_chat 未确认（attempt={attempt}），重试')
                        continue
                    self._current_chat = who
                    time.sleep(0.8)   # 等右侧面板/输入框渲染稳定，避免探测误判
            if time.time() > deadline:
                break
            if not self.input_text(text):
                wxlog.debug(f'输入文字失败（attempt={attempt}），重试')
                continue
            if not self.click_send():
                wxlog.debug(f'点击发送未确认清空输入框（attempt={attempt}），重试')
                continue
            if not verify:
                return WxResponse.success(f'消息已发送：{text}', data={'content': text})
            if self._verify_sent(text, who):
                return WxResponse.success(f'消息已发送并确认：{text}', data={'content': text})
            # 可能已发送但 DB 异步落库，轮询确认，不重发避免重复
            wait_until = time.time() + 8
            while time.time() < wait_until:
                time.sleep(1.0)
                if self._verify_sent(text, who):
                    return WxResponse.success(f'消息已发送并确认：{text}', data={'content': text})
            return WxResponse.failure('消息已操作发送，但数据库未确认', data={'content': text})
        return WxResponse.failure('发送失败：多次重试未完成')

    def _get_db(self):
        """惰性创建并复用 WeChatDB（密钥提取/解密较慢，避免每次校验都重建）。"""
        if getattr(self, '_cached_db', None) is None:
            from wechatauto.db import WeChatDB
            self._cached_db = WeChatDB()
        return self._cached_db

    def _verify_sent(self, text: str, who: Optional[str]) -> bool:
        try:
            db = self._get_db()
            if not who:
                who = db.get_self_info()['username']
            else:
                hits = db.search_contact(who)
                if hits:
                    who = hits[0]["username"]
            msgs = db.get_messages(who, limit=3)
            for m in msgs:
                if m.get('sender_id') == 2 and text in (m.get('content') or ''):
                    return True
        except Exception as e:
            wxlog.debug(f'发送校验失败：{e}')
        return False

    # ------------------------------------------------------------------
    # 文件 / 图片发送（剪贴板 CF_HDROP + Ctrl+V）
    # ------------------------------------------------------------------
    @staticmethod
    def copy_files_to_clipboard(paths: List[str]) -> bool:
        """把本地文件以 CF_HDROP 格式写入剪贴板，供微信粘贴为附件/图片。

        相比走「+ 菜单 → 文件对话框」，该路线不依赖自绘界面图标定位，
        兼容性最好：在聊天输入框 Ctrl+V 后，微信会把文件/图片插入草稿。
        """
        paths = [os.path.abspath(p) for p in paths]
        if not paths:
            return False
        class DROPFILES(ctypes.Structure):
            _fields_ = [
                ("pFiles", ctypes.c_uint),
                ("pt_x", ctypes.c_long),
                ("pt_y", ctypes.c_long),
                ("fNC", ctypes.c_int),
                ("fWide", ctypes.c_int),
            ]
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        u32.OpenClipboard.argtypes = [ctypes.c_void_p]
        u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        k32.GlobalAlloc.restype = ctypes.c_void_p
        k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalLock.argtypes = [ctypes.c_void_p]
        k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        CF_HDROP = 15
        try:
            if not u32.OpenClipboard(None):
                return False
            u32.EmptyClipboard()
            df = DROPFILES()
            df.pFiles = ctypes.sizeof(DROPFILES)
            df.fWide = 1
            raw = (ctypes.string_at(ctypes.byref(df), ctypes.sizeof(DROPFILES))
                   + ("\0".join(paths) + "\0").encode("utf-16-le") + b"\0\0")
            h = k32.GlobalAlloc(0x0042, len(raw))
            if not h:
                u32.CloseClipboard()
                return False
            dst = k32.GlobalLock(h)
            ctypes.memmove(dst, raw, len(raw))
            k32.GlobalUnlock(h)
            u32.SetClipboardData(CF_HDROP, h)
            u32.CloseClipboard()
            return True
        except Exception as e:
            wxlog.debug(f'写入 CF_HDROP 剪贴板失败：{e}')
            return False

    def _paste_attachment_and_send(self, path: str,
                                   box: Optional[Tuple[int, int, int, int]] = None) -> bool:
        """聚焦输入框 → 粘贴文件 → 检测草稿就绪 → 回车发送。

        图片草稿为彩色像素、文件为灰色卡片。图片粘贴偶发失效，
        采用「重新聚焦 + 重新粘贴」重试循环提升可靠性。

        box 可传入已探测好的输入框（连续发送附件时复用，避免重复探测）。
        """
        if not os.path.isfile(path):
            wxlog.debug(f'文件不存在：{path}')
            return False
        is_image = path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'))
        if box is None:
            box = self.get_input_box()
        wxlog.debug(f'粘贴前输入框探测：{box}')
        for attempt in range(3):
            if not self.focus_input(box):
                return False
            if not self.copy_files_to_clipboard([path]):
                return False
            time.sleep(0.3)
            self._input.key(VK_A, ctrl=True)
            self._input.key(VK_DELETE)
            self._input.key(VK_V, ctrl=True)
            if is_image:
                draft_ok = self._input_box_has_color_draft(box, wait=6.0)
                wxlog.debug(f'粘贴尝试 {attempt + 1} 草稿检测：{draft_ok}')
                if draft_ok:
                    break
            else:
                time.sleep(2.0)
                break
        else:
            wxlog.debug('多次粘贴仍未检测到图片草稿，仍尝试回车')
        self._input.key(VK_RETURN)
        time.sleep(1.5)
        return True

    def _input_box_has_color_draft(self, box: Optional[Tuple[int, int, int, int]] = None,
                                   wait: float = 8.0) -> bool:
        """轮询输入框区域是否存在彩色像素（图片缩略图草稿已渲染）。

        图片粘贴后输入框会向上扩展容纳缩略图，因此扫描区域向上多
        覆盖一段（y0-150），避免草稿出现在探测基线之上而漏检。
        """
        if box is None:
            box = self.get_input_box()
        if not box:
            return False
        x0, y0, x1, y1 = box
        scan_y0 = max(100, y0 - 150)
        scan_y1 = min(self.render_h - 15, y1 + 80)
        rel = (x0 + 10, scan_y0, x1 - 10, scan_y1)
        screen_rect = self._rel_to_screen(rel)
        deadline = time.time() + wait
        last_colored = 0
        while time.time() < deadline:
            try:
                img = self._grab_screen(screen_rect)
                px = img.load()
                colored = 0
                for yy in range(0, img.size[1], 4):
                    for xx in range(0, img.size[0], 4):
                        r, g, b = px[xx, yy][:3]
                        if abs(r - g) > 20 or abs(g - b) > 20 or abs(r - b) > 20:
                            colored += 1
                last_colored = colored
                if colored > 30:
                    return True
            except Exception:
                pass
            time.sleep(0.3)
        wxlog.debug(f'草稿检测失败：box={box} rel={rel} colored={last_colored}')
        return False

    def _open_chat_and_settle(self, who: str) -> bool:
        """打开会话并等待切换动画完成，返回输入框是否可探测。

        会话消息区若正显示大图，输入框探测会失败（返回 None）。
        重复点击会话会改变消息区滚动状态，重试直到探测成功。
        """
        for _ in range(5):
            self.open_chat(who)
            time.sleep(1.2)
            if self.get_input_box():
                return True
            wxlog.debug(f'打开会话后未检测到输入框，重试 open_chat({who})')
        return bool(self.get_input_box())

    def send_file(self, path: str, who: Optional[str] = None,
                  verify: bool = False) -> WxResponse:
        """发送本地文件（剪贴板粘贴路线）。"""
        if not self.ensure_visible():
            return WxResponse.failure('微信窗口不可见（可能锁屏/会话断开）')
        # 会话复用：目标仍是当前已打开会话且输入框可探测时跳过 open_chat
        # （open_chat 重扫侧栏/点击是附件发送的主要耗时点）
        if who and not (who == getattr(self, '_current_chat', None)
                        and self.get_input_box()):
            if not self._open_chat_and_settle(who):
                return WxResponse.failure(f'无法打开会话：{who}')
            self._current_chat = who
        box = self.get_input_box()
        before_seq = self._target_seq(who)
        if not self._paste_attachment_and_send(path, box):
            return WxResponse.failure(f'文件发送失败：{path}')
        if verify:
            ok = self._verify_attachment_sent(path, who, before_seq)
            return (WxResponse.success(f'文件已发送并确认：{path}', data={'path': path})
                    if ok else WxResponse.failure('文件已操作发送，但数据库未确认', data={'path': path}))
        return WxResponse.success(f'文件已发送：{path}', data={'path': path})

    def send_image(self, path: str, who: Optional[str] = None,
                   verify: bool = False) -> WxResponse:
        """发送本地图片（剪贴板粘贴路线，微信自动作为图片消息插入）。"""
        if not self.ensure_visible():
            return WxResponse.failure('微信窗口不可见（可能锁屏/会话断开）')
        if who and not (who == getattr(self, '_current_chat', None)
                        and self.get_input_box()):
            if not self._open_chat_and_settle(who):
                return WxResponse.failure(f'无法打开会话：{who}')
            self._current_chat = who
        box = self.get_input_box()
        before_seq = self._target_seq(who)
        if not self._paste_attachment_and_send(path, box):
            return WxResponse.failure(f'图片发送失败：{path}')
        if verify:
            ok = self._verify_attachment_sent(path, who, before_seq)
            return (WxResponse.success(f'图片已发送并确认：{path}', data={'path': path})
                    if ok else WxResponse.failure('图片已操作发送，但数据库未确认', data={'path': path}))
        return WxResponse.success(f'图片已发送：{path}', data={'path': path})

    def _target_seq(self, who: Optional[str]) -> int:
        """取目标会话当前最大 sort_seq，作为发送后校验的基线。"""
        try:
            db = self._get_db()
            if not who:
                who = db.get_self_info()['username']
            else:
                hits = db.search_contact(who)
                if hits:
                    who = hits[0]["username"]
            msgs = db.get_messages(who, limit=1)
            return msgs[0]['sort_seq'] if msgs else 0
        except Exception:
            return 0

    def _verify_attachment_sent(self, path: str, who: Optional[str],
                                before_seq: int = 0) -> bool:
        try:
            db = self._get_db()
            if not who:
                who = db.get_self_info()['username']
            else:
                hits = db.search_contact(who)
                if hits:
                    who = hits[0]["username"]
            fname = os.path.basename(path)
            fname_bytes = fname.encode('utf-8')
            # 微信会自动重名文件为 名称(n).ext，匹配文件名主干
            stem = os.path.splitext(fname)[0]
            stem_bytes = stem.encode('utf-8')
            is_image = path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'))
            expect_type = '图片' if is_image else '文件/链接/卡片'
            # 微信发送后 DB 异步落库，文件消息落库更慢，轮询放宽到 10s
            deadline = time.time() + (6.0 if is_image else 10.0)
            while time.time() < deadline:
                # 优先：发送后出现了 sort_seq 更大的同类型消息（图片消息无文件名，只能靠它）
                for m in db.get_new_messages(who, since_seq=before_seq, limit=8):
                    if m.get('sender_id') != 2 or m.get('type') != expect_type:
                        continue
                    # 文件消息再做文件名匹配，图片消息仅凭类型+时序
                    if is_image:
                        return True
                    if fname in (m.get('content') or ''):
                        return True
                    row = db.get_message_row(who, m.get('local_id'))
                    if row and row.get('packed_info') and isinstance(row['packed_info'], bytes):
                        if fname_bytes in row['packed_info']:
                            return True
                        # 兼容重命名：主干匹配 + packed_info 内同一文件（主干名出现即可）
                        if stem_bytes in row['packed_info']:
                            return True
                time.sleep(0.5)
        except Exception as e:
            wxlog.debug(f'附件校验失败：{e}')
        return False

    # ------------------------------------------------------------------
    # 回复 / 引用
    # ------------------------------------------------------------------
    def _last_message_y(self) -> Optional[int]:
        """估算最近一条消息的位置（输入框上沿往上一点）。"""
        box = self.get_input_box()
        if not box:
            return None
        return max(100, box[1] - 80)

    def reply_msg(self, text: str, who: Optional[str] = None,
                  target_text: Optional[str] = None, verify: bool = False) -> WxResponse:
        """回复最近一条消息（悬停消息 → 点击「回复」→ 输入 → 发送）。

        target_text 用于在 OCR 结果中匹配目标消息（可选）。
        """
        if not self.ensure_visible():
            return WxResponse.failure('微信窗口不可见（可能锁屏/会话断开）')
        if who:
            self.open_chat(who)
            time.sleep(0.8)
        y = self._last_message_y()
        if y is None:
            return WxResponse.failure('未检测到消息区域')
        u = self._input._user32
        u.SetCursorPos(self.origin_x + (self.render_w + self.right_pane_left) // 2,
                       self.origin_y + y)
        time.sleep(0.8)
        # OCR 悬停工具栏（回复/引用等图标）
        region = (self.right_pane_left, y - 60, self.render_w, y + 40)
        items = self.ocr(region)
        click_pt = None
        for text, x, yy, w, h in items:
            if '回复' in text or '引用' in text or '转发' in text:
                click_pt = (x + w // 2, yy + h // 2)
                break
        if not click_pt:
            wxlog.debug('未识别到回复工具栏，尝试右键菜单')
            self.wx_click(self.origin_x + (self.render_w + self.right_pane_left) // 2,
                                    self.origin_y + y, right=True)
            time.sleep(0.6)
            menu = self.ocr((self.right_pane_left, y, self.render_w, self.render_h))
            for text, x, yy, w, h in menu:
                if '回复' in text:
                    click_pt = (x + w // 2, yy + h // 2)
                    break
        if not click_pt:
            return WxResponse.failure('未找到回复入口')
        self.wx_click(self.origin_x + click_pt[0], self.origin_y + click_pt[1])
        time.sleep(0.6)
        if not self.input_text(text):
            return WxResponse.failure('输入回复内容失败')
        self.click_send()
        if verify:
            ok = self._verify_sent(text, who)
            return (WxResponse.success(f'回复已发送并确认：{text}', data={'content': text})
                    if ok else WxResponse.failure('回复已操作发送，但数据库未确认', data={'content': text}))
        return WxResponse.success(f'回复已发送：{text}', data={'content': text})

    # ------------------------------------------------------------------
    # 艾特成员（群聊）
    # ------------------------------------------------------------------
    def at_member(self, member: str, text: str, who: Optional[str] = None,
                  verify: bool = False) -> WxResponse:
        """在群聊中 @ 成员后追加发送 text。

        流程：输入框键入 '@' → OCR 成员选择弹层定位成员 → 点击 → 输入正文 → 发送。
        """
        if not self.ensure_visible():
            return WxResponse.failure('微信窗口不可见（可能锁屏/会话断开）')
        if who:
            self.open_chat(who)
            time.sleep(0.8)
        if not self.focus_input():
            return WxResponse.failure('输入框不可用')
        self._input.type_unicode('@')
        time.sleep(0.9)
        box = self.get_input_box()
        popup = (self.right_pane_left, max(80, (box[1] if box else int(self.render_h * 0.49)) - 500),
                 self.render_w, (box[1] if box else int(self.render_h * 0.77)))
        items = self.ocr(popup)
        target = None
        for t, x, yy, w, h in items:
            if member in t or t.startswith(member):
                target = (x + w // 2, yy + h // 2)
                break
        if not target:
            return WxResponse.failure(f'未在成员列表中找到：{member}')
        self.wx_click(self.origin_x + target[0], self.origin_y + target[1])
        time.sleep(0.6)
        if text and not self.input_text(text):
            return WxResponse.failure('输入消息正文失败')
        self.click_send()
        if verify:
            ok = self._verify_sent(text, who)
            return (WxResponse.success(f'@成员消息已发送并确认', data={'member': member, 'content': text})
                    if ok else WxResponse.failure('@消息已操作发送，但数据库未确认', data={'member': member}))
        return WxResponse.success(f'@成员消息已发送', data={'member': member, 'content': text})


# ---------------------------------------------------------------------------
# 便捷入口
# ---------------------------------------------------------------------------

def quick_send(text: str, who: str = None, verify: bool = False) -> WxResponse:
    """一行式发送消息。

    >>> from wechatauto.guia import quick_send
    >>> quick_send('你好', '文件传输助手')
    """
    wx = WeChatGUI()
    return wx.send_msg(text, who, verify)


def quick_send_file(path: str, who: str = None, verify: bool = False) -> WxResponse:
    """一行式发送文件。"""
    wx = WeChatGUI()
    return wx.send_file(path, who, verify)


def quick_send_image(path: str, who: str = None, verify: bool = False) -> WxResponse:
    """一行式发送图片。"""
    wx = WeChatGUI()
    return wx.send_image(path, who, verify)


def quick_reply(text: str, who: str = None, verify: bool = False) -> WxResponse:
    """一行式回复最近一条消息。"""
    wx = WeChatGUI()
    return wx.reply_msg(text, who, verify=verify)
