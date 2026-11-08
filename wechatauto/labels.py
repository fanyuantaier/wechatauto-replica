# -*- coding: utf-8 -*-
"""通讯录「标签」：建/改名/删标签，给标签批量加成员、批量移成员。

界面走法（微信 4.x PC，全部按 4.1.15.13 真机实测的锚点写）::

    主窗导航栏「通讯录」→ 通讯录页第一行 ``mmui::ContactsCellMangerBtnView``
      → 打开**独立顶层窗口**「通讯录管理」(``mmui::ContactsManagerWindow``)
        左栏：全部(171) / 筛选 / 朋友权限 / 标签 / 无标签(90) / 同学(63) / 新建标签
        右栏：``ContactsManagerDetailView``，每行 ``ContactsManagerDetailCell``
              的 Name 就是「昵称 + 该成员的标签」

三条实测出来的关键事实（不写下来一定会踩）：

1. **通讯录管理是另一个顶层窗口**，不在主窗子树里。所以本模块所有查找都以
   ``FindWindowW('Qt51514QWindowIcon', '通讯录管理')`` 拿到的窗口为根，
   而不是 :attr:`WeChatUIA._win`。右键菜单、「微信添加成员」选择框同理，
   都是各自独立的顶层小窗。
2. **每次点击之前必须把目标窗口抬到最前，并校验落点归属**。实测：主窗盖住
   管理窗左栏时、以及用户的浏览器盖住微信时，``mouse_event`` 会落到**别的窗口**
   上——UIA 那边读到的还是微信的树，看起来就是「点了没反应」。:meth:`_click`
   做 ``_force_foreground`` + ``WindowFromPoint`` 归属校验，不匹配就
   ``REFUSED`` 返回，绝不盲投。
3. **行内改名没有可寻址的编辑框**：点「修改标签名」之后整棵树里只有两个搜索框，
   ``SetFocus`` 反而会把编辑态取消。实测唯一可行的是「剪贴板 + Ctrl+V + 回车」
   （管理窗此时已经是前台，按键落得准）。

另外两条也一样致命，都在 :meth:`open_manager` / :meth:`create_label` 里处理了：

4. **左栏「标签」那一组会被折叠**（微信记住状态）。折叠时 ``无标签`` / 各标签 /
   ``新建标签`` **整组都不在 UIA 树里**，只剩分组标题，什么都是「找不到」。
   点一下分组标题就回来，而且实测再点一次不会收起，所以
   :meth:`ensure_labels_open` 是可以反复跑的。
5. **刚点「新建标签」产出的那一行 Name 是空的**（``'(0)'``，提交后才变成
   ``未命名(0)``），并且此时它已在行内编辑态、右键弹不出菜单。所以判「有没有多出
   一行」用不带滤镜的 :meth:`_label_cells`，命名先走「直接贴 + 回车」
   （``via='editing'``），没生效才退回右键改名（``via='menu'``）。

校验真值用**界面**，不用数据库：标签行的 Name 自带成员数（``同学(63)``），
选择框右侧有 ``已选择N个联系人``。``contact.db:contact_label`` 只存标签定义
（本机实测：删掉标签后库里那几行**还在**，滞后于界面），所以它只当辅助读，
不当判据。成员归属在数据库里读不到——全库扫过一遍，带 label/tag 字样的表只有
``contact_label``（只有定义）、收藏的 ``fav_*_tag_*``、``general.db:FMessageTable
.label_ids_``（「新的朋友」验证消息上的字段）；``contact.extra_buffer`` 按
protobuf 逐字段解过 409 个有值的联系人，也没有取值 ⊆ 已知 label_id 的字段。
"""
from __future__ import annotations

import ctypes
import os
import re
import time
from ctypes import wintypes
from typing import Dict, List, Optional, Sequence, Tuple

from wechatauto import rhythm
from wechatauto.logger import wxlog

# ---------------------------------------------------------------------------
# 实测锚点（微信 4.1.15.13；类名/文案逐条来自 label_probe\\ 里的树快照）
# ---------------------------------------------------------------------------
QT_SHELL_CLS = "Qt51514QWindowIcon"
CONTACTS_TAB = ("通讯录",)
TAB_ITEM_CLS = "mmui::XTabBarItem"
MAIN_MGR_ROW_CLS = "mmui::ContactsCellMangerBtnView"   # 通讯录页第一行，Name 为空
MGR_TITLE = "通讯录管理"
MGR_WIN_CLS = "mmui::ContactsManagerWindow"
PICKER_TITLE = "微信添加成员"
MENU_TITLE = "Weixin"                                   # 右键菜单那种小窗

LABEL_ROW_CLS = "mmui::ContactsManagerControlLabelCell"   # name='同学(63)'
CREATE_ROW_CLS = "mmui::ContactsManagerControlCreateLabelCell"
FOLDER_ROW_CLS = "mmui::ContactsManagerControlFolderCell"   # '朋友权限' / '标签' / '最近群聊'
GROUP_FOLDER = "标签"        # 折叠时整组标签行（含「新建标签」）都不在树里
DETAIL_CELL_CLS = "mmui::ContactsManagerDetailCell"       # name='昵称  标签'
DETAIL_LIST_CLS = "mmui::ContactsManagerDetailView"
DETAIL_HEADER_CLS = "mmui::ContactsManagerHBoxlayoutClickArea"
PICKER_ROW_CLS = "mmui::SPSelectionContactRow"            # CheckBoxControl
# 在选择框里**搜过之后**，结果行换了个类名（实测 4.1.15.13：容器也从
# StickyHeaderRecyclerListView 变成 SearchContactView/XTableView）
PICKER_SEARCH_ROW_CLS = "mmui::SearchContactCellView"
PICKER_CLEAR = "清空"
PICKER_LIST_NAME = "请勾选需要添加的联系人"
SEARCH_BOX_NAME = "搜索"
SELECTED_RE = re.compile(r"已选择\s*(\d+)\s*个?人?")
LABEL_ROW_RE = re.compile(r"^(?P<name>.*)\((?P<count>\d+)\)$")

MENU_ADD = "添加成员"
MENU_RENAME = "修改标签名"
MENU_DELETE = "删除标签"
BTN_DONE = "完成"
BTN_CANCEL = "取消"
BTN_CONFIRM_DELETE = "删除"
BTN_REMOVE_MEMBER = "移出标签"
BTN_SET_LABEL = "设置标签"
BTN_UNCHECK = "取消选择"

VK_CONTROL, VK_V, VK_RETURN = 0x11, 0x56, 0x0D
KEYEVENTF_KEYUP = 0x0002

try:                                     # 非 Windows 上 ctypes.windll 根本不存在
    _USER32 = ctypes.windll.user32
except AttributeError:
    _USER32 = None


def _result(ok: bool, reason: str, **extra) -> Dict:
    out = {"ok": bool(ok), "reason": reason}
    out.update(extra)
    return out


