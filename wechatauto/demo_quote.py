# -*- coding: utf-8 -*-
"""quote_msg / quick_quote 实测脚本：右键 → 菜单「引用」→ 输入 → 发送。"""
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
from wechatauto.db import WeChatDB


def db_latest(who, n=5):
    """数据库读回最近 n 条，交叉验证发送结果。"""
    try:
        db = WeChatDB()
        hits = db.search_contact(who)
        if not hits:
            return []
        msgs = db.get_messages(hits[0]["username"], limit=n)
        return [f"[{time.strftime('%H:%M:%S', time.localtime(m['create_time']))}] "
                f"{'我' if m['sender_id'] == 2 else '对方'} {m['content'][:40]}"
                for m in reversed(msgs)]
    except Exception as e:
        return [f"（读回失败：{e}）"]


def main():
    wx = WeChatGUI()

    who = "文件传输助手"
    target = None                 # ← 引用最近一条消息（可改成目标消息里的文字）
    text = "这条是引用回复测试 [quote]"

    print("=" * 60)
    print(f"[quote_msg] 引用后发送（会话：{who}，目标文案：{target or '最近一条'}）")
    print("=" * 60)
    print("数据库当前最近 3 条：")
    for line in db_latest(who, 3):
        print("   ", line)

    r = wx.quote_msg(text, who=who, target_text=target or None, verify=True)
    print(f"\nquote_msg 结果：\n  ok={r.is_success}\n  消息={r['message']}\n  数据={r['data']}")

    print("\n发送后数据库最近 5 条：")
    for line in db_latest(who, 5):
        print("   ", line)

    print("\n完成。若 ok=False，请把打印的失败信息贴出来。")


if __name__ == "__main__":
    main()