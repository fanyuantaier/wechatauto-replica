# -*- coding: utf-8 -*-
"""收藏转发的离线自测：假收藏页 + 假「微信发送给」窗口 + 假 UIA 入口。

跑法：python tools/test_favorite_forward.py
      python tools/test_favorite_forward.py --mutateN   打一处变异，验证判据真的会红

被测的是 :mod:`wechatauto.favorite_forward` 的**判断链**。假树照真机 4.2.0.13
的快照搭（实测锚点来自 favorite_probe_tree.log，2026-11-07）：

  导航     mmui::MainTabBar 下的 mmui::XTabBarItem（Name=微信/通讯录/收藏/发现）
  收藏列表 mmui::XRecyclerTableView aid=fav_detail_list（懒渲染：点 tab 后才有）
  条目     mmui::XTableCell，Name=「类型+标题+来源+日期」拼串
           第一条 top=110 < 列表 top=168（被页头压住，右键点不进去）
  菜单     mmui::XMenuView（主窗子树先例）/ 独立小窗（标签先例）两路都要认
"""
import sys
import types

from wechatauto import favorite_forward as FF
from wechatauto import forward as F
from wechatauto.favorite_forward import FavoriteOps
from wechatauto.forward import ForwardOps
from wechatauto.labels import (CHECKBOX_INSET_PX, PICKER_ROW_CLS,
                               PICKER_SEARCH_ROW_CLS, SEARCH_BOX_NAME)

PASSED, FAILED, EVENTS = [], [], []
MUT = next((a for a in sys.argv if a.startswith("--mutate")), None)

CLOCK = [1000.0]


def _fake_sleep(sec):
    CLOCK[0] += float(sec)


def _fake_time():
    return CLOCK[0]


# rhythm.nap 的实现内部调 rhythm 模块自己的 time.sleep —— 不换掉它，
# ensure_fav_page 的轮询里 nap(0.35) 会真睡，CLOCK 却一动不动。
import wechatauto.rhythm as _rhythm_mod
for _mod in (F, FF, sys.modules["wechatauto.labels"], _rhythm_mod):
    _mod.time = types.SimpleNamespace(sleep=_fake_sleep, time=_fake_time)
for _m in (F, FF, sys.modules["wechatauto.labels"]):
    _m.rhythm.nap = _fake_sleep          # nap(base) → CLOCK 推进 base 秒
    _m.rhythm.gate = lambda kind="write": 0.0


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

    def GetFirstChildControl(self):
        for index, child in enumerate(self._kids):
            child._next_sibling = self._kids[index+1] if index+1 < len(self._kids) else None
        return self._kids[0] if self._kids else None

    def GetNextSiblingControl(self):
        return getattr(self, '_next_sibling', None)

    def add(self, *kids):
        self._kids.extend(kids)


