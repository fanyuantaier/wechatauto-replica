# -*- coding: utf-8 -*-
"""右键转发的离线自测：假的「微信发送给」窗口 + 假的 UIA 入口。

跑法：python tools/test_forward.py
      python tools/test_forward.py --mutateN   打一处变异，验证判据真的会红（N=1..7）

被测的是 :mod:`wechatauto.forward` 的**判断链**，不是控件本身。假窗口照真机
4.1.15.13 的样子搭（实测锚点来自 label_probe/forward_probe*.log）：

  行     mmui::SPSelectionContactRow      Name=会话/联系人显示名
  搜索   mmui::XValidatorTextEdit          Name=「搜索」
  已选   mmui::SPChoiceContactRow          Name=「移除<显示名>」
  按钮   mmui::XButton aid=confirm_btn     Name=「发送」/「分别发送(N)」
  按钮   mmui::XButton aid=cancel_btn      Name=「取消」

最要紧的几条判据都来自那颗按钮：**它上面写的数字就是微信准备发给几个人**。
勾了 4 个但按钮写 3，就必须停下来报 count-mismatch，不能把消息发出去。
"""
import sys
import types

from wechatauto import forward as F
from wechatauto.forward import ForwardOps
from wechatauto.labels import (CHECKBOX_INSET_PX, PICKER_ROW_CLS,
                               PICKER_SEARCH_ROW_CLS, SEARCH_BOX_NAME)

PASSED, FAILED, EVENTS = [], [], []
DEFERRED = []      # 后面才定义的类，变异开关先把手法存这儿
MUT = next((a for a in sys.argv if a.startswith("--mutate")), None)

# 假时钟：真代码里的 time.sleep 一律不真睡，只推进虚拟时间（否则一轮测试要跑几分钟，
# 而且 deadline 循环靠真墙钟走，判据就变成了比谁睡得久）。
CLOCK = [1000.0]


def _fake_sleep(sec):
    CLOCK[0] += float(sec)


def _fake_time():
    return CLOCK[0]


for _mod in (F, sys.modules["wechatauto.labels"]):
    _mod.time = types.SimpleNamespace(sleep=_fake_sleep, time=_fake_time)
F.rhythm.gate = lambda kind="write": 0.0
sys.modules["wechatauto.labels"].rhythm.gate = lambda kind="write": 0.0


def check(name, got, want):
    if got == want:
        PASSED.append(name)
        print("ok   %-58s got=%r" % (name, got))
    else:
        FAILED.append(name)
        print("FAIL %-58s got=%r want=%r" % (name, got, want))


def code(r):
    return ((r or {}).get("reason") or "").split("：")[0]


class Rect:
    def __init__(self, l, t, r, b):
        self.left, self.top, self.right, self.bottom = l, t, r, b

    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top


class Node:
    def __init__(self, name="", cls="", ctype="GroupControl", kids=None,
                 aid="", rect=(814, 462, 1454, 562)):
        self.Name = name
        self.ClassName = cls
        self.ControlTypeName = ctype
        self.AutomationId = aid
        self.BoundingRectangle = Rect(*rect)
        self.NativeWindowHandle = 0
        self._kids = kids or []

    def GetChildren(self):
        return list(self._kids)

    def add(self, *kids):
        self._kids.extend(kids)