def parse_detail_name(raw: str, label: str = "") -> Dict:
    """拆成员行的 Name：``昵称 备注 标签`` 三段拼，空段留一个空格。

    实测样本（4.1.15.13）::

        '阿凡买买提  大人'      -> 昵称=阿凡买买提 备注='' 标签=大人
        '暗影精灵S 杨君瑞-17班 ' -> 昵称=暗影精灵S 备注=杨君瑞-17班 标签=''
        '安阳  '               -> 三个都空/只有昵称

    昵称里带空格的人会被拆错（``Br. 毕然-10班`` 这种就是昵称真带空格），所以
    调用方一律再用数据库校一遍：拆出来的三段里只有一段能唯一命中 wxid 才发。
    """
    raw = (raw or "").strip()
    remark = lbl = ""
    nick = raw
    if "  " in raw:                       # 空段 = 连着两个空格
        left, right = raw.split("  ", 1)
        nick, lbl = left.strip(), right.strip()
    else:
        parts = raw.split(" ")
        if len(parts) >= 3:
            nick, remark, lbl = parts[0], " ".join(parts[1:-1]).strip(), parts[-1]
        elif len(parts) == 2:
            nick, remark = parts[0], parts[1]
    disp = remark or nick or raw
    return {"raw": raw, "nick": nick, "remark": remark,
            "labels": lbl, "display": disp, "same_label": bool(
                label and lbl and label in lbl)}


def stitch_rows(acc: List[str], page: List[str]) -> Tuple[List[str], int]:
    """把新读到的一屏**接**到已经收集的序列后面，返回 ``(序列, 重叠行数)``。

    成员列表是虚拟化的：每屏和上一屏必然重叠几行。重叠按「已有序列的尾巴 ==
    新屏的头部」找最大匹配，**不按名字去重**——实测 63 人的标签里有重名的，
    按名字去重只剩 54 行，那样永远对不上微信报的人数。

    一格都接不上（重叠 0）说明中间漏了一屏：调用方要把它记成 ``gapped``，
    这次读取不算取全。
    """
    if not page:
        return acc, 0
    if not acc:
        return list(page), len(page)
    for k in range(min(len(acc), len(page)), 0, -1):
        if acc[-k:] == page[:k]:
            return acc + page[k:], k
    return acc + page, 0


def _press(*vks) -> None:
    """按一组虚拟键（先按下后抬起，顺序与按下相反）。"""
    if _USER32 is None:
        return
    for vk in vks:
        _USER32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.03)
    for vk in reversed(vks):
        _USER32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)


