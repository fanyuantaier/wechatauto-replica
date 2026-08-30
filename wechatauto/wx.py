"""wechatauto 顶层 API —— 兼容当前微信 4.x 客户端。

实现说明
========
早期版本基于 UIAutomation（``mmui::*`` 控件树）驱动微信。当前 4.1.x 客户端
冷启动时 UIA 树只暴露 ``Qt51514QWindowIcon`` + ``MMUIRenderSubWindow*`` 空壳
（原 wxauto UI 方案因此失效）；通过热激活 Qt accessibility gate（见
:mod:`wechatauto.uia_driver`）后可物化 ``mmui::*`` 完整控件树。

本模块把 :class:`WeChat` / :class:`Chat` 的公共 API 重新实现为
「UIA 优先（:class:`wechatauto.uia_driver.WeChatUIA`）+ 坐标/OCR
（:class:`wechatauto.guia.WeChatGUI`）+ 本地数据库
（:class:`wechatauto.db.WeChatDB`）」混合技术栈，**保持方法签名不变**，
原有调用方代码无需改动即可运行。

:class:`Listener` 抽象类保留仅为向后兼容（已由 :mod:`wechatauto.db` 的
``Listener`` 取代）。
"""

from __future__ import annotations

import ctypes
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from typing import (
    Callable,
    TYPE_CHECKING,
    Union,
    List,
    Dict,
    Literal,
    Optional,
)

from wechatauto.param import WxParam, WxResponse, PROJECT_NAME
from wechatauto.logger import wxlog
from wechatauto.utils.lock import uilock

if TYPE_CHECKING:
    from wechatauto.msgs.base import Message


def _find_descendant(control, predicate, max_depth: int = 12):
    """在 UIA 控件子树中递归查找第一个满足 ``predicate`` 的后代。

    用树遍历（``GetChildren``）替代 ``uiautomation`` 的名称模式匹配，
    避免兼容层对 Name 的模糊匹配在“朋友圈”等中文按钮上失配。
    """
    try:
        if predicate(control):
            return control
    except Exception:
        return None
    if max_depth <= 0:
        return None
    try:
        children = control.GetChildren()
    except Exception:
        return None
    for child in children:
        found = _find_descendant(child, predicate, max_depth - 1)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# 兼容占位：UIA 时代的监听器抽象基类（保留导出，不再使用）
# ---------------------------------------------------------------------------

class Listener(ABC):
    """监听器抽象基类（兼容保留）。

    当前版本请使用 :class:`wechatauto.db.Listener`。
    """

    @abstractmethod
    def _get_listen_messages(self):
        ...


# ---------------------------------------------------------------------------
# DB 消息 → Message 对象适配
# ---------------------------------------------------------------------------

class _FakeRect:
    """伪矩形，供现有 Message 类计算 hash 使用。"""

    def __init__(self):
        self.top = self.left = self.bottom = self.right = 0

    def height(self):
        return self.bottom - self.top

    def width(self):
        return self.right - self.left


class _DBMessageControl:
    """让 DB 消息复用现有 Message 子类的轻量伪控件。

    仅提供 ``Name`` / ``runtimeid`` / ``BoundingRectangle`` / ``Exists``
    等只读接口；交互类操作（点击/滚动）因 DB 消息无对应控件而明确报错。
    """

    def __init__(self, content: str, msg_id):
        self.Name = content or ''
        self.AutomationId = None
        self.ClassName = "mmui::ChatTextItemView"
        self.runtimeid = str(msg_id)
        self._rect = _FakeRect()

    @property
    def BoundingRectangle(self):
        return self._rect

    def Exists(self, timeout=0) -> bool:
        return True

    def GetChildren(self):
        return []

    def Click(self, *args, **kwargs):
        raise NotImplementedError('DB 消息不支持点击操作')

    def RightClick(self, *args, **kwargs):
        raise NotImplementedError('DB 消息不支持右键操作')


class _DBMessageParent:
    """Message 所需的 parent 占位（root 指向 Chat）。"""

    def __init__(self, chat):
        self.root = chat
        self.msgbox = None


class _AllMessageChat:
    """AddListenAll 使用的轻量 Chat 占位（仅含 .who，不触发 GUI 初始化）。"""

    def __init__(self, username: str):
        self.who = username
        self._wxid = username


