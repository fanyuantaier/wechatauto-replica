# -*- coding: utf-8 -*-
"""标签模块的离线自测：假窗口树 + 一个「假微信」（WechatSim）演出点完之后的状态变化。

跑法：python tools/test_labels.py
      python tools/test_labels.py --mutateN   打一处变异，验证判据真的会红（N=1..20）

被测代码走的全是真路径，只有三样是假的：**找窗口**、**抬窗口**、**落点归属**。
这三样恰好是真机上最容易出事的地方（实测：通讯录管理是独立顶层窗口、右键菜单和
确认框各是另一个小窗、「微信添加成员」又是一个、成员列表还是虚拟化的），所以
WechatSim 按实测的样子把这些窗口和它们的状态转移演出来：

  点通讯录页那一行      → 「通讯录管理」独立窗口出现
  点「新建标签」        → 左栏多出一行 ``未命名(0)``
  右键标签行            → 弹出菜单小窗（添加成员/复制/修改标签名/删除标签）
  点「修改标签名」+回车 → 那一行改成剪贴板里的名字
  点「删除标签」        → 弹出确认框小窗；点「删除」→ 那一行消失
  勾成员行复选框        → 底部浮出「移出标签」等按钮
  点「移出标签」        → 成员行消失、标签行人数变少
  勾选择框里的联系人    → 右侧「已选择N个联系人」跟着变
  点「完成」            → 选择框关掉、标签行人数变多
  在成员列表上滚轮      → 换一批行（虚拟化列表）

断言的是**结构化 reason**，不是布尔值：「点了没建成」和「压根没找到按钮」对用户
是两件事。每条判据都配一条反例。
"""
import os
import sys
import types

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from wechatauto import labels as L                      # noqa: E402
from wechatauto import uia_driver as _ud                # noqa: E402
from wechatauto import wx as _wxmod                     # noqa: E402

PASSED, FAILED = [], []
EVENTS = []
GATES = []


def check(name, got, want):
    ok = got == want
    (PASSED if ok else FAILED).append(name)
    print("%-4s %-56s got=%r want=%r" % ("ok" if ok else "FAIL", name, got, want))


def in_rect(pt, left, top, right, bottom):
    """落点还在不在这一块矩形里（抖点之后不能再用「等于正中」当判据）。"""
    x, y = pt
    return left <= x < right and top <= y < bottom


def code(r):
    """reason 的机器可读前缀：'no-page：点了…' -> 'no-page'。"""
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
                 rect=(816, 700, 1216, 780)):
        self.Name = name
        self.ClassName = cls
        self.ControlTypeName = ctype
        self.AutomationId = ""
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

    def drop(self, pred):
        self._kids = [k for k in self._kids if not pred(k)]
        for k in self._kids:
            if hasattr(k, "drop"):
                k.drop(pred)


def walk(root, d=0):
    if root is None or d > 30:
        return
    for k in list(root.GetChildren()):
        yield k
        for x in walk(k, d + 1):
            yield x


def first(root, pred):
    for n in walk(root):
        if pred(n):
            return n
    return None


# ---------------------------------------------------------------------------
# 真机形状的树（类名/文案照 label_probe 里的快照）
# ---------------------------------------------------------------------------
def label_cell(name, count):
    return Node("%s(%d)" % (name, count), L.LABEL_ROW_CLS, "ListItemControl")


def detail_cell(nick, label="同学", remark=""):
    """成员行：``昵称 备注 标签`` 三段拼，空段留空格（实测就是这个格式）。"""
    return Node("%s %s %s" % (nick, remark, label),
                L.DETAIL_CELL_CLS, "ListItemControl", rect=(816, 700, 1216, 748))


def main_window(mgr_row=True, tab=True):
    kids = []
    if tab:
        kids.append(Node("导航", "mmui::MainTabBar", "ToolBarControl", [
            Node("微信", "mmui::XTabBarItem", "ButtonControl"),
            Node("通讯录", "mmui::XTabBarItem", "ButtonControl"),
            Node("发现", "mmui::XTabBarItem", "ButtonControl"),
        ]))
    kids.append(Node("新的朋友", "mmui::ContactsCellGroupView", "ListItemControl"))
    if mgr_row:
        kids.append(Node("", L.MAIN_MGR_ROW_CLS, "ListItemControl"))
    return Node("微信", "mmui::MainWindow", "WindowControl", kids)


def at(node, top):
    """给一行安排真机那样的位置：左栏每行高 80px，从上往下排（实测 rect 就是这个间距）。"""
    node.BoundingRectangle = Rect(816, top, 1216, top + 80)
    return node


def mgr_window(labels=(("同学", 63), ("大人", 18)), members=("小明", "小红", "小刚"),
               collapsed=False):
    """左栏。折叠态照真机：只剩分组标题，**标签行和「新建标签」整组不在树里**。"""
    folder = Node(L.GROUP_FOLDER, L.FOLDER_ROW_CLS, "ListItemControl")
    group = ([Node("无标签(90)", L.LABEL_ROW_CLS, "ListItemControl")]
             + [label_cell(n, c) for n, c in labels]
             + [Node("新建标签", L.CREATE_ROW_CLS, "ListItemControl")])
    rows = [Node("全部(171)", "mmui::ContactsManagerControlAllCell", "ListItemControl"),
            Node("筛选", "mmui::ContactsManagerControlFilterCell", "ListItemControl"),
            Node("朋友权限", L.FOLDER_ROW_CLS, "ListItemControl"),
            folder]
    if not collapsed:
        rows += group
    rows.append(Node("最近群聊", L.FOLDER_ROW_CLS, "ListItemControl"))
    for i, r in enumerate(rows):          # 一行一格，80px（实测就是这个间距）
        at(r, 432 + i * 80)
    panel = Node("", "mmui::ContactsManagerControlView", "ListControl", rows)
    header = Node("昵称", L.DETAIL_HEADER_CLS, "ButtonControl",
                  rect=(880, 664, 997, 696))
    detail = Node("", L.DETAIL_LIST_CLS, "ListControl",
                  [detail_cell(m) for m in members], rect=(816, 700, 1216, 940))
    win = Node("通讯录管理", L.MGR_WIN_CLS, "WindowControl", [panel, header, detail])
    win.hidden_group = [] if not collapsed else group     # 点开分组时插回去
    return win


def expand_group(mgr):
    """点分组标题 = 那一组的行插回树里（实测：再点一次**不会**收起）。"""
    panel = first(mgr, lambda c: "ControlView" in (c.ClassName or ""))
    if panel is None or not getattr(mgr, "hidden_group", None):
        return False
    kids = panel.GetChildren()
    idx = next((i for i, k in enumerate(kids)
                if (k.Name or "").strip() == L.GROUP_FOLDER), len(kids))
    panel._kids = kids[:idx + 1] + mgr.hidden_group + kids[idx + 1:]
    for i, r in enumerate(panel._kids):
        at(r, 432 + i * 80)
    mgr.hidden_group = []
    return True


def picker_window(names=("小明", "小红")):
    counter = Node("已选择0个联系人", "mmui::XTableView", "TextControl")
    win = Node("微信添加成员", "mmui::XView", "WindowControl", [
        Node(L.PICKER_LIST_NAME, "mmui::StickyHeaderRecyclerListView", "ListControl",
             [Node(n, L.PICKER_ROW_CLS, "CheckBoxControl") for n in names]),
        counter,
        Node("搜索", "mmui::XValidatorTextEdit", "EditControl"),
        Node("完成", "mmui::XButton", "ButtonControl"),
        Node("取消", "mmui::XOutlineButton", "ButtonControl"),
    ])
    win.searched = ""
    win.browsed = list(names)
    return win, counter


