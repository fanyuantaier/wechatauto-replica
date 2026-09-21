# -*- coding: utf-8 -*-
"""wechatauto 演示：读取并下载会话中的媒体文件（图片/语音/视频/文件）。

从微信本地数据库直接取媒体消息并解密落地，无需 GUI 操作。

用法：
    python demo_media.py [会话] [--out 目录] [--photos N]
                         [--limit N] [--ids 3,5,7] [--filter 图片,文件]
                         [--list] [--open]

默认行为（不带任何选项）：
    下载文件传输助手里最近 N 张真实照片（jpg/png，自动跳过表情）和所有视频。

参数：
    会话    会话名（昵称/备注/username），默认「文件传输助手」
    --photos 默认模式下下载照片张数（默认 3）
    --limit 列出最近 N 条消息（默认 200）
    --ids   仅下载指定 local_id（逗号分隔，此时忽略 --limit）
    --filter 只处理指定类型，逗号分隔，可选 图片/语音/视频/文件
    --out   下载保存目录（默认 ~/Documents/wechatauto_media）
    --list  仅列出媒体消息，不下载
    --open  下载完成后用系统默认程序打开（仅单文件时有效）

例：
    python demo_media.py                          # 最近 3 张照片+视频
    python demo_media.py 兔仔仔 --photos 5        # 指定会话和数量
    python demo_media.py 文件传输助手 --list --limit 30
    python demo_media.py 我的群 --ids 105,107,109 --out D:\\media
    python demo_media.py 文件传输助手 --filter 文件 --open

原理：
    WeChatDB.get_messages 列出消息（local_type 识别媒体类型），
    MediaDownloader.download_media 按类型自动分发：图片解密 .dat、
    语音 SILK 提取、视频/文件从缓存目录复制。
"""

from __future__ import annotations

import argparse
import os
import re
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

from wechatauto import WeChatDB, MediaDownloader

MEDIA_TYPES = {"图片": 3, "语音": 34, "视频": 43, "文件": 49}


def resolve_target(db: WeChatDB, raw: str) -> str:
    hits = db.search_contact(raw)
    return hits[0]["username"] if hits else raw


def fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n}B"


def _xml_attr(content: str, name: str) -> str:
    i = content.find(' %s="' % name)
    if i < 0:
        return ""
    j = content.find('"', i + len(name) + 3)
    return content[i + len(name) + 3:j] if j > 0 else ""


def _xml_text(content: str, tag: str) -> str:
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), content, re.S)
    return m.group(1).strip() if m else ""


def media_label(mtype: str, content: str) -> str:
    """媒体消息的 content 是 XML，只取能看懂的那几个字段。

    图片/视频的 XML 里带 aeskey、md5、CDN 链接，原样打印既占屏又把密钥类
    字段显示出来，所以这里一律只回尺寸/字节数/时长/文件名。
    """
    c = content or ""
    if mtype == "图片":
        n = _xml_attr(c, "length")
        hd = _xml_attr(c, "hdlength")
        txt = "图片 " + (fmt_size(int(n)) if n.isdigit() else "?")
        if hd.isdigit() and int(hd) > 0:
            txt += f"（原图 {fmt_size(int(hd))}）"
        return txt
    if mtype == "视频":
        n = _xml_attr(c, "playlength")
        size = _xml_attr(c, "length")
        txt = "视频 " + (f"{int(n) // 60}:{int(n) % 60:02d}" if n.isdigit() else "?")
        if size.isdigit():
            txt += f" {fmt_size(int(size))}"
        return txt
    if mtype == "语音":
        ms = _xml_attr(c, "voicelength")
        return "语音 " + (f"{int(ms) / 1000:.0f}s" if ms.isdigit() else "?")
    if mtype == "文件":
        return "文件 " + (_xml_text(c, "title") or "?")[:40]
    return mtype


def media_failure_reason(md, who: str, local_id: int) -> str:
    """下载失败时给出细分原因（表情容器 / 未落地 / 不支持）。

    图片消息本地缓存的 .dat 解密后若为 wxgf（微信动画表情容器），
    库会按设计不落盘；本函数复现该判定以区分「表情」与「真失败」。
    """
    row = md.db.get_message_row(who, local_id)
    if not row:
        return "消息不存在"
    t = row["local_type"]
    if t == 3:  # 图片
        md5 = md._img_md5(row)
        dat = md._find_dat(who, md5, row["create_time"]) if md5 else None
        if not dat:
            return "本地无 .dat 缓存（未在微信查看过/已清理）"
        try:
            data = md.decrypt_image(dat)
        except Exception as e:
            return f"解密失败：{str(e)[:40]}"
        if data[:4] == b"wxgf":
            return "微信动画表情容器（wxgf）"
        return "未知图片格式"
    if t in (34, 43, 49):
        return "本地缓存未落地或文件已清理"
    return "类型不支持"