class Sim:
    """假微信主窗 + 假收藏页 + 假「微信发送给」窗口，一台状态机全管。"""

    def __init__(self, fav_items=("笔记 一周计划 我 11月5日",
                                  "图片 风景.jpg 相册 11月4日",
                                  "链接 站内文档 腾讯文档 11月3日",
                                  "文本 会议纪要 我 11月2日"),
                 first_clamped=True, no_tab=False, tab_dead=False,
                 menu_item_present=True, picker_never_opens=False,
                 names=("文件传输助手", "送你挖银子", "小明"),
                 searchable=("同学甲",), check_fails=False, cap=None,
                 search_fails=False, send_keeps_open=False):
        self.fav_items = list(fav_items)
        self.first_clamped = first_clamped
        self.no_tab = no_tab
        self.tab_dead = tab_dead
        self.menu_item_present = menu_item_present
        self.picker_never_opens = picker_never_opens
        self.names = list(names)
        self.searchable = list(searchable)
        self.check_fails = check_fails
        self.cap = cap
        self.search_fails = search_fails
        self.send_keeps_open = send_keeps_open
        self.on_fav_page = False      # 点 tab 前收藏列表不在树里（懒渲染）
        self.scrolls = []             # 每次滚轮的方向
        self.right_at = []            # 右键落点
        self.tab_clicks = 0
        self.picker = None
        self.chosen = []
        self.searched = ""
        self.scroll_shift = 0         # 滚动使条目整体上移的像素

    # ------------------------------------------------------------- 主窗收藏页
    def build_main(self):
        bar = Node("", "mmui::MainTabBar", "TabControl")
        for nm in ("微信", "通讯录", "收藏", "发现"):
            if nm == "收藏" and self.no_tab:
                continue
            bar.add(Node(nm, "mmui::XTabBarItem", "TabItemControl",
                         rect=(20, 100 + ["微信", "通讯录", "收藏", "发现"].index(nm) * 80,
                               100, 170 + ["微信", "通讯录", "收藏", "发现"].index(nm) * 80)))
        root = Node("微信", "mmui::MainWindow", "WindowControl")
        root.add(bar)
        if self.on_fav_page:
            root.add(self._fav_page())
        return root

    def _fav_page(self):
        page = Node("", "mmui::FavoritePage", "GroupControl")
        page.add(Node("全部收藏", "mmui::XText", "TextControl"))
        lst = Node("收藏列表", "mmui::XRecyclerTableView", "ListControl",
                   aid="fav_detail_list",
                   rect=(508, 168, 3570, 1982))
        top0 = 168 if self.first_clamped is False else 110   # 第一条被页头压住
        for i, nm in enumerate(self.fav_items):
            t = top0 + i * 254 + self.scroll_shift
            lst.add(Node(nm, "mmui::XTableCell", "ListItemControl",
                         rect=(508, t, 3570, t + 222)))
        page.add(lst)
        page.add(Node("", "mmui::StickyHeaderRecyclerListView", "ListControl",
                      aid="fav_category_list"))
        return page

    def refresh_main(self):
        """主窗树重建（真机每次读到的都是新树）。"""
        self.main = self.build_main()
        FF_FAV_WINDOW[0] = self.main

    # --------------------------------------------------------------- 转发窗
    def open_picker(self):
        if self.picker_never_opens:
            return
        self.searched = ""
        self.picker = self._build_picker()

    def _rowlist(self):
        if not self.searched:
            kids = [Node(nm, PICKER_ROW_CLS, "CheckBoxControl",
                         rect=(814, 462 + i * 100, 1454, 562 + i * 100))
                    for i, nm in enumerate(self.names)]
        else:
            hits = [nm for nm in self.names + self.searchable
                    if self.searched in nm]
            kids = [Node(nm, PICKER_SEARCH_ROW_CLS, "CheckBoxControl",
                         rect=(814, 462 + i * 100, 1454, 562 + i * 100))
                    for i, nm in enumerate(hits)]
        return Node("请勾选", "mmui::StickyHeaderRecyclerListView", "ListControl",
                    kids, aid="sp_to_select_contact_list")

    def _chosenlist(self):
        kids = [Node("移除" + nm, "mmui::SPChoiceContactRow", "ButtonControl")
                for nm in self.chosen]
        return Node("", "mmui::XTableView", "TextControl", kids,
                    aid="sp_choice_contact_list")

    def _build_picker(self):
        root = Node("微信发送给", "mmui::SessionPickerWindow", "WindowControl")
        root.add(Node(SEARCH_BOX_NAME, "mmui::XValidatorTextEdit", "EditControl"))
        root.add(self._rowlist())
        detail = Node("", "mmui::SPDetailView", "GroupControl")
        n = len(self.chosen)
        detail.add(self._chosenlist())
        detail.add(Node("发送" if n <= 1 else "分别发送(%d)" % n,
                        "mmui::XButton", "ButtonControl", aid="confirm_btn",
                        rect=(1606, 1362, 1830, 1422)))
        detail.add(Node("取消", "mmui::XButton", "ButtonControl", aid="cancel_btn",
                        rect=(1878, 1362, 2102, 1422)))
        root.add(detail)
        return root

    def click_row(self, name):
        if self.check_fails:
            return
        if self.cap is not None and len(self.chosen) >= self.cap:
            return
        if name not in self.chosen:
            self.chosen.append(name)
        if self.picker is not None:
            self.picker = self._build_picker()

    def click_confirm(self):
        EVENTS.append("confirm")
        if not self.send_keeps_open:
            self.picker = None
            self.chosen = []

    def click_cancel(self):
        EVENTS.append("cancel")
        self.picker = None
        self.chosen = []


# 主窗树挂在模块级槽里，FakeUIA 和 FavOps 都能拿到同一棵
FF_FAV_WINDOW = [None]


