# -*- coding: utf-8 -*-
"""自定义表情包发送引擎（纯 UI 自动化，库无 send_sticker API）。

流程：确保目标会话已打开 -> 点输入栏「发送表情」按钮 -> 切到「自定义表情」tab ->
PrintWindow 抓面板位图（这个 Qt 弹窗 ImageGrab 抓不到，只会截到聊天区）->
模板匹配定位目标缩略图 -> 向弹窗 PostMessage 点格子直接发送（不移动真实鼠标）。

只发用户账号里收藏的 用户自备贴纸包，不用微信内置系统表情。贴纸 26 张、5 列网格，
一屏只完整显示 20 个：先滚到顶认前 20 张，认不到再滚到底认最后几行。
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from json_tables import read_table

BASE = Path(__file__).resolve().parent
STICKERS_DIR = BASE / "stickers"
RAW_DIR = STICKERS_DIR / "raw2"
REG_PATH = STICKERS_DIR / "stickers.json"

_POPOVER_AID = "EmoticonPopover"
_POPUP_CLS = "Qt51514QWindowToolSaveBits"
_CUSTOM_TAB = "自定义表情"
_BTN_SUBSTR = "表情"
_CELL_CLS = "mmui::FavEmoticonItemView"
_TOOLBAR_AID_SUFFIX = "emoticon_panel_tool_bar"


_SCROLL_NOTCHES = 3

_GDI = None
_uia = None


LAST_ERROR = ""


def load_registry() -> dict:
    return read_table(REG_PATH)


def available_labels() -> list:
    reg = load_registry()
    return list((reg.get("labels") or {}).keys())


def _thumbs_for(label: str) -> list:
    reg = load_registry()
    files = (reg.get("labels") or {}).get(label) or []
    return [str(RAW_DIR / f) for f in files]


def get_uia(timeout: float = 20.0):
    """懒加载一个 WeChatUIA 实例并缓存（避免每次发送都重建）。"""
    global _uia
    if _uia is None:
        from wechatauto.uia_driver import WeChatUIA
        _uia = WeChatUIA(timeout=timeout)
    return _uia


def _match(panel_bgr, thumb_bgr):
    """在面板截图里找缩略图，返回 (score, (cx, cy))，坐标相对面板截图左上角。"""
    import cv2
    res = cv2.matchTemplate(panel_bgr, thumb_bgr, cv2.TM_CCOEFF_NORMED)
    _mn, mx, _mnl, mxl = cv2.minMaxLoc(res)
    h, w = thumb_bgr.shape[:2]
    return mx, (mxl[0] + w // 2, mxl[1] + h // 2)


def _find_popover():
    import uiautomation as auto
    root = auto.GetRootControl()
    for k, _d in auto.WalkControl(root, maxDepth=6):
        try:
            if k.ControlTypeName == "WindowControl" and (k.AutomationId or "") == _POPOVER_AID:
                return k
        except Exception:
            continue
    return None


def _cells(pop):
    import uiautomation as auto
    out = []
    for k, _d in auto.WalkControl(pop, maxDepth=10):
        try:
            if (k.ClassName or "") == _CELL_CLS:
                r = k.BoundingRectangle
                if r.width() > 60 and r.height() > 60:
                    out.append(k)
        except Exception:
            continue
    return out


def _gdi():
    """初始化 PrintWindow / GetDIBits 的句柄类型。

    64 位下不声明 argtypes 会按 32 位 int 传句柄，直接 OverflowError。
    """
    global _GDI
    if _GDI is not None:
        return _GDI
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    g = ctypes.windll.gdi32
    u.GetWindowDC.restype = wintypes.HDC
    u.GetWindowDC.argtypes = [wintypes.HWND]
    u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    u.PrintWindow.restype = wintypes.BOOL
    u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    g.CreateCompatibleDC.restype = wintypes.HDC
    g.CreateCompatibleDC.argtypes = [wintypes.HDC]
    g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    g.SelectObject.restype = wintypes.HGDIOBJ
    g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteDC.argtypes = [wintypes.HDC]
    g.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                            wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                            wintypes.UINT]
    g.GetDIBits.restype = ctypes.c_int
    _GDI = (u, g)
    return _GDI


def _popup_hwnd(pop):
    """取表情弹窗的顶层 HWND：先问控件，再按窗口类 + 尺寸兜底枚举。"""
    try:
        h = int(pop.NativeWindowHandle or 0)
        if h:
            return h
    except Exception:
        pass
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    try:
        pid = int(pop.ProcessId)
    except Exception:
        return 0
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _l):
        p = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid:
            return True
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, 256)
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        if (cls.value == _POPUP_CLS and u.IsWindowVisible(hwnd)
                and r.right - r.left > 300 and r.bottom - r.top > 300):
            found.append(hwnd)
        return True

    u.EnumWindows(cb, 0)
    return found[0] if found else 0


def _printwindow(hwnd, rect):
    """PrintWindow 抓窗口位图，返回 (BGR ndarray 或 None, 是否成功)。"""
    import ctypes
    import numpy as np

    u, g = _gdi()
    left, top, right, bottom = rect
    w, h = int(right - left), int(bottom - top)
    if w <= 0 or h <= 0:
        return None, False

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    hdc = u.GetWindowDC(hwnd)
    if not hdc:
        return None, False
    mdc = bmp = previous = None
    try:
        mdc = g.CreateCompatibleDC(hdc)
        if not mdc:
            return None, False
        bmp = g.CreateCompatibleBitmap(hdc, w, h)
        if not bmp:
            return None, False
        previous = g.SelectObject(mdc, bmp)

        ok = u.PrintWindow(hwnd, mdc, 2) or u.PrintWindow(hwnd, mdc, 0)
        bi = BIH()
        bi.biSize = ctypes.sizeof(BIH)
        bi.biWidth = w
        bi.biHeight = -h
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        g.SelectObject(mdc, previous)
        if not g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0):
            return None, False
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
        return arr, bool(ok)
    finally:
        if mdc and previous:
            g.SelectObject(mdc, previous)
        if bmp:
            g.DeleteObject(bmp)
        if mdc:
            g.DeleteDC(mdc)
        u.ReleaseDC(hwnd, hdc)


def _grab(pop):
    """抓面板内容：PrintWindow 优先（ImageGrab 看不见这个弹窗），失败才退回截屏。"""
    import cv2
    import numpy as np
    r = pop.BoundingRectangle
    rect = (r.left, r.top, r.right, r.bottom)
    hwnd = _popup_hwnd(pop)
    if hwnd:
        arr, _ok = _printwindow(hwnd, rect)
        if arr is not None and float(arr.mean()) > 5:
            return arr, (rect[0], rect[1])
    from PIL import ImageGrab
    img = ImageGrab.grab(bbox=rect).convert("RGB")
    bgr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    return bgr, (rect[0], rect[1])


def _post_click(hwnd, rect, sx, sy) -> None:
    """向弹窗投递一次鼠标点击（相对坐标），不动真实鼠标、不抢焦点。"""
    import ctypes
    u = ctypes.windll.user32
    rx = int(sx - rect[0]) & 0xFFFF
    ry = int(sy - rect[1]) & 0xFFFF
    lp = (ry << 16) | rx
    u.PostMessageW(hwnd, 0x0200, 0, lp)
    time.sleep(0.05)
    u.PostMessageW(hwnd, 0x0201, 1, lp)
    time.sleep(0.06)
    u.PostMessageW(hwnd, 0x0202, 0, lp)


def _wheel(hwnd, rect, delta: int, times: int) -> None:
    """向弹窗投递滚轮事件（lParam 要屏幕坐标，Qt 才认）。"""
    import ctypes
    u = ctypes.windll.user32
    sx = (rect[0] + 260) & 0xFFFF
    sy = (rect[1] + 200) & 0xFFFF
    lp = (sy << 16) | sx
    for _ in range(max(0, int(times))):
        u.PostMessageW(hwnd, 0x020A, (int(delta) << 16) & 0xFFFFFFFF, lp)
        time.sleep(0.12)


def _cell_at(pop, x: int, y: int):
    for cell in _cells(pop):
        try:
            r = cell.BoundingRectangle
        except Exception:
            continue
        if r.left <= x <= r.right and r.top <= y <= r.bottom:
            return cell
    return None


def _wait_popover(timeout: float = 3.0):
    """轮询等表情面板出现（固定 sleep 要么慢、要么偶发抢跑）。"""
    end = time.time() + max(0.5, timeout)
    while time.time() < end:
        pop = _find_popover()
        if pop is not None:
            return pop
        time.sleep(0.15)
    return None


def _click_tab(pop, hwnd, rect) -> bool:
    """切到「自定义表情」tab。面板会记住上次位置，所以每发一次都点一下保险。"""
    import uiautomation as auto
    tab = None
    for k, _d in auto.WalkControl(pop, maxDepth=8):
        try:
            if k.ControlTypeName == "TabItemControl" and _CUSTOM_TAB in (k.Name or ""):
                tab = k
                break
        except Exception:
            continue
    if tab is None:
        return False
    if _cells(pop):
        return True
    tr = tab.BoundingRectangle
    _post_click(hwnd, rect, (tr.left + tr.right) // 2, (tr.top + tr.bottom) // 2)
    end = time.time() + 2.0
    while time.time() < end:
        time.sleep(0.15)
        if _cells(pop):
            return True
    return False


def _click_match(uia, hwnd, pop, rect, thumbs, threshold) -> bool:
    """当前面板位置做模板匹配，命中就点它的格子（点一张即发）。

    返回 True 表示已经点过（不管面板有没有关，都不再点第二张，避免连发）；
    False 表示这一屏没认出来，可以滚动后再试。
    """
    bgr, (offx, offy) = _grab(pop)
    for t in thumbs:
        score, (cx, cy) = _match(bgr, t)
        if score < threshold:
            continue
        sx, sy = offx + cx, offy + cy
        cell = _cell_at(pop, sx, sy)
        if cell is None:
            continue
        time.sleep(random.uniform(0.2, 0.5))
        _post_click(hwnd, rect, (cell.BoundingRectangle.left
                                 + cell.BoundingRectangle.right) // 2,
                    (cell.BoundingRectangle.top
                     + cell.BoundingRectangle.bottom) // 2)

        end = time.time() + 1.2
        while time.time() < end:
            time.sleep(0.1)
            if _find_popover() is None:
                return True

        try:
            if uia is not None:
                uia._click_ctrl(cell)
                time.sleep(1.0)
        except Exception:
            pass
        return True
    return False


def _same_chat(a, b) -> bool:
    if not a or not b:
        return False
    a = " ".join(str(a).split())
    b = " ".join(str(b).split())
    return a == b or a in b or b in a


def _ensure_chat(uia, who: str, who_id: str = "") -> bool:
    """切到目标会话并确认真的切过去了，避免贴纸发到别的群。

    坑：群的 ``current_chat()`` 不稳定——刚切过去时给显示名（群名），
    过一会儿又变成 wxid（``xxxx@chatroom``）。只比群名的话切换永远判失败，
    结果一张贴纸都发不出去。所以显示名和 wxid 任意一个命中都算切到了。
    """
    def hit(cur) -> bool:
        for cand in (who, who_id):
            if cand and _same_chat(cur, cand):
                return True
        return False

    try:
        cur = uia.current_chat()
    except Exception:
        cur = None
    if hit(cur):
        return True
    try:
        ok = uia.open_chat(who)
    except Exception:
        ok = False
    if not ok:
        return False

    for i, delay in enumerate((0.2, 0.4, 0.6)):
        time.sleep(delay)
        try:
            cur = uia.current_chat()
        except Exception:
            cur = None
        if hit(cur):
            return True
    return False


def send_sticker(label: str, threshold: float = None, uia=None,
                 specific: str = None, who: str = None,
                 who_id: str = None) -> bool:
    """发一个指定标签的贴纸。返回是否发送成功。

    who: 目标会话显示名；who_id: 该会话的 username（群是 xxxx@chatroom）。
         给了就先切过去并校验，避免贴纸落到别的群。
    specific: 指定某张缩略图文件（文件名如 '0012.png'），用于校准/精确发送；
              为 None 时同标签多张（如「压力」×2）随机挑一张。
    """
    global LAST_ERROR
    LAST_ERROR = ""
    import cv2
    import uiautomation as auto

    reg = load_registry()
    threshold = float(threshold if threshold is not None else reg.get("threshold", 0.88))
    files = _thumbs_for(label)
    if specific:
        wanted = str(RAW_DIR / specific)
        files = [wanted] if wanted in files else files
    thumbs = [cv2.imread(p, cv2.IMREAD_COLOR) for p in files]
    thumbs = [t for t in thumbs if t is not None]
    if not thumbs:
        return False
    if not specific:
        random.shuffle(thumbs)

    uia = uia or get_uia()
    uia.ensure_window()

    if who and not _ensure_chat(uia, who, who_id or ""):
        LAST_ERROR = "会话没切过去：%s" % who
        return False


    win = uia._win
    btn = None
    for k, _d in auto.WalkControl(win, maxDepth=20):
        try:
            if k.ControlTypeName == "ButtonControl" and _BTN_SUBSTR in (k.Name or ""):
                btn = k
                break
        except Exception:
            continue
    if btn is None:
        LAST_ERROR = "找不到「发送表情」按钮"
        return False
    uia._click_ctrl(btn)
    pop = _wait_popover(3.0)
    if pop is None:
        LAST_ERROR = "表情面板没弹出来"
        return False
    hwnd = _popup_hwnd(pop)
    if not hwnd:
        LAST_ERROR = "表情面板取不到窗口句柄"
        return False
    r = pop.BoundingRectangle
    rect = (r.left, r.top, r.right, r.bottom)

    if not _click_tab(pop, hwnd, rect):

        LAST_ERROR = "切不到「自定义表情」tab"
        return False


    time.sleep(0.2)
    if _click_match(uia, hwnd, pop, rect, thumbs, threshold):
        return True


    _wheel(hwnd, rect, 120, _SCROLL_NOTCHES)
    time.sleep(0.5)
    if _click_match(uia, hwnd, pop, rect, thumbs, threshold):
        return True


    _wheel(hwnd, rect, -120, _SCROLL_NOTCHES)
    time.sleep(0.8)
    if _click_match(uia, hwnd, pop, rect, thumbs, threshold):
        return True
    LAST_ERROR = "面板里没找到「%s」的缩略图（阈值 %.2f）" % (label, threshold)
    return False


def close_panel(uia=None) -> None:
    """Esc 收起表情面板（面板在点击贴纸后通常已自动关闭）。"""
    pop = _find_popover()
    if pop is None:
        return
    try:
        import ctypes
        u = ctypes.windll.user32
        u.keybd_event(0x1B, 0, 0, 0)
        u.keybd_event(0x1B, 0, 2, 0)
        time.sleep(0.3)
    except Exception:
        pass
