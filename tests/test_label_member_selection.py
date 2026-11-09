"""标签移出成员的实际命中区、选择人数与失败保护；不连接微信。"""
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechatauto.labels import LabelOps
from wechatauto import labels as labels_module


HEADER_CLASS = "mmui::ContactsManagerHBoxlayoutClickArea"


@dataclass
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top

    def contains(self, point):
        x, y = point
        return self.left <= x < self.right and self.top <= y < self.bottom


class Node:
    def __init__(self, name="", cls="", rect=None, children=(), kind="GroupControl"):
        self.Name, self.ClassName, self.ControlTypeName = name, cls, kind
        self.BoundingRectangle = rect or Rect(600, 236, 1320, 796)
        self.parent = None
        self.set_children(children)

    def set_children(self, children):
        self.children = list(children)
        for child in self.children:
            child.parent = self

    def GetChildren(self):
        return list(self.children)

    def GetFirstChildControl(self):
        return self.children[0] if self.children else None

    def GetNextSiblingControl(self):
        if self.parent is None:
            return None
        siblings = self.parent.children
        index = siblings.index(self) + 1
        return siblings[index] if index < len(siblings) else None

    def GetParentControl(self):
        return self.parent

    def GetRuntimeId(self):
        return [id(self)]


def count_bar(number="1"):
    return Node(children=[
        Node("已选择", "mmui::XTextView", kind="TextControl"),
        Node(number, "mmui::XButton", kind="ButtonControl"),
        Node("取消选择", "mmui::XTextView", kind="TextControl"),
        Node("移出标签", "mmui::XMouseEventView", kind="ButtonControl"),
    ])


class SelectionGeometryTests(unittest.TestCase):
    def setUp(self):
        self.ops = LabelOps(uia=object(), db=object())
        self.row = Node("甲  临时标签", labels_module.DETAIL_CELL_CLS,
                        Rect(800, 316, 1320, 364))
        self.header = Node("昵称", HEADER_CLASS, Rect(864, 280, 981, 312))
        self.win = Node(children=[self.header, self.row])

    def test_measured_checkbox_center_moves_and_scales_with_table(self):
        # 数值取自真实行、表头和选择框，变换它们而非复制被测定位公式。
        for scale, dx, dy in ((1, 0, 0), (1, 137, -200),
                              (1.25, 0, 0), (1.5, -150, 30), (2, 0, 0)):
            def transform(rect):
                return Rect(round(rect.left * scale + dx), round(rect.top * scale + dy),
                            round(rect.right * scale + dx), round(rect.bottom * scale + dy))
            with self.subTest(scale=scale, offset=(dx, dy)):
                row = Node("甲", labels_module.DETAIL_CELL_CLS,
                           transform(Rect(800, 316, 1320, 364)))
                header = Node("昵称", HEADER_CLASS, transform(Rect(864, 280, 981, 312)))
                root = Node(children=[header, row])
                self.assertEqual(self.ops._detail_checkbox_point(root, row),
                                 (round(832 * scale + dx), round(340 * scale + dy)))

    def test_missing_header_does_not_guess_a_click(self):
        self.win.set_children([self.row])
        self.assertIsNone(self.ops._detail_checkbox_point(self.win, self.row))

    def test_duplicate_headers_are_ambiguous(self):
        duplicate = Node("昵称", HEADER_CLASS, self.header.BoundingRectangle)
        self.win.set_children([self.header, duplicate, self.row])
        self.assertIsNone(self.ops._detail_checkbox_point(self.win, self.row))

    def test_invalid_header_and_row_rectangles_are_rejected(self):
        cases = [
            (Rect(799, 280, 981, 312), self.row.BoundingRectangle),
            (Rect(1320, 280, 1400, 312), self.row.BoundingRectangle),
            (Rect(864, 400, 981, 432), self.row.BoundingRectangle),
            (Rect(864, 280, 864, 312), self.row.BoundingRectangle),
            (Rect(1100, 280, 1200, 312), self.row.BoundingRectangle),
            (self.header.BoundingRectangle, Rect(800, 316, 1320, 316)),
        ]
        for header_rect, row_rect in cases:
            with self.subTest(header=header_rect, row=row_rect):
                self.header.BoundingRectangle = header_rect
                self.row.BoundingRectangle = row_rect
                self.assertIsNone(self.ops._detail_checkbox_point(self.win, self.row))

    def test_stale_header_or_row_control_does_not_guess_a_click(self):
        class StaleControl:
            def __init__(self, source, unavailable):
                self.source = source
                self.unavailable = unavailable

            def __getattr__(self, name):
                if name == self.unavailable:
                    raise RuntimeError("UIA control no longer available")
                return getattr(self.source, name)

        for stale_part, attribute in (("header", "Name"),
                                      ("header", "BoundingRectangle"),
                                      ("row", "BoundingRectangle")):
            with self.subTest(control=stale_part, attribute=attribute):
                header = (StaleControl(self.header, attribute)
                          if stale_part == "header" else self.header)
                row = (StaleControl(self.row, attribute)
                       if stale_part == "row" else self.row)
                root = Node(children=[header, row])
                self.assertIsNone(self.ops._detail_checkbox_point(root, row))


