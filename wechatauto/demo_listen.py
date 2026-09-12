# -*- coding: utf-8 -*-
"""实时消息监听示例 —— 基于本地数据库增量轮询（Listener）

用法：
    python demo_listen.py [会话名1] [会话名2] ...
    # 不带参数则默认监听「文件传输助手」

例：
    python demo_listen.py wxid_xxx 123456@chatroom
    python demo_listen.py 兔仔仔 我的群    # 昵称/备注会自动映射到会话 username
    python demo_listen.py --all             # 监听全部会话（运行中自动发现新会话）

⚠️ 注意：
    names 里最终匹配的是「会话 username」——即 get_sessions() 返回的
    wxid_xxx（个聊）或 xxx@chatroom（群聊），不是微信昵称、也不是
    你设置的微信号(alias)。传入昵称/备注时本脚本会自动帮你转换；
    若转换失败会给出提示，此时请先运行一次本脚本查看列出的会话清单。
"""
from __future__ import annotations

import os
import sys
import time
import threading

try:
    os.system("chcp 65001 >nul 2>&1")
except Exception:
    pass
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from wechatauto.db import WeChatDB, Listener


def fmt_time(ts: float) -> str:
    return time.strftime("%m-%d %H:%M:%S", time.localtime(ts))


def sender_name(db, sid: int) -> str:
    if sid == 2:
        return "我"
    nick = db.get_nickname(sid) if isinstance(sid, str) else None
    return nick or f"用户{sid}"


def make_callback(db, chat_name: str):
    """为某个会话生成回调函数。callback(msg: dict, listener)"""
    def on_msg(msg: dict, lst: Listener):
        sender = sender_name(db, msg["sender_id"])
        t = fmt_time(msg["create_time"])
        print(f"[{t}] {chat_name} | {sender} ({msg['type']}) {msg['content']}")
        # 可在此扩展业务：msg['content'] 含关键字时自动回复等
    return on_msg


def resolve_name(db, sessions, raw: str) -> str:
    """把用户输入（username / 昵称 / 备注 / 微信号）解析成会话 username。

    匹配优先级：session.username 精确匹配 > contact 的 nick_name/remark 精确匹配
    > 未命中直接原样返回（可能本身即为有效 username）。
    """
    sessions_by_user = {s["username"]: s for s in sessions}
    if raw in sessions_by_user:
        return raw
    hits = db.search_contact(raw)
    if hits:
        return hits[0]["username"]
    return raw


def main():
    names = [a for a in sys.argv[1:] if not a.startswith("-")]
    all_chats = "--all" in sys.argv

    db = WeChatDB()
    info = db.get_self_info()
    print(f"账号：{info.get('nick_name') or info.get('username')}")

    # 1. 列出当前会话，供挑选（username 就是监听必须使用的值）
    sessions = db.get_sessions(limit=500)
    #print(f"\n当前会话（共 {len(sessions)} 个，最近 15 个，请把 username 填入 names）：")
    #for s in sessions[:15]:
        #print(f"  {s['username']:<24} 未读={s['unread']}  {s['summary'][:24] or ''}")

    # 2. 注册监听（回调在后台线程触发；水位自动落盘，重启不重复推送）
    lst = Listener(db, interval=1.0)
    if all_chats:
        # 全局监听：用 add_all(discover=True) 自动发现新会话，
        # 不局限于启动时的会话清单（之前只取最近 30 个，之后新会话不会加入）
        def on_all(msg: dict, _lst: Listener):
            chat = msg.get("username", "")
            sender = sender_name(db, msg["sender_id"])
            t = fmt_time(msg["create_time"])
            print(f"[{t}] {chat} | {sender} ({msg['type']}) {msg['content']}")

        lst.add_all(on_all, discover=True)
        print("  全局监听：已注册现有会话，运行中自动发现新会话")
    else:
        if not names:
            names = ["送你挖银子"]
        # 昵称/备注 → username 映射
        resolved = [resolve_name(db, sessions, n) for n in names]
        for raw, got in zip(names, resolved):
            if raw != got:
                print(f"  「{raw}」→ {got}")
        for name in resolved:
            lst.add_listener(name, make_callback(db, name))
            print(f"  监听：{name}")

    print("\n开始监听（Ctrl+C 停止）...")
    lst.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        lst.stop()
        print("\n已停止监听。")


if __name__ == "__main__":
    main()
