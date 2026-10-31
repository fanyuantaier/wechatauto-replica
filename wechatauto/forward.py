# -*- coding: utf-8 -*-
"""右键消息「转发」→「微信发送给」选择框 → 分小块多选 → 发送。

界面走法（微信 4.x PC，全部按 4.1.15.13 真机实测的锚点写）::

    打开会话 → 右键某条消息（默认最新一条，可按文字定位）
      → 菜单项 ``mmui::XMenuView`` Name=「转发...」
        → **独立顶层窗口**「微信发送给」(``mmui::SessionPickerWindow``)
            左栏 ``mmui::StickyHeaderRecyclerListView``（aid=sp_to_select_contact_list）
                 行 ``mmui::SPSelectionContactRow``，Name 就是会话/联系人显示名
                 搜索框 EditControl ``mmui::XValidatorTextEdit`` Name=「搜索」
            右栏 ``mmui::SPDetailView``
                 已选中的收件人 = ``mmui::SPChoiceContactRow``，Name=「移除<显示名>」
                 留言框 ``mmui::ChatInputField``（aid=leave_message_view.chat_input_field）
                 按钮：取消 aid=cancel_btn；发送 aid=confirm_btn

三条实测出来的关键事实：

1. **发送按钮的名字自带收件人数**：单选时是「发送」，多选时变成
   ``分别发送(2)``。这是这件事最便宜的真值——勾了 9 个人但按钮写着
   ``分别发送(7)``，就说明有两个人没勾上，绝不能当成功发出去。
2. **「分别发送」= 给每个人单独发一条**，不是合并成一条群发（实测：两个收件人
   各自的会话里都新增了一行同刻的消息）。
3. 这个选择框和「标签→添加成员」那个是同一族控件（行类名、搜索框、行左缘
   +70px 的复选框位置都一样），所以本模块直接复用 :mod:`wechatauto.labels`
   的窗口层/点击层，不重写一套。

批量转发的节奏是**用户拍板**的：一个标签 63 人不在同一个窗口里一次勾满
（那等于一次点击把 63 条消息全砸出去，没有任何节流间隔），而是分小块——
每块默认 9 人，块与块之间过 :func:`wechatauto.rhythm.gate`。
"""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Sequence, Tuple

from wechatauto import rhythm
from wechatauto.labels import (CHECKBOX_INSET_PX, PICKER_ROW_CLS,
                               PICKER_SEARCH_ROW_CLS, SEARCH_BOX_NAME,
                               LabelOps, _result)
from wechatauto.logger import wxlog

FWD_TITLE = "微信发送给"
FWD_WIN_CLS = "mmui::SessionPickerWindow"
CHOSEN_ROW_CLS = "mmui::SPChoiceContactRow"
TO_LIST_AID = "sp_to_select_contact_list"
MENU_FORWARD = "转发"               # 界面上是「转发...」，按子串匹配
BTN_CANCEL = "取消"
AID_CONFIRM = "confirm_btn"
AID_CANCEL = "cancel_btn"
LEAVE_INPUT_AID = "leave_message_view.chat_input_field"
CHOSEN_PREFIX = "移除"              # 已选行的 Name = 「移除<显示名>」
SEND_COUNT_RE = re.compile(r"分别发送\s*[（(](\d+)[)）]")

DEFAULT_CHUNK = 9                   # 一次勾选几个人（用户定的节奏）


def parse_send_button(name: str) -> Tuple[bool, Optional[int]]:
    """发送按钮的 Name → (可点, 收件人数)。

    实测只有三种形状：``发送``（单选）、``分别发送(2)``（多选）、以及没选任何人时
    那颗不可用的按钮。认不出来的文字一律当「不知道有几个人」，不猜。
    """
    nm = (name or "").strip()
    m = SEND_COUNT_RE.search(nm)
    if m:
        return True, int(m.group(1))
    if nm == "发送":
        return True, 1
    return False, None


