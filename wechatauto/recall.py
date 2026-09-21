# -*- coding: utf-8 -*-
"""防撤回监听（RecallGuard）：增量镜像库 + 媒体备份 + 撤回复原提示。

方案1 —— 增量镜像库：Listener 回调里把每条新消息完整落一份到独立 sqlite
（``mirror/recall.db``），不依赖微信本地库的删除/替换行为。对方撤回后，
微信会把本地原文行替换为 ``revokemsg`` 系统消息，镜像中的原件仍可恢复。

方案3 —— 媒体备份：消息为图片/语音/视频/文件时，立即调用
:class:`MediaDownloader` 下载一份到备份目录（``media/``），撤回后文件仍在。

撤回检测：微信撤回是**原地改写原文那一行**（``message_content`` 变成
``revokemsg`` 系统消息，``local_id`` 与 ``sort_seq`` 都不变），而 :class:`Listener`
按 ``sort_seq >`` 水位增量取，改写过的行不会再下发一次。所以撤回**不能靠 Listener
触发**，由 RecallGuard 自己的轮询线程定期重读会话最近的行，拿 ``local_id`` 与镜像
比对：镜像里还是正常消息、现库已变成 ``revokemsg``，即一次撤回。原文直接取镜像那一行，
终端打印并写 ``recall_events`` 表。Listener 回调仍保留（新消息入镜像 + 媒体备份）。

用法::

    from wechatauto import WeChatDB
    from wechatauto.db import Listener
    from wechatauto.recall import RecallGuard

    db = WeChatDB()
    guard = RecallGuard(db)
    lst = Listener(db, interval=1.0)
    guard.watch(lst)          # 监听所有会话（backfill 历史消息到镜像）
    lst.start()
    ...
    guard.close()             # 停轮询线程并关镜像库
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
# 撤回行的 create_time 还是原文的时间，真正的撤回时刻只在这个字段里
_REVOKE_TIME_RE = re.compile(r'<revoketime>(\d+)</revoketime>')


def _is_revoke_row(msg: dict) -> bool:
    return (msg.get("type") == "系统消息"
            and "revokemsg" in str(msg.get("content", "")))


class RecallGuard:
    """防撤回监听：挂在 :class:`wechatauto.db.Listener` 上使用。

    Args:
        db: :class:`WeChatDB` 实例。
        mirror_dir: 镜像库与媒体备份根目录，默认
            ``~/Documents/wechatauto_recall``。
        media_dir: 媒体备份目录，默认 ``<mirror_dir>/media``。
        downloader: 自定义 :class:`MediaDownloader`，默认按需创建。
        window: 撤回复原的兜底时间窗（秒），默认 120 —— 只在
            ``local_id`` 查不到镜像行时才用（如镜像早于消息建立）。
        scan_interval: 撤回轮询间隔秒数，默认 2.0。
        scan_limit: 每会话每次轮询重读最近多少条，默认 30。
        scan: 关掉撤回轮询（只保留镜像与媒体备份），默认 True。
    """

    def __init__(self, db, mirror_dir: Optional[str] = None,
                 media_dir: Optional[str] = None,
                 downloader=None, window: int = 120,
                 scan_interval: float = 2.0, scan_limit: int = 30,
                 scan: bool = True):
        self.db = db
        self.window = int(window)
        self.scan_interval = max(0.5, float(scan_interval))
        self.scan_limit = max(1, int(scan_limit))
        self.mirror_dir = mirror_dir or DEFAULT_RECALL_DIR
        self.media_dir = media_dir or os.path.join(self.mirror_dir, "media")
        self._lock = threading.Lock()
        self._conn = None
        self._downloader = downloader
        self._scan_enable = bool(scan)
        self._scan_users: List[str] = []
        self._stop = threading.Event()
        self._scan_thread: Optional[threading.Thread] = None
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
            if _is_revoke_row(msg):
                # 撤回行不能覆盖镜像里的原文，否则这一存就把要救的东西弄丢了
                self._handle_revoke(msg)
                return
            self._store(msg)
            self._backup_media(msg)
        except Exception as exc:
            sys.stderr.write("recall on_msg error: %r\n" % exc)

    def _handle_revoke(self, msg: dict) -> None:
        """一次撤回的落账入口（Listener 与轮询共用，按 revoke_time 去重）。"""
        content = str(msg.get("content", ""))
        revoke_time = self._extract_revoke_time(content) or (msg.get("create_time") or 0)
        msg = dict(msg, create_time=revoke_time)
        with self._lock:
            hit = self._conn.execute(
                "SELECT 1 FROM recall_events WHERE chat=? AND revoke_time=? LIMIT 1",
                (msg.get("username") or "", revoke_time)).fetchone()
        if hit:
            return
        self._on_revoke(msg)

    def _on_revoke(self, msg: dict) -> None:
        revoke_time = msg.get("create_time") or 0
        content = str(msg.get("content", ""))
        revoker = self._extract_revoker(content) or msg.get("sender_username") or "未知"
        original = self._find_original(msg.get("username") or "", revoke_time,
                                       msg.get("local_id"))
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

    def _extract_revoke_time(self, content: str) -> int:
        m = _REVOKE_TIME_RE.search(content or "")
        return int(m.group(1)) if m else 0

    def _find_original(self, chat: str, revoke_time: int,
                       local_id: Optional[int] = None) -> Optional[dict]:
        """镜像里的那条原文。

        撤回是原地改写原文那一行，``local_id`` 不变 —— 所以同 ``local_id`` 的镜像行
        就是原文本身。时间窗只作兜底：撤回行的 ``create_time`` 等于原文自己的时间，
        条件必须是 ``<=``，用严格小于永远查不到。
        """
        if not chat:
            return None
        with self._lock:
            row = None
            if local_id:
                row = self._conn.execute(
                    "SELECT content, media_path, create_time, sender_username "
                    "FROM mirror WHERE chat=? AND local_id=? AND type!='系统消息'",
                    (chat, local_id)).fetchone()
            if row is None:
                row = self._conn.execute(
                    "SELECT content, media_path, create_time, sender_username "
                    "FROM mirror WHERE chat=? AND type NOT IN ('系统消息','') "
                    "AND create_time<=? AND create_time>=?-? "
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

    # -------------------------------------------------------- 撤回轮询
    def _has_original(self, chat: str, local_id) -> bool:
        if not local_id:
            return False
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM mirror WHERE chat=? AND local_id=? "
                "AND type NOT IN ('系统消息','') LIMIT 1",
                (chat, local_id)).fetchone() is not None

    def scan_now(self, users: Optional[List[str]] = None) -> int:
        """立即扫一遍，返回本次新记下的撤回条数。

        Listener 收不到「原地改写」的行，撤回只能靠这里重读现库、拿 local_id
        与镜像比对来发现。只报镜像里存着原文的那些：更早的撤回在监听建立前
        就被覆盖了，救不回来，记进 recall_events 也只是刷空行。
        """
        n = 0
        for u in self._scan_users if users is None else users:
            try:
                live = self.db.get_messages(u, limit=self.scan_limit)
            except Exception as exc:
                sys.stderr.write("recall scan error: %r\n" % exc)
                continue
            for m in live:
                if not _is_revoke_row(m) or not self._has_original(u, m.get("local_id")):
                    continue
                m["username"] = u
                before = len(self.get_recalled(u, limit=1000))
                self._handle_revoke(m)
                n += len(self.get_recalled(u, limit=1000)) - before
        return n

    def _scan_run(self) -> None:
        while not self._stop.wait(self.scan_interval):
            try:
                self.scan_now()
            except Exception as exc:
                sys.stderr.write("recall scan loop error: %r\n" % exc)

    def start_scan(self, users: List[str]) -> None:
        """起撤回轮询线程（watch 里自动调；不用 Listener 时可手动起）。"""
        self._scan_users = list(users)
        if not self._scan_enable or self._scan_thread:
            return
        self._scan_thread = threading.Thread(target=self._scan_run,
                                             name="wxrecall-scan", daemon=True)
        self._scan_thread.start()

    # ------------------------------------------------------------ 挂载
    def watch(self, listener, users: Optional[List[str]] = None,
              backfill: int = 50) -> None:
        """把 RecallGuard 挂到 Listener，并起撤回轮询线程。

        Args:
            listener: :class:`wechatauto.db.Listener` 实例。
            users: 指定会话 username 列表；为 None 时用 ``add_all``
                监听所有会话（自动发现新会话）。
            backfill: 启动时补入镜像的历史消息条数（0 表示不补）。

        注意：``users=None`` 时轮询要覆盖全部会话，一轮耗时随会话数线性涨，
        撤回提示的延迟就是一轮的长度。只关心少数会话时显式传 ``users``。
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
        self.start_scan(users)

    def close(self) -> None:
        self._stop.set()
        t, self._scan_thread = self._scan_thread, None
        if t:
            t.join(timeout=self.scan_interval + 5)
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None