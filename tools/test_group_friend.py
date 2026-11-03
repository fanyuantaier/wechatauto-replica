# -*- coding: utf-8 -*-
"""群成员加好友的离线自测：假控件树 + 一个「假微信」演出界面状态变化。

跑法：python tools/test_group_friend.py

被测代码走真路径（open_group / open_panel / search_member / open_card /
card_state / send_request），只有三样是假的：**点击**、**顶层窗枚举**、**键盘粘贴**。
真机上最容易出事的恰好是这三样，所以 Sim 按实测形状演：

  点「聊天信息」      → 群成员面板出现在主窗树里
  在搜索框写入名字    → 结果列表换成 XTableCell 行（**子节点顺序故意和视觉顺序相反**）
  点结果行            → 弹出 ProfileUniquePop（按钮按好友/非好友两种）
  点「添加到通讯录」  → 新增一颗好友申请框（可能缺输入框/缺发送按钮/压根不弹）
  点「发送」          → 申请框关闭，记一笔

第一条断言就是这次修的问题：搜「小明」同时命中三行时，必须选**屏幕最上面**那行，
不能选树遍历先撞到的那行（Qt 回收列表的子节点顺序不等于视觉顺序）。
"""
import os
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from wechatauto.group_friend import GroupAddFriendOps, PANEL_CLS, PROFILE_POP_CLS  # noqa: E402

PASSED, FAILED = [], []


def check(name, got, want):
    ok = got == want
    (PASSED if ok else FAILED).append(name)
    print("%-4s %-52s got=%r want=%r" % ("ok" if ok else "FAIL", name, got, want))


def code(r):
    return ((r or {}).get("reason") or "").split("：")[0]


# ---------------------------------------------------------------------------
# 假控件
# ---------------------------------------------------------------------------
class Rect:
    def __init__(self, l, t, r, b):
        self.left, self.top, self.right, self.bottom = l, t, r, b

    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top


class Node:
    def __init__(self, name="", cls="", ctype="GroupControl", kids=None,
                 rect=(0, 0, 100, 40)):
        self.Name = name
        self.ClassName = cls
        self.ControlTypeName = ctype
        self.AutomationId = ""
        self.BoundingRectangle = Rect(*rect)
        self.NativeWindowHandle = 0
        self.ProcessId = 0
        self._kids = list(kids or [])

    def GetChildren(self):
        return list(self._kids)

    def GetFirstChildControl(self):
        for index, child in enumerate(self._kids):
            child._next_sibling = self._kids[index+1] if index+1 < len(self._kids) else None
        return self._kids[0] if self._kids else None

    def GetNextSiblingControl(self):
        return getattr(self, '_next_sibling', None)

    def add(self, *kids):
        self._kids.extend(kids)

    def remove(self, node):
        self._kids = [k for k in self._kids if k is not node]

    def SendKeys(self, keys, waitTime=0):
        raise RuntimeError("假控件不发真按键")


def row(name, top, h=96):
    return Node(name, "mmui::XTableCell", "ListItemControl",
                rect=(1910, top, 2410, top + h))


