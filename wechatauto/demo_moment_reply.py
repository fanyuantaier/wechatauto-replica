"""演示：回复指定朋友圈的某条评论。

用法：
    python -m wechatauto.demo_moment_reply --author 醒醒大王 --reply-to 豆芽 --text 说得真好
    python -m wechatauto.demo_moment_reply --keyword 故事 --reply-to 豆芽 --text 哈哈 --target 官宣

流程：find_moment 定位 -> 滚动至完整可见 -> 截图 OCR「评论区」定位目标评论行
      -> 点击该行（打开回复框）-> 输入内容 -> 点「发送」。

注意：
- 评论行为纯自绘，靠截图 + 内置 OCR 定位；需朋友圈页面已打开、目标朋友圈在视野内。
- 会真实点击目标评论并输入回复文字；「发送」按钮走模板识别（moments_send.png）。
- --target 为被回复评论的正文关键词，用于同作者多条评论时消歧（可选）。
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
    ap = argparse.ArgumentParser(description='回复指定朋友圈的某条评论')
    ap.add_argument('--author', default=None, help='发布者昵称')
    ap.add_argument('--keyword', default="测试", help='正文关键词')
    ap.add_argument('--reply-to', required=True, help='被回复评论的作者昵称')
    ap.add_argument('--text', required=True, help='回复内容')
    ap.add_argument('--target', default=None, help='被回复评论正文关键词（可选）')
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

    resp = m.ReplyCommentMoment(publisher=args.author, keyword=args.keyword,
                                reply_to=args.reply_to, content=args.text,
                                target_text=args.target, db=db,
                                max_screens=args.max_screens)
    print('结果:', resp.is_success, resp['message'])
    return 0 if resp.is_success else 1


if __name__ == '__main__':
    raise SystemExit(main())
