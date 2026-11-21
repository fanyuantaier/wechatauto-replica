# -*- coding: utf-8 -*-
"""微信文件自动下载：监听新消息（只读 DB 轮询）→ 文件落盘即自动复制。

基于 :class:`wechatauto.db.Listener`（只读、不驱动界面），监听指定会话（或全部
会话）的新消息；命中文件消息（local_type 49，可选加图片/视频/语音）就调用
:meth:`wechatauto.media.MediaDownloader.download_file` 把
``msg/file/<YYYY-MM>/<原文件名>`` 复制到目标目录。

微信侧文件**落盘有延迟**（点击下载/自动下载策略），所以这里带**待办重试**：
首次复制不到就挂起，每隔几秒复查一次，直到拿到文件或超时（默认 30 分钟）。
状态（成功/失败/待办）记录在 json 里，断电重启不丢账（消息去重由 Listener
的水位持久化负责，本模块状态只管下载结果与待办）。

用法::

    from wechatauto.db import WeChatDB
    from wechatauto.media import MediaDownloader
    from wechatauto.auto_download import AutoFileDownloader

    db = WeChatDB()
    auto = AutoFileDownloader(db, save_dir=r'D:\\downloads',
                              users=None,          # None = 监听全部会话
                              types=(49,))         # 只下文件；可加 3/34/43
    auto.run_forever()               # Ctrl+C 退出；或 auto.start_async()

命令行::

    python -m wechatauto.auto_download --save D:\\downloads
    python -m wechatauto.auto_download --chat 兔仔仔 --chat filehelper --types file,image
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from .db import Listener
from .logger import wxlog
from .media import MediaDownloader

TYPE_IDS = {"file": 49, "image": 3, "voice": 34, "video": 43}
TYPE_NAMES = {v: k for k, v in TYPE_IDS.items()}


def _safe_component(name: str) -> str:
    """把会话名转成合法目录名（Windows 保留字符剔除）。"""
    name = (name or "unknown").strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name[:80] or "unknown"


class AutoFileDownloader:
    """监听新消息并自动下载文件（只读 DB 轮询，绝不驱动微信界面）。

    Args:
        db: :class:`wechatauto.db.WeChatDB` 实例。
        users: 要监听的会话 username / 显示名列表；``None``（默认）监听
            **全部**会话（含后续新建的群，经 :meth:`Listener.add_all` 自动发现）。
        save_dir: 落盘根目录；默认 ``~/Documents/wechatauto_auto``。
            ``per_chat=True``（默认）时再按会话名分一层子目录。
        types: 要自动下载的 local_type 集合，默认 ``(49,)`` 只下文件；
            可传 ``(49, 3, 43, 34)`` 连图片/视频/语音一起。
        interval: db.Listener 轮询间隔（秒）。
        pending_timeout: 文件未落盘时的最长等待（秒），超时放弃并记录。
        poll_pending: 待办复查间隔（秒）。
        state_file: 结果/待办账本 json 路径；默认
            ``save_dir/auto_download_state.json``。
        on_result: 每次下载结算回调 ``fn(record: dict)``（可写自己的日志/通知）。
    """

    def __init__(self, db, users: Optional[Iterable[str]] = None,
                 save_dir: Optional[str] = None,
                 types: Iterable[int] = (49,),
                 per_chat: bool = True,
                 interval: float = 2.0,
                 pending_timeout: float = 1800.0,
                 poll_pending: float = 10.0,
                 state_file: Optional[str] = None,
                 on_result: Optional[Callable[[dict], None]] = None,
                 media: Optional[MediaDownloader] = None):
        self.db = db
        self._md = media or MediaDownloader(db)
        self.save_dir = save_dir or os.path.join(
            os.path.expanduser("~"), "Documents", "wechatauto_auto")
        os.makedirs(self.save_dir, exist_ok=True)
        self.types: Set[int] = set(int(t) for t in types)
        unknown = self.types - set(TYPE_NAMES)
        if unknown:
            raise ValueError("不支持的 local_type: %s（可用: %s）"
                             % (sorted(unknown), sorted(TYPE_NAMES)))
        self.per_chat = bool(per_chat)
        self.pending_timeout = max(0.0, float(pending_timeout))
        self.poll_pending = max(1.0, float(poll_pending))
        self.on_result = on_result

        self.users: Optional[List[str]] = (
            [self._resolve_user(u) for u in users] if users else None)
        if state_file is None:
            state_file = os.path.join(self.save_dir, "auto_download_state.json")
        self.state_file = state_file
        self._state_lock = threading.Lock()
        self._state: dict = {"results": [], "pending": []}
        self._load_state()
        self._last_state_write = 0.0

        self.listener = Listener(db, interval=interval)
        self._pending_lock = threading.Lock()
        # key=(user, local_id, shard) -> {"first_seen": ts, "deadline": ts, "row": {...}}
        self._pending: Dict[Tuple, dict] = {}
        self._pending_thread: Optional[threading.Thread] = None
        self._pending_stop = threading.Event()
        self._stopped = threading.Event()

    # ------------------------------------------------------------------
    # 对外 API
    # ------------------------------------------------------------------
    def start_async(self) -> None:
        """后台启动（监听线程 + 待办复查线程），立即返回。"""
        if self.users is None:
            self.listener.add_all(self._on_msg, discover=True)
            wxlog.info("自动下载：监听全部会话（自动发现新会话），类型=%s"
                       % sorted(TYPE_NAMES[t] for t in self.types))
        else:
            for u in self.users:
                self.listener.add_listener(u, self._on_msg)
            wxlog.info("自动下载：监听 %d 个会话 %s，类型=%s"
                       % (len(self.users), self.users,
                          sorted(TYPE_NAMES[t] for t in self.types)))
        self._start_pending_worker()
        self.listener.start()

    def run_forever(self) -> None:
        """前台运行直到 Ctrl+C。"""
        self.start_async()
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        self._pending_stop.set()
        if self._pending_thread and self._pending_thread.is_alive():
            self._pending_thread.join(timeout=3)
        self.listener.stop()
        self._write_state(force=True)
        wxlog.info("自动下载已停止；累计成功=%d 待办=%d"
                   % (len(self._state.get("results", [])), len(self._pending)))

    def status(self) -> dict:
        with self._pending_lock:
            pending = [dict(v, key=list(k)) for k, v in self._pending.items()]
        return {"save_dir": self.save_dir, "types": sorted(self.types),
                "done": len(self._state.get("results", [])),
                "pending": pending}

    # ------------------------------------------------------------------
    # 消息过滤与下载
    # ------------------------------------------------------------------
    def _resolve_user(self, name: str) -> str:
        """兼容传 username 或显示名（filehelper 直通）。"""
        name = (name or "").strip()
        if not name or name == "filehelper" or name == "文件传输助手":
            return name if name != "文件传输助手" else "filehelper"
        resolve = getattr(self.db, "username_by_nickname", None)
        if resolve is not None:
            try:
                got = resolve(name)
                if got:
                    return got
            except Exception:
                pass
        return name

    def _on_msg(self, msg: dict, listener) -> None:
        """db.Listener 回调：命中目标类型就尝试下载。"""
        mt = msg.get("local_type", msg.get("type"))
        try:
            mt = int(mt)
        except (TypeError, ValueError):
            return
        if mt not in self.types:
            return
        user = msg.get("username") or msg.get("user") or ""
        local_id = msg.get("local_id")
        shard = msg.get("shard") or None
        if not user or local_id is None:
            return
        self._process(user, int(local_id), mt, shard)

    def _target_dir(self, user: str) -> str:
        d = self.save_dir
        if self.per_chat:
            d = os.path.join(d, _safe_component(user))
        os.makedirs(d, exist_ok=True)
        return d

    def _try_download(self, user: str, local_id: int, mt: int,
                      shard: Optional[str]) -> Optional[str]:
        if mt == 49:
            return self._md.download_file(user, local_id,
                                          save_dir=self._target_dir(user),
                                          shard=shard)
        if mt == 3:
            return self._md.download_image(user, local_id,
                                           save_dir=self._target_dir(user),
                                           shard=shard)
        if mt == 34:
            return self._md.download_voice(user, local_id,
                                           save_dir=self._target_dir(user),
                                           shard=shard)
        if mt == 43:
            return self._md.download_video(user, local_id,
                                           save_dir=self._target_dir(user),
                                           shard=shard)
        return None

    def _process(self, user: str, local_id: int, mt: int,
                 shard: Optional[str]) -> None:
        """立即尝试一次；拿不到就挂待办（微信落盘常有延迟）。"""
        key = (user, local_id, shard or "")
        with self._pending_lock:
            if key in self._pending:
                return                      # 已在待办里，等复查线程
        try:
            path = self._try_download(user, local_id, mt, shard)
        except Exception as e:
            wxlog.warning("自动下载异常 user=%s id=%s：%s: %s"
                          % (user, local_id, type(e).__name__, str(e)[:160]))
            path = None
        if path:
            self._settle(key, mt, "ok", path=path)
            wxlog.info("自动下载完成：%s", path)
        else:
            now = time.time()
            with self._pending_lock:
                if key not in self._pending:
                    self._pending[key] = {
                        "user": user, "local_id": local_id, "local_type": mt,
                        "shard": shard, "first_seen": now,
                        "deadline": now + self.pending_timeout,
                        "attempts": 1}
            wxlog.info("文件暂未落盘（微信还没下载完），已挂待办稍后复查："
                       "%s/%s 类型=%s" % (user, local_id, TYPE_NAMES.get(mt, mt)))
            self._write_state()

    # ------------------------------------------------------------------
    # 待办复查线程
    # ------------------------------------------------------------------
    def _start_pending_worker(self) -> None:
        if self._pending_thread and self._pending_thread.is_alive():
            return
        self._pending_stop.clear()
        self._pending_thread = threading.Thread(
            target=self._pending_run, name="wx-auto-download-pending",
            daemon=True)
        self._pending_thread.start()

    def _pending_run(self) -> None:
        while not self._pending_stop.wait(self.poll_pending):
            self._retry_pending_once()

    def _retry_pending_once(self) -> None:
        now = time.time()
        with self._pending_lock:
            items = list(self._pending.items())
        for key, info in items:
            if now >= info["deadline"]:
                self._settle(key, info.get("local_type"), "timeout",
                             error="等待微信落盘超时（%.0fs）"
                                   % self.pending_timeout)
                wxlog.warning("自动下载放弃（微信一直没把文件下载到本地）："
                              "%s/%s 类型=%s"
                              % (info["user"], info["local_id"],
                                 TYPE_NAMES.get(info.get("local_type"),
                                                info.get("local_type"))))
                continue
            try:
                path = self._try_download(info["user"], info["local_id"],
                                          info.get("local_type", 49),
                                          info.get("shard"))
            except Exception as e:
                wxlog.debug("待办复查异常 %s/%s：%s: %s"
                            % (info["user"], info["local_id"],
                               type(e).__name__, str(e)[:120]))
                path = None
            if path:
                self._settle(key, info.get("local_type"), "ok", path=path)
                wxlog.info("待办复查拿到文件：%s", path)
            else:
                with self._pending_lock:
                    if key in self._pending:
                        self._pending[key]["attempts"] = \
                            int(self._pending[key].get("attempts", 1)) + 1

    # ------------------------------------------------------------------
    # 状态账本
    # ------------------------------------------------------------------
    def _settle(self, key: Tuple, mt: Optional[int], status: str,
                path: Optional[str] = None, error: Optional[str] = None) -> None:
        user, local_id, shard = key
        with self._pending_lock:
            info = self._pending.pop(key, None)
        record = {
            "user": user, "local_id": local_id, "shard": shard,
            "type": TYPE_NAMES.get(mt, mt), "status": status,
            "path": path, "error": error, "ts": time.time(),
        }
        with self._state_lock:
            self._state.setdefault("results", []).append(record)
        self._write_state()
        if self.on_result:
            try:
                self.on_result(record)
            except Exception:
                pass

    def _load_state(self) -> None:
        path = self.state_file
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._state["results"] = list(data.get("results") or [])
                # 历史待办不再自动恢复（微信侧下载状态未知），只保留账目
                self._state["pending"] = []
        except Exception:
            pass

    def _write_state(self, force: bool = False) -> None:
        now = time.time()
        with self._state_lock:
            payload = json.dumps(self._state, ensure_ascii=False, indent=1)
        if not force and now - self._last_state_write < 2.0:
            return
        self._last_state_write = now
        try:
            tmp = self.state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self.state_file)
        except OSError:
            pass


def _main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m wechatauto.auto_download",
        description="监听微信新消息并自动下载文件（只读 DB 轮询，不驱动界面）")
    ap.add_argument("--chat", action="append", default=[],
                    help="只监听指定会话（username 或显示名；可重复；缺省=全部会话）")
    ap.add_argument("--save", default=None, help="落盘根目录（默认 ~/Documents/wechatauto_auto）")
    ap.add_argument("--types", default="file",
                    help="逗号分隔：file,image,voice,video（默认 file）")
    ap.add_argument("--interval", type=float, default=2.0, help="DB 轮询间隔秒")
    ap.add_argument("--pending-timeout", type=float, default=1800.0,
                    help="文件未落盘的最长等待秒（默认 1800）")
    ap.add_argument("--flat", action="store_true", help="不按会话分子目录")
    args = ap.parse_args(argv)

    types = tuple(TYPE_IDS[t.strip()] for t in args.types.split(",") if t.strip())

    from .db import WeChatDB
    db = WeChatDB()
    auto = AutoFileDownloader(db,
                              users=args.chat or None,
                              save_dir=args.save,
                              types=types,
                              per_chat=not args.flat,
                              interval=args.interval,
                              pending_timeout=args.pending_timeout)
    print("自动下载已启动：save=%s types=%s chats=%s（Ctrl+C 退出）"
          % (auto.save_dir, sorted(TYPE_NAMES[t] for t in auto.types),
             args.chat or "全部会话"))
    auto.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