def _extract_group_sender(content) -> str:
    """群消息内容形如 ``wxid_xxx:\\n正文``，提取发送者 wxid。"""
    if isinstance(content, bytes):
        content = content.decode('utf-8', errors='ignore')
    m = re.match(r'^(wxid_[0-9a-zA-Z_]+):\s*\n', content or '')
    return m.group(1) if m else ''


def _pick_msg_class(is_self: bool, mtype: Optional[str], content: str):
    from wechatauto.msgs import friend as friendmsg
    from wechatauto.msgs import self as selfmsg

    mod = selfmsg if is_self else friendmsg

    def get(name):
        return getattr(mod, name)

    if mtype == '文本':
        return get('SelfTextMessage' if is_self else 'FriendTextMessage')
    if mtype == '图片':
        return get('SelfImageMessage' if is_self else 'FriendImageMessage')
    if mtype == '语音':
        return get('SelfVoiceMessage' if is_self else 'FriendVoiceMessage')
    if mtype == '视频':
        return get('SelfVideoMessage' if is_self else 'FriendVideoMessage')
    if mtype == '位置':
        return get('SelfLocationMessage' if is_self else 'FriendLocationMessage')
    if mtype == '文件/链接/卡片':
        head = (content or '')[:8]
        if '[链接' in head or head.startswith('链接'):
            return get('SelfLinkMessage' if is_self else 'FriendLinkMessage')
        if head.startswith('文件') or '[文件' in head:
            return get('SelfFileMessage' if is_self else 'FriendFileMessage')
        if head.startswith('位置') or head.startswith('[位置'):
            return get('SelfLocationMessage' if is_self else 'FriendLocationMessage')
        if '[个人名片' in head or '[名片' in head:
            return get('SelfPersonalCardMessage' if is_self else 'FriendPersonalCardMessage')
        return get('SelfOtherMessage' if is_self else 'FriendOtherMessage')
    if mtype == '动画表情':
        return get('SelfEmojiMessage' if is_self else 'FriendEmojiMessage')
    return get('SelfOtherMessage' if is_self else 'FriendOtherMessage')


def _db_row_to_message(row: dict, chat: 'Chat', self_wxid: str = None) -> 'Message':
    """把 db.py 的消息行转换为现有 Message 子类实例。

    direction 判定：``sender_id == 2`` 视为自己（与 guia 发送校验一致），
    也可用 self_wxid 比对兜底。
    """
    from wechatauto.db import WeChatDB
    from wechatauto.msgs.mattr import SystemMessage

    mtype = row.get('type')
    if mtype is None and row.get('local_type') is not None:
        mtype = WeChatDB._msg_type_name(row.get('local_type'))
    content = row.get('content') or ''
    if isinstance(content, bytes):
        content = WeChatDB._friendly_content(content, mtype)
    sender_id = row.get('sender_id')
    is_self = sender_id == 2 or bool(self_wxid and str(sender_id) == str(self_wxid))

    ctrl = _DBMessageControl(content, row.get('local_id'))
    parent = _DBMessageParent(chat)

    if mtype == '系统消息':
        msg = SystemMessage(ctrl, parent)
    else:
        cls = _pick_msg_class(is_self, mtype, content)
        msg = cls(ctrl, parent)

    # 附加 DB 元数据
    msg.local_id = row.get('local_id')
    msg.sort_seq = row.get('sort_seq')
    msg.create_time = row.get('create_time')
    msg.wxid = sender_id
    msg.attr = 'self' if is_self else 'friend'
    sender = _extract_group_sender(content) or getattr(chat, 'who', '')
    msg.sender = sender or getattr(chat, 'who', '')
    msg.sender_remark = msg.sender
    return msg


class SessionItem:
    """会话列表条目（兼容 SessionElement 常用字段）。"""

    def __init__(self, name: str, unread: int = 0, summary: str = '',
                 last_time: int = 0, username: str = ''):
        self.name = name
        self.unread = unread
        self.summary = summary
        self.last_time = last_time
        self.username = username

    def __repr__(self):
        return f'<{PROJECT_NAME} - {self.__class__.__name__}("{self.name}")>'


