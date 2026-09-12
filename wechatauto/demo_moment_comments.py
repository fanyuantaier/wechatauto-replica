"""演示：定位指定朋友圈并读取其全部可见评论（含回复）。

用法：
    python -m wechatauto.demo_moment_comments --author 醒醒大王
    python -m wechatauto.demo_moment_comments --keyword 故事 --as-tree

流程：find_moment 定位 -> 读取该条可见 cell 的评论文本并解析。
这是**只读**操作，不会点赞/评论/改动任何数据。
"""
from __future__ import annotations

import argparse
import json
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
    ap = argparse.ArgumentParser(description='读取指定朋友圈的全部评论')
    ap.add_argument('--author', default=None, help='发布者昵称')
    ap.add_argument('--keyword', default=None, help='正文关键词')
    ap.add_argument('--as-tree', action='store_true', help='额外输出回复嵌套树')
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

    resp = m.GetComments(publisher=args.author, keyword=args.keyword,
                         db=db, max_screens=args.max_screens,
                         as_tree=args.as_tree)
    if not resp.is_success:
        print('结果:', resp.is_success, resp['message'])
        return 1

    d = resp['data']
    print('结果: True', resp['message'])
    print('发布者:', d['publisher'])
    print('正文:', d['content'])
    print('时间:', d['time'])
    print('点赞:', ', '.join(d['likes']) if d['likes'] else '(无)')
    print('评论数:', d['comment_count'])
    for i, c in enumerate(d['comments'], 1):
        reply = f" 回复 {c['reply_to']}" if c.get('reply_to') else ''
        print(f"  {i}. {c['author']}{reply}: {c['content']}")
    if args.as_tree and d.get('tree'):
        print('--- 回复树 ---')
        print(json.dumps(d['tree'], ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
