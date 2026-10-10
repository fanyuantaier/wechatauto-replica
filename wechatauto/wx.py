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
    """``AddListenAll`` 回调里拿到的会话对象。

    构造时**不碰 GUI**（每条消息都 new 一个 Chat 会触发 ``WeChatGUI`` 初始化），
    只带 username 和解析好的昵称。想在回调里直接回话时，用 ``factory`` 按需升级
    成真正的 :class:`Chat` 并缓存下来，``SendMsg`` 原样转发过去。
    """

    def __init__(self, username: str, nickname: str = '', factory=None, db=None):
        self.who = username
        self._wxid = username
        self._nickname = nickname or ''
        self._factory = factory
        self._chat = None
        self._db = db          # 只读库用（查群成员），不需要 GUI

    @property
    def nickname(self) -> str:
        return self._nickname or self.who

    def __str__(self):
        return self.nickname

    def __repr__(self):
        return f'<{PROJECT_NAME} - _AllMessageChat("{self.who}")>'

    def GetGroupMembers(self) -> List[dict]:
        """群成员列表（与 :meth:`Chat.GetGroupMembers` 同义，只读库、不建 GUI）。

        全局监听回调里拿到的就是本类实例，所以这里也要能查名字。
        """
        wxid = self._wxid or ''
        if not wxid.endswith('@chatroom') or self._db is None:
            return []
        try:
            return self._db.get_group_members(wxid)
        except Exception:
            return []

    @property
    def chat(self):
        if self._chat is None and self._factory is not None:
            try:
                self._chat = self._factory(self.who)
            except Exception as e:
                wxlog.debug(f'全局监听会话升级为 Chat 失败：{e}')
        return self._chat

    def SendMsg(self, msg: str, **kw):
        chat = self.chat
        if chat is None:
            from wechatauto.exceptions import WechatautoError
            raise WechatautoError('全局监听回调里无法回复：未能构造 Chat')
        return chat.SendMsg(msg, **kw)


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


