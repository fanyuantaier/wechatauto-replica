# -*- coding: utf-8 -*-
"""转发**收藏**内容 →「微信发送给」选择框 → 分小块多选 → 发送。

与 :mod:`wechatauto.forward` 只差开头那一下：不是右键会话里的消息行，而是
点左侧导航「收藏」→ 在收藏列表里右键目标条目 → 点「转发...」；之后是同一个
「微信发送给」窗口（``mmui::SessionPickerWindow``），选人/发送/回读校验整条
后链原样复用 :class:`wechatauto.forward.ForwardOps`。

界面走法（微信 4.2.0.13 真机实测的锚点，2026-11-07 UIA 树快照）::

    主窗导航栏 MainTabBar 下的 ``mmui::XTabBarItem``（Name=「收藏」）
      → 收藏页就在主窗内（不换窗口）：
        右栏条目列表  ``mmui::XRecyclerTableView`` aid=fav_detail_list
            每条  ``mmui::XTableCell``，Name 是「类型+标题+来源+日期」拼串
        左栏分类列表  ``mmui::StickyHeaderRecyclerListView`` aid=fav_category_list
        左栏搜索框    ``mmui::XValidatorTextEdit`` Name=「搜索」

三条实测出来的关键事实：

1. **收藏页的两条列表靠 aid 认，不靠类名**：类名 ``XTableCell`` 主窗里到处都是，
   ``fav_detail_list`` / ``fav_category_list`` 这两个 AutomationId 才是唯一的。
2. **第一条收藏项顶部会被页头压着**（实测 ``top=110`` < 列表容器 ``top=168``），
   它的矩形越界了。要右键就挑矩形完整露出来的行，或者先滚一格。
3. 桌面锁定时右键菜单不物化（点击根本落不进应用）——菜单锚点分两路找：
   主窗子树 ``mmui::XMenuView``（聊天右键菜单的先例）+ 独立顶层小窗
   ``Qt51514QWindowIcon`` 标题 'Weixin'（标签右键菜单的先例），两路谁有算谁。

转发是 outward write：整链默认 ``verify=True``（每个收件人回读 ``create_time >= since``
的新行），失败如实进 ``failed``，绝不把「点了发送」当「发到了」。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Sequence

from wechatauto import rhythm
from wechatauto.forward import ForwardOps, MENU_FORWARD
from wechatauto.forward import _result
from wechatauto.labels import TAB_ITEM_CLS, MENU_TITLE
from wechatauto.logger import wxlog

FAV_TAB_NAME = "收藏"
FAV_LIST_AID = "fav_detail_list"
FAV_CATEGORY_AID = "fav_category_list"
FAV_ROW_CLS = "mmui::XTableCell"
FAV_SEARCH_NAME = "搜索"

# 收藏页第一条常常被页头压住一部分（矩形 top < 列表容器 top），右键点不进去；
# 判「矩形完整露出」用它：行顶不能高过列表容器顶太多。
_ROW_OVERSCAN_PX = 8


def _aid_in(c, aid: str) -> bool:
    """AutomationId 容错相等（新旧 aid 可能带点分前缀）。"""
    return ((getattr(c, "AutomationId", "") or "").strip()
            .rsplit(".", 1)[-1]) == aid


class FavoriteOps(ForwardOps):
    """收藏条目的右键转发：只重写开窗前半段，选人/发送/校验全在 ForwardOps。"""

    # ---------------------------------------------------------------- 收藏页
    def _fav_tab(self):
        """导航栏「收藏」tab。树里 tab 顺序固定：微信/通讯录/收藏/发现。"""
        uia = self.uia
        if uia._win is None and not uia.ensure_window():
            return None
        from wechatauto.uia_driver import _find_by, MAIN_TAB_BAR_CLS
        bar = _find_by(uia._win, lambda c: (c.ClassName or "") == MAIN_TAB_BAR_CLS,
                       max_depth=20)
        if bar is None:
            return None
        return _find_by(bar, lambda c: (c.ClassName or "") == TAB_ITEM_CLS
                        and (c.Name or "").strip() == FAV_TAB_NAME, max_depth=6)

    def _fav_detail_list(self, win=None):
        """右栏收藏条目列表（aid=fav_detail_list）。"""
        from wechatauto.uia_driver import _find_by
        root = win if win is not None else self.uia._win
        if root is None:
            return None
        return _find_by(root, lambda c: _aid_in(c, FAV_LIST_AID), max_depth=40)

    def ensure_fav_page(self) -> Dict:
        """点导航「收藏」，确认右栏条目列表渲染出来了。可反复跑（不点第二下）。

        收藏页的列表是懒渲染：点完 tab 后树里不一定马上有，按 2.5s 轮询判据等；
        还不出来再点一下（人手遇到没反应的 tab 也会再点一下）。
        """
        uia = self.uia
        if not uia.ensure_window():
            return _result(False, "no-window：微信窗口没拿到（先确认已登录）")
        if not uia.ensure_materialized():
            return _result(False, "tree-cold：UIA 树没物化（无障碍闸门未热激活）")
        if self._fav_detail_list() is not None:
            return _result(True, "already")
        tab = self._fav_tab()
        if tab is None:
            return _result(False, "no-tab：导航栏里找不到「%s」" % FAV_TAB_NAME)
        hwnd = uia._win.NativeWindowHandle
        for attempt in (1, 2):
            r = self._click(hwnd, tab)
            if not r["ok"]:
                return r
            deadline = time.time() + 2.5
            while time.time() < deadline:
                rhythm.nap(0.35)
                if self._fav_detail_list() is not None:
                    return _result(True, "ok", attempt=attempt)
            wxlog.debug("点「%s」第 %d 次后列表还没渲染", FAV_TAB_NAME, attempt)
        return _result(False, "no-page：点了「%s」但收藏列表没出来" % FAV_TAB_NAME)

    # ---------------------------------------------------------------- 条目行
    def fav_rows(self, win=None) -> List:
        """右栏**矩形完整露出**的收藏条目（界面顺序，跳过被页头压住的第一条）。

        被压住的那条右键点不进去（点了顶到页头上）；要转它就先 ``scroll``。
        """
        lst = self._fav_detail_list(win)
        if lst is None:
            return []
        out: List = []
        try:
            lr = lst.BoundingRectangle
            top_min = lr.top - _ROW_OVERSCAN_PX
        except Exception:
            top_min = None
        for ch in lst.GetChildren():
            try:
                if not (ch.ClassName or "").startswith(FAV_ROW_CLS.rsplit("::", 1)[-1]) \
                        and FAV_ROW_CLS not in (ch.ClassName or ""):
                    continue
                r = ch.BoundingRectangle
            except Exception:
                continue
            if r.bottom - r.top < 40:
                continue
            if top_min is not None and r.top < top_min:
                continue          # 被页头压住的那条
            out.append(ch)
        return out

    def _fav_row_by_text(self, text: str):
        """按文字片段找收藏条目（Name 是「类型+标题+来源+日期」拼串，子串匹配）。"""
        text = (text or "").strip()
        if not text:
            return None
        for ch in self.fav_rows():
            try:
                nm = (ch.Name or "").strip()
            except Exception:
                continue
            if text in nm:
                return ch
        return None

    # ---------------------------------------------------------------- 开窗
    def open_favorite_dialog(self, match: Optional[str] = None,
                             index: int = 0, scroll: int = 0) -> Dict:
        """右键一条收藏 →「转发...」→ 等「微信发送给」窗口出现。

        ``match`` 给文字片段（子串匹配条目 Name），不给就取 ``index`` 那条
        （默认 0 = 可视区最上面完整露出的那条）。``scroll`` 先滚几格再找
        （收藏列表没有按文字搜索的可靠先例，滚动是唯一的翻页手段）。
        """
        self.close_dialog()
        page = self.ensure_fav_page()
        if not page["ok"]:
            return page
        if scroll:
            self._scroll_fav(scroll)
        rows = self.fav_rows()
        row = self._fav_row_by_text(match) if match else (
            rows[index] if 0 <= index < len(rows) else None)
        if row is None:
            return _result(False, "no-item：收藏列表里没有%s"
                           % ("包含「%s」的条目" % match if match else "可选的条目"),
                           match=match, index=index)
        try:
            r = row.BoundingRectangle
        except Exception:
            return _result(False, "no-rect：收藏条目矩形读不到")
        if not r or r.width() <= 0 or r.height() <= 0:
            return _result(False, "empty-rect：收藏条目矩形是空的（滚出可视区了？）")
        # 落点取行内左侧 1/4（标题文字那一片），行右侧留白处右键弹不出菜单
        x = r.left + max(1, (r.right - r.left) // 4)
        y = (r.top + r.bottom) // 2
        self.uia._set_cursor(x, y)
        rhythm.nap(0.3)
        self.uia._right_click()
        rhythm.nap(1.2)

        item = self._menu_item_anywhere(MENU_FORWARD)
        if item is None:
            return _result(False, "no-forward-item：右键菜单里没有「%s...」"
                           % MENU_FORWARD, point=(x, y))
        try:
            mr = item.BoundingRectangle
        except Exception:
            return _result(False, "no-rect：菜单项矩形读不到")
        self.uia._click_at(int((mr.left + mr.right) // 2),
                           int((mr.top + mr.bottom) // 2))
        h, win = self._wait_window("微信发送给")
        if win is None:
            return _result(False, "no-picker：点了「%s...」但「微信发送给」窗口没出现"
                           % MENU_FORWARD)
        return _result(True, "ok", hwnd=h, win=win, point=(x, y))

    def _menu_item_anywhere(self, want: str):
        """右键菜单两路找：主窗子树（聊天先例）→ 独立弹层小窗（标签先例）。"""
        hit = self._menu_item(want)
        if hit is not None:
            return hit
        try:
            found, _mhwnd = self._find_in_popups((want,), also=self.uia._win,
                                                 also_hwnd=self.uia._win.NativeWindowHandle
                                                 if self.uia._win is not None else 0)
        except Exception as e:
            wxlog.debug("弹层找菜单项失败：%s", e)
            found = None
        return found

    def _scroll_fav(self, notches: int) -> None:
        """在收藏列表上滚几格（负数向上）。滚轮打在列表中心，不挪光标语义。"""
        lst = self._fav_detail_list()
        if lst is None:
            return
        try:
            r = lst.BoundingRectangle
            x = (r.left + r.right) // 2
            y = (r.top + r.bottom) // 2
        except Exception:
            return
        self.uia._set_cursor(x, y)
        rhythm.nap(0.2)
        for _ in range(max(1, abs(int(notches)))):
            self.uia._mouse_wheel(-120 if notches > 0 else 120)
            rhythm.nap(0.25)

    # ------------------------------------------------------------------ 整链
    def forward_favorite(self, targets: Sequence[str], match: Optional[str] = None,
                         index: int = 0, scroll: int = 0,
                         leave_message: Optional[str] = None,
                         dry_run: bool = False, chunk: int = 9,
                         verify: bool = True, since: Optional[int] = None) -> Dict:
        """把一条收藏分别转发给 ``targets``。参数与 :meth:`ForwardOps.forward` 一致，
        只把「会话+消息定位」换成「收藏页+条目定位」。

        ``dry_run`` 走完开窗+勾选+对数，最后点「取消」，一条都不发。
        """
        names = [str(t).strip() for t in (targets or []) if str(t).strip()]
        if not names:
            return _result(False, "bad-targets：没给收件人")
        opened = self.open_favorite_dialog(match=match, index=index, scroll=scroll)
        if not opened["ok"]:
            return opened
        return self.forward_from_open(names, leave_message=leave_message,
                                      dry_run=dry_run, chunk=chunk, verify=verify,
                                      since=since, match=match, index=index,
                                      scroll=scroll)

    def forward_from_open(self, names: Sequence[str],
                          leave_message: Optional[str] = None,
                          dry_run: bool = False, chunk: int = 9,
                          verify: bool = True, since: Optional[int] = None,
                          match: Optional[str] = None, index: int = 0,
                          scroll: int = 0) -> Dict:
        """「微信发送给」窗口已开时的选人/发送/校验整链（从 ForwardOps.forward 抽的）。

        为了让收藏转发复用同一套分块/对数/回读逻辑而不复制代码：ForwardOps.forward
        的开窗段在这里换成收藏入口，之后调本方法。``since`` 语义同 forward；
        ``match``/``index``/``scroll`` 只在多块续发重开窗口时回传给收藏入口。
        """
        chunk = max(1, int(chunk or 9))
        picked_all, sent, failed, skipped = [], [], [], []
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
                    # 下一块重开转发窗：转发窗是顶层窗口，收藏页在它下面没动，
                    # 关掉后同一条目还在原位，重走一遍右键就行。
                    opened = self.open_favorite_dialog(match=match, index=index,
                                                       scroll=scroll)
                    if not opened["ok"]:
                        return _result(False, "no-picker：%s（后面 %d 个人没发）"
                                       % (opened["reason"], len(names) - i - chunk),
                                       sent=sent, failed=failed, skipped=skipped)
            if dry_run:
                self.close_dialog()
                return _result(True, "dry-run", targets=list(names),
                               picked=picked_all, skipped=skipped,
                               chunks=(len(names) + chunk - 1) // chunk,
                               sent=[], failed=[])
        finally:
            self.close_dialog()
        return _result(not failed, "ok" if not failed else "partial",
                       targets=list(names), sent=sent, failed=failed,
                       skipped=skipped,
                       chunks=(len(names) + chunk - 1) // chunk)
