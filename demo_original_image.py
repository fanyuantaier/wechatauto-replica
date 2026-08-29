# -*- coding: utf-8 -*-
"""原图下载示例脚本

演示如何通过UI自动化触发微信下载原图。

用法：
    python demo_original_image.py                    # 下载最近一张图片的原图
    python demo_original_image.py 群名 --count 5     # 下载指定会话最近5张图片
"""

import argparse
import sys
import time

from wechatauto import WeChatDB, MediaDownloader


def main():
    parser = argparse.ArgumentParser(description="原图下载示例")
    parser.add_argument("chat", nargs="?", default="送你挖银子", help="会话名称/昵称（默认：文件传输助手）")
    parser.add_argument("--count", type=int, default=1, help="下载图片数量（默认：1）")
    parser.add_argument("--timeout", type=float, default=30, help="每张图片等待超时（秒，默认：30）")
    parser.add_argument("--save-dir", default=None, help="保存目录（默认：~/Documents/wechatauto_media）")
    args = parser.parse_args()

    db = WeChatDB()
    md = MediaDownloader(db, save_dir=args.save_dir)

    # 查找会话（支持昵称和wxid）
    chat_name = args.chat
    contact = db.search_contact(chat_name)
    if contact:
        chat_name = contact[0].get("username", chat_name)
        print(f"找到会话: {contact[0].get('nickname', chat_name)} ({chat_name})")
    else:
        print(f"未找到联系人 '{args.chat}'，尝试直接使用名称...")

    # 获取最新消息，筛选图片消息
    print(f"正在查找 {chat_name} 中的最新图片消息...")
    messages = db.get_messages(chat_name, limit=100)  # 获取最近100条消息
    image_ids = [m["local_id"] for m in messages if m["type"] == "图片"]

    if not image_ids:
        print(f"未找到 {chat_name} 中的图片消息")
        return

    # 取最新的N张（已经是降序，取前N个）
    image_ids = image_ids[:args.count]
    print(f"找到 {len(image_ids)} 张图片，开始下载原图...")

    success = 0
    for i, local_id in enumerate(image_ids, 1):
        print(f"\n[{i}/{len(image_ids)}] 下载图片 local_id={local_id}...")

        # 先尝试直接下载（如果原图已存在且大于100KB）
        import os
        out = md.download_image(chat_name, local_id, save_dir=args.save_dir)
        if out and "_thumb" not in out and os.path.getsize(out) > 102400:
            print(f"  ✓ 图片已存在: {out}")
            success += 1
            continue

        # 原图不存在，通过UI点击触发下载
        print(f"  原图不存在，通过UI点击触发下载...")
        out = md.download_image_original(
            chat_name, local_id,
            save_dir=args.save_dir,
            timeout=args.timeout,
            chat_name=args.chat
        )
        print(out)
        if out:
            if "_thumb" not in out:
                print(f"  ✓ 原图下载成功: {out}")
                success += 1
            else:
                print(f"  ⚠ 下载的是缩略图: {out}")
        else:
            print(f"  ✗ 下载失败")

    print(f"\n完成: {success}/{len(image_ids)} 张原图下载成功")


if __name__ == "__main__":
    main()
