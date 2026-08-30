"""朋友圈（Moments）相关接口实现。

本模块提供两类接口：

- :class:`Moment`（UIA 控件路线）：通过热激活 Qt accessibility gate
  （``WeChatUIA``）获得 ``mmui`` UIA 树后，可在界面上点击导航栏“朋友圈”
  进入朋友圈，并按 UIA 控件完成**点赞（Like）与评论（Comment）**。需要
  微信主窗口已登录；UIA 树不可用时相关接口返回失败/None。
- :class:`MomentDB`（数据库路线，4.x 推荐）：直接读取微信本地
  ``sns.db`` 的 ``SnsTimeLine`` 表，内容为 ``SnsDataItem`` XML，
  可稳定获取全部朋友圈（含正文、图片/视频 md5、点赞、评论、定位）。
  该路线用于**只读**读取；点赞/评论属于服务端行为，仅能通过 UIA 控件完成。

注：**发朋友圈（PublishMoments）功能已舍弃**——4.x 的发表为自绘
界面操作，无法可靠自动化；本模块仅保留朋友圈读取/点赞/评论能力。
"""

from __future__ import annotations

import html
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Union

from wechatauto import uia
from wechatauto.languages import MOMENTS, get_lang
from wechatauto.logger import wxlog
from wechatauto.param import WxParam, WxResponse
from wechatauto.ui.base import BaseUISubWnd
from wechatauto.utils.tools import find_all_windows_from_root
from wechatauto.utils.win32 import SetClipboardText