class LabelOps:
    """标签操作。``uia`` 是 :class:`WeChatUIA`，单测里换成假对象即可。"""

    def __init__(self, uia=None, db=None, dump_dir: Optional[str] = None,
                 appear_timeout: float = 6.0):
        self._uia = uia
        self._db = db
        self.dump_dir = dump_dir
        self.appear_timeout = appear_timeout
        # 搜索框里现在有没有**我们**打进去的词：有就得先清掉再找下一个人，
        # 没有就别白点一下（每多一次输入=多一次 rhythm 节流）。
        self._searched: Optional[str] = None

    # ------------------------------------------------------------------ 依赖
    @property
    def uia(self):
        if self._uia is None:
            from wechatauto.uia_driver import WeChatUIA
            self._uia = WeChatUIA()
        return self._uia

    @property
    def db(self):
        if self._db is None:
            from wechatauto.db import WeChatDB
            self._db = WeChatDB()
        return self._db

    # ---------------------------------------------------------------- 窗口层
    def _top(self, title: str) -> int:
        if _USER32 is None:
            return 0
        return int(_USER32.FindWindowW(QT_SHELL_CLS, title) or 0)

    def _control(self, hwnd: int):
        import uiautomation as auto
        return auto.ControlFromHandle(hwnd)

    def _root(self, title: str) -> Tuple[int, Optional[object]]:
        """按标题拿顶层窗口 (hwnd, 控件)；没有返回 (0, None)。"""
        hwnd = self._top(title)
        if not hwnd:
            return 0, None
        try:
            return hwnd, self._control(hwnd)
        except Exception as e:
            wxlog.debug("ControlFromHandle(%s) 失败：%s", hwnd, e)
            return hwnd, None

    def _wait_window(self, title: str, timeout: Optional[float] = None):
        """等某个顶层窗口出现（点完菜单到窗口画出来有几百毫秒到几秒）。"""
        deadline = time.time() + (timeout or self.appear_timeout)
        while time.time() < deadline:
            hwnd, win = self._root(title)
            if win is not None:
                return hwnd, win
            time.sleep(0.3)
        return 0, None

    def _owner_at(self, x: int, y: int) -> int:
        if _USER32 is None:
            return 0
        h = _USER32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        if not h:
            return 0
        return int(_USER32.GetAncestor(h, 2) or 0)       # GA_ROOT

    def _raise(self, hwnd: int) -> bool:
        from wechatauto.uia_driver import WeChatUIA
        ok = WeChatUIA._force_foreground(hwnd)
        time.sleep(0.35)
        return ok

    def _click(self, hwnd: int, ctrl, right: bool = False,
               at: Optional[Tuple[int, int]] = None,
               raise_it: bool = True) -> Dict:
        """（可选）抬窗 → 校验落点归属 → 点。归属不对就 REFUSED，绝不盲投。

        这一步不是保守：用户的浏览器盖住微信时，注入点击会真的落进浏览器，
        而 UIA 读回来的还是微信的树——那时候「点了没反应」和「点错了地方」
        在日志里长得一模一样。

        ``raise_it=False`` 给**弹层**（右键菜单、确认框）用：那种小窗出现时本来
        就在最前，再去 ``SetForegroundWindow`` 它一下反而会让 Qt 判定「失焦」而
        自己关掉，等回来读到的矩形就已经是废的了。
        """
        try:
            r = ctrl.BoundingRectangle
        except Exception:
            return _result(False, "no-rect：控件矩形读不到")
        if not r or r.width() <= 0 or r.height() <= 0:
            return _result(False, "empty-rect：控件矩形是空的（滚出可视区了？）")
        # 落点在控件矩形内取随机点（``rhythm.point`` 四边内缩 15%，off 档自动回正中）。
        # 「永远命中 BoundingRectangle 中心」是脚本最好认的特征，而这条链一次 add
        # 就要点 8~10 下。调用方显式给了 ``at``（窄目标只能按实测锚点打）时不动它。
        x, y = at if at else rhythm.point((r.left, r.top, r.right, r.bottom))
        if raise_it:
            self._raise(hwnd)
        owner = self._owner_at(x, y)
        for _ in range(2):
            if not owner or owner == hwnd:
                break
            time.sleep(0.35)
            owner = self._owner_at(x, y)
        if owner and owner != hwnd:
            return _result(False, "occluded：落点被窗口 %s 盖着，不点" % owner,
                           point=(int(x), int(y)), owner=owner)
        self.uia._click_at(int(x), int(y), right=right)
        time.sleep(0.6)
        return _result(True, "ok", point=(int(x), int(y)))

    def _find(self, root, names: Sequence[str], cls: Optional[str] = None,
              contains: bool = True, exact_first: bool = True):
        """按候选文字（可选类名）找控件：先精确、后包含。

        两级匹配是必需的：「通讯录」精确命中的是导航栏 tab，包含匹配会先撞到
        「通讯录管理」；「标签」会撞到「移出标签」。
        """
        if root is None:
            return None
        wanted = [n for n in names if n]

        def make(exact: bool):
            def pred(c):
                try:
                    nm = (c.Name or "").strip()
                    cn = c.ClassName or ""
                except Exception:
                    return False
                if cls and cls not in cn:
                    return False
                if not wanted:
                    return True             # 只按类名找
                if exact:
                    return nm in wanted
                return any(w in nm for w in wanted)
            return pred

        from wechatauto import uia_driver
        passes = (True, False) if exact_first else (False,)
        for exact in passes:
            if not exact and not contains:
                break
            hit = uia_driver._find_by(root, make(exact), max_depth=40)
            if hit is not None:
                return hit
        return None

    def _rows(self, root, cls: str) -> List:
        """按**界面顺序**收集某类控件（栈式 DFS 会把兄弟节点倒过来，
        标签行的顺序就是用户在左栏看到的顺序，别乱）。"""
        out: List = []

        def walk(c, d):
            if c is None or d > 30:
                return
            try:
                kids = list(c.GetChildren())
            except Exception:
                return
            for k in kids:
                try:
                    if cls in (k.ClassName or ""):
                        out.append(k)
                except Exception:
                    continue
                walk(k, d + 1)

        walk(root, 0)
        return out

    def _names(self, root, limit: int = 120) -> List[str]:
        out: List[str] = []
        stack = [(root, 0)]
        while stack and len(out) < limit:
            el, d = stack.pop()
            if d > 26:
                continue
            try:
                nm = (el.Name or "").strip()
                if nm and nm not in out:
                    out.append(nm)
                for k in el.GetChildren():
                    stack.append((k, d + 1))
            except Exception:
                continue
        return out

    def _popup_windows(self) -> List[Tuple[int, object]]:
        """微信进程里除主窗/管理窗外的可见顶层小窗，按 z 序（从上到下）。

        右键菜单、确认框、「微信添加成员」都是这种独立小窗（实测类名
        ``Qt51514QWindowIcon`` / ``Qt51514QWindowToolSaveBits``，标题 'Weixin'）。
        把它们当主窗子树来找，是这一类「点了没反应」的共同根因。
        """
        if _USER32 is None:
            return []
        main_h = int(getattr(self.uia._win, "NativeWindowHandle", 0) or 0) \
            if self.uia._win is not None else 0
        anchor = main_h or self._top(MGR_TITLE)
        if not anchor:
            return []
        mp = wintypes.DWORD()
        _USER32.GetWindowThreadProcessId(anchor, ctypes.byref(mp))
        if not mp.value:
            return []
        keep = {anchor, self._top(MGR_TITLE)}
        out: List[Tuple[int, object]] = []
        Proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def cb(h, l):
            try:
                h = int(h)
                if h in keep or not _USER32.IsWindowVisible(h):
                    return True
                p = wintypes.DWORD()
                _USER32.GetWindowThreadProcessId(h, ctypes.byref(p))
                if p.value != mp.value:
                    return True             # 不是微信的窗口，一律不算弹层
                r = wintypes.RECT()
                _USER32.GetWindowRect(h, ctypes.byref(r))
                if r.right - r.left < 80 or r.bottom - r.top < 60:
                    return True
                out.append((h, self._control(h)))
            except Exception:
                pass
            return True

        _USER32.EnumWindows(Proc(cb), 0)
        return out

    def close_stray_picker(self) -> bool:
        """关掉残留的「微信添加成员」窗口（上一次运行中断留下的）。

        它是管理窗的 owned window，**永远压在管理窗上面**——抬管理窗抬不过它，
        于是管理窗里被它盖住的那一片一律点不到。实测就是这么把「移出成员」
        变成 ``occluded`` 的。取消一个没提交的选择框没有副作用。
        """
        ph, picker = self._root(PICKER_TITLE)
        if picker is None:
            return False
        cancel = self._find(picker, (BTN_CANCEL,), contains=False)
        if cancel is not None:
            # 这里要抬窗：它是个正常窗口（不像右键菜单那样一失焦就关），
            # 而压在它上面的可能还有图片预览窗——不抬就永远取消不掉。
            self._click(ph, cancel, raise_it=True)
        time.sleep(0.6)
        gone = not self._top(PICKER_TITLE)
        if gone:
            wxlog.info("关掉了残留的「%s」窗口", PICKER_TITLE)
        return gone

    # ---------------------------------------------------------------- 进窗口
    def open_contacts_tab(self) -> Dict:
        """点导航栏「通讯录」，确认通讯录页渲染出来了。"""
        uia = self.uia
        if not uia.ensure_window():
            return _result(False, "no-window：微信窗口没拿到（先确认已登录）")
        if not uia.ensure_materialized():
            return _result(False, "tree-cold：UIA 树没物化（无障碍闸门未热激活）")
        hwnd = uia._win.NativeWindowHandle
        tab = self._find(uia._win, CONTACTS_TAB, cls=TAB_ITEM_CLS, contains=False)
        if tab is None:
            tab = self._find(uia._win, CONTACTS_TAB, contains=False)
        if tab is None:
            return _result(False, "no-tab：导航栏里找不到「通讯录」",
                           names=self._names(uia._win))
        r = self._click(hwnd, tab)
        if not r["ok"]:
            return r
        row = self._wait_class(MAIN_MGR_ROW_CLS, root=uia._win)
        if row is None:
            return _result(False, "no-page：点了「通讯录」但通讯录页没出来",
                           names=self._names(uia._win))
        return _result(True, "ok")

    def _wait_class(self, cls: str, root=None, timeout: Optional[float] = None):
        deadline = time.time() + (timeout or 3.0)
        while time.time() < deadline:
            rows = self._rows(root if root is not None else self.uia._win, cls)
            if rows:
                return rows[0]
            time.sleep(0.4)
        return None

    def open_manager(self) -> Dict:
        """打开（或复用）「通讯录管理」独立窗口，返回 (hwnd, 控件) 在 data 里。"""
        hwnd, win = self._root(MGR_TITLE)
        if win is None:
            nav = self.open_contacts_tab()
            if not nav["ok"]:
                return nav
            hwnd, win = self._root(MGR_TITLE)
        if win is None:
            main = self.uia._win
            row = self._wait_class(MAIN_MGR_ROW_CLS, root=main)
            if row is None:
                return _result(False, "no-manager-row：通讯录页里没有那一行入口",
                               names=self._names(main) if main else [])
            r = self._click(main.NativeWindowHandle, row)
            if not r["ok"]:
                return r
            hwnd, win = self._wait_window(MGR_TITLE)
        if win is None:
            return _result(False, "no-manager-win：点了入口但「通讯录管理」窗口没出现")
        self.close_stray_picker()
        hwnd, win = self._root(MGR_TITLE)
        # 窗口出现了 ≠ 左栏画出来了：实测刚点开的那一会儿左栏还是空的，读早了就
        # 是「一个标签都没有」——和之前右栏晚画是同一类坑。等到看见标签行、或者
        # 至少看见「新建标签」那一格（它俩有一个就证明这栏真的画过了）为止。
        deadline = time.time() + self.appear_timeout
        while time.time() < deadline:
            if self._label_cells(win):
                break
            if self._find(win, (), cls=CREATE_ROW_CLS) is not None:
                break
            time.sleep(0.4)
            hwnd, win = self._root(MGR_TITLE)
            if win is None:
                return _result(False, "no-manager-win：管理窗中途没了")
        ex = self.ensure_labels_open(hwnd, win)
        if not ex["ok"]:
            return ex
        hwnd, win = self._root(MGR_TITLE)
        if not self._rows(win, LABEL_ROW_CLS) and \
                self._find(win, (), cls=CREATE_ROW_CLS) is None:
            return _result(False, "manager-empty：管理窗开了但左栏没渲染",
                           names=self._names(win))
        return _result(True, "ok", hwnd=hwnd, win=win)

    def ensure_labels_open(self, hwnd=None, win=None) -> Dict:
        """确保左栏「标签」那一组是**展开**的。

        实测（4.1.15.13）：微信记住折叠状态，折叠的时候「无标签 / 同学 / 大人 /
        新建标签」这些行**整组都不在 UIA 树里**，只剩分组标题「标签」——这时候
        什么都找不到，表现就是 `no-create` / `no-label` / 空标签列表。点开它只要
        点一下分组标题；已经展开时一下都不点。

        再点一次**不会**把它收起（实测），所以这个动作重复跑是安全的。
        """
        if win is None:
            hwnd, win = self._root(MGR_TITLE)
        if win is None:
            return _result(False, "no-manager-win：管理窗不在")
        if self._find(win, (), cls=CREATE_ROW_CLS) is not None or \
                self._rows(win, LABEL_ROW_CLS):
            return _result(True, "already")
        folder = self._find(win, (GROUP_FOLDER,), cls=FOLDER_ROW_CLS, contains=False)
        if folder is None:
            return _result(False, "no-folder：左栏找不到「%s」分组行" % GROUP_FOLDER,
                           names=self._names(win))
        if not hwnd:
            hwnd, _w = self._root(MGR_TITLE)
        rhythm.gate("label")
        r = self._click(hwnd, folder)
        if not r["ok"]:
            return r
        deadline = time.time() + 3.0
        while time.time() < deadline:
            _h, win = self._root(MGR_TITLE)
            if win is not None and (self._find(win, (), cls=CREATE_ROW_CLS) is not None
                                    or self._rows(win, LABEL_ROW_CLS)):
                return _result(True, "expanded")
            time.sleep(0.4)
        return _result(False, "not-expanded：点了「%s」分组，那一组还是没出来"
                       % GROUP_FOLDER)

    # -------------------------------------------------------------------- 读
    def _label_cells(self, win=None) -> List[Dict]:
        """左栏**所有**标签行，连名字叫空的也要（新建那一下产出的就是空名行）。

        ``label_rows()`` 会把空名和「无标签」滤掉——那是对的（那是给人看的标签
        列表），但 ``create_label`` 用它判「有没有多出一行」就永远看不见刚建出来
        的那一行（实测 4.1.15.13：新行 Name 是 ``'(0)'``，要等编辑态提交才变成
        ``未命名(0)``）。所以这里不做任何过滤。
        """
        if win is None:
            _h, win = self._root(MGR_TITLE)
        if win is None:
            return []
        out: List[Dict] = []
        for cell in self._rows(win, LABEL_ROW_CLS):
            nm = (cell.Name or "").strip()
            m = LABEL_ROW_RE.match(nm)
            try:
                top = int(cell.BoundingRectangle.top)
            except Exception:
                top = None
            out.append({"name": (m.group("name").strip() if m else nm),
                        "count": int(m.group("count")) if m else None,
                        "top": top, "cell": cell})
        return out

    def label_rows(self, win=None) -> List[Dict]:
        """左栏的标签行：``[{name, count, cell}]``。

        「无标签」「新建标签」不是标签，名字为空的（刚点出来还没提交的那一行）
        也不算，过滤条件都在这层，:meth:`_label_cells` 是不带滤镜的原始行。
        """
        return [r for r in self._label_cells(win)
                if r["name"] and r["name"] != "无标签" and r["count"] is not None]

    def find_label(self, name: str, win=None) -> Optional[Dict]:
        for row in self.label_rows(win):
            if row["name"] == name:
                return row
        return None

    def list_labels(self, prefer: str = "db") -> Dict:
        """标签列表。``db`` 只读库（不碰窗口，空表也是有效答案）；
        ``ui`` 走管理窗；``auto`` 先库后界面。"""
        if prefer in ("db", "auto"):
            try:
                rows = self.db.list_labels()
            except Exception as e:
                if prefer == "db":
                    return _result(False, "db-fail：%s" % type(e).__name__,
                                   labels=[], names=[])
                rows = []
            if rows or prefer == "db":
                return _result(True, "db", labels=rows,
                               names=[r["name"] for r in rows])
        op = self.open_manager()
        if not op["ok"]:
            return _result(False, "ui-unreachable：" + op["reason"], labels=[], names=[])
        ui = [{"name": r["name"], "count": r["count"]} for r in self.label_rows(op["win"])]
        return _result(True, "ui", labels=ui, names=[r["name"] for r in ui])

    def label_members(self, label: str) -> Dict:
        """某标签的成员（读右侧列表里能看到的行）。

        **只覆盖可视区**：``ContactsManagerDetailView`` 是虚拟化列表，一屏十几行，
        本方法不滚动。要全量请拿标签行上的 ``count``。
        """
        op = self.open_manager()
        if not op["ok"]:
            return op
        row = self.find_label(label, op["win"])
        if row is None:
            return _result(False, "no-label：没有标签「%s」" % label, label=label,
                           members=[])
        r = self._click(op["hwnd"], row["cell"])
        if not r["ok"]:
            return r
        time.sleep(1.2)
        hwnd, win = self._root(MGR_TITLE)
        members = []
        for cell in self._rows(win, DETAIL_CELL_CLS):
            nm = (cell.Name or "").strip()
            disp = nm.split("  ")[0].strip() or nm
            members.append({"display": disp, "raw": nm, "cell": cell})
        return _result(True, "visible-page", label=label, members=members,
                       count=row["count"], visible=len(members))

    # -------------------------------------------------------------------- 写
    def create_label(self, name: str, verify: bool = True) -> Dict:
        """新建标签：点「新建标签」立刻产出一个空名标签，再改名成 ``name``。

        实测微信不给输入框，「新建」和「改名」是两步；空名标签会显示成
        ``未命名``，所以**改名失败必须报出来**，不能留一堆未命名给用户收拾。
        """
        name = (name or "").strip()
        if not name:
            return _result(False, "bad-name：标签名不能为空")
        op = self.open_manager()
        if not op["ok"]:
            return op
        hwnd, win = op["hwnd"], op["win"]
        if self.find_label(name, win) is not None:
            return _result(True, "already-there", label=name)
        btn = self._wait_class(CREATE_ROW_CLS, root=win)
        if btn is None:
            # 折叠着就点一下分组标题（实测：折叠时这一行整组都不在树里），再等一次
            ex = self.ensure_labels_open(hwnd, win)
            hwnd, win = self._root(MGR_TITLE)
            btn = self._wait_class(CREATE_ROW_CLS, root=win)
            if btn is None:
                return _result(False, "no-create：找不到「新建标签」那一行（%s）"
                               % (ex["reason"] if not ex["ok"] else "展开后还是没有"),
                               names=self._names(win) if win else [])
        # 「多出来一行」的基线必须在**分组已经展开之后**才取，不然展开本身就会被
        # 当成新增。
        before = {(r["name"], r["top"]) for r in self._label_cells(win)}
        try:
            create_top = int(btn.BoundingRectangle.top)
        except Exception:
            create_top = None
        rhythm.gate("label")
        r = self._click(hwnd, btn)
        if not r["ok"]:
            return r
        new_row = None
        deadline = time.time() + 3.0
        while time.time() < deadline:
            hwnd, win = self._root(MGR_TITLE)
            fresh = [c for c in self._label_cells(win) if (c["name"], c["top"]) not in before]
            if fresh:
                # 新行是插在「新建标签」原来那个位置上的（实测：它把入口往下顶了一格）
                new_row = next((c for c in fresh if c["top"] == create_top), fresh[-1])
                break
            time.sleep(0.4)
        if new_row is None:
            return _result(False, "no-new-row：点了「新建标签」但列表没多出行",
                           label=name)
        # 刚建出来的那一行微信**已经把它放进行内编辑态**了（Name 还是空的）。这个
        # 时候右键是弹不出菜单的（实测：no-menu），直接「剪贴板 + Ctrl+V + 回车」
        # 就落在它身上；这条路没生效，再退回「右键 →「修改标签名」」那条。
        if not new_row["name"]:
            self._commit_name(name)
            if not verify or self._label_exists(name):
                return _result(True, "ok", label=name, via="editing")
        renamed = self._rename(new_row, name)
        if not verify:
            return renamed if not renamed["ok"] else _result(True, "ok",
                                                             label=name, via="menu")
        if self._label_exists(name):
            return _result(True, "ok", label=name, via="menu")
        # 两条路都没把名字落下。真话是「多了一行、名字没提交」，界面上它会念成
        # 「未命名」；兜底那一下报的是 no-menu 还是别的，一起带出来好排查。
        return _result(False, "not-renamed：改名没生效（列表里多了一行「%s」，界面上会"
                              "显示成「未命名」；兜底改名：%s）"
                       % (new_row["name"] or "空名", renamed["reason"]),
                       label=name, menu_reason=renamed["reason"])

    def _label_exists(self, name: str) -> bool:
        hwnd, win = self._root(MGR_TITLE)
        return self.find_label(name, win) is not None

    def _commit_name(self, new_name: str) -> None:
        """行内编辑态下直接把名字贴进去并回车（不右键、不开菜单）。"""
        from wechatauto.uia_driver import WeChatUIA
        WeChatUIA._clip_set(new_name)
        _press(VK_CONTROL, VK_V)
        time.sleep(0.7)
        _press(VK_RETURN)
        time.sleep(1.4)

    def _rename(self, row, new_name: str) -> Dict:
        """行内改名：右键 →「修改标签名」→ 剪贴板粘贴 → 回车。"""
        hwnd, win = self._root(MGR_TITLE)
        cell = row["cell"] if isinstance(row, dict) else row
        menu, mhwnd = self._open_menu(hwnd, cell, want=MENU_RENAME)
        if menu is None:
            return _result(False, "no-menu：右键没出菜单或菜单里没有「%s」" % MENU_RENAME)
        item = self._find(menu, (MENU_RENAME,), contains=False)
        if item is None:
            return _result(False, "no-rename-item：菜单里没有「%s」" % MENU_RENAME)
        from wechatauto.uia_driver import WeChatUIA
        WeChatUIA._clip_set(new_name)
        r = self._click(mhwnd, item, raise_it=False)
        if not r["ok"]:
            return r
        time.sleep(0.5)
        # 编辑框不进 UIA，只能贴 + 回车（实测唯一可行路径）
        _press(VK_CONTROL, VK_V)
        time.sleep(0.7)
        _press(VK_RETURN)
        time.sleep(1.4)
        return _result(True, "ok")

    def rename_label(self, old: str, new: str) -> Dict:
        old, new = (old or "").strip(), (new or "").strip()
        if not old or not new:
            return _result(False, "bad-name：旧名和新名都要给")
        op = self.open_manager()
        if not op["ok"]:
            return op
        row = self.find_label(old, op["win"])
        if row is None:
            return _result(False, "no-label：没有标签「%s」" % old, label=old)
        rhythm.gate("label")
        r = self._rename(row, new)
        if not r["ok"]:
            return r
        hwnd, win = self._root(MGR_TITLE)
        if self.find_label(new, win) is None:
            return _result(False, "not-renamed：改名没生效", label=old)
        return _result(True, "ok", label=new)

    def delete_label(self, name: str) -> Dict:
        """删标签（微信自己的文案：「删除标签后，相关联系人不会被删除」）。"""
        name = (name or "").strip()
        op = self.open_manager()
        if not op["ok"]:
            return op
        row = self.find_label(name, op["win"])
        if row is None:
            return _result(False, "no-label：没有标签「%s」" % name, label=name)
        rhythm.gate("label")
        menu, mhwnd = self._open_menu(op["hwnd"], row["cell"], want=MENU_DELETE)
        if menu is None:
            return _result(False, "no-menu：右键标签行没弹出菜单（或菜单里没有「%s」）" % MENU_DELETE)
        item = self._find(menu, (MENU_DELETE,), contains=False)
        if item is None:
            return _result(False, "no-delete-item：菜单里没有「%s」" % MENU_DELETE)
        r = self._click(mhwnd, item, raise_it=False)
        if not r["ok"]:
            return r
        time.sleep(1.0)
        # 确认框是又一个独立小窗：XOutlineButton「删除」/「取消」
        hwnd, win = self._root(MGR_TITLE)
        confirm, chwnd = self._find_in_popups((BTN_CONFIRM_DELETE,), also=win,
                                              also_hwnd=hwnd)
        if confirm is not None:
            r = self._click(chwnd, confirm, raise_it=False)
            if not r["ok"]:
                return r
            time.sleep(1.0)
        hwnd, win = self._root(MGR_TITLE)
        if self.find_label(name, win) is not None:
            return _result(False, "still-there：确认之后标签还在", label=name)
        return _result(True, "ok", label=name)

    def _open_menu(self, hwnd: int, ctrl, want: Optional[str] = None,
                   attempts: int = 3) -> Tuple[Optional[object], int]:
        """右键某控件，返回 (菜单根控件, 菜单窗口句柄)。菜单是独立顶层小窗。

        ``want`` 给定时会**重试到菜单里真的出现那一项**：实测弹层有时先出来一个
        空壳（几百毫秒后才填内容），只认「有新窗口」就会报「菜单里没有 X」，
        时好时坏。
        """
        for _ in range(max(1, attempts)):
            before = {h for h, _ in self._popup_windows()}
            r = self._click(hwnd, ctrl, right=True)
            if not r["ok"]:
                wxlog.debug("右键没落准：%s", r["reason"])
                return None, 0
            deadline = time.time() + 2.5
            while time.time() < deadline:
                for h, w in self._popup_windows():
                    if h in before or w is None:
                        continue
                    if want is None:
                        return w, h
                    if self._find(w, (want,), contains=False) is not None:
                        return w, h
                time.sleep(0.25)
            time.sleep(0.4)
        return None, 0

    def _find_in_popups(self, names: Sequence[str], also=None, also_hwnd: int = 0):
        """在那些独立小窗（和可选的某个根）里按名字找控件，返回 (控件, 它所在窗口)。

        必须把句柄一起带回来：点击前的落点归属校验要拿它和 ``WindowFromPoint``
        的结果比，拿错窗口就等于没校验。
        """
        for h, w in self._popup_windows():
            if w is None:
                continue
            hit = self._find(w, names, contains=False)
            if hit is not None:
                return hit, h
        if also is not None:
            hit = self._find(also, names, contains=False)
            if hit is not None:
                return hit, also_hwnd
        return None, 0

    # ------------------------------------------------------------- 批量成员
    def add_members(self, label: str, members: Sequence[str],
                    verify: bool = True) -> Dict:
        """右键标签 →「添加成员」→ 在「微信添加成员」里勾人 →「完成」。

        勾选是否真落上，看选择框右侧那行 ``已选择N个联系人``（实测可读），
        不靠「点了没报错」。
        """
        label = (label or "").strip()
        names = [str(m).strip() for m in (members or []) if str(m).strip()]
        if not label:
            return _result(False, "bad-label：没给标签名")
        if not names:
            return _result(False, "bad-members：没给成员")
        op = self.open_manager()
        if not op["ok"]:
            return op
        row = self.find_label(label, op["win"])
        if row is None:
            made = self.create_label(label)
            if not made["ok"]:
                return made
            op = self.open_manager()
            if not op["ok"]:
                return op
            row = self.find_label(label, op["win"])
            if row is None:
                return _result(False, "no-label：标签「%s」拿不到" % label, label=label)
        rhythm.gate("label")
        count_before = row["count"]
        menu, mhwnd = self._open_menu(op["hwnd"], row["cell"], want=MENU_ADD)
        item = self._find(menu, (MENU_ADD,), contains=False) if menu else None
        if item is None:
            return _result(False, "no-add-item：菜单里没有「%s」" % MENU_ADD)
        r = self._click(mhwnd, item, raise_it=False)
        if not r["ok"]:
            return r
        ph, picker = self._wait_window(PICKER_TITLE)
        if picker is None:
            return _result(False, "no-picker：「%s」窗口没出现" % PICKER_TITLE,
                           label=label)
        self._searched = None
        picked, missing = [], []
        for nm in names:
            got = self._pick_one(ph, picker, nm)
            (picked if got else missing).append(nm)
            hwnd, picker = self._root(PICKER_TITLE)
        sel = self._selected_count(picker)
        done = self._find(picker, (BTN_DONE,), contains=False)
        if done is None:
            return _result(False, "no-done：选择框里没有「%s」按钮" % BTN_DONE,
                           label=label, picked=picked, missing=missing)
        if not picked:
            self._click(ph, self._find(picker, (BTN_CANCEL,), contains=False) or done)
            return _result(False, "no-member：一个都没勾上（名字要和界面显示一致，"
                                  "备注/昵称都行）", label=label, missing=missing)
        r = self._click(ph, done)
        if not r["ok"]:
            return r
        time.sleep(1.5)
        out = _result(True, "ok", label=label, picked=picked, missing=missing,
                      selected_in_picker=sel, count_before=count_before)
        if verify:
            op = self.open_manager()
            now = self.find_label(label, op.get("win")) if op["ok"] else None
            out["count_after"] = now["count"] if now else None
            if now is None or now["count"] <= count_before:
                out["verified"] = False
                out["reason"] = "count-unchanged：点了完成但标签行上的人数没变"
            else:
                out["verified"] = True
        return out

    def _pick_one(self, ph: int, picker, name: str) -> bool:
        """在选择框里勾一个人。

        两条路：① 直接翻列表找 ``SPSelectionContactRow``；② 找不到就用它自己的
        搜索框，**搜索结果行是另一个类名** ``SearchContactCellView``（实测）。
        上一个人是用搜索找到的话，先清掉那个词——列表被旧词过滤着，下一个人
        翻列表一定翻不到。
        """
        self._clear_picker_search(ph, picker)
        for cls in (PICKER_ROW_CLS, PICKER_SEARCH_ROW_CLS):
            row = self._find(picker, (name,), cls=cls, contains=False)
            if row is not None:
                return bool(self._click(ph, row)["ok"])
        box = self._find(picker, (SEARCH_BOX_NAME,), contains=False)
        if box is None:
            return False
        if not self.uia._set_text(box, name):
            return False
        self._searched = name
        deadline = time.time() + 3.0
        while time.time() < deadline:
            ph, picker2 = self._root(PICKER_TITLE)
            picker2 = picker2 or picker
            for cls in (PICKER_SEARCH_ROW_CLS, PICKER_ROW_CLS):
                row = self._find(picker2, (name,), cls=cls, contains=False)
                if row is not None:
                    return bool(self._click(ph, row)["ok"])
            time.sleep(0.4)
        return False

    def _clear_picker_search(self, ph: int, picker) -> None:
        """清掉选择框的搜索词（有「清空」按钮就点它，没有就写空串）。

        只在**我们上一次确实搜过**时动手：搜索词只会把行藏起来、不会凭空多出
        不相干的行，所以直接翻列表那条路不受它影响；真被过滤住了，下面会落到
        搜索那条路并把词覆盖掉。为了一个不存在的旧词多点一下，代价是一次节流。
        """
        if not self._searched:
            return
        self._searched = None
        btn = self._find(picker, (PICKER_CLEAR,), contains=False)
        if btn is not None:
            self._click(ph, btn)
            return
        box = self._find(picker, (SEARCH_BOX_NAME,), contains=False)
        if box is not None:
            self.uia._set_text(box, "")
            time.sleep(0.5)

    def _selected_count(self, picker) -> Optional[int]:
        """读取合并文案或管理窗同一行的「已选择 / N / 人」；不明时不猜 0。"""
        counts = set()
        stack = [(picker, 0)]
        while stack:
            control, depth = stack.pop()
            if control is None or depth > 30:
                continue
            try:
                match = SELECTED_RE.search((control.Name or "").strip())
                if match:
                    counts.add(int(match.group(1)))
                children = list(control.GetChildren())
                for index, child in enumerate(children[:-1]):
                    if (child.Name or "").strip() != "已选择":
                        continue
                    number = children[index + 1]
                    text = (number.Name or "").strip()
                    if number.ControlTypeName == "ButtonControl" and text.isdecimal():
                        counts.add(int(text))
                stack.extend((child, depth + 1) for child in children)
            except Exception as exc:
                wxlog.debug("读取选择人数失败：%s", exc)
                return None
        return next(iter(counts)) if len(counts) == 1 else None

    def _detail_checkbox_point(self, win, cell) -> Optional[Tuple[int, int]]:
        """选择框不进 UIA，用行左界和昵称列左界之间的选择列定位，避免点到头像。"""
        try:
            headers = [header for header in self._rows(win, DETAIL_HEADER_CLS)
                       if (header.Name or "").strip() == "昵称"]
            if len(headers) != 1:
                return None
            row = cell.BoundingRectangle
            header = headers[0].BoundingRectangle
            width = header.left - row.left
            if (row.width() <= 0 or row.height() <= 0 or header.width() <= 0
                    or header.height() <= 0 or header.bottom > row.top
                    or not 0 < width < row.width() or width > row.height() * 2):
                return None
            return (row.left + header.left) // 2, (row.top + row.bottom) // 2
        except Exception as exc:
            wxlog.debug("读取成员选择列失败：%s", exc)
            return None

    def remove_members(self, label: str, members: Sequence[str]) -> Dict:
        """选中标签 → 勾成员行左侧复选框 → 底部「移出标签」。

        复选框不进 UIA，依据当前选择列定位；每次点击后核实选择人数增加，
        不能把点击投递成功或已有操作栏当作勾选成功。
        """
        label = (label or "").strip()
        names = [str(m).strip() for m in (members or []) if str(m).strip()]
        if not label:
            return _result(False, "bad-label：没给标签名")
        if not names:
            return _result(False, "bad-members：没给成员")
        op = self.open_manager()
        if not op["ok"]:
            return op
        row = self.find_label(label, op["win"])
        if row is None:
            return _result(False, "no-label：没有标签「%s」" % label, label=label)
        rhythm.gate("label")
        count_before = row["count"]
        r = self._click(op["hwnd"], row["cell"])
        if not r["ok"]:
            return r
        time.sleep(1.2)
        ph, win = self._root(MGR_TITLE)
        selected = self._selected_count(win)
        if selected not in (None, 0) or (selected is None and self._find(
                win, (BTN_REMOVE_MEMBER,), contains=False) is not None):
            return _result(False, "selection-not-empty：已有选择或选择人数读不到，不移出",
                           label=label, selected=selected)
        selected = 0
        clicked, missing = [], []
        for nm in dict.fromkeys(names):
            cell = None
            for c in self._rows(win, DETAIL_CELL_CLS):
                raw = (c.Name or "").strip()
                if raw.split("  ")[0].strip() == nm or raw.startswith(nm + " "):
                    cell = c
                    break
            if cell is None:
                missing.append(nm)
                continue
            point = self._detail_checkbox_point(win, cell)
            if point is None:
                return _result(False, "no-checkbox：选择列位置不明确，不点击成员行",
                               label=label, clicked=clicked, missing=missing, member=nm)
            got = self._click(ph, cell, at=point)
            if not got["ok"]:
                return dict(got, label=label, clicked=clicked, missing=missing, member=nm)
            deadline = time.monotonic() + self.appear_timeout
            while True:
                ph, win = self._root(MGR_TITLE)
                actual = self._selected_count(win)
                if actual == selected + 1:
                    selected = actual
                    clicked.append(nm)
                    break
                if time.monotonic() >= deadline or actual not in (None, selected):
                    return _result(False, "not-selected：点击后选择人数未按预期增加，不移出",
                                   label=label, clicked=clicked, missing=missing, member=nm,
                                   selected_before=selected, selected_after=actual)
                time.sleep(0.2)
        if not clicked:
            return _result(False, "no-member：一个都没勾上（成员只列可视区，"
                                  "或者名字和显示名不一致）", label=label,
                           missing=missing)
        bar = self._find(win, (BTN_REMOVE_MEMBER,), contains=False)
        if bar is None:
            return _result(False, "no-bar：底部没浮出「%s」（选中态没进去？）"
                           % BTN_REMOVE_MEMBER, label=label, clicked=clicked,
                           missing=missing, names=self._names(win))
        r = self._click(ph, bar)
        if not r["ok"]:
            return r
        time.sleep(1.5)
        ph, win = self._root(MGR_TITLE)
        now = self.find_label(label, win)
        count_after = now["count"] if now else None
        if count_after is None or count_after >= count_before:
            return _result(False, "count-unchanged：点了移出但人数没变",
                           label=label, clicked=clicked, missing=missing,
                           count_before=count_before, count_after=count_after)
        if count_after != count_before - len(clicked):
            return _result(False, "count-mismatch：人数变化与已选成员数不一致",
                           label=label, clicked=clicked, missing=missing,
                           count_before=count_before, count_after=count_after)
        return _result(True, "ok", label=label, clicked=clicked, missing=missing,
                       count_before=count_before, count_after=count_after)

    def label_members(self, label: str, scroll: bool = True,
                      limit: Optional[int] = None,
                      max_scrolls: int = 80) -> Dict:
        """某个标签的**全部**成员（右侧列表是虚拟化的，一屏只有十几行）。

        滚动取全量的口径和 ``media`` 那条链一致：滚到「连续两次都没有新行」为止，
        并且把停下来原因如实报出来（``bottom`` / ``limit`` / ``no-list`` /
        ``scroll-cap`` / ``incomplete``），不假装取全了。``count`` 是标签行上
        微信自己显示的人数，拿它和 ``len(members)`` 对不上时 ``complete`` 就是
        False，而 ``bottom`` 却没取全会被判成失败（``incomplete``）。

        成员行的 Name 是 ``昵称 备注 标签`` 三段拼的（空段留空格），拆出来的
        ``display`` 才是搜索框认的那个名字；拆坏的会被 :meth:`resolve_members`
        按「查不到/重名」退回给调用方，不猜人。
        """
        label = (label or "").strip()
        op = self.open_manager()
        if not op["ok"]:
            return op
        row = self.find_label(label, op["win"])
        if row is None:
            return _result(False, "no-label：没有标签「%s」" % label, label=label,
                           members=[])
        r = self._click(op["hwnd"], row["cell"])
        if not r["ok"]:
            return r
        # 左栏先画、右栏后画：刚点开时读一次往往是空的，读早了会误报「没成员」。
        deadline = time.time() + self.appear_timeout
        while time.time() < deadline:
            _h, win = self._root(MGR_TITLE)
            if win is not None and self._rows(win, DETAIL_CELL_CLS):
                break
            time.sleep(0.4)
        # 实测：这个窗口**留着上一次的滚动位置**，再点一次同一个标签行也不会回到
        # 顶部。不倒回去就从中间开始数，数到「下面没新行」还以为取全了（真机第一次
        # 就栽在这：63 人只读到 22 个就报「到底」）。
        rewound = 0
        if scroll:
            rewound = self._rewind()
        acc: List[str] = []
        scrolls, stalled = 0, 0
        reason = "bottom"
        gapped = False
        prev_page: Optional[List[str]] = None
        while True:
            _h, win = self._root(MGR_TITLE)
            if win is None:
                reason = "window-gone"
                break
            page = [(c.Name or "").strip() for c in self._rows(win, DETAIL_CELL_CLS)
                    if (c.Name or "").strip()]
            if not page:
                if not acc:
                    _h2, w2 = self._root(MGR_TITLE)
                    if w2 is None or self._find(w2, (), cls=DETAIL_LIST_CLS) is None:
                        reason = "no-list"          # 连列表容器都没有
                        break
                    if not (row["count"] or 0):
                        reason = "bottom"           # 微信说 0 人，列表也真是空的
                        break
                # 空的一屏不算「漏了一段」：滚过底、或者行还没画出来都会这样，
                # 交给下面「连续两轮看到的没变化」收工。
                if acc:
                    stalled += 1
                    if stalled >= 2:
                        break
            else:
                acc, overlap = stitch_rows(acc, page)
                if overlap == 0 and scrolls:
                    gapped = True               # 中间漏了一屏，别装作接上了
                if prev_page is not None and page == prev_page:
                    stalled += 1
                    if stalled >= 2:
                        break
                else:
                    stalled = 0
                prev_page = page
            if limit is not None and len(acc) >= limit:
                acc = acc[:limit]
                reason = "limit"
                break
            if not scroll:
                reason = "no-scroll"
                break
            self._raise(_h)
            if not self._scroll_detail(win):
                reason = "scroll-stuck" if acc else "no-list"
                break
            scrolls += 1
            if scrolls >= max_scrolls:
                reason = "scroll-cap"
                break
        members = [parse_detail_name(raw, label) for raw in acc]
        if members and not any(m["same_label"] for m in members):
            # 右侧那一栏**必须真的切到这个标签**了：没切的话它显示的还是「全部」，
            # 念出来就是一整屏不相干的人，比报错更糟（成员行的标签列带标签名）。
            return _result(False, "not-selected：右侧没切到标签「%s」" % label,
                           label=label, members=members)
        count = row["count"] or 0
        # 「取全了没有」只认微信自己报的那个人数：名字重叠对不对只能当线索。实测
        # 同一把滚轮每轮走 5~10 行、不等速，正好跳整屏时两屏会一个名字都对不上，
        # 但那一次其实**没漏人**；反过来真漏了行时，数出来的一定比 63 小。
        complete = len(members) >= count
        if reason == "bottom" and not complete:
            # 微信自己报的人数比数出来的多：这不是「取全了」。当成成功返回，
            # 按标签批量发送就会把差掉的人默默跳过——宁可停下来报错。
            return _result(False, "incomplete：标签「%s」显示 %s 人，只读到 %s 个（%s）"
                           % (label, count, len(members),
                              "有一屏没接上，中间可能漏了几行"
                              if gapped else "列表没滚到底"),
                           label=label, members=members, count=count,
                           scrolls=scrolls, rewinds=rewound, complete=False,
                           gapped=gapped)
        msg = reason
        if gapped and complete:
            msg = "%s（有一屏没接上，按微信显示的人数核过：%s 个对得上）" % (reason, count)
        return _result(reason in ("bottom", "limit"), reason, label=label,
                       members=members, count=count, scrolls=scrolls,
                       rewinds=rewound, gapped=gapped, complete=complete,
                       detail=msg)

    def _rewind(self, notches: int = 20, max_rounds: int = 15) -> int:
        """把成员列表倒回顶部，回来回滚了几轮。

        倒回顶不需要精确：只要**看得见的这一屏不再变**，就是到顶了（顶部之上滚
        不动）。轮数封顶，免得某个版本改成能无限滚的弹性列表就把我们挂住。
        """
        moved = 0
        prev: Optional[List[str]] = None
        for _ in range(max_rounds):
            _h, win = self._root(MGR_TITLE)
            if win is None:
                break
            page = [(c.Name or "").strip() for c in self._rows(win, DETAIL_CELL_CLS)
                    if (c.Name or "").strip()]
            if prev is not None and page == prev:
                break
            prev = page
            if not self._scroll_detail(win, notches=notches, direction="up"):
                break
            moved += 1
        return moved

    def _scroll_detail(self, win, notches: int = 3,
                       direction: str = "down") -> bool:
        """在成员列表里滚一小段。返回有没有可滚的列表。

        实测（4.1.15.13，63 人的标签，一屏 10~11 行）：一格 ``120`` 平均滚
        1~2 行（同一把滚轮每轮走 5~10 行，不等速），而一整把 ``-600`` 经常一点
        不动。所以按 ``find_in_message_list`` 那条已验证的写法来：游标停在**列表
        矩形中心**，一小格一小格地滚，每轮 3 格 ≈ 4 行 —— 一屏 10 行、每轮走 4 行，
        屏与屏至少还剩 6 行重叠，拼接才不会时不时对不上。
        """
        box = None
        lst = self._find(win, (), cls=DETAIL_LIST_CLS)
        if lst is not None:
            try:
                box = lst.BoundingRectangle
            except Exception:
                box = None
        if box is None:
            for c in self._rows(win, DETAIL_CELL_CLS):
                try:
                    box = c.BoundingRectangle
                    break
                except Exception:
                    continue
        if box is None:
            return False
        x = (box.left + box.right) // 2
        y = (box.top + box.bottom) // 2
        step = 120 if direction == "up" else -120
        for _ in range(max(1, int(notches))):
            self.uia._wheel_at(x, y, step)
        time.sleep(0.8)
        return True

    def _lookup(self, name: str) -> List[str]:
        """显示名 -> 候选 wxid 列表（0 个=查不到，>1 个=重名）。

        用 ``search_contact`` 而不是 ``username_by_nickname``：后者只返回第一个
        命中，重名的人会被它悄悄折成「查不到」，而「查不到」和「不敢发」
        是两件事——后者必须单独报出来让人换 wxid。
        """
        out: List[str] = []
        try:
            for hit in self.db.search_contact(name) or []:
                if name in (hit.get("nick_name"), hit.get("remark"),
                            hit.get("username")):
                    u = hit.get("username")
                    if u and u not in out:
                        out.append(u)
        except Exception as e:
            wxlog.debug("search_contact(%r) 失败：%s", name, e)
        if out:
            return out
        try:
            u = self.db.username_by_nickname(name)
        except Exception:
            u = None
        return [u] if u else []

    def resolve_members(self, members: Sequence[Dict]) -> Dict:
        """把成员行解析成**能安全发送**的收件人：每人必须唯一对应一个 wxid。

        重名的人（两个「小明」）和查不到的人一律进 ``skipped`` 交回调用方，
        不随便挑一个——按标签群发发错人是最贵的一种错。
        """
        resolved, skipped = [], []
        for m in members or []:
            disp = (m.get("display") or "").strip()
            if not disp:
                skipped.append({"display": m.get("raw"), "reason": "no-name"})
                continue
            cands = []
            for v in (disp, m.get("remark"), m.get("nick")):
                v = (v or "").strip()
                if v and v not in cands:
                    cands.append(v)
            users: List[str] = []
            for c in cands:
                for u in self._lookup(c):
                    if u not in users:
                        users.append(u)
                if len(users) > 1:
                    break
            if not users:
                skipped.append({"display": disp, "reason": "unresolved"})
            elif len(users) > 1:
                skipped.append({"display": disp, "reason": "ambiguous",
                                "usernames": users})
            else:
                resolved.append({"display": disp, "username": users[0],
                                 "raw": m.get("raw")})
        return _result(True, "ok", recipients=resolved, skipped=skipped)

    # ------------------------------------------------------------------ 探针
    def probe(self) -> List[Dict]:
        """走一遍路径，把每一步判定 + 当层控件名 + 整棵树存盘（只导航不写）。"""
        out: List[Dict] = []
        for key, fn in (("open_contacts_tab", self.open_contacts_tab),
                        ("open_manager", self.open_manager)):
            r = fn()
            r = dict(r)
            r.pop("win", None)
            r["step"] = key
            out.append(r)
            if not r["ok"]:
                break
        hwnd, win = self._root(MGR_TITLE)
        if win is not None:
            rows = [{"name": x["name"], "count": x["count"]} for x in self.label_rows(win)]
            out.append(_result(True, "ok", step="label_rows", labels=rows,
                               names=self._names(win)))
            self._dump("label_rows", ["%s(%s)" % (x["name"], x["count"]) for x in rows]
                       + self._tree_lines(win))
        return out

    def _dump(self, tag: str, lines: Sequence[str]) -> None:
        if not self.dump_dir:
            return
        try:
            os.makedirs(self.dump_dir, exist_ok=True)
            with open(os.path.join(self.dump_dir, "probe_%s.txt" % tag),
                      "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass

    def _tree_lines(self, root=None, max_depth: int = 26, max_nodes: int = 4000) -> List[str]:
        win = root if root is not None else self.uia._win
        out: List[str] = []
        if win is None:
            return out
        stack = [(win, 0)]
        while stack and len(out) < max_nodes:
            el, d = stack.pop()
            if d > max_depth:
                continue
            try:
                r = el.BoundingRectangle
                rect = "(%d,%d,%d,%d)" % (r.left, r.top, r.right, r.bottom)
            except Exception:
                rect = "?"
            try:
                out.append("%s%s cls=%r name=%r aid=%r rect=%s" % (
                    "  " * d, el.ControlTypeName, el.ClassName,
                    (el.Name or "")[:40], getattr(el, "AutomationId", ""), rect))
            except Exception:
                continue
            try:
                kids = list(el.GetChildren())
            except Exception:
                kids = []
            for k in reversed(kids):
                stack.append((k, d + 1))
        return out

    # ------------------------------------------------------------------ 收尾
    def back_to_chat(self) -> bool:
        """把主窗带回聊天页（管理窗留着不动，用户可能还在看）。"""
        try:
            return bool(self.uia.back_to_chat_tab())
        except Exception:
            return False