class FakeUIA:
    """forward.py 用到的那几个 uia 原语，够就行。"""

    def __init__(self, sim):
        self.sim = sim
        self.clicks = []
        self.typed = []
        self.rights = []
        self._win = None

    # --- 会话/消息
    def open_chat(self, who):
        EVENTS.append(("open_chat", who))
        return not self.sim.chat_fails

    def ensure_window(self):
        return True

    def _right_click_latest_row(self):
        EVENTS.append("right_latest")
        return None if self.sim.no_message else (2751, 1315)

    def find_in_message_list(self, predicate, match_last=False, max_scrolls=30):
        EVENTS.append(("find", match_last))
        if self.sim.match_hit is None:
            return None
        return ("mmui::ChatItemView", self.sim.match_hit,
                Rect(1800, 900, 2600, 980))

    @staticmethod
    def _row_content_point(r):
        # 与真实现同语义：4.2.0 行中心在气泡外，右键点要落在内容上
        return ((r.left + r.right) // 2, (r.top + r.bottom) // 2)

    def _set_cursor(self, x, y):
        pass

    def _right_click(self):
        self.rights.append(1)
        EVENTS.append("right_at")

    def _uia_find_menu_item(self, want):
        if not self.sim.menu_item_present:
            return None
        return Node(want + "...", "mmui::XMenuView", "MenuItemControl",
                    aid="XMenuItem", rect=(2746, 1202, 3050, 1268))

    def _click_at(self, x, y, right=False):
        self.clicks.append((int(x), int(y), bool(right)))
        if self.sim.menu_item_present:
            self.sim.open_picker()            # 点「转发...」= 转发窗出现

    def _set_text(self, ctrl, text):
        self.typed.append(text)
        if self.sim.search_fails:
            return False
        self.sim.searched = text
        self.sim.refresh()          # 打上搜索词 = 列表换成搜索结果那一族行
        return True


class Sim:
    """假的「微信发送给」窗口状态机。"""

    def __init__(self, names=("文件传输助手", "送你挖银子", "小明"),
                 searchable=("同学甲",), chat_fails=False, no_message=False,
                 menu_item_present=True, check_fails=False, send_keeps_open=False,
                 search_fails=False, match_hit="测试文本123", cap=None,
                 stale_after_send=False):
        self.names = list(names)
        self.searchable = list(searchable)
        self.chat_fails = chat_fails
        self.no_message = no_message
        self.menu_item_present = menu_item_present
        self.check_fails = check_fails
        self.send_keeps_open = send_keeps_open
        self.search_fails = search_fails
        self.match_hit = match_hit
        self.cap = cap                    # 最多能勾几个人（真机上界没测出来）
        self.stale_after_send = stale_after_send
        self.picker = None
        self.chosen = []                  # 已选收件人显示名
        self.searched = ""

    # --- 窗口
    def open_picker(self):
        self.searched = ""
        self.picker = self._build()
        return self.picker

    def _rowlist(self):
        if not self.searched:
            kids = [Node(nm, PICKER_ROW_CLS, "CheckBoxControl",
                         rect=(814, 462 + i * 100, 1454, 562 + i * 100))
                    for i, nm in enumerate(self.names)]
        else:
            # 一打上搜索词，结果行就换成另一个类名（实测：SearchContactCellView）
            hits = [nm for nm in self.names + self.searchable
                    if self.searched in nm]
            kids = [Node(nm, PICKER_SEARCH_ROW_CLS, "CheckBoxControl",
                         rect=(814, 462 + i * 100, 1454, 562 + i * 100))
                    for i, nm in enumerate(hits)]
        return Node("请勾选需要添加的联系人",
                    "mmui::StickyHeaderRecyclerListView", "ListControl", kids,
                    aid="sp_to_select_contact_list")

    def _chosenlist(self):
        kids = [Node("移除" + nm, "mmui::SPChoiceContactRow", "ButtonControl",
                     aid="") for nm in self.chosen]
        return Node("", "mmui::XTableView", "TextControl", kids,
                    aid="sp_choice_contact_list")

    def _button(self):
        n = len(self.chosen)
        name = "发送" if n <= 1 else "分别发送(%d)" % n
        return Node(name, "mmui::XButton", "ButtonControl", aid="confirm_btn",
                    rect=(1606, 1362, 1830, 1422))

    def _build(self):
        root = Node("微信发送给", "mmui::SessionPickerWindow", "WindowControl")
        root.add(Node(SEARCH_BOX_NAME, "mmui::XValidatorTextEdit", "EditControl"))
        root.add(self._rowlist())
        detail = Node("", "mmui::SPDetailView", "GroupControl")
        detail.add(self._chosenlist())
        detail.add(self._button())
        detail.add(Node("取消", "mmui::XButton", "ButtonControl", aid="cancel_btn",
                        rect=(1878, 1362, 2102, 1422)))
        root.add(detail)
        return root

    def refresh(self):
        """勾选之后重建（真机每次读到的都是新树）。"""
        if self.picker is not None:
            self.picker = self._build()
        return self.picker

    # --- 点击产生的后果
    def click_row(self, name):
        if self.check_fails:
            return
        if self.cap is not None and len(self.chosen) >= self.cap:
            return
        if name not in self.chosen:
            self.chosen.append(name)
        self.refresh()

    def click_confirm(self):
        EVENTS.append("confirm")
        if not self.send_keeps_open:
            self.picker = None
            self.chosen = []

    def click_cancel(self):
        EVENTS.append("cancel")
        self.picker = None
        self.chosen = []


class Ops(ForwardOps):
    """只假掉「找窗口 / 抬窗口 / 落点归属 / 点击」这四样，其余走真代码。"""

    def __init__(self, sim, db):
        ForwardOps.__init__(self, None, None, None, 6.0)
        self.sim = sim
        self.uia_obj = FakeUIA(sim)
        self.db_obj = db
        self.clicks = []

    @property
    def uia(self):
        return self.uia_obj

    @property
    def db(self):
        return self.db_obj

    def _top(self, title):
        return 500 if title == F.FWD_TITLE and self.sim.picker is not None else 0

    def _control(self, hwnd):
        return self.sim.picker if hwnd == 500 else None

    def _raise(self, hwnd):
        return True

    def _owner_at(self, x, y):
        return 500

    def _click(self, hwnd, ctrl, right=False, at=None, raise_it=True):
        if ctrl is None:
            return _res(False, "no-ctrl")
        self.clicks.append(((at or (0, 0))[0], (at or (0, 0))[1], bool(right)))
        aid = getattr(ctrl, "AutomationId", "") or ""
        cls = ctrl.ClassName or ""
        if aid == "confirm_btn":
            self.sim.click_confirm()
        elif aid == "cancel_btn":
            self.sim.click_cancel()
        elif PICKER_ROW_CLS in cls or PICKER_SEARCH_ROW_CLS in cls:
            self.sim.click_row((ctrl.Name or "").strip())
        return _res(True, "ok")


def _res(ok, reason, **kw):
    out = {"ok": bool(ok), "reason": reason}
    out.update(kw)
    return out


class FakeDB:
    """get_messages 里有没有「比发送时刻更新的一行」= 到底送达没有。"""

    def __init__(self, landed=(), t0=1000):
        self.landed = set(landed)
        self.t0 = t0
        self.queries = []

    def username_by_nickname(self, nm):
        return {"文件传输助手": "filehelper", "送你挖银子": "wxid_self"}.get(nm, nm)

    def get_messages(self, user, limit=5):
        self.queries.append(user)
        who = next((k for k in self.landed
                    if k == user or self.username_by_nickname(k) == user), None)
        rows = [{"type": "文本", "content": "旧的", "create_time": self.t0 - 50}]
        if who:
            rows.insert(0, {"type": "图片", "content": "转发的",
                            "create_time": self.t0 + 5})
        return rows


def build(names=("文件传输助手", "送你挖银子", "小明"), landed=(), **kw):
    del EVENTS[:]        # 每个场景只看自己这一段事件，别拿上一条测试的记录来判
    sim = Sim(names=names, **kw)
    db = FakeDB(landed=landed)
    return Ops(sim, db), sim, db


# ---------------------------------------------------------------------------
# 变异：每条都必须让某些判据变红
# ---------------------------------------------------------------------------
if MUT:
    n = MUT[len("--mutate"):]
    if n == "1":
        # 不做人数预检：按钮写几个人都照样点发送
        _old0 = F.ForwardOps.send

        def no_precheck(self, expect=None):
            h, win = self._forward_window()
            btn = self._find_by_aid(win, "confirm_btn") if win else None
            if btn is None:
                return _res(False, "no-send-btn")
            self._click(h, btn, raise_it=False)
            return _res(True, "ok")
        F.ForwardOps.send = no_precheck
    elif n == "2":
        # 点了发送但窗口没关也当成功
        _old = F.ForwardOps.send

        def blind(self, expect=None):
            h, win = self._forward_window()
            ok, cnt = self.send_state(win)
            if not ok:
                return _res(False, "no-send-btn")
            if expect is not None and cnt != expect:
                return _res(False, "count-mismatch")
            self._click(h, self._find_by_aid(win, "confirm_btn"), raise_it=False)
            return _res(True, "ok", on_button=cnt)
        F.ForwardOps.send = blind
    elif n == "3":
        # 不回读数据库，勾了就当送到
        F.ForwardOps._landed = lambda self, who, since: True
    elif n == "4":
        # dry_run 也真点发送
        _old2 = F.ForwardOps.forward

        def nosafe(self, targets, chat=None, match=None, leave_message=None,
                   dry_run=False, chunk=9, verify=True, since=None):
            return _old2(self, targets, chat=chat, match=match, dry_run=False,
                         chunk=chunk, verify=verify, since=since)
        F.ForwardOps.forward = nosafe
    elif n == "5":
        # 下一块复用上一个已经关掉的窗口（不重新开）
        _old3 = F.ForwardOps.open_dialog

        def reuse(self, chat=None, match=None, max_scrolls=30):
            if EVENTS.count("confirm") >= 1 and self.sim.picker is None:
                return _res(True, "ok", hwnd=0, win=None)   # 装作窗还开着
            return _old3(self, chat=chat, match=match, max_scrolls=max_scrolls)
        F.ForwardOps.open_dialog = reuse
    elif n == "7":
        # 第一条没发出去也照样去转发（会右键到一条**别的**消息上转发出去）
        def ignore_seed(self, names, text, dry_run, chunk, verify):
            first, rest = names[0], names[1:]
            if dry_run or not rest:
                return _res(True, "plan", seed=first, rest=rest, chunks=0,
                             sent=[], failed=[], skipped=[])
            self._gui.send_msg(text, who=first, verify=verify)
            r = self._forward().forward(rest, chat=first,
                                        match=self._match_key(text), chunk=chunk,
                                        verify=verify)
            r["seed"] = first
            r["sent"] = [first] + list(r.get("sent") or [])
            return r
        DEFERRED.append(ignore_seed)      # FakeWX 在后面才定义，这里只能先存着
    elif n == "6":
        # 勾没勾上都当选中（漏掉 not-checked）
        F.ForwardOps.select_one = lambda self, name: _res(True, "ok", who=name)
    print("!!! 变异模式:", MUT)


# ---------------------------------------------------------------------------
print("--- A. 开窗")
o, sim, db = build()
r = o.open_dialog(chat="文件传输助手")
check("A1 右键最新一条 →「转发...」→「微信发送给」出现", code(r), "ok")
check("A1b 右键的是最新一条（没给 match 就走 _right_click_latest_row）",
      "right_latest" in EVENTS, True)

o, sim, db = build(chat_fails=True)
check("A2 会话打不开 → no-chat，不去点任何右键",
      (code(o.open_dialog(chat="没有这个会话")), sim.menu_item_present),
      ("no-chat", True))

o, sim, db = build(menu_item_present=False)
check("A3 菜单里没有「转发」→ no-forward-item（不盲点）",
      code(o.open_dialog(chat="文件传输助手")), "no-forward-item")

o, sim, db = build(no_message=True)
check("A4 会话里一条消息都没有 → no-message",
      code(o.open_dialog(chat="文件传输助手")), "no-message")

o, sim, db = build(match_hit=None)
r = o.open_dialog(chat="文件传输助手", match="找不到这段话")
check("A5 --match 定位不到 → no-message，绝不随便挑一条发",
      code(r), "no-message")

o, sim, db = build(match_hit="测试文本123")
r = o.open_dialog(chat="文件传输助手", match="测试文本")
check("A5b --match 命中时在那一行上右键（不是最新一条）",
      (code(r), "right_at" in EVENTS and "right_latest" not in EVENTS),
      ("ok", True))

print("--- B. 勾选与已选栏")
o, sim, db = build()
o.open_dialog(chat="文件传输助手")
check("B1 勾第一个人 → 已选栏有他",
      (code(o.select_one("文件传输助手")), sim.chosen), ("ok", ["文件传输助手"]))
check("B2 按钮写着「发送」（1 人）", o.send_state()[1], 1)
check("B3 再勾一个 → 按钮改成「分别发送(2)」",
      (code(o.select_one("送你挖银子")), o.send_state()[1]), ("ok", 2))
check("B4 已选栏里两个人都在", sorted(sim.chosen), sorted(["文件传输助手", "送你挖银子"]))
check("B5 点的是行左缘 +70px 那个复选框位置",
      o.clicks[-1][0], 814 + CHECKBOX_INSET_PX)
check("B6 勾过了不重复点（再选同一个人 = already）",
      code(o.select_one("文件传输助手")), "already")
check("B6b already 那次没有再点一下", len([c for c in o.clicks]), 2)

o, sim, db = build(check_fails=True)
o.open_dialog(chat="文件传输助手")
check("B7 点了但没进已选栏 → not-checked（不能当勾上了）",
      code(o.select_one("文件传输助手")), "not-checked")

o, sim, db = build(names=("小明",), searchable=("同学甲",))
o.open_dialog(chat="文件传输助手")
check("B8 列表里没有 → 走搜索框，认得 SearchContactCellView 这个类名",
      (code(o.select_one("同学甲")), sim.chosen), ("ok", ["同学甲"]))
check("B8b 搜索框确实打了字", "同学甲" in o.uia_obj.typed, True)

o, sim, db = build(names=("小明",), searchable=(), search_fails=False)
o.open_dialog(chat="文件传输助手")
check("B9 搜索也搜不到 → not-found（这个人进 skipped，不瞎点）",
      code(o.select_one("查无此人")), "not-found")

o, sim, db = build(names=("小明",), search_fails=True)
o.open_dialog(chat="文件传输助手")
check("B10 搜索框打字失败 → no-search，不硬点", code(o.select_one("同学甲")),
      "no-search")

print("--- C. 发送前的闸")
o, sim, db = build(landed=("文件传输助手", "送你挖银子"))
o.open_dialog(chat="文件传输助手")
o.select(["文件传输助手", "送你挖银子"])
check("C1 按钮人数和预期一致 → 点发送，窗口关掉",
      (code(o.send(expect=2)), sim.picker), ("ok", None))
check("C1b 两个人都靠回读确认送达", EVENTS.count("confirm"), 1)

o, sim, db = build(cap=1)     # 第二个人勾不上：按钮只写 1 人
o.open_dialog(chat="文件传输助手")
sel = o.select(["文件传输助手", "送你挖银子"])
check("C2 有一个没勾上 → 报进 missing", sel["missing"][0]["name"] if sel["missing"] else None,
      "送你挖银子")
r = o.send(expect=2)
check("C3 按钮写 1 人但预期 2 人 → count-mismatch，先不点", code(r), "count-mismatch")
check("C3b 而且真的没点发送", "confirm" in EVENTS, False)

o, sim, db = build(send_keeps_open=True, landed=("文件传输助手",))
o.open_dialog(chat="文件传输助手")
o.select(["文件传输助手"])
check("C4 点了发送但窗口没关 → still-open，不报成功",
      code(o.send(expect=1)), "still-open")

o, sim, db = build()
o.open_dialog(chat="文件传输助手")
check("C5 一个人都没勾 → no-recipient，绝不点发送",
      (code(o.send()), "confirm" in EVENTS), ("no-recipient", False))

o, sim, db = build()
o.open_dialog(chat="文件传输助手")
o.select(["文件传输助手"])
r = o.send(expect=1)
check("C5b 已选栏 1 人、按钮写「发送」(1) → 两个读数一致才放行",
      (code(r), r.get("chosen")), ("ok", ["文件传输助手"]))
check("C5b2 发完之后窗口已关（所以只能在结果里看已选栏，不能再去读窗口）",
      o.selected(), [])

o, sim, db = build()
o.open_dialog(chat="文件传输助手")
o.select(["文件传输助手"])
check("C5c 预期人数和已选栏不一致 → count-mismatch，不点（按钮/已选/预期三方对齐）",
      (code(o.send(expect=3)), "confirm" in EVENTS), ("count-mismatch", False))

print("--- D. 整链 forward()")
o, sim, db = build(landed=("文件传输助手", "送你挖银子", "小明"))
r = o.forward(["文件传输助手", "送你挖银子", "小明"], chat="文件传输助手",
              chunk=9, since=1000)
check("D1 三个人一块发完 → sent 三条",
      (code(r), sorted(r["sent"]), r["chunks"]),
      ("ok", sorted(["文件传输助手", "送你挖银子", "小明"]), 1))
check("D1b 每个人都被回读过", sorted(db.queries)[:3],
      sorted(["filehelper", "wxid_self", "小明"][:3]))

o, sim, db = build(landed=("文件传输助手", "小明"))   # 送你挖银子 没落库
r = o.forward(["文件传输助手", "送你挖银子", "小明"], chat="文件传输助手", since=1000)
check("D2 回读没有新行的那个人进 failed，不算送到",
      (sorted(r["sent"]), r["failed"], r["ok"]),
      (sorted(["文件传输助手", "小明"]), ["送你挖银子"], False))

o, sim, db = build(landed=("文件传输助手", "送你挖银子", "小明"))
sends = []
r = o.forward(["文件传输助手", "送你挖银子", "小明"], chat="文件传输助手",
              chunk=2, since=1000)
check("D3 6 人分块是多次「开窗→勾选→发送」（chunk=2 → 2 块）",
      (r["chunks"], EVENTS.count("confirm")), (2, 2))
check("D3b 第二块之前重新开了一次窗", EVENTS.count("right_latest") >= 2, True)
check("D3c 三个人都送到", sorted(r["sent"]), sorted(["文件传输助手", "送你挖银子", "小明"]))

o, sim, db = build(landed=("文件传输助手",))
r = o.forward(["文件传输助手", "送你挖银子"], chat="文件传输助手", dry_run=True,
              since=1000)
check("D4 预演：勾上了、但一次发送都没有",
      (code(r), EVENTS.count("confirm"), sorted(r.get("picked") or [])),
      ("dry-run", 0, sorted(["文件传输助手", "送你挖银子"])))
check("D4b 预演收尾点了「取消」把窗口收掉", "cancel" in EVENTS, True)

o, sim, db = build(landed=("文件传输助手", "送你挖银子", "小明"))
r = o.forward([], chat="文件传输助手")
check("D5 没给收件人 → bad-targets，一个窗都不开",
      (code(r), "open_chat" in [e[0] if isinstance(e, tuple) else e for e in EVENTS[-1:]] or False),
      ("bad-targets", False))

o, sim, db = build(names=("小明",), landed=("小明",))
r = o.forward(["小明", "查无此人"], chat="文件传输助手", since=1000)
check("D6 搜不到的人进 skipped，不影响另一个人发出去",
      (sorted(x["name"] for x in r["skipped"]), r["sent"]),
      (["查无此人"], ["小明"]))

print("--- E. 按钮文案解析")
check("E1 半角括号带人数", F.parse_send_button("分别发送(9)"), (True, 9))
check("E2 全角括号也认（微信两种都出现过）", F.parse_send_button("分别发送（10）"),
      (True, 10))
check("E3 单选时只有「发送」= 1 个人", F.parse_send_button("发送"), (True, 1))
check("E4 认不出的文字不猜人数", F.parse_send_button("转发"), (False, None))
check("E5 空字符串不可点", F.parse_send_button(""), (False, None))

print("--- M. 先发给一个人，再把那一条转发给剩下的（wx 层编排）")
from wechatauto.wx import WeChat as _WX
from wechatauto.param import WxResponse


class RecOps:
    """记 ops.forward 收到什么参数，并决定它成功还是失败。"""

    def __init__(self, ok=True, sent=("B", "C")):
        self.calls = []
        self.ok = ok
        self.sent = list(sent)

    def forward(self, names, chat=None, match=None, dry_run=False, chunk=9,
                verify=True):
        self.calls.append({"names": names, "chat": chat, "match": match,
                           "dry_run": dry_run, "chunk": chunk})
        if not self.ok:
            return {"ok": False, "reason": "count-mismatch：按钮写 1 人、已选栏 1 人、预期 2 人",
                    "sent": [], "failed": list(names), "skipped": []}
        return {"ok": True, "reason": "ok", "sent": list(self.sent),
                "failed": [], "skipped": [], "chunks": 1, "picked": list(names)}


class FakeGui:
    def __init__(self, fail_for=()):
        self.sent = []
        self.fail_for = set(fail_for)

    def send_msg(self, text, who=None, verify=False):
        if who in self.fail_for:
            return WxResponse.failure("发送失败：多次重试未完成")
        self.sent.append({"text": text, "who": who, "verify": verify})
        return WxResponse.success("发送成功")


class FakeWX:
    """只借「先发再转发」这段逻辑，绕开 WeChat.__init__（它会连库、抢窗口）。"""

    _match_key = staticmethod(_WX.__dict__["_match_key"].__func__)
    _forward_response = staticmethod(_WX.__dict__["_forward_response"].__func__)
    _seed_then_forward = _WX.__dict__["_seed_then_forward"]

    def __init__(self, ops, gui):
        self._ops = ops
        self._gui = gui

    def _forward(self):
        return self._ops


for _fn in DEFERRED:           # arm 7：把「先发给一个人」那段换成不认失败的版本
    FakeWX._seed_then_forward = _fn


def fwx(names=("A", "B", "C"), ok=True, fail_for=()):
    ops = RecOps(ok=ok)
    return FakeWX(ops, FakeGui(fail_for)), ops


check("M1 定位串取首行前 24 字", _WX._match_key("周五校庆放假\n第二行别管"), "周五校庆放假")
check("M2 太短（<4 字）不当定位串（会撞到别的气泡）", _WX._match_key("在吗"), None)
check("M3 长正文截到 24 字", len(_WX._match_key("一" * 60)), 24)
check("M4 空正文没有定位串", _WX._match_key("   "), None)

w, ops = fwx()
r = w._seed_then_forward(["A", "B", "C"], "周五校庆放假", True, 9, True)
check("M5 预演：一条都不发、连 ops.forward 都不进（还没有可转发的消息）",
      (r["reason"], w._gui.sent, ops.calls), ("plan", [], []))
check("M5b 预演说清楚：先发给谁、剩下几个、几块",
      (r["seed"], r["rest"], r["chunks"]), ("A", ["B", "C"], 1))

w, ops = fwx()
r = w._seed_then_forward(["A", "B", "C"], "周五校庆放假", False, 9, True)
check("M6 真发：第一笔是普通发送给第一个人（带 verify 回读）",
      w._gui.sent, [{"text": "周五校庆放假", "who": "A", "verify": True}])
check("M6b 转发只发给剩下的人，且**在第一个人的会话里**右键那条刚发的",
      (ops.calls[0]["names"], ops.calls[0]["chat"]), (["B", "C"], "A"))
check("M6c 定位串就是刚发的正文（不是随手右键最新一条）",
      ops.calls[0]["match"], "周五校庆放假")
check("M6d 第一个人算已发（他拿原件，不会被重复发第二遍）",
      sorted(r["sent"]), sorted(["A", "B", "C"]))

w, ops = fwx(fail_for=("A",))
r = w._seed_then_forward(["A", "B", "C"], "周五校庆放假", False, 9, True)
check("M7 第一条没发出去 → 整链停住，不去转发（否则会转发一条不存在的消息）",
      (code(r), ops.calls, sorted(r["failed"])),
      ("seed-fail", [], sorted(["B", "C"])))

ops = RecOps(ok=False)
w = FakeWX(ops, FakeGui())
r = w._seed_then_forward(["A", "B", "C"], "周五校庆放假", False, 9, True)
check("M8 闸门没过（count-mismatch）→ ok=False，第一个人已发要如实分开报",
      (r["ok"], code(r), r["sent"]), (False, "count-mismatch", ["A"]))

resp = w._forward_response(r, False, 3)
check("M8b 闸门失败时点名「第一个人已经收到」，免得重跑给他发第二遍",
      (resp["status"], "已发出的 1 条" in resp["message"],
       resp["data"]["already_sent"]),
      ("失败", True, ["A"]))

w, ops = fwx()
r3 = w._seed_then_forward(["A"], "只给一个人", False, 9, True)
check("M9 名单只有 1 人：发完就收，不开转发窗（也没第二个人可转）",
      (r3["reason"], r3["sent"], ops.calls), ("seed-only", ["A"], []))
check("M9b 这条路上第一个人只收到原件，不会被再转一遍",
      w._gui.sent, [{"text": "只给一个人", "who": "A", "verify": True}])

print()
print("通过 %d，失败 %d" % (len(PASSED), len(FAILED)))
if MUT:
    print("（变异模式：期望上面有 FAIL）")
    sys.exit(0 if FAILED else 1)
if FAILED:
    for f in FAILED:
        print("  FAIL:", f)
sys.exit(1 if FAILED else 0)