class SelectionCountTests(unittest.TestCase):
    def setUp(self):
        self.ops = LabelOps(uia=object(), db=object())

    def test_combined_picker_text_still_works(self):
        root = Node(children=[Node("已选择 3 个联系人")])
        self.assertEqual(self.ops._selected_count(root), 3)

    def test_manager_split_label_and_button_are_read_together(self):
        for number in ("0", "1", "12"):
            with self.subTest(number=number):
                self.assertEqual(self.ops._selected_count(count_bar(number)), int(number))

    def test_unrelated_numbers_are_not_selection_counts(self):
        root = Node(children=[Node("134", kind="ButtonControl"), Node("临时标签(1)")])
        self.assertIsNone(self.ops._selected_count(root))

    def test_number_from_another_parent_is_not_joined(self):
        root = Node(children=[Node(children=[Node("已选择")]),
                              Node(children=[Node("9", kind="ButtonControl")])])
        self.assertIsNone(self.ops._selected_count(root))

    def test_nonadjacent_or_nonnumeric_button_is_not_a_count(self):
        roots = [Node(children=[Node("已选择"), Node("提示"),
                                Node("9", kind="ButtonControl")]), count_bar("无法读取")]
        for root in roots:
            with self.subTest(root=root):
                self.assertIsNone(self.ops._selected_count(root))

    def test_conflicting_combined_and_split_counts_are_rejected(self):
        root = Node(children=[Node("已选择3个联系人"), count_bar("2")])
        self.assertIsNone(self.ops._selected_count(root))


class MemberTable(LabelOps):
    """只有点击真实选择框区域才改变勾选，头像点击只打开资料浮层。"""
    def __init__(self, names=("甲",), selected=(), block=(), count_text=None):
        super().__init__(uia=object(), db=object())
        self.names = list(names)
        self.selected = set(selected)
        self.block = set(block)
        self.count_text = count_text
        self.remaining = len(names)
        self.profile_open = False
        self.member_clicks = []
        self.remove_clicks = 0
        self.header = Node("昵称", HEADER_CLASS, Rect(864, 280, 981, 312))
        self.tag = Node("临时标签", labels_module.LABEL_ROW_CLS, Rect(600, 500, 800, 540))
        self.rows = [Node(name + "  临时标签", labels_module.DETAIL_CELL_CLS,
                          Rect(800, 316 + 48 * i, 1320, 364 + 48 * i))
                     for i, name in enumerate(names)]
        self.win = Node("通讯录管理")
        self._sync_tree()

    def _sync_tree(self):
        children = ([self.header] if self.header is not None else []) + [self.tag] + self.rows
        if self.selected:
            children.append(count_bar(self.count_text if self.count_text is not None
                                      else str(len(self.selected))))
        self.win.set_children(children)

    def open_manager(self):
        return {"ok": True, "hwnd": 22, "win": self.win}

    def _root(self, title):
        return 22, self.win

    def find_label(self, name, win=None):
        return {"cell": self.tag, "count": self.remaining}

    def _click(self, hwnd, ctrl, right=False, at=None, raise_it=True):
        if ctrl in self.rows:
            index = self.rows.index(ctrl)
            self.member_clicks.append(at)
            # 命中框来自实测矩形，与生产方法的表头几何计算相互独立。
            box = Rect(824, 332 + 48 * index, 840, 348 + 48 * index)
            if at is not None and box.contains(at):
                name = self.names[index]
                if name not in self.block:
                    self.selected.symmetric_difference_update({name})
            else:
                self.profile_open = True
        elif ctrl.Name == "移出标签":
            self.remove_clicks += 1
            self.remaining -= len(self.selected)
            self.selected.clear()
        self._sync_tree()
        return {"ok": True, "reason": "ok", "point": at}