def _db_row_to_message(row: dict, chat: 'Chat', self_wxid: str = None,
                       db=None) -> 'Message':
    """把 db.py 的消息行转换为现有 Message 子类实例。

    direction 判定：只认「db 解析出来的发送者 username == 本机 wxid」。

    发送者身份：``real_sender_id`` 是**消息所在分片** ``Name2Id`` 的 rowid，db 层按
    那一片把它换成真 wxid 放在 ``sender_username``（issue #34：以前拿
    ``message_resource.db`` 的 ``SenderName2Id`` 解析，同一个数字解析成无关的人），但老代码既没往下传，
    ``msg.wxid`` 存的又还是那个数字，于是调用方只能靠文本消息正文里的 ``wxid_xxx:\\n``
    前缀刮发送者——图片/语音/文件这些类型没有前缀，就彻底拿不到是谁发的。现在：
    ``msg.sender_wxid`` 给真实 wxid（解析不到时退回正文前缀，再退回那个数字，
    保持老代码能读到的值不变），``msg.sender`` 给备注/昵称（非文本消息也能对上人了），
    ``msg.wxid`` 对自己的消息给 ``self_wxid`` 而不是常量 2。
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
    sender_wxid = str(row.get('sender_username') or '').strip()
    if sender_wxid.isdigit():
        sender_wxid = ''          # 1.2.4 之前兜底遗留：把数字 rowid 冒充成了用户名
    if not sender_wxid:
        sender_wxid = _extract_group_sender(content)   # 正文前缀仍然更准的场景
    # 「编号 2 就是自己」只在**某些分片**成立：同一账号在 message_0/1/2.db 的
    # Name2Id rowid 实测是 2/4/1（issue #34），写死会把自己发的消息判成对方发的。
    is_self = bool(self_wxid and (sender_wxid == self_wxid
                                  or str(sender_id) == str(self_wxid)))

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
    msg.attr = 'self' if is_self else 'friend'

    msg.sender_wxid = sender_wxid
    msg.wxid = sender_wxid or (self_wxid if is_self else sender_id)
    db = db if db is not None else getattr(chat, '_db', None)
    disp = ''
    if sender_wxid and db is not None:
        try:
            disp = db.nickname_map().get(sender_wxid, '')
        except Exception:
            disp = ''
    msg.sender = disp or sender_wxid or getattr(chat, 'who', '')
    msg.sender_remark = disp or msg.sender
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


def _plain_result(r: dict) -> dict:
    """把 LabelOps 的结果变成能安全打印/序列化的字典。

    结果里会顺手带上 UIA 控件对象（``win`` / ``cell``），那是给同一次调用内部
    用的；直接塞进 ``WxResponse.data`` 会让用户 ``print`` 出一个控件 repr，
    ``json.dumps`` 还会当场抛。
    """
    out = {}
    for k, v in (r or {}).items():
        if k in ('win', 'cell') or hasattr(v, 'BoundingRectangle'):
            continue
        if isinstance(v, list):
            v = [x for x in v if not hasattr(x, 'BoundingRectangle')]
        out[k] = v
    return out


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
        return [_db_row_to_message(r, self, self_wxid, self._db) for r in rows]

    def GetGroupMembers(self) -> List[dict]:
        """群成员列表（静态读库，不点界面）：``username`` / ``nick_name`` /
        ``remark`` / ``is_owner``。不是群聊时返回空列表。

        配合监听回调里的 ``msg.sender_wxid`` 用：群里那些**不在你通讯录**的人，
        只有这张表能给出名字。
        """
        wxid = self._wxid or ''
        if not wxid.endswith('@chatroom'):
            return []
        try:
            return self._db.get_group_members(wxid)
        except Exception:
            return []

    def GetNewMessage(self, max_backlog: int = 5000) -> List['Message']:
        """获取新消息（首次调用仅建立基线，返回空列表）。

        积压超过单批上限（200）时连续分批拉取直到追平；水位只推进到
        **实际取回**的最后一条，不会跳过中间消息。max_backlog 为保护上限，
        若因上限截断，下次调用会从断点继续拉取。
        """
        latest = self._db.get_messages(self._wxid, limit=1)
        current = latest[0]['sort_seq'] if latest else 0
        if self._last_seq is None:
            self._last_seq = current
            return []
        if current <= self._last_seq:
            return []
        rows: List[dict] = []
        since = self._last_seq
        batch_sz = 200
        while len(rows) < max_backlog:
            batch = self._db.get_new_messages(self._wxid, since_seq=since, limit=batch_sz)
            if not batch:
                break
            rows.extend(batch)
            since = batch[-1]['sort_seq']
            if len(batch) < batch_sz:
                break
        if not rows:
            return []
        if max_backlog and len(rows) > max_backlog:
            rows = rows[:max_backlog]
        # 水位只推进到实际取回的最后一条（而非数据库最新位置）
        self._last_seq = rows[-1]['sort_seq']
        self_wxid = self._db.get_self_info()['username']
        return [_db_row_to_message(r, self, self_wxid, self._db) for r in rows]

    def GetMessageById(self, msg_id) -> Optional['Message']:
        """根据消息 local_id 获取消息实例。"""
        try:
            local_id = int(str(msg_id).replace('db-', ''))
        except (TypeError, ValueError):
            return None
        row = self._db.get_message_row(self._wxid, local_id)
        if not row:
            return None
        return _db_row_to_message(row, self, db=self._db)

    def GetMessageByHash(self, msg_hash: str) -> Optional['Message']:
        """根据消息哈希值获取消息实例。"""
        if not msg_hash:
            return None
        self_wxid = self._db.get_self_info()['username']
        for row in self._db.get_messages(self._wxid, limit=200):
            m = _db_row_to_message(row, self, self_wxid, self._db)
            if m.hash == msg_hash or getattr(m, 'hash_text', None) == msg_hash:
                return m
        return None

    def GetLastMessage(self) -> Optional['Message']:
        """获取当前聊天窗口的最后一条消息。"""
        rows = self._db.get_messages(self._wxid, limit=1)
        if not rows:
            return None
        return _db_row_to_message(rows[0], self, db=self._db)


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
            gui=None,
            db=None,
            **kwargs
        ):
        from wechatauto.guia import WeChatGUI
        from wechatauto.db import WeChatDB

        self._gui = gui if gui is not None else WeChatGUI()
        self._db = db or WeChatDB()
        info = self._db.get_self_info()
        self.nickname = nickname or info.get('nick_name') or info.get('username') or ''
        self.who = self.nickname
        self._wxid = info.get('username') or ''
        self.listen: Dict[str, tuple] = {}
        self._listener = None
        self._listen_wrappers: Dict[str, Callable] = {}
        self._all_chat_cache: Dict[str, object] = {}
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

    # -- 通讯录标签（UIA 控件路线）-----------------------------------------

    def _labels(self, uia=None):
        """构造 :class:`LabelOps`；UIA 树不可用时返回 None。"""
        from wechatauto.labels import LabelOps
        eng = uia if uia is not None else self._gui._get_uia()
        if eng is None:
            eng = self._gui._get_uia(refresh=True)
        if eng is None:
            return None
        return LabelOps(uia=eng, db=self._db)

    def _display_names(self, members) -> List[str]:
        """把 wxid/微信号换成界面上显示的那个名字（备注 > 昵称）。

        标签面板的成员列表按**显示名**渲染，拿 wxid 去勾一个都勾不上。
        """
        out = []
        for m in members or []:
            m = str(m).strip()
            if not m:
                continue
            try:
                nick = self._db.get_nickname(m)
            except Exception:
                nick = m
            out.append(nick or m)
        return out

    @uilock
    def ListLabels(self, prefer: str = "db") -> WxResponse:
        """列出所有标签。

        ``prefer='db'``（默认）读 contact.db，不碰窗口——**删掉的标签在库里会
        滞后**（实测：界面上已经删干净了，``contact_label`` 还留着那两行）。
        要准数用 ``prefer='ui'``，它读管理窗左栏，顺带带上微信自己显示的人数。

        Returns:
            WxResponse，``data['labels']`` 为 ``[{label_id, name, sort_order}]``
            （走库）或 ``[{name, count}]``（走界面）。
        """
        ops = self._labels()
        if prefer != "ui":
            try:
                rows = (ops.list_labels(prefer="db")["labels"] if ops is not None
                        else self._db.list_labels())
            except Exception as e:
                return WxResponse.failure(f'读取标签列表失败：{e}')
            return WxResponse.success(f'共 {len(rows)} 个标签',
                                      {'labels': rows, 'via': 'db'})
        if ops is None:
            return WxResponse.failure('UIA 驱动不可用，读不了界面里的标签列表')
        try:
            r = ops.list_labels(prefer="ui")
        finally:
            ops.back_to_chat()
        if not r['ok']:
            return WxResponse.failure(r['reason'], {'labels': r.get('labels', [])})
        return WxResponse.success(f"共 {len(r['labels'])} 个标签",
                                  {'labels': r['labels'], 'via': r['reason']})

    @uilock
    def CreateLabel(self, name: str, verify: bool = True) -> WxResponse:
        """新建一个标签（已存在时直接算成功）。

        Args:
            name: 标签名。微信是「先建出空标签、再改名」两步，改名走剪贴板粘贴
                + 回车（实测行内编辑框不进 UIA）。
            verify: 建完回读左栏标签行确认；没改成名字时**明确报 not-renamed**，
                不含糊报成功（那条路径会在账号上留一个「未命名」标签，得说清楚）。
        """
        ops = self._labels()
        if ops is None:
            return WxResponse.failure('UIA 驱动不可用，无法创建标签')
        try:
            r = ops.create_label(name, verify=verify)
        finally:
            ops.back_to_chat()
        if not r['ok']:
            return WxResponse.failure(f"创建标签失败：{r['reason']}", _plain_result(r))
        return WxResponse.success(f"标签「{name}」已就绪", _plain_result(r))

    @uilock
    def RenameLabel(self, old: str, new: str) -> WxResponse:
        """改标签名（右键 →「修改标签名」→ 剪贴板粘贴 + 回车）。

        Args:
            old: 现在的标签名。
            new: 改成什么。只改名字，成员和 label_id 都不动。
        """
        ops = self._labels()
        if ops is None:
            return WxResponse.failure('UIA 驱动不可用，无法改标签名')
        try:
            r = ops.rename_label(old, new)
        finally:
            ops.back_to_chat()
        if not r['ok']:
            return WxResponse.failure(f"改标签名失败：{r['reason']}", _plain_result(r))
        return WxResponse.success(f"标签「{old}」已改名为「{new}」", _plain_result(r))

    @uilock
    def AddLabelMembers(self, label: str, members, verify: bool = True) -> WxResponse:
        """给标签批量添加成员。

        Args:
            label: 标签名；不存在时先创建。
            members: 成员列表，接受备注名/昵称/wxid（wxid 会自动换成显示名）。
            verify: 完成后回读左栏标签行上的成员数（``同学(63)`` 那个数字）。
                人数没变就报 ``count-unchanged``，不报成功。

        Returns:
            WxResponse，``data['missing']`` 是一个都没勾上的名字，
            ``data['count_before']`` / ``data['count_after']`` 是成员数变化。
        """
        return self._label_members(label, members, remove=False, verify=verify)

    @uilock
    def RemoveLabelMembers(self, label: str, members) -> WxResponse:
        """把成员从标签里移出（只去标签，不会删好友）。"""
        return self._label_members(label, members, remove=True)

    def _label_members(self, label: str, members, remove: bool,
                       verify: bool = True) -> WxResponse:
        ops = self._labels()
        if ops is None:
            return WxResponse.failure('UIA 驱动不可用，无法操作标签成员')
        names = self._display_names(
            [members] if isinstance(members, str) else list(members or []))
        act = '移出' if remove else '添加'
        if not names:
            return WxResponse.failure(f'{act}成员失败：成员列表为空')
        try:
            r = (ops.remove_members(label, names) if remove
                 else ops.add_members(label, names, verify=verify))
        finally:
            ops.back_to_chat()
        if not r['ok']:
            return WxResponse.failure(f"{act}成员失败：{r['reason']}", _plain_result(r))
        done = r.get('clicked') or r.get('picked') or []
        detail = "标签「%s」%s %d 人" % (label, act, len(done))
        if r.get('count_before') is not None and r.get('count_after') is not None:
            detail += "（成员数 %s → %s）" % (r['count_before'], r['count_after'])
        missing = r.get('missing') or []
        if missing:
            detail += f"，没弄上：{'、'.join(missing)}"
        return WxResponse.success(detail, _plain_result(r))

    @uilock
    def DeleteLabel(self, name: str) -> WxResponse:
        """删掉一个标签。只去标签，**不删好友**（微信的确认文案就这么写）。"""
        ops = self._labels()
        if ops is None:
            return WxResponse.failure('UIA 驱动不可用，无法删除标签')
        try:
            r = ops.delete_label(name)
        finally:
            ops.back_to_chat()
        if not r['ok']:
            return WxResponse.failure(f"删除标签失败：{r['reason']}", _plain_result(r))
        return WxResponse.success(f"标签「{name}」已删除", _plain_result(r))

    @uilock
    def LabelMembers(self, label: str, limit: int = None) -> WxResponse:
        """列出一个标签里的**全部**成员（右侧列表是虚拟化的，靠滚动取全量）。

        只读界面，不发消息。``data['complete']`` 说清楚有没有取全
        （拿微信自己显示的人数对：``同学(63)`` 对到 63 个才算全）。
        """
        ops = self._labels()
        if ops is None:
            return WxResponse.failure('UIA 驱动不可用，无法读取标签成员')
        try:
            r = ops.label_members(label, limit=limit)
        finally:
            ops.back_to_chat()
        if not r['ok']:
            return WxResponse.failure(f"读取成员失败：{r['reason']}", _plain_result(r))
        res = ops.resolve_members(r['members'])
        names = [x['display'] for x in res['recipients']]
        extra = ''
        if r.get('gapped'):
            extra += f"，有一屏没按名字接上（按微信显示的 {r['count']} 人核过）"
        if res['skipped']:
            extra += f"，{len(res['skipped'])} 个人没认出来/重名（见 data['skipped']）"
        return WxResponse.success(
            f"标签「{label}」微信显示 {r['count']} 人，读到 {len(r['members'])} 人"
            f"（{r['reason']}），其中 {len(names)} 个能唯一对上 wxid{extra}",
            dict(_plain_result(r), recipients=res['recipients'],
                 skipped=res['skipped'], names=names))

    @uilock
    def SendToLabel(self, text: str, label: str, members=None,
                    dry_run: bool = False, limit: int = None,
                    verify: bool = True) -> WxResponse:
        """按标签批量发同一条文本（先读成员，再逐个走普通发送通道）。

        Args:
            text: 消息正文。
            label: 标签名。
            members: 只发给其中一部分（显示名或 wxid 的列表）；不给=全发。
            dry_run: **只解析收件人不发送**——第一次用请先跑这个。
            limit: 最多发几个人（试水用）。
            verify: 每个人发完都回读数据库确认（``send_msg`` 自带的水位校验）。

        安全口径：每个成员都要先在通讯录里**唯一命中一个 wxid** 才会被发；
        重名的人和查不到的人进 ``data['skipped']`` 交回来，不猜人——按标签
        群发发错人是最贵的一种错。发送本身走 :meth:`Chat.SendMsg` 同一条路，
        每一笔都过 :mod:`wechatauto.rhythm` 的拟人节流。

        Returns:
            WxResponse，``data`` 里有 ``sent`` / ``failed`` / ``skipped`` /
            ``recipients`` / ``dry_run``。
        """
        if not (text or '').strip():
            return WxResponse.failure('批量发送失败：正文为空')
        ops = self._labels()
        if ops is None:
            return WxResponse.failure('UIA 驱动不可用，无法读取标签成员')
        got = ops.label_members(label, limit=None)
        if not got['ok']:
            return WxResponse.failure(f"读取标签成员失败：{got['reason']}",
                                      _plain_result(got))
        res = ops.resolve_members(got['members'])
        recipients = res['recipients']
        if members:
            want = {str(m).strip() for m in members}
            recipients = [x for x in recipients
                          if x['display'] in want or x['username'] in want]
            if not recipients:
                return WxResponse.failure(
                    '指定的成员都不在该标签里（名字要和界面显示一致）',
                    {'names': [x['display'] for x in res['recipients']]})
        if limit:
            recipients = recipients[:int(limit)]
        base = {'label': label, 'total_in_label': got['count'],
                'dry_run': bool(dry_run), 'recipients': recipients,
                'skipped': res['skipped']}
        if dry_run:
            return WxResponse.success(
                f"预演：标签「{label}」可发 {len(recipients)} 人，"
                f"跳过 {len(res['skipped'])} 人（没有真的发出去）", base)
        sent, failed = [], []
        gui = self._gui
        if gui is None:                       # 离线自测注入的假发送通道
            from wechatauto.guia import WeChatGUI
            gui = WeChatGUI()
        for r in recipients:
            one = gui.send_msg(text, who=r['display'], verify=verify)
            item = dict(r)
            if one['status'] == '成功':
                sent.append(item)
            else:
                item['reason'] = one.get('message')
                failed.append(item)
        msg = (f"标签「{label}」已发 {len(sent)} 人"
               + (f"，失败 {len(failed)} 人" if failed else "")
               + (f"，跳过 {len(res['skipped'])} 人" if res['skipped'] else ""))
        out = WxResponse.failure if (failed and not sent) else WxResponse.success
        return out(msg, dict(base, sent=sent, failed=failed))

    def _forward(self):
        """构造 :class:`ForwardOps`；UIA 树不可用时返回 None。"""
        from wechatauto.forward import ForwardOps
        eng = self._gui._get_uia()
        if eng is None:
            eng = self._gui._get_uia(refresh=True)
        if eng is None:
            return None
        return ForwardOps(uia=eng, db=self._db)

    @staticmethod
    def _match_key(text: str) -> Optional[str]:
        """给「刚发出去的那一条」定一个定位串：取首行前 24 字。

        转发必须先**在界面上找到那条消息**再右键。刚发的正文就是最直接的锚点；
        太短（<4 字）的正文容易和别的气泡撞名，那种就退回「右键最新一条」。
        """
        line = (text or "").strip().splitlines()
        if not line:
            return None
        key = line[0].strip()
        return key[:24] if len(key) >= 4 else None

    def _seed_then_forward(self, names, text, dry_run, chunk, verify):
        """先老老实实发给第一个人，再把**那一条**分别转发给剩下的人。

        为什么要有这条路：右键转发的物料必须已经在某个会话里躺着——不先发一条，
        「转发」根本没有可点的目标。第一个人拿的是原件（普通消息），剩下的人拿的是
        转发件，所以总数正好等于名单长度，不会重发。
        """
        first, rest = names[0], names[1:]
        if dry_run:
            return {'ok': True, 'reason': 'plan', 'seed': first, 'rest': rest,
                    'chunks': (len(rest) + chunk - 1) // chunk if rest else 0,
                    'sent': [], 'failed': [], 'skipped': []}
        one = self._gui.send_msg(text, who=first, verify=verify)
        if not one or one.get('status') != '成功':
            return {'ok': False, 'reason': 'seed-fail：第一条没发出去，'
                                           '后面没有可转发的消息（%s）'
                    % (one.get('message') if one else '无返回'),
                    'seed': first, 'rest': rest, 'sent': [], 'failed': list(rest),
                    'skipped': []}
        if not rest:
            return {'ok': True, 'reason': 'seed-only', 'seed': first, 'rest': [],
                    'chunks': 0, 'sent': [first], 'failed': [], 'skipped': []}
        ops = self._forward()
        r = ops.forward(rest, chat=first, match=self._match_key(text),
                        chunk=chunk, verify=verify)
        r['seed'] = first
        r['sent'] = [first] + list(r.get('sent') or [])
        return r

    @uilock
    def ForwardMessage(self, to, chat: str = None, match: str = None,
                       dry_run: bool = False, chunk: int = None,
                       verify: bool = True, text: str = None) -> WxResponse:
        """把一条消息分别转发给 ``to``。

        Args:
            to: 收件人，单个名字或列表。
            text: **正文**。给了它就走「先发给第一个人 → 再把那一条转发给剩下的」；
                不给就转发 ``chat``（或当前会话）里已有的一条。
            chat: 在哪个会话里右键；不给就用当前打开的会话。
            match: 按文字定位那一条消息；不给就右键**最新一条**。
            dry_run: 只开窗、勾选、核对按钮上的人数，最后点取消，**一条都不发**。
                给了 ``text`` 时预演连窗口都不开（那时候还没有可转发的消息）。
            chunk: 一个转发窗勾几个人（默认 9）；块与块之间过 rhythm。
            verify: 发完回读每个人的会话，看有没有比发送时刻更新的一行消息。

        「分别发送(N)」那颗按钮上的 N 是真值：勾的人和按钮数的对不上就停下来报
        ``count-mismatch``，不会把消息发出去。
        """
        ops = self._forward()
        if ops is None:
            return WxResponse.failure('UIA 驱动不可用，无法转发消息')
        names = [str(n).strip() for n in (([to] if isinstance(to, str) else list(to or []))
                                          if to else []) if str(n).strip()]
        if text and text.strip():
            if len(names) < 2:
                return WxResponse.failure('给了正文但只有 1 个收件人：那直接 SendMsg 就行，不用转发')
            r = self._seed_then_forward(names, text.strip(), dry_run,
                                        chunk or 9, verify)
            return self._forward_response(r, dry_run, len(names))
        r = ops.forward(names, chat=chat, match=match, dry_run=dry_run,
                        chunk=chunk or 9, verify=verify)
        return self._forward_response(r, dry_run, len(names))

    @staticmethod
    def _forward_response(r, dry_run, total) -> WxResponse:
        if dry_run:
            if not r.get('ok'):
                return WxResponse.failure(f"预演没走通：{r['reason']}", r)
            if r.get('reason') == 'plan':
                return WxResponse.success(
                    f"预演：先发给「{r['seed']}」1 人，再把这条分别转发给剩下 "
                    f"{len(r['rest'])} 人（分 {r['chunks']} 块）——"
                    f"窗口都没开，一条都没发", r)
            return WxResponse.success(
                f"预演：{(r.get('picked') or [])} 已勾上（{r.get('chunks')} 块），"
                f"没有真的发出去", r)
        if not r.get('ok'):
            # 已经落库的那些人必须点名：这条链是「先发第一个人 → 再转发给剩下的」，
            # 中途失败时第一个人**真的收到了一条**。不写出来，用户照着提示重跑一遍
            # 就会给他发第二遍。
            already = [x for x in (r.get('sent') or []) if x]
            note = ("（注意：已发出的 %s 条已经在库里，重跑会再发一遍，"
                    "先把这几个人剔掉）" % len(already)) if already else ""
            return WxResponse.failure(
                f"转发失败：{r['reason']}{note}",
                dict(r, already_sent=already))
        seed = r.get('seed')
        return WxResponse.success(
            f"已分别转发给 {len(r.get('sent') or [])} 人"
            + (f"（含先发的「{seed}」原件）" if seed and seed in (r.get('sent') or []) else "")
            + (f"，失败 {len(r['failed'])} 人" if r.get('failed') else "")
            + (f"，跳过 {len(r['skipped'])} 人" if r.get('skipped') else ""), r)

    @uilock
    def ForwardToLabel(self, label: str, chat: str = None, match: str = None,
                       dry_run: bool = True, limit: int = None,
                       chunk: int = None, verify: bool = True,
                       text: str = None) -> WxResponse:
        """把一条消息按**标签**分别转发给标签里的每个人（默认只预演）。

        收件人来自 :meth:`LabelMembers` 那套滚动枚举；每个人都要在通讯录里
        **唯一命中一个 wxid** 才会被转发，重名/查不到的进 ``data['skipped']``，
        不猜人。节奏是分小块（默认 9 人/块），块与块之间过 rhythm。

        Args:
            text: 正文。给了它就**先发给名单里第一个人**，再把那一条分别转发给
                剩下的人——右键转发的物料得先在界面上存在，不然没东西可转。
                第一个人拿原件、其余拿转发件，每人恰好一条。
            chat / match: 不给 ``text`` 时用这两个指定「转发哪一条已有的消息」。

        ``dry_run`` 给了 ``text`` 时**连窗口都不开**（这时候还没有可转发的消息），
        只把名单、分块和第一个收件人报出来。
        """
        ops = self._forward()
        if ops is None:
            return WxResponse.failure('UIA 驱动不可用，无法转发消息')
        got = ops.label_members(label, limit=None)
        if not got['ok']:
            return WxResponse.failure(f"读取标签成员失败：{got['reason']}",
                                      _plain_result(got))
        res = ops.resolve_members(got['members'])
        recipients = res['recipients']
        if limit:
            recipients = recipients[:int(limit)]
        names = [x['display'] for x in recipients]
        base = {'label': label, 'total_in_label': got['count'],
                'dry_run': bool(dry_run), 'recipients': recipients,
                'skipped': res['skipped']}
        if not names:
            return WxResponse.failure(
                f"标签「{label}」里没有能唯一对上 wxid 的人（{len(res['skipped'])} 人都对不上）",
                base)
        if text and text.strip():
            if len(names) < 2:
                return WxResponse.failure(
                    f"标签「{label}」只有 {len(names)} 个人可发，直接 SendMsg 就行，"
                    f"不用先发再转", base)
            r = self._seed_then_forward(names, text.strip(), dry_run,
                                        chunk or 9, verify)
            return self._forward_response(r, dry_run, len(names))
        r = ops.forward(names, chat=chat, match=match, dry_run=dry_run,
                        chunk=chunk or 9, verify=verify)
        if dry_run:
            if not r['ok']:
                return WxResponse.failure(f"预演没走通：{r['reason']}",
                                          dict(base, detail=r))
            return WxResponse.success(
                f"预演：标签「{label}」可转发 {len(names)} 人，"
                f"分 {r.get('chunks')} 块，跳过 {len(res['skipped'])} 人（没发）",
                dict(base, chunks=r.get('chunks'), picked=r.get('picked')))
        if not r['ok']:
            return WxResponse.failure(f"按标签转发失败：{r['reason']}",
                                      dict(base, detail=r))
        return WxResponse.success(
            f"标签「{label}」已分别转发给 {len(r.get('sent') or [])} 人"
            + (f"，失败 {len(r['failed'])} 人" if r.get('failed') else "")
            + (f"，跳过 {len(res['skipped'])} 人" if res['skipped'] else ""),
            dict(base, sent=r.get('sent'), failed=r.get('failed'),
                 skipped=r.get('skipped'), chunks=r.get('chunks')))

    @uilock
    def ProbeLabels(self, dump_dir: str = None) -> WxResponse:
        """走一遍「通讯录 → 通讯录管理 → 标签」，只导航不写，报每一步判定。

        界面文案/控件类名随版本漂移时用这个定位是哪一段断了。
        """
        ops = self._labels()
        if ops is None:
            return WxResponse.failure('UIA 驱动不可用，无法探测')
        ops.dump_dir = dump_dir
        try:
            steps = ops.probe()
        finally:
            ops.back_to_chat()
        bad = [s for s in steps if not s['ok']]
        data = {'steps': steps}
        if bad:
            return WxResponse.failure(
                f"探测到「{bad[0]['step']}」这一步断：{bad[0]['reason']}", data)
        return WxResponse.success(f"{len(steps)} 步全部走通", data)

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
                # 首次拿不到 → 强制重新热激活 accessibility gate（微信重启
                # /重登后 gate byte 会失效），再取一次，避免“昨天能用今天不能”。
                _wxlog.info('UIA 引擎初始不可用，强制重新热激活后重试')
                uia_eng = self._gui._get_uia(refresh=True)
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

        需要微信主窗口已登录且 UIA 树被热激活。优先用新版合并布局入口
        （含已在朋友圈页的快速判定）；失败时退化为主窗口控件树的递归扫描
        （按 ClassName ``mmui::MainTabBar`` 定位左侧导航栏，再匹配 Name 含
        “朋友圈”的按钮），最后退回 NavigationBox 预订按钮。成功返回 True，
        树不可用或找不到按钮时返回 False。

        注意：本方法不依赖 :attr:`_api`（其背后的 ``WeChatMainWnd()`` 构造
        会在某些环境下触发全桌面 UIA 枚举导致长时间阻塞），内部一律从
        ``ControlFromHandle(main_hwnd)`` 直接锚定主窗口。
        """
        # 路线 1：新版合并布局（含已在朋友圈页的快速判定，无需导航）
        if self._switch_to_moments_new_style():
            return True

        # 路线 2：直接递归扫描主窗口控件树，命中“朋友圈”导航按钮（旧版）
        try:
            import uiautomation as _uia
            mw = getattr(self, '_gui', None)
            hwnd = getattr(mw, 'main_hwnd', None) if mw is not None else None
            if hwnd:
                root = _uia.ControlFromHandle(hwnd)
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
        return False

    def _switch_to_moments_new_style(self) -> bool:
        """新版微信（4.x 合并布局）切换到朋友圈。

        新版左侧导航只有 微信/通讯录/收藏/发现/更多 五个 tab，没有“朋友圈”
        按钮；需先点“发现”tab，再点发现页左侧的“朋友圈”入口
        （``ExtensionDiscoverContentCell`` / Name ``朋友圈`` 的按钮）。
        双入口都先回到标注状态再判断时间线是否出现。
        """
        import time as _t
        try:
            import uiautomation as _uia
            mw = getattr(self, '_gui', None)
            hwnd = getattr(mw, 'main_hwnd', None) if mw is not None else None
            if not hwnd:
                return False
            root = _uia.ControlFromHandle(hwnd)
        except Exception as exc:
            wxlog.debug(f'UIA 根控件获取失败：{exc!r}')
            return False

        # 树没物化时 ControlFromHandle 只拿到 Qt 空壳（微信重启/升级后 gate byte
        # 归零），下面每条查找都会落空，最后报出来的却是「找不到导航按钮」，
        # 很容易被误判成版本不兼容。先按需唤醒 mmui 树，再重新锚定根控件。
        def _tree_ready(node) -> bool:
            try:
                return _find_descendant(
                    node,
                    lambda c: (getattr(c, 'ClassName', '') or '').startswith('mmui::MainTabBar')
                    or (getattr(c, 'ClassName', '') or '') == 'mmui::SNSContentView',
                    max_depth=15) is not None
            except Exception:
                return False

        if root is not None and not _tree_ready(root):
            try:
                from wechatauto.uia_driver import WeChatUIA
                if WeChatUIA().ensure_materialized(timeout=6.0):
                    root = _uia.ControlFromHandle(hwnd) or root
                    wxlog.debug('进朋友圈前已唤醒 mmui 树')
            except Exception as exc:
                wxlog.debug(f'唤醒 mmui 树失败：{exc!r}')

        def _has_timeline() -> bool:
            try:
                tl = _find_descendant(root, lambda c: getattr(c, 'ClassName', '') == 'mmui::TimeLineListView', max_depth=30)
                if tl is not None:
                    return True
                sc = _find_descendant(root, lambda c: getattr(c, 'ClassName', '') == 'mmui::SNSContentView', max_depth=30)
                return sc is not None
            except Exception as exc:
                wxlog.debug(f'朋友圈控件探测失败：{exc!r}')
                return False

        if _has_timeline():
            return True

        try:
            # 左侧导航：先在 MainTabBar 容器内找 XTabBarItem，避免全局遍历
            # 命中 Name='发现' 的其它空 rect 控件。
            tabbar = _find_descendant(root, lambda c: getattr(c, 'ClassName', '') == 'mmui::MainTabBar', max_depth=15)
            disc = None
            if tabbar is not None:
                disc = _find_descendant(
                    tabbar,
                    lambda c: (getattr(c, 'ClassName', '') == 'mmui::XTabBarItem'
                               or getattr(c, 'ControlTypeName', '') in ('ButtonControl', 'TabItemControl'))
                              and (getattr(c, 'Name', '') or '').strip() == '发现',
                    max_depth=6,
                )
            if disc is None:
                disc = _find_descendant(root, lambda c: (getattr(c, 'Name', '') or '').strip() == '发现', max_depth=15)
            if disc is not None:
                try:
                    disc.Click()
                    _t.sleep(0.8)
                except Exception:
                    # Click 可能因控件临时失效失败，退化为坐标点击
                    try:
                        import pyautogui as _pg
                        r = disc.BoundingRectangle
                        if r.right > r.left and r.bottom > r.top:
                            x = int(r.left + (r.right - r.left) // 2)
                            y = int(r.top + (r.bottom - r.top) // 2)
                            _pg.click(x, y)
                            _t.sleep(0.8)
                        else:
                            return False
                    except Exception:
                        return False
        except Exception:
            pass

        if _has_timeline():
            return True

        try:
            btn = _find_descendant(root, lambda c: getattr(c, 'ControlTypeName', '') in ('ButtonControl', 'TabItemControl', 'ListItemControl')
                                   and (getattr(c, 'Name', '') or '').strip() == '朋友圈')
            if btn is not None:
                btn.Click()
                _t.sleep(1.0)
                return _has_timeline()
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
        # 全局监听也要在重建监听器时补挂：否则 StopListening() 之后再
        # StartListening()，AddListenAll 的回调就悄悄没了，而
        # _listen_all_active 还是 True，再调 AddListenAll 只会回「已开启全局监听」。
        if (getattr(self, '_listen_all_active', False)
                and getattr(self, '_listen_all_wrapper', None)):
            self._listener.add_all(self._listen_all_wrapper,
                                   discover=getattr(self, '_listen_all_discover', True))
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
                msg = _db_row_to_message(row, chat, self_wxid, self._db)
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
            callback: 回调函数，参数为 (Message 对象, Chat 对象)。第二个参数的
                ``.who`` 是会话 username、``.nickname`` 是显示名；想直接在回调里
                回话就调 ``chat.SendMsg(...)``（第一次用时按需构造真 Chat 并缓存，
                不会为每条消息都初始化 GUI）。
            discover: 为 True 时自动发现新出现的会话（如新群聊）并注册
                回调，无需重复调用。默认 True。

        已经用 ``AddListenChat`` 单独监听过的会话**同样**会收到这里的回调
        （一个会话可以挂多个回调）。

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
                username = row.get('username', '') or ''
                chat = self._all_chat_cache.get(username)
                if chat is None:
                    try:
                        nick = self._db.get_nickname(username) or ''
                    except Exception:
                        nick = ''
                    chat = _AllMessageChat(
                        username, nick,
                        factory=lambda who: Chat(who, self._gui, self._db),
                        db=self._db)
                    self._all_chat_cache[username] = chat
                msg = _db_row_to_message(row, chat, self_wxid, self._db)
                callback(msg, chat)
            except Exception:
                import traceback
                wxlog.debug(f'全局监听回调发生错误：{traceback.format_exc()}')

        self._listen_all_callback = callback
        self._listen_all_wrapper = _wrap
        self._listen_all_discover = discover
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
        self._listen_all_wrapper = None
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
