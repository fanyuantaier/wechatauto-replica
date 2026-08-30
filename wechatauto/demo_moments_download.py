# -*- coding: utf-8 -*-
"""wechatauto 演示：下载朋友圈（Moments）中的图片与视频。

从本地 sns.db 读取朋友圈动态，把每条动态的图片/视频下载（落地）到本地目录。
优先从微信本地缓存复制（秒级、离线），缓存缺失时回退到 CDN URL 下载。

用法：
    python demo_moments_download.py [N] [--out 目录] [--only-video]
                                    [--list] [--open]

参数：
    N          要处理的最新 N 条动态（默认 5）
    --out      保存根目录（默认 ~/Documents/wechatauto_moments）
    --only-video  只下载视频
    --list     只列出各条动态的媒体清单，不下载
    --open     下载完成后用系统默认程序打开第一个文件

例：
    python demo_moments_download.py 5
    python demo_moments_download.py 10 --only-video
    python demo_moments_download.py 3 --out D:\\moments
    python demo_moments_download.py --list 50

说明：
    每条动态会保存到 <out>/<动态id>/ 子目录，图片和视频分开落盘。
    微信本地朋友圈缓存是否为可读文件与版本/设置有关；当本地缓存
    命中时直接复制（无需联网），否则从 CDN URL 拉取。
"""

from __future__ import annotations

import argparse
import os
import subprocess
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

from wechatauto import WeChatDB, MomentDB


def fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n}B"


def media_summary(feed: dict) -> str:
    parts = []
    if feed.get("images"):
        parts.append(f"{len(feed['images'])} 图")
    if feed.get("videos"):
        parts.append(f"{len(feed['videos'])} 视频")
    return "、".join(parts) or "无媒体"


def main() -> None:
    ap = argparse.ArgumentParser(description="下载朋友圈图片/视频")
    ap.add_argument("n", nargs="?", type=int, default=5, help="最新 N 条动态（默认 5）")
    ap.add_argument("--out", default=None, help="保存根目录")
    ap.add_argument("--only-video", action="store_true", help="只下载视频")
    ap.add_argument("--list", action="store_true", help="只列媒体清单不下载")
    ap.add_argument("--open", action="store_true", help="下载后打开第一个文件")
    args = ap.parse_args()

    save = args.out or os.path.join(os.path.expanduser("~"), "Documents", "wechatauto_moments")
    db = WeChatDB()
    md = MomentDB(db)
    print(f"账号: {db.account}")
    print(f"保存目录: {save}")

    t0 = time.time()
    feeds = md.get_moments(limit=args.n)
    # 只保留带媒体的动态，避免空转
    feeds = [f for f in feeds if f.get("images") or f.get("videos")]
    print(f"最新 {args.n} 条中带图/视频的动态: {len(feeds)}\n")

    total_ok = total_fail = 0
    first_saved = None
    for idx, feed in enumerate(feeds, 1):
        text = (feed.get("text") or "").replace("\n", " ").strip()[:24]
        who = feed.get("nickname") or feed.get("username") or "?"
        print(f"[{idx}] {who}: {text or '(无文字)'}  ->  {media_summary(feed)}")

        if args.list:
            for im in (feed.get("images") or []):
                print(f"      图 md5={im.get('md5')}")
            for v in (feed.get("videos") or []):
                print(f"      视频 md5={v.get('md5')}")
            continue

        files = md.download_moment_media(
            feed,
            save_dir=save,
            images=not args.only_video,
            videos=True,
            make_subdirs=False,
        )
        expect = len(feed.get("videos") or [])
        if not args.only_video:
            expect += len(feed.get("images") or [])
        for p in files:
            if os.path.exists(p):
                total_ok += 1
                print(f"      OK  {fmt_size(os.path.getsize(p)):>10}  {p}")
                if first_saved is None:
                    first_saved = p
            else:
                total_fail += 1
                print(f"      FAIL  {p}")
        if not files:
            print(f"      （{expect} 项媒体均未能落地：图片本地缓存为加密格式、"
                  f"公众号 CDN url 需联网且部分受限。视频若未在本地明文缓存中则同样需联网）")
        print()

    print(f"完成，耗时 {time.time() - t0:.1f}s；成功 {total_ok}，失败 {total_fail}。")
    if args.open and first_saved:
        try:
            subprocess.Popen(["cmd", "/c", "start", "", first_saved], shell=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
