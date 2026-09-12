# -*- coding: utf-8 -*-
"""wechatauto 演示：读取并识别固定群的最新消息（含红包 ZSTD 解析）。

从微信本地数据库直接读取指定群的最新消息，识别每条消息的类型、
发送者昵称与内容；红包消息自动解压 ZSTD 并提取祝福语/红包类型。

用法：
    python demo_group_messages.py [群名关键词]
                                 [--limit N] [--list-groups]
                                 [--watch] [--red-only] [--sleep 秒]

参数：
    群名关键词  匹配群名（模糊匹配），默认列出所有群后取第一个
    --list-groups   仅列出所有群（名称 + wxid + 消息数），不读消息
    --limit     读取最新 N 条消息（默认 20）
    --watch     轮询模式：每 --sleep 秒读取一次新消息（增量，按 sort_seq）
    --sleep     轮询间隔秒数（默认 3）
    --red-only  只显示红包消息

例：
    python demo_group_messages.py --list-groups
    python demo_group_messages.py 家长群
    python demo_group_messages.py 家长群 --limit 30
    python demo_group_messages.py 家长群 --watch --red-only
    python demo_group_messages.py 家长群 --watch --sleep 5

原理：
    contact.db 存群（username 含 @chatroom）与成员（wxid → nick_name/remark）；
    message_*.db 按 Md5(群wxid) 建表存消息，群文本消息的 message_content 形如
    "wxid: 内容"，发送者 wxid 藏在内容前缀中（4.x 群消息 sender_id 不可靠）。
    红包卡片 local_type=0x7D100000031，message_content 为「容器头 + ZSTD 压缩
    XML」，解压后可读到祝福语、红包类型(sceneid)、sendid 等。
"""

from __future__ import annotations

import argparse
import hashlib
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

from wechatauto import WeChatDB

# 红包卡片 local_type = 0x7D100000031
RED_PACKET_TYPE = 0x7D100000031

TYPE_LABEL = {
    1: "文本",
    3: "图片",
    34: "语音",
    43: "视频",
    47: "动画表情",
    48: "位置",
    49: "文件/链接/卡片",
    50: "音视频通话",
    10000: "系统消息",
    11000: "动画表情",
}


