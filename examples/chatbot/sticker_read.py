# -*- coding: utf-8 -*-
"""识别别人发的表情包/贴纸。

微信 4.x 表情消息的图片在本地库里是加密的、无法直接解出原图；但表情消息的
content 里带 md5（内容哈希，同一张贴纸不管谁发都相同）。因此用「md5 ↔ 标签」
映射表即可精确认出 自定义表情是哪一张，完全不需要解码图片。

md5_labels.json 由校准流程建立：把 26 张 自定义表情发到「文件传输助手」，
从库里回读每张的 md5 再绑定到标签。md5 是稳定的内容哈希，因此映射对群里别人
发的同款表情同样有效。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
import threading
from json_tables import read_table, write_table

_bind_lock = threading.RLock()

BASE = Path(__file__).resolve().parent
MAP_PATH = BASE / "stickers" / "md5_labels.json"

_EMOJI_MD5 = re.compile(r'md5="([0-9A-Fa-f]{32})"')


def emoji_md5(content) -> str:
    """从表情消息的 XML content 里取 emoji 的 md5（小写），取不到返回空串。"""
    m = _EMOJI_MD5.search(str(content or ""))
    return m.group(1).lower() if m else ""


def load_md5_map() -> dict:
    return read_table(MAP_PATH)


def save_md5_map(map_: dict) -> None:
    write_table(MAP_PATH, map_)


def bind_md5_label(md5: str, label: str) -> bool:
    """把某张贴纸的 md5 绑定到一个标签（用于校准/累积）。"""
    md5 = (md5 or "").lower()
    if not md5 or not label:
        return False
    with _bind_lock:
        m = load_md5_map()
        if m.get(md5) == label:
            return True
        m[md5] = label
        save_md5_map(m)
        return True


def label_for_md5(md5):
    """查 md5 对应的标签；查不到返回 None。"""
    if not md5:
        return None
    return load_md5_map().get(md5.lower())
