"""UIA 循环及账号边界回归；不连接真实微信。"""
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechatauto.uia_driver import WeChatUIA, _find_by
from wechatauto.wx import WeChat, _find_descendant


class Node:
    def __init__(self, identity, *, aid='', cls='', kind='GroupControl', hwnd=22):
        self.identity = identity
        self.AutomationId = aid
        self.ClassName = cls
        self.ControlTypeName = kind
        self.NativeWindowHandle = hwnd
        self.children = []
        self.reads = 0

    def GetRuntimeId(self):
        return [42, self.identity]

    def GetChildren(self):
        raise AssertionError('GetChildren 无法在兄弟循环内检查超时')

    def GetFirstChildControl(self):
        self.reads += 1
        for index, child in enumerate(self.children):
            child.next_sibling = self.children[index+1] if index+1 < len(self.children) else None
        return self.children[0] if self.children else None

    def GetNextSiblingControl(self):
        return getattr(self, 'next_sibling', None)


class TraversalTests(unittest.TestCase):
    def test_runtime_identity_stops_ancestor_cycle_across_wrappers(self):
        first = Node(1)
        child = Node(2)
        wrapped_first = Node(1)
        first.children = [child]
        child.children = [wrapped_first]
        wrapped_first.children = [child]
        visited = []
        self.assertIsNone(_find_by(first, lambda n: visited.append(n.identity) or False))
        self.assertEqual(visited, [1, 2])
        self.assertEqual(wrapped_first.reads, 0)

    def test_cycle_does_not_hide_valid_sibling(self):
        root = Node(1)
        bad = Node(2)
        match = Node(3)
        root.children = [match, bad]
        bad.children = [root]
        self.assertIs(_find_by(root, lambda n: n is match), match)
        self.assertEqual(root.reads, 1)

    def test_empty_or_unavailable_runtime_id_does_not_merge_distinct_nodes(self):
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                root, target = Node(1), Node(2)
                root.children = [target]
                if unavailable:
                    root.GetRuntimeId = Mock(side_effect=RuntimeError('元素已失效'))
                    target.GetRuntimeId = Mock(side_effect=RuntimeError('元素已失效'))
                else:
                    root.GetRuntimeId = target.GetRuntimeId = lambda: []
                self.assertIs(_find_by(root, lambda n: n is target), target)

    def test_sibling_cycle_across_wrappers_does_not_trap_enumeration(self):
        root, left, right, wrapped_left = Node(1), Node(2), Node(3), Node(2)
        root.GetFirstChildControl = Mock(return_value=left)
        left.next_sibling, right.next_sibling = right, wrapped_left
        wrapped_left.GetNextSiblingControl = Mock(side_effect=AssertionError('必须在重复节点处停止'))
        visited = []
        self.assertIsNone(_find_by(root, lambda n: visited.append(n.identity) or False))
        self.assertEqual(visited, [1, 3, 2])

    def test_deadline_is_checked_during_sibling_enumeration(self):
        root, first = Node(1), Node(2)
        root.GetFirstChildControl = Mock(return_value=first)
        first.GetNextSiblingControl = Mock(return_value=Node(3))
        with patch('wechatauto.uia_driver.time.monotonic', side_effect=[0, 0, 0, 2, 2]):
            self.assertIsNone(_find_by(root, lambda n: False, deadline=1))
        first.GetNextSiblingControl.assert_called_once()

    def test_moment_lookup_keeps_child_order_and_does_not_repeat_cycle(self):
        root, left, right = Node(1), Node(2), Node(3)
        root.children = [left, right]
        left.children = [root]
        seen = []
        self.assertIs(_find_descendant(root, lambda n: seen.append(n.identity) or n is right), right)
        self.assertEqual(seen, [1, 2, 3])

    def test_depth_limit_does_not_read_children_outside_limit(self):
        root = Node(1)
        root.children = [Node(2)]
        self.assertIsNone(_find_by(root, lambda n: False, max_depth=0))
        self.assertEqual(root.reads, 0)

    def test_expired_search_deadline_performs_no_tree_read(self):
        root = Node(1)
        self.assertIsNone(_find_by(root, lambda n: False, deadline=time.monotonic()-1))
        self.assertEqual(root.reads, 0)


class SearchScopeTests(unittest.TestCase):
    def setUp(self):
        self.main = Node(1, cls='mmui::MainWindow')
        self.engine = object.__new__(WeChatUIA)
        self.engine.hwnd = 22
        self.engine._bound_pid = 202
        self.engine._win = self.main
        self.engine._find_main = Mock(return_value=self.main)
        self.engine._pid_from_hwnd = Mock(side_effect=lambda h: {22:202,23:202,24:202,11:101}.get(h))
        self.engine._search_popup_hwnds = Mock(return_value=[])
        for name in ('GetRootControl', 'ListControl'):
            p = patch('wechatauto.uia_driver.auto.'+name, side_effect=AssertionError('禁止全桌面搜索'))
            p.start()
            self.addCleanup(p.stop)

    def test_new_search_anchor_is_found_inside_bound_window(self):
        result = Node(2, aid='MainView.search_list', kind='ListControl')
        self.main.children = [result]
        self.assertIs(self.engine._find_search_list(.05), result)

    def test_missing_result_never_returns_other_account_list(self):
        self.assertIsNone(self.engine._find_search_list(.01))
        self.engine._find_main.assert_called()

    def test_replaced_window_does_not_use_stale_cached_root(self):
        self.main.children = [Node(2, aid='search_list', kind='ListControl')]
        self.engine._find_main.return_value = None
        self.assertIsNone(self.engine._find_search_list(.01))
        self.assertEqual(self.main.reads, 0)

    def test_owned_popup_is_searched_but_foreign_process_is_not(self):
        popup = Node(3, hwnd=23)
        result = Node(4, aid='search_list', kind='ListControl', hwnd=23)
        popup.children = [result]
        self.engine._search_popup_hwnds.return_value = [23,11]
        with patch('wechatauto.uia_driver.auto.ControlFromHandle', return_value=popup) as anchor:
            self.assertIs(self.engine._find_search_list(.05), result)
        anchor.assert_called_once_with(23)

    def test_popup_enumeration_requires_same_process_and_owner_chain(self):
        self.engine._search_popup_hwnds = WeChatUIA._search_popup_hwnds.__get__(self.engine)
        def enum(callback, arg):
            for hwnd in (11,22,23,24):
                callback(hwnd,arg)
        with patch('wechatauto.uia_driver.win32gui.EnumWindows',side_effect=enum), \
             patch('wechatauto.uia_driver.win32gui.IsWindowVisible',return_value=True), \
             patch('wechatauto.uia_driver.win32gui.GetWindow',side_effect=lambda h,flag:{23:22,24:0}.get(h,0)):
            self.assertEqual(self.engine._search_popup_hwnds(22,202),[23])


class MomentBindingTests(unittest.TestCase):
    def test_moment_wrapper_uses_the_clients_bound_window(self):
        client = object.__new__(WeChat)
        client._moment_api = None
        client._gui = SimpleNamespace(main_hwnd=22,_get_uia=Mock(return_value=object()))
        with patch('wechatauto.ui.main.WeChatMainWnd') as factory:
            self.assertIs(client._ensure_moment_api(),factory.return_value)
        factory.assert_called_once_with(hwnd=22)


if __name__ == '__main__':
    unittest.main()