def picker_list(win):
    return first(win, lambda c: L.PICKER_LIST_NAME in (c.Name or ""))


def menu_window():
    return Node("Weixin", "mmui::XMenu", "WindowControl",
                [Node(i, "mmui::XMenuView", "MenuItemControl")
                 for i in (L.MENU_ADD, "复制", L.MENU_RENAME, L.MENU_DELETE)])


def confirm_window():
    # 顺序是故意的：``uiautomation._find_by`` 用栈遍历，兄弟节点**倒序**访问，
    # 所以「最后一个」会先被命中。把标题文字「删除标签」放最后，就能把「按包含
    # 匹配」的实现打成红——它会更早撞上标题而不是按钮「删除」。
    return Node("Weixin", "mmui::XView", "WindowControl", [
        Node(L.BTN_CONFIRM_DELETE, "mmui::XOutlineButton", "ButtonControl"),
        Node(L.BTN_CANCEL, "mmui::XOutlineButton", "ButtonControl"),
        Node(L.MENU_DELETE, "mmui::XTextView", "TextControl"),
    ])


# ---------------------------------------------------------------------------
# 假微信
# ---------------------------------------------------------------------------
class WechatSim:
    MAIN, MGR, PICKER, MENU, CONFIRM = 1000, 1001, 1002, 2001, 2002
    PAGE = 5                        # 一屏画得下的成员行数（真机 10~11 行，比例照真机）
    ROWS_PER_NOTCH = 1.3            # 实测：一格滚轮走 1~2 行，且每轮不等速

    def __init__(self, main, mgr=None, picker=None, pick_names=("小明", "小红"),
                 extra_contacts=(), member_pool=None, picker_fails=False,
                 picker_blocks=False, rows_per_notch=None):
        self.rows_per_notch = self.ROWS_PER_NOTCH if rows_per_notch is None \
            else rows_per_notch
        self.main = main
        self.mgr = mgr
        self.picker = picker[0] if picker else None
        self.counter = picker[1] if picker else None
        self.popup = None           # 当前弹层：(hwnd, control)
        self.bar = None             # 底部动作条（勾了成员才出现）
        self.selection_status = self.selection_counter = None
        self.checked = []           # 成员行里勾上的
        self.last_click_point = None
        self.member_click_points = []
        self.profile_open = False
        self.picked = []            # 选择框里勾上的
        self.clip = None
        self.rename_pending = False
        self.rename_target = None   # 右键菜单是为哪一行弹的（改名就落到它身上）
        self.editing = None         # 新建出来那行正处在行内编辑态
        self.confirm_shown = False
        self.picker_fails = picker_fails
        self.picker_blocks = picker_blocks   # 残留选择框压住管理窗
        self.cancel_ineffective = False
        self.foreign = False
        self.pool = list(member_pool) if member_pool is not None else [
            detail_cell(m) for m in ("小明", "小红", "小刚")]
        self.page = 0               # 可视区顶上那一条是池子里的第几条
        self._rowpos = 0.0          # 累计滚了多少行（小数：一格不到一行）
        self.slow_render = 0        # >0：假时钟睡够 N 次右栏才画出来
        self.left_late = 0          # >0：睡够 N 次左栏才画出标签行
        self.left_pending = []      # 左栏那一组待插入的行
        self.browsable = list(pick_names)
        self.all_contacts = list(pick_names) + list(extra_contacts)
        self._show_page0()

    # --- 窗口注册表 ---------------------------------------------------
    def titled(self, title):
        if title == L.MGR_TITLE and self.mgr is not None:
            return self.MGR, self.mgr
        if title == L.PICKER_TITLE and self.picker is not None:
            return self.PICKER, self.picker
        return 0, None

    def control(self, hwnd):
        if self.popup and self.popup[0] == hwnd:
            return self.popup[1]
        return {self.MAIN: self.main, self.MGR: self.mgr,
                self.PICKER: self.picker}.get(hwnd)

    # --- 定位 ---------------------------------------------------------
    def first_detail(self):
        if self.mgr is None:
            return None
        return first(self.mgr, lambda c: L.DETAIL_LIST_CLS in (c.ClassName or ""))

    def detail(self):
        return self.first_detail()

    def panel(self):
        return first(self.mgr, lambda c: "ControlView" in (c.ClassName or ""))

    def find_row(self, name):
        for k in self.panel().GetChildren():
            if L.LABEL_ROW_CLS in (k.ClassName or "") and \
                    (k.Name or "").split("(")[0] == name:
                return k
        return None

    def set_count(self, name, delta):
        panel = self.panel()
        for i, k in enumerate(panel.GetChildren()):
            if (k.Name or "").startswith(name + "("):
                cur = int(k.Name.split("(")[1].rstrip(")"))
                panel._kids[i] = label_cell(name, cur + delta)
                return cur

    def _show_page0(self):
        self._show_at_page()

    def _show_at_page(self):
        if self.mgr is None or not self.pool:
            return
        det = self.first_detail()
        if det is not None:
            det._kids = self.pool[self.page:self.page + self.PAGE]
            for index, cell in enumerate(det._kids):
                cell.BoundingRectangle = Rect(816, 700 + index * 48, 1216, 748 + index * 48)

    def wheel(self, delta):
        """滚一小格：虚拟化列表换掉顶上的行（不滚就永远只看到第一屏）。

        照真机的样子来：一格 120 ≈ 0.7 行；正 delta 往上、负 delta 往下，两头都
        夹住（到顶/到底之后再滚，看见的行不变——判据就靠这个「没变化」收工）。
        一整把 -600 在真机上经常什么都不动，所以这里也按格折算，不让「一次调用=
        换一屏」混进判据。
        """
        if not self.pool:
            return
        self._rowpos += (-delta) / 120.0 * self.rows_per_notch
        self._rowpos = max(0.0, min(float(len(self.pool)), self._rowpos))
        self.page = int(self._rowpos)
        self._show_at_page()

    def type_search(self, text):
        """选择框里打字：结果行换类名，列表也被过滤（实测）。"""
        if self.picker is None:
            return
        lst = picker_list(self.picker)
        if lst is None:
            return
        self.picker.searched = text
        if not text:
            lst._kids = [Node(n, L.PICKER_ROW_CLS, "CheckBoxControl")
                         for n in self.picker.browsed]
            return
        lst._kids = [Node(n, L.PICKER_SEARCH_ROW_CLS, "CheckBoxControl")
                     for n in self.all_contacts if n == text]

    def press(self, *vks):
        EVENTS.append(vks)
        if vks == (L.VK_RETURN,) and (self.rename_pending or self.editing is not None):
            # 编辑态有两种来源：新建后那行本来就处于编辑态（实测 Name='(0)'），
            # 和右键「修改标签名」之后进入编辑态。两条路都必须能走到。
            row = self.editing if self.editing is not None else (
                self.rename_target or self.find_row(""))
            if row is not None and self.clip:
                m = L.LABEL_ROW_RE.match((row.Name or "").strip())
                cnt = m.group("count") if m else "0"
                row.Name = "%s(%s)" % (self.clip, cnt)
            self.rename_pending = False
            self.rename_target = None
            self.editing = None

    # --- 点击之后的反应 ------------------------------------------------
    def click(self, ctrl, right=False):
        nm = (ctrl.Name or "").strip()
        cls = ctrl.ClassName or ""
        if not right and L.LABEL_ROW_CLS in cls and self.editing is not None                 and ctrl is not self.editing:
            self.editing = None      # 点了别处 = 那一行提交掉（界面上变成「未命名」）
        if L.MAIN_MGR_ROW_CLS in cls and not right:
            if self.mgr is None:
                self.mgr = mgr_window()
            return
        if L.FOLDER_ROW_CLS in cls and nm == L.GROUP_FOLDER:
            if self.left_pending:
                # 左栏还没画出来就点标题：这一组本来就是展开的，点一下反而把它
                # 折叠掉，行就此没了（「读早了」最真实的代价）
                self.left_pending = []
                return
            self.mgr and expand_group(self.mgr)     # 点分组标题 = 那一组回到树里
            return
        if right and L.LABEL_ROW_CLS in cls:
            if self.editing is not None:
                self.popup = None           # 实测：那一行还在行内编辑态，右键弹不出菜单
                return
            self.popup = (self.MENU, menu_window())
            self.rename_target = ctrl        # 菜单是给**这一行**弹的
            return
        if L.LABEL_ROW_CLS in cls and not right:
            # 实测：再点一次同一个标签行**不会**把右侧列表拨回顶部，它就停在上次
            # 读到的位置。所以这里什么都不动——读取方必须自己倒回顶部。
            return
        if nm == L.MENU_RENAME:
            self.popup = None
            self.rename_pending = True
            return
        if nm == L.MENU_DELETE:
            self.popup = (self.CONFIRM, confirm_window())
            self.confirm_shown = True
            return
        if nm == L.MENU_ADD:
            self.popup = None
            if not self.picker_fails:
                self.picker, self.counter = picker_window(names=self.browsable)
            return
        if nm == L.BTN_CONFIRM_DELETE and self.popup and \
                self.popup[0] == self.CONFIRM:
            row = self.find_row("同学")
            if row is not None:
                self.panel().drop(lambda k: k is row)
            self.popup = None
            return
        if L.CREATE_ROW_CLS in cls:
            panel = self.panel()
            kids = panel.GetChildren()
            top = ctrl.BoundingRectangle.top
            # 实测：刚点出来的那一行 Name 是 ``'(0)'``（名字是空的，编辑态还没提交），
            # 而且它插在「新建标签」原来那一格，把入口往下顶一格。
            new = at(label_cell("", 0), top)
            panel._kids.insert(len(kids) - 1, new)
            at(ctrl, top + 80)
            self.editing = new          # 微信把新行直接放行内编辑态
            return
        if L.DETAIL_CELL_CLS in cls:
            point = self.last_click_point
            self.member_click_points.append(point)
            rect = ctrl.BoundingRectangle
            # 实测选择圆直径16px、距行左侧24px；头像区域不能当成已勾选。
            if point is None or not in_rect(point, rect.left + 24, rect.top + 16,
                                            rect.left + 40, rect.top + 32):
                self.profile_open = True
                return
            who = (ctrl.Name or "").split("  ")[0].strip()
            if who in self.checked:
                self.checked.remove(who)
            elif who:
                self.checked.append(who)
            if self.bar is None:
                self.bar = Node(L.BTN_REMOVE_MEMBER, "mmui::XMouseEventView",
                                "ButtonControl")
                self.selection_counter = Node("0", "mmui::XButton", "ButtonControl")
                self.selection_status = Node("", "mmui::XHBoxView", kids=[
                    Node("已选择", "mmui::XTextView", "TextControl"),
                    self.selection_counter,
                    Node("人", "mmui::XTextView", "TextControl"),
                    Node(L.BTN_UNCHECK, "mmui::XTextView", "TextControl"),
                    Node(L.BTN_SET_LABEL, "mmui::XMouseEventView", "ButtonControl"),
                    self.bar])
                self.mgr.add(self.selection_status)
            self.selection_counter.Name = str(len(self.checked))
            return
        if nm == L.BTN_REMOVE_MEMBER:
            self.set_count("同学", -len(self.checked))
            gone = set(self.checked)
            det = self.first_detail()
            if det is not None:
                det.drop(lambda k: (k.Name or "").split("  ")[0].strip() in gone)
            self.checked = []
            self.mgr.drop(lambda k: k is self.selection_status)
            self.bar = self.selection_status = self.selection_counter = None
            return
        if (L.PICKER_ROW_CLS in cls or L.PICKER_SEARCH_ROW_CLS in cls) and nm:
            if nm not in self.picked:
                self.picked.append(nm)
                if self.counter is not None:
                    self.counter.Name = "已选择%d个联系人" % len(self.picked)
            return
        if nm == L.BTN_DONE:
            self.set_count("同学", len(self.picked))
            self.picker = None
            self.counter = None
            return
        if nm == L.BTN_CANCEL:
            if self.cancel_ineffective:
                return                      # 演「点了取消但窗口还在」
            self.picker = None
            self.counter = None
            return
        if nm == L.PICKER_CLEAR and self.picker is not None:
            lst = picker_list(self.picker)
            if lst is not None:
                lst._kids = [Node(n, L.PICKER_ROW_CLS, "CheckBoxControl")
                             for n in self.picker.browsed]
            self.picker.searched = ""
            return


