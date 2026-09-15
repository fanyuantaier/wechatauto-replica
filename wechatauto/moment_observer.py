# -*- coding: utf-8 -*-
"""朋友圈缓存观察器（``MomentObserver``）——「观察即固化」。

背景
----

微信把朋友圈媒体缓存在 ``cache/<月>/Sns/{Img,Video}/<2hex>/<30hex>``：目录 2 位
加文件名 30 位组成一个 32 位十六进制 **不透明会话 key**。该 key 与 feed 里的
``<url md5=...>`` 之间**不存在可代数推导的映射**（已穷尽 md5/sha 与全字段组合），
但它是一次性的「谁是谁」的唯一线索；同时缓存是**易失**的（微信会清理）。

因此正确用法不是把它当数据库，而是**观察即固化**：

1. :meth:`MomentObserver.snapshot` / :meth:`MomentObserver.diff` —— 快照与轮询
2. :meth:`MomentObserver.export` —— 立即解密并复制到持久目录，写映射条目
   （幂等：同 key 同内容跳过；跨 key 同内容记别名）
3. :meth:`MomentObserver.capture` —— 结合朋友圈界面滚动（或自定义动作）自动捕获

无 UI 部分只依赖 :class:`~wechatauto.moment.MomentDB`；带 UI 部分
（:meth:`visible_items` / :meth:`scroll` / :meth:`capture`）需要朋友圈界面。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

from wechatauto.logger import wxlog
from wechatauto.moment import MomentDB

__all__ = ["MomentObserver"]


class MomentObserver:
    """把易失的 Sns 缓存转成持久映射 + 解密字节。

    Args:
        db: 可选的 :class:`~wechatauto.db.WeChatDB`；缺省时按需创建。
        moment: 可选的 :class:`~wechatauto.moment.Moment`（朋友圈界面对象）；
            缺省时在首次使用 UI 方法时按需创建。
        export_root: 持久导出根目录，缺省 ``~/Documents/wechatauto_moments/_cache_export``。
        mapping_path: 映射 JSON 路径，缺省 ``<export_root>/mapping.json``。
    """

    def __init__(self, db=None, moment=None, export_root: Optional[str] = None,
                 mapping_path: Optional[str] = None) -> None:
        self._db = db
        self._mdb = MomentDB(db) if db is not None else None
        self._moment = moment
        self.export_root = export_root or MomentDB._export_root()
        self.mapping_path = mapping_path or os.path.join(self.export_root, "mapping.json")

    # ------------------------------------------------------------------
    # 惰性依赖
    # ------------------------------------------------------------------
    @property
    def db(self):
        """底层 :class:`WeChatDB`（首次访问时创建）。"""
        if self._db is None:
            from wechatauto.db import WeChatDB
            self._db = WeChatDB()
        return self._db

    @property
    def mdb(self) -> MomentDB:
        """底层 :class:`MomentDB`（首次访问时创建）。"""
        if self._mdb is None:
            self._mdb = MomentDB(self.db)
        return self._mdb

    @property
    def moment(self):
        """朋友圈界面对象（首次访问时创建）。"""
        if self._moment is None:
            from wechatauto.wx import WeChat
            self._moment = WeChat().Moment
        return self._moment

    # ------------------------------------------------------------------
    # 缓存快照 / 差分（无需 UI）
    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[tuple, str]:
        """快照当前全部 Sns 缓存 key → 路径。"""
        return self.mdb.snapshot_cache_keys()

    def diff(self, before: Dict[tuple, str]) -> Dict[tuple, str]:
        """相对基线 ``before`` 的新增 key → 路径。"""
        return self.mdb.diff_cache_keys(before)

    def poll(self, before: Dict[tuple, str], interval: float = 0.3,
             timeout: float = 15.0) -> Dict[tuple, str]:
        """轮询新增 key（点击/滚动触发下载后调用）。"""
        return self.mdb.poll_new_cache_keys(before, poll_interval=interval,
                                            timeout=timeout)

    # ------------------------------------------------------------------
    # 固化导出（无需 UI）
    # ------------------------------------------------------------------
    def mapping(self) -> Dict[str, Any]:
        """读取当前映射表（不存在时返回空 dict）。"""
        if not os.path.isfile(self.mapping_path):
            return {}
        try:
            with open(self.mapping_path, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except (OSError, ValueError) as e:
            wxlog.debug(f'读取映射失败：{e}')
            return {}

    def export(self, key: tuple, path: str, tid: Optional[int] = None,
               username: str = "", nickname: str = "",
               create_time: int = 0, media_index: int = 0,
               kind: str = "image") -> dict:
        """解密单个缓存 key 并固化到 ``<export_root>/<tid|unknown>/``。

        幂等由底层 :meth:`MomentDB.export_cache_key` 保证。
        """
        group = str(tid) if tid else "unknown"
        group_dir = os.path.join(self.export_root, group)
        for d in (group_dir, os.path.dirname(self.mapping_path)):
            if not d:
                continue
            try:
                os.makedirs(d, exist_ok=True)
            except OSError as e:
                return {"status": "error", "reason": f"mkdir: {e}"}
        return self.mdb.export_cache_key(
            key, path, export_dir=group_dir, tid=tid, username=username,
            nickname=nickname, create_time=create_time,
            media_index=media_index, kind=kind,
            mapping_path=self.mapping_path)

    def export_new(self, new_keys: Dict[tuple, str], **meta) -> List[dict]:
        """把 ``diff``/``poll`` 得到的新增 key 全部固化导出。"""
        return [self.export(k, p, **meta) for k, p in sorted(new_keys.items())]

    # ------------------------------------------------------------------
    # UI 观察（需要朋友圈界面）
    # ------------------------------------------------------------------
    def visible_items(self, refresh: bool = True, max_items: int = 30) -> List[dict]:
        """读取当前屏幕可见的朋友圈条目（发布者 + 文本片段）。

        评论文案会混进 UIA 名称里，这里做粗过滤（同 ``observe`` 脚本的启发式）。
        """
        out: List[dict] = []
        try:
            items = self.moment.GetMoments(refresh=refresh)
        except Exception as e:
            wxlog.debug(f'读取可见朋友圈失败：{e}')
            return out
        for it in (items or [])[:max_items]:
            try:
                raw = (it.control.Name or "").replace("\n", " | ")
            except Exception:
                raw = ""
            parts = [p.strip() for p in raw.split(" | ")
                     if p.strip() and "评论" not in p and "余下" not in p]
            out.append({
                "publisher": parts[0][:30] if parts else "",
                "text": " | ".join(parts[1:])[:200],
                "raw": raw[:200],
            })
        return out

    def scroll(self, delta: int = -200, times: int = 1, settle: float = 2.5) -> None:
        """滚动时间线并等待缩略图下载（``settle`` 秒）。"""
        self.moment._scroll(delta=delta, times=times)
        if settle and settle > 0:
            time.sleep(settle)

    def capture(self, steps: int = 12, delta: int = -200, settle: float = 2.5,
                target: int = 3, before: Optional[Dict[tuple, str]] = None,
                action: Optional[Callable[[int], Any]] = None,
                nickname: str = "", tid: Optional[int] = None,
                default_kind: str = "image",
                read_screen: bool = True) -> dict:
        """一次完整观察闭环：滚动/动作 → 轮询新增 → 立即固化。

        Args:
            steps: 最大轮数。
            delta: 每轮滚动像素（默认向上滚 200）。
            settle: 每轮滚动后等待下载的秒数。
            target: 累计成功导出达到该数量即提前结束（``0`` 表示不设上限）。
            before: 起始快照；缺省现场采集。
            action: 自定义「触发下载」动作 ``action(step)``；缺省为滚动时间线。
                需要点击开大图时，可传入一个执行点击的 callable。
            nickname / tid: 写入映射的归属信息；缺省时尝试从可见条目猜发布者。
            default_kind: 新增 key 默认归属的媒体类型。
            read_screen: 是否读取屏幕可见条目来推断发布者（无界面时设 False）。

        Returns:
            dict: ``{"exported", "skipped", "failed", "steps_run", "mapping_path"}``。
        """
        baseline = before if before is not None else self.snapshot()
        exported: List[dict] = []
        skipped: List[dict] = []
        failed: List[dict] = []
        steps_run = 0

        for step in range(max(1, steps)):
            steps_run = step + 1
            if action is not None:
                action(step)
            else:
                self.scroll(delta=delta, times=1, settle=settle)

            new = self.diff(baseline)
            if not new:
                continue
            nick = nickname
            if not nick and read_screen:
                for item in self.visible_items():
                    if item.get("publisher"):
                        nick = item["publisher"]
                        break
            results = self.export_new(new, tid=tid, nickname=nick,
                                      default_kind=default_kind)
            for r in results:
                status = r.get("status")
                if status == "ok":
                    exported.append(r)
                elif status == "skip":
                    skipped.append(r)
                else:
                    failed.append(r)
                    wxlog.debug(f'固化缓存失败：{r}')
            baseline = self.snapshot()

            if target and len(exported) >= target:
                break

        if failed:
            wxlog.info(f'观察闭环：成功={len(exported)} 跳过={len(skipped)} 失败={len(failed)}')
        return {
            "exported": exported,
            "skipped": skipped,
            "failed": failed,
            "steps_run": steps_run,
            "mapping_path": self.mapping_path,
        }

    def observe(self, **kwargs) -> dict:
        """``capture`` 的别名（语义更贴近「观察即固化」）。"""
        return self.capture(**kwargs)
