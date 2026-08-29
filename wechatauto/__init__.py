"""wechatauto —— Windows版本微信客户端（非网页版）自动化。

基于 UIAutomation 技术驱动当前微信4.x客户端，可实现简单的发送、
接收微信消息，编写简单的微信机器人。
"""

from __future__ import annotations

from .wx import WeChat, Chat, Listener
from .param import WxParam, WxResponse, PROJECT_NAME
from .logger import wxlog
from .moment import Moment, MomentDB
from .db import WeChatDB, auto_detect_db_dir, list_accounts
from .media import MediaDownloader
from .guia import (
    WeChatGUI,
    quick_send,
    quick_send_file,
    quick_send_image,
    quick_reply,
    WinInput,
    ScreenOCR,
)
from .exceptions import (
    NetWorkError,
    WechatautoError,
    WechatautoNoteLoadTimeoutError,
    WechatautoUINotFoundError,
    WechatautoNotLoggedInError,
)
from .utils.lock import LockManager, uilock
from .msgs import (
    Message,
    BaseMessage,
    HumanMessage,
    TextMessage,
    ImageMessage,
    VideoMessage,
    VoiceMessage,
    FileMessage,
    QuoteMessage,
    LinkMessage,
    LocationMessage,
    PersonalCardMessage,
    OtherMessage,
    SystemMessage,
    FriendMessage,
    SelfMessage,
    parse_msg,
)

__version__ = "1.1.10.1"

__all__ = [
    "WeChat",
    "Chat",
    "Listener",
    "WeChatDB",
    "auto_detect_db_dir",
    "list_accounts",
    "MediaDownloader",
    "WeChatGUI",
    "quick_send",
    "quick_send_file",
    "quick_send_image",
    "quick_reply",
    "WinInput",
    "ScreenOCR",
    "WxParam",
    "WxResponse",
    "wxlog",
    "Moment",
    "MomentDB",
    "LockManager",
    "uilock",
    "WechatautoError",
    "NetWorkError",
    "WechatautoUINotFoundError",
    "WechatautoNoteLoadTimeoutError",
    "WechatautoNotLoggedInError",
    "Message",
    "BaseMessage",
    "HumanMessage",
    "TextMessage",
    "ImageMessage",
    "VideoMessage",
    "VoiceMessage",
    "FileMessage",
    "QuoteMessage",
    "LinkMessage",
    "LocationMessage",
    "PersonalCardMessage",
    "OtherMessage",
    "SystemMessage",
    "FriendMessage",
    "SelfMessage",
    "parse_msg",
    "PROJECT_NAME",
    "__version__",
]
