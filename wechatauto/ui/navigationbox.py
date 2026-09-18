from wechatauto import uia
from wechatauto.languages import WECHAT_NAVIGATION_BOX, get_lang


class NavigationBox:
    """微信左侧导航栏"""

    def __init__(self, control, parent):
        self.control: uia.Control = control
        self.root = parent.root
        self.parent = parent
        self.init()

    def _lang(self, text):
        return get_lang(WECHAT_NAVIGATION_BOX, text)

    def init(self):
        self.chat_icon = self.control.ButtonControl(Name=self._lang('聊天'))
        self.contact_icon = self.control.ButtonControl(Name=self._lang('通讯录'))
        self.favorites_icon = self.control.ButtonControl(Name=self._lang('收藏'))
        self.files_icon = self.control.ButtonControl(Name=self._lang('聊天文件'))
        self.moments_icon = self.control.ButtonControl(Name=self._lang('朋友圈'))
        self.browser_icon = self.control.ButtonControl(Name=self._lang('搜一搜'))
        self.video_icon = self.control.ButtonControl(Name=self._lang('视频号'))
        self.stories_icon = self.control.ButtonControl(Name=self._lang('看一看'))
        self.mini_program_icon = self.control.ButtonControl(Name=self._lang('小程序面板'))
        self.phone_icon = self.control.ButtonControl(Name=self._lang('手机'))
        self.settings_icon = self.control.ButtonControl(Name=self._lang('更多'))

    def switch_to_chat_page(self):
        self.chat_icon.Click()

    def switch_to_contact_page(self):
        self.contact_icon.Click()

    def switch_to_favorites_page(self):
        self.favorites_icon.Click()

    def switch_to_files_page(self):
        self.files_icon.Click()

    def switch_to_moments_page(self):
        self.moments_icon.Click()

    def switch_to_browser_page(self):
        self.browser_icon.Click()

    def switch_to_video_page(self):
        self.video_icon.Click()

    def switch_to_stories_page(self):
        self.stories_icon.Click()

    def switch_to_mini_program_page(self):
        self.mini_program_icon.Click()

    def switch_to_phone_page(self):
        self.phone_icon.Click()

    def switch_to_settings_page(self):
        self.settings_icon.Click()
