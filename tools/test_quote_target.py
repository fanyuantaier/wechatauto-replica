"""引用目标回归：OCR 空格一致性、歧义拒绝和发送失败传播。

运行：python tools/test_quote_target.py；不连接或操作真实微信。
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechatauto.guia import WeChatGUI


class QuoteTargetTests(unittest.TestCase):
    def setUp(self):
        self.gui = object.__new__(WeChatGUI)
        self.gui.origin_x = self.gui.origin_y = 0
        self.gui.main_hwnd = 42
        self.gui._get_uia = Mock(return_value=None)
        self.gui.right_pane_left, self.gui.render_w, self.gui.render_h = 300, 1500, 975
        self.gui._input = SimpleNamespace(send_input_click=Mock(), _user32=Mock())
        self.gui.ensure_visible = Mock(return_value=True)
        self.gui.open_chat = Mock(return_value=True)
        self.gui.get_input_box = Mock(return_value=(300, 840, 1500, 920))
        self.gui.wx_click = Mock()
        self.gui.input_text = Mock(return_value=True)
        self.gui.click_send = Mock(return_value=True)
        self.gui._send_watermark = Mock(return_value=123)
        self.gui._verify_sent = Mock(return_value=True)
        self.gui._last_message_y = Mock(return_value=600)
        self.gui._locate_last_message_pt = Mock(return_value=(500, 600))
        sleeper = patch('wechatauto.guia.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def messages(self, *texts):
        rows = [(text, 400, 500 + n * 50, 100, 20) for n, text in enumerate(texts)]
        self.gui.ocr = Mock(side_effect=[rows, [('引用', 450, 550, 40, 20)]])

    def test_spaced_parameter_matches_normalized_ocr(self):
        self.messages('引用原文甲')
        result = self.gui.quote_msg('回复', target_text='引用 原文甲')
        self.assertEqual(result['status'], '成功')
        self.gui._input.send_input_click.assert_called_once_with(450, 510, right=True)
        self.gui.click_send.assert_called_once()

    def test_unique_substring_still_supported(self):
        self.messages('本周会议安排')
        self.assertEqual(self.gui.quote_msg('回复', target_text='会 议')['status'], '成功')

    def test_normalized_collision_and_repeated_text_do_not_click(self):
        for rows in [('引用原文甲', '引用 原文甲'), ('收到', '收到'), ('收到', '已收到')]:
            with self.subTest(rows=rows):
                self.messages(*rows)
                result = self.gui.quote_msg('回复', target_text=rows[0])
                self.assertEqual(result['status'], '失败')
                self.gui._input.send_input_click.assert_not_called()

    def test_missing_and_blank_targets_do_not_click(self):
        for target in ('不存在', '   '):
            with self.subTest(target=target):
                self.messages('原文')
                self.assertEqual(self.gui.quote_msg('回复', target_text=target)['status'], '失败')
                self.gui._input.send_input_click.assert_not_called()

    def test_send_gate_failure_is_reported_even_without_verification(self):
        for verify in (False, True):
            with self.subTest(verify=verify):
                self.messages('原文')
                self.gui.click_send.return_value = False
                result = self.gui.quote_msg('回复', target_text='原文', verify=verify)
                self.assertEqual(result['status'], '失败')
                self.gui._verify_sent.assert_not_called()

    def test_missing_input_or_failed_chat_switch_stops_before_click(self):
        self.gui.get_input_box.return_value = None
        self.assertEqual(self.gui.quote_msg('回复', target_text='原文')['status'], '失败')
        self.gui.open_chat.return_value = False
        self.assertEqual(self.gui.quote_msg('回复', who='测试好友')['status'], '失败')
        self.gui._input.send_input_click.assert_not_called()

    def test_omitted_target_keeps_latest_message_behavior(self):
        self.gui.ocr = Mock(return_value=[('引用', 450, 550, 40, 20)])
        self.assertEqual(self.gui.quote_msg('回复')['status'], '成功')
        self.gui._locate_last_message_pt.assert_called_once_with(600)

    def test_omitted_target_uses_uia_latest_row_instead_of_ocr(self):
        # 最后一条是表情/图片时 OCR 带内只剩上一行昵称（issue #49），
        # UIA 可用时必须改走消息行控件矩形 + 像素重心
        uia = SimpleNamespace(_win=SimpleNamespace(NativeWindowHandle=42),
                              _latest_message_point=Mock(return_value=(750, 800)),
                              _uia_find_menu_item=Mock(return_value=None))
        self.gui._get_uia.return_value = uia
        self.gui.ocr = Mock(return_value=[('引用', 450, 550, 40, 20)])
        self.assertEqual(self.gui.quote_msg('回复')['status'], '成功')
        uia._latest_message_point.assert_called_once()
        self.gui._last_message_y.assert_not_called()
        self.gui._locate_last_message_pt.assert_not_called()
        self.gui._input.send_input_click.assert_called_once_with(750, 800, right=True)

    def test_uia_point_from_foreign_window_is_ignored(self):
        uia = SimpleNamespace(_win=SimpleNamespace(NativeWindowHandle=99),
                              _latest_message_point=Mock(return_value=(750, 800)))
        self.gui._get_uia.return_value = uia
        self.gui.ocr = Mock(return_value=[('引用', 450, 550, 40, 20)])
        self.assertEqual(self.gui.quote_msg('回复')['status'], '成功')
        uia._latest_message_point.assert_not_called()
        self.gui._locate_last_message_pt.assert_called_once_with(600)

    def test_uia_without_latest_row_falls_back_to_ocr(self):
        uia = SimpleNamespace(_win=SimpleNamespace(NativeWindowHandle=42),
                              _latest_message_point=Mock(return_value=None),
                              _uia_find_menu_item=Mock(return_value=None))
        self.gui._get_uia.return_value = uia
        self.gui.ocr = Mock(return_value=[('引用', 450, 550, 40, 20)])
        self.assertEqual(self.gui.quote_msg('回复')['status'], '成功')
        self.gui._locate_last_message_pt.assert_called_once_with(600)
        self.gui._input.send_input_click.assert_called_once_with(500, 600, right=True)

    def test_reply_without_target_also_uses_uia_latest_row(self):
        uia = SimpleNamespace(_win=SimpleNamespace(NativeWindowHandle=42),
                              _latest_message_point=Mock(return_value=(750, 800)),
                              _uia_find_menu_item=Mock(return_value=None))
        self.gui._get_uia.return_value = uia
        self.gui.ocr = Mock(return_value=[('引用', 450, 550, 40, 20)])
        self.assertEqual(self.gui.reply_msg('回复正文')['status'], '成功')
        uia._latest_message_point.assert_called_once()
        self.gui.input_text.assert_called_once_with('回复正文')
        self.gui._locate_last_message_pt.assert_not_called()

    def test_real_menu_control_precedes_ocr_and_reports_click_failure(self):
        item = object()
        uia = SimpleNamespace(_win=SimpleNamespace(NativeWindowHandle=42),
                              _uia_find_menu_item=Mock(return_value=item),
                              _uia_click_menu_item=Mock(return_value=True))
        self.gui._get_uia.return_value = uia
        self.messages('原文')
        self.assertEqual(self.gui.quote_msg('回复', target_text='原文')['status'], '成功')
        self.assertEqual(self.gui.ocr.call_count, 1)  # 菜单不再交给 OCR 猜位置。
        uia._uia_click_menu_item.assert_called_once_with(item)
        self.gui.input_text.reset_mock()
        uia._uia_click_menu_item.return_value = False
        self.messages('原文')
        self.assertEqual(self.gui.quote_msg('回复', target_text='原文')['status'], '失败')
        self.gui.input_text.assert_not_called()

    def test_menu_from_other_window_is_not_used(self):
        uia = SimpleNamespace(_win=SimpleNamespace(NativeWindowHandle=99),
                              _uia_find_menu_item=Mock())
        self.gui._get_uia.return_value = uia
        self.messages('原文')
        self.assertEqual(self.gui.quote_msg('回复', target_text='原文')['status'], '成功')
        uia._uia_find_menu_item.assert_not_called()

    def test_body_containing_quote_label_is_not_treated_as_menu(self):
        for menu in ([('这是引用原文', 400, 500, 100, 20)],
                     [('引用', 400, 500, 40, 20), ('引用', 600, 500, 40, 20)]):
            self.gui.ocr = Mock(side_effect=[[('原文', 400, 500, 100, 20)], menu])
            self.assertEqual(self.gui.quote_msg('回复', target_text='原文')['status'], '失败')
            self.gui.input_text.assert_not_called()

    def test_reply_uses_requested_target_and_preserves_body(self):
        for target, point in [('原文甲', (450, 510)), ('原文乙', (450, 560))]:
            with self.subTest(target=target):
                self.messages('原文甲', '原文乙')
                self.gui._input.send_input_click.reset_mock()
                self.gui.input_text.reset_mock()
                result = self.gui.reply_msg('真正的回复正文', target_text=target)
                self.assertEqual(result['status'], '成功')
                self.gui._input.send_input_click.assert_called_once_with(*point, right=True)
                self.gui.input_text.assert_called_once_with('真正的回复正文')
                self.gui._last_message_y.assert_not_called()

    def test_reply_missing_or_ambiguous_target_cannot_send(self):
        for target in ('不存在', '原文'):
            with self.subTest(target=target):
                self.messages('原文甲', '原文乙')
                self.assertEqual(self.gui.reply_msg('回复', target_text=target)['status'], '失败')
                self.gui._input.send_input_click.assert_not_called()
                self.gui.input_text.assert_not_called()

    def test_reply_propagates_send_failure(self):
        self.messages('原文甲')
        self.gui.click_send.return_value = False
        self.assertEqual(self.gui.reply_msg('回复', target_text='原文甲', verify=True)['status'], '失败')
        self.gui._verify_sent.assert_not_called()


if __name__ == '__main__':
    unittest.main()
