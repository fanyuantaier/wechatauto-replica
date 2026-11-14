# -*- coding: utf-8 -*-
"""查看当前生效的黑话表，并逐条显示模型会收到的提示。

用法：
    python slang_show.py

改完 data/aliases.json 后跑一下：能看到词条、以及 aliases.hint() 会不会
把这句解释喂给模型。只读，不改任何文件、不连微信。
"""

import io
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import aliases

cfg = json.load(io.open(BASE / "config.json", encoding="utf-8"))
groups = [cfg.get("group_name") or ""] + list(cfg.get("extra_group_names") or [])

total = 0
for name in [g for g in groups if g]:
    slang = aliases._pick(aliases._load(), name, "slang")
    print("=" * 60)
    print("群：%s" % name)
    if not slang:
        print("  （还没有黑话；在 data/aliases.json 的 slang 里填）")
        continue
    for word, meaning in slang.items():
        total += 1
        print("  「%s」= %s" % (word, meaning))
        hint = aliases.hint(name, word)
        print("     模型会看到：%s" % (hint or "（没命中，检查词有没有写对）"))

print("=" * 60)
print("共 %d 条黑话。改完 aliases.json 不用重启；这里能看到就说明已加载。" % total)
