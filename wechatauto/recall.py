# -*- coding: utf-8 -*-
"""防撤回监听（RecallGuard）：增量镜像库 + 媒体备份 + 撤回复原提示。

方案1 —— 增量镜像库：Listener 回调里把每条新消息完整落一份到独立 sqlite
（``mirror/recall.db``），不依赖微信本地库的删除/替换行为。对方撤回后，
微信会把本地原文行替换为 ``revokemsg`` 系统消息，镜像中的原件仍可恢复。

方案3 —— 媒体备份：消息为图片/语音/视频/文件时，立即调用
:class:`MediaDownloader` 下载一份到备份目录（``media/``），撤回后文件仍在。

撤回提示：检测到 ``sysmsg type="revokemsg"`` 时，从镜像库反查撤回时间窗口
内最近一条非系统消息作为原文，终端打印并写 ``recall_events`` 表。

用法::

    from wechatauto import WeChatDB
    from wechatauto.db import Listener
    from wechatauto.recall import RecallGuard

    db = WeChatDB()
    guard = RecallGuard(db)
    lst = Listener(db, interval=1.0)
    guard.watch(lst)          # 监听所有会话（backfill 历史消息到镜像）
    lst.start()
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import threading
import time
from typing import List, Optional

DEFAULT_RECALL_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "wechatauto_recall")

# 媒体类型（local_type）：3 图片 / 34 语音 / 43 视频 / 49 文件
_MEDIA_TYPES = {3, 34, 43, 49}
_MEDIA_TYPE_NAMES = {"图片", "语音", "视频", "文件/链接/卡片"}

_REVOKE_RE = re.compile(r'"\s*([^"<>]*?)\s*"\s*撤回了一条消息')


class RecallGuard:
    """防撤回监听：挂在 :class:`wechatauto.db.Listener` 上使用。

    Args:
        db: :class:`WeChatDB` 实例。
        mirror_dir: 镜像库与媒体备份根目录，默认
            ``~/Documents/wechatauto_recall``。
        media_dir: 媒体备份目录，默认 ``<mirror_dir>/media``。
        downloader: 自定义 :class:`MediaDownloader`，默认按需创建。
        window: 撤回复原时向前回溯的时间窗口（秒），默认 120。
    """

    def __init__(self, db, mirror_dir: Optional[str] = None,
                 media_dir: Optional[str] = None,
                 downloader=None, window: int = 120):
        self.db = db
        self.window = int(window)
        self.mirror_dir = mirror_dir or DEFAULT_RECALL_DIR
        self.media_dir = media_dir or os.path.join(self.mirror_dir, "media")
        self._lock = threading.Lock()
        self._conn = None
        self._downloader = downloader
        os.makedirs(self.media_dir, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------ 镜像
    def _init_db(self) -> None:
        path = os.path.join(self.mirror_dir, "recall.db")
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS mirror ("
            "  chat TEXT NOT NULL, local_id INTEGER NOT NULL, "
            "  type TEXT, sender_id INTEGER, sender_username TEXT, "
            "  create_time INTEGER, sort_seq INTEGER, "
            "  content TEXT, media_path TEXT, "
            "  PRIMARY KEY (chat, local_id)"
            ")")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS recall_events ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "  chat TEXT, revoke_time INTEGER, revoker TEXT, "
            "  content TEXT, original_content TEXT, original_media_path TEXT"
            ")")
        self._conn.commit()

    def _store(self, msg: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO mirror ("
                " chat, local_id, type, sender_id, sender_username, "
                " create_time, sort_seq, content, media_path"
                ") VALUES (?,?,?,?,?,?,?,?,?)",
                (msg.get("username") or "", msg.get("local_id") or 0,
                 msg.get("type", ""), msg.get("sender_id"),
                 msg.get("sender_username", ""),
                 msg.get("create_time", 0), msg.get("sort_seq", 0),
                 str(msg.get("content", "")), None))
            self._conn.commit()

    def backfill(self, user: str, limit: int = 50) -> None:
        """把该会话最近 limit 条消息补入镜像（watch 时自动调用）。"""
        try:
            for m in self.db.get_messages(user, limit=limit):
                m["username"] = user
                self._store(m)
        except Exception as exc:
            sys.stderr.write("recall backfill error: %r\n" % exc)

    # -------------------------------------------------------------- 媒体备份
    def _backup_media(self, msg: dict) -> Optional[str]:
        if msg.get("type") not in _MEDIA_TYPE_NAMES:
            return None
        chat = msg.get("username") or ""
        local_id = msg.get("local_id")
        md = self._downloader
        try:
            if md is None:
                from .media import MediaDownloader
                md = self._downloader = MediaDownloader(self.db)
            out = md.download_media(chat, local_id, save_dir=self.media_dir)
            if out:
                with self._lock:
                    self._conn.execute(
                        "UPDATE mirror SET media_path=? "
                        "WHERE chat=? AND local_id=?",
                        (out, chat, local_id))
                    self._conn.commit()
            return out
        except Exception as exc:
            sys.stderr.write("recall media backup error: %r\n" % exc)
            return None

    # ---------------------------------------------------------- 撤回检测
    def on_msg(self, msg: dict, listener) -> None:
        """Listener 回调：镜像 → 媒体备份 → 撤回复原提示。

        Args:
            msg: Listener 下发的消息 dict（含 local_id / type / content /
                sort_seq / username 等字段）。
            listener: 触发回调的 :class:`Listener` 实例（未使用，保留签名）。
        """
        try:
            self._store(msg)
            self._backup_media(msg)
            if msg.get("type") == "系统消息" and "revokemsg" in str(msg.get("content", "")):
                self._on_revoke(msg)
        except Exception as exc:
            sys.stderr.write("recall on_msg error: %r\n" % exc)

    def _on_revoke(self, msg: dict) -> None:
        revoke_time = msg.get("create_time") or 0
        content = str(msg.get("content", ""))
        revoker = self._extract_revoker(content) or msg.get("sender_username") or "未知"
        original = self._find_original(msg.get("username") or "", revoke_time)
        orig_content = original["content"] if original else ""
        orig_media = original["media_path"] if original else None

        line = time.strftime("%H:%M:%S", time.localtime(revoke_time or time.time()))
        print("\n[撤回][%s] %s 撤回了一条消息" % (line, revoker))
        if original:
            snippet = str(orig_content)[:200]
            print("   原文(镜像): %s" % snippet)
        if orig_media:
            print("   媒体备份  : %s" % orig_media)
        if not original:
            print("   镜像中无原文（监听启动前已被撤回，或消息未缓存）")

        with self._lock:
            self._conn.execute(
                "INSERT INTO recall_events ("
                " chat, revoke_time, revoker, content, "
                " original_content, original_media_path"
                ") VALUES (?,?,?,?,?,?)",
                (msg.get("username") or "", revoke_time, revoker, content,
                 orig_content, orig_media))
            self._conn.commit()

    def _extract_revoker(self, content: str) -> str:
        m = _REVOKE_RE.search(content)
        if m:
            return m.group(1).strip()
        m = re.search(r"\u4f60\u64a4\u56de\u4e86\u4e00\u6761\u6d88\u606f",
                      content)  # 你撤回了一条消息
        if m:
            return "我"
        m = re.search(r"([^\s\"<>]+?)撤回了一条消息", content)
        if m:
            return m.group(1)
        return ""

    def _find_original(self, chat: str, revoke_time: int) -> Optional[dict]:
        if not chat:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT content, media_path, create_time, sender_username "
                "FROM mirror WHERE chat=? AND type!='系统消息' "
                "AND create_time<? AND create_time>=?-? "
                "ORDER BY sort_seq DESC LIMIT 1",
                (chat, revoke_time, revoke_time, self.window)).fetchone()
        if not row:
            return None
        return {
            "content": row[0],
            "media_path": row[1],
            "create_time": row[2],
            "sender_username": row[3],
        }

    def get_recalled(self, chat: Optional[str] = None,
                     limit: int = 50) -> List[dict]:
        """查询撤回事件记录（镜像库 recall_events 表）。"""
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            if chat:
                rows = self._conn.execute(
                    "SELECT * FROM recall_events WHERE chat=? "
                    "ORDER BY id DESC LIMIT ?", (chat, limit)).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM recall_events ORDER BY id DESC LIMIT ?",
                    (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ 挂载
    def watch(self, listener, users: Optional[List[str]] = None,
              backfill: int = 50) -> None:
        """把 RecallGuard 挂到 Listener。

        Args:
            listener: :class:`wechatauto.db.Listener` 实例。
            users: 指定会话 username 列表；为 None 时用 ``add_all``
                监听所有会话（自动发现新会话）。
            backfill: 启动时补入镜像的历史消息条数（0 表示不补）。
        """
        if users is None:
            listener.add_all(self.on_msg)
            sessions = self.db.get_sessions(limit=500)
            users = [s["username"] for s in sessions]
        else:
            for u in users:
                listener.add_listener(u, self.on_msg)
        if backfill > 0:
            for u in users:
                self.backfill(u, backfill)

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None