def _resolve_wxid(db, name: str) -> str:
    """把会话显示名解析为数据库 wxid；文件传输助手/未知则原样返回。"""
    if name in ('filehelper', '文件传输助手'):
        return 'filehelper'
    try:
        for hit in db.search_contact(name):
            if name in (hit.get('nick_name'), hit.get('remark')):
                return hit['username']
    except Exception:
        pass
    return name


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class Chat:
    """聊天窗口实例（基于 GUI + 本地数据库）。"""

    def __init__(self, who: str = None, gui=None, db=None):
        from wechatauto.guia import WeChatGUI
        from wechatauto.db import WeChatDB

        self.who = who or ''
        self._gui = gui or WeChatGUI()
        self._db = db or WeChatDB()
        self._wxid = _resolve_wxid(self._db, self.who)
        self._last_seq: Optional[int] = None

    def __repr__(self):
        return f'<{PROJECT_NAME} - {self.__class__.__name__} object("{self.who}")>'

    def __str__(self):
        return self.who or self.nickname

    def __add__(self, other):
        return (self.who or '') + other

    def __radd__(self, other):
        return other + (self.who or '')

    # -- 展示 -------------------------------------------------------------

    def Show(self):
        """打开该会话的聊天窗口并置前。"""
        self._gui.open_chat(self.who)

    def Close(self) -> None:
        """关闭聊天（GUI 模式下无独立窗口，置前即可）。"""
        self._gui.bring_to_front()

    @uilock
    def VoiceCall(self, who: str = None, video: bool = False) -> WxResponse:
        """发起语音/视频通话。

        Args:
            who: 通话对象，不指定则使用当前聊天对象
            video: True 尝试视频通话（当前版本未暴露视频按钮，通常失败）

        Returns:
            WxResponse
        """
        target = who or self.who
        uia = self._gui._get_uia()
        if uia is None:
            return WxResponse.failure('UIA 驱动不可用，无法发起通话')
        if not uia.voice_call(target, video=video):
            return WxResponse.failure('通话发起失败（可能未打开会话或控件不可用）')
        return WxResponse.success(f'已发起通话：{target}')

    @uilock
    def Poke(self, who: str = None) -> WxResponse:
        """对联系人发起「拍一拍」（右键头像 → 点击拍一拍）。

        Args:
            who: 拍一拍对象，不指定则使用当前聊天对象

        Returns:
            WxResponse
        """
        target = who or self.who
        uia = self._gui._get_uia()
        if uia is None:
            return WxResponse.failure('UIA 驱动不可用，无法发起拍一拍')
        if not uia.poke(target):
            return WxResponse.failure('拍一拍失败（未找到对方消息或菜单不可识别）')
        return WxResponse.success(f'已对 {target} 拍一拍')

    @uilock
    def RecallLastMessage(self, who: str = None) -> WxResponse:
        """撤回当前会话最近一条自己发送的消息。

        Args:
            who: 会话对象，不指定则使用当前聊天对象

        Returns:
            WxResponse
        """
        target = who or self.who
        uia = self._gui._get_uia()
        if uia is None:
            return WxResponse.failure('UIA 驱动不可用，无法撤回消息')
        if not uia.recall_last_message(target):
            return WxResponse.failure('撤回失败（消息已过期或控件不可识别）')
        return WxResponse.success(f'已撤回对 {target} 发送的最近一条消息')

    @uilock
    def ForwardVoiceMessage(
            self,
            who: str = None,
            target: str = None,
            save_dir: str = None,
        ) -> WxResponse:
        """转发语音消息（从本地媒体库提取 SILK 文件发送给目标）。

        微信不支持右键直接转发语音，故实现为「找到本地语音文件 → 以文件
        消息发送」。默认转发本会话最近一条语音到 target（不指定则发给
        本会话对象自身）。

        Args:
            who: 语音所在会话，不指定则用当前会话
            target: 转发目标联系人，不指定则转发给 who 本身
            save_dir: 语音文件临时保存目录

        Returns:
            WxResponse
        """
        chat = Chat(who or self.who, self._gui, self._db) if who else self
        msgs = chat.GetAllMessage()
        for m in msgs:
            if getattr(m, 'type', None) == 'voice':
                return m.forward_to(target or chat.who, save_dir=save_dir)
        return WxResponse.failure(f'会话「{chat.who}」最近 50 条中没有语音消息')

    # -- 信息 -------------------------------------------------------------

    def ChatInfo(self) -> Dict[str, str]:
        """获取聊天窗口信息。"""
        info = {'chat_name': self.who, 'chat_type': 'friend'}
        if self._wxid and self._wxid.endswith('@chatroom'):
            info['chat_type'] = 'group'
        return info

    # -- 发送 -------------------------------------------------------------

    @uilock
    def SendMsg(
            self,
            msg: str,
            who: str = None,
            clear: bool = True,
            at: Union[str, List[str]] = None,
            exact: bool = False,
        ) -> WxResponse:
        """发送消息。

        Args:
            msg: 消息内容
            who: 发送对象，不指定则发送给当前聊天对象
            clear: 是否发送前清空编辑框（GUI 路径恒清理）
            at: @对象（支持 str 或 list）
            exact: 是否精确匹配会话名

        Returns:
            WxResponse
        """
        target = who or self.who
        if at:
            return self._gui.at_member(at, msg, target)
        return self._gui.send_msg(msg, target)

    @uilock
    def SendFiles(
            self,
            filepath,
            who=None,
            exact=False
        ) -> WxResponse:
        """向当前聊天窗口发送文件/图片。

        Args:
            filepath: 文件绝对路径（str 或 list）
            who: 发送对象，不指定则发送给当前聊天对象
            exact: 是否精确匹配会话名

        Returns:
            WxResponse
        """
        target = who or self.who
        if isinstance(filepath, (list, tuple)):
            result = None
            for p in filepath:
                result = self._gui.send_file(p, target)
            return result or WxResponse.failure('文件列表为空')
        return self._gui.send_file(filepath, target)

    # -- 读取 -------------------------------------------------------------

    def GetAllMessage(self) -> List['Message']:
        """获取当前聊天窗口最近 50 条消息。"""
        rows = self._db.get_messages(self._wxid, limit=50)
        self_wxid = self._db.get_self_info()['username']
        return [_db_row_to_message(r, self, self_wxid) for r in rows]

    def GetNewMessage(self) -> List['Message']:
        """获取新消息（首次调用仅建立基线，返回空列表）。"""
        latest = self._db.get_messages(self._wxid, limit=1)
        current = latest[0]['sort_seq'] if latest else 0
        if self._last_seq is None:
            self._last_seq = current
            return []
        if current <= self._last_seq:
            return []
        rows = self._db.get_new_messages(self._wxid, since_seq=self._last_seq)
        self._last_seq = current
        self_wxid = self._db.get_self_info()['username']
        return [_db_row_to_message(r, self, self_wxid) for r in rows]

    def GetMessageById(self, msg_id) -> Optional['Message']:
        """根据消息 local_id 获取消息实例。"""
        try:
            local_id = int(str(msg_id).replace('db-', ''))
        except (TypeError, ValueError):
            return None
        row = self._db.get_message_row(self._wxid, local_id)
        if not row:
            return None
        return _db_row_to_message(row, self)

    def GetMessageByHash(self, msg_hash: str) -> Optional['Message']:
        """根据消息哈希值获取消息实例。"""
        if not msg_hash:
            return None
        self_wxid = self._db.get_self_info()['username']
        for row in self._db.get_messages(self._wxid, limit=200):
            m = _db_row_to_message(row, self, self_wxid)
            if m.hash == msg_hash or getattr(m, 'hash_text', None) == msg_hash:
                return m
        return None

    def GetLastMessage(self) -> Optional['Message']:
        """获取当前聊天窗口的最后一条消息。"""
        rows = self._db.get_messages(self._wxid, limit=1)
        if not rows:
            return None
        return _db_row_to_message(rows[0], self)


