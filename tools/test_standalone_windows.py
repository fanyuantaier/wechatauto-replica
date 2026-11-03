"""独立聊天/记录窗口回归；不连接或操作真实微信。"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechatauto.guia import WeChatGUI
from wechatauto.uia_driver import WeChatUIA


def control(cls, name='', aid='', children=(), hwnd=22):
    node = SimpleNamespace(ClassName=cls, Name=name, AutomationId=aid,
                           NativeWindowHandle=hwnd, ControlTypeName='EditControl',
                           IsOffscreen=False, GetChildren=lambda: list(children))
    node.EditControl = Mock(return_value=SimpleNamespace(Exists=lambda *a: False))
    def first_child():
        items = node.GetChildren()
        for index, child in enumerate(items):
            child._next_sibling = items[index+1] if index+1 < len(items) else None
        return items[0] if items else None
    node.GetFirstChildControl = first_child
    node.GetNextSiblingControl = lambda: getattr(node, '_next_sibling', None)
    return node


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        self.edit = control('mmui::ChatInputField', '输入', 'chat_input_field')
        self.search = control('mmui::XValidatorTextEdit', '搜索')
        self.root = control('mmui::ChatSingleWindow', '测试会话',
                            'ChatSingleWindowtest@chatroom', [self.edit])
        for name, replacement in (
                ('win32gui.IsWindow', lambda h: h == 22),
                ('WeChatUIA._pid_from_hwnd', lambda h: 202)):
            p = patch('wechatauto.uia_driver.' + name, side_effect=replacement)
            p.start()
            self.addCleanup(p.stop)
        self.engine = WeChatUIA(hwnd=22)
        self.engine._anchor = Mock(side_effect=lambda h: self.root)
        self.engine._activate = Mock()
        self.engine._wechat_hwnds = Mock(return_value=[22])
        ensure = self.engine.ensure_window
        self.engine.ensure_window = Mock(side_effect=lambda **kw: ensure(wake=False))

    def history(self):
        self.root = control('mmui::SearchMsgUniqueChatWindow', '测试会话的聊天记录',
                            'test@chatroom', [self.search])

    def ready(self):
        self.assertTrue(self.engine.ensure_window(wake=False))

    def test_explicit_chat_and_history_are_materialized(self):
        self.ready()
        self.assertEqual(self.engine.window_kind(), 'chat')
        self.history()
        self.ready()
        self.assertEqual(self.engine.window_kind(), 'history')

    def test_default_discovery_still_ignores_standalone_windows(self):
        self.engine.hwnd = None
        self.assertFalse(self.engine.ensure_window(wake=False))
        self.history()
        self.assertFalse(self.engine.ensure_window(wake=False))

    def test_foreign_root_is_rejected_for_standalone_too(self):
        self.root.NativeWindowHandle = 11
        self.assertFalse(self.engine.ensure_window(wake=False))

    def test_chat_identity_is_title_not_input_placeholder(self):
        self.ready()
        self.assertEqual(self.engine.current_chat(), '测试会话')
        self.assertIs(self.engine._chat_input(), self.edit)

    def test_title_is_not_stripped_as_input_placeholder(self):
        self.root.Name = '测试语音输入'
        self.ready()
        self.assertEqual(self.engine.current_chat(), '测试语音输入')

    def test_fixed_chat_accepts_exact_title_or_native_id_without_search(self):
        self.engine._search_box = Mock(side_effect=AssertionError('不能搜索'))
        self.engine._resolve_search_keyword = Mock(side_effect=AssertionError('不能自动选择数据库'))
        for target in ('测试会话', 'test@chatroom'):
            with self.subTest(target=target):
                self.assertTrue(self.engine.open_chat(target))
        for target in ('测试', '另一会话', '', 'other@chatroom'):
            with self.subTest(target=target):
                self.assertFalse(self.engine.open_chat(target))

    def test_history_search_never_becomes_send_input(self):
        self.history()
        self.ready()
        self.assertIsNone(self.engine._chat_input())
        self.assertIsNone(self.engine.current_chat())
        self.engine._paste_into = Mock()
        self.assertFalse(self.engine.send_text('不得写入搜索框'))
        self.engine._paste_into.assert_not_called()
        self.assertFalse(self.engine.open_chat('测试会话'))

    def test_chat_without_real_input_does_not_use_arbitrary_edit(self):
        self.root.GetChildren = lambda: [control('mmui::XValidatorTextEdit', '其他编辑框')]
        self.ready()
        self.assertIsNone(self.engine._chat_input())

    def test_history_list_is_readable_and_preferred_to_other_lists(self):
        self.history()
        unrelated = control('mmui::RecyclerListView', aid='other_list')
        messages = control('mmui::RecyclerListView', aid='chat_log_message_list')
        self.root.GetChildren = lambda: [unrelated, messages]
        self.ready()
        self.assertIs(self.engine._message_list(), messages)

    def test_layout_distinguishes_standalone_types(self):
        self.assertEqual(self.engine.describe_layout()['layout'], 'standalone-chat')
        self.history()
        self.assertEqual(self.engine.describe_layout()['layout'], 'history')


class StandaloneGuiTests(unittest.TestCase):
    def gui(self, kind):
        gui = object.__new__(WeChatGUI)
        gui.main_hwnd = 22
        gui._uia = Mock()
        gui._uia.window_kind.return_value = kind
        gui._get_uia = Mock(return_value=gui._uia)
        gui._uia.current_chat.return_value = None if kind == 'history' else '测试会话'
        gui._uia.open_chat.return_value = False
        gui._uia._send_attempted = False
        gui._uia.send_text.return_value = False
        gui._update_render_rect = Mock()
        gui.ensure_visible = Mock(side_effect=AssertionError('不能走 OCR'))
        gui._probe_input_box = Mock(side_effect=AssertionError('不能猜输入框'))
        gui._uia_input_box = Mock(return_value=None)
        return gui

    def test_open_wrong_chat_never_falls_back(self):
        for kind in ('chat', 'history'):
            gui = self.gui(kind)
            self.assertFalse(gui.open_chat('另一会话'))

    def test_open_current_chat_uses_uia(self):
        gui = self.gui('chat')
        gui._uia.open_chat.return_value = True
        self.assertTrue(gui.open_chat('测试会话'))
        self.assertEqual(gui._current_chat, '测试会话')

    def test_history_and_missing_chat_input_never_probe_pixels(self):
        for kind in ('chat', 'history'):
            with self.subTest(kind=kind):
                self.assertIsNone(self.gui(kind).get_input_box())

    def test_chat_input_box_comes_from_uia(self):
        gui = self.gui('chat')
        gui._uia_input_box.return_value = (1, 2, 3, 4)
        self.assertEqual(gui.get_input_box(), (1, 2, 3, 4))

    def test_history_send_rejected_even_with_old_fast_send_cache(self):
        gui = self.gui('history')
        gui._current_chat = '测试会话'
        gui._last_input_box = (1, 2, 3, 4)
        gui.input_text = Mock(side_effect=AssertionError('不能复用输入框'))
        result = gui.send_msg('不得发送', who='测试会话')
        self.assertFalse(result)
        gui._uia.send_text.assert_not_called()

    def test_wrong_chat_or_uia_send_failure_never_falls_back(self):
        for who in ('另一会话', '测试会话', None):
            with self.subTest(who=who):
                gui = self.gui('chat')
                result = gui.send_msg('不得降级', who=who)
                self.assertFalse(result)
                if who == '另一会话':
                    gui._uia.send_text.assert_not_called()
                gui._uia.back_to_chat_tab.assert_not_called()

    def test_fixed_chat_sends_once_with_database_verification(self):
        gui = self.gui('chat')
        gui._uia.open_chat.return_value = True
        gui._uia.send_text.return_value = True
        gui._send_watermark = Mock(return_value={'seq': 10})
        gui._wait_landed = Mock(return_value=True)
        self.assertTrue(gui.send_msg('测试正文', who='test@chatroom', verify=True))
        gui._uia.send_text.assert_called_once_with('测试正文')
        gui._wait_landed.assert_called_once_with('测试正文', 'test@chatroom', {'seq': 10})

    def test_history_attachments_never_copy_paste_or_press_keys(self):
        gui = self.gui('history')
        gui.ensure_visible = Mock(return_value=True)
        gui._target_seq = Mock(return_value=10)
        gui.copy_files_to_clipboard = Mock(side_effect=AssertionError('不得复制附件'))
        gui._input = SimpleNamespace(key=Mock(side_effect=AssertionError('不得发送按键')))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'test.txt'
            path.write_text('测试', encoding='utf-8')
            for method in (gui.send_file, gui.send_image):
                with self.subTest(method=method.__name__):
                    self.assertFalse(method(str(path)))
        gui.copy_files_to_clipboard.assert_not_called()
        gui._input.key.assert_not_called()

    def test_known_standalone_type_blocks_fallback_when_uia_disappears(self):
        for kind in ('chat', 'history'):
            with self.subTest(kind=kind):
                gui = self.gui(kind)
                self.assertEqual(gui._window_kind(gui._uia), kind)
                gui._get_uia.return_value = None
                self.assertFalse(gui.open_chat('测试会话'))
                self.assertIsNone(gui.get_input_box())
                self.assertFalse(gui.send_msg('不得回退', who='测试会话'))
                self.assertFalse(gui.send_msg('不得回退'))

    def test_known_window_type_cannot_follow_a_different_handle(self):
        gui = self.gui('history')
        self.assertEqual(gui._window_kind(gui._uia), 'history')
        gui.main_hwnd = 11
        self.assertEqual(gui._window_kind(None), 'unknown')


if __name__ == '__main__':
    unittest.main()
