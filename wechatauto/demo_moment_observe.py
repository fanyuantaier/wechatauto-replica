# -*- coding: utf-8 -*-
"""演示：观察即固化 —— 把易失的朋友圈缓存转成持久文件 + 映射。

用法::

    python -m wechatauto.demo_moment_observe                 # 滚动 12 轮，最多固化 3 张
    python -m wechatauto.demo_moment_observe --steps 20 --target 10
    python -m wechatauto.demo_moment_observe --out D:\\sns_export

说明：
- 会真实滚动微信朋友圈时间线，触发缩略图下载，然后立刻解密并复制到持久目录
  （默认 ``~/Documents/wechatauto_moments/_cache_export/<tid|unknown>/``）。
- 幂等：同一张图重复捕获不会重复导出（同 key 同内容跳过；跨 key 同内容记别名）。
- 缓存是易失的，所以「捕获即落盘」是这套流程的重点。
"""
from __future__ import annotations

import argparse
import os

from wechatauto.moment_observer import MomentObserver


def main() -> None:
    ap = argparse.ArgumentParser(description="朋友圈缓存观察即固化")
    ap.add_argument("--steps", type=int, default=12, help="最大滚动轮数")
    ap.add_argument("--target", type=int, default=3, help="累计固化数量达到即停止（0=不限）")
    ap.add_argument("--delta", type=int, default=-200, help="每轮滚动像素")
    ap.add_argument("--settle", type=float, default=2.5, help="每轮等待下载秒数")
    ap.add_argument("--nickname", default="", help="写入映射的归属昵称（缺省自动猜）")
    ap.add_argument("--tid", type=int, default=None, help="写入映射的动态 tid")
    ap.add_argument("--out", default=None, help="持久导出根目录")
    args = ap.parse_args()

    obs = MomentObserver(export_root=args.out)
    print(f"导出根目录: {obs.export_root}")
    print(f"映射文件  : {obs.mapping_path}")

    res = obs.capture(steps=args.steps, target=args.target, delta=args.delta,
                      settle=args.settle, nickname=args.nickname, tid=args.tid)

    print(f"\n轮数: {res['steps_run']}")
    print(f"固化: {len(res['exported'])}  跳过(已存在): {len(res['skipped'])}  失败: {len(res['failed'])}")
    for r in res["exported"]:
        print("  +", os.path.basename(r.get("path", "")), "sha=%s" % r.get("sha256", "")[:16])
    for r in res["skipped"]:
        print("  =", os.path.basename(r.get("path", "")), "(幂等跳过)")

    mapping = obs.mapping()
    print(f"\n映射条目: {len(mapping)}")
    for k, v in list(mapping.items())[:5]:
        if isinstance(v, dict):
            print("  %s -> %s nick=%s" % (k[:16], os.path.basename(v.get("path", "")),
                                          v.get("nickname")))


if __name__ == "__main__":
    main()
