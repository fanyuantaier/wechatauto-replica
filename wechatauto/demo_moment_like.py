"""演示：一键点赞指定朋友圈。

用法：
    python -m wechatauto.demo_moment_like --author 醒醒大王
    python -m wechatauto.demo_moment_like --keyword 故事

流程：find_moment 定位 -> 点击 "…" -> 浮层内点「赞」。
注意：这是**真实点赞**，会真的在你的账号上给那条朋友圈点赞！
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
    ap = argparse.ArgumentParser(description='一键点赞指定朋友圈')
    ap.add_argument('--author', default="兔仔仔", help='发布者昵称')
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

    resp = m.LikeMoment(publisher=args.author, keyword=args.keyword,
                        db=db, max_screens=args.max_screens)
    print('结果:', resp.is_success, resp['message'])
    return 0 if resp.is_success else 1


if __name__ == '__main__':
    raise SystemExit(main())
