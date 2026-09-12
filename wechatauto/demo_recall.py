"""防撤回监听示例 —— 镜像库 + 媒体备份 + 撤回终端提示（RecallGuard）

用法::

    python -m wechatauto.demo_recall                # 监听所有会话
    python -m wechatauto.demo_recall 张三 李四      # 只监听指定会话(昵称/username)
    python -m wechatauto.demo_recall --backlog 200  # 额外回填 200 条历史到镜像

原理:
    1. 每收到一条新消息即写入独立镜像库（recall.db），不依赖微信本地删改；
    2. 图片/语音/视频/文件消息立即增量备份到 media/ 目录；
    3. 检测到 revokemsg 系统消息时，从镜像反查秒级窗口内的最近原文，
       终端打印撤回提醒并写入 recall_events 表（可用 get_recalled 查询）。
"""

from __future__ import annotations

import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from wechatauto import WeChatDB
from wechatauto.db import Listener
from wechatauto.recall import RecallGuard


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    all_chats = "--all" in sys.argv
    backlog = 50
    for a in sys.argv[1:]:
        if a.startswith("--backlog="):
            backlog = int(a.split("=", 1)[1])

    db = WeChatDB()
    guard = RecallGuard(db)
    lst = Listener(db, interval=1.0)

    if all_chats or not args:
        guard.watch(lst, users=None, backfill=backlog)
        print("监听所有会话（自动发现新会话）...")
    else:
        sessions = db.get_sessions(limit=500)
        by_username = {s["username"]: s for s in sessions}
        names = []
        for raw in args:
            if raw in by_username:
                names.append(raw)
            else:
                hits = db.search_contact(raw)
                if hits:
                    names.append(hits[0]["username"])
                else:
                    names.append(raw)
        guard.watch(lst, users=names, backfill=backlog)
        print("监听会话: %s" % ", ".join(names))

    print("镜像目录: %s" % guard.mirror_dir)
    print("开始监听（Ctrl+C 停止）...")
    lst.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        lst.stop()
        guard.close()
        print("\n已停止监听。历史撤回事件可查 guard.get_recalled()")


if __name__ == "__main__":
    main()