# ---------------------------------------------------------------------------
# 假微信
# ---------------------------------------------------------------------------
class Sim:
    MAIN_HWND, CARD_HWND, DIALOG_HWND = 1000, 5000, 6000

    def __init__(self, results=(("小明", 336), ("小明妈妈", 432), ("小明爸爸", 528)),
                 kind="friend", dialog=True, dialog_edit=True, send_btn=True):
        self.kind = kind              # friend / add / unknown
        self.dialog_wanted = dialog
        self.dialog_edit = dialog_edit
        self.dialog_send = send_btn
        self.chat = "某个群"
        self.clicks = []
        self.edit_value = ""
        self.sent = False
        self.card = None
        self.dialog_win = None
        self.search_text = ""

        self.info_btn = Node("聊天信息", "mmui::XButton", "ButtonControl",
                             rect=(2322, 82, 2378, 138))
        self.box = Node("搜索", "mmui::XValidatorTextEdit", "EditControl",
                        rect=(1992, 188, 2324, 228))
        self.rows_list = Node("", "mmui::StickyHeaderRecyclerListView", "ListControl",
                              [], rect=(1910, 252, 2410, 1600))
        self.panel = Node("", PANEL_CLS, "GroupControl",
                          [self.box, self.rows_list], rect=(1910, 160, 2410, 1612))
        self.main = Node("微信", "mmui::MainWindow", "WindowControl",
                         [self.info_btn], rect=(1018, 0, 2418, 1620))
        self.main.NativeWindowHandle = self.MAIN_HWND
        # 结果是视觉顺序 (name, top)；树里**倒着放**，模拟 Qt 回收列表的顺序
        self.visual = list(results)
        self._rows = [row(n, t) for n, t in self.visual]
        self.rows_list._kids = []          # 搜索之前列表里没有结果行
        self.panel_open = False
        self.uia = FakeUIA(self)

    # -- 顶层窗 ---------------------------------------------------------
    def top_windows(self):
        out = [(self.MAIN_HWND, self.main)]
        if self.card is not None:
            out.append((self.CARD_HWND, self.card))
        if self.dialog_win is not None:
            out.append((self.DIALOG_HWND, self.dialog_win))
        return out

    # -- 点击 -----------------------------------------------------------
    def click(self, hwnd, ctrl):
        if ctrl is self.info_btn:
            self.clicks.append("info")
            if not self.panel_open:
                self.panel_open = True
                self.main.add(self.panel)
            return {"ok": True, "reason": "ok"}
        if ctrl is self.box:
            # 拟人化：搜索前先点一下输入框（随机落点由 LabelOps._click 负责）
            self.clicks.append("box")
            return {"ok": True, "reason": "ok"}
        if ctrl in self._rows:
            self.clicks.append("row:%s" % ctrl.Name)
            self.open_card(ctrl.Name)
            return {"ok": True, "reason": "ok"}
        if self.card is not None and ctrl in self.card.GetChildren():
            if (ctrl.Name or "") == "添加到通讯录":
                self.clicks.append("add")
                if self.dialog_wanted:
                    self.open_dialog()
                return {"ok": True, "reason": "ok"}
        if self.dialog_win is not None:
            if (ctrl.Name or "") in ("发送", "确定"):
                self.clicks.append("send")
                self.sent = True
                self.dialog_win = None
                return {"ok": True, "reason": "ok"}
            if ctrl.ControlTypeName == "EditControl":
                self.clicks.append("edit")
                return {"ok": True, "reason": "ok"}
        self.clicks.append("other:%s" % (ctrl.Name or ""))
        return {"ok": True, "reason": "ok"}

    def open_card(self, name):
        kids = []
        if self.kind == "add":
            kids.append(Node("添加到通讯录", "mmui::ProfileBottomButton", "ButtonControl"))
        elif self.kind == "friend":
            for n in ("发消息", "语音聊天", "视频聊天"):
                kids.append(Node(n, "mmui::ProfileBottomButton", "ButtonControl"))
        self.card = Node("Weixin", PROFILE_POP_CLS, "WindowControl", kids)
        self.card.NativeWindowHandle = self.CARD_HWND

    def open_dialog(self):
        kids = []
        if self.dialog_edit:
            kids.append(Node("发送添加朋友申请", "mmui::XLineEdit", "EditControl"))
        if self.dialog_send:
            # 4.1.15.53 真机实测：申请框的提交按钮是「确定」，不是「发送」
            kids.append(Node("确定", "mmui::XOutlineButton", "ButtonControl"))
        self.dialog_win = Node("Weixin", "mmui::VerifyFriendWindow", "WindowControl", kids)
        self.dialog_win.NativeWindowHandle = self.DIALOG_HWND


class FakeUIA:
    def __init__(self, sim):
        self.sim = sim
        self._win = sim.main

    def ensure_window(self):
        return True

    def current_chat(self):
        return self.sim.chat

    def open_chat(self, name):
        self.sim.chat = name
        return True

    def _set_text(self, ctrl, text):
        if ctrl is self.sim.box:
            self.sim.search_text = text
            # 搜索结果：树里倒着放，模拟 Qt 回收列表的子节点顺序
            hit = [r for r in self.sim._rows if text in r.Name]
            self.sim.rows_list._kids = list(reversed(hit))
        else:
            self.sim.edit_value = text
        return True

    def _clip_set(self, text):
        pass

    def _click_at(self, x, y, right=False):
        pass