# ---------------------------------------------------------------------------
# WeChat
# ---------------------------------------------------------------------------

class WeChat(Chat, Listener):
    """微信主窗口实例（兼容 API）。"""

    def __init__(
            self,
            nickname: str = None,
            start_listener: bool = False,
            debug: bool = False,
            **kwargs
        ):
        from wechatauto.guia import WeChatGUI
        from wechatauto.db import WeChatDB

        self._gui = WeChatGUI()
        self._db = WeChatDB()
        info = self._db.get_self_info()
        self.nickname = nickname or info.get('nick_name') or info.get('username') or ''
        self.who = self.nickname
        self._wxid = info.get('username') or ''
        self.listen: Dict[str, tuple] = {}
        self._listener = None
        self._listen_wrappers: Dict[str, Callable] = {}
        self._listener_is_listening = False
        self._listener_stop_event = threading.Event()
        self._current_chat: Optional['Chat'] = None
        self._listen_all_active = False
        self._listen_all_callback: Optional[Callable] = None
        self._moment_api: Optional[object] = None
        self._moment: Optional[object] = None

        if start_listener:
            self._listener_start()
        if debug:
            wxlog.set_debug(True)
            wxlog.debug('Debug mode is on')

    # -- 朋友圈（UIA 控件路线）--------------------------------------------

    @property
    def _api(self):
        """朋友圈 UIA 根控件（懒构建，需热激活 UIA 树后才有值）。"""
        return self._ensure_moment_api()

    def _ensure_moment_api(self):
        """确保 UIA 树被热激活并返回主窗口封装，失败返回 None。"""
        from wechatauto.logger import wxlog as _wxlog
        if self._moment_api is not None:
            return self._moment_api
        try:
            uia_eng = self._gui._get_uia()
            if uia_eng is None:
                _wxlog.info('UIA 树不可用，无法进入朋友圈（无 UI 节点）')
                return None
        except Exception as e:
            _wxlog.debug('初始化 UIA 引擎失败：%s', e)
            return None
        try:
            from wechatauto.ui.main import WeChatMainWnd
            self._moment_api = WeChatMainWnd()
        except Exception as e:
            _wxlog.debug('获取微信主窗口 UIA 封装失败：%s', e)
            self._moment_api = None
        return self._moment_api

    def SwitchToMoments(self) -> bool:
        """切换到朋友圈页面（UIA 控件点击导航栏“朋友圈”按钮）。

        需要微信主窗口已登录且 UIA 树被热激活。优先用 :class:`NavigationBox`
        的预订按钮；失败时退化为主窗口控件树的递归扫描（按 ClassName
        ``mmui::MainTabBar`` 定位左侧导航栏，再匹配 Name 含“朋友圈”的按钮）。
        成功返回 True，树不可用或找不到按钮时返回 False。
        """
        api = self._api
        if api is None:
            return False

        # 路线 1：直接递归扫描主窗口控件树，命中“朋友圈”导航按钮（快且稳）
        try:
            root = api.control
            tabbar = _find_descendant(root, lambda c: getattr(c, 'ClassName', '') == 'mmui::MainTabBar')
            if tabbar is not None:
                btn = _find_descendant(
                    tabbar,
                    lambda c: getattr(c, 'ControlTypeName', '') == 'ButtonControl'
                              and (getattr(c, 'Name', '') or '').strip() == '朋友圈',
                )
                if btn is not None:
                    btn.Click()
                    return True
        except Exception:
            pass

        # 路线 2：复用 NavigationBox（老接口）
        nav = getattr(api, '_navigation_api', None)
        if nav is not None:
            try:
                nav.switch_to_moments_page()
                return True
            except Exception:
                pass
        return False

    @property
    def Moment(self):
        """朋友圈 UIA 控件接口（点赞/评论/读取）。UIA 树不可用时为 None。"""
        if self._moment is None:
            from wechatauto.moment import Moment
            self._moment = Moment(self)
        return self._moment

    # -- 监听（基于 db.Listener）------------------------------------------

    def _listener_start(self):
        from wechatauto.db import Listener as DBListener
        if self._listener is not None:
            if self._listener._thread and self._listener._thread.is_alive():
                return
            self._listener = None
        self._listener = DBListener(self._db, interval=WxParam.LISTEN_INTERVAL)
        for name, (chat, _cb) in self.listen.items():
            wrapper = self._make_listen_cb(chat, _cb)
            self._listen_wrappers[name] = wrapper
            self._listener.add_listener(chat._wxid, wrapper)
        self._listener.start()
        self._listener_is_listening = True
        self._listener_stop_event.clear()

    def _listener_stop(self):
        if self._listener is not None:
            self._listener.stop()
        self._listener_is_listening = False
        self._listener_stop_event.set()

    def _make_listen_cb(self, chat: 'Chat', callback: Callable) -> Callable:
        self_wxid = self._db.get_self_info()['username']

        def _wrapper(row: dict, listener) -> None:
            try:
                msg = _db_row_to_message(row, chat, self_wxid)
                callback(msg, chat)
            except Exception:
                import traceback
                wxlog.debug(f'监听消息回调发生错误：{traceback.format_exc()}')

        return _wrapper

    def _get_listen_messages(self):
        """兼容占位：实际监听由 db.Listener 完成。"""
        return

    @uilock
    def AddListenChat(
            self,
            nickname: str,
            callback: Callable[['Message', 'Chat'], None],
        ) -> WxResponse:
        """添加监听聊天。

        Args:
            nickname: 要监听的聊天对象（显示名）
            callback: 回调函数，参数为 (Message 对象, Chat 对象)

        Returns:
            Chat 对象（监听成功后返回）
        """
        if not self._listener_is_listening:
            wxlog.debug('检测到未开启监听器，开启监听器')
            self._listener_start()
        if nickname in self.listen:
            return WxResponse.failure('该聊天已监听')
        chat = Chat(nickname, self._gui, self._db)
        if self._db.get_messages(chat._wxid, limit=1) == [] and not chat._wxid:
            return WxResponse.failure('找不到聊天窗口')
        self.listen[nickname] = (chat, callback)
        wrapper = self._make_listen_cb(chat, callback)
        self._listen_wrappers[nickname] = wrapper
        if self._listener is not None:
            self._listener.add_listener(chat._wxid, wrapper)
        return chat

    def AddListenAll(
            self,
            callback: Callable[['Message', 'Chat'], None],
            discover: bool = True,
        ) -> WxResponse:
        """监听所有会话的新消息（包括好友、群聊、文件传输助手等）。

        Args:
            callback: 回调函数，参数为 (Message 对象, Chat-like 对象)。
                Chat-like 对象的 .who 属性为会话原始 username。
            discover: 为 True 时自动发现新出现的会话（如新群聊）并注册
                回调，无需重复调用。默认 True。

        Returns:
            WxResponse

        示例::

            wx = WeChat()
            def on_all(msg, chat):
                print(f'[{chat.who}] {msg.content}')
            wx.AddListenAll(on_all)
            wx.StartListening()
        """
        if not self._listener_is_listening:
            wxlog.debug('检测到未开启监听器，开启监听器')
            self._listener_start()
        if getattr(self, '_listen_all_active', False):
            return WxResponse.failure('已开启全局监听')
        self_wxid = self._db.get_self_info()['username']

        def _wrap(row: dict, listener) -> None:
            try:
                username = row.get('username', '')
                fake_chat = _AllMessageChat(username)
                msg = _db_row_to_message(row, fake_chat, self_wxid)
                callback(msg, fake_chat)
            except Exception:
                import traceback
                wxlog.debug(f'全局监听回调发生错误：{traceback.format_exc()}')

        self._listen_all_callback = callback
        self._listen_all_active = True
        if self._listener is not None:
            self._listener.add_all(_wrap, discover=discover)
        return WxResponse.success('已开启全局监听')

    def RemoveListenAll(self) -> WxResponse:
        """停止全局监听。"""
        if not getattr(self, '_listen_all_active', False):
            return WxResponse.failure('未开启全局监听')
        self._listen_all_active = False
        self._listen_all_callback = None
        if self._listener is not None:
            self._listener._discover_new = False
            self._listener._all_callback = None
        return WxResponse.success('已停止全局监听')

    def StartListening(self) -> None:
        """启动监听。"""
        self._listener_start()

    def StopListening(self, remove: bool = True) -> None:
        """停止监听。

        Args:
            remove: 是否同时移除所有监听对象
        """
        self._listener_stop()
        if remove:
            self.listen.clear()
            self._listen_wrappers.clear()
            self._listen_all_active = False
            self._listen_all_callback = None

    @uilock
    def RemoveListenChat(
            self,
            nickname: str,
            close_window: bool = True
        ) -> WxResponse:
        """移除监听聊天。

        Args:
            nickname: 要移除监听的聊天对象
            close_window: 是否关闭聊天窗口（GUI 模式忽略）

        Returns:
            WxResponse
        """
        if nickname not in self.listen:
            return WxResponse.failure('未找到监听对象')
        chat, _cb = self.listen[nickname]
        if self._listener is not None:
            wrapper = self._listen_wrappers.pop(nickname, None)
            if wrapper is not None:
                self._listener.remove_listener(chat._wxid, wrapper)
        del self.listen[nickname]
        return WxResponse.success()

    def KeepRunning(self):
        """阻塞主线程直到手动停止监听。"""
        while not self._listener_stop_event.is_set():
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                wxlog.debug(f'wechatauto("{self.nickname}") shutdown')
                self.StopListening(True)
                break

    # -- 会话 -------------------------------------------------------------

    def GetSession(self) -> List['SessionItem']:
        """获取当前会话列表。"""
        sessions = []
        for row in self._db.get_sessions(limit=50):
            username = row.get('username') or ''
            name = row.get('last_sender') or username
            if not name or name == username:
                try:
                    nick = self._db.get_nickname(username)
                    name = nick or username
                except Exception:
                    name = username
            sessions.append(SessionItem(
                name=name,
                unread=row.get('unread', 0),
                summary=row.get('summary', ''),
                last_time=row.get('last_time', 0),
                username=username,
            ))
        return sessions

    @uilock
    def ChatWith(
        self,
        who: str,
        exact: bool = True,
        force: bool = False,
        force_wait: Union[float, int] = 0.5
    ):
        """打开聊天窗口。

        Args:
            who: 要聊天的对象
            exact: 搜索会话时是否精确匹配
            force: 忽略（兼容保留）
            force_wait: 忽略（兼容保留）

        Returns:
            str: 成功时返回会话显示名，失败返回 None
        """
        chat = Chat(who, self._gui, self._db)
        self._gui.open_chat(chat.who)
        if self._gui.get_input_box():
            self._current_chat = chat
            self.who = chat.who
            self._wxid = chat._wxid
            return chat.who
        self._gui.open_chat(chat.who)
        if self._gui.get_input_box():
            self._current_chat = chat
            self.who = chat.who
            self._wxid = chat._wxid
            return chat.who
        return None

    # -- 消息读取（委托给当前打开的会话）----------------------------------

    def _cur(self) -> 'Chat':
        return self._current_chat if self._current_chat is not None else self

    def GetAllMessage(self) -> List['Message']:
        """获取当前打开会话的最近 50 条消息。"""
        return self._cur().GetAllMessage()

    def GetNewMessage(self) -> List['Message']:
        """获取当前打开会话的新消息。"""
        return self._cur().GetNewMessage()

    def GetMessageById(self, msg_id) -> Optional['Message']:
        """根据消息 local_id 获取消息实例。"""
        return self._cur().GetMessageById(msg_id)

    def GetMessageByHash(self, msg_hash: str) -> Optional['Message']:
        """根据消息哈希值获取消息实例。"""
        return self._cur().GetMessageByHash(msg_hash)

    def GetLastMessage(self) -> Optional['Message']:
        """获取当前打开会话的最后一条消息。"""
        return self._cur().GetLastMessage()

    def GetSubWindow(self, nickname: str) -> Optional['Chat']:
        """获取子窗口实例（GUI 模式下返回对应 Chat 对象）。"""
        chat = Chat(nickname, self._gui, self._db)
        try:
            hits = self._db.search_contact(nickname)
        except Exception:
            hits = []
        if hits or nickname in ('filehelper', '文件传输助手'):
            return chat
        return None

    def GetAllSubWindow(self) -> List['Chat']:
        """获取所有子窗口实例（GUI 模式下无独立子窗口，返回空列表）。"""
        return []

    # -- 路径 / 生命周期 ---------------------------------------------------

    @property
    def path(self):
        from wechatauto.utils.win32 import GetPathByHwnd
        return GetPathByHwnd(self._gui.main_hwnd)

    @property
    def dir(self):
        wxdir = self.path
        if not wxdir:
            return None
        wxdir = os.path.dirname(wxdir)
        for d in os.listdir(wxdir):
            if re.match(r'\d+\.\d+\.\d+\.\d+', d):
                return os.path.join(wxdir, d)
        return None

    def ShutDown(self):
        """强制退出微信进程。"""
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(
            self._gui.main_hwnd, ctypes.byref(pid))
        if pid.value:
            os.system(f'taskkill /f /pid {pid.value}')
