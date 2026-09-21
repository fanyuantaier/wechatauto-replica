# -*- coding: utf-8 -*-
"""wechatauto 测试发送脚本 —— 坐标 + OCR 发送（微信 4.x 自绘界面）

默认发送三件套：一条测试消息 + 一张图片 + 一个文件（README.md）。

用法：
    python demo_send.py [目标] [内容] [--image [图片路径]] [--file 文件路径]
                        [--skip-text] [--skip-image] [--skip-file]
                        [--verify] [--times N]

参数：
    目标    要发送到的会话名（昵称/备注/搜索关键词），默认「文件传输助手」
    内容    文本消息内容；不填则生成带时间戳的测试消息
    --image 图片路径。不带路径时使用默认图片（RWTemp 最新截图）
    --file  文件路径。默认当前目录下的 README.md
    --skip-text   跳过文本消息
    --skip-image  跳过图片
    --skip-file   跳过文件
    --verify 发送后通过本地数据库读回确认
    --times 重复发送轮数（每轮三件套，默认 1）

例：
    python demo_send.py                          # 三件套发到文件传输助手
    python demo_send.py 兔仔仔                   # 三件套发给人
    python demo_send.py 文件传输助手 "你好"       # 自定义文本
    python demo_send.py 我的群 --image C:\\pics\\a.png   # 指定图片
    python demo_send.py 文件传输助手 --skip-text --skip-image   # 只发文件

原理：
    WeChatGUI.send_msg 采用 坐标+OCR：激活窗口 → 搜索并打开会话 → 点击输入框
    → 剪贴板粘贴 → 点击发送；可选 verify 用数据库读回（sender_id=2）确认。
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time

try:
    os.system("chcp 65001 >nul 2>&1")
except Exception:
    pass
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from wechatauto import WeChatGUI, WeChatDB, WxResponse

# 默认图片：RWTemp 中最新的截图（微信收到的图片会缓存在这里）
# 默认图片：留空则用 pick_default_image() 自动挑 RWTemp 里最新的截图；
# 如需固定路径，请填自己的本地路径（勿提交真实 wxid/路径）
DEFAULT_IMAGE = ""
# 默认文件
DEFAULT_FILE = os.path.join(os.getcwd(), "README.md")   # 默认发当前目录的 README


def pick_default_image() -> str:
    r"""在微信 RWTemp 目录里找最新的一张图片；找不到则回退 DEFAULT_IMAGE（可能为空）。

    目录自动探测（不写死本机路径）：在「文档\xwechat_files\*\temp」下找
    RWTemp 之类的缓存目录；也可用环境变量 WECHATAUTO_RWTEMP 显式指定。
    """
    cands = []
    env = os.environ.get("WECHATAUTO_RWTEMP")
    if env:
        cands.append(env)
    for base in (os.path.expanduser("~/Documents/xwechat_files"),
                 os.path.expanduser("~/Documents/WeChat Files"),
                 os.path.expanduser("~/Documents/WeChatFiles")):
        cands += glob.glob(os.path.join(base, "*", "temp", "RWTemp"))
        cands += glob.glob(os.path.join(base, "*", "temp"))
    newest, newest_m = "", 0.0
    for d in cands:
        if not os.path.isdir(d):
            continue
        for p in glob.glob(os.path.join(d, "**", "*"), recursive=True):
            if os.path.splitext(p)[1].lower() not in (".png", ".jpg", ".jpeg"):
                continue
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            if m > newest_m:
                newest, newest_m = p, m
    return newest or DEFAULT_IMAGE


def resolve_target(db: WeChatDB, raw: str) -> str:
    """把输入（username / 昵称 / 备注）解析成会话 username 并打印映射。"""
    hits = db.search_contact(raw)
    if hits:
        return hits[0]["username"]
    return raw


def show_result(resp: WxResponse) -> bool:
    """打印发送结果。WxResponse 是 dict，状态值：成功/失败/错误。"""
    print(f"  [{resp['status']}] {resp['message']}")
    return resp.is_success


def read_back(db: WeChatDB, who: str, text: str = None, n: int = 5) -> None:
    """从数据库读回最近消息做交叉验证。"""
    msgs = db.get_messages(who, limit=n)
    print(f"\n--- 数据库读回最近 {len(msgs)} 条 ---")
    for m in reversed(msgs):
        sender = "我" if m["sender_id"] == 2 else "对方"
        t = time.strftime("%H:%M:%S", time.localtime(m["create_time"]))
        print(f"  [{t}] {sender} {m['content'][:50]}")
    if text:
        sent = any(m["sender_id"] == 2 and text in (m.get("content") or "")
                   for m in msgs)
        print(f"\n验证结果：{'✅ 已找到我发送的该消息' if sent else '⚠️ 最近记录中未找到该消息（可能未落库或异步延迟）'}")


def main():
    parser = argparse.ArgumentParser(
        description="wechatauto 测试发送脚本（坐标+OCR 发送）", add_help=False)
    parser.add_argument("target", nargs="?", default="文件传输助手",
                        help="目标会话（昵称/备注/username），默认文件传输助手")
    parser.add_argument("content", nargs="?", default=None,
                        help="文本内容；不填则生成带时间戳的测试消息")
    parser.add_argument("--image", nargs="?", const=True, default=None,
                        help="图片路径。不带路径时用默认图片（RWTemp 最新截图）")
    parser.add_argument("--file", nargs="?", const=True, default=None,
                        help="文件路径。不带路径时用默认文件 README.md")
    parser.add_argument("--skip-text", action="store_true", help="跳过文本消息")
    parser.add_argument("--skip-image", action="store_true", help="跳过图片")
    parser.add_argument("--skip-file", action="store_true", help="跳过文件")
    parser.add_argument("--verify", action="store_true", help="发送后读库确认")
    parser.add_argument("--times", type=int, default=1, help="重复发送轮数")
    parser.add_argument("-h", "--help", action="help")
    args = parser.parse_args()

    #who = args.target
    who = "文件传输助手"
    text = args.content or f"wechatauto 发送测试 {time.strftime('%H:%M:%S')}"
    image = pick_default_image() if (args.image is True or (args.image is None and not args.skip_image)) else args.image
    file_ = DEFAULT_FILE if (args.file is True or (args.file is None and not args.skip_file)) else args.file
    if args.skip_text:
        text = None
    if args.skip_image:
        image = None
    if args.skip_file:
        file_ = None

    print("=" * 60)
    print("wechatauto 测试发送")
    print("=" * 60)
    if text:
        print(f"  文本：{text}")
    if image:
        print(f"  图片：{image}")
    if file_:
        print(f"  文件：{file_}")
    if not (text or image or file_):
        print("  （未选择任何发送内容）")

    # 1. 目标会话 username 解析（仅用于读回验证）
    db = None
    target_username = None
    if args.verify:
        db = WeChatDB()
        info = db.get_self_info()
        print(f"账号：{info.get('nick_name') or info.get('username')}")
        target_username = resolve_target(db, who)
        print(f"目标：{who} -> {target_username}")

    # 2. 初始化 GUI
    wx = WeChatGUI()
    wx.bring_to_front()
    print(f"已连接微信主窗口（hwnd={wx.main_hwnd}）")

    # 3. 逐轮发送三件套
    ok = True
    for i in range(args.times):
        if args.times > 1:
            print(f"\n--- 第 {i + 1}/{args.times} 轮 ---")
        if text:
            print("\n[发送文本]")
            if not show_result(wx.send_msg(text, who, verify=args.verify)):
                ok = False
        if image:
            print("\n[发送图片]")
            if not show_result(wx.send_image(image, who, verify=args.verify)):
                ok = False
        if file_:
            print("\n[发送文件]")
            if not show_result(wx.send_file(file_, who, verify=args.verify)):
                ok = False

    # 4. 读回确认
    if args.verify and db and target_username:
        read_back(db, target_username, text)

    print("\n" + ("✅ 全部发送完成。" if ok else "⚠️ 存在失败的发送，请查看上方结果。"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
