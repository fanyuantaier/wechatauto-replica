"""Win32 INPUT 布局及 Unicode 事件回归；SendInput 全部由内存桩替代。"""
import ctypes
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechatauto import guia


class InputAbiTests(unittest.TestCase):
    def test_keyboard_input_uses_complete_win32_input_size(self):
        # cbSize 是整个 INPUT 的大小，不是当前选中联合成员的大小。
        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(guia.INPUT), expected)
        self.assertEqual(ctypes.sizeof(guia.INPUT), ctypes.sizeof(guia.MOUSE_INPUT))

    def test_union_contains_all_native_input_variants(self):
        self.assertEqual(set(dict(guia._INPUT_UNION._fields_)), {"ki", "mi", "hi"})
        self.assertEqual(ctypes.sizeof(guia.HARDWAREINPUT), 8)

    def test_native_union_alignment_and_pointer_width(self):
        pointer_size = ctypes.sizeof(ctypes.c_void_p)
        self.assertEqual(guia.INPUT.u.offset, pointer_size)
        for native_type in (guia.KEYBDINPUT, guia.MOUSEINPUT):
            self.assertEqual(ctypes.sizeof(dict(native_type._fields_)["dwExtraInfo"]),
                             pointer_size)
            value = native_type()
            value.dwExtraInfo = (1 << (pointer_size * 8)) - 1
            self.assertEqual(value.dwExtraInfo, (1 << (pointer_size * 8)) - 1)

    def test_unicode_submission_passes_full_input_and_key_pairs(self):
        observed = []

        class User32:
            def SendInput(self, count, pointer, size):
                value = ctypes.cast(pointer, ctypes.POINTER(guia.INPUT)).contents
                observed.append((count, size, value.type, value.u.ki.wVk,
                                 value.u.ki.wScan, value.u.ki.dwFlags,
                                 value.u.ki.time, value.u.ki.dwExtraInfo))
                return count

        sender = guia.WinInput.__new__(guia.WinInput)
        sender._user32 = User32()
        with patch.object(guia.rhythm, "key_hold"), patch.object(guia.rhythm, "type_gap"):
            sender.type_unicode("@中")

        size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(observed, [
            (1, size, 1, 0, ord("@"), 0x0004, 0, 0),
            (1, size, 1, 0, ord("@"), 0x0006, 0, 0),
            (1, size, 1, 0, ord("中"), 0x0004, 0, 0),
            (1, size, 1, 0, ord("中"), 0x0006, 0, 0),
        ])

    def test_empty_unicode_does_not_submit_input(self):
        class User32:
            def SendInput(self, *_):
                raise AssertionError("空文本不应提交输入")

        sender = guia.WinInput.__new__(guia.WinInput)
        sender._user32 = User32()
        sender.type_unicode("")


if __name__ == "__main__":
    unittest.main()