# ---------------------------------------------------------------------------
# 被测对象：只把「找窗口 / 抬窗 / 落点归属 / 弹层清单」换成假的
# ---------------------------------------------------------------------------
class FakeUIA:
    def __init__(self, main):
        self._win = main
        self.clicks = []
        self.wheels = []
        self.texts = []
        self.back_called = False
        self.on_type = None
        self.on_wheel = None

    def ensure_window(self):
        return True

    def ensure_materialized(self, timeout=6.0, force=False):
        return True

    def _click_at(self, x, y, right=False):
        self.clicks.append((int(x), int(y), bool(right)))

    def _wheel_at(self, x, y, delta):
        self.wheels.append((int(x), int(y), int(delta)))
        if self.on_wheel:
            self.on_wheel(delta)

    def _set_text(self, ctrl, text):
        self.texts.append(text)
        if self.on_type:
            self.on_type(text)
        return True

    def back_to_chat_tab(self, **kw):
        self.back_called = True
        return True


class Ops(L.LabelOps):
    def __init__(self, sim, db):
        L.LabelOps.__init__(self, None, None, None, 6.0)
        self.sim = sim
        self.uia_obj = FakeUIA(sim.main)
        self.db_obj = db
        self.raised = []
        self.hide_confirm = False
        self.appear_timeout = 6.0
        self.dump_dir = None

    @property
    def uia(self):
        return self.uia_obj

    @property
    def db(self):
        return self.db_obj

    def _top(self, title):
        return self.sim.titled(title)[0]

    def _root(self, title):
        return self.sim.titled(title)

    def _control(self, hwnd):
        return self.sim.control(hwnd)

    def _wait_window(self, title, timeout=None):
        return self._root(title)

    def _wait_class(self, cls, root=None, timeout=None):
        rows = self._rows(root, cls)
        return rows[0] if rows else None

    def _popup_windows(self):
        """弹层：右键菜单 / 确认框。``hide_confirm`` 演「确认框自己先关了」。"""
        p = self.sim.popup
        if p is None:
            return []
        if p[0] == self.sim.CONFIRM and self.hide_confirm:
            return []
        return [(p[0], p[1])]

    def _raise(self, hwnd):
        self.raised.append(hwnd)
        return True

    def _owner_at(self, x, y):
        if self.sim.foreign:
            return 999999
        if self.sim.picker_blocks and self.sim.picker is not None \
                and self.raised and self.raised[-1] == self.sim.MGR:
            return self.sim.PICKER      # owned window 永远压在主人上面
        if self.sim.popup is not None:
            return self.sim.popup[0]    # 弹层出现时本来就在最前（不用抬）
        return self.raised[-1] if self.raised else 0

    def _click(self, hwnd, ctrl, right=False, at=None, raise_it=True):
        r = L.LabelOps._click(self, hwnd, ctrl, right=right, at=at,
                              raise_it=raise_it)
        if r["ok"]:
            self.sim.last_click_point = r["point"]
            self.sim.click(ctrl, right)
        return r


