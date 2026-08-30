"""朋友圈点赞 / 评论 —— UIA 控件交互示例（1.1.10.7）。

前置条件：
  * 微信 4.x 桌面版已登录并打开主窗口；
  * 屏幕上微信主窗口可交互；
  * 依赖热激活 Qt accessibility gate 后获得的 ``mmui`` UIA 树
    （``WeChatGUI`` 惰性初始化）。若 UIA 树不可用，脚本会提示。

要点（务必沿用，否则命令行会卡死）：
  * 只调用 ``sys.stdout.reconfigure(...)``，不要重新包装 stdout；
  * 本示例会真实点击界面按钮，请确认不会打扰正在使用的微信。

用法：
  python -m wechatauto.demo_moments_interact              # 列出最新动态（不操作）
  python -m wechatauto.demo_moments_interact --like N     # 给第 N 条点赞
  python -m wechatauto.demo_moments_interact --unlike N   # 取消第 N 条的赞
  python -m wechatauto.demo_moments_interact --comment N 文字  # 评论第 N 条
"""
from __future__ import annotations

import argparse
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # 勿改用 TextIOWrapper 重包装


def _resp(r) -> str:
    if r is None:
        return 'None'
    return f"{getattr(r, 'is_success', None)} {r.get('message') if isinstance(r, dict) else r}"


def main() -> int:
    try:
        from wechatauto import WeChat
    except Exception as e:
        print(f'导入 wechatauto 失败：{e}')
        return 2

    parser = argparse.ArgumentParser(description='朋友圈点赞/评论（UIA 控件路线）')
    parser.add_argument('--like', type=int, metavar='N', help='给第 N 条动态点赞')
    parser.add_argument('--unlike', type=int, metavar='N', help='取消第 N 条动态的赞')
    parser.add_argument('--comment', nargs='+', metavar='', help='评论：--comment <第几条> <文字>')
    parser.add_argument('--list', type=int, default=5, metavar='N', help='列出最新 N 条（默认 5）')
    args = parser.parse_args()

    wx = WeChat()
    print(f'账号: {wx.nickname or getattr(wx, "_wxid", "")}')

    moments = wx.Moment
    if moments is None:
        print('UIA 树不可用：无法进入朋友圈（本机未热激活出 mmui 节点）。')
        print('提示：请确认微信主窗口在前台且已登录；或改用数据库只读路线读取，点赞/评论需 UI。')
        return 1

    if not wx.SwitchToMoments():
        print('切换到朋友圈页面失败（找不到导航按钮/mmui 主窗口）。')
        return 1

    items = moments.GetMoments()
    print(f'已读取 UIA 动态列表，共 {len(items)} 条。')
    for i, it in enumerate(items[: args.list], 1):
        print(f'  [{i}] {getattr(it, "publisher", "?")}: {getattr(it, "text", "")[:40]}')

    if not items:
        print('界面动态列表为空（可能需要滚动加载，或本版 UIA 节点未识别）。')
        return 0

    def pick(idx: int):
        if idx < 1 or idx > len(items):
            print(f'N={idx} 越界（共 {len(items)} 条），取第 1 条。')
            return items[0]
        return items[idx - 1]

    if args.like is not None:
        it = pick(args.like)
        r = moments.Like(it)
        print(f'点赞第 {args.like} 条: {_resp(r)}')
        return 0

    if args.unlike is not None:
        it = pick(args.unlike)
        r = moments.Like(it, cancel=True)
        print(f'取消点赞第 {args.unlike} 条: {_resp(r)}')
        return 0

    if args.comment:
        idx = args.comment[0]
        text = ' '.join(args.comment[1:])
        it = pick(int(idx))
        r = moments.Comment(it, text)
        print(f'评论第 {idx} 条「{text}」: {_resp(r)}')
        return 0

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
