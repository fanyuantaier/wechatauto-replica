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

import hashlib
import html
import io
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Union

from PIL import Image

from wechatauto import uia
from wechatauto.languages import MOMENTS, get_lang
from wechatauto.logger import wxlog
from wechatauto.param import WxParam, WxResponse
from wechatauto.ui.base import BaseUISubWnd
from wechatauto.utils.tools import find_all_windows_from_root
from wechatauto.utils.win32 import SetClipboardText


# 多候选 dims 消歧的字节数偏差上限（绝对），相对 feed size 的 10% 同时生效。
# 真图不在缓存 + 同尺寸多张缓存图时，偏差超限即拒绝，避免无关图冒充。
_MAX_SIZE_DEV = 512


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
            target = pkg.joinpath(name)
            if target.is_file():
                return str(target)
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


_REL_TIME_PATTERNS = [
    r"刚刚",
    r"\d+\s*分钟前",
    r"\d+\s*小时前",
    r"\d+\s*天前",
    r"昨天",
]


def _find_time_tail(text: str) -> Optional[re.Match]:
    """在合并布局单行摘要里找时间片段（取最靠右的匹配）。

    微信合并布局把「昵称 正文… 媒体标记 时间」压成一行 UIA Name，
    时间通常在行尾，形如 ``39分钟前``、``1小时前``、``昨天`` 或绝对时间。
    """
    if not text:
        return None
    patterns = _REL_TIME_PATTERNS + [
        r"\d{4}年\d{1,2}月\d{1,2}日",
        r"\d{1,2}月\d{1,2}日",
        r"\d{2}-\d{2}",
        r"\d{1,2}:\d{2}",
    ]
    found: Optional[re.Match] = None
    for pat in patterns:
        for m in re.finditer(pat, text):
            if found is None or m.start() > found.start():
                found = m
    return found


