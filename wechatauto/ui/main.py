from .base import BaseUISubWnd, BaseUIWnd
from .navigationbox import NavigationBox
from .sessionbox import SessionBox
from .chatbox import ChatBox
from wechatauto.utils.win32 import (
    FindWindow,
    GetAllWindows,
    GetPathByHwnd,
    get_windows_by_pid
)
from wechatauto.param import WxResponse, PROJECT_NAME
from wechatauto.logger import wxlog
from wechatauto import uia
from typing import (
    Union,
    List,
    Literal
)
import os
import re


def find_wechat_windows(ui_cls_name: str) -> List[uia.Control]:
    """从所有顶层窗口中查找指定 UIA 类名的微信窗口控件。

    Args:
        ui_cls_name: 目标 UIA 类名，如 ``mmui::MainWindow``

    Returns:
        匹配到的窗口控件列表
    """
    targets = []
    for hwnd, clsname, winname in GetAllWindows():
        # 跳过 Qt 内部消息泵等隐藏窗口，其 UIA 服务可能阻塞调用
        if clsname.startswith(uia.QT_INTERNAL_WIN_CLASS_PREFIX):
            continue
        if uia.GetUiClassNameWithTimeout(hwnd) != ui_cls_name:
            continue
        try:
            control = uia.ControlFromHandle(hwnd)
        except Exception:
            continue
        if control is not None:
            targets.append(control)
    return targets


class WeChatSubWnd(BaseUISubWnd):
    """微信独立聊天子窗口"""

    _ui_cls_name: str = 'mmui::FramelessMainWindow'
    _win_cls_name: str = 'Qt51514QWindowIcon'
    _chat_api: ChatBox = None
    nickname: str = ''

    def __init__(
            self,
            key: Union[str, int],
            parent: 'WeChatMainWnd',
            timeout: int = 3
        ):
        self.root = self
        self.parent = parent
        if isinstance(key, str):
            hwnd = FindWindow(classname=self._win_cls_name, name=key, timeout=timeout)
        else:
            hwnd = key
        self.control = uia.ControlFromHandle(hwnd)
        if self.control is not None:
            chatbox_control = self.control.\
                GroupControl(ClassName="mmui::ChatMessagePage").\
                CustomControl(ClassName="mmui::XSplitterView")
            self._chat_api = ChatBox(chatbox_control, self)
            self.nickname = self.control.Name

    def __repr__(self):
        return f'<{PROJECT_NAME} - {self.__class__.__name__} object("{self.nickname}")>'

    @property
    def pid(self):
        if not hasattr(self, '_pid'):
            self._pid = self.control.ProcessId
        return self._pid

    def _get_chatbox(
            self,
            nickname: str = None,
            exact: bool = False
        ) -> ChatBox:
        return self._chat_api

    def _get_windows(self):
        wins = []
        for hwnd in get_windows_by_pid(self.pid):
            try:
                wins.append(uia.ControlFromHandle(hwnd))
            except Exception:
                pass
        ignore_cls = ['basepopupshadow', 'popupshadow']
        return [win for win in wins if win.ClassName not in ignore_cls]

    def chat_info(self):
        return self._chat_api.get_info()

    def send_msg(
            self,
            msg: str,
            who: str = None,
            clear: bool = True,
            at: Union[str, List[str]] = None,
            exact: bool = False,
        ) -> WxResponse:
        chatbox = self._get_chatbox(who, exact)
        if chatbox is None:
            return WxResponse.failure(f"未找到聊天窗口：{who}")
        return chatbox.send_msg(msg, clear, at)

    def send_files(
            self,
            filepath,
            who=None,
            exact=False
        ) -> WxResponse:
        chatbox = self._get_chatbox(who, exact)
        if chatbox is None:
            return WxResponse.failure(f"未找到聊天窗口：{who}")
        return chatbox.send_file(filepath)

    def get_msgs(self):
        chatbox = self._get_chatbox()
        if chatbox:
            return chatbox.get_msgs()
        return []

    def get_new_msgs(self):
        return self._get_chatbox().get_new_msgs()

    def get_msg_by_id(self, msg_id):
        chatbox = self._get_chatbox()
        if chatbox:
            return chatbox.get_msg_by_id(msg_id)

    def get_msg_by_hash(self, msg_hash: str):
        chatbox = self._get_chatbox()
        if chatbox:
            return chatbox.get_msg_by_hash(msg_hash)

    def get_last_msg(self):
        chatbox = self._get_chatbox()
        if chatbox:
            return chatbox.get_last_msg()


