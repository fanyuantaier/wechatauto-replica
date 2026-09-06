# -*- coding: utf-8 -*-
"""wechatauto 复刻版 —— 坐标 + OCR 发送消息示例程序

针对微信 4.1.12+ 自绘聊天界面（UIA 方案失效）的发送路线：
定位窗口 → 打开会话 → 点击输入框 → 剪贴板粘贴 → 点击发送。

使用前提：
    1. 微信 4.x 已登录、桌面已解锁；
    2. 已安装依赖：pip install -e .
       若使用拼音回退输入，另需：pip install pypinyin

用法：
    python demo_guia.py
"""

from __future__ import annotations

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

from wechatauto.guia import WeChatGUI


def main():
    print("=" * 60)
    print("wechatauto 复刻版 —— 坐标 + OCR 发送消息示例")
    print("=" * 60)

    wx = WeChatGUI()

    wx.bring_to_front()
    print(f"已连接到微信主窗口（hwnd={wx.main_hwnd}）")

    # 1. 列出当前可见会话（OCR）
    print("\n--- 当前可见会话 ---")
    for row in wx.get_sessions()[:10]:
        print(f"  {row['name']}")

    # 2. 打开会话
    who = "文件传输助手"
    print(f"\n--- 打开会话：{who} ---")
    wx.open_chat(who)
    time.sleep(0.5)

    # 3. 检测输入框
    box = wx.get_input_box()
    if box:
        x0, y0, x1, y1 = box
        print(f"检测到输入框（相对坐标）：x[{x0}..{x1}] y[{y0}..{y1}]")
    else:
        print("未检测到输入框")

    # 4. 发送消息
    text = "这是 wechatauto 复刻版坐标+OCR 自动化测试消息"
    print(f"\n--- 发送消息 ---")
    print(f"内容：{text}")
    result = wx.send_msg(text, verify=True)
    print(f"结果：{result}")
    if not result:
        sys.exit(1)

    # 5. 读取最近消息（数据库路线，交叉验证）
    try:
        from wechatauto.db import WeChatDB
        db = WeChatDB()
        hits = db.search_contact(who)
        if hits:
            msgs = db.get_messages(hits[0]["username"], limit=3)
            print("\n--- 数据库读回最近 3 条 ---")
            for m in reversed(msgs):
                sender = "我" if m["sender_id"] == 2 else "对方"
                t = time.strftime("%H:%M:%S", time.localtime(m["create_time"]))
                print(f"  [{t}] {sender} {m['content'][:40]}")
    except Exception as e:
        print(f"（数据库读回失败：{e}）")

    print("\n完成。")


if __name__ == "__main__":
    main()