class ForwardOps(LabelOps):
    """右键转发的界面操作层（继承 LabelOps 的窗口层/点击层/选择框那套原语）。"""

    # ---------------------------------------------------------------- 开窗
    def _forward_window(self):
        return self._root(FWD_TITLE)

    def close_dialog(self, cancel: bool = True) -> bool:
        """把还开着的转发窗收掉（点取消），避免下一次操作撞在旧窗口上。"""
        h, win = self._forward_window()
        if win is None:
            return True
        btn = (self._find(win, (BTN_CANCEL,), contains=False, cls=None)
               if cancel else None)
        if btn is None:
            btn = self._find_by_aid(win, AID_CANCEL)
        if btn is not None:
            self._click(h, btn, raise_it=False)
        time.sleep(1.0)
        return self._forward_window()[1] is None

    def _find_by_aid(self, root, aid: str):
        """按 AutomationId 找控件（这颗按钮的 Name 会随人数变，aid 不变）。"""
        from wechatauto import uia_driver
        return uia_driver._find_by(root, lambda c: (
            getattr(c, "AutomationId", "") or "") == aid, max_depth=40)

    def _menu_item(self, want: str = MENU_FORWARD):
        """右键菜单里的某一项。菜单物化在主窗子树里（实测 Name=「转发...」）。"""
        try:
            return self.uia._uia_find_menu_item(want)
        except Exception as e:
            wxlog.debug("找菜单项 %r 失败：%s", want, e)
            return None

    def open_dialog(self, chat: Optional[str] = None,
	                  match: Optional[str] = None,
	                  max_scrolls: int = 30) -> Dict:
        """右键一条消息 →「转发...」→ 等「微信发送给」窗口出现。

        ``match`` 不给就右键**当前会话最新一条**消息；给了就在消息列表里按文字
        找最近一条包含它的行（找不到返回 ``no-message``，绝不随便挑一条发出去）。
        """
        self.close_dialog()
        if chat:
            if not self.uia.open_chat(chat):
                return _result(False, "no-chat：打不开会话「%s」" % chat, chat=chat)
        elif not self.uia.ensure_window():
            return _result(False, "no-window：微信窗口没就绪")
        rhythm.gate("forward")
        pt = None
        if match:
            hit = self.uia.find_in_message_list(
                lambda cn, nm: match in (nm or ""), match_last=True,
                max_scrolls=max_scrolls)
            if hit is None:
                return _result(False, "no-message：可视区里找不到包含「%s」的消息"
                               % match, match=match)
            # 4.2.0 行铺满整窗宽，行中心落在气泡外的空白处，右键弹不出菜单；
            # 用行内内容重心（气泡实际位置）当右键点
            x, y = self.uia._row_content_point(hit[2])
            self.uia._set_cursor(x, y)
            time.sleep(0.2)
            self.uia._right_click()
            pt = (x, y)
        else:
            pt = self.uia._right_click_latest_row()
            if pt is None:
                return _result(False, "no-message：当前会话里没有可右键的消息行")
        time.sleep(1.0)
        item = self._menu_item(MENU_FORWARD)
        if item is None:
            return _result(False, "no-forward-item：右键菜单里没有「%s...」"
                           % MENU_FORWARD, point=pt)
        r = item.BoundingRectangle
        self.uia._click_at(int((r.left + r.right) // 2),
                           int((r.top + r.bottom) // 2))
        h, win = self._wait_window(FWD_TITLE)
        if win is None:
            return _result(False, "no-picker：点了「%s...」但「%s」窗口没出现"
                           % (MENU_FORWARD, FWD_TITLE))
        return _result(True, "ok", hwnd=h, win=win, point=pt)

    # ---------------------------------------------------------------- 选人
    def selected(self, win=None) -> List[str]:
        """右侧「发送给」栏里已经勾上的收件人（按 Name 去重）。"""
        if win is None:
            _h, win = self._forward_window()
        if win is None:
            return []
        out: List[str] = []
        for c in self._rows(win, CHOSEN_ROW_CLS):
            nm = (c.Name or "").strip()
            if nm.startswith(CHOSEN_PREFIX):
                who = nm[len(CHOSEN_PREFIX):].strip()
                if who and who not in out:
                    out.append(who)
        return out

    def send_state(self, win=None):
        """发送按钮：(能不能点, 上面写的收件人数)。"""
        if win is None:
            _h, win = self._forward_window()
        if win is None:
            return False, None
        btn = self._find_by_aid(win, AID_CONFIRM)
        if btn is None:
            return False, None
        ok, n = parse_send_button(btn.Name or "")
        return ok, n

    def _check_row(self, hwnd, row) -> Dict:
        """勾一行：复选框在行左缘 +70px（和标签选择框同一族，实测）。"""
        rb = row.BoundingRectangle
        return self._click(hwnd, row,
                           at=(rb.left + CHECKBOX_INSET_PX,
                               (rb.top + rb.bottom) // 2))

    def select_one(self, name: str) -> Dict:
        """在选择框里勾中一个收件人：先翻列表，找不到再用它自己的搜索框。"""
        name = (name or "").strip()
        if not name:
            return _result(False, "bad-name：没给收件人")
        h, win = self._forward_window()
        if win is None:
            return _result(False, "no-picker：「%s」窗口不在" % FWD_TITLE)
        if name in self.selected(win):
            return _result(True, "already", who=name)
        row = self._find(win, (name,), cls=PICKER_ROW_CLS, contains=False)
        if row is None:
            box = self._find(win, (SEARCH_BOX_NAME,), contains=False)
            if box is None or not self.uia._set_text(box, name):
                return _result(False, "no-search：搜不了「%s」" % name, who=name)
            self._searched = name
            deadline = time.time() + 3.0
            while time.time() < deadline:
                h, win = self._forward_window()
                for cls in (PICKER_SEARCH_ROW_CLS, PICKER_ROW_CLS):
                    row = self._find(win, (name,), cls=cls, contains=False)
                    if row is not None:
                        break
                if row is not None:
                    break
                time.sleep(0.4)
        if row is None:
            return _result(False, "not-found：选择框里没有「%s」" % name, who=name)
        r = self._check_row(h, row)
        if not r["ok"]:
            return r
        _h, win = self._forward_window()
        if name not in self.selected(win):
            return _result(False, "not-checked：点了「%s」但没进右侧已选栏" % name,
                           who=name)
        return _result(True, "ok", who=name)

    def select(self, names: Sequence[str]) -> Dict:
        picked, missing = [], []
        for nm in names:
            r = self.select_one(nm)
            (picked if r["ok"] else missing).append(nm if r["ok"] else
                                                   {"name": nm, "reason": r["reason"]})
        _h, win = self._forward_window()
        return _result(True, "ok", picked=picked, missing=missing,
                       selected=self.selected(win))

    # ------------------------------------------------------------ 发送/取消
    def send(self, expect: Optional[int] = None) -> Dict:
        """点「发送」。两个独立读数必须一致，否则不点。

        一个是右侧已选栏里的人头（``移除<显示名>`` 那几行），一个是发送按钮上写的
        数字（``发送`` / ``分别发送(N)``）。两个都对不上（或压根没选任何人）就报
        ``count-mismatch`` / ``no-recipient`` 并停手——这一步一旦点下去就是真的
        往那么多人身上一条条发消息了，没有后悔药。
        """
        h, win = self._forward_window()
        if win is None:
            return _result(False, "no-picker：「%s」窗口不在" % FWD_TITLE)
        now = self.selected(win)
        ok, n = self.send_state(win)
        if expect is None:
            expect = len(now)
        if not ok:
            return _result(False, "no-send-btn：发送按钮点不了（写着 %r 个人）" % n,
                           on_button=n, chosen=now)
        if expect == 0:
            return _result(False, "no-recipient：右侧一个人都没选，不点发送")
        if n != expect or len(now) != expect:
            return _result(False, "count-mismatch：按钮写 %s 人、已选栏 %s 人、预期 %s 人，"
                                  "先不点" % (n, len(now), expect),
                           on_button=n, chosen=now, expect=expect)
        btn = self._find_by_aid(win, AID_CONFIRM)
        r = self._click(h, btn, raise_it=False)
        if not r["ok"]:
            return r
        time.sleep(2.0)
        if self._forward_window()[1] is not None:
            return _result(False, "still-open：点了发送但窗口没关（没发出去？）",
                           on_button=n, chosen=now)
        return _result(True, "ok", on_button=n, chosen=now)

    # ------------------------------------------------------------------ 整链
    def forward(self, targets: Sequence[str], chat: Optional[str] = None,
                match: Optional[str] = None, leave_message: Optional[str] = None,
                dry_run: bool = False, chunk: int = DEFAULT_CHUNK,
                verify: bool = True, since: Optional[int] = None) -> Dict:
        """把一条消息分别转发给 ``targets``，每 ``chunk`` 个人一个窗口。

        ``dry_run`` 走完开窗+勾选+对数，最后点「取消」，一条都不发。
        ``since`` 是发送时刻的水位：给每个人的会话读最近几条，有 ``create_time
        >= since`` 的新行才算送达（没有新行 = 没发出去，如实报 failed）。
        """
        names = [str(t).strip() for t in (targets or []) if str(t).strip()]
        if not names:
            return _result(False, "bad-targets：没给收件人")
        chunk = max(1, int(chunk or DEFAULT_CHUNK))
        picked_all, sent, failed, skipped = [], [], [], []
        opened = self.open_dialog(chat=chat, match=match)
        if not opened["ok"]:
            return opened
        try:
            for i in range(0, len(names), chunk):
                group = names[i:i + chunk]
                rhythm.gate("forward")
                sel = self.select(group)
                picked_all.extend(sel["picked"])
                skipped.extend(sel["missing"])
                got = len(sel["picked"])
                if not got:
                    continue
                if dry_run:
                    continue
                if since is None:
                    since = int(time.time()) - 5
                r = self.send(expect=got)
                if not r["ok"]:
                    failed.extend(sel["picked"])
                    continue
                time.sleep(1.5)
                # 发送之前两个读数已经对齐了，这里以**对话框实际要发给的那些人**
                # 为准来回读；勾了但没进已选栏的人在上面就被 send 拦下来了。
                actual = r.get("chosen") or sel["picked"]
                if verify:
                    for who in actual:
                        if self._landed(who, since):
                            sent.append(who)
                        else:
                            failed.append(who)
                else:
                    sent.extend(actual)
                if i + chunk < len(names):
                    opened = self.open_dialog(chat=chat, match=match)
                    if not opened["ok"]:
                        return _result(False, "no-picker：%s（后面 %d 个人没发）"
                                       % (opened["reason"],
                                          len(names) - i - chunk),
                                       sent=sent, failed=failed, skipped=skipped)
            if dry_run:
                self.close_dialog()
                return _result(True, "dry-run", targets=names, picked=picked_all,
                               skipped=skipped, chunks=(len(names) + chunk - 1) // chunk,
                               sent=[], failed=[])
        finally:
            self.close_dialog()
        return _result(not failed, "ok" if not failed else "partial",
                       targets=names, sent=sent, failed=failed, skipped=skipped,
                       chunks=(len(names) + chunk - 1) // chunk)

    def _landed(self, who: str, since: int) -> bool:
        """这个人的会话里有没有比 ``since`` 更新的一行（= 真的发出去了）。"""
        try:
            u = self.db.username_by_nickname(who) or who
            rows = self.db.get_messages(u, limit=5) or []
        except Exception as e:
            wxlog.debug("回读 %r 失败：%s", who, e)
            return False
        for r in rows:
            try:
                if int(r.get("create_time") or 0) >= since:
                    return True
            except Exception:
                continue
        return False