class FakeGUI:
    """假的发送通道：记下每一笔发给谁、带没带 verify。"""

    def __init__(self):
        self.sent = []
        self.fail_for = set()

    def send_msg(self, text, who=None, verify=False):
        from wechatauto.param import WxResponse
        self.sent.append({"text": text, "who": who, "verify": verify})
        if who in self.fail_for:
            return WxResponse.failure("发送失败：多次重试未完成")
        return WxResponse.success("已发送")


class FakeWX:
    """只借 ``SendToLabel`` 这段逻辑，绕开 ``WeChat.__init__``（它会连库、抢窗口）。"""

    SendToLabel = _wxmod.WeChat.SendToLabel

    def __init__(self, ops):
        self.ops = ops
        self.gui = FakeGUI()
        self._gui = self.gui
        self._db = ops.db

    def _labels(self, uia=None):
        return self.ops


class FakeDB:
    def __init__(self, names=("同学", "大人")):
        self.names = list(names)

    def list_labels(self):
        return [{"label_id": i + 1, "name": n, "sort_order": i}
                for i, n in enumerate(self.names)]

    def label_names(self):
        return list(self.names)


class DBMap:
    """显示名 -> [wxid]。``search_contact`` 给多行 = 重名。"""

    def __init__(self, table):
        self.table = table

    def list_labels(self):
        return [{"label_id": i + 1, "name": n, "sort_order": i}
                for i, n in enumerate(self.table)]

    def search_contact(self, keyword):
        return [{"username": u, "nick_name": keyword, "remark": ""}
                for u in self.table.get(keyword, [])]

    def username_by_nickname(self, name):
        hits = self.table.get(name, [])
        return hits[0] if len(hits) == 1 else None


# ---------------------------------------------------------------------------
# 假时钟 / 假节流 / 假键盘 / 假剪贴板
# ---------------------------------------------------------------------------
READS = {"n": 0}
SIMS = []


def _nap(sec):
    """假睡：顺便推进「右栏晚画 / 左栏晚画」的计数，让等待循环真的能等到东西。"""
    READS["n"] += 1
    for w in list(SIMS):
        if w.pool and w.slow_render and READS["n"] >= w.slow_render:
            det = w.first_detail()
            if det is not None and not det._kids:
                w._show_at_page()          # 按当前位置画，不许跳回顶部
        if w.left_pending and READS["n"] >= w.left_late:
            pn = w.panel()
            if pn is not None and not pn.GetChildren():
                pn._kids = w.left_pending
                w.left_pending = []


L.time = types.SimpleNamespace(sleep=_nap, time=__import__("time").time,
                               monotonic=__import__("time").monotonic)
if L._USER32 is None:
    print("非 Windows：取不到 user32，落点归属那几条测不了，本套件不适用")
    sys.exit(2)
L.rhythm.gate = lambda kind="write": (GATES.append(kind), 0.0)[1]
L._press = lambda *vks: EVENTS.append(vks)
_ud.WeChatUIA._clip_set = staticmethod(lambda t: None)


def _set_clip(sim, text):
    sim.clip = text
    EVENTS.append(("clip", text))


def build(labels=(("同学", 63), ("大人", 18)), members=("小明", "小红", "小刚"),
          mgr_open=True, db_names=("同学", "大人"), mgr_row=True, tab=True,
          pick_names=("小明", "小红"), extra_contacts=(), picker=False,
          picker_fails=False, picker_blocks=False, member_pool=None,
          member_start=0, rows_per_notch=None, collapsed=False, left_late=0):
    main = main_window(mgr_row=mgr_row, tab=tab)
    mgr = (mgr_window(labels=labels, members=members, collapsed=collapsed)
           if mgr_open else None)
    pk = picker_window(names=pick_names) if picker else None
    sim = WechatSim(main, mgr=mgr, picker=pk, pick_names=pick_names,
                    extra_contacts=extra_contacts, member_pool=member_pool,
                    picker_fails=picker_fails, picker_blocks=picker_blocks,
                    rows_per_notch=rows_per_notch)
    if left_late:
        # 演真机那个坑：管理窗已经出现了，但左栏还没画（读早了就是「0 个标签」）
        pn = sim.panel()
        sim.left_late = left_late
        sim.left_pending = list(pn.GetChildren())
        pn._kids = []
    if member_start:
        # 演「上一次读把列表留在了中间/底部」：这个窗口不会自己回顶部（实测）
        sim._rowpos = float(member_start)
        sim.page = int(member_start)
        sim._show_at_page()
    o = Ops(sim, FakeDB(db_names))
    o.uia_obj.on_type = sim.type_search
    o.uia_obj.on_wheel = sim.wheel
    SIMS.append(sim)
    READS["n"] = 0
    L._press = sim.press
    _ud.WeChatUIA._clip_set = staticmethod(lambda t: _set_clip(sim, t))
    return o, sim