class FakeUIA:
    """FavoriteOps 会碰到的那些 uia 原语。"""

    def __init__(self, sim):
        self.sim = sim
        self.clicks = []
        self.typed = []
        self._win = None

    def ensure_window(self):
        self._win = self.sim.build_main()
        return True

    def ensure_materialized(self):
        return True

    def _set_cursor(self, x, y):
        pass

    def _right_click(self):
        self.sim.right_at.append(1)
        EVENTS.append("right_at")

    def _mouse_wheel(self, delta):
        self.sim.scrolls.append(delta)
        self.sim.scroll_shift += (delta // 120) * -80    # 向下滚，条目上移
        self.sim.on_fav_page = True
        self.sim.refresh_main()

    def _click_at(self, x, y, right=False):
        self.clicks.append((int(x), int(y), bool(right)))
        if right:
            return
        # 点了 tab：收藏页懒渲染出来；点菜单项：转发窗出现
        if self.sim.menu_item_present and self._menu_zone(x, y):
            self.sim.open_picker()
        elif self._tab_zone(x, y) and not self.sim.tab_dead:
            self.sim.tab_clicks += 1
            self.sim.on_fav_page = True
            self.sim.refresh_main()

    def _menu_zone(self, x, y):
        r = (2746, 1202, 3050, 1268)      # 「转发...」菜单项矩形（照 forward 测试）
        return r[0] <= x <= r[2] and r[1] <= y <= r[3]

    def _tab_zone(self, x, y):
        return x < 400

    def _uia_find_menu_item(self, want):
        if not (self.sim.on_fav_page and self.sim.menu_item_present):
            return None
        return Node(want + "...", "mmui::XMenuView", "MenuItemControl",
                    aid="XMenuItem", rect=(2746, 1202, 3050, 1268))

    def _set_text(self, ctrl, text):
        self.typed.append(text)
        if self.sim.search_fails:
            return False
        self.sim.searched = text
        if self.sim.picker is not None:
            self.sim.picker = self.sim._build_picker()
        return True


class FavOps(FavoriteOps):
    """只假掉「找顶层窗口 / 抬窗 / 落点归属 / 点击」四样，其余走真代码。"""

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
        if title == F.FWD_TITLE and self.sim.picker is not None:
            return 500
        return 0

    def _control(self, hwnd):
        return self.sim.picker if hwnd == 500 else None

    def _raise(self, hwnd):
        return True

    def _owner_at(self, x, y):
        return 500

    def _click(self, hwnd, ctrl, right=False, at=None, raise_it=True):
        if ctrl is None:
            return {"ok": False, "reason": "no-ctrl"}
        self.clicks.append(((at or (0, 0))[0], (at or (0, 0))[1], bool(right)))
        cls = ctrl.ClassName or ""
        aid = getattr(ctrl, "AutomationId", "") or ""
        if aid == "confirm_btn":
            self.sim.click_confirm()
        elif aid == "cancel_btn":
            self.sim.click_cancel()
        elif PICKER_ROW_CLS in cls or PICKER_SEARCH_ROW_CLS in cls:
            self.sim.click_row((ctrl.Name or "").strip())
        elif "XTabBarItem" in cls and (ctrl.Name or "").strip() == "收藏":
            self.sim.tab_clicks += 1
            if not self.sim.tab_dead:
                self.sim.on_fav_page = True
                self.sim.refresh_main()
                self.uia_obj._win = self.sim.main
        return {"ok": True, "reason": "ok"}


class FakeDB:
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


def build(**kw):
    del EVENTS[:]
    landed = kw.pop("landed", ())
    sim = Sim(**kw)
    FF_FAV_WINDOW[0] = None
    db = FakeDB(landed=landed)
    return FavOps(sim, db), sim, db


# ---------------------------------------------------------------------------
# 变异：每条都必须让某些判据变红
# ---------------------------------------------------------------------------
if MUT:
    n = MUT[len("--mutate"):]
    if n == "1":
        # 右键前不看矩形完整性：第一条被页头压住的也去右键
        def all_rows(self, win=None):
            lst = self._fav_detail_list(win)
            return list(lst.GetChildren()) if lst is not None else []
        FF.FavoriteOps.fav_rows = all_rows
    elif n == "2":
        # 菜单只找主窗子树一路（弹层路删掉）——弹层菜单场景要靠 C 组的
        # 主窗子树无菜单 + 弹层有菜单这一臂来抓
        FF.FavoriteOps._menu_item_anywhere = (
            lambda self, want: self._menu_item(want))
        _sim_menu_only_in_popups = True   # 见下方 FakeUIA 补丁
    elif n == "3":
        # 不等懒渲染：点完 tab 一次没看到列表就报 no-page
        def impatient(self):
            if not self.uia.ensure_window() or not self.uia.ensure_materialized():
                return {"ok": False, "reason": "no-window"}
            if self._fav_detail_list() is not None:
                return {"ok": True, "reason": "already"}
            tab = self._fav_tab()
            if tab is None:
                return {"ok": False, "reason": "no-tab"}
            r = self._click(self.uia._win.NativeWindowHandle, tab)
            if not r["ok"]:
                return r
            return {"ok": False, "reason": "no-page：点了「收藏」但收藏列表没出来"}
        FF.FavoriteOps.ensure_fav_page = impatient
    elif n == "4":
        # dry_run 也真点发送
        _old = FF.FavoriteOps.forward_from_open

        def nosafe(self, names, leave_message=None, dry_run=False, chunk=9,
                   verify=True, since=None, match=None, index=0, scroll=0):
            return _old(self, names, leave_message=leave_message, dry_run=False,
                        chunk=chunk, verify=verify, since=since, match=match,
                        index=index, scroll=scroll)
        FF.FavoriteOps.forward_from_open = nosafe
    elif n == "5":
        # 不回读数据库，点了发送就当送到
        FF.ForwardOps._landed = lambda self, who, since: True
    print("!!! 变异模式:", MUT)


if MUT and MUT[len("--mutate"):] == "2":
    # 变异 2 的配套：让主窗子树那一路失明（菜单只在弹层里物化），
    # 这样「只查主窗子树」的变异会在 C1 报 no-forward-item 而红。
    _orig_find_menu = FakeUIA._uia_find_menu_item

    def _menu_hidden_from_main(self, want):
        return None
    FakeUIA._uia_find_menu_item = _menu_hidden_from_main
    # 弹层路仍能找到：给 _find_in_popups 一个假的命中
    from wechatauto.labels import LabelOps as _LO

    def _popups_hit(self, names, also=None, also_hwnd=0):
        if names and names[0] == "转发" and self.sim.on_fav_page:
            return (Node("转发...", "mmui::XMenuView", "MenuItemControl",
                         aid="XMenuItem",
                         rect=(2746, 1202, 3050, 1268)), 777)
        return None, 0
    _LO._find_in_popups = _popups_hit


# ---------------------------------------------------------------------------
print("--- A. 收藏页入口")
o, sim, db = build()
r = o.open_favorite_dialog(index=1)
check("A1 点收藏 tab → 列表渲染 → 右键 →「转发...」→ 转发窗出现",
      (code(r), sim.tab_clicks >= 1, len(sim.right_at)), ("ok", True, 1))
check("A2 收藏页进了主窗树（不换窗口）",
      o._fav_detail_list() is not None, True)

o, sim, db = build(no_tab=True)
check("A3 导航栏没有「收藏」→ no-tab", code(o.open_favorite_dialog()), "no-tab")

o, sim, db = build(tab_dead=True)
check("A4 点了 tab 但列表一直不出（懒渲染判据等满 2.5s×2）→ no-page",
      code(o.ensure_fav_page()), "no-page")
check("A4b 人手式的重试：tab 被点了第二下", sim.tab_clicks, 2)

o, sim, db = build()
sim.on_fav_page = True          # 前一轮已把收藏页点开（状态在 Sim 上，不在 uia 上）
sim.refresh_main()
check("A5 已在收藏页就不点第二下 tab（already 短路）",
      (code(o.ensure_fav_page()), sim.tab_clicks), ("already", 0))

print("--- B. 条目定位")
o, sim, db = build()
o.ensure_fav_page()
rows = o.fav_rows()
check("B1 被页头压住的第一条不进候选（top=110 < 列表 top=168−8）",
      [r_.Name for r_ in rows][0] if rows else None, sim.fav_items[1])
check("B2 可选条目数 = 4−1", len(rows), 3)

o, sim, db = build(first_clamped=False)
o.ensure_fav_page()
check("B3 没被压住时第一条也算可选", o.fav_rows()[0].Name, sim.fav_items[0])

o, sim, db = build()
r = o.open_favorite_dialog(match="站内文档")
check("B4 match 子串命中 → 在那一行右键",
      (code(r), sim.right_at), ("ok", [1]))
o, sim, db = build()
r = o.open_favorite_dialog(match="不存在的条目")
check("B5 match 落空 → no-item，绝不随便挑一条右键", code(r), "no-item")

o, sim, db = build()
r = o.open_favorite_dialog(index=7)
check("B6 index 越界 → no-item", code(r), "no-item")

o, sim, db = build()
o.open_favorite_dialog(index=1)
check("B7 右键落点在行内左侧 1/4（气泡文字那一片）",
      all(508 <= c[0] <= 508 + (3570 - 508) // 2 for c in o.uia_obj.clicks
          if c[2]), True)

print("--- C. 开窗失败路")
o, sim, db = build(menu_item_present=False)
check("C1 右键菜单里没有「转发」→ no-forward-item（不盲点）",
      code(o.open_favorite_dialog(index=1)), "no-forward-item")

o, sim, db = build(picker_never_opens=True)
check("C2 点了「转发...」但转发窗没出现 → no-picker",
      code(o.open_favorite_dialog(index=1)), "no-picker")

print("--- D. 选人/发送/校验（复用 ForwardOps 后链）")
o, sim, db = build(landed=("文件传输助手", "送你挖银子"))
r = o.forward_favorite(["文件传输助手", "送你挖银子"], match="站内文档", since=1000)
check("D1 整链：开窗→勾选→发送→回读，两人都送到",
      (code(r), sorted(r["sent"]), r["chunks"]),
      ("ok", sorted(["文件传输助手", "送你挖银子"]), 1))
check("D1b 回读走的是转发目标不是收藏条目", sorted(db.queries)[:2],
      ["filehelper", "wxid_self"])

o, sim, db = build(landed=("文件传输助手",))
r = o.forward_favorite(["文件传输助手", "送你挖银子"], index=1, since=1000)
check("D2 回读没落库的那个人进 failed，不算送到",
      (sorted(r["sent"]), r["failed"], r["ok"]),
      (["文件传输助手"], ["送你挖银子"], False))

o, sim, db = build(landed=("文件传输助手", "送你挖银子", "小明"))
r = o.forward_favorite(["文件传输助手", "送你挖银子", "小明"], index=2,
                       chunk=2, since=1000)
check("D3 3 人分块（chunk=2 → 2 块）每块重开转发窗",
      (r["chunks"], EVENTS.count("confirm")), (2, 2))
check("D3b 三个人都送到", sorted(r["sent"]),
      sorted(["文件传输助手", "送你挖银子", "小明"]))

o, sim, db = build(landed=("文件传输助手",))
r = o.forward_favorite(["文件传输助手", "送你挖银子"], dry_run=True, index=1)
check("D4 预演：勾上了、一次发送都没有",
      (code(r), EVENTS.count("confirm"), sorted(r.get("picked") or [])),
      ("dry-run", 0, sorted(["文件传输助手", "送你挖银子"])))
check("D4b 预演收尾点了「取消」", "cancel" in EVENTS, True)

o, sim, db = build()
r = o.forward_favorite([])
check("D5 没给收件人 → bad-targets，连收藏页都不点",
      (code(r), sim.tab_clicks), ("bad-targets", 0))

o, sim, db = build(names=("小明",), searchable=("同学甲",), landed=("同学甲",))
r = o.forward_favorite(["同学甲"], index=1, since=1000)
check("D6 列表里没有的收件人走搜索框（后链复用）",
      (code(r), r["sent"]), ("ok", ["同学甲"]))

o, sim, db = build(cap=1, landed=("文件传输助手",))
r = o.forward_favorite(["文件传输助手", "送你挖银子"], index=1, since=1000)
check("D7 第二个人勾不上 → 进 skipped，只发勾上的那 1 个（按钮写 1 人，一致）",
      (code(r), r["sent"], r["failed"],
       r["skipped"][0]["name"] if r["skipped"] else None),
      ("ok", ["文件传输助手"], [], "送你挖银子"))

o, sim, db = build()
r = o.forward_favorite(["文件传输助手"], scroll=2, index=1)
check("D8 scroll 先滚两格再找条目", len(sim.scrolls), 2)

print("--- E. 变异自检基准（正常模式才有）")
o, sim, db = build()
r = o.open_favorite_dialog(index=0)
check("E1 默认 index=0 拿到的是第二条（第一条被压住）",
      (code(r), "right_at" in EVENTS), ("ok", True))

print()
print("通过 %d，失败 %d" % (len(PASSED), len(FAILED)))
if MUT:
    print("（变异模式：期望上面有 FAIL）")
    sys.exit(0 if FAILED else 1)
if FAILED:
    for f in FAILED:
        print("  FAIL:", f)
sys.exit(1 if FAILED else 0)