def _asset_path(name: str) -> Optional[str]:
    """返回 wechatauto/assets 下静态资源（如朋友圈按钮模板）的绝对路径。

    兼容两种安装形态：源码目录（与 wechatauto 包同层）与已安装到
    site-packages 的包内资源。
    """
    candidates = [
        os.path.join(os.path.dirname(__file__), 'assets', name),
        os.path.join(os.path.dirname(__file__), '..', 'assets', name),
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    try:
        import importlib.resources
        pkg = importlib.resources.files('wechatauto.assets')
        if pkg is not None:
            return str(pkg.joinpath(name))
    except Exception:
        pass
    return None


def _lang(table, key: str) -> str:
    """根据当前语言环境返回对应文案。"""
    return get_lang(table, key)


def _send_scroll(x: int, y: int, delta: int = -120, times: int = 1) -> None:
    """在屏幕坐标 (x, y) 处向该窗口发送滚轮事件（模拟用户滚动）。

    Args:
        x, y: 目标屏幕坐标（需落在朋友圈时间线区域）。
        delta: 滚轮增量，负=向下滚动(看更早)，正=向上滚动(看最新)。
        times: 重复次数。
    """
    import ctypes

    INPUT_MOUSE = 0
    MOVE = 0x0001
    WHEEL = 0x0800
    ABS = 0x8000

    class MI(ctypes.Structure):
        _fields_ = [("dx", ctypes.wintypes.DWORD), ("dy", ctypes.wintypes.DWORD),
                    ("mouseData", ctypes.wintypes.DWORD), ("dwFlags", ctypes.wintypes.DWORD),
                    ("time", ctypes.wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]

    class I(ctypes.Structure):
        _fields_ = [("type", ctypes.wintypes.DWORD), ("mi", MI)]

    sw = ctypes.windll.user32.GetSystemMetrics(0)
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    for _ in range(times):
        dx0 = int(x * 65535 / max(sw - 1, 1))
        dy0 = int(y * 65535 / max(sh - 1, 1))
        move = I(INPUT_MOUSE, MI(dx0, dy0, 0, MOVE | ABS, 0, 0))
        ctypes.windll.user32.SendInput(1, ctypes.byref(move), ctypes.sizeof(I))
        wheel = I(INPUT_MOUSE, MI(0, 0, ctypes.wintypes.DWORD(delta & 0xFFFFFFFF), WHEEL, 0, 0))
        ctypes.windll.user32.SendInput(1, ctypes.byref(wheel), ctypes.sizeof(I))
        time.sleep(0.05)


def _is_time_line(text: str) -> bool:
    """粗略判断一行文本是否为时间信息。"""

    if not text:
        return False
    patterns = [
        r"\d{4}年\d{1,2}月\d{1,2}日",
        r"\d{2}-\d{2}",
        r"\d{1,2}:\d{2}",
        r"昨[天日]",
        r"星期[一二三四五六日天]",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def _split_like_names(text: str) -> List[str]:
    """解析点赞字符串。"""

    if not text:
        return []

    like_prefix = _lang(MOMENTS, '赞')
    text = text.strip()
    if text.startswith(like_prefix):
        text = text[len(like_prefix):].lstrip('：: ')

    sep = _lang(MOMENTS, '分隔符_点赞')
    if sep:
        parts = [part.strip() for part in text.split(sep) if part.strip()]
    else:
        parts = [name.strip() for name in re.split(r'[,:，]', text) if name.strip()]
    return parts


@dataclass
class MomentComment:
    """朋友圈评论数据结构。"""

    author: str
    content: str
    reply_to: Optional[str] = None
    raw: str = ''

    @classmethod
    def from_text(cls, text: str) -> 'MomentComment':
        text = text.strip()
        reply_to = None
        author = ''
        content = text

        # 格式示例："张三 回复 李四：你好" 或 "张三: 哈喽"
        match = re.match(r'^(?P<author>[^：:]+?)\s*(?:回复\s*(?P<reply>[^：:]+?)\s*)?[：:](?P<content>.*)$', text)
        if match:
            author = match.group('author').strip()
            reply_to = match.group('reply')
            if reply_to:
                reply_to = reply_to.strip()
            content = match.group('content').strip()
        else:
            author = ''
            content = text.strip()

        return cls(author=author, content=content, reply_to=reply_to, raw=text)


class MomentItem(BaseUISubWnd):
    """朋友圈单条动态。"""

    def __init__(self, control: uia.Control, parent: 'MomentList'):
        self.control = control
        self.parent = parent
        self.root = parent.root
        self._parsed = False
        self.nickname: str = ''
        self.content: str = ''
        self.location: Optional[str] = None
        self.time: str = ''
        self.likes: List[str] = []
        self.comments: List[MomentComment] = []
        self.image_count: int = 0
        self.is_advertisement: bool = False
        self._comment_controls: Dict[str, uia.Control] = {}

    # ----------------------------------------------------------------------------------------------
    # 数据解析
    # ----------------------------------------------------------------------------------------------

    def _ensure_parsed(self) -> None:
        if self._parsed:
            return

        raw_text = self.control.Name or ''
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

        if lines:
            self.nickname = lines[0]

        body_lines = lines[1:]
        content_lines: List[str] = []
        comment_lines: List[str] = []
        likes_line: Optional[str] = None

        for line in body_lines:
            if not line:
                continue

            if re.search(_lang(MOMENTS, 're_图片数'), line):
                count = re.findall(r'\d+', line)
                if count:
                    self.image_count = int(count[0])
                continue

            if line.startswith(_lang(MOMENTS, '赞')):
                likes_line = line
                continue

            if line == _lang(MOMENTS, '评论'):
                # 后续均为评论
                comment_lines.extend(body_lines[body_lines.index(line) + 1:])
                break

            if _lang(MOMENTS, '广告') in line:
                self.is_advertisement = True
                continue

            if not self.time and _is_time_line(line):
                self.time = line
                continue

            content_lines.append(line)

        # 若未在循环中捕获评论，则继续检查剩余行
        if not comment_lines:
            collecting = False
            for line in body_lines:
                if line == _lang(MOMENTS, '评论'):
                    collecting = True
                    continue
                if collecting:
                    comment_lines.append(line)

        if likes_line:
            self.likes = _split_like_names(likes_line)

        self.content = '\n'.join(content_lines).strip()
        self.comments = [MomentComment.from_text(line) for line in comment_lines if line.strip()]

        # 记录可用于回复的控件
        for child in self.control.GetChildren():
            if child.ControlTypeName == 'TextControl':
                text = (child.Name or '').strip()
                if text:
                    self._comment_controls.setdefault(text, child)

        self._parsed = True

    # ----------------------------------------------------------------------------------------------
    # 对外属性访问
    # ----------------------------------------------------------------------------------------------

    @property
    def publisher(self) -> str:
        self._ensure_parsed()
        return self.nickname

    @property
    def text(self) -> str:
        self._ensure_parsed()
        return self.content

    @property
    def timestamp(self) -> str:
        self._ensure_parsed()
        return self.time

    @property
    def like_users(self) -> List[str]:
        self._ensure_parsed()
        return list(self.likes)

    @property
    def comment_list(self) -> List[MomentComment]:
        self._ensure_parsed()
        return list(self.comments)

    # ----------------------------------------------------------------------------------------------
    # 工具方法
    # ----------------------------------------------------------------------------------------------

    def find_comment(self, author: str) -> Optional[MomentComment]:
        self._ensure_parsed()
        for comment in self.comments:
            if comment.author == author:
                return comment
        return None

    def get_comment_control(self, comment: MomentComment) -> Optional[uia.Control]:
        self._ensure_parsed()
        key_candidates = [comment.raw, f"{comment.author}: {comment.content}", f"{comment.author}：{comment.content}"]
        for key in key_candidates:
            if key and key in self._comment_controls:
                return self._comment_controls[key]
        # fallback: 遍历匹配
        for text, ctrl in self._comment_controls.items():
            if comment.author and text.startswith(comment.author):
                if comment.content in text:
                    return ctrl
        return None


class MomentList(BaseUISubWnd):
    """朋友圈时间线列表。"""

    def __init__(self, parent: 'Moment'):
        self.parent = parent
        self.root = parent.root
        self.control = self._locate_list(parent)
        self._items: Optional[List[MomentItem]] = None

    def _locate_list(self, parent: 'Moment') -> Optional[uia.Control]:
        wxlog.debug('尝试定位朋友圈列表控件')
        # 首先尝试通过常用 className 定位
        candidates: Iterable[uia.Control] = []
        try:
            root_control = parent.control  # mmui::SNSWindow（时间线所在独立窗口）
            candidates = root_control.GetChildren() if root_control is not None else []
        except Exception:
            candidates = []

        queue = list(candidates)
        visited = set()

        while queue:
            ctrl = queue.pop(0)
            if ctrl in visited:
                continue
            visited.add(ctrl)

            class_name = getattr(ctrl, 'ClassName', '') or ''
            automation_id = getattr(ctrl, 'AutomationId', '') or ''
            # 微信 4.x：时间线列表为 mmui::TimeLineListView（位于独立 SNSWindow 内）
            if ctrl.ControlTypeName == 'ListControl' and 'TimeLineListView' in class_name:
                wxlog.debug(f'找到朋友圈时间线列表控件：{class_name}')
                return ctrl
            if ctrl.ControlTypeName == 'ListControl' and ('Moment' in class_name or 'moment' in automation_id.lower()):
                wxlog.debug(f'找到疑似朋友圈列表控件：{class_name}')
                return ctrl

            # 朋友圈列表一般会包含"评论"按钮
            children = []
            try:
                children = ctrl.GetChildren()
            except Exception:
                children = []

            if ctrl.ControlTypeName == 'ListControl':
                for child in children:
                    try:
                        if getattr(child, 'Name', '') == _lang(MOMENTS, '评论'):
                            wxlog.debug('通过子元素匹配到朋友圈列表控件')
                            return ctrl
                    except Exception:
                        continue

            queue.extend(children)

        wxlog.debug('未能定位到朋友圈列表控件')
        return None

    def exists(self, wait: float = 0) -> bool:  # type: ignore[override]
        if not self.control:
            return False
        try:
            return self.control.Exists(wait)
        except Exception:
            return False

    def refresh(self) -> None:
        self._items = None

    def get_items(self, refresh: bool = False) -> List[MomentItem]:
        if refresh or self._items is None:
            self._items = []
            if not self.control:
                return self._items

            try:
                children = self.control.GetChildren()
            except Exception:
                children = []

            for child in children:
                try:
                    if child.ControlTypeName in {'ListItemControl', 'CustomControl'}:
                        text = getattr(child, 'Name', '') or ''
                        if text.strip():
                            self._items.append(MomentItem(child, self))
                except Exception:
                    continue
        return list(self._items)


class Moment:
    """朋友圈接口封装。"""

    def __init__(self, wx_obj):
        self._wx = wx_obj
        self._api: Optional[uia.Control] = None   # mmui::SNSWindow 原始控件
        self.root = self                          # 自身即根（暴露 control/pid）
        self._list: Optional[MomentList] = None

    @property
    def control(self) -> Optional[uia.Control]:
        """朋友圈时间线所在的独立窗口控件（``mmui::SNSWindow``）。"""
        return self._api

    @property
    def pid(self) -> Optional[int]:
        if self._api is None:
            return None
        try:
            return self._api.ProcessId
        except Exception:
            return None

    def _find_sns_window(self, timeout: float = 3.0) -> Optional[uia.Control]:
        """定位独立的“朋友圈”顶层窗口（``mmui::SNSWindow``）。

        微信 4.x 的 t朋友圈 时间线是一个独立顶层窗口，不在主窗口内容区；
        早期的定位逻辑基于主窗口，故找不到列表。这里按 UIA 类名扫描顶层窗口。
        """
        t0 = time.time()
        pid = None
        try:
            api = getattr(self._wx, '_api', None)
            pid = api.pid if (api is not None) else None
        except Exception:
            pid = None
        while time.time() - t0 < timeout:
            try:
                wins = find_all_windows_from_root(uiaclsname='mmui::SNSWindow', pid=pid) \
                    if pid else find_all_windows_from_root(uiaclsname='mmui::SNSWindow')
            except Exception:
                wins = []
            if wins:
                return wins[0]
            time.sleep(0.3)
        return None

    def _ensure_list(self) -> Optional[MomentList]:
        if self._list and self._list.exists(0):
            return self._list

        try:
            self._wx.SwitchToMoments()
        except Exception:
            wxlog.debug('切换到朋友圈页面失败')
            return None

        # 时间线在独立的 mmui::SNSWindow 顶层窗口中，等待其出现
        win = self._find_sns_window()
        if win is None:
            wxlog.debug('未找到朋友圈窗口（mmui::SNSWindow）')
            self._api = None
            return None
        self._api = win

        self._list = MomentList(self)
        if not self._list.control:
            return None
        return self._list


    def _time_line_rect(self) -> Optional[tuple]:
        """返回时间线列表控件的屏幕矩形；找不到返回 None。"""
        lst = self._ensure_list()
        if not lst or not lst.control:
            return None
        try:
            r = lst.control.BoundingRectangle
            return (r.left, r.top, r.right, r.bottom)
        except Exception:
            return None


    def _scroll(self, delta: int = -120, times: int = 1) -> None:
        """在时间线中心滚动。负 delta=向下(看更早)，正=向上(看最新)。"""
        rect = self._time_line_rect()
        if not rect:
            return
        x = int((rect[0] + rect[2]) // 2)
        y = int((rect[1] + rect[3]) // 2)
        _send_scroll(x, y, delta=delta, times=times)


    def _scroll_to_top(self, steps: int = 20) -> None:
        """向上滚动到时间线顶部，作为定位的参考起点。"""
        self._scroll(delta=120, times=steps)
        time.sleep(0.6)


    def _read_visible_items(self, refresh: bool = True) -> List[MomentItem]:
        lst = self._ensure_list()
        if not lst:
            return []
        return lst.get_items(refresh=refresh)


    def _db_posts(self, db) -> List[dict]:
        """取数据库朋友圈有序列表（最新在前），供标尺对齐。"""
        try:
            return list(db.get_moments(limit=0))
        except Exception:
            return []

    def _signature(self, nickname: str, text: str) -> str:
        """构造模糊签名用于 UIA↔DB 对齐：昵称 + 正文前若干字。"""
        norm = lambda s: ''.join((s or '').split()) or ''
        return (norm(nickname) + '|' + norm(text)[:12]).lower()

    def _db_pos(self, db_posts: List[dict], item: MomentItem) -> Optional[int]:
        """把 UIA 单元格对齐到数据库索引位置。

        用「昵称 + 正文前缀」签名在 db_posts 里找最接近的匹配索引。
        可能有同昵称多条，故优先正文开头的子串匹配；都失败返回 None。
        """
        try:
            nickname = item.publisher or ''
            text = item.text or ''
        except Exception:
            return None
        norm = lambda s: ''.join((s or '').split()) or ''
        n_nick = norm(nickname)
        n_text = norm(text)
        # 先精确签名
        for i, f in enumerate(db_posts):
            if self._signature(f.get('nickname', ''), f.get('text', '')) == self._signature(nickname, text):
                return i
        # 退化：同昵称，且正文前缀匹配
        for i, f in enumerate(db_posts):
            if norm(f.get('nickname', '')) != n_nick:
                continue
            f_text = norm(f.get('text', ''))
            if n_text and (n_text[:10] in f_text or f_text[:10] in n_text):
                return i
        return None

    def _target_idx(self, db_posts: List[dict], publisher: Optional[str],
                    keyword: Optional[str]) -> Optional[int]:
        """返回目标在 db_posts 中的索引（最新在前，0=最顶部）。"""
        for i, f in enumerate(db_posts):
            if publisher and f.get('nickname') == publisher:
                return i
            if keyword and keyword in f.get('text', ''):
                return i
        # 以关键词子串尽量宽松再找一次
        if keyword:
            for i, f in enumerate(db_posts):
                if keyword in f.get('text', ''):
                    return i
        return None

    def _matches(self, item: MomentItem, publisher: Optional[str],
                 keyword: Optional[str]) -> bool:
        """判断单个可见 cell 是否匹配目标（作者 / 正文关键词）。

        优先复用 item 的解析结果；取不到时退回匹配 control.Name 原文。
        """
        if publisher:
            try:
                if item.publisher == publisher:
                    return True
            except Exception:
                pass
            name = (item.control.Name or '')
            if publisher in name:
                return True
            return False
        if keyword:
            try:
                if keyword in item.text:
                    return True
            except Exception:
                pass
            name = (item.control.Name or '')
            if keyword in name:
                return True
            return False
        return False

    def find_moment(self, publisher: Optional[str] = None,
                    keyword: Optional[str] = None,
                    db=None, max_screens: int = 300,
                    start_from_top: bool = True,
                    proximity: int = 3) -> Optional[MomentItem]:
        """滚动定位指定朋友圈（作者昵称 / 正文关键词），返回其 UIA 控件。

        采用数据库标尺 + UIA 实时匹配的双向滚动：
          - 用数据库有序列表确定目标索引 target_idx。
          - 每滚一步，读当前可见 cell，把最顶部可见 cell 对齐到数据库索引 pos。
          - 由 pos 与 target_idx 的差决定**往上还是往下**滚，并按距离调整步长。
          - 目标出现在可见区内即命中；到顶/到底且无明显进展才判定失败。

        Args:
            publisher: 发布者昵称（精确匹配）。
            keyword: 正文关键词（子串匹配）。
            db: 可选 ``MomentDB``/``WeChatDB``，提供标尺；缺失时仅向下逐屏匹配。
            max_screens: 最大滚动迭代次数。
            start_from_top: True 时先滚到顶部作为参考起点。
            proximity: 接近目标索引多少条时改用小步长（逐条逼近）。

        Returns:
            命中的 :class:`MomentItem`；找不到返回 None。
        """
        if not publisher and not keyword:
            wxlog.debug('find_moment 缺少定位条件（publisher 或 keyword）')
            return None

        db_posts = self._db_posts(db) if db is not None else []
        target_idx = self._target_idx(db_posts, publisher, keyword) if db_posts else None
        if publisher and db_posts and target_idx is None:
            wxlog.debug('数据库未找到该朋友圈，直接返回 None')
            return None

        if start_from_top:
            self._scroll_to_top()

        for screen in range(max_screens):
            items = self._read_visible_items(refresh=True)
            if not items:
                break

            # 1) 目标当前可见 -> 命中
            for item in items:
                if self._matches(item, publisher, keyword):
                    wxlog.debug(f'第 {screen} 屏命中目标朋友圈')
                    return item

            # 2) 无 DB：只能向下逐屏
            if target_idx is None:
                self._scroll(delta=-120, times=2)
                time.sleep(0.5)
                continue

            # 3) 对齐当前顶部 cell 到 DB 索引
            pos = self._db_pos(db_posts, items[0])
            if pos is None:
                # 顶部对不上，尝试可见区任意一个可对齐 item
                for it in items:
                    pos = self._db_pos(db_posts, it)
                    if pos is not None:
                        break
            if pos is None:
                # 无法对齐：保守地向下滚一屏
                self._scroll(delta=-120, times=3)
                time.sleep(0.35)
                continue

            diff = target_idx - pos
            wxlog.debug(f'当前 pos={pos} 目标={target_idx} diff={diff} 屏={screen}')

            # 4) 按距离自适应步长；接近时缩小步长精确逼近
            adiff = abs(diff)
            if adiff <= proximity:
                times, delta = 1, (-120 if diff >= 0 else 120)
                sleep = 0.45
            elif adiff <= 20:
                times, delta, sleep = 2, (-120 if diff > 0 else 120), 0.4
            elif adiff <= 60:
                times, delta, sleep = 4, (-120 if diff > 0 else 120), 0.35
            else:
                times, delta, sleep = 6, (-120 if diff > 0 else 120), 0.3
            self._scroll(delta=delta, times=times)
            time.sleep(sleep)

        wxlog.debug('滚动定位超过最大屏数或到尽头，未命中')
        return None


    # ------------------------------------------------------------------------------------------
    # “…”按钮识别（模板匹配 + 深浅模式）
    # ------------------------------------------------------------------------------------------

    def _theme(self, rect: tuple) -> str:
        """依据 cell 背景亮度判断当前深浅模式，返回 'dark' 或 'light'。

        采样 cell 中下部若干空白点求平均亮度，暗->dark，亮->light。
        """
        try:
            import pyautogui
        except Exception:
            return 'light'
        left, top, right, bottom = rect
        w, h = right - left, bottom - top
        pts = [
            (int(left + w * 0.5), int(top + h * 0.3)),
            (int(left + w * 0.7), int(top + h * 0.3)),
            (int(left + w * 0.5), int(top + h * 0.85)),
            (int(left + w * 0.8), int(top + h * 0.6)),
        ]
        lum, n = 0, 0
        for (x, y) in pts:
            try:
                r, g, b = pyautogui.pixel(x, y)
            except Exception:
                continue
            lum += 0.299 * r + 0.587 * g + 0.114 * b
            n += 1
        if n == 0:
            return 'light'
        return 'dark' if lum / n < 150 else 'light'

    def _more_template(self, theme: str) -> Optional[str]:
        return _asset_path('moments_more_dark.png' if theme == 'dark' else 'moments_more_light.png')

    def _find_more_button(self, item: MomentItem,
                          region: Optional[tuple] = None) -> Optional[tuple]:
        """在 cell 右下角用模板匹配定位 “…” 按钮，返回其中心 (x, y) 或 None。

        Args:
            item: 目标朋友圈项（须有 .control 的 BoundingRectangle）。
            region: 可选自定义搜索区域 (left, top, width, height)；缺省用 cell 区域。
        """
        try:
            import pyautogui
        except Exception as e:
            wxlog.debug(f'pyautogui 不可用：{e}')
            return None
        br = item.control.BoundingRectangle
        theme = self._theme((br.left, br.top, br.right, br.bottom))
        tpl = self._more_template(theme)
        if not tpl:
            wxlog.debug('缺少 “…” 按钮模板资源')
            return None

        if region is not None:
            rleft, rtop, rw, rh = region
        else:
            left, top, right, bottom = br.left, br.top, br.right, br.bottom
            rleft, rtop = max(0, left - 10), max(0, top - 10)
            rw = (right - left) + 20
            rh = (bottom - top) + 20
        try:
            pos = pyautogui.locateOnScreen(tpl, region=(rleft, rtop, rw, rh),
                                           confidence=0.75)
        except Exception as e:
            wxlog.debug(f'“…” 模板匹配失败：{e}')
            return None
        if not pos:
            return None
        return (int(pos.left + pos.width // 2), int(pos.top + pos.height // 2))

    def _locate_more_click(self, item: MomentItem, max_retry: int = 8) -> bool:
        """定位并点击目标朋友圈的 “…” 按钮，弹出点赞/评论浮层。

        未识别到 “…” 时微调滚动让该条完整进入视野后重试，最多 max_retry 次。
        只点击 “…”；不含后续的点赞/评论动作。

        Returns:
            True 表示识别到并已点击 “…”（浮层应已弹出）。
        """
        if max_retry < 1:
            return False
        for attempt in range(max_retry):
            pt = self._find_more_button(item)
            if pt is not None:
                try:
                    import pyautogui
                    pyautogui.click(pt[0], pt[1])
                    time.sleep(0.6)
                    return True
                except Exception as e:
                    wxlog.debug(f'点击 “…” 失败：{e}')
                    return False
            # 未识别到：微调滚动（向下一点）让该条 “…” 完整露出
            wxlog.debug(f'未识别到 “…”（第 {attempt} 次），微调滚动')
            self._scroll(delta=-120, times=1)
            time.sleep(0.4)
        return False


    def _find_float_button(self, name: str, timeout: float = 2.0) -> Optional[uia.Control]:
        """在浮层（全局深度 FindAll）中查找 Name 为指定文案的按钮。

        例如 “…” 浮层打开后，其中的「赞 / 评论」是真实 UIA Button，
        可通过从 UI 自动化根节点向下的深度遍历枚举到，并用其中心坐标点击。
        """
        t0 = time.time()
        target = _lang(MOMENTS, name) if name in ('赞', '评论') else name
        while time.time() - t0 <= timeout:
            try:
                root = uia.GetRootControl()
            except Exception:
                root = None
            if root is not None:
                try:
                    hit = self._collect_float_button(root, target, max_depth=40)
                    if hit is not None:
                        return hit
                except Exception:
                    pass
            time.sleep(0.15)
        return None

    @staticmethod
    def _collect_float_button(ctrl, target, max_depth: int = 40, depth: int = 0):
        """深度优先搜索整棵 UI 树，返回第一个 Name 恰好等于 target 的控件。

        优先匹配 ButtonControl，但也可退化为任意控件（某些自绘控件类型并非
        标准 ButtonControl，只要 Name 匹配即可）。
        """
        if depth > max_depth:
            return None
        try:
            ctrl_name = ctrl.Name or ''
        except Exception:
            ctrl_name = ''
        if ctrl_name == target:
            try:
                if ctrl.ControlTypeName == 'ButtonControl':
                    return ctrl
            except Exception:
                pass
        try:
            kids = ctrl.GetChildren()
        except Exception:
            kids = []
        for kid in kids:
            r = Moment._collect_float_button(kid, target, max_depth, depth + 1)
            if r is not None:
                return r
        if ctrl_name == target:
            return ctrl
        return None

    def _click_float_button(self, name: str, timeout: float = 2.0) -> bool:
        """查找并点击浮层按钮（按按钮中心屏幕坐标 pyautogui.click）。"""
        btn = self._find_float_button(name, timeout=timeout)
        if btn is None:
            wxlog.debug(f'未找到浮层按钮：{name}')
            return False
        try:
            r = btn.BoundingRectangle
            x, y = int((r.left + r.right) // 2), int((r.top + r.bottom) // 2)
        except Exception:
            return False
        try:
            import pyautogui
            pyautogui.click(x, y)
            time.sleep(0.5)
            return True
        except Exception as e:
            wxlog.debug(f'点击浮层按钮 {name} 失败：{e}')
            return False

    def _like_open(self) -> bool:
        """在 “…” 浮层已弹出的前提下，点击「赞」。"""
        return self._click_float_button('赞', timeout=2.5)

    def LikeMoment(self, publisher: Optional[str] = None,
                   keyword: Optional[str] = None, db=None,
                   max_screens: int = 300, max_retry: int = 8) -> WxResponse:
        """一键：定位指定朋友圈 -> 点击 “…” -> 点赞。

        Args:
            publisher: 发布者昵称。
            keyword: 正文关键词。
            db: 可选 MomentDB 标尺。
            max_screens / max_retry: 滚动定位 / “…” 重试上限。

        Returns:
            WxResponse。
        """
        if not publisher and not keyword:
            return WxResponse.failure('LikeMoment 缺少定位条件')
        item = self.find_moment(publisher=publisher, keyword=keyword,
                                db=db, max_screens=max_screens)
        if item is None:
            return WxResponse.failure('未能定位到目标朋友圈')
        if not self._locate_more_click(item, max_retry=max_retry):
            return WxResponse.failure('未能打开 “…” 浮层')
        if self._like_open():
            return WxResponse.success('点赞成功')
        return WxResponse.failure('未能在浮层中找到点赞按钮')


    # ------------------------------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------------------------------

    def GetMoments(self, refresh: bool = False) -> List[MomentItem]:
        """获取朋友圈动态列表。

        Args:
            refresh: 是否强制刷新控件缓存。

        Returns:
            List[MomentItem]: 朋友圈动态对象列表。
        """

        moment_list = self._ensure_list()
        if not moment_list:
            return []
        return moment_list.get_items(refresh)

    def FindMomentByPublisher(self, nickname: str, refresh: bool = False) -> Optional[MomentItem]:
        """根据发布者昵称查找朋友圈动态。"""

        nickname = nickname.strip()
        for item in self.GetMoments(refresh=refresh):
            if item.publisher == nickname:
                return item
        return None

    # ------------------------------------------------------------------------------------------
    # 点赞与评论（部分功能依赖 UI 结构，尽量保证稳健）
    # ------------------------------------------------------------------------------------------

    def _invoke_action_menu(self, item: MomentItem) -> Optional['MomentActionMenu']:
        action_button = None
        try:
            for child in item.control.GetChildren():
                if child.ControlTypeName == 'ButtonControl':
                    action_button = child
                    break
        except Exception:
            action_button = None

        if action_button:
            action_button.Click()
        else:
            try:
                item.control.RightClick()
            except Exception:
                return None

        menu = MomentActionMenu(item)
        if not menu.exists(0.5):
            return None
        return menu

    def Like(self, item: MomentItem, cancel: bool = False) -> WxResponse:
        menu = self._invoke_action_menu(item)
        if not menu:
            return WxResponse.failure('未能打开朋友圈操作菜单')
        try:
            return menu.like(cancel)
        finally:
            menu.close()

    def Comment(self, item: MomentItem, content: str, reply_to: Optional[str] = None) -> WxResponse:
        if reply_to:
            comment = item.find_comment(reply_to)
            if not comment:
                return WxResponse.failure('未找到需要回复的评论')
            ctrl = item.get_comment_control(comment)
            if not ctrl:
                return WxResponse.failure('未定位到评论控件')
            ctrl.Click()
        else:
            menu = self._invoke_action_menu(item)
            if not menu:
                return WxResponse.failure('未能打开朋友圈操作菜单')
            try:
                result = menu.comment()
            finally:
                menu.close()
            if not result:
                return result

        dialog = MomentCommentDialog(self)
        if not dialog.exists(0.5):
            return WxResponse.failure('未弹出评论窗口')
        return dialog.send(content)


class MomentActionMenu(BaseUISubWnd):
    """朋友圈点赞/评论菜单。"""

    _win_cls_name: str = 'Qt51514QWindowToolSaveBits'

    def __init__(self, parent: MomentItem, timeout: float = 1.0):
        self.parent = parent
        self.root = parent.root
        self.control = self._locate(timeout)

    def _locate(self, timeout: float) -> Optional[uia.Control]:
        t0 = time.time()
        while time.time() - t0 <= timeout:
            wins = find_all_windows_from_root(classname=self._win_cls_name, pid=self.root.pid)
            for win in wins:
                try:
                    children = win.GetChildren()
                except Exception:
                    children = []
                for child in children:
                    name = getattr(child, 'Name', '')
                    if name in {_lang(MOMENTS, '赞'), _lang(MOMENTS, '取消'), _lang(MOMENTS, '评论')}:
                        return win
            time.sleep(0.05)
        return None

    def exists(self, wait: float = 0) -> bool:  # type: ignore[override]
        if not self.control:
            return False
        try:
            return self.control.Exists(wait)
        except Exception:
            return False

    def _find_button(self, names: Iterable[str]) -> Optional[uia.Control]:
        if not self.control:
            return None
        target_names = list(names)
        try:
            children = self.control.GetChildren()
        except Exception:
            children = []
        for child in children:
            if child.ControlTypeName != 'ButtonControl':
                continue
            name = getattr(child, 'Name', '')
            if name in target_names:
                return child
        return None

    def like(self, cancel: bool = False) -> WxResponse:
        target_names = [_lang(MOMENTS, '赞')]
        if cancel:
            target_names.insert(0, _lang(MOMENTS, '取消'))

        button = self._find_button(target_names)
        if not button:
            return WxResponse.failure('未找到点赞按钮')
        button.Click()
        return WxResponse.success('操作成功')

    def comment(self) -> WxResponse:
        button = self._find_button([_lang(MOMENTS, '评论')])
        if not button:
            return WxResponse.failure('未找到评论按钮')
        button.Click()
        return WxResponse.success('已触发评论')

    def close(self) -> None:
        if not self.control:
            return
        try:
            self.control.SendKeys('{Esc}')
        except Exception:
            pass


class MomentCommentDialog(BaseUISubWnd):
    """朋友圈评论输入窗口。"""

    _win_cls_name: str = 'Qt51514QWindowToolSaveBits'

    def __init__(self, parent: Moment):
        self.parent = parent
        self.root = parent.root
        self.control = self._locate()
        if self.control:
            self._init_controls()

    def _locate(self) -> Optional[uia.Control]:
        wins = find_all_windows_from_root(classname=self._win_cls_name, pid=self.root.pid)
        for win in wins:
            try:
                children = win.GetChildren()
            except Exception:
                children = []
            for child in children:
                if child.ControlTypeName == 'ButtonControl' and getattr(child, 'Name', '') == _lang(MOMENTS, '发送'):
                    return win
        return None

    def _init_controls(self) -> None:
        self.edit: Optional[uia.Control] = None
        self.send_button: Optional[uia.Control] = None
        try:
            children = self.control.GetChildren()
        except Exception:
            children = []
        for child in children:
            if child.ControlTypeName == 'EditControl' and self.edit is None:
                self.edit = child
            elif child.ControlTypeName == 'ButtonControl' and getattr(child, 'Name', '') == _lang(MOMENTS, '发送'):
                self.send_button = child

    def exists(self, wait: float = 0) -> bool:  # type: ignore[override]
        if not self.control:
            return False
        try:
            return self.control.Exists(wait)
        except Exception:
            return False

    def send(self, content: str) -> WxResponse:
        if not self.exists(0):
            return WxResponse.failure('评论窗口不存在')

        if not content:
            return WxResponse.failure('评论内容不能为空')

        if not self.edit or not self.edit.Exists(0):
            return WxResponse.failure('未找到评论输入框')

        try:
            self.edit.Click()
            self.edit.SendKeys('{Ctrl}a')
            SetClipboardText(content)
            self.edit.SendKeys('{Ctrl}v')

            if self.send_button and self.send_button.Exists(0):
                self.send_button.Click()
            else:
                self.edit.SendKeys('{Enter}')
        except Exception as exc:  # pragma: no cover - UI 交互异常仅记录日志
            wxlog.debug(f'发送朋友圈评论失败：{exc}')
            return WxResponse.failure('发送评论失败')

        return WxResponse.success('评论成功')


# ===========================================================================
# 数据库路线（微信 4.x 推荐）：读取 sns.db SnsTimeLine
# ===========================================================================

def _parse_user_comment(block: str) -> dict:
    """解析单个 ``<user_comment>...</user_comment>`` 块。"""
    def tag(name: str) -> Optional[str]:
        m = re.search(r"<%s>([^<]*)</%s>" % (name, name), block)
        return m.group(1).strip() if m else None

    return {
        "username": tag("username") or "",
        "nickname": tag("nickname") or "",
        "content": tag("content") or "",
        "create_time": int(tag("create_time") or 0),
        "type": int(tag("type") or 0),
        "comment_id": tag("comment_id") or "",
        "ref_comment_id": tag("ref_comment_id") or "",
        "b_deleted": int(tag("b_deleted") or 0),
    }


class MomentDB:
    """微信 4.x 朋友圈数据库读取器（无需 UI/OCR）。

    数据来源：``sns.db`` 的 ``SnsTimeLine`` 表，``content`` 为
    ``SnsDataItem`` XML。支持解析正文、图片/视频 md5 与本地缓存路径、
    点赞、评论、定位。

    用法::

        from wechatauto import WeChatDB, MomentDB
        md = MomentDB(WeChatDB())
        for feed in md.get_moments(limit=10):
            print(feed["nickname"], feed["text"])
    """

    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------
    # 数据访问
    # ------------------------------------------------------------------
    def _open_sns(self):
        for rel, path, _ in self.db._db_files:
            if os.path.basename(path) == "sns.db":
                return self.db._open(rel)
        raise RuntimeError("未找到 sns.db（朋友圈库）")

    def get_moments(self, limit: int = 20, offset: int = 0,
                    username: Optional[str] = None,
                    since: Optional[int] = None,
                    until: Optional[int] = None,
                    keyword: Optional[str] = None) -> List[dict]:
        """读取朋友圈时间线（按 tid 倒序）。

        Args:
            limit: 返回条数；``limit=0`` 表示不限制（配合时间/关键词过滤做全量遍历）。
            offset: 分页偏移。
            username: 只返回指定发布者的动态。
            since: 只返回 create_time >= since（Unix 秒）的动态。
            until: 只返回 create_time <= until（Unix 秒）的动态。
            keyword: 只返回正文（contentDesc）包含该关键词的动态。

        Notes:
            create_time 与关键词都存在 ``content`` XML 内，无法下推到 SQL 过滤；
            时间/关键词采用「放大内部拉取 + 逐条后过滤」策略，避免分页遗漏。
            过滤时 ``limit`` 语义为「过滤后返回的条数」，``limit=0`` 全量返回。
        """
        want_since = since is not None
        want_until = until is not None
        want_key = bool(keyword)
        # limit=0 表示「全量」。时间/关键词无法下推到 SQL，
        # 放大内部拉取量后逐条后过滤，避免分页遗漏。
        if want_since or want_until or want_key:
            internal_limit = 0 if limit == 0 else max(limit * 20, 200)
        else:
            internal_limit = limit
        limit_clause = "" if internal_limit == 0 else "LIMIT ? OFFSET ?"
        bind = () if internal_limit == 0 else (internal_limit, offset)

        conn = self._open_sns()
        try:
            if username:
                rows = conn.execute(
                    "SELECT tid, user_name, content FROM SnsTimeLine "
                    "WHERE user_name=? ORDER BY tid DESC " + limit_clause,
                    (username,) + bind,
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT tid, user_name, content FROM SnsTimeLine "
                    "ORDER BY tid DESC " + limit_clause,
                    bind,
                ).fetchall()
        finally:
            conn.close()

        feeds = [self.parse_feed(r["content"], r["user_name"]) for r in rows]
        if want_since or want_until or want_key:
            out = []
            kw = keyword
            for f in feeds:
                if want_since and f["create_time"] < since:
                    continue
                if want_until and f["create_time"] > until:
                    continue
                if want_key and kw and kw not in f["text"]:
                    continue
                out.append(f)
            if limit and len(out) > limit:
                return out[:limit]
            return out
        return feeds

    def get_moment(self, tid: int) -> Optional[dict]:
        conn = self._open_sns()
        try:
            r = conn.execute(
                "SELECT tid, user_name, content FROM SnsTimeLine WHERE tid=?",
                (tid,),
            ).fetchone()
        finally:
            conn.close()
        return self.parse_feed(r["content"], r["user_name"]) if r else None

    def get_my_moments(self, limit: int = 20) -> List[dict]:
        return self.get_moments(limit=limit, username=self.db.wxid)

    def count(self) -> int:
        conn = self._open_sns()
        try:
            return conn.execute("SELECT count(*) FROM SnsTimeLine").fetchone()[0]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 增量同步
    # ------------------------------------------------------------------
    def latest_tid(self) -> Optional[int]:
        """返回时间线最新的 tid（增量拉取的起点水位）。"""
        conn = self._open_sns()
        try:
            r = conn.execute("SELECT tid FROM SnsTimeLine ORDER BY tid DESC LIMIT 1").fetchone()
            return int(r[0]) if r else None
        finally:
            conn.close()

    def get_moments_since(self, since_tid: Optional[int] = None,
                          limit: int = 200) -> (List[dict], Optional[int]):
        """增量拉取比 ``since_tid`` 更新的动态，便于轮询实现「有新朋友圈就处理」。

        Returns:
            (feeds, new_latest_tid): 新增动态列表，以及当前最新的 tid。
            无新动态时 feeds 为空、new_latest_tid 为 None（无需更新水位）。
        """
        latest = self.latest_tid()
        if latest is None:
            return [], None
        if since_tid is not None and latest <= since_tid:
            return [], None
        feeds = self.get_moments(limit=limit)
        return feeds, latest

    # ------------------------------------------------------------------
    # 与我相关的互动（点赞/评论通知）
    # ------------------------------------------------------------------
    def get_interactions(self, limit: int = 50, offset: int = 0,
                         only_unread: bool = False) -> List[dict]:
        """读取 ``SnsMessage_tmp3``：他人对我朋友圈的点赞/评论通知。

        Args:
            limit / offset: 分页（按 local_id 倒序，最新在前）。
            only_unread: 只返回未读通知（is_unread == 1）。

        Returns:
            List[dict]，每条含：local_id, create_time, type(1=赞,2=评论),
            feed_id, from_username, from_nickname, to_username, to_nickname,
            content(评论正文，赞默认 '赞'), unread, relative_me, deleted。
        """
        conn = self._open_sns()
        try:
            where = " WHERE is_unread=1" if only_unread else ""
            limit_clause = "" if limit == 0 else "LIMIT ? OFFSET ?"
            bind = () if limit == 0 else (limit, offset)
            rows = conn.execute(
                "SELECT local_id, create_time, type, feed_id, from_username, "
                "from_nickname, to_username, to_nickname, content, "
                "serialized_comment_buf, comment_id, del_status, is_relative_me, is_unread "
                "FROM SnsMessage_tmp3%s "
                "ORDER BY local_id DESC %s" % (where, limit_clause),
                bind,
            ).fetchall()
        finally:
            conn.close()

        result = []
        for r in rows:
            content = r["content"]
            if not isinstance(content, str) or not content.strip():
                content = ""
            result.append({
                "local_id": r["local_id"],
                "create_time": r["create_time"],
                "type": r["type"],  # 1=赞, 2=评论
                "feed_id": r["feed_id"],
                "from_username": r["from_username"],
                "from_nickname": r["from_nickname"],
                "to_username": r["to_username"],
                "to_nickname": r["to_nickname"],
                "content": content,
                "comment_id": r["comment_id"],
                "deleted": r["del_status"],
                "relative_me": r["is_relative_me"],
                "unread": r["is_unread"],
            })
        for it in result:
            if it["type"] == 1 and not it["content"]:
                it["content"] = "赞"
        return result

    def interactions_unread_count(self) -> int:
        """返回未读的「赞/评论」通知条数。"""
        conn = self._open_sns()
        try:
            return conn.execute(
                "SELECT count(*) FROM SnsMessage_tmp3 WHERE is_unread=1"
            ).fetchone()[0]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # XML 解析
    # ------------------------------------------------------------------
    def parse_feed(self, xml: str, fallback_user: str = "") -> dict:
        if isinstance(xml, bytes):
            xml = xml.decode("utf-8", "replace")
        m = re.search(r"<id>(\d+)</id>", xml)
        feed_id = m.group(1) if m else ""
        m = re.search(r"<username>([^<]+)</username>", xml)
        author = m.group(1) if m else (fallback_user or "")
        m = re.search(r"<createTime>(\d+)</createTime>", xml)
        create_time = int(m.group(1)) if m else 0
        m = re.search(r"<contentDesc>([^<]*)</contentDesc>", xml)
        text = html.unescape(m.group(1)) if m else ""
        m = re.search(r'<location latitude="([^"]*)" longitude="([^"]*)"', xml)
        location = {"latitude": m.group(1), "longitude": m.group(2)} if m else None

        # 图片 / 视频（mediaList 中的 url 带 md5）
        images, videos = [], []
        for mm in re.finditer(r"<media>(.*?)</media>", xml, re.S):
            block = mm.group(1)
            um = re.search(r'<url[^>]*>(.*?)</url>', block, re.S)
            url = um.group(1).strip() if um else ""
            m = re.search(r"\bmd5=\"([0-9a-fA-F]{32})\"", block)
            md5 = m.group(1).lower() if m else ""
            vm = re.search(r"\bvideomd5=\"([0-9a-fA-F]{32})\"", block)
            msz = re.search(r"<size[^>]*\btotalSize=\"(\d+)\"", block)
            size = int(msz.group(1)) if msz else 0
            is_video = any(ext in url.lower() for ext in (".mp4", ".mov", ".avi")) \
                or vm is not None \
                or re.search(r"<videoDuration>\s*[1-9]", block) \
                or re.search(r'<type>\s*6\s*</type>', block)
            entry = {
                "md5": (vm.group(1).lower() if vm else md5),
                "url": url,
                "size": size,
            }
            if is_video:
                videos.append(entry)
            else:
                images.append(entry)

        # 点赞 / 评论
        likes, comments = [], []
        lm = re.search(r"<like_user_list>(.*?)</like_user_list>", xml, re.S)
        if lm:
            for blk in re.findall(r"<user_comment>(.*?)</user_comment>", lm.group(1), re.S):
                c = _parse_user_comment(blk)
                if c["username"]:
                    likes.append(c)
        cm = re.search(r"<comment_user_list>(.*?)</comment_user_list>", xml, re.S)
        if cm:
            for blk in re.findall(r"<user_comment>(.*?)</user_comment>", cm.group(1), re.S):
                c = _parse_user_comment(blk)
                if c["username"] and not c["b_deleted"]:
                    comments.append(c)

        return {
            "id": feed_id,
            "tid": fallback_user,
            "username": author,
            "nickname": self._nick(author),
            "text": text,
            "location": location,
            "create_time": create_time,
            "images": images,
            "videos": videos,
            "likes": likes,
            "comments": comments,
        }

    def _nick(self, username: str) -> str:
        if not username:
            return ""
        try:
            return self.db.get_nickname(username)
        except Exception:
            return username

    # ------------------------------------------------------------------
    # 评论树
    # ------------------------------------------------------------------
    def comment_tree(self, feed: dict, with_raw: bool = False) -> List[dict]:
        """把一条朋友圈的评论按「回复」关系组织成树。

        微信 4.x 的评论内嵌于 SnsTimeLine.content 的 XML（经 parse_feed 解析），
        无独立的 SnsComment 表；评论通过 ``comment_id`` / ``ref_comment_id``
        表达父子关系（ref_comment_id 指向被回复的上级评论）。

        Args:
            feed: ``get_moments`` / ``get_moment`` 返回的单条动态 dict。
            with_raw: True 时保留原始 `comment_id` / `ref_comment_id` / `type`。

        Returns:
            顶层评论节点列表；每条含 username / nickname / content /
            create_time / replies(子评论列表，可能为空)。
        """
        roots, by_id = [], {}
        comments = feed.get("comments") or []
        for c in comments:
            node = {
                "username": c.get("username", ""),
                "nickname": c.get("nickname", "") or self._nick(c.get("username", "")),
                "content": c.get("content", ""),
                "create_time": c.get("create_time", 0),
                "replies": [],
            }
            if with_raw:
                node["comment_id"] = c.get("comment_id", "")
                node["ref_comment_id"] = c.get("ref_comment_id", "")
                node["type"] = c.get("type", 0)
            cid = c.get("comment_id") or ""
            if cid:
                by_id[cid] = node
            ref = c.get("ref_comment_id") or ""
            if ref and ref in by_id:
                by_id[ref]["replies"].append(node)
            else:
                roots.append(node)
        return roots

    def comment_reply_to(self, feed: dict, comment: dict) -> Optional[dict]:
        """返回某条评论所回复的上级评论（若无回复对象返回 None）。

        依据 ``ref_comment_id`` 在同 feed 内查找；找不到时按
        ``parse_moment_text`` 的 `回复 xx:` 前缀推断。
        """
        ref = (comment or {}).get("ref_comment_id") or ""
        for c in (feed.get("comments") or []):
            if c.get("comment_id") and str(c.get("comment_id")) == str(ref):
                return c
        # 前端 MomentComment.from_text 针对纯文本的猜测
        parsed = MomentComment.from_text(comment.get("content", ""))
        if parsed.reply_to:
            for c in (feed.get("comments") or []):
                if c.get("nickname") == parsed.reply_to:
                    return c
        return None

    # ------------------------------------------------------------------
    # 媒体落地
    # ------------------------------------------------------------------
    @staticmethod
    def _cache_root(db) -> str:
        return os.path.join(db.account_dir, "cache")

    def find_local_media(self, md5: str, kind: str = "image",
                         size: int = 0) -> Optional[str]:
        """在 cache/<月>/Sns/<Img|Video>/<md5前两位> 下按 md5 查找本地缓存。

        微信把 Sns 缩略图/媒体按内容哈希散列到 ``Sns/Img|Video/xx/`` 分桶，
        文件名通常是该 md5 的前缀或后缀（可能与 feed 里的 url md5 不完全一致，
        这里做前缀与后缀双向匹配）。``size`` 非 0 时，md5 未命中的情况下
        再按文件大小近似匹配一次（对 Video 明文缓存尤其有用），未命中返回 None。
        """
        if len(md5) < 2:
            return None
        sub = "Img" if kind == "image" else "Video"
        prefix, suffix = md5[:2], md5[-2:]
        base = self._cache_root(self.db)
        if not os.path.isdir(base):
            return None

        def match(name: str) -> bool:
            base_n = os.path.splitext(name)[0]
            return base_n.startswith(md5) or base_n.endswith(md5) \
                or md5.startswith(base_n) or md5.endswith(base_n)

        # 1) 先按 md5 精确/前缀匹配
        for mon in os.listdir(base):
            for bucket in (prefix, suffix):
                d = os.path.join(base, mon, "Sns", sub, bucket)
                if not os.path.isdir(d):
                    continue
                try:
                    for f in os.listdir(d):
                        p = os.path.join(d, f)
                        if os.path.isfile(p) and match(f):
                            return p
                except OSError:
                    continue
        # 2) 未命中且指定了 size：遍历整个 media 树按文件大小近似匹配
        #    （视频缓存文件名是内容哈希、分桶与 feed md5 无关，须跨桶查找）
        if size:
            for mon in os.listdir(base):
                d_root = os.path.join(base, mon, "Sns", sub)
                if not os.path.isdir(d_root):
                    continue
                if sub == "Video":
                    try:
                        for bucket in os.listdir(d_root):
                            bd = os.path.join(d_root, bucket)
                            if not os.path.isdir(bd):
                                continue
                            for f in os.listdir(bd):
                                p = os.path.join(bd, f)
                                if os.path.isfile(p) and os.path.getsize(p) == size:
                                    return p
                    except OSError:
                        continue
        return None

    def download_media(self, media: dict, save_dir: Optional[str] = None,
                       kind: str = "image") -> Optional[str]:
        """下载单条图片/视频：优先本地缓存，否则从 URL 拉取。

        Args:
            media: ``parse_feed`` 返回的 images/videos 中的一条
                   （含 ``md5`` 与 ``url`` 字段）。
            save_dir: 保存目录，默认 ``~/Documents/wechatauto_moments``。
            kind: ``"image"`` 或 ``"video"``，决定本地缓存子目录。

        Returns:
            保存后的文件绝对路径；失败或缺少 md5/url 时返回 None。
        """
        save_dir = save_dir or os.path.join(
            os.path.expanduser("~"), "Documents", "wechatauto_moments"
        )
        os.makedirs(save_dir, exist_ok=True)
        media = media or {}
        md5 = media.get("md5")
        size = media.get("size") or 0
        local = self.find_local_media(md5, kind, size=size) if md5 else None
        url = media.get("url") or ""

        if local:
            src = local
            with open(src, "rb") as f:
                data = f.read()
            name = os.path.basename(src)
        else:
            if not url:
                return None
            try:
                import urllib.request
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0)"}
                )
                data = urllib.request.urlopen(req, timeout=20).read()
            except Exception as e:
                wxlog.debug(f'下载朋友圈媒体失败：{e}')
                return None
            if kind == "video" or url.lower().endswith((".mp4", ".mov", ".avi")):
                ext = ".mp4"
            else:
                ext = os.path.splitext(url)[1].lower() or ".jpg"
            name = "%s%s" % (md5 or os.path.basename(url) or "media", ext)

        out = os.path.join(save_dir, name)
        with open(out, "wb") as f:
            f.write(data)
        return out

    def download_moment_media(self, feed: dict, save_dir: Optional[str] = None,
                              images: bool = True, videos: bool = True,
                              make_subdirs: bool = True) -> List[str]:
        """下载一条朋友圈动态的全部图片/视频。

        单条动态可同时含多张图片与一个视频；此方法批量下载并把
        结果统一返回，供一次性落地整条朋友圈的素材。

        Args:
            feed: ``get_moments`` / ``get_moment`` 返回的单条动态 dict
                  （需含 ``images`` 与 ``videos`` 列表）。
            save_dir: 保存目录，默认 ``~/Documents/wechatauto_moments``。
            images / videos: 是否分别下载图片 / 视频。
            make_subdirs: True 时按 ``<save_dir>/<tid>_<id>/`` 为每条动态
                          建独立子目录存放，便于按动态归档。

        Returns:
            成功下载的文件绝对路径列表（跳过失败项）。
        """
        target = save_dir or os.path.join(
            os.path.expanduser("~"), "Documents", "wechatauto_moments"
        )
        if make_subdirs:
            sub = "%s_%s" % (feed.get("tid") or feed.get("username") or "sns",
                             feed.get("id") or "")
            target = os.path.join(target, sub)
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as e:
            wxlog.debug(f'创建保存目录失败：{e}')
            return []

        saved: List[str] = []
        for img in (feed.get("images") or []):
            if images:
                p = self.download_media(img, target, "image")
                if p:
                    saved.append(p)
        for vid in (feed.get("videos") or []):
            if videos:
                p = self.download_media(vid, target, "video")
                if p:
                    saved.append(p)
        return saved