# ---------------------------------------------------------------------------
# 变异开关：每条都必须让某些判据变红，否则那条判据是摆设
# ---------------------------------------------------------------------------
MUT = next((a for a in sys.argv if a.startswith("--mutate")), None)
if MUT:
    n = MUT[len("--mutate"):]
    if n == "1":
        Ops._owner_at = lambda self, x, y: (self.raised[-1] if self.raised else 1001)
    elif n == "2":
        from wechatauto import uia_driver
        L.LabelOps._find = lambda self, root, names, cls=None, contains=True, \
            exact_first=True: uia_driver._find_by(
                root, lambda c: any(w in (c.Name or "") for w in names), max_depth=40)
    elif n == "3":
        _a = L.LabelOps.add_members
        L.LabelOps.add_members = lambda self, label, members, verify=True: \
            _a(self, label, members, verify=False)
    elif n == "4":
        L.rhythm.gate = lambda kind="write": 0.0
    elif n == "5":
        def add_submit_empty(self, label, members, verify=True):
            op = self.open_manager()
            if not op["ok"]:
                return op
            row = self.find_label(label, op["win"])
            menu, mh = self._open_menu(op["hwnd"], row["cell"], want=L.MENU_ADD)
            item = self._find(menu, (L.MENU_ADD,), contains=False)
            self._click(mh, item, raise_it=False)
            ph, picker = self._root(L.PICKER_TITLE)
            picked, missing = [], []
            for nm in members:
                (picked if self._pick_one(ph, picker, nm) else missing).append(nm)
            done = self._find(picker, (L.BTN_DONE,), contains=False)
            self._click(ph, done)          # 少了「一个都没勾上就点取消」那段
            return L._result(True, "ok", label=label, picked=picked,
                             missing=missing)
        L.LabelOps.add_members = add_submit_empty
    elif n == "6":
        _lm = L.LabelOps.label_members
        L.LabelOps.label_members = lambda self, label, scroll=True, limit=None, \
            max_scrolls=80: _lm(self, label, scroll=False, limit=limit)
    elif n == "7":
        def loose_resolve(self, members):
            resolved, skipped = [], []
            for m in members or []:
                disp = (m.get("display") or "").strip()
                users = self._lookup(disp) or self._lookup((m.get("nick") or ""))
                if users:
                    resolved.append({"display": disp, "username": users[0],
                                     "raw": m.get("raw")})
                else:
                    skipped.append({"display": disp, "reason": "unresolved"})
            return L._result(True, "ok", recipients=resolved, skipped=skipped)
        L.LabelOps.resolve_members = loose_resolve
    elif n == "8":
        _orig_send = _wxmod.WeChat.SendToLabel

        def no_dry(self, text, label, members=None, dry_run=False, limit=None,
                   verify=True):
            return _orig_send(self, text, label, members=members, dry_run=False,
                              limit=limit, verify=verify)
        FakeWX.SendToLabel = no_dry

        def boom(self, text, who=None, verify=False):
            raise AssertionError("dry_run=True 却真的去发了")
        FakeGUI.send_msg = boom
    elif n == "9":
        # 把「微信说 99 人、只读到 7 个」重新报成成功——批量发送会默默漏人
        _lm2 = L.LabelOps.label_members

        def optimistic(self, label, scroll=True, limit=None, max_scrolls=80):
            r = _lm2(self, label, scroll=scroll, limit=limit,
                     max_scrolls=max_scrolls)
            if code(r) == "incomplete":
                r = dict(r, ok=True, reason="bottom", complete=True)
            return r
        L.LabelOps.label_members = optimistic
    elif n == "10":
        # 不清旧搜索词：第二个人翻列表一定翻不到
        L.LabelOps._clear_picker_search = lambda self, ph, picker: None
    elif n == "11":
        # 回到「一把 -600」的写法：真机上列表一动不动（实测）
        def one_big(self, win, notches=6, **kw):
            self.uia._wheel_at(1736, 992, -600)
            return True
        L.LabelOps._scroll_detail = one_big
    elif n == "12":
        # 不倒回顶部：上次读到哪儿就从哪儿开始数（真机就是这么少读 40 个人的）
        L.LabelOps._rewind = lambda self, notches=20, max_rounds=15: 0
    elif n == "13":
        # 用「按名字去重」代替重叠拼接：重名的人直接被吃掉
        def dedupe(acc, page):
            out = list(acc)
            for nm in page:
                if nm not in out:
                    out.append(nm)
            return out, 1
        L.stitch_rows = dedupe
    elif n == "14":
        # 名字一接不上就判没取全（旧规则）：整屏跳过去、其实一行没漏的那种会误报
        def strict(self, label, scroll=True, limit=None, max_scrolls=80):
            r = _lm3(self, label, scroll=scroll, limit=limit,
                     max_scrolls=max_scrolls)
            if r.get("gapped"):
                r = dict(r, ok=False, complete=False,
                         reason="incomplete：有一屏没接上")
            return r
        _lm3 = L.LabelOps.label_members
        L.LabelOps.label_members = strict
    elif n == "15":
        # 干脆不看微信报的人数，滚完就算取全：真漏行时它照样报成功
        def blind(self, label, scroll=True, limit=None, max_scrolls=80):
            r = _lm4(self, label, scroll=scroll, limit=limit,
                     max_scrolls=max_scrolls)
            if code(r) == "incomplete":
                r = dict(r, ok=True, complete=True, reason="bottom")
            return r
        _lm4 = L.LabelOps.label_members
        L.LabelOps.label_members = blind
    elif n == "17":
        # 砍掉「编辑态直接贴名字」那条路：新建时右键弹不出菜单（实测），就只剩失败
        _orig_create = L.LabelOps.create_label

        def menu_only(self, name, verify=True):
            _saved = L.LabelOps._commit_name
            L.LabelOps._commit_name = lambda s2, n2: None
            try:
                return _orig_create(self, name, verify=verify)
            finally:
                L.LabelOps._commit_name = _saved
        L.LabelOps.create_label = menu_only
    elif n == "20":
        # 「窗口出现了」当成「左栏画出来了」：把等渲染那一步跳掉（appear_timeout=0
        # 就是不等）。剩下的都照旧——这样红的只可能是「没等」这一条判据。
        _om = L.LabelOps.open_manager

        def impatient(self, *a2, **k2):
            save = self.appear_timeout
            self.appear_timeout = 0.0
            try:
                return _om(self, *a2, **k2)
            finally:
                self.appear_timeout = save
        L.LabelOps.open_manager = impatient
    elif n == "19":
        # 落点回到「永远正中」：同一个控件每次命中同一个像素，是脚本最明显的特征
        import wechatauto.labels as _L
        _pt = _L.rhythm.point
        _L.rhythm.point = lambda rect, inset=0.15: (
            (int(rect[0]) + int(rect[2])) // 2, (int(rect[1]) + int(rect[3])) // 2)
    elif n == "18":
        # 不展开「标签」分组：折叠态下那一组的行全都不在树里
        L.LabelOps.ensure_labels_open = lambda self, hwnd=None, win=None:             L._result(True, "skipped")
    elif n == "16":
        # 用「滤掉空名」的那一层去看有没有多出一行 = 10-06 真机 no-new-row 那个 bug
        _lc = L.LabelOps._label_cells
        L.LabelOps._label_cells = lambda self, win=None: [
            r for r in _lc(self, win) if r["name"]]
    print("!!! 变异模式:", MUT)


# ---------------------------------------------------------------------------
print("--- A. 进管理窗 ---")
o, sim = build()
r = o.open_manager()
check("A1 管理窗已开着 → 直接复用，一次都不投", (code(r), o.uia_obj.clicks),
      ("ok", []))

o, sim = build(mgr_open=False)
r = o.open_manager()
check("A2 没开着 → 点 tab + 点管理行把窗口点开",
      (code(r), len(o.uia_obj.clicks)), ("ok", 2))
check("A3 第二下点的是通讯录页那一行（Name 是空的，只能按类名找）",
      in_rect(o.uia_obj.clicks[1][:2], 816, 700, 1216, 780), True)

o, sim = build(mgr_open=False, mgr_row=False)
r = o.open_manager()
check("A4 通讯录页没渲染出那一行 → no-page（不是静默 False）", code(r), "no-page")

o, sim = build(mgr_open=False, mgr_row=False, tab=False)
r = o.open_manager()
check("A5 导航栏没有「通讯录」→ no-tab", code(r), "no-tab")

# 真机（用户 10-06 报的）：微信记住「标签」那一组的折叠状态，折叠时那一组的行
# （无标签/各标签/新建标签）**整组都不在 UIA 树里**，只剩分组标题。
o, sim = build(collapsed=True)
r = o.open_manager()
check("A6 分组折叠着 → open_manager 先点一下「标签」分组把它展开",
      (code(r), [(x["name"], x["count"]) for x in o.label_rows(r.get("win"))]),
      ("ok", [("同学", 63), ("大人", 18)]))
check("A6b 展开只点一下，且落在分组标题那一格里",
      (len(o.uia_obj.clicks),
       all(in_rect(c[:2], 816, 672, 1216, 752) for c in o.uia_obj.clicks)),
      (1, True))
o, sim = build()
r = o.open_manager()
check("A7 已经展开时一下都不点（不能把用户展开的状态又点回去）",
      (code(r), o.uia_obj.clicks), ("ok", []))
o, sim = build(collapsed=True)
r = o.create_label("同事")
check("A8 折叠态下新建标签：先展开 → 点新建 → 编辑态贴名字", code(r), "ok")
o, sim = build(collapsed=True)
r = o.list_labels(prefer="ui")
check("A9 折叠态下读标签列表也不能报「一个标签都没有」",
      (code(r), sorted(r["names"])), ("ui", ["同学", "大人"]))

print("--- B. 落点归属（用户别的窗口盖住微信时不能盲投）---")
o, sim = build(mgr_open=False)
sim.foreign = True
r = o.open_manager()
check("B1 落点被别的窗口盖住 → occluded", code(r), "occluded")
check("B2 而且真的一个点都没投出去", o.uia_obj.clicks, [])

print("--- C. 读标签行 ---")
o, sim = build()
check("C1 解析出名字和人数（顺序=界面顺序）",
      [(x["name"], x["count"]) for x in o.label_rows(sim.mgr)],
      [("同学", 63), ("大人", 18)])
check("C2 「无标签」不当标签用",
      any(x["name"] == "无标签" for x in o.label_rows(sim.mgr)), False)
check("C3 find_label 精确命中", o.find_label("同学", sim.mgr)["count"], 63)
check("C4 找不存在的返回 None", o.find_label("同事", sim.mgr), None)
check("C5 list_labels 默认读库（不碰界面）", code(o.list_labels()), "db")

o, sim = build(left_late=3)          # 管理窗出现了，左栏还没画
r = o.open_manager()
check("C5b 左栏晚画 → 等到它画出来，而不是报「一个标签都没有」",
      (code(r), sorted(x["name"] for x in o.label_rows(r.get("win")))),
      ("ok", ["同学", "大人"]))
o, sim = build(left_late=3)
r = o.list_labels(prefer="ui")
check("C5c 同样情况下读标签列表也不能交空表当成功",
      (code(r), sorted(r["names"])), ("ui", ["同学", "大人"]))

print("--- D. 建标签 / 改名 ---")
o, sim = build()
r = o.create_label("同学")
check("D1 已存在 → already-there，零点击", (code(r), o.uia_obj.clicks),
      ("already-there", []))
EVENTS.clear()
o, sim = build(labels=(("同学", 63),), db_names=("同学",))
r = o.create_label("同事")
check("D2 新建 → 多出一行 → 改名 → 界面上查得到「同事」", code(r), "ok")
check("D2b 新行本来就在编辑态：名字直接贴进去回车，不绕右键菜单",
      r.get("via"), "editing")
check("D3 改名走「剪贴板 + Ctrl+V + 回车」（实测行内编辑框不进 UIA）",
      (("clip", "同事") in EVENTS and (L.VK_CONTROL, L.VK_V) in EVENTS
       and (L.VK_RETURN,) in EVENTS), True)
check("D4 新建这条路一次右键都不做（实测：编辑态下右键弹不出菜单）",
      any(c[2] for c in o.uia_obj.clicks), False)
check("D5 界面上那行真的改成了新名字", sim.find_row("同事") is not None, True)

EVENTS.clear()
o, sim = build(labels=(("同学", 63),), db_names=("同学",))
r = o.rename_label("同学", "老同学")
check("D5b 改已有标签的名字：右键 →「修改标签名」→ 贴 → 回车",
      (code(r), sim.find_row("老同学") is not None, sim.find_row("同学") is None),
      ("ok", True, True))
check("D5c 这条路确实点了右键", any(c[2] for c in o.uia_obj.clicks), True)
check("D5d 人数跟着名字走，没被改名改没",
      o.find_label("老同学", sim.mgr)["count"], 63)
o, sim = build(labels=(("同学", 63),), db_names=("同学",))
check("D5e 没这个标签就如实报，不去动别的行",
      (code(o.rename_label("没有的", "新名")), sim.find_row("新名") is None),
      ("no-label", True))

o, sim = build(labels=(("同学", 63),), db_names=("同学",))
sim.click = lambda ctrl, right=False: None       # 微信一点反应都没有
r = o.create_label("同事")
check("D6 点了「新建标签」但列表没多出行 → no-new-row", code(r), "no-new-row")

o, sim = build(labels=(("同学", 63),), db_names=("同学",))
L._press = lambda *vk: EVENTS.append(vk)         # 回车不生效 = 改名没落地
r = o.create_label("同事")
check("D7 改名没落到界面上 → not-renamed（说清留了个未命名）",
      (code(r), "未命名" in r["reason"]), ("not-renamed", True))
check("D7b 刚建出来那一行名字是空的（实测 Name='(0)'），空名行也算「多出来的一行」",
      any((c["name"] == "" and c["count"] == 0) for c in o._label_cells()), True)

print("--- E. 删标签 ---")
o, sim = build()
_eaten = []
_orig_click = sim.click


def eat(ctrl, right=False):
    _eaten.append(ctrl)
    _orig_click(ctrl, right)


sim.click = eat
r = o.delete_label("同学")
check("E1 菜单「删除标签」→ 确认框「删除」→ 行真消失了才算 ok", code(r), "ok")
check("E2 点的是按钮「删除」，不是标题文字「删除标签」"
      "（_find_by 倒序遍历兄弟节点，包含匹配会先撞上标题）",
      [(c.Name, c.ControlTypeName) for c in _eaten if "删除" in (c.Name or "")],
      [(L.MENU_DELETE, "MenuItemControl"),
       (L.BTN_CONFIRM_DELETE, "ButtonControl")])
check("E3 确认框是从独立小窗里找的（点完就关掉了）", sim.popup, None)

o, sim = build()
sim.click = lambda ctrl, right=False: None
r = o.delete_label("同学")
check("E4 右键没弹出菜单 → no-menu", code(r), "no-menu")

o, sim = build()
o.hide_confirm = True        # 确认框在我们点它之前就自己关掉了
r = o.delete_label("同学")
check("E5 确认框没点到 → still-there，不报成功（菜单确实弹过了）",
      (code(r), sim.confirm_shown), ("still-there", True))

print("--- F. 批量加成员 ---")
o, sim = build(labels=(("同学", 63),), db_names=("同学",))
r = o.add_members("同学", ["小明", "小红"])
check("F1 勾两个人 → 完成", (code(r), sorted(r["picked"])), ("ok", ["小明", "小红"]))
check("F2 读的是选择框里的「已选择2个联系人」", r.get("selected_in_picker"), 2)
check("F3 完成后标签行人数 63 → 65（真回读）",
      (r.get("count_before"), r.get("count_after")), (63, 65))
check("F4 没有漏掉的人", r["missing"], [])

o, sim = build(labels=(("同学", 63),), db_names=("同学",),
               pick_names=("别人", "还有别人"))
r = o.add_members("同学", ["小明"])
check("F5 列表里没这个人 → no-member", code(r), "no-member")
check("F6 标签人数一点没变（没把空选择提交上去）",
      o.find_label("同学", sim.mgr)["count"], 63)
check("F7 提交走的是「取消」：选择框已关掉", sim.picker, None)

o, sim = build(labels=(("同学", 63),), db_names=("同学",))
r = o.add_members("同学", ["小明", "查无此人"])
check("F8 部分命中：picked / missing 分开报",
      (sorted(r["picked"]), r["missing"], r["count_after"]),
      (["小明"], ["查无此人"], 64))

o, sim = build(labels=(("同学", 63),), db_names=("同学",), picker_fails=True)
r = o.add_members("同学", ["小明"])
check("F9 菜单点了但选择框没弹出来 → no-picker", code(r), "no-picker")

o, sim = build()
r = o.add_members("同学", [])
check("F10 空成员列表 → bad-members，零点击", (code(r), o.uia_obj.clicks),
      ("bad-members", []))

o, sim = build(labels=(("同学", 63),), db_names=())
r = o.add_members("同学", ["小明"])
check("F11 标签不存在时先建再加人", code(r), "ok")

o, sim = build(labels=(("同学", 63),), db_names=("同学",),
               pick_names=("小明",), extra_contacts=("小红", "小刚"))
r = o.add_members("同学", ["小红", "小刚"])
check("F12 翻列表翻不到 → 走搜索，认得 SearchContactCellView 这个类名",
      (code(r), sorted(r["picked"]), r["count_after"]),
      ("ok", ["小刚", "小红"], 65))
check("F13 搜索确实打了字",
      "小红" in o.uia_obj.texts and "小刚" in o.uia_obj.texts, True)
check("F14 换下一个人的时候先清掉上一次的搜索词",
      sum(1 for t in o.uia_obj.texts if t == ""), 1)

print("--- G. 批量移成员 ---")
o, sim = build(labels=(("同学", 63),), db_names=("同学",))
r = o.remove_members("同学", ["小明", "小红"])
check("G1 勾复选框 → 底部「移出标签」→ 人数 63 → 61",
      (code(r), r.get("count_before"), r.get("count_after")), ("ok", 63, 61))
check("G2 两个落点均命中实际选择圆，而非头像或行中心",
      sim.member_click_points, [(848, 724), (848, 772)])
check("G3 移走的人不再出现在成员列表里",
      [(c.Name or "").split("  ")[0] for c in sim.first_detail().GetChildren()],
      ["小刚"])

o, sim = build(labels=(("同学", 63),), db_names=("同学",))
cell = sim.first_detail().GetChildren()[0]
o._click(sim.MGR, cell, at=(886, 724))
check("G3b 旧落点只打开资料，不选中成员或生成操作栏",
      (sim.profile_open, sim.checked, sim.bar), (True, [], None))

o, sim = build(labels=(("同学", 63),), db_names=("同学",))
r = o.remove_members("同学", ["查无此人"])
check("G4 成员不在可视区 → no-member + missing", (code(r), r["missing"]),
      ("no-member", ["查无此人"]))

o, sim = build(labels=(("同学", 63),), db_names=("同学",))
_orig = sim.click


def no_uncheck(ctrl, right=False):
    if (ctrl.Name or "").strip() == L.BTN_REMOVE_MEMBER:
        return
    _orig(ctrl, right)


sim.click = no_uncheck
r = o.remove_members("同学", ["小明"])
check("G5 点了移出但人数没变 → count-unchanged，不报成功",
      code(r), "count-unchanged")

o, sim = build()
r = o.remove_members("没这个标签", ["小明"])
check("G6 标签不存在 → no-label", code(r), "no-label")

o, sim = build(picker=True, picker_blocks=True)
r = o.open_manager()
check("G7 残留的选择框压在管理窗上 → 先取消它，后面才点得动",
      (code(r), sim.picker), ("ok", None))
r2 = o.remove_members("同学", ["小明"])
check("G7b 关掉残留框之后「移出成员」正常走通",
      (code(r2), r2.get("count_after")), ("ok", 62))

o, sim = build(picker=True, picker_blocks=True)
sim.cancel_ineffective = True
r = o.remove_members("同学", ["小明"])
check("G8 取消不掉时如实报 occluded（除清理那一下，管理窗里一个点都没投）",
      (code(r), len(o.uia_obj.clicks)), ("occluded", 1))

print("--- I. 成员全量（右侧列表是虚拟化的）---")
POOL7 = [detail_cell("同学%d" % i) for i in range(7)]
o, sim = build(members=(), member_pool=POOL7, labels=(("同学", 7),),
               db_names=("同学",))
r = o.label_members("同学")
check("I1 一屏只画 5 行 → 靠滚动取到全部 7 人",
      (code(r), len(r["members"]), r["complete"]), ("bottom", 7, True))
check("I2 拼接出来的顺序就是列表从上到下（不是集合）",
      [m["display"] for m in r["members"]], ["同学%d" % i for i in range(7)])
check("I3 每格只给 120（实测一把 -600 会被列表吞掉）",
      sorted({abs(w[2]) for w in o.uia_obj.wheels}), [120])
check("I3b 列表真的挪动了（顶上那条不再是第 0 条）", sim.page > 0, True)

o, sim = build(members=(), member_pool=POOL7, labels=(("同学", 99),),
               db_names=("同学",))
r = o.label_members("同学")
check("I4 微信说 99 人只读到 7 个 → complete=False（不假装取全）",
      (r["count"], len(r["members"]), r["complete"]), (99, 7, False))
check("I4b 取不全是失败状态，不是「成功」", r["ok"], False)

o, sim = build(members=(), member_pool=POOL7, labels=(("同学", 7),),
               db_names=("同学",))
r = o.label_members("同学", limit=4)
check("I5 limit 生效并如实报 limit", (code(r), len(r["members"])), ("limit", 4))

o, sim = build(members=(), member_pool=[], labels=(("同学", 0),), db_names=("同学",))
r = o.label_members("同学")
check("I6 标签里 0 个人 → 如实报 0 且 complete=True",
      (code(r), len(r["members"]), r["complete"]), ("bottom", 0, True))

o, sim = build(members=(), member_pool=POOL7, labels=(("同学", 7),),
               db_names=("同学",))
sim.mgr._kids = [sim.mgr._kids[0]]        # 把成员列表整个容器拿掉
r = o.label_members("同学")
check("I7 根本没有成员列表 → no-list（不是静默空成功）", code(r), "no-list")

o, sim = build(members=(), member_pool=POOL7, labels=(("同学", 7),),
               db_names=("同学",))
sim.pool = [detail_cell("别人%d" % i) for i in range(7)]
sim.slow_render = 4                 # 右栏比左栏晚画：假时钟睡够 4 次才出内容
r = o.label_members("同学")
check("I8 右栏比左栏晚画 → 等到它，不误报「没成员」",
      (code(r), len(r["members"])), ("bottom", 7))

o, sim = build(members=(), member_pool=POOL7, labels=(("同学", 7),),
               db_names=("同学",))
sim.pool = [detail_cell("路人甲", label="大人")]
r = o.label_members("同学")
check("I9 右侧其实没切到这个标签 → not-selected（不把「全部」当「同学」念出来）",
      code(r), "not-selected")

o, sim = build(members=(), member_pool=POOL7, labels=(("同学", 7),),
               db_names=("同学",), member_start=6)
r = o.label_members("同学")
check("I10 列表停在上一次读到的位置（实测：再点标签行不会回顶）→ 先倒回顶部再数",
      (code(r), len(r["members"]), r["complete"]), ("bottom", 7, True))
check("I10b 真的倒过（往上滚的轮数记在 rewinds 里）",
      (r["rewinds"] >= 1, any(w[2] > 0 for w in o.uia_obj.wheels)), (True, True))

DUP = [detail_cell("同学甲"), detail_cell("同学甲"), detail_cell("同学乙"),
       detail_cell("同学乙"), detail_cell("同学丙"), detail_cell("同学丁"),
       detail_cell("同学戊")]
o, sim = build(members=(), member_pool=DUP, labels=(("同学", 7),),
               db_names=("同学",))
r = o.label_members("同学")
check("I11 重名的两个人不能被「按名字去重」吃掉（真机 63 人只剩 54 行就是这么来的）",
      (code(r), len(r["members"]), r["complete"]), ("bottom", 7, True))
check("I11b 数出来的是 7 行、不是 5 个不同名字",
      len({m["display"] for m in r["members"]}), 5)

# 每轮正好跳一整屏：两屏之间一个名字都接不上（gapped），但**一行都没漏**。
o, sim = build(members=(), member_pool=POOL7, labels=(("同学", 7),),
               db_names=("同学",), rows_per_notch=5 / 3.0)
r = o.label_members("同学")
check("I12 整屏跳、名字接不上 ≠ 漏人：人数对得上就算取全",
      (code(r), len(r["members"]), r["complete"], r["gapped"]),
      ("bottom", 7, True, True))

# 每轮跳 2 屏：真的会漏掉中间那几行 → 数出来的一定比微信说的少。
o, sim = build(members=(), member_pool=POOL7, labels=(("同学", 7),),
               db_names=("同学",), rows_per_notch=10 / 3.0)
r = o.label_members("同学")
check("I13 真漏了行 → 数出来的一定对不上微信的人数，报 incomplete",
      (code(r), r["complete"], r["ok"]), ("incomplete", False, False))

print("--- J. 成员名拆解与收件人解析 ---")
check("J1 昵称 + 空备注 + 标签", L.parse_detail_name("阿凡买买提  大人"),
      {"raw": "阿凡买买提  大人", "nick": "阿凡买买提", "remark": "",
       "labels": "大人", "display": "阿凡买买提", "same_label": False})
_j2 = L.parse_detail_name("暗影精灵S 杨君瑞-17班 ")
check("J2 昵称 + 备注 + 空标签（显示名取备注）", (_j2["nick"], _j2["display"]),
      ("暗影精灵S", "杨君瑞-17班"))
check("J3 只有昵称", L.parse_detail_name("安阳  ")["nick"], "安阳")
check("J4 在本标签视角里同标签的人标出来",
      L.parse_detail_name("Ayi  同学", "同学")["same_label"], True)

POOL3 = [detail_cell("小明"), detail_cell("重名"), detail_cell("查无此人")]
o, sim = build(members=(), member_pool=POOL3, labels=(("同学", 3),),
               db_names=("同学",))
o.db_obj = DBMap({"小明": ["wxid_ming"], "重名": ["a", "b"]})
res = o.resolve_members([L.parse_detail_name((c.Name or ""), "同学") for c in POOL3])
check("J5 只有唯一命中的人进 recipients",
      [x["display"] for x in res["recipients"]], ["小明"])
check("J6 重名/查不到的分开报（重名绝不能随便挑一个）",
      sorted((x["display"], x["reason"]) for x in res["skipped"]),
      [("查无此人", "unresolved"), ("重名", "ambiguous")])

print("--- K. 按标签批量发送 ---")
o, sim = build(members=(), member_pool=POOL3, labels=(("同学", 3),),
               db_names=("同学",))
o.db_obj = DBMap({"小明": ["wxid_ming"], "重名": ["a", "b"]})
wx = FakeWX(o)
r = wx.SendToLabel("周五校庆放假", "同学", dry_run=True)
check("K1 预演一笔都不发", (r["status"], len(wx.gui.sent)), ("成功", 0))
check("K2 预演把可发和跳过的人都列出来",
      ([x["display"] for x in r["data"]["recipients"]],
       [x["display"] for x in r["data"]["skipped"]]),
      (["小明"], ["重名", "查无此人"]))

r = wx.SendToLabel("周五校庆放假", "同学")
check("K3 只发给唯一命中的人", [s0["who"] for s0 in wx.gui.sent], ["小明"])
check("K4 每一笔都带 verify（发完回读数据库）",
      all(s0["verify"] for s0 in wx.gui.sent), True)
check("K5 结果分 sent/failed/skipped 三堆",
      (r["data"]["sent"][0]["display"], r["data"]["failed"],
       len(r["data"]["skipped"])), ("小明", [], 2))

wx.gui.fail_for = {"小明"}
r = wx.SendToLabel("周五校庆放假", "同学")
check("K6 全失败时状态是「失败」，不含糊报成功",
      (r["status"], r["data"]["sent"], r["data"]["failed"][0]["reason"]),
      ("失败", [], "发送失败：多次重试未完成"))

wx.gui.fail_for = set()
n0 = len(wx.gui.sent)
r = wx.SendToLabel("周五校庆放假", "同学", members=["查无此人"])
check("K7 指定的成员都不在该标签里 → 直接失败，一笔都不发",
      (r["status"], len(wx.gui.sent) - n0), ("失败", 0))
check("K8 空正文直接拒绝", wx.SendToLabel("", "同学")["status"], "失败")
n1 = len(wx.gui.sent)
wx.SendToLabel("周五校庆放假", "同学", limit=1)
check("K9 limit=1 只发一个人（试水）", len(wx.gui.sent) - n1, 1)

print("--- H. 节流与收尾 ---")
GATES.clear()
o, sim = build(labels=(("同学", 63),), db_names=("同学",))
o.add_members("同学", ["小明"])
o.delete_label("同学")
check("H1 每个对外写动作前都过了一次 rhythm.gate", GATES, ["label", "label"])
o, sim = build()
check("H2 收尾能带回聊天页", o.back_to_chat(), True)

print("--- L. 落点不能永远正中同一个像素 ---")
o, sim = build()
row = first(sim.main, lambda c: L.MAIN_MGR_ROW_CLS in (c.ClassName or ""))
pts = [o._click(sim.MAIN, row)["point"] for _ in range(40)]
check("L1 同一个控件连点 40 下，落点不止一个像素（rhythm.point 真接上了）",
      len(set(pts)) > 5, True)
check("L1b 而且没有一下落在正中（正中=脚本最好认的特征）",
      (1016, 740) in pts, False)
check("L2 每个落点都还在这一行的矩形里（归属校验照旧成立）",
      all(in_rect(p, 816, 700, 1216, 780) for p in pts), True)
o, sim = build()
cell = first(sim.mgr, lambda c: L.LABEL_ROW_CLS in (c.ClassName or "")
             and (c.Name or "").startswith("同学"))
check("L3 调用者明确给出的窄目标坐标不随机偏移",
      o._click(sim.MGR, cell, at=(840, 850))["point"], (840, 850))

print()
print("通过 %d，失败 %d" % (len(PASSED), len(FAILED)))
if MUT:
    print("（变异模式：期望上面有 FAIL）")
    sys.exit(0 if FAILED else 1)
if FAILED:
    for f in FAILED:
        print("  FAIL:", f)
sys.exit(1 if FAILED else 0)