def main():
    parser = argparse.ArgumentParser(
        description="wechatauto 演示：读取/下载微信会话中的媒体文件",
        add_help=False)
    parser.add_argument("target", nargs="?", default="文件传输助手",
                        help="会话名（昵称/备注/username），默认文件传输助手")
    parser.add_argument("--limit", type=int, default=1000, help="列出最近 N 条（默认 200）")
    parser.add_argument("--photos", type=int, default=10, help="默认模式下载照片张数（默认 3）")
    parser.add_argument("--ids", default=None,
                        help="仅下载指定 local_id，逗号分隔，如 105,107")
    parser.add_argument("--filter", default=None,
                        help="仅处理指定类型，逗号分隔：图片/语音/视频/文件")
    parser.add_argument("--out", default=None, help="下载保存目录")
    parser.add_argument("--image-key", default=None,
                        help="图片 AES 密钥（16 位，若内存扫描不可用）")
    parser.add_argument("--list", action="store_true", help="仅列出媒体消息，不下载")
    parser.add_argument("--open", action="store_true",
                        help="下载后用系统默认程序打开（仅单文件）")
    parser.add_argument("-h", "--help", action="help")
    args = parser.parse_args()

    filt = None
    if args.filter:
        filt = set(t.strip() for t in args.filter.split(",") if t.strip())
        unknown = [t for t in filt if t not in MEDIA_TYPES]
        if unknown:
            print(f"未知类型：{', '.join(unknown)}（可选：图片/语音/视频/文件）")
            sys.exit(1)

    print("=" * 60)
    print("wechatauto 媒体读取演示")
    print("=" * 60)

    db = WeChatDB()
    info = db.get_self_info()
    print(f"账号：{info.get('nick_name') or info.get('username')}")
    who = resolve_target(db, args.target)
    print(f"会话：{args.target} -> {who}")

    # ---- 指定 local_id：直接下载 ----
    if args.ids:
        ids = [int(x.strip()) for x in args.ids.split(",") if x.strip().isdigit()]
        if not ids:
            print("--ids 无效")
            sys.exit(1)
        md = MediaDownloader(db, save_dir=args.out, image_key=args.image_key)
        for lid in ids:
            row = db.get_message_row(who, lid)
            if not row:
                print(f"  local_id={lid} 未找到，跳过")
                continue
            name = next((k for k, v in MEDIA_TYPES.items() if v == row["local_type"]), "")
            t = time.strftime("%m-%d %H:%M", time.localtime(row["create_time"]))
            label = name or ("类型%d" % row["local_type"])
            print(f"\n[local_id={lid}] {t} {label}")
            out = md.download_media(who, lid, args.out)
            if out:
                print(f"  ✅ 已保存：{out} ({fmt_size(os.path.getsize(out))})")
                if args.open:
                    os.startfile(out)
            else:
                print(f"  ⚠️ 下载失败：{media_failure_reason(md, who, lid)}")
        print("\n完成。")
        sys.exit(0)

    # ---- 列出最近消息并筛选媒体 ----
    mode = "默认照片+视频" if filt is None else "+".join(sorted(filt))
    msgs = db.get_messages(who, limit=args.limit)
    media_msgs = []
    print(f"\n最近 {len(msgs)} 条消息中的媒体项（模式：{mode}）：")
    for m in reversed(msgs):
        name = MEDIA_TYPES.get(m.get("type"), "")
        if not name:
            continue
        if filt and m.get("type") not in filt:
            continue
        if filt is None and m.get("type") not in ("图片", "视频"):
            continue
        t = time.strftime("%m-%d %H:%M", time.localtime(m["create_time"]))
        sender = "我" if m["sender_id"] == 2 else "对方"
        media_msgs.append(m)
        label = media_label(m.get("type"), m.get("content", ""))
        print(f"  {m['local_id']:<6} {t} {sender} {label}")

    if args.list:
        print(f"\n共 {len(media_msgs)} 条媒体消息（用 --ids 或 --filter 配合下载）。")
        sys.exit(0)

    if not media_msgs:
        print("\n没有符合条件的媒体消息。")
        sys.exit(0)

    # ---- 下载筛选出的媒体 ----
    md = MediaDownloader(db, save_dir=args.out, image_key=args.image_key)
    ok = skip = fail = 0
    photo_done = 0
    for m in media_msgs:
        t = time.strftime("%H:%M:%S", time.localtime(m["create_time"]))
        print(f"\n[local_id={m['local_id']}] {t} {m.get('type')}")
        is_photo_target = filt is None and m.get("type") == "图片"
        if is_photo_target and photo_done >= args.photos:
            print(f"  ➖ 已达照片上限 {args.photos} 张，停止")
            break
        out = md.download_media(who, m["local_id"], args.out)
        if out:
            print(f"  ✅ {fmt_size(os.path.getsize(out))}  {out}")
            ok += 1
            if is_photo_target:
                photo_done += 1
        else:
            reason = media_failure_reason(md, who, m["local_id"])
            if "表情容器" in reason:
                print(f"  ➖ {reason}")
                skip += 1
            else:
                print(f"  ⚠️ 下载失败：{reason}")
                fail += 1

    print(f"\n完成：成功 {ok} 项，跳过 {skip} 项（表情），失败 {fail} 项。"
          + (f" 保存目录：{os.path.abspath(args.out or md.save_dir)}" if ok else ""))
    if args.open and ok == 1:
        out = md.download_media(who, media_msgs[0]["local_id"], args.out)
        if out:
            os.startfile(out)
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