def load_nickname_map(db: WeChatDB) -> dict:
    """wxid -> 昵称/备注"""
    mapping = {}
    for rel, path, _ in db._db_files:
        if os.path.basename(path) != "contact.db":
            continue
        conn = db._open(rel)
        try:
            rows = conn.execute(
                "SELECT username, nick_name, remark FROM contact"
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            mapping[r[0]] = r[2] or r[1] or r[0]
        break
    return mapping


def list_groups(db: WeChatDB) -> list:
    """列出所有群（名称 + wxid + 消息数），按消息数倒序"""
    groups = []
    for rel, path, _ in db._db_files:
        if os.path.basename(path) != "contact.db":
            continue
        conn = db._open(rel)
        try:
            rows = conn.execute(
                "SELECT username, nick_name, remark FROM contact "
                "WHERE username LIKE '%@chatroom'"
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            groups.append({"name": r[2] or r[1] or r[0], "wxid": r[0]})
        break
    for g in groups:
        g["count"] = _chat_message_count(db, g["wxid"])
    groups.sort(key=lambda x: -x["count"])
    return groups


def _chat_message_count(db: WeChatDB, wxid: str) -> int:
    md5hex = hashlib.md5(wxid.encode()).hexdigest()
    total = 0
    for rel in db._message_dbs():
        conn = db._open(rel)
        try:
            tabs = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for tab in tabs:
                if tab == "Msg_%s" % md5hex:
                    total += conn.execute(
                        "SELECT COUNT(*) FROM %s" % tab).fetchone()[0]
        finally:
            conn.close()
    return total


def find_group(db: WeChatDB, keyword: str):
    kw = keyword.strip()
    groups = list_groups(db)
    if not kw:
        return groups[0] if groups else None
    exact = [g for g in groups if g["name"] == kw]
    if exact:
        return exact[0]
    fuzzy = [g for g in groups if kw in g["name"]]
    return fuzzy[0] if fuzzy else None


def _raw_messages(db: WeChatDB, wxid: str, limit: int):
    """批量读取该会话最新消息原始行（保留 message_content 二进制），升序返回。

    4.x 会把同一会话的表拆分在多个 message_*.db 中（历史遗留），
    因此需跨库聚合后按 sort_seq 排序取最新 limit 条。
    """
    md5hex = hashlib.md5(wxid.encode()).hexdigest()
    tab = "Msg_%s" % md5hex
    all_rows = []
    for rel in db._message_dbs():
        conn = db._open(rel)
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (tab,)).fetchone()
            if not exists:
                continue
            cur = conn.execute(
                "SELECT local_id, local_type, real_sender_id, create_time, "
                "message_content, sort_seq FROM %s" % tab)
            cols = [c[0] for c in cur.description]
            all_rows.extend(dict(zip(cols, r)) for r in cur.fetchall())
        finally:
            conn.close()
    all_rows.sort(key=lambda r: r.get("sort_seq") or 0)
    return all_rows[-limit:]


def zstd_decompress(content: bytes, max_size: int = 200000) -> str:
    """解 ZSTD：微信 4.x 卡片/红包 message_content 为 ZSTD 压缩 XML"""
    if not content:
        return ""
    if isinstance(content, bytes):
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            pass
        try:
            import zstandard as zstd
            dctx = zstd.ZstdDecompressor()
            return dctx.decompress(content, max_output_size=max_size).decode(
                "utf-8", "ignore")
        except ImportError:
            return ""
        except Exception:
            return ""
    return str(content)


def parse_red_packet(xml: str) -> dict:
    """从红包 XML 提取关键字段"""
    info = {}
    if "wcpayinfo" not in xml:
        return info

    def cdata(tag):
        m = re.search(r"<%s><!\[CDATA\[(.*?)\]\]></%s>" % (tag, tag), xml, re.S)
        return m.group(1).strip() if m else ""

    info["des"] = cdata("des")
    info["receivertitle"] = cdata("receivertitle")
    info["sendertitle"] = cdata("sendertitle")
    info["sceneid"] = cdata("sceneid")
    info["paymsgid"] = cdata("paymsgid")
    info["fromusername"] = cdata("fromusername")
    m = re.search(r"<type><!\[CDATA\[(\d+)\]\]>", xml)
    info["type"] = m.group(1) if m else ""
    m = re.search(r"total_num=(\d+)", xml)
    info["total_num"] = m.group(1) if m else ""
    m = re.search(r"<invalidtime><!\[CDATA\[(\d+)\]\]>", xml)
    if m:
        info["invalidtime"] = int(m.group(1))
    return info


def parse_sender(content, sender_id, nickname_map) -> str:
    """解析群消息发送者昵称：
    1) 文本 content 前缀 "wxid: 内容"（4.x 群消息 sender 藏在这里）
    2) 兜底 real_sender_id（已不推荐）
    """
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
    else:
        text = content
    m = re.match(r"^(wxid_[0-9a-zA-Z_-]+|.*@chatroom):\s*", text)
    if m:
        wx = m.group(1)
        return nickname_map.get(wx, wx), m.end()
    return "", 0


def summarize_xml(text: str) -> str:
    """把系统消息 XML 压成一行可读摘要（如撤回提示）。"""
    if not text or not text.lstrip("\ufeff \t\r\n").startswith("<?xml"):
        return ""
    m = re.search(r'<sysmsg[^>]*type="([^"]+)"', text)
    if not m:
        return ""
    kind = m.group(1)
    c = re.search(r"<content>(.*?)</content>", text, re.S)
    body = (c.group(1).strip() if c else "")
    return "[%s] %s" % (kind, body) if body else "[%s]" % kind


def summarize_payload(text: str, label: str) -> str:
    """把卡片/表情 XML 压成一行可读摘要（便于 demo 输出）。"""
    if not isinstance(text, str):
        return text
    t = text.lstrip("\ufeff").strip()          # 容忍微信 XML 自带的 BOM
    if not t:
        return t
    one = lambda s: " ".join(
        re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s or "", flags=re.S).split())
    if "<appmsg" in t:
        title = re.search(r"<title>(.*?)</title>", t, re.S)
        des = re.search(r"<des>(.*?)</des>", t, re.S)
        parts = []
        if title:
            parts.append(one(title.group(1))[:60])
        if des:
            d = one(des.group(1))[:40]
            if d and d not in parts:
                parts.append(d)
        return " / ".join(parts) if parts else "[%s]" % label
    if "<emoji" in t:
        md5 = re.search(r'md5\s*=\s*"([0-9a-fA-F]{32})"', t)
        return "[动画表情%s]" % (" md5=" + md5.group(1)[:8] if md5 else "")
    return one(t)[:80]


def type_label(local_type) -> str:
    """类型名：兼容微信 4.x 复合 local_type（如 244813135921 = 57<<32 | 49）。"""
    if local_type == RED_PACKET_TYPE:
        return "红包"
    label = TYPE_LABEL.get(local_type)
    if label is None and isinstance(local_type, int):
        base = local_type & 0xFFFFFFFF          # 低 32 位才是真实类型（如 57<<32|49 → 49）
        label = TYPE_LABEL.get(base)
        if label is None:
            label = TYPE_LABEL.get(base & 0xFF)
    if label is None:
        return "类型%d" % local_type
    return label


