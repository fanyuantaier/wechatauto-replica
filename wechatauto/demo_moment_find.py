"""演示：滚动定位指定朋友圈（作者昵称 / 正文关键词）。

用法：
    python -m wechatauto.demo_moment_find --author 醒醒大王
    python -m wechatauto.demo_moment_find --keyword 故事
    python -m wechatauto.demo_moment_find --author 兔仔仔 --keyword 测试

流程：先从顶部进入朋友圈，用 UIA 边滚动边匹配；若提供 MoodDB 一并做快速判断。
只滚动与读取，不点赞/评论。
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
    ap = argparse.ArgumentParser(description='滚动定位指定朋友圈')
    ap.add_argument('--author', default=None, help='发布者昵称')
    ap.add_argument('--keyword', default=None, help='正文关键词')
    ap.add_argument('--max-screens', type=int, default=120, help='最大滚动迭代数')
    ap.add_argument('--no-db', action='store_true', help='不使用数据库标尺')
    ap.add_argument('--debug', action='store_true', help='开启 debug 日志')
    args = ap.parse_args()

    if args.debug:
        wxlog.set_debug(True)

    if not args.author and not args.keyword:
        print('请至少提供 --author 或 --keyword')
        return 1

    wx = WeChat()
    m = wx.Moment

    db = None
    db_posts = []
    if not args.no_db:
        try:
            from wechatauto import WeChatDB, MomentDB
            db = MomentDB(WeChatDB())
            db_posts = db.get_moments(limit=0)
            print(f'已初始化 MomentDB，共 {len(db_posts)} 条')
        except Exception as e:
            print('DB 初始化失败（将仅用 UIA）：', e)
            db = None

    # 打印目标在数据库的索引，便于核对
    if db_posts:
        tidx = None
        for i, f in enumerate(db_posts):
            if args.author and f.get('nickname') == args.author:
                tidx = i
                break
            if args.keyword and args.keyword in f.get('text', ''):
                tidx = i
                break
        print(f'目标在数据库索引(最新在前,0=顶): {tidx}')

    print(f'正在定位：author={args.author!r} keyword={args.keyword!r}')
    item = m.find_moment(publisher=args.author, keyword=args.keyword,
                         db=db, max_screens=args.max_screens)
    if item is None:
        print('>> 未能定位到目标朋友圈 <<')
        return 1

    rect = item.control.BoundingRectangle
    print('>> 定位成功 <<')
    print('   Author :', item.publisher)
    print('   Text   :', (item.text or '')[:60])
    print('   Time   :', item.timestamp)
    print('   Rect   :', (rect.left, rect.top, rect.right, rect.bottom))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
