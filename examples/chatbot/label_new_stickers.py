# -*- coding: utf-8 -*-
"""给新贴纸补标签（只改标签表，不动微信收藏）。

用法：
    python label_new_stickers.py           # 只列出未标注的图
    python label_new_stickers.py --apply   # 交互式填标签

流程：
1. 把新图放进 stickers/raw2（文件名随意，建议 0030.png 这种）；
2. 运行 --apply，按提示输入标签（回车跳过，q 退出）；
3. 脚本会备份并更新 stickers/stickers.json 和 stickers/labels_preview.json；
4. 还要在微信「自定义表情」面板里收藏这张图，发送时才能匹配到。

注意：这只负责“发”的标签；要让 bot 认出别人发的同款贴纸，还需要 md5 校准。
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
STICKERS_DIR = BASE / "stickers"
RAW_DIR = STICKERS_DIR / "raw2"
REG_PATH = STICKERS_DIR / "stickers.json"
PREVIEW_PATH = STICKERS_DIR / "labels_preview.json"
BACKUP_DIR = STICKERS_DIR / "backups"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}

BLOCKED_FILES = set()


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    tmp = str(path) + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def iter_images(raw_dir: Path) -> list:
    if not raw_dir.exists():
        return []
    return sorted(p.name for p in raw_dir.iterdir()
                  if p.is_file() and p.suffix.lower() in IMAGE_EXT)


def labeled_files(reg: dict, preview: dict) -> set:
    out = set()
    for files in (reg.get("labels") or {}).values():
        for f in files or []:
            out.add(str(f))
    for row in (preview or {}).values():
        if isinstance(row, dict) and row.get("file"):
            out.add(str(row["file"]))
    return out


def find_unlabeled(raw_dir: Path, reg: dict, preview: dict) -> list:
    known = labeled_files(reg, preview)
    return [f for f in iter_images(raw_dir)
            if f not in known and f not in BLOCKED_FILES]


def next_preview_id(preview: dict) -> int:
    ids = []
    for k in (preview or {}):
        try:
            ids.append(int(k))
        except (TypeError, ValueError):
            continue
    return (max(ids) + 1) if ids else 1


def add_label(reg: dict, preview: dict, filename: str, label: str) -> bool:
    label = str(label or "").strip()
    filename = str(filename or "").strip()
    if not label or not filename or filename in BLOCKED_FILES:
        return False
    labels = reg.setdefault("labels", {})
    files = labels.setdefault(label, [])
    if not isinstance(files, list):
        files = labels[label] = []
    if filename not in files:
        files.append(filename)
    if not any(isinstance(v, dict) and v.get("file") == filename
               for v in (preview or {}).values()):
        preview[str(next_preview_id(preview))] = {
            "file": filename, "label": label}
    return True


def backup(path: Path) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_DIR / ("%s_%s%s" % (path.stem, stamp, path.suffix))
    dst.write_bytes(path.read_bytes())


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    argv = list(sys.argv[1:] if argv is None else argv)
    apply = "--apply" in argv

    reg = load_json(REG_PATH, {})
    preview = load_json(PREVIEW_PATH, {})
    unlabeled = find_unlabeled(RAW_DIR, reg, preview)
    print("stickers/raw2：%d 张图，已标注 %d 张，未标注 %d 张"
          % (len(iter_images(RAW_DIR)), len(labeled_files(reg, preview)),
             len(unlabeled)))
    skipped = sorted(f for f in iter_images(RAW_DIR) if f in BLOCKED_FILES)
    if skipped:
        print("已跳过禁用贴纸：%s" % "、".join(skipped))
    if not unlabeled:
        print("所有贴纸都有标签了。")
        return 0
    for name in unlabeled:
        print("  未标注：%s" % name)
    if not apply:
        print("\n只列出，没有改文件。要填标签请运行：")
        print("  python label_new_stickers.py --apply")
        return 0

    backup(REG_PATH)
    backup(PREVIEW_PATH)
    changed = 0
    for name in unlabeled:
        try:
            label = input("%s 标签（回车跳过，q 退出）：" % name).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if label.lower() == "q":
            break
        if not label:
            continue
        if add_label(reg, preview, name, label):
            changed += 1
            print("  已标注：%s -> %s" % (name, label))
    if changed:
        save_json(REG_PATH, reg)
        save_json(PREVIEW_PATH, preview)
    print("\n完成：新标注 %d 张，已备份到 stickers/backups/。" % changed)
    print("别忘了在微信「自定义表情」面板里收藏这些图；")
    print("要让 bot 认出别人发的同款，还需要做 md5 校准。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