def _is_layout_decoration_cell(class_name: str, name: str) -> bool:
    """判断一个 ListItem 是否为合并布局的装饰性 cell（非动态正文）。

    合并布局下每条动态在时间线里摊成同一层的 3 个 ListItem：正文
    （``TimelineContentCell``）、评论区（Name ``评论区``）、余下计数
    （Name ``余下N条``）。这里识别并剔除后两者，避免污染动态列表。
    """
    if 'ContentCell' in class_name:
        return False
    name = (name or '').strip()
    if not name:
        return True
    if name in {_lang(MOMENTS, '评论'), _lang(MOMENTS, '评论区')}:
        return True
    if re.match(r'^(余下|餘下|Remaining)\s*\d+', name):
        return True
    return False


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

        if len(lines) == 1:
            # 合并布局：单行摘要（昵称/正文片段/媒体/时间压在一行）
            if self._parse_merged_summary(lines[0]):
                self._parsed = True
                self._collect_comment_controls()
                return

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

        self._collect_comment_controls()
        self._parsed = True

    def _collect_comment_controls(self) -> None:
        """收集可用于回复的评论控件（TextControl）。"""
        for child in self.control.GetChildren():
            if child.ControlTypeName == 'TextControl':
                text = (child.Name or '').strip()
                if text:
                    self._comment_controls.setdefault(text, child)

    def _parse_merged_summary(self, line: str) -> bool:
        """解析合并布局下的单行摘要 Name。

        格式：``<昵称> <正文摘要…> <媒体标记> <时间>``，其中媒体标记形如
        ``包含N张图片`` / ``视频``，时间形如 ``39分钟前`` / ``昨天``。
        单行时昵称与正文片段以空格分隔，第一个 token 视为昵称。

        Returns:
            True 表示识别到有效正文 cell 并已填充字段；False 表示装饰性 cell。
        """
        s = line.strip()
        if not s:
            return False

        # 排除装饰性 cell（评论区 / 余下N条）
        if s in {_lang(MOMENTS, '评论'), _lang(MOMENTS, '评论区')}:
            return False
        if re.match(r'^(余下|餘下|Remaining)', s):
            return False

        # 广告标记：可能出现在行首（广告 cell）或正文后
        if _lang(MOMENTS, '广告') in s:
            self.is_advertisement = True
            s = s.replace(_lang(MOMENTS, '广告'), '').strip()

        # 1) 时间（取最靠右的匹配，通常在行尾）
        time_hit = _find_time_tail(s)
        if time_hit:
            self.time = time_hit.group(0)
            s = s[:time_hit.start()].rstrip()

        # 2) 媒体标记（图片数 / 视频）
        mm = re.search(_lang(MOMENTS, 're_图片数'), s)
        if mm:
            count = re.findall(r'\d+', mm.group())
            if count:
                self.image_count = int(count[0])
            s = s[:mm.start()] + s[mm.end():]

        # 视频标记（中文硬编码，与微信 UI 一致）
        mv = re.search(r'视频', s)
        if mv:
            s = s[:mv.start()] + s[mv.end():]

        # 3) 昵称 + 正文片段（空格分隔：第一块昵称，其余为正文片段）
        tokens = s.split()
        if not tokens:
            self.nickname = ''
            return True
        self.nickname = tokens[0]
        rest = ' '.join(tokens[1:])

        # 4) 内容片段：微信 UIA 把长文截为省略，片段含关键字可用于 keyword 定位
        if rest:
            self.content = rest.strip()

        return True

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
        counter = [0]

        def _walk(node, depth):
            counter[0] += 1
            if counter[0] > 20000 or depth > 30:
                return None
            try:
                ctrl_type = node.ControlTypeName or ''
                cls = (node.ClassName or '') or ''
                aid = (node.AutomationId or '') or ''
            except Exception:
                return None
            # 微信 4.x：时间线列表为 mmui::TimeLineListView（位于独立 SNSWindow 内）
            if ctrl_type == 'ListControl' and ('TimeLineListView' in cls
                                               or 'Moment' in cls
                                               or 'moment' in aid.lower()):
                wxlog.debug(f'找到朋友圈时间线列表控件：{cls}')
                return node
            try:
                kids = node.GetChildren()
            except Exception:
                return None
            # 朋友圈列表一般会包含"评论"按钮（作为列表的直接子元素）
            if ctrl_type == 'ListControl':
                for child in kids:
                    try:
                        if getattr(child, 'Name', '') == _lang(MOMENTS, '评论'):
                            wxlog.debug('通过子元素匹配到朋友圈列表控件')
                            return node
                    except Exception:
                        continue
            for kid in kids:
                found = _walk(kid, depth + 1)
                if found is not None:
                    return found
            return None

        try:
            root_control = parent.control  # mmui::SNSContentView / SNSWindow
        except Exception:
            root_control = None
        if root_control is None:
            wxlog.debug('未能定位到朋友圈列表控件')
            return None
        return _walk(root_control, 0)

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
                if not self.control.Exists(1.0):
                    return self._items
                children = self.control.GetChildren()
            except Exception:
                children = []

            for child in children:
                try:
                    if child.ControlTypeName in {'ListItemControl', 'CustomControl'}:
                        text = getattr(child, 'Name', '') or ''
                        if text.strip():
                            cls_name = getattr(child, 'ClassName', '') or ''
                            if _is_layout_decoration_cell(cls_name, text):
                                continue
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
        """定位朋友圈时间线所在窗口/容器控件。

        兼容两种布局：

        - 旧版独立窗口：朋友圈时间线是顶层 ``mmui::SNSWindow``。
        - 新版合并布局：朋友圈内容嵌入主窗口，成为主窗口子树里的
          ``mmui::SNSContentView``（内有 ``mmui::TimeLineListView``）。

        先按顶层窗口扫描，找不到再回退从主窗口子树 BFS 定位
        ``SNSContentView``，保证两种布局都能把时间线容器交还给
        :class:`MomentList` 复用后续逻辑。
        """
        t0 = time.time()
        pid = None
        try:
            mw_hwnd = self._wx._gui.main_hwnd
            if mw_hwnd:
                import ctypes
                _pid = ctypes.c_ulong()
                ctypes.windll.user32.GetWindowThreadProcessId(ctypes.c_void_p(mw_hwnd), ctypes.byref(_pid))
                pid = int(_pid.value or 0) or None
        except Exception:
            pid = None
        while time.time() - t0 < timeout:
            # 合并布局：直接从主窗口子树 BFS 定位 SNSContentView / TimeLineListView
            ctl = self._main_window_control()
            if ctl is not None:
                sc = self._bfs_control(ctl, 'SNSContentView', max_nodes=20000, max_depth=30)
                if sc is not None:
                    return sc
                tl = self._bfs_control(ctl, 'TimeLineListView', max_nodes=20000, max_depth=30)
                if tl is not None:
                    return tl
            time.sleep(0.3)
        return None

    def _main_window_control(self) -> Optional[uia.Control]:
        """从微信主窗口句柄锚定 UIA 控件（合并布局回退用）。"""
        try:
            mw = self._wx._gui.main_hwnd
        except Exception:
            return None
        if not mw:
            return None
        try:
            from uiautomation import ControlFromHandle
            return ControlFromHandle(mw)
        except Exception:
            return None

    @staticmethod
    def _bfs_control(root: 'uia.Control', class_name: str,
                     max_nodes: int = 2000, max_depth: int = 20) -> Optional['uia.Control']:
        """在控件子树里查找 ``class_name``（包含匹配）的控件。

        uiautomation 的包装对象每次 ``GetChildren()`` 返回新引用，不易用
        ``id()`` 去重，且子控件需从父节点即时取得才能可靠展开；这里用朴素
        递归深度优先遍历（与探针已验证一致的行为），以 ``max_nodes`` 限制
        总访问量、``max_depth`` 限制深度，避免爬满整棵微信控件树。
        """
        counter = [0]

        def _walk(node, depth):
            counter[0] += 1
            if counter[0] > max_nodes or depth > max_depth:
                return None
            try:
                cls = getattr(node, 'ClassName', '') or ''
            except Exception:
                cls = ''
            if class_name in cls:
                return node
            try:
                children = node.GetChildren()
            except Exception:
                children = []
            for ch in children:
                hit = _walk(ch, depth + 1)
                if hit is not None:
                    return hit
            return None

        return _walk(root, 0)

    def _ensure_list(self) -> Optional[MomentList]:
        if self._list and self._list.exists(0):
            return self._list

        try:
            self._wx.SwitchToMoments()
        except Exception:
            wxlog.debug('切换到朋友圈页面失败，继续尝试定位已打开的时间线')

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


    def _scroll_to_top(self, steps: int = 20, chunk: int = 5) -> None:
        """向上滚动到时间线顶部，作为定位的参考起点。

        逐块上滚并在「顶部指纹不再变化」（已到顶）时提前停止：
        否则已经在顶部时仍空翻 20 格——用户反馈「最开始在顶部还往上翻」。
        """
        remain = max(1, steps)
        prev = self._visible_fingerprint()
        while remain > 0:
            take = min(chunk, remain)
            self._scroll(delta=120, times=take)
            remain -= take
            time.sleep(0.35)
            cur = self._visible_fingerprint()
            if cur is not None and cur == prev:
                return          # 滚不动了 = 已在顶部
            prev = cur
        time.sleep(0.2)


    def _visible_fingerprint(self, items=None) -> Optional[str]:
        """顶部若干 cell 的稳定指纹（含几何位置），用于判断是否真的滚动了。

        重要：微信合并布局下整屏复用 ListItem，**Name 可能完全相同**；
        只用 Name 会把“真实滚动了”误判成“卡死”，导致翻到一半就放弃。
        因此指纹 = Name + 包围盒(top:bottom:left)，真的滚了就会变。
        """
        if items is None:
            items = self._read_visible_items(refresh=True)
        parts = []
        for it in items[:3]:
            try:
                name = ''.join((it.control.Name or '').split())[:40]
            except Exception:
                name = ''
            try:
                br = it.control.BoundingRectangle
                geo = '%d:%d:%d' % (br.top, br.bottom, br.left)
            except Exception:
                geo = ''
            parts.append(name + '#' + geo)
        return '|'.join(parts) or None


    @staticmethod
    def _notches_for(px: float, per_notch: int = 55, hi: int = 8) -> int:
        """把需要滚动的像素换算成滚轮格数（1~hi 格）。

        原先固定 1 格/次，对高动态（长文+图+评论）距离不够，
        导致目标或 “…” 按钮迟迟进不了视口；这里下调 px/格 并抬高上限，
        让接近目标时也能一次滚够。
        """
        try:
            n = int(abs(float(px)) // max(1, per_notch)) + 1
        except Exception:
            n = 1
        return max(1, min(hi, n))


    def _read_visible_items(self, refresh: bool = True) -> List[MomentItem]:
        lst = self._ensure_list()
        if not lst:
            return []
        return lst.get_items(refresh=refresh)

    def _scroll_item_fully_visible(self, publisher: Optional[str] = None,
                                   keyword: Optional[str] = None,
                                   max_retry: int = 10) -> Optional[MomentItem]:
        """把目标朋友圈滚入视野，返回刷新后的完整 cell。

        停止判据（用户指定）：**下一条朋友圈的 UIA 控件出现在本条下方**时
        立即停止翻动（`_has_next_moment_below`，自带视口过滤与不同发布者
        校验）——能看见下一条，就说明本条（含 “…” 按钮/评论区）已完整露出。
        仅当目标已是最后一条（永远没有“下一条”）时，才退回像素余量兜底。

        Returns:
            刷新后完整可见的目标 :class:`MomentItem`；失败返回 None。
        """
        rect = self._time_line_rect()
        if not rect:
            return None
        vleft, vtop, vright, vbottom = rect
        view_h = max(1, vbottom - vtop)
        margin = 40          # 兜底（目标已是最后一条、永远没有“下一条”时）用的底部余量
        no_next = 0
        for _ in range(max_retry):
            item = None
            for it in self._read_visible_items(refresh=True):
                if self._matches(it, publisher, keyword):
                    item = it
                    break
            if item is None:
                return None
            try:
                br = item.control.BoundingRectangle
                top, bottom = br.top, br.bottom
            except Exception:
                return item
            # 主判据（用户指定）：**下一条朋友圈的 UIA 已出现在本条下方** →
            # 说明本条（含 “…” 按钮/评论区）已完整露出，立即停止翻动。
            # 该判据自带视口过滤与“不同发布者”校验，且成立时本条底边必然
            # 落在视口内，比按像素余量猜更可靠。
            try:
                if self._has_next_moment_below(item, bottom):
                    wxlog.debug('下一条朋友圈已出现，停止翻动')
                    return item
            except Exception:
                pass
            # 兜底：目标已是最后一条（永远没有下一条）时，退回像素余量判据
            roomy = (bottom <= vbottom - margin and
                     ((bottom - top) > view_h or top >= vtop))
            if roomy:
                no_next += 1
                if no_next >= 2:
                    wxlog.debug('下方无下一条朋友圈（末条），按底部余量判据停止')
                    return item
            else:
                no_next = 0
            # 未到位：向下滚，把本条底边继续上移、把“下一条”带进视口；
            # 若本条顶边还被裁着（翻过头且不高于视口），先向上拉回。
            if top < vtop and (bottom - top) <= view_h:
                self._scroll(delta=120, times=self._notches_for(vtop - top))
            else:
                need = max(0, bottom - (vbottom - margin))
                self._scroll(delta=-120,
                             times=self._notches_for(need) if need > 0 else 2)
            time.sleep(0.25)
        return None


    def _db_posts(self, db, limit: int = 500) -> List[dict]:
        """取数据库朋友圈有序列表（最新在前），供标尺对齐。

        默认最多拉 500 条（避免 limit=0 全量拉取导致卡死）。
        """
        try:
            return list(db.get_moments(limit=limit))
        except Exception:
            return []

    def _signature(self, nickname: str, text: str) -> str:
        """构造模糊签名用于 UIA↔DB 对齐：昵称 + 正文前若干字。"""
        norm = lambda s: ''.join((s or '').split()) or ''
        return (norm(nickname) + '|' + norm(text)[:12]).lower()

    def _db_pos(self, db_posts: List[dict], item: MomentItem) -> Optional[int]:
        """把 UIA 单元格对齐到数据库索引位置。

        先按「DB 条目的昵称+正文文本是否连续出现在 cell 的 UIA 摘要里」
        做全文子串匹配（兼容昵称含空格时摘要解析被拆散的情况）；成功时
        顺便校正 item 的 nickname/text（用 DB 中的权威昵称边界）。失败再
        退回「昵称+正文前缀」签名匹配。
        """
        norm = lambda s: ''.join((s or '').split()) or ''
        try:
            raw_name = norm(item.control.Name or '')
        except Exception:
            raw_name = ''
        # 1) DB 全文作为 cell 摘要的连续子串（去空格后比对）
        if raw_name:
            for i, f in enumerate(db_posts):
                db_full = norm((f.get('nickname', '') or '') + ' ' + (f.get('text', '') or ''))
                if db_full and (db_full in raw_name or raw_name.startswith(db_full)):
                    # 校正昵称/正文（用 DB 权威边界）
                    db_nick = f.get('nickname', '') or ''
                    db_text = f.get('text', '') or ''
                    if db_nick:
                        item.nickname = db_nick
                    item.content = db_text
                    return i
        # 2) 精确签名
        try:
            nickname = item.publisher or ''
            text = item.text or ''
        except Exception:
            return None
        n_nick = norm(nickname)
        n_text = norm(text)
        if n_nick:
            for i, f in enumerate(db_posts):
                if self._signature(f.get('nickname', ''), f.get('text', '')) == self._signature(nickname, text):
                    return i
        # 3) 退化：同昵称。仅在正文够长（防止把纯图片/短文案错配到任意旧条目）
        #    且命中的候选里，**返回该昵称在 db 中最新（index 最小）的一条**。
        #    微信时间线同昵称按时间倒序连续排列，屏幕顶部该昵称动态应对应其
        #    db 最新一条；取 index 最小者即可避免在“文本为空 / 正文过短”时
        #    全局错配导致 pos 跳变、方向误判（滚动震荡）。
        if len(n_text) >= 6:
            best: Optional[int] = None
            for i, f in enumerate(db_posts):
                fn_nick = norm(f.get('nickname', ''))
                if not (fn_nick and n_nick and (fn_nick == n_nick
                        or fn_nick.startswith(n_nick) or n_nick.startswith(fn_nick))):
                    continue
                f_text = norm(f.get('text', ''))
                if n_text[:10] in f_text or f_text[:10] in n_text:
                    if best is None or i < best:
                        best = i
            if best is not None:
                return best
        return None

    def _correct_nickname_from_db(self, db_posts: List[dict], item: MomentItem) -> None:
        """用 DB 全文子串反查校正 item 的 nickname/text（OpenClaw 建议的昵称边界修复）。

        只在 DB 可用时由外部（如 find_moment）调用；纯 UIA 路径不依赖此方法。
        """
        norm = lambda s: ''.join((s or '').split()) or ''
        try:
            raw_name = norm(item.control.Name or '')
        except Exception:
            raw_name = ''
        if not raw_name:
            return
        for f in db_posts:
            db_full = norm((f.get('nickname', '') or '') + ' ' + (f.get('text', '') or ''))
            if db_full and (db_full in raw_name or raw_name.startswith(db_full)):
                db_nick = f.get('nickname', '') or ''
                db_text = f.get('text', '') or ''
                if db_nick:
                    item.nickname = db_nick
                item.content = db_text
                return

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
            # 先用数据库标尺判断目标在当前视野的上方还是下方：目标在**下方**
            # 时直接向下滚，不必先翻到顶部（用户反馈：开始时向上翻了几下，
            # 但目标其实在下面）。
            skip_top = False
            if target_idx is not None:
                try:
                    items0 = self._read_visible_items(refresh=True)
                except Exception:
                    items0 = []
                pos0 = None
                for it in (items0 or []):
                    pos0 = self._db_pos(db_posts, it)
                    if pos0 is not None:
                        break
                if pos0 is not None and target_idx > pos0:
                    skip_top = True
                    wxlog.debug(
                        f'目标在下方（target={target_idx} > 当前 pos={pos0}），跳过滚到顶部')
            if not skip_top:
                self._scroll_to_top()

        # 方向震荡防护（沿用既有做法：稳定对齐 + 单向收敛，不来回翻）：
        #   1) 顶部 cell 连续多屏指纹不变 → 视为到底/加载失败，先反向解锁
        #      一次，仍不变则放弃，避免空转 max_screens（原注释只声明未实现）；
        #   2) 反向次数上限 max_reversals：往回滚超过上限即改为**只向下**
        #      扫描到尽头，杜绝“上翻几下又下翻几下”；
        #   3) |diff|<=proximity 时若仍未命中，用发布者兜底命中对齐条；
        #      连续多屏都命中不了则停止，不再掉头。
        stall_key = None
        stall_count = 0
        unlock_tried = False
        max_reversals = 1
        reversals = 0
        near_miss = 0
        downward_only = False
        last_delta = None

        # 注：顶部指纹复用 _visible_fingerprint()，与 _scroll_to_top 一致

        for screen in range(max_screens):
            items = self._read_visible_items(refresh=True)
            if not items:
                break

            # 0) 卡死检测：顶部指纹连续不变 = 到底/未加载
            key = self._visible_fingerprint(items)
            if key is not None and key == stall_key:
                stall_count += 1
            else:
                stall_count = 0
                stall_key = key
            if stall_count >= 3:
                if not unlock_tried:
                    unlock_tried = True
                    # 反向解锁：与最近一次滚动方向相反（顶部卡死时上滚无意义）
                    undo = -last_delta if last_delta else -120
                    wxlog.debug(f'滚动定位疑似到底/未加载，反向解锁一次（delta={undo}）')
                    self._scroll(delta=undo, times=3)
                    last_delta = undo
                    time.sleep(0.3)
                    continue
                wxlog.debug('滚动定位卡死（顶部 cell 多屏不变），放弃')
                break

            # 1) 目标当前可见 -> 命中
            for item in items:
                if self._matches(item, publisher, keyword):
                    wxlog.debug(f'第 {screen} 屏命中目标朋友圈')
                    if db_posts:
                        self._correct_nickname_from_db(db_posts, item)
                    return item

            # 2) 无 DB：只能向下逐屏
            if target_idx is None:
                self._scroll(delta=-120, times=3)
                last_delta = -120
                time.sleep(0.25)
                continue

            # 3) 对齐当前顶部 cell 到 DB 索引（先 items[0]，失败再试可见项）
            pos = self._db_pos(db_posts, items[0])
            if pos is None:
                for it in items:
                    pos = self._db_pos(db_posts, it)
                    if pos is not None:
                        break

            diff = target_idx - pos if pos is not None else None
            if diff is None:
                # 顶部对不上，保守向下滚一屏
                self._scroll(delta=-120, times=3)
                last_delta = -120
                time.sleep(0.25)
                continue

            wxlog.debug(f'当前 pos={pos} 目标={target_idx} diff={diff} 屏={screen}')

            adiff = abs(diff)
            direction = 1 if diff >= 0 else -1   # +1=向下(更旧)  -1=向上(更新)

            # 3.1) 已在目标下方：只允许有限次反向，超过即改单向向下，
            #      否则对齐抖动会让方向反复翻转（上翻几下又下翻几下）
            if direction < 0:
                if downward_only or reversals >= max_reversals:
                    wxlog.debug('反向次数达上限，改为单向向下扫描')
                    downward_only = True
                    self._scroll(delta=-120, times=2)
                    last_delta = -120
                    time.sleep(0.25)
                    continue
                reversals += 1

            # 3.2) 贴近目标索引：不再掉头，用发布者兜底命中；连续失败则停止
            if adiff <= proximity:
                for it in items:
                    if publisher and self._matches(it, publisher, None):
                        wxlog.debug(f'第 {screen} 屏按发布者兜底命中（diff={diff}）')
                        if db_posts:
                            self._correct_nickname_from_db(db_posts, it)
                        return it
                near_miss += 1
                if near_miss >= 4:
                    if downward_only:
                        wxlog.debug('贴近目标索引但连续多屏未命中且已单向，停止滚动')
                        break
                    # 不急着放弃：对齐偏差可能把 pos 拉偏，改为单向向下继续找
                    wxlog.debug('贴近目标索引但未命中，改为单向向下继续找')
                    downward_only = True
                    near_miss = 0
            else:
                near_miss = 0

            # 4) 按距离自适应步长；接近时缩小步长精确逼近（同时加快节奏）
            if adiff <= proximity:
                # 接近目标：多滚一档避免“还差一次滚动”，并加快节奏
                times, delta = 2, (-120 if direction > 0 else 120)
                sleep = 0.25
            elif adiff <= 20:
                times, delta, sleep = 3, (-120 if direction > 0 else 120), 0.25
            elif adiff <= 60:
                times, delta, sleep = 5, (-120 if direction > 0 else 120), 0.22
            else:
                times, delta, sleep = 7, (-120 if direction > 0 else 120), 0.2
            if downward_only:
                delta = -120
            self._scroll(delta=delta, times=times)
            last_delta = delta
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

    def _match_template_multi(self, scr, tpl, rleft, rtop, rw, rh,
                              scales=None, min_ncc: float = 0.65) -> Optional[tuple]:
        """多尺度模板匹配，返回 (ncc, center_x, center_y, scale) 或 None。

        在该方法之前调用方应保证 rleft/rtop/rw/rh 已裁剪到截图范围内。
        坐标系说明：scr 为物理像素截图（如 pyautogui 3072x1920），
        tpl 来自 assets（采集时即物理像素），两者同源即可直接匹配；
        BoundingRectangle 亦为物理像素（已用 OCR 交叉验证），无需换算。
        """
        try:
            import cv2
            import numpy as np
        except Exception as e:
            wxlog.debug(f'cv2/numpy 不可用：{e}')
            return None
        if scales is None:
            scales = (0.6, 0.7, 0.8, 0.9, 1.0, 1.05, 1.1, 1.15, 1.2)
        sub = scr[rtop:rtop + rh, rleft:rleft + rw]
        best = (0.0, None)
        for s in scales:
            tw, th = int(tpl.shape[1] * s), int(tpl.shape[0] * s)
            if tw < 8 or th < 8 or tw >= sub.shape[1] or th >= sub.shape[0]:
                continue
            tmp = cv2.resize(tpl, (tw, th), interpolation=cv2.INTER_AREA)
            res = cv2.matchTemplate(sub, tmp, cv2.TM_CCOEFF_NORMED)
            _, mx, _, mxloc = cv2.minMaxLoc(res)
            if mx > best[0]:
                cx = rleft + mxloc[0] + tw // 2
                cy = rtop + mxloc[1] + th // 2
                best = (mx, (int(cx), int(cy), s))
        if best[0] < min_ncc:
            return None
        ncc, (cx, cy, scale) = best
        return (ncc, cx, cy, scale)

    def _find_more_button(self, item: MomentItem,
                          region: Optional[tuple] = None) -> Optional[tuple]:
        """在 cell 右下角用模板匹配定位 “…” 按钮，返回其中心 (x, y) 或 None。

        Args:
            item: 目标朋友圈项（须有 .control 的 BoundingRectangle）。
            region: 可选自定义搜索区域 (left, top, width, height)；缺省用 cell 区域。
        """
        try:
            import pyautogui
            import numpy as np
        except Exception as e:
            wxlog.debug(f'pyautogui/numpy 不可用：{e}')
            return None
        br = item.control.BoundingRectangle
        theme = self._theme((br.left, br.top, br.right, br.bottom))
        tpls = [self._more_template(theme), self._more_template('light' if theme == 'dark' else 'dark')]
        try:
            import cv2
        except Exception as e:
            wxlog.debug(f'cv2 不可用：{e}')
            return None

        if region is not None:
            rleft, rtop, rw, rh = region
        else:
            left, top, right, bottom = br.left, br.top, br.right, br.bottom
            rleft, rtop = max(0, left - 30), max(0, top - 10)
            rw = (right - left) + 60
            rh = (bottom - top) + 20
        shot = pyautogui.screenshot()
        scr = np.array(shot.convert('RGB'))[:, :, ::-1]
        rw = min(rw, max(1, scr.shape[1] - rleft))
        rh = min(rh, max(1, scr.shape[0] - rtop))
        if rw < 16 or rh < 16:
            return None
        best = None
        for tp in tpls:
            if not tp:
                continue
            tpl = cv2.imread(tp)
            if tpl is None:
                continue
            hit = self._match_template_multi(scr, tpl, rleft, rtop, rw, rh)
            if hit and (best is None or hit[0] > best[0]):
                best = hit
        if best is None:
            wxlog.debug(f'“…” 模板多尺度匹配失败（region=({rleft},{rtop},{rw},{rh})）')
            return None
        ncc, cx, cy, scale = best
        wxlog.debug(f'“…” 匹配 NCC={ncc:.3f} scale={scale:.2f} center=({cx},{cy})')
        return (cx, cy)

    def _float_region_shot(self, item: MomentItem) -> tuple:
        """拍摄浮层候选区域，返回 (region, PIL Image) 供点击前后对比。"""
        try:
            import pyautogui
        except Exception as e:
            wxlog.debug(f'pyautogui 不可用：{e}')
            return None
        br = item.control.BoundingRectangle
        left, top, right, bottom = br.left, br.top, br.right, br.bottom
        w, h = right - left, bottom - top
        sw, sh = pyautogui.size()
        rl, rt = max(0, right - 120), max(0, top)
        rw = min(200, max(1, sw - rl))
        rh = min(h + 100, max(1, sh - rt))
        region = (rl, rt, rw, rh)
        return (region, pyautogui.screenshot(region=region))

    def _float_region_diff(self, before_shot) -> float:
        """对比点击前后浮层候选区域，返回像素变化比例（0~1）。

        浮层为自绘覆盖层（UIA 不可见），用点击前后截图 diff 判断是否弹出。
        """
        if before_shot is None:
            return 0.0
        try:
            import pyautogui
            import numpy as np
        except Exception as e:
            wxlog.debug(f'pyautogui/numpy 不可用：{e}')
            return 0.0
        region, before_img = before_shot
        after = pyautogui.screenshot(region=region)
        a = np.asarray(before_img.convert('L'), dtype=np.int16)
        b = np.asarray(after.convert('L'), dtype=np.int16)
        changed = int((np.abs(a - b) > 30).sum())
        return changed / float(max(1, region[2] * region[3]))

    def _ensure_window_foreground(self, timeout: float = 2.0) -> bool:
        """确保微信主窗口为前台窗口。

        微信窗口非前台时，第一次 click 往往只用来激活窗口而被吞掉。
        先检查 GetForegroundWindow 是否等于主窗口句柄；不是则调用
        SetForegroundWindow，并再做一次窗口内空白点击确认前台。
        """
        import ctypes
        user32 = ctypes.windll.user32
        try:
            hwnd = self._wx._gui.main_hwnd
        except Exception as e:
            wxlog.debug(f'获取主窗口句柄失败：{e}')
            return False
        if not hwnd:
            return False
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                fg = user32.GetForegroundWindow()
                if fg == hwnd:
                    return True
            except Exception:
                pass
            try:
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            time.sleep(0.3)
        # 兜底：点击窗口标题栏（顶部）激活，避免误触内容区
        try:
            import pyautogui
            r = user32.GetWindowRect(hwnd)
            left, top = max(0, r[0]), max(0, r[1])
            right, bottom = r[2], r[3]
            if right - left < 100 or bottom - top < 100:
                return False
            x = int((left + right) // 2)
            y = int(top + 25)
            pyautogui.FAILSAFE = False
            pyautogui.click(x, y)
            time.sleep(0.5)
            return True
        except Exception as e:
            wxlog.debug(f'前台激活兜底点击失败：{e}')
            return False

    def _try_invoke_cell_button(self, item: MomentItem) -> bool:
        """在 cell 内找可 Invoke/Click 的 ButtonControl 并直接触发一次。

        某些新版微信 self-drawn 的 “…” 按钮在 UIA 里是真实 ButtonControl，
        但屏幕坐标点击可能因 focus 等原因失效；这里直接走 UIA 模式触发。
        """
        try:
            for child in item.control.GetChildren():
                try:
                    if child.ControlTypeName != 'ButtonControl':
                        continue
                    if not getattr(child, 'IsVisible', True):
                        continue
                except Exception:
                    continue
                try:
                    inv = child.GetPattern('InvokePattern')
                    if inv is not None:
                        inv.Invoke()
                        return True
                except Exception:
                    pass
                try:
                    child.Click()
                    return True
                except Exception:
                    pass
        except Exception:
            return False
        return False

    def _nudge_to_reveal(self, item: MomentItem) -> tuple:
        """微调滚动让目标 cell 完整进入视口，返回 (delta, times)。

        原先固定“向下滚 1 格”，对高动态（长文+图+评论）距离不够，导致
        “…” 按钮始终进不了视口。这里按 cell 与时间线视口的位置关系决定
        方向与格数：底部被裁→向下滚；顶部被裁→向上滚；已完整可见则小步
        下移换屏继续找。
        """
        vp = self._time_line_rect()
        try:
            br = item.control.BoundingRectangle
            top, bottom = br.top, br.bottom
        except Exception:
            return -120, 3
        if vp is None:
            return -120, 3
        vtop, vbottom = vp[1], vp[3]
        margin = 40          # “…” 按钮贴边会点不到：多留余量（相当于多滚 1 格）
        if top < vtop:
            return 120, self._notches_for(vtop - top + margin, hi=6)
        if bottom > vbottom - margin:
            return -120, self._notches_for(bottom - vbottom + margin, hi=6)
        return -120, 2

    def _locate_more_click(self, item: MomentItem, max_retry: int = 8) -> bool:
        """定位并点击目标朋友圈的 “…” 按钮，弹出点赞/评论浮层。

        未识别到 “…” 时微调滚动让该条完整进入视野后重试，最多 max_retry 次。
        每次点击前先确保微信主窗口为前台窗口（否则第一次 click 只激活窗口
        会被吞掉、浮层不弹）。点击后必须确认浮层已弹出（UIA
        `_find_float_button('赞'/'评论')` 或 before/after 像素 diff），
        确认不了就继续重试，最终仍确认不了则返回 False，绝不静默返回 True。

        Returns:
            True 表示浮层已确认弹出。
        """
        if max_retry < 1:
            return False
        for attempt in range(max_retry):
            pt = self._find_more_button(item)
            if pt is not None:
                before_shot = self._float_region_shot(item)
                try:
                    import pyautogui
                except Exception as e:
                    wxlog.debug(f'pyautogui 不可用：{e}')
                    return False
                if not self._ensure_window_foreground():
                    wxlog.debug(f'未能确认微信窗口为前台（第 {attempt + 1} 次）')
                # moveTo + 短暂停留 + down/up，避免点击过快被丢弃
                try:
                    pyautogui.moveTo(pt[0], pt[1], duration=0.1)
                    time.sleep(0.15)
                    pyautogui.FAILSAFE = False
                    pyautogui.mouseDown()
                    time.sleep(0.05)
                    pyautogui.mouseUp()
                except Exception as e:
                    wxlog.debug(f'点击 “…” 失败：{e}')
                    time.sleep(1.2)
                    continue
                time.sleep(1.2)

                def _verify_float():
                    diff = self._float_region_diff(before_shot)
                    if diff >= 0.05:
                        return True, f'像素变化比例={diff:.3f}'
                    if self._find_float_button('赞', timeout=0.8) is not None or \
                            self._find_float_button('评论', timeout=0.8) is not None:
                        return True, 'UIA 找到 “赞/评论”'
                    return False, '未见浮层'

                ok, why = _verify_float()
                if ok:
                    wxlog.debug(f'点击 “…” 后 {why}（第 {attempt + 1} 次）')
                    return True
                wxlog.debug(f'点击 “…” 后 {why}（第 {attempt + 1} 次），尝试 hover 展开')
                # hover 展开：某些新版微信 “…” 是 hover 触发
                try:
                    pyautogui.moveTo(pt[0], pt[1], duration=0.2)
                    time.sleep(1.5)
                except Exception:
                    pass
                ok, why = _verify_float()
                if ok:
                    wxlog.debug(f'hover 后 {why}（第 {attempt + 1} 次）')
                    return True
                wxlog.debug(f'hover 后 {why}（第 {attempt + 1} 次），尝试 UIA Invoke')
                # UIA 直接 Invoke cell 内按钮
                if self._try_invoke_cell_button(item):
                    time.sleep(0.8)
                    ok, why = _verify_float()
                    if ok:
                        wxlog.debug(f'UIA Invoke 后 {why}（第 {attempt + 1} 次）')
                        return True
                wxlog.debug(f'Invoke 后 {why}（第 {attempt + 1} 次），重试')
            else:
                wxlog.debug(f'未识别到 “…”（第 {attempt + 1} 次），微调滚动')
            _d, _t = self._nudge_to_reveal(item)
            self._scroll(delta=_d, times=_t)
            time.sleep(0.25)
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


    def _comment_open(self) -> bool:
        """在 “…” 浮层已弹出的前提下，点击浮层里的「评论」。

        点击后微信会在该动态下方/底部弹出评论输入框。
        """
        return self._click_float_button('评论', timeout=2.5)

    def _comment_input_focus(self) -> bool:
        """聚焦评论输入框（尽力而为，失败不致命）。

        新版本评论输入框为自绘控件，可能没有 UIA EditControl；此时假定
        点击「评论」后输入框已自动聚焦，直接进入输入阶段即可。

        如果 EditControl 存在但 BoundingRectangle 为零（自绘控件 UIA 无法
        获取屏幕坐标），同样假定已自动聚焦，返回 False 以示区分。
        """
        try:
            root = uia.GetRootControl()
            hit = self._find_edit_control(root, max_depth=30)
            if hit is None:
                wxlog.debug('未找到评论输入框 EditControl（可能为自绘控件）')
                return False
            r = hit.BoundingRectangle
            cx, cy = int((r.left + r.right) // 2), int((r.top + r.bottom) // 2)
            if cx == 0 and cy == 0:
                wxlog.debug('评论输入框 EditControl 坐标为零，跳过手动聚焦')
                return False
            import pyautogui
            pyautogui.click(cx, cy)
            time.sleep(0.3)
            return True
        except Exception:
            return False

    @staticmethod
    def _find_edit_control(ctrl, target_text: str = '评论', max_depth: int = 30, depth: int = 0):
        """深度优先搜索可见的评论输入框（EditControl，Name 含“评论”或为空）。"""
        if depth > max_depth:
            return None
        try:
            if ctrl.ControlTypeName == 'EditControl':
                name = ctrl.Name or ''
                if target_text in name or name == '':
                    try:
                        if ctrl.IsVisible:
                            return ctrl
                    except Exception:
                        return ctrl
        except Exception:
            pass
        try:
            kids = ctrl.GetChildren()
        except Exception:
            kids = []
        for kid in kids:
            r = Moment._find_edit_control(kid, target_text, max_depth, depth + 1)
            if r is not None:
                return r
        return None

    def _type_comment(self, content: str) -> bool:
        """把评论内容输入到评论框（剪贴板粘贴）。"""
        if not content:
            return False
        try:
            SetClipboardText(content)
            uia.SendKeys('{Ctrl}v')
            time.sleep(0.4)
            return True
        except Exception as e:
            wxlog.debug(f'输入评论内容失败：{e}')
            return False

    def _send_template(self, theme: str) -> Optional[str]:
        """返回「发送」按钮模板资源路径。

        发送按钮明暗两套主题颜色一致，始终使用 `moments_send.png`
        （必用 ASCII 文件名，OpenCV 在 Windows 上无法读中文路径）。
        """
        return _asset_path('moments_send.png')

    def _click_comment_send(self) -> WxResponse:
        """点击评论输入框的「发送」按钮（pyautogui 模板匹配）。

        新版「发送」按钮为自绘控件，没有 UIA 控件，只能靠模板匹配坐标点击。
        """
        try:
            import pyautogui
        except Exception as e:
            return WxResponse.failure(f'pyautogui 不可用：{e}')

        try:
            theme = self._comment_box_theme()
        except Exception:
            theme = 'light'
        tpl = self._send_template(theme)
        if not tpl:
            return WxResponse.failure('缺少「发送」按钮模板资源（assets/moments_send.png）')

        region = None
        rect = self._time_line_rect()
        if rect:
            left, top, right, bottom = rect
            rl = left
            rt = max(0, int(bottom) + 8)
            rw = (right - left)
            rh = 80
            region = (rl, rt, rw, rh)
        try:
            import numpy as np
            import cv2
            shot = pyautogui.screenshot()
            scr = np.array(shot.convert('RGB'))[:, :, ::-1]
            tpl_img = cv2.imread(tpl)
            if tpl_img is None:
                return WxResponse.failure(f'「发送」模板读取失败：{tpl}')
            if region is None:
                rl, rt = 0, 0
                rw, rh = scr.shape[1], scr.shape[0]
            else:
                rl, rt, rw, rh = region
                rl, rt = max(0, rl), max(0, rt)
                rw = min(rw, max(1, scr.shape[1] - rl))
                rh = min(rh, max(1, scr.shape[0] - rt))
            if rw < 16 or rh < 16:
                return WxResponse.failure('「发送」匹配区域过小')
            hit = self._match_template_multi(scr, tpl_img, rl, rt, rw, rh,
                                             scales=(0.7, 0.8, 0.9, 1.0, 1.1, 1.2),
                                             min_ncc=0.55)
        except Exception as e:
            wxlog.debug(f'识别「发送」按钮失败：{e}')
            return WxResponse.failure(f'识别「发送」按钮失败：{e}')
        if hit is None:
            wxlog.debug('屏幕中未识别到「发送」按钮')
            return WxResponse.failure('屏幕中未识别到「发送」按钮')
        ncc, x, y, scale = hit
        wxlog.debug(f'「发送」匹配 NCC={ncc:.3f} scale={scale:.2f} center=({x},{y})')
        try:
            old = pyautogui.FAILSAFE
            pyautogui.FAILSAFE = False
            try:
                pyautogui.click(x, y)
            finally:
                pyautogui.FAILSAFE = old
            time.sleep(0.5)
            return WxResponse.success('评论成功')
        except Exception as e:
            wxlog.debug(f'点击「发送」失败：{e}')
            return WxResponse.failure(f'点击「发送」失败：{e}')

    def _comment_box_theme(self) -> str:
        """采样评论输入框所在区域（时间线底部）判断深浅主题（像素）。"""
        try:
            import pyautogui
        except Exception:
            return 'light'
        rect = self._time_line_rect()
        if not rect:
            return 'light'
        left, top, right, bottom = rect
        x = int((left + right) // 2)
        y = int(top + (bottom - top) * 0.95)
        try:
            r, g, b = pyautogui.pixel(x, y)
        except Exception:
            return 'light'
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        return 'dark' if lum < 128 else 'light'

    def CommentMoment(self, publisher: Optional[str] = None,
                      keyword: Optional[str] = None, content: str = '',
                      db=None, max_screens: int = 300,
                      max_retry: int = 8) -> WxResponse:
        """一键：定位指定朋友圈 -> 点击 “…” -> 点「评论」 -> 输入内容 -> 发送。

        Args:
            publisher: 发布者昵称。
            keyword: 正文关键词。
            content: 评论内容。
            db / max_screens / max_retry: 定位参数。
        """
        if not publisher and not keyword:
            return WxResponse.failure('CommentMoment 缺少定位条件')
        if not content:
            return WxResponse.failure('评论内容不能为空')
        item = self.find_moment(publisher=publisher, keyword=keyword,
                                db=db, max_screens=max_screens)
        if item is None:
            return WxResponse.failure('未能定位到目标朋友圈')
        if not self._locate_more_click(item, max_retry=max_retry):
            return WxResponse.failure('未能打开 “…” 浮层')
        if not self._comment_open():
            return WxResponse.failure('未能在浮层中找到“评论”按钮')
        self._comment_input_focus()
        if not self._type_comment(content):
            return WxResponse.failure('输入评论内容失败')
        return self._click_comment_send()

    def ReplyCommentMoment(self, publisher: Optional[str] = None,
                           keyword: Optional[str] = None,
                           reply_to: Optional[str] = None,
                           content: str = '',
                           target_text: Optional[str] = None,
                           db=None, max_screens: int = 300,
                           max_retry: int = 8) -> WxResponse:
        """一键：定位指定朋友圈 -> 回复其某条评论（截图 OCR 定位 -> 点击 -> 输入 -> 发送）。

        Args:
            publisher: 发布者昵称。
            keyword: 正文关键词。
            reply_to: 被回复评论的作者昵称（OCR 匹配评论行前缀）。
            content: 回复内容。
            target_text: 可选，被回复评论正文（同作者多条时消歧）。
            db / max_screens / max_retry: 定位参数。
        """
        if not publisher and not keyword:
            return WxResponse.failure('ReplyCommentMoment 缺少定位条件')
        if not reply_to:
            return WxResponse.failure('ReplyCommentMoment 缺少被回复评论的作者')
        if not content:
            return WxResponse.failure('回复内容不能为空')
        item = self.find_moment(publisher=publisher, keyword=keyword,
                                db=db, max_screens=max_screens)
        if item is None:
            return WxResponse.failure('未能定位到目标朋友圈')
        full = self._scroll_item_fully_visible(
            publisher=publisher, keyword=keyword, max_retry=max_retry)
        if full is not None:
            item = full
        return self.ReplyComment(item, reply_to=reply_to, content=content,
                                 target_text=target_text)


    def GetComments(self, publisher: Optional[str] = None,
                    keyword: Optional[str] = None, db=None,
                    max_screens: int = 300, max_retry: int = 8,
                    as_tree: bool = False) -> WxResponse:
        """定位指定朋友圈并读取其全部可见评论（含回复）。

        评论直接取目标可见 cell 的 UIA 文本解析；单条评论若带“回复”，
        解析结果中 ``reply_to`` 记录被回复者昵称。

        Args:
            publisher: 发布者昵称。
            keyword: 正文关键词。
            db / max_screens / max_retry: 定位参数（沿用 find_moment）。
            as_tree: True 时额外返回按“回复上级”堆成的嵌套树（启发式）。

        Returns:
            WxResponse；成功时 ``data`` 含：
              publisher/content/time/likes/comment_count/comments（每条为
              {author, content, reply_to, raw}），as_tree=True 时另有 tree。
        """
        if not publisher and not keyword:
            return WxResponse.failure('GetComments 缺少定位条件')

        # ---- 优先：本地 DB 路线（最可靠，含完整回复关系，无 UIA 依赖）----
        if db is not None:
            resp = self._get_comments_db(publisher, keyword, db, as_tree)
            if resp is not None:
                return resp

        # ---- 降级：UIA 可见文本路线 ----
        item = self.find_moment(publisher=publisher, keyword=keyword,
                                db=db, max_screens=max_screens)
        if item is None:
            return WxResponse.failure('未能定位到目标朋友圈')
        full = self._scroll_item_fully_visible(
            publisher=publisher, keyword=keyword, max_retry=10)
        if full is not None:
            item = full
        try:
            item._ensure_parsed()
            comments = [
                {
                    'author': c.author,
                    'content': c.content,
                    'reply_to': c.reply_to,
                    'raw': c.raw,
                }
                for c in item.comments
            ]
        except Exception as e:
            return WxResponse.failure(f'解析评论失败：{e}')
        data = {
            'source': 'uia',
            'publisher': item.publisher,
            'content': item.text,
            'time': item.timestamp,
            'likes': list(item.likes),
            'comment_count': len(comments),
            'comments': comments,
        }
        if as_tree:
            data['tree'] = self._comments_tree(comments)
        if not comments:
            return WxResponse.failure('该朋友圈当前可见区无评论（可能被折叠）')
        return WxResponse.success(message=f'获取到 {len(comments)} 条评论', data=data)

    def _get_comments_db(self, publisher: Optional[str],
                         keyword: Optional[str], db,
                         as_tree: bool) -> Optional[WxResponse]:
        """从本地 DB 读取匹配朋友圈的完整评论；无匹配返回 None（交由上层降级）。"""
        feed = None
        # 快速路径：能反查到 wxid / keyword 都能下推到 SQL，避免全量解析数千条。
        if publisher:
            wxid = None
            try:
                base = getattr(db, 'db', None)
                wxid = base.username_by_nickname(publisher) if base else None
            except Exception:
                wxid = None
            if wxid is None and str(publisher).startswith('wxid_'):
                wxid = publisher
            if wxid:
                try:
                    feeds = list(db.get_moments(username=wxid, limit=1))
                    if feeds:
                        feed = feeds[0]
                except Exception as e:
                    wxlog.debug(f'DB 按 username 读取失败：{e}')
            if feed is None and not wxid:
                try:
                    feeds = db.get_moments(limit=0)
                    feed = next((f for f in feeds
                                 if f.get('nickname') == publisher), None)
                except Exception:
                    feed = None
        elif keyword:
            try:
                feeds = db.get_moments(keyword=keyword, limit=1)
                if feeds:
                    feed = feeds[0]
            except Exception as e:
                wxlog.debug(f'DB 按 keyword 读取失败：{e}')
        if feed is None:
            return None

        def resolve_reply(comment: dict) -> Optional[str]:
            try:
                target = db.comment_reply_to(feed, comment)
            except Exception:
                target = None
            return target.get('nickname') if target else None

        comments = []
        for c in (feed.get('comments') or []):
            comments.append({
                'author': c.get('nickname') or c.get('username', ''),
                'username': c.get('username', ''),
                'content': c.get('content', ''),
                'reply_to': resolve_reply(c),
                'create_time': c.get('create_time', 0),
                'comment_id': c.get('comment_id', ''),
                'ref_comment_id': c.get('ref_comment_id', ''),
            })

        likes = [u.get('nickname') or u.get('username', '')
                 for u in (feed.get('likes') or [])]
        data = {
            'source': 'db',
            'publisher': feed.get('nickname', ''),
            'username': feed.get('username', ''),
            'content': feed.get('text', ''),
            'time': feed.get('create_time', 0),
            'likes': likes,
            'comment_count': len(comments),
            'comments': comments,
        }
        if as_tree:
            data['tree'] = db.comment_tree(feed)
        return WxResponse.success(message=f'获取到 {len(comments)} 条评论', data=data)

    @staticmethod
    def _comments_tree(comments: List[dict]) -> List[dict]:
        """把扁平评论列表按“回复上级”堆成树（启发式）。

        仅当某条评论的 reply_to 等于前面某条评论的 author 时才视为子回复；
        其余均作为顶层评论。返回嵌套结构：
        [{author, content, reply_to, replies:[...]}, ...]
        """
        root: List[dict] = []
        by_author: Dict[str, dict] = {}
        nodes = []
        for c in comments:
            node = {
                'author': c.get('author', ''),
                'content': c.get('content', ''),
                'reply_to': c.get('reply_to'),
                'replies': [],
            }
            nodes.append(node)
            if node['author']:
                by_author.setdefault(node['author'], node)
        for c, node in zip(comments, nodes):
            parent = None
            if c.get('reply_to'):
                parent = by_author.get(c['reply_to'])
            if parent is not None:
                parent['replies'].append(node)
            else:
                root.append(node)
        return root


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

    # ----------------------------------------------------------------------------------------------
    # 回复指定评论（截图 OCR 定位评论行 → 点击 → 输入 → 发送）
    # ----------------------------------------------------------------------------------------------

    def ReplyComment(self, item: MomentItem, reply_to: Optional[str] = None,
                     content: Optional[str] = None,
                     target_text: Optional[str] = None,
                     expand: bool = True,
                     scan_screens: int = 8) -> WxResponse:
        """回复朋友圈某条评论（``reply_to`` 为被回复评论的作者昵称）。

        WeChat 4.1.13.12 评论区评论行为**纯自绘**、不在 UIA 树内，故本方法采用
        验证过的「截图 + 内置 OCR」定位：先定位目标朋友圈对应的「评论区」
        cell（ClassName 为 ``mmui::TimelineCommentCell``），截图后 OCR 出各评论行，
        按作者名匹配目标评论，点击其中心打开回复框，粘贴内容并点击「发送」。

        Args:
            item: 目标朋友圈（由 ``find_moment`` 返回的 :class:`MomentItem`）。
            reply_to: 被回复评论的作者昵称（精确匹配 OCR 行前缀）。
            content: 回复内容。
            target_text: 可选，被回复评论的正文（用于同作者多条评论时消歧）。
            expand: True 时若评论区折叠则先展开。

        Returns:
            :class:`WxResponse`；成功时 message 为“回复成功”。
        """
        if not content:
            return WxResponse.failure('ReplyComment 缺少回复内容')
        if not reply_to:
            return WxResponse.failure('ReplyComment 缺少被回复评论的作者')

        cell_box = self._locate_comment_cell(item)
        if cell_box is None:
            return WxResponse.failure('未能定位评论区控件')

        # 定位到朋友圈后先向下多滚一轮，让评论区尽早进入视野，
        # 减少 OCR 循环内的滚动次数。
        self._scroll_comments_down(item, cell_box, max_tries=2)
        time.sleep(0.3)
        cell_box = self._locate_comment_cell(item) or cell_box

        # 迭代：先向下滚动直到「下一条朋友圈」出现在评论区下方（滚动到位），
        # 确认到位后再截图 OCR 匹配目标评论。评论行为纯自绘、仅在完整展示时
        # 才能 OCR 读到，故必须先滚到「下一条朋友圈」出现、目标评论区完整露出，
        # 再识别；未命中再继续滚动，避免“没滚到位就提前 OCR”导致一直找不到。
        click_pos = None
        last_box = cell_box
        for screen in range(scan_screens):
            cell_box = self._locate_comment_cell(item) or last_box
            last_box = cell_box
            left, top, right, bottom = cell_box
            try:
                item._ensure_parsed()
                _nick = item.nickname or ''
            except Exception:
                _nick = ''
            _vp = self._parent_list_box(item)
            wxlog.debug(
                f'[scan {screen}] 昵称={_nick!r} box={cell_box} 视口={_vp} '
                f'下一条出现={self._has_next_moment_below(item, bottom)}')
            if (right - left) < 10 or (bottom - top) < 10:
                wxlog.debug(f'[scan {screen}] 评论区 box 异常，跳过：{cell_box}')
                time.sleep(0.3)
                continue
            # 1) 滚动直到「下一条（不同发布者）朋友圈」出现在评论区下方
            #    （= 评论区完整显示）；**不到位就绝不 OCR**，否则连评论区都
            #    没翻到就识别。
            if not self._has_next_moment_below(item, bottom):
                arrived = self._scroll_comments_down(item, cell_box, max_tries=6)
                wxlog.debug(f'[scan {screen}] 滚动到出现下一条朋友圈...={arrived}')
                time.sleep(0.4)
                cell_box = self._locate_comment_cell(item) or last_box
                left, top, right, bottom = cell_box
                last_box = cell_box
                if (right - left) < 10 or (bottom - top) < 10:
                    wxlog.debug(f'[scan {screen}] 滚动后评论区 box 异常，跳过：{cell_box}')
                    continue
                # 仍没凑齐「下一条」→ 本轮到此为止不 OCR，下轮继续滚动
                if not self._has_next_moment_below(item, bottom):
                    wxlog.debug(f'[scan {screen}] 评论区尚未完整显示，本轮不 OCR，继续滚动')
                    continue
            # 2) 已到位，截图 OCR 匹配目标评论
            try:
                from PIL import ImageGrab
                img = ImageGrab.grab(bbox=(left, top, right, bottom))
            except Exception as e:
                return WxResponse.failure(f'截图失败：{e}')
            from wechatauto.guia import ScreenOCR
            lines = ScreenOCR.recognize(img)
            wxlog.debug(f'[scan {screen}] box={cell_box} OCR共{len(lines)}行')
            for t, x, y, w, h in lines:
                wxlog.debug(f'    OCR: ({x},{y},{w},{h}) {t[:40]!r}')
            target = self._match_comment_line(lines, reply_to, target_text)
            if target is not None:
                # 点「评论内容」而非作者名：OCR 的 box 宽 w 常严重偏小（如 8 字的
                # `送你挖银子：测试` 只报 28px），据此点 x+w*0.9 会命中作者名。
                # 故用 _match_comment_line 返回的内容起点 c_left 与内容长度 rest_n，
                # 按「单字宽×字数」定位到内容**中点**。
                t_x, t_y, t_w, t_h, c_left, rest_n = target
                char_w = max(t_h, 12)
                cx0 = left + c_left + int(rest_n * char_w * 0.5)
                cy0 = top + t_y + t_h // 2
                click_pos = (cx0, cy0)
                wxlog.debug(f'[scan {screen}] 命中目标评论，落点(内容中点)={click_pos}')
                break
            # 未命中：继续向下滚动，让评论区/后续内容进入视野后下轮再 OCR
            arrived = self._scroll_comments_down(item, cell_box, max_tries=3)
            wxlog.debug(f'[scan {screen}] 未命中，继续滚动以露出评论区={arrived}')
            time.sleep(0.4)

        if click_pos is None:
            return WxResponse.failure(
                f'滚动评论区后仍未匹配到评论（作者={reply_to}）')

        cx, cy = click_pos
        try:
            import pyautogui
            old = pyautogui.FAILSAFE
            pyautogui.FAILSAFE = False
            try:
                pyautogui.click(cx, cy)
            finally:
                pyautogui.FAILSAFE = old
        except Exception as e:
            return WxResponse.failure(f'点击评论行失败：{e}')
        time.sleep(0.6)

        if not self._type_comment(content):
            return WxResponse.failure('输入回复内容失败')

        return self._click_comment_send()

    def _sns_windows(self) -> List[uia.Control]:
        """返回朋友圈 SNSWindow 根控件列表（用于递归定位评论 cell）。"""
        wins: List[uia.Control] = []
        try:
            wins += list(find_all_windows_from_root(uiaclsname='mmui::SNSWindow'))
        except Exception as e:
            wxlog.debug(f'SNSWindow 查找失败：{e}')
        if not wins:
            try:
                wins += list(find_all_windows_from_root())
            except Exception as e:
                wxlog.debug(f'顶层窗口兜底查找失败：{e}')
        return wins

    @staticmethod
    def _walk_comment_cells(root: uia.Control, out: List[uia.Control]) -> None:
        """DFS 收集所有 ClassName == ``mmui::TimelineCommentCell`` 的控件。"""
        try:
            if getattr(root, 'ClassName', '') == 'mmui::TimelineCommentCell':
                out.append(root)
        except Exception:
            pass
        try:
            kids = root.GetChildren()
        except Exception:
            kids = []
        for k in kids:
            Moment._walk_comment_cells(k, out)

    def _locate_comment_cell(self, item: MomentItem) -> Optional[tuple]:
        """定位 ``item`` 对应的「评论区」cell，返回 (left, top, right, bottom)。

        以目标朋友圈 ListItem 的底边为基准，取“顶边最近且 ≥ 底边”的评论 cell。
        """
        try:
            item_box = item.control.BoundingRectangle
            target_bottom = item_box.bottom
        except Exception as e:
            wxlog.debug(f'读取目标朋友圈控件矩形失败：{e}')
            return None

        cells: List[uia.Control] = []
        for root in self._sns_windows():
            Moment._walk_comment_cells(root, cells)

        best: Optional[tuple] = None
        best_d = None
        for cell in cells:
            try:
                br = cell.BoundingRectangle
            except Exception:
                continue
            if br.right <= 0 or br.bottom <= 0:
                continue
            d = br.top - target_bottom
            if d < -5:
                continue
            if best is None or d < best_d:
                best = (br.left, br.top, br.right, br.bottom)
                best_d = d
        return best

    def _expand_comment_cell(self, item: MomentItem) -> bool:
        """对目标朋友圈的「评论区」cell 执行一次 Click 以展开（若可点击）。"""
        cells: List[uia.Control] = []
        for root in self._sns_windows():
            Moment._walk_comment_cells(root, cells)
        try:
            item_box = item.control.BoundingRectangle
            target_bottom = item_box.bottom
        except Exception:
            return False
        best: Optional[uia.Control] = None
        best_d = None
        for cell in cells:
            try:
                br = cell.BoundingRectangle
            except Exception:
                continue
            if br.right <= 0 or br.top <= 0:
                continue
            d = br.top - target_bottom
            if d < -5:
                continue
            if best is None or d < best_d:
                best, best_d = cell, d
        if best is None:
            return False
        try:
            best.Click()
            return True
        except Exception as e:
            wxlog.debug(f'展开评论区失败：{e}')
            return False

    _MOMENT_CELL_EXCLUDE = re.compile(r'^\s*余下\s*\d*\s*条\s*$')

    @staticmethod
    def _is_moment_cell_name(name: str) -> bool:
        """判断一个 ListItem 名是否为**朋友圈 cell**（排除评论区/余下N条）。

        朋友圈 cell 以作者昵称开头、通常含正文/时间/图片数；「评论区」与
        「余下N条」是固定名，需排除，避免被误判为“新出现的下一条朋友圈”。
        """
        if not name:
            return False
        n = name.strip()
        if not n or n == '评论区':
            return False
        if '评论' in n or Moment._MOMENT_CELL_EXCLUDE.match(n):
            return False
        return True

    def _has_next_moment_below(self, item: MomentItem, below_top: int) -> bool:
        """判断**下一条朋友圈是否已经出现（滚动到位）**。

        判据：父 List 下是否存在 BoundingRectangle.top >= below_top 的
        **朋友圈 cell**（非评论区/余下N条）。以「目标评论区底部」为界，
        位于其下的朋友圈 cell 即目标动态之后的**下一条朋友圈**。

        注意：朋友圈列表整屏复用 ListItem，滚动后 Name 集合未必变化，
        故**不用 Name 新增**判据，改用它是否**真实出现在评论区下方**。

        Args:
            item: 目标朋友圈。
            below_top: 目标评论区 box 的 bottom（界线）。

        Returns:
            评论区下方是否已出现（可见）下一条朋友圈。
        """
        parent = None
        try:
            parent = item.control.GetParentControl()
        except Exception:
            parent = None
        if parent is None:
            try:
                parent = item.control.GetParent()
            except Exception:
                parent = None
        if parent is None:
            return False
        vp = self._parent_list_box(item)
        vp_bottom = vp[3] if vp is not None else None
        try:
            sibs = parent.GetChildren()
        except Exception:
            return False
        # 目标朋友圈自己的发布者昵称：下方“下一条”必须是**不同发布者**的朋友圈，
        # 否则（自身尾部 cell）会造成误判——即时贴里“下方出现的是目标朋友圈”。
        target_pub = ''
        try:
            item._ensure_parsed()
            target_pub = (item.nickname or '').strip()
        except Exception:
            target_pub = ''
        # 取评论区下方**最近**、且**发布者不同于目标**的“朋友圈” cell 作为候选
        # 下一条，再要求它**真正可见**（top 落在列表可视区内），屏外的不算“出现”。
        best_top = None
        best_name = None
        best_br = None
        for sib in sibs:
            try:
                ctype = sib.ControlTypeName or ''
            except Exception:
                ctype = ''
            if 'List' in ctype and 'Item' in ctype:
                cell_name = sib.Name or ''
                if not Moment._is_moment_cell_name(cell_name):
                    continue
                try:
                    br = sib.BoundingRectangle
                except Exception:
                    continue
                if br.top < below_top - 5:
                    continue
                if target_pub and cell_name.strip().startswith(target_pub):
                    continue  # 目标朋友圈自身，非“下一条”
                if best_top is None or br.top < best_top:
                    best_top = br.top
                    best_name = cell_name
                    best_br = (br.left, br.top, br.right, br.bottom)
        if best_top is None:
            return False
        if vp_bottom is not None and best_top > vp_bottom + 8:
            # 最近的下一条仍完全在视口下缘之下（屏外未显示），不算“出现”
            wxlog.debug(
                f'下一条朋友圈候选仍在视口外（top={best_top} > 视口底={vp_bottom}）：'
                f'{best_name!r} {best_br}')
            return False
        wxlog.debug(f'评论区下方已出现下一条（不同发布者）朋友圈：{best_name!r} {best_br}')
        return True

    def _parent_list_box(self, item: MomentItem):
        """返回父 List（朋友圈时间线列表）的 BoundingRectangle（可视区）。

        用它判定评论区是否完整显示在视口内，以及作为可靠的滚动落点。
        """
        parent = None
        try:
            parent = item.control.GetParentControl()
        except Exception:
            parent = None
        if parent is None:
            try:
                parent = item.control.GetParent()
            except Exception:
                parent = None
        if parent is None:
            return None
        try:
            br = parent.BoundingRectangle
            return (br.left, br.top, br.right, br.bottom)
        except Exception:
            return None

    @staticmethod
    def _comment_fully_visible(cell_box: tuple, vp: tuple) -> bool:
        """目标评论区 cell 是否**完整显示在列表可视区**内。

        cell_box=(left,top,right,bottom)，vp=(left,top,right,bottom)。
        顶部未被视口上缘裁切、底部未超出视口下缘，即滚到位可 OCR。
        """
        t, b = cell_box[1], cell_box[3]
        return (t >= vp[1] - 8) and (b <= vp[3] + 8)

    def _scroll_comments_down(self, item: MomentItem, box: tuple,
                              amount: int = -64, repeats: int = 1,
                              max_tries: int = 12) -> bool:
        """向下滚动**整个朋友圈窗口**，直到「下一条朋友圈」出现在评论区下方。

        判据（用户确认正确）：**评论区下方出现下一条朋友圈 = 评论区完整显示**
        ——能看见评论区下方的下一条朋友圈，就说明评论区已完整露出可 OCR。
        故滚动目标是让 `_has_next_moment_below` 为 True（已加视口过滤，屏外的
        不算出现）。滚动落点放在**列表可视区中心**，避免评论区未显示时 moveTo
        到屏外坐标导致滚动失效。

        Args:
            item: 目标朋友圈。
            box: 评论区 cell 矩形 (left, top, right, bottom)，取其 bottom 为界。
            amount: 每次滚轮格数（负值=向下滚动）。
            repeats: 每次滚动重复滚轮次数。
            max_tries: 最大滚动次数（避免无限滚动越过目标）。

        Returns:
            下一条朋友圈是否已出现在评论区下方（= 评论区已完整显示）。
        """
        vp = self._parent_list_box(item)
        try:
            import pyautogui
        except Exception as e:
            wxlog.debug(f'pyautogui 不可用：{e}')
            return False
        for _ in range(max_tries):
            cb = self._locate_comment_cell(item) or box
            if self._has_next_moment_below(item, cb[3]):
                wxlog.debug(f'评论区下方已出现下一条朋友圈，滚动到位')
                return True
            # 滚动落点：优先用列表可视区中心（始终在屏内可见）
            if vp is not None:
                cx = (vp[0] + vp[2]) // 2
                cy = max(80, (vp[1] + vp[3]) // 2)
            else:
                cx = (cb[0] + cb[2]) // 2
                cy = max(60, (cb[1] + cb[3]) // 2)
            try:
                pyautogui.moveTo(cx, cy)
                time.sleep(0.06)
                old = pyautogui.FAILSAFE
                pyautogui.FAILSAFE = False
                try:
                    for _r in range(repeats):
                        pyautogui.scroll(amount, x=cx, y=cy)
                        time.sleep(0.06)
                finally:
                    pyautogui.FAILSAFE = old
            except Exception as e:
                wxlog.debug(f'滚动朋友圈窗口失败：{e}')
                return False
            time.sleep(0.22)
        return False

    @staticmethod
    def _match_comment_line(lines, reply_to: str,
                            target_text: Optional[str] = None) -> Optional[tuple]:
        """在 OCR 行列表中匹配目标评论行。

        返回 ``(x, y, w, h, content_left, rest_n)``：
        ``(x,y,w,h)`` 为原 OCR box；``content_left`` 为按**文字长度估算**的
        内容区左端（作者名宽 × 单字高 h）；``rest_n`` 为内容字符数。

        注意：OCR 的 box 宽 ``w`` 常严重偏小（对 `送你挖银子：测试` 只报 28px），
        据此点 ``x+w`` 会命中作者名。故落点改由文字长度推算，不依赖错误 w。
        """
        target_author = (reply_to or '').replace(' ', '')
        target_content = (target_text or '').replace(' ', '')
        for text, x, y, w, h in lines:
            t = (text or '').replace(' ', '')
            if not t:
                continue
            if t in ('发送', '回复', '赞', '点赞', '取消'):
                continue
            if t.startswith('回复') or len(t) < 4:
                continue
            author, sep, rest = t.partition('：')
            if not sep:
                author, sep, rest = t.partition(':')
            if not sep or not rest:
                continue
            if author != target_author:
                continue
            if target_content and target_content not in rest:
                continue
            char_w = max(h, 12)  # 单字宽≈字号（h），中文近方形
            content_left = x + (len(author) + 1) * char_w  # 作者名 + 冒号
            return (x, y, w, h, content_left, len(rest))
        return None


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
        self._media_dl = None
        self._cache_index = None

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
            msz = re.search(r"<size[^>]*\bwidth=\"(\d+)\"\s*height=\"(\d+)\"\s*totalSize=\"(\d+)\"", block)
            width = int(msz.group(1)) if msz else 0
            height = int(msz.group(2)) if msz else 0
            size = int(msz.group(3)) if msz else 0
            is_video = any(ext in url.lower() for ext in (".mp4", ".mov", ".avi")) \
                or vm is not None \
                or re.search(r"<videoDuration>\s*[1-9]", block) \
                or re.search(r'<type>\s*6\s*</type>', block)
            entry = {
                "md5": (vm.group(1).lower() if vm else md5),
                "url": url,
                "size": size,
                "width": width,
                "height": height,
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

    def _get_downloader(self):
        """按需创建共享的 MediaDownloader（缓存实例）。"""
        if self._media_dl is None:
            from wechatauto.media import MediaDownloader
            self._media_dl = MediaDownloader(self.db)
        return self._media_dl

    def decrypt_cache(self, path: str) -> Optional[bytes]:
        """解密朋友圈 Sns 缓存容器，返回明文（图片 JPEG / 视频 MP4）。

        缓存是 ``070856320807`` 开头的 V2 加密容器；解密依赖微信主库
        的 key 派生。若输入文件不是 V2 容器则原样返回其字节。
        """
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            wxlog.debug(f'读取缓存失败：{e}')
            return None
        if raw.startswith(b"\x07\x08"):
            try:
                return self._get_downloader().decrypt_image(path)
            except Exception as e:
                wxlog.debug(f'解密缓存失败：{path} {e}')
                return None
        return raw or None

    def _build_cache_index(self, kind: str = "image"):
        """一次性扫描 Sns 缓存并建立索引，避免每次匹配全树解密。

        索引：``{(w, h): [(plain_size, path), ...]}`` 与 ``{plain_size: [path, ...]}``。
        微信 Sns 缓存带 31 字节 V2 容器头，明文尺寸=文件尺寸-31（视频/图片通用，
        个别文件可能有偏差，仍以实测为准）。缓存是易失的，每次调用都会重扫目录
        结构（廉价），但只在文件集合变化时才重新解密。

        Returns:
            dict: 含 ``"dims"``、``"sizes"``、``"files"`` 三张表。
        """
        base = self._cache_root(self.db)
        files = []
        for mon in sorted(os.listdir(base)):
            d_root = os.path.join(base, mon, "Sns", "Img" if kind == "image" else "Video")
            if not os.path.isdir(d_root):
                continue
            for bucket in sorted(os.listdir(d_root)):
                bd = os.path.join(d_root, bucket)
                if not os.path.isdir(bd):
                    continue
                for f in sorted(os.listdir(bd)):
                    p = os.path.join(bd, f)
                    if os.path.isfile(p):
                        try:
                            files.append((p, os.path.getsize(p), os.path.getmtime(p)))
                        except OSError:
                            continue
        files.sort()
        sig = tuple((p, sz, mt) for p, sz, mt in files)
        if self._cache_index is not None and self._cache_index.get("sig") == sig:
            return self._cache_index

        idx = {"sig": sig, "dims": {}, "sizes": {}, "fail": 0}
        if not files:
            self._cache_index = idx
            return idx
        md = self._get_downloader()
        for p, fsz, _ in files:
            data = self.decrypt_cache(p)
            w = h = 0
            if data and data.startswith(b"\xff\xd8"):
                try:
                    w, h = Image.open(io.BytesIO(data)).size
                except Exception:
                    w = h = 0
            plain_size = len(data) if data else -1
            if w and h:
                idx["dims"].setdefault((w, h), []).append((plain_size, p))
            if plain_size > 0:
                idx["sizes"].setdefault(plain_size, []).append(p)
            else:
                idx["fail"] += 1
        self._cache_index = idx
        return idx

    def find_local_candidates(self, md5: str, kind: str = "image",
                              size: int = 0, width: int = 0,
                              height: int = 0,
                              limit: int = 3) -> List[dict]:
        """按内容特征查找 Sns 缓存候选，返回**排序后的候选列表**。

        微信缓存文件名是不透明 32 位会话 key，与 feed 的 url md5 **无代数映射**，
        只能按内容特征匹配。匹配规则（OpenClaw 验收结论）：

        - 宽高 ``(w, h)`` 是主判据：与 feed ``<size width height/>`` **精确相等**
          的候选进入列表，优先；宽高为 0（未知）时跳过主判据。
        - 明文字节数降为**加分项**（微信常存重编码版，字节数仅供消歧排序），
          不再作为硬门槛。
        - 无精确宽高命中时，只有 feed 宽高**未知**且「候选唯一 + 明文偏差
          ≤32B」才退回 1 个 near-size 候选；多候选/偏差大一律返回空，
          避免字节数接近但内容无关的图被当作命中。
        - 存在多个 dims 候选时（同一尺寸多张图），加分排序后返回候选列表，
          **不静默取第一个**，由 `find_local_media` 用 size 消歧或拒绝。

        Args:
            md5: feed 媒体的 md5（用于区分图片/视频子目录，非匹配键）。
            kind: ``"image"`` / ``"video"``。
            size: feed 的 ``<size totalSize/>``（CDN 原图字节数，加分项）。
            width/height: feed 的 ``<size width/height>``（主判据）。
            limit: 最多返回候选数。

        Returns:
            排序后的候选 dict 列表，每项含 ``path``、``width``、``height``、
            ``plain_size``、``score``。无候选返回空列表。
        """
        if len(md5) < 2:
            return []
        base = self._cache_root(self.db)
        if not os.path.isdir(base):
            return []
        idx = self._build_cache_index(kind)
        scored = []

        # 精确宽高命中
        if width and height:
            for plain_size, p in idx["dims"].get((width, height), []):
                score = 100.0
                # 加分项：明文尺寸与 feed totalSize 越接近分越高
                if size > 0:
                    d = abs(plain_size - size)
                    score += 50.0 if d <= 64 else 20.0 if d <= 512 else 0.0
                scored.append({
                    "path": p, "width": width, "height": height,
                    "plain_size": plain_size, "score": score, "hit": "dims",
                })

        # 无精确宽高命中时的兜底：仅当 feed 宽高**未知**（0/0）且「候选唯一 +
        # 明文偏差极小(≤32B)」才收一个 near-size 候选（OpenClaw P0：命中即整图，
        # 绝不能在宽高对不上时用字节数接近的图冒充）。宽高已知时必须用宽高
        # 精确匹配，bytes 只能作排序加分项。
        if not scored and size > 0 and not (width and height):
            near = []
            for p, fsz, _ in self._scan_cached_files(base, "Img" if kind == "image" else "Video"):
                if abs(fsz - (size + 31)) <= 128:
                    data = self.decrypt_cache(p)
                    if not data:
                        continue
                    w = h = 0
                    if data.startswith(b"\xff\xd8"):
                        try:
                            w, h = Image.open(io.BytesIO(data)).size
                        except Exception:
                            pass
                    near.append({
                        "path": p, "width": w, "height": h,
                        "plain_size": len(data), "fsz": fsz,
                    })
            if near:
                deviation = min(abs(c["plain_size"] - size) for c in near)
                if deviation <= 32:
                    best = [c for c in near
                            if abs(c["plain_size"] - size) == deviation]
                    if len(best) == 1:
                        c = best[0]
                        score = 100.0 - deviation / max(size, 1) * 100.0
                        scored.append({
                            "path": c["path"], "width": c["width"],
                            "height": c["height"], "plain_size": c["plain_size"],
                            "score": score, "hit": "near-size",
                        })

        scored.sort(key=lambda c: c["score"], reverse=True)
        scored = scored[:limit]
        # 多候选歧义（宽高已知且 >1 个 dims 命中）时不再静默取第一个：
        # 让调用方看到候选列表，由 find_local_media 用 size 消歧或拒绝。
        return scored

    def _scan_cached_files(self, base: str, sub: str):
        for mon in sorted(os.listdir(base)):
            d_root = os.path.join(base, mon, "Sns", sub)
            if not os.path.isdir(d_root):
                continue
            for bucket in sorted(os.listdir(d_root)):
                bd = os.path.join(d_root, bucket)
                if not os.path.isdir(bd):
                    continue
                for f in sorted(os.listdir(bd)):
                    p = os.path.join(bd, f)
                    if os.path.isfile(p):
                        try:
                            yield p, os.path.getsize(p), os.path.getmtime(p)
                        except OSError:
                            continue

    def find_local_media(self, md5: str, kind: str = "image",
                         size: int = 0, width: int = 0,
                         height: int = 0) -> Optional[str]:
        """在 Sns 缓存里按内容特征查找本地缓存，返回最佳命中路径。

        消歧策略（第三轮 OpenClaw 验收）：单一 dims 候选直接采纳；
        同尺寸多候选时用 ``size`` 绝对偏差消歧，但**偏差不得超过
        ``_MAX_SIZE_DEV=512B``（绝对）或相对 feed size 的 10%**——
        真图不在缓存 + 同尺寸有多张缓存图时，宁返回 None 也不让
        另一张无关图冒充命中。

        Args:
            md5: feed 媒体的 md5（区分图片/视频子目录）。
            kind: ``"image"`` / ``"video"``。
            size: feed 的 ``<size totalSize/>``（加分项）。
            width/height: feed 的 ``<size width/height>``（主判据）。

        Returns:
            最佳命中的缓存文件绝对路径；未命中返回 None。
        """
        if kind == "video":
            return self._find_local_video(md5, size)
        cands = self.find_local_candidates(md5, kind, size=size,
                                           width=width, height=height, limit=3)
        if not cands:
            return None
        dims_hit = [c for c in cands if c["hit"] == "dims"]
        if dims_hit:
            # 宽高精确命中
            if len(dims_hit) == 1:
                return dims_hit[0]["path"]
            # 同尺寸多张图：用 size 消歧（唯一最小值才采纳，且偏差受限）
            if size > 0:
                max_dev = max(_MAX_SIZE_DEV, size * 0.10)
                devs = [abs(c["plain_size"] - size) for c in dims_hit]
                best_d = min(devs)
                if best_d > max_dev:
                    wxlog.info(f'多候选消歧偏差 {best_d}B 超上限 '
                               f'({max_dev:.0f}B)，真图可能不在缓存，'
                               f'返回 None 拒绝冒充')
                    return None
                best = [c for c, d in zip(dims_hit, devs) if d == best_d]
                if len(best) == 1:
                    return best[0]["path"]
            wxlog.info(f'较多候选歧义（{len(dims_hit)} 张同尺寸图），'
                       f'size={size} 无法唯一消歧，返回 None 待调用方处理')
            return None
        # 仅 near-size 候选：find_local_candidates 已保证唯一 + 极小偏差
        if len(cands) == 1:
            return cands[0]["path"]
        return None

    def _find_local_video(self, md5: str, size: int) -> Optional[str]:
        """视频缓存按字节数匹配（跨桶查找），**明文优先、结果确定**。

        demo 视频缓存可能有两种形态：纯明文 mp4（文件尺寸 == feed totalSize）
        或带 31 字节 V2 容器头（文件尺寸 == totalSize + 31）。两者都接受。
        同内容两版共存时优先明文版（直接可用、少一步解密），不依赖扫描顺序：
        先扫一遍记录明文组（|fsz-size|≤8）与容器组（|fsz-(size+31)|≤8），
        明文组有最佳候选则返回之，否则回退容器组。当前环境视频缓存为空，
        行为由本方法的可复现测试（构造假缓存）验证。
        """
        if len(md5) < 2 or size <= 0:
            return None
        base = self._cache_root(self.db)
        if not os.path.isdir(base):
            return None
        plain_best = None   # (deviation, path)
        cont_best = None    # (deviation, path)
        for mon in sorted(os.listdir(base)):
            d_root = os.path.join(base, mon, "Sns", "Video")
            if not os.path.isdir(d_root):
                continue
            for bucket in sorted(os.listdir(d_root)):
                bd = os.path.join(d_root, bucket)
                if not os.path.isdir(bd):
                    continue
                for f in sorted(os.listdir(bd)):
                    p = os.path.join(bd, f)
                    if not os.path.isfile(p):
                        continue
                    try:
                        fsz = os.path.getsize(p)
                    except OSError:
                        continue
                    if abs(fsz - size) <= 8:
                        if plain_best is None or abs(fsz - size) < plain_best[0]:
                            plain_best = (abs(fsz - size), p)
                    if abs(fsz - (size + 31)) <= 8:
                        if cont_best is None or abs(fsz - (size + 31)) < cont_best[0]:
                            cont_best = (abs(fsz - (size + 31)), p)
        if plain_best:
            return plain_best[1]
        if cont_best:
            return cont_best[1]
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
            保存后的文件绝对路径；失败返回 None（具体原因见日志，
            或改用 :meth:`download_media_detailed` 拿到结构化结果）。
        """
        res = self.download_media_detailed(media, save_dir, kind)
        return res.get("path")

    def download_media_detailed(self, media: dict, save_dir: Optional[str] = None,
                                kind: str = "image") -> dict:
        """下载图片/视频并返回**结构化结果**（含失败原因）。

        两条路都可能失败：本地缓存未命中、缓存解密失败、URL 缺失、
        URL 拉取失败。此方法不静默丢弃，而是把原因明确返回/记录，
        让「下载了 0 个文件」有可解释来源。

        Returns:
            dict: ``{"status", "path", "reason", "media"}``。
            ``status`` 取 ``ok`` / ``no-cache`` / ``cache-decrypt-failed`` /
            ``url-missing`` / ``url-dead`` / ``write-failed``。
        """
        save_dir = save_dir or os.path.join(
            os.path.expanduser("~"), "Documents", "wechatauto_moments"
        )
        try:
            os.makedirs(save_dir, exist_ok=True)
        except OSError as e:
            wxlog.error(f'创建保存目录失败：{e}')
            return {"status": "write-failed", "path": None,
                    "reason": f"mkdir: {e}", "media": media}
        media = media or {}
        md5 = media.get("md5")
        size = media.get("size") or 0
        width = media.get("width") or 0
        height = media.get("height") or 0
        url = media.get("url") or ""

        local = None
        if md5:
            local = self.find_local_media(md5, kind, size=size,
                                          width=width, height=height)
            if local and not os.path.isfile(local):
                local = None

        if local:
            data = self.decrypt_cache(local)
            if not data:
                wxlog.error(f'缓存解密失败：{local}')
                return {"status": "cache-decrypt-failed", "path": None,
                        "reason": f"decrypt failed: {local}", "media": media}
            if md5:
                if kind == "video" or not data.startswith(b"\xff\xd8"):
                    name = "%s.mp4" % md5
                else:
                    name = "%s.jpg" % md5
            else:
                name = os.path.basename(local)
        else:
            if not url:
                # no-cache（有 md5 但本地命中 none）与 url-missing（连 md5 都
                # 没有、无 URL 可兜底）是两种独立状态，均可达。
                if md5:
                    return {"status": "no-cache", "path": None,
                            "reason": "md5 given but no local cache hit and no url",
                            "media": media}
                return {"status": "url-missing", "path": None,
                        "reason": "media has neither md5 nor url", "media": media}
            try:
                import urllib.request
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0)"}
                )
                data = urllib.request.urlopen(req, timeout=20).read()
            except Exception as e:
                wxlog.error(f'URL 拉取失败：{url} {e}')
                return {"status": "url-dead", "path": None,
                        "reason": f"urlopen: {e}", "media": media}
            if kind == "video" or url.lower().endswith((".mp4", ".mov", ".avi")):
                ext = ".mp4"
            else:
                tail = os.path.splitext(url.rsplit("?", 1)[0])[1].lower()
                ext = tail if tail in (".jpg", ".jpeg", ".png", ".gif", ".webp") else ".jpg"
            stem = md5
            if not stem:
                import re
                base = url.rsplit("?", 1)[0].rstrip("/")
                base = os.path.basename(base)
                if base:
                    stem = re.sub(r"[^0-9a-zA-Z._-]", "_", base)
                else:
                    stem = "media"
            name = "%s%s" % (stem, ext)

        out = os.path.join(save_dir, name)
        try:
            with open(out, "wb") as f:
                f.write(data)
        except OSError as e:
            wxlog.error(f'写文件失败：{out} {e}')
            return {"status": "write-failed", "path": None,
                    "reason": f"write: {e}", "media": media}
        return {"status": "ok", "path": out, "reason": None, "media": media}

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
        failures: List[dict] = []
        for img in (feed.get("images") or []):
            if images:
                res = self.download_media_detailed(img, target, "image")
                if res.get("path"):
                    saved.append(res["path"])
                elif res.get("status") != "ok":
                    failures.append(res)
        for vid in (feed.get("videos") or []):
            if videos:
                res = self.download_media_detailed(vid, target, "video")
                if res.get("path"):
                    saved.append(res["path"])
                elif res.get("status") != "ok":
                    failures.append(res)
        if failures:
            by = {}
            for f in failures:
                s = f.get("status")
                by[s] = by.get(s, 0) + 1
            wxlog.info(f'朋友圈媒体下载部分失败：{by} 成功={len(saved)}')
        return saved

    # ------------------------------------------------------------------
    # 观察即固化（P0-1）：把易失的 Sns 缓存转成持久映射 + 解密字节
    # ------------------------------------------------------------------
    def snapshot_cache_keys(self) -> dict:
        """快照当前 Sns 缓存全部 key（key = 目录2位 + 文件名30位）。

        Returns:
            ``{(month, kind, key): path}`` 全量字典。
        """
        base = self._cache_root(self.db)
        out = {}
        if not os.path.isdir(base):
            return out
        for mon in os.listdir(base):
            sns_dir = os.path.join(base, mon, "Sns")
            if not os.path.isdir(sns_dir):
                continue
            for kind in ("Img", "Video"):
                kd = os.path.join(sns_dir, kind)
                if not os.path.isdir(kd):
                    continue
                for b in os.listdir(kd):
                    bd = os.path.join(kd, b)
                    if not os.path.isdir(bd):
                        continue
                    for f in os.listdir(bd):
                        p = os.path.join(bd, f)
                        if os.path.isfile(p):
                            out[(mon, kind, b + f)] = p
        return out

    def diff_cache_keys(self, before: dict,
                        after: Optional[dict] = None) -> dict:
        """对比两次快照，返回**新增**的缓存 key→路径。"""
        after = after if after is not None else self.snapshot_cache_keys()
        return {k: p for k, p in after.items() if k not in before}

    @staticmethod
    def _export_root(base=None) -> str:
        """持久导出根目录（默认 ~/Documents/wechatauto_moments/_cache_export）。"""
        return os.path.join(base or os.path.join(
            os.path.expanduser("~"), "Documents", "wechatauto_moments"),
            "_cache_export")

    def export_cache_key(self, key, path, export_dir=None,
                         tid: Optional[int] = None,
                         username: str = "", nickname: str = "",
                         create_time: int = 0,
                         media_index: int = 0,
                         kind: str = "image",
                         mapping_path: Optional[str] = None) -> dict:
        """把单个缓存 key 解密并固化到持久目录，同时写映射条目。

        幂等：映射文件里已记录该 key（相同 sha256）时直接跳过，不重复导出
        （验收标准 2）。

        Args:
            key: ``(month, kind, key_string)`` 三元组。
            path: 缓存文件绝对路径。
            export_dir: 持久目录，默认 ``<export_root>/<tid>``。
            tid/username/nickname/create_time/media_index/kind: 映射元数据。
            mapping_path: 映射 JSON 路径（默认 ``<export_root>/mapping.json``）。

        Returns:
            dict: ``{"status", "exported", "sha256", "path", "group"}``。
        """
        month, ckind, kstr = key
        root = self._export_root()
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as e:
            return {"status": "error", "reason": f"mkdir: {e}"}

        mapping_path = mapping_path or os.path.join(root, "mapping.json")
        mdir = os.path.dirname(mapping_path)
        if mdir:
            try:
                os.makedirs(mdir, exist_ok=True)
            except OSError as e:
                return {"status": "error", "reason": f"mkdir mapping dir: {e}"}
        mapping = {}
        if os.path.isfile(mapping_path):
            try:
                with open(mapping_path, "r", encoding="utf-8") as fp:
                    mapping = json.load(fp)
            except Exception:
                mapping = {}

        data = self.decrypt_cache(path)
        if not data:
            return {"status": "decrypt-failed", "key": key, "path": path}

        sha = hashlib.sha256(data).hexdigest()
        # 幂等 1：同一 key + 同一 sha256 已导出 → 跳过
        existing = mapping.get(kstr)
        if existing and existing.get("sha256") == sha:
            return {"status": "skip", "exported": False,
                    "sha256": sha, "path": existing.get("path")}
        # 幂等 2（跨 key 内容级去重）：缓存易失、每轮下载 key 可能重生成，
        # 但同一视觉动态的**解密字节一致**。已导出过该 sha256 → 跳过，
        # 并把新 key 记别名指向既有导出，避免内容级重复导出。
        for other_key, other in mapping.items():
            if other.get("sha256") == sha:
                mapping.setdefault("_aliases", {})[kstr] = other_key
                try:
                    with open(mapping_path, "w", encoding="utf-8") as fp:
                        json.dump(mapping, fp, ensure_ascii=False, indent=2)
                except OSError:
                    pass
                return {"status": "skip", "exported": False,
                        "sha256": sha, "path": other.get("path"),
                        "alias_of": other_key}

        group = str(tid) if tid else "unknown"
        group_dir = export_dir or os.path.join(root, group)
        try:
            os.makedirs(group_dir, exist_ok=True)
        except OSError as e:
            return {"status": "error", "reason": f"mkdir group: {e}"}

        fname = kstr + ("." + ("mp4" if not data.startswith(b"\xff\xd8") else "jpg"))
        out_path = os.path.join(group_dir, fname)
        try:
            with open(out_path, "wb") as fp:
                fp.write(data)
        except OSError as e:
            return {"status": "error", "reason": f"write: {e}"}

        entry = {
            "key": kstr, "month": month, "cache_kind": ckind,
            "sha256": sha, "path": out_path,
            "tid": tid, "username": username, "nickname": nickname,
            "create_time": create_time, "media_index": media_index,
            "feed_kind": kind,
            "captured_at": int(time.time()),
        }
        mapping[kstr] = entry
        try:
            with open(mapping_path, "w", encoding="utf-8") as fp:
                json.dump(mapping, fp, ensure_ascii=False, indent=2)
        except OSError as e:
            return {"status": "error", "reason": f"mapping write: {e}"}
        return {"status": "ok", "exported": True, "sha256": sha,
                "path": out_path, "group": group}

    def poll_new_cache_keys(self, before: dict, poll_interval: float = 0.3,
                            timeout: float = 15.0) -> dict:
        """轮询新增缓存 key（点击开大图 / 滚动加载触发下载后调用）。

        Args:
            before: ``snapshot_cache_keys()`` 的基线。
            poll_interval: 轮询间隔秒。
            timeout: 总超时秒。

        Returns:
            新增 key→路径 字典；超时返回空。
        """
        t0 = time.time()
        while time.time() - t0 < timeout:
            new = self.diff_cache_keys(before)
            if new:
                return new
            time.sleep(poll_interval)
        return {}

    def export_new_keys(self, new_keys: dict, tid: Optional[int] = None,
                        username: str = "", nickname: str = "",
                        create_time: int = 0,
                        default_kind: str = "image") -> List[dict]:
        """把轮询到的全部新增 key 固化导出（逐个字节固化到持久目录）。"""
        out = []
        for (month, kind, kstr), p in sorted(new_keys.items()):
            feed_kind = "video" if kind == "Video" else default_kind
            r = self.export_cache_key(
                (month, kind, kstr), p, tid=tid,
                username=username, nickname=nickname,
                create_time=create_time, kind=feed_kind)
            out.append(r)
        return out
