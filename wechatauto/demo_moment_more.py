"""演示：识别并点击目标朋友圈的 "…" 按钮（未识别到则继续微调滚动）。

用法：
    python -m wechatauto.demo_moment_more --author 醒醒大王
    python -m wechatauto.demo_moment_more --keyword 故事

流程：find_moment 定位目标 -> _locate_more_click 用模板匹配识别 "…" 并点击。
注意：会真实点击 "…" 弹出浮层（可能出现浮层一闪），但**不会**点赞/评论。
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


def main():
    ap = argparse.ArgumentParser(description='识别并点击 "…" 按钮')
    ap.add_argument('--author', default=None, help='发布者昵称')
    ap.add_argument('--keyword', default=None, help='正文关键词')
    ap.add_argument('--max-screens', type=int, default=300)
    ap.add_argument('--debug', action='store_true')
    args = ap.parse_args()

    if args.debug:
        wxlog.set_debug(True)
    if not args.author and not args.keyword:
        print('请至少提供 --author 或 --keyword')
        return 1

    wx = WeChat()
    m = wx.Moment
    db = None
    try:
        from wechatauto import WeChatDB, MomentDB
        db = MomentDB(WeChatDB())
    except Exception as e:
        print('DB 初始化失败（仅用 UIA）：', e)

    print(f'定位：author={args.author!r} keyword={args.keyword!r}')
    item = m.find_moment(publisher=args.author, keyword=args.keyword,
                         db=db, max_screens=args.max_screens)
    if item is None:
        print('>> 未能定位到目标朋友圈 <<')
        return 1
    print('>> 定位成功 <<  Author:', item.publisher, '| Text:', (item.text or '')[:30])

    ok = m._locate_more_click(item, max_retry=8)
    if ok:
        print('>> 已识别并点击 "…" 按钮（浮层应弹出）<<')
    else:
        print('>> 未能识别/点击 "…" 按钮 <<')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