def classify_message(row: dict, nickname_map: dict) -> dict:
    """识别单条消息：类型 + 发送者昵称 + 内容（bytes 一律先 ZSTD 解压）"""
    local_type = row.get("local_type")
    label = type_label(local_type)

    content = row.get("message_content")
    extra = {}
    cut = 0
    text = None

    # 4.x 的卡片/表情/系统消息 message_content 都是 ZSTD 压缩 XML：
    # 一律先解压再解析（此前只在红包分支解压，导致其它类型直接打印原始字节）
    if isinstance(content, bytes):
        text = zstd_decompress(content)

    if local_type == RED_PACKET_TYPE and text:
        extra = parse_red_packet(text)

    # 发送者：优先 content 前缀 "wxid_xxx: "，其次 XML 的 fromusername
    sender = ""
    if extra.get("fromusername"):
        fw = extra["fromusername"]
        sender = nickname_map.get(fw, fw)
    if not sender:
        sender, cut = parse_sender(text if text is not None else content,
                                   row.get("real_sender_id"), nickname_map)
    if not sender and text:
        m = re.search(r'fromusername\s*=\s*"([^"]+)"', text)
        if m:
            sender = nickname_map.get(m.group(1), m.group(1))
    if not sender:
        sender = "成员#%s" % row.get("real_sender_id")

    if text is not None:
        display = text
    elif isinstance(content, bytes):
        display = content
    else:
        display = str(content)

    # 系统消息 XML → 一行摘要（撤回/拍一拍等）；卡片/表情 XML → 标题摘要
    # 注：按内容标记判断而非前缀——微信 XML 常带 BOM，前缀会匹配不上
    if isinstance(display, str) and display:
        summary = summarize_xml(display)
        if summary:
            display = summary
        elif ("<appmsg" in display or "<emoji" in display
              or display.lstrip("\ufeff \t\r\n").startswith("<msg")):
            display = summarize_payload(display, label)
        elif cut:
            display = display[cut:]        # 去掉 "wxid_xxx: " 前缀（文本等）

    return {
        "local_id": row.get("local_id"),
        "type": label,
        "sender": sender,
        "create_time": row.get("create_time"),
        "sort_seq": row.get("sort_seq"),
        "content": display,
        "extra": extra,
    }


def print_message(m: dict) -> None:
    line = "[%s] %s %s | %s" % (
        m["type"], m["sender"], m["create_time"], str(m["content"])[:80])
    print(line)
    ex = m["extra"]
    if ex:
        parts = []
        if ex.get("des"):
            parts.append("描述: %s" % ex["des"])
        if ex.get("receivertitle"):
            parts.append("祝福语: %s" % ex["receivertitle"])
        if ex.get("sceneid"):
            parts.append("类型(sceneid): %s" % ex["sceneid"])
        if ex.get("total_num"):
            parts.append("个数: %s" % ex["total_num"])
        if ex.get("paymsgid"):
            parts.append("红包ID: %s" % ex["paymsgid"])
        if ex.get("fromusername"):
            parts.append("来源: %s" % ex["fromusername"])
        if parts:
            print("      " + "  |  ".join(parts))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="读取并识别固定群的最新消息（含红包解析）")
    parser.add_argument("group", nargs="?", default="",
                        help="群名关键词（模糊匹配）")
    parser.add_argument("--list-groups", action="store_true",
                        help="仅列出所有群，不读消息")
    parser.add_argument("--limit", type=int, default=20,
                        help="读取最新 N 条消息（默认 20）")
    parser.add_argument("--watch", action="store_true",
                        help="轮询模式：持续读取新消息")
    parser.add_argument("--sleep", type=float, default=3.0,
                        help="轮询间隔秒数（默认 3）")
    parser.add_argument("--red-only", action="store_true",
                        help="只显示红包消息")
    args = parser.parse_args()

    db = WeChatDB()
    nickname_map = load_nickname_map(db)

    if args.list_groups:
        groups = list_groups(db)
        print("群列表（%d 个，按消息数倒序）:" % len(groups))
        for g in groups:
            print("  %-28s %-32s %d 条" % (g["name"], g["wxid"], g["count"]))
        return

    group = find_group(db, args.group)
    if not group:
        print("未找到群：%r（可用 --list-groups 查看全部群）" % args.group)
        sys.exit(1)

    print("读取群：%s (%s)\n" % (group["name"], group["wxid"]))

    since_seq = 0
    while True:
        rows = _raw_messages(db, group["wxid"], args.limit)
        for row in rows:
            if args.watch and row.get("sort_seq", 0) <= since_seq:
                continue
            info = classify_message(row, nickname_map)
            if args.red_only and info["type"] != "红包":
                continue
            print_message(info)
            since_seq = max(since_seq, row.get("sort_seq", 0))

        if not args.watch:
            break
        print("\n--- 等待新消息 (%ds) ---" % args.sleep)
        time.sleep(args.sleep)


if __name__ == "__main__":
    main()