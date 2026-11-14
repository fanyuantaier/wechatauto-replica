# -*- coding: utf-8 -*-
"""看看/清理 bot 记下来的群友信息（纯本地文件操作，不连微信）。

用法：
  python memory_tool.py list           # 谁被记了什么
  python memory_tool.py show 小鱼       # 按名字/wxid/群名查
  python memory_tool.py del 小鱼        # 删掉这个人
  python memory_tool.py path           # 文件在哪
  python memory_tool.py list --path path/to/memory.json
"""

from __future__ import annotations

import argparse
import sys

import memory


def _fmt(row: dict) -> str:
    who = row.get("name") or row.get("wxid") or "?"
    facts = "；".join(row.get("facts") or []) or "(空)"
    return f"[{row.get('group') or '未知群'}] {who}（{row.get('wxid')}）：{facts}"


def _match(row: dict, kw: str) -> bool:
    kw = kw.strip().lower()
    if not kw:
        return False
    for key in ("name", "wxid", "group", "chat"):
        if kw in str(row.get(key) or "").lower():
            return True
    return any(kw in f.lower() for f in (row.get("facts") or []))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("cmd", choices=["list", "show", "del", "path"])
    ap.add_argument("keyword", nargs="?", default="")
    ap.add_argument("--path", default="", help="记忆文件路径（默认 data/memory.json）")
    ap.add_argument("--force", action="store_true", help="匹配到多个时全部删除")
    args = ap.parse_args(argv)

    if args.path:
        memory.set_path(args.path)

    if args.cmd == "path":
        print(memory.path())
        return 0

    rows = memory.people()
    if args.cmd == "list":
        if not rows:
            print("还没有记下任何人。")
            return 0
        for r in rows:
            print(_fmt(r))
        print(f"\n共 {len(rows)} 人，文件：{memory.path()}")
        return 0

    hit = [r for r in rows if _match(r, args.keyword)]
    if not hit:
        print(f"没有匹配「{args.keyword}」的记录。")
        return 1
    if args.cmd == "show":
        for r in hit:
            print(_fmt(r))
        return 0


    if len(hit) > 1 and not args.force:
        print(f"「{args.keyword}」匹配到 {len(hit)} 个人，先看清楚再删（确认要全删加 --force）：")
        for r in hit:
            print("  " + _fmt(r))
        return 2
    for r in hit:
        ok = memory.forget(r["chat"], r["wxid"])
        print(("已删除 " if ok else "没删掉 ") + _fmt(r))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
