"""诊断：定位微信朋友圈 UIA 树中 `Name=='评论区'` 的 ListItem 及其评论行。

Accessibility Insights 显示朋友圈时间线为 mmui::SNSWindow -> ... ->
List('朋友圈')，其下每个 ListItem 即一条记录，其中：
  - 作者 cell（如 '诡影藏锋 2小时前'）
  - '评论区' ListItem（整片评论区的容器）
  - 各评论 ListItem（如 '豆芽 仅仅只是正在去学校的路...'）

本脚本从 mmui::SNSWindow（或其顶层代理窗口）递归扫描：
  1. 抓出所有 Name=='评论区' 的 ListItem，打印 BoundingRectangle；
  2. 打印其父 List 下的全部兄弟 ListItem（作者行 + 评论行）的 Name/BoundingRectangle，
     供确认评论行是否可直接用 UIA（而非截图 OCR）拿到底。

用法：
    python -m wechatauto.demo_moment_cells
"""
from __future__ import annotations

import argparse
import sys
import time


def _setup_stdout():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


_setup_stdout()

from wechatauto.logger import wxlog
from wechatauto.utils.tools import find_all_windows_from_root


def _box(ctrl):
    try:
        br = ctrl.BoundingRectangle
        return (br.left, br.top, br.right, br.bottom)
    except Exception:
        return None


def _children(ctrl):
    try:
        return ctrl.GetChildren()
    except Exception:
        return []


def _is_list_item(ctrl):
    try:
        t = ctrl.ControlTypeName or ''
    except Exception:
        t = ''
    return 'List' in t and 'Item' in t


def _name(ctrl):
    try:
        return (ctrl.Name or '').strip()
    except Exception:
        return ''


def walk(root, comment_cells, seen):
    if root in seen:
        return
    seen.add(root)
    nm = _name(root)
    if nm == '评论区':
        comment_cells.append(root)
    for k in _children(root):
        walk(k, comment_cells, seen)


def print_row(tag, ctrl):
    box = _box(ctrl)
    nm = _name(ctrl)
    print(f"  {tag}: Name={nm[:60]!r} box={box}")


def dump_subtree(ctrl, depth, maxd=5):
    if depth > maxd:
        return
    try:
        ctype = ctrl.ControlTypeName or '?'
    except Exception:
        ctype = '?'
    try:
        cls = getattr(ctrl, 'ClassName', '') or ''
    except Exception:
        cls = ''
    print('  ' * depth + f"{ctype} cls={cls!r} box={_box(ctrl)} Name={_name(ctrl)[:40]!r}")
    for k in _children(ctrl):
        dump_subtree(k, depth + 1, maxd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--debug', action='store_true')
    args = ap.parse_args()
    if args.debug:
        wxlog.set_debug(True)

    roots = []
    try:
        roots += find_all_windows_from_root(uiaclsname='mmui::SNSWindow')
    except Exception as e:
        print('SNSWindow 查找异常:', e)

    if not roots:
        # 兜底：列出所有顶层窗口，稍后从主窗口也扫一遍
        try:
            roots += find_all_windows_from_root()
        except Exception as e:
            print('顶层窗口查找异常:', e)

    print(f'候选根窗口数: {len(roots)}')
    comment_cells = []
    for root in roots:
        walk(root, comment_cells, set())

    print(f'==== 共找到 {len(comment_cells)} 个 「评论区」 ListItem ====')
    for i, cell in enumerate(comment_cells):
        box = _box(cell)
        print(f'\n[评论区 #{i}] box={box}')
        # 找父 List：向上取父节点
        parent = None
        try:
            parent = cell.GetParentControl()
        except Exception:
            parent = None
        if parent is None:
            try:
                parent = cell.GetParent()
            except Exception:
                parent = None
        # 打印父级下所有兄弟 ListItem（作者/评论行）
        if parent is not None:
            print(f'  父级 List 下所有 ListItem:')
            for sib in _children(parent):
                if _is_list_item(sib):
                    print_row('item', sib)
        try:
            cell.Click()
        except Exception:
            pass
        time.sleep(0.5)
        print(f'  --- 展开后 «评论区» cell 的子树:')
        dump_subtree(cell, 1)

    # 截图 + OCR 最高的「评论区」可视区域，验证评论行能否被 OCR 读出
    tall = None
    for cell in comment_cells:
        b = _box(cell)
        if b is None:
            continue
        if tall is None or (b[3] - b[1]) > (tall[3] - tall[1]):
            tall = b
    if tall is None:
        print('\n未找到可用的「评论区」区域')
        return 0
    print(f'\n==== 截图并 OCR 最高的评论区 box={tall} ====')
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(bbox=tall)
    except Exception as e:
        print('截图失败:', e)
        return 0
    from wechatauto.guia import ScreenOCR
    lines = ScreenOCR.recognize(img)
    print(f'OCR 共识别 {len(lines)} 行（图像内坐标，需加 box 偏移后为屏幕绝对坐标）:')
    for text, x, y, w, h in lines:
        print(f'  ({x:5},{y:5},{w:5},{h:5})  {text[:60]!r}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