class TestOps(GroupAddFriendOps):
    def __init__(self, sim):
        super().__init__(uia=sim.uia, db=None)
        self.sim = sim
        self.appear_timeout = 0.5

    def _click(self, hwnd, ctrl, right=False, at=None, raise_it=True):
        return self.sim.click(hwnd, ctrl)

    def _top_windows(self):
        return self.sim.top_windows()

    def _raise(self, hwnd):
        return True

    def _type_text(self, ctrl, text):
        if ctrl is self.sim.box:
            self.sim.search_text = text
            hit = [r for r in self.sim._rows if text in r.Name]
            self.sim.rows_list._kids = list(reversed(hit))
        else:
            self.sim.edit_value = text
        return True


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def test_first_visual_row():
    """三行命中时选屏幕最上面那行（树顺序是反的——这次修的 bug）。"""
    sim = Sim(results=(("小明妈妈", 336), ("小明爸爸", 432), ("小明", 528)),
              kind="friend")
    ops = TestOps(sim)
    r = ops.probe("某个群", "小明")
    check("probe.ok", r["ok"], True)
    check("多行结果按屏幕顺序", r["results"],
          ["小明妈妈", "小明爸爸", "小明"])
    check("选中第一行", r["selected"], "小明妈妈")
    check("第一行是好友（但选的是视觉第一行）", r["reason"], "already-friend")
    check("好友不点添加", "add" in sim.clicks, False)


def test_add_then_send():
    """非好友：添加到通讯录 → 申请框 → 填留言 → 发送。"""
    sim = Sim(kind="add")
    ops = TestOps(sim)
    r = ops.add("某个群", "小明妈妈", message="我是同班家长")
    check("add.ok", r["ok"], True)
    check("add.added", r.get("added"), True)
    check("点击顺序", sim.clicks,
          ["info", "box", "row:小明妈妈", "add", "edit", "send"])
    check("留言已填", sim.edit_value, "我是同班家长")
    check("真的点了发送", sim.sent, True)
    check("申请框已关", sim.dialog_win, None)


def test_dry_run_does_not_add():
    sim = Sim(kind="add")
    ops = TestOps(sim)
    r = ops.add("某个群", "小明妈妈", dry_run=True)
    check("dry-run.ok", r["ok"], True)
    check("dry-run 可添加", r.get("add"), True)
    check("dry-run 不点添加", "add" in sim.clicks, False)
    check("dry-run 不发送", sim.sent, False)


def test_already_friend_add_is_noop():
    sim = Sim(kind="friend")
    ops = TestOps(sim)
    r = ops.add("某个群", "小明")
    check("已是好友 ok", r["ok"], True)
    check("已是好友 reason", r["reason"], "already-friend")
    check("已是好友 added=False", r.get("added"), False)
    check("已是好友不点添加", "add" in sim.clicks, False)


def test_no_dialog():
    sim = Sim(kind="add", dialog=False)
    ops = TestOps(sim)
    r = ops.add("某个群", "小明妈妈")
    check("无申请框失败", r["ok"], False)
    check("无申请框 reason", code(r), "no-dialog")
    check("无申请框不点发送", sim.sent, False)


def test_no_edit_with_message():
    sim = Sim(kind="add", dialog_edit=False)
    ops = TestOps(sim)
    r = ops.add("某个群", "小明妈妈", message="你好")
    check("无留言框失败", code(r), "no-edit")
    check("无留言框不发送", sim.sent, False)


def test_no_send_button():
    sim = Sim(kind="add", send_btn=False)
    ops = TestOps(sim)
    r = ops.add("某个群", "小明妈妈")
    check("无发送按钮失败", code(r), "no-send")
    check("无发送按钮不发送", sim.sent, False)


def test_unknown_card():
    sim = Sim(kind="unknown")
    ops = TestOps(sim)
    r = ops.add("某个群", "小明妈妈")
    check("认不出资料卡", code(r), "unknown-card")
    check("认不出不点添加", "add" in sim.clicks, False)


def test_no_result():
    sim = Sim(results=())
    ops = TestOps(sim)
    r = ops.add("某个群", "查无此人")
    check("无结果失败", code(r), "no-result")
    check("无结果不点添加", "add" in sim.clicks, False)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
