"""诊断：定位指定朋友圈后 dump 该 cell 的完整 UIA 子树，排查评论/点赞归属控件。

用法：
    python -m wechatauto.demo_moment_dump --author 醒醒大王
    python -m wechatauto.demo_moment_dump --keyword 故事
"""
from __future__ import annotations

import argparse
import sys


def _setup_stdout():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


_setup_stdout()

from wechatauto import WeChat
from wechatauto.logger import wxlog

MAX_DEPTH = 8


def dump(ctrl, depth, maxd=MAX_DEPTH):
    if depth > maxd:
        return
    try:
        ctype = ctrl.ControlTypeName or '?'
    except Exception:
        ctype = '?'
    try:
        name = (ctrl.Name or '')[:60]
    except Exception:
        name = ''
    try:
        br = ctrl.BoundingRectangle
        box = f"[{br.left},{br.top},{br.right},{br.bottom}]"
    except Exception:
        box = ""
    print('  ' * depth + f"{ctype} {box} Name={name!r}")
    if depth >= maxd:
        return
    try:
        kids = ctrl.GetChildren()
    except Exception:
        kids = []
    for k in kids:
        dump(k, depth + 1, maxd)


def main():
    ap = argparse.ArgumentParser(description='dump 指定朋友圈 cell 的 UIA 子树')
    ap.add_argument('--author', default=None)
    ap.add_argument('--keyword', default=None)
    ap.add_argument('--max-screens', type=int, default=300)
    ap.add_argument('--depth', type=int, default=6)
    ap.add_argument('--debug', action='store_true')
    args = ap.parse_args()
    if args.debug:
        wxlog.set_debug(True)
    if not args.author and not args.keyword:
        print('请至少提供 --author 或 --keyword')
        return 1

    wx = WeChat()
    m = wx.Moment
    item = m.find_moment(publisher=args.author, keyword=args.keyword,
                         max_screens=args.max_screens)
    if item is None:
        print('未能定位到目标朋友圈')
        return 1
    full = m._scroll_item_fully_visible(
        publisher=args.author, keyword=args.keyword, max_retry=10)
    ctrl = (full if full is not None else item).control
    print('=== dump cell UIA 子树 (depth<=%d) ===' % args.depth)
    dump(ctrl, 0, args.depth)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