class RemoveMemberTests(unittest.TestCase):
    def setUp(self):
        self.sleep_patch = patch("wechatauto.labels.time.sleep")
        self.gate_patch = patch("wechatauto.labels.rhythm.gate")
        self.sleep_patch.start()
        self.gate_patch.start()
        self.addCleanup(self.sleep_patch.stop)
        self.addCleanup(self.gate_patch.stop)

    def test_actual_checkbox_is_selected_and_member_is_removed(self):
        table = MemberTable()
        result = table.remove_members("临时标签", ["甲"])
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["count_before"], result["count_after"]), (1, 0))
        self.assertEqual(table.member_clicks, [(832, 340)])
        self.assertFalse(table.profile_open)
        self.assertEqual(table.remove_clicks, 1)

    def test_second_click_must_increase_count_even_when_bar_already_exists(self):
        table = MemberTable(names=("甲", "乙"), block=("乙",))
        real_click = table._click

        def hit_checkbox(hwnd, ctrl, right=False, at=None, raise_it=True):
            # 此例只验证计数：提供已确认命中，避免旧坐标问题挡住后续分支。
            if ctrl in table.rows:
                at = (832, 340 + 48 * table.rows.index(ctrl))
            return real_click(hwnd, ctrl, right=right, at=at, raise_it=raise_it)

        with patch.object(table, "_click", side_effect=hit_checkbox):
            result = table.remove_members("临时标签", ["甲", "乙"])
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["reason"].startswith("not-selected"), result)
        self.assertEqual(table.remove_clicks, 0)
        self.assertEqual(table.remaining, 2)

    def test_partial_member_count_decrease_is_not_reported_as_complete(self):
        table = MemberTable(names=("甲", "乙"))
        real_click = table._click

        def remove_only_one(hwnd, ctrl, right=False, at=None, raise_it=True):
            result = real_click(hwnd, ctrl, right=right, at=at, raise_it=raise_it)
            if ctrl.Name == "移出标签":
                # 两人都已勾选，但最终列表人数只少一人，不能宣称完整成功。
                table.remaining = 1
            return result

        with patch.object(table, "_click", side_effect=remove_only_one):
            result = table.remove_members("临时标签", ["甲", "乙"])
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["reason"].startswith("count-mismatch"), result)
        self.assertEqual(result["clicked"], ["甲", "乙"])
        self.assertEqual((result["count_before"], result["count_after"]), (2, 1))
        self.assertEqual(table.remove_clicks, 1)

    def test_duplicate_requested_members_are_selected_only_once(self):
        table = MemberTable(names=("甲", "乙"))
        result = table.remove_members("临时标签", ["甲", "甲", "乙", "甲", "乙"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["clicked"], ["甲", "乙"])
        self.assertEqual(table.member_clicks, [(832, 340), (832, 388)])
        self.assertEqual((result["count_before"], result["count_after"]), (2, 0))
        self.assertEqual(table.remove_clicks, 1)

    def test_existing_other_selection_is_not_removed(self):
        table = MemberTable(names=("甲", "乙"), selected=("乙",))
        result = table.remove_members("临时标签", ["甲"])
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["reason"].startswith("selection-not-empty"), result)
        self.assertEqual(table.member_clicks, [])
        self.assertEqual(table.remove_clicks, 0)
        self.assertEqual(table.selected, {"乙"})

    def test_existing_bar_with_unknown_count_is_not_assumed_empty(self):
        table = MemberTable(names=("甲", "乙"), selected=("乙",), count_text="无法读取")
        result = table.remove_members("临时标签", ["甲"])
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["reason"].startswith("selection-not-empty"), result)
        self.assertEqual(table.member_clicks, [])
        self.assertEqual(table.remove_clicks, 0)

    def test_missing_checkbox_geometry_does_not_click_avatar(self):
        table = MemberTable()
        table.header = None
        table._sync_tree()
        result = table.remove_members("临时标签", ["甲"])
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["reason"].startswith("no-checkbox"), result)
        self.assertEqual(table.member_clicks, [])
        self.assertFalse(table.profile_open)
        self.assertEqual(table.remove_clicks, 0)


if __name__ == "__main__":
    unittest.main()