class WeChatMainWnd(WeChatSubWnd):
    """微信主窗口"""

    _ui_cls_name: str = 'mmui::MainWindow'
    _win_cls_name: str = 'Qt51514QWindowIcon'
    _ui_name: str = '微信'

    def __init__(self, nickname: str = None, hwnd: int = None):
        self.root = self
        self.parent = self
        if hwnd:
            self._setup_ui(hwnd)
        else:
            # 优先按 UIA 类名定位主窗口，兼容不同 Qt 版本的客户端
            controls = find_wechat_windows(self._ui_cls_name)
            if not controls:
                controls = find_wechat_windows(self._ui_cls_name.replace('MainWindow', 'FramelessMainWindow'))
            if not controls:
                raise Exception('未找到已登录的微信主窗口')
            target = None
            for control in controls:
                if nickname and control.Name != nickname:
                    continue
                target = control
                break
            if target is None:
                target = controls[0]
            self._setup_ui(target.NativeWindowHandle)

        print(f'初始化成功，获取到已登录窗口：{self.nickname}')

    def _setup_ui(self, hwnd: int):
        self.HWND = hwnd
        self.control = uia.ControlFromHandle(hwnd)
        if self.control is not None:
            navigation_control = self.control.\
                ToolBarControl(ClassName="mmui::MainTabBar", AutomationId='main_tabbar')
            sessionbox_control = self.control.\
                GroupControl(ClassName="mmui::ChatMasterView")
            chatbox_control = self.control.\
                GroupControl(ClassName="mmui::ChatMessagePage").\
                CustomControl(ClassName="mmui::XSplitterView")
            self._navigation_api = NavigationBox(navigation_control, self)
            self._session_api = SessionBox(sessionbox_control, self)
            self._chat_api = ChatBox(chatbox_control, self)
            self.nickname = self.control.Name

    def __repr__(self):
        return f'<{PROJECT_NAME} - {self.__class__.__name__} object("{self.nickname}")>'

    def _get_wx_path(self):
        return GetPathByHwnd(self.HWND)

    def _get_wx_dir(self):
        wxdir = os.path.dirname(self._get_wx_path())
        for d in os.listdir(wxdir):
            if re.match(r'\d+\.\d+\.\d+\.\d+', d):
                return os.path.join(wxdir, d)

    def _get_chatbox(
            self,
            nickname: str = None,
            exact: bool = False
        ) -> ChatBox:
        if nickname and (chatbox := WeChatSubWnd(nickname, self, timeout=0)).control:
            return chatbox._chat_api
        else:
            if nickname:
                switch_result = self._session_api.switch_chat(keywords=nickname, exact=exact)
                if not switch_result:
                    return None
            if self._chat_api.msgbox.Exists(0.5):
                return self._chat_api

    def switch_chat(
            self,
            keywords: str,
            exact: bool = True,
            force: bool = False,
            force_wait: Union[float, int] = 0.5
        ):
        return self._session_api.switch_chat(keywords, exact, force, force_wait)

    def get_all_sub_wnds(self):
        sub_wxs = GetAllWindows(classname=WeChatSubWnd._win_cls_name)
        return [
            sub_win
            for i in sub_wxs
            if (
                uia.ControlFromHandle(i[0]).ClassName == WeChatSubWnd._ui_cls_name
                and (sub_win := WeChatSubWnd(i[0], self)).pid == self.pid
            )
        ]

    def get_sub_wnd(self, who: str):
        subwins = self.get_all_sub_wnds()
        for subwin in subwins:
            if subwin.nickname == who:
                return subwin

    def open_separate_window(self, keywords: str) -> WeChatSubWnd:
        if subwin := self.get_sub_wnd(keywords):
            wxlog.debug(f"{keywords} 获取到已存在的子窗口: {subwin}")
            return subwin
        if nickname := self._session_api.switch_chat(keywords):
            wxlog.debug(f"{keywords} 切换到聊天窗口: {nickname}")
            if subwin := self.get_sub_wnd(nickname):
                wxlog.debug(f"{nickname} 获取到已存在的子窗口: {subwin}")
                return subwin
            else:
                keywords = nickname
        if result := self._session_api.open_separate_window(keywords):
            find_nickname = result['data'].get('nickname', keywords)
            return WeChatSubWnd(find_nickname, self)
