# -*- coding: utf-8 -*-
"""候选黑话：扫群聊天记录，把高频、词表里没有的词列出来供确认。

只读：不动 aliases.json，也不碰微信窗口。
确认后自己填进 data/aliases.json 的 groups -> 群名 -> slang：

    "slang": {"乌鱼子": "无语子，表示无语"}

判断口径（都是启发式，只用来缩小范围）：
  * 只看群友发的文字，不看机器人自己说的话；
  * 至少在 3 条不同消息、2 个不同的人那里出现过；
  * 至少 3 个字（2 个字和普通词太难分开）；
  * 排掉称呼/别名/黑话表，以及机器人自己也常说的词。

用法：
    python slang_candidates.py             # 每群看最近 2000 条
    python slang_candidates.py 4000        # 自定条数
输出：
    data/slang_candidates.txt
"""

from __future__ import annotations

import html
import io
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
ALIASES_PATH = BASE_DIR / "data" / "aliases.json"
OUT_PATH = BASE_DIR / "data" / "slang_candidates.txt"

MIN_MESSAGES = 3
MIN_SENDERS = 2
MIN_LEN = 3
MAX_N = 5
BOT_MIN_MESSAGES = 3
TOP_N = 60

CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
URL_RE = re.compile(r"(?:https?://|www\.)\S+")
AT_RE = re.compile(r"@\S+")
TAG_RE = re.compile(r"<[^>]+>")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
WXID_PREFIX = re.compile(r"^wxid_[A-Za-z0-9]+:\s*")


EDGE_CHARS = set(
    "的了是我你他她它吗呢啊吧呀哈哦嗯就都也在有不这那个们和与及或把被让给对"
    "说要想会能可还又很太更最才只等一二三四五六七八九十"
    "去来看到买吃玩睡找做弄搞问发现特挺蛮超好多少大小新旧真假非发"
)

STOP = set("""
哈哈 哈哈哈 哈哈哈哈 呵呵 嗯嗯 哦哦 好的 好吧 好呀 好滴 可以 收到 谢谢 拜拜 晚安 早安
什么 怎么 为什么 这个 那个 真的 不是 没有 就是 已经 一下 一点 感觉 觉得 知道 不知
今天 明天 昨天 现在 时候 事情 东西 大家 自己 我们 你们 他们 她们 咱们 别人 这里 那里
去学校 多少钱 发消息 现在正 特别好 有没有 是不是 能不能 可不可以 什么时候 怎么办
""".split())


def load_json(path):
    try:
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def collect_known(cfg, aliases, member_names):
    """所有已知的称呼/词：这些不该再当候选。"""
    known = set()

    def add(x):
        x = str(x or "").strip()
        if len(x) >= 2:
            known.add(x)

    for n in cfg.get("bot_names") or []:
        add(n)
    for n in (cfg.get("nickname_map") or {}).values():
        add(n)
    for n in member_names:
        add(n)

    def walk(node):

        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("_comment", "blocked"):
                    continue
                add(k)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, str):
                    add(v)
                else:
                    walk(v)

    walk(aliases)
    return known


def build_known_sub(known):
    """已知词的所有 2 字以上片段：候选只要沾上，就不是新词。"""
    out = set()
    for w in known:
        w = w[:16]
        for i in range(len(w)):
            for j in range(i + 2, len(w) + 1):
                out.add(w[i:j])
    return out


def known_hit(gram, known, known_sub):
    if gram in known or gram in known_sub:
        return True

    for i in range(len(gram)):
        for j in range(i + 2, len(gram) + 1):
            if gram[i:j] in known:
                return True
    return False


def message_text(m):
    mtype = m.get("type")
    raw = str(m.get("content") or "")
    if mtype == "文本":
        return WXID_PREFIX.sub("", raw, count=1)
    if mtype == "引用":
        t = TITLE_RE.search(raw)
        return html.unescape(TAG_RE.sub(" ", t.group(1))) if t else ""
    return ""


def normalize(text):
    text = html.unescape(text or "")
    text = URL_RE.sub(" ", text)
    text = AT_RE.sub(" ", text)
    text = TAG_RE.sub(" ", text)
    return text


def grams_of(text):
    """一句话里所有 2~5 字的候选片段（去重，避免同一句重复计数）。"""
    out = set()
    for run in CJK_RUN.findall(normalize(text)):
        n_run = len(run)
        for n in range(2, MAX_N + 1):
            for i in range(0, n_run - n + 1):
                g = run[i:i + n]
                if g in STOP:
                    continue
                if g[0] in EDGE_CHARS or g[-1] in EDGE_CHARS:
                    continue
                out.add(g)
    return out


def scan(db, uid, limit, self_wxid):
    """分别统计群友和机器人自己的说法。"""
    try:
        msgs = db.get_messages(uid, limit=limit) or []
    except Exception as e:
        sys.stderr.write("读取群消息失败（%s）：%r\n" % (uid, e))
        return [], Counter(), Counter(), defaultdict(set), set()

    texts = []
    occurrences = Counter()
    msg_counts = Counter()
    senders = defaultdict(set)
    bot_seen = Counter()
    for m in msgs:
        text = normalize(message_text(m))
        if len(text.strip()) < 2:
            continue
        gs = grams_of(text)
        if self_wxid and (m.get("sender_username") or "") == self_wxid:
            for g in gs:
                bot_seen[g] += 1
            continue
        texts.append(text)
        sender = (m.get("sender_username") or "").strip()
        for g in gs:
            occurrences[g] += 1
            msg_counts[g] += 1
            if sender:
                senders[g].add(sender)
    bot_grams = {g for g, c in bot_seen.items() if c >= BOT_MIN_MESSAGES}
    return texts, occurrences, msg_counts, senders, bot_grams


def pick(occurrences, msg_counts, sender_counts, known, known_sub, bot_grams):
    pool = []
    for g, c in msg_counts.items():
        if c < MIN_MESSAGES or len(g) < MIN_LEN:
            continue
        if len(sender_counts.get(g) or ()) < MIN_SENDERS:
            continue
        if g in bot_grams or known_hit(g, known, known_sub):
            continue
        pool.append(g)

    pool.sort(key=lambda g: (-len(sender_counts[g]), -msg_counts[g],
                             -len(g), -occurrences[g]))
    pool = pool[:400]
    kept = []
    for g in pool:

        if any(g in k or k in g for k in kept):
            continue
        kept.append(g)
        if len(kept) >= TOP_N:
            break
    return kept


def examples_for(texts, gram, n=2):
    out = []
    for t in texts:
        if gram in t:
            out.append(t.strip()[:60])
            if len(out) >= n:
                break
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        limit = int(argv[0]) if argv else 2000
    except ValueError:
        limit = 2000

    cfg = load_json(CONFIG_PATH)
    aliases = load_json(ALIASES_PATH)

    try:
        from wechatauto import WeChatDB
        db = WeChatDB()
        self_wxid = (db.get_self_info() or {}).get("username") or ""
    except Exception as e:
        sys.stderr.write("打不开微信数据库：%r\n" % (e,))
        return 2

    names = [cfg.get("group_name") or ""]
    for extra in cfg.get("extra_group_names") or []:
        if extra and extra not in names:
            names.append(extra)

    sections = []
    for name in [n for n in names if n]:
        hits = db.search_contact(name)
        if not hits:
            sections.append((name, 0, []))
            continue
        uid = hits[0].get("username") or ""
        member_names = []
        try:
            for r in db.get_group_members(uid) or []:
                member_names.append(r.get("remark") or r.get("nick_name") or "")
        except Exception:
            pass
        known = collect_known(cfg, aliases, member_names)
        known_sub = build_known_sub(known)

        texts, occurrences, msg_counts, senders, bot_grams = scan(
            db, uid, limit, self_wxid)
        cands = pick(occurrences, msg_counts, senders, known, known_sub,
                     bot_grams)
        rows = []
        for g in cands:
            rows.append((g, len(senders[g]), msg_counts[g], occurrences[g],
                         examples_for(texts, g)))
        sections.append((name, len(texts), rows))

    lines = []
    lines.append("候选黑话（自动生成，仅供参考）")
    lines.append("=" * 60)
    lines.append("把确认过的填进 data/aliases.json 的 groups -> 群名 -> slang：")
    lines.append('    "slang": {"词": "意思"}')
    lines.append("没确认的不要填；不同群的词各填各的。")
    lines.append("")
    total = 0
    for name, scanned, rows in sections:
        total += len(rows)
        lines.append("-" * 60)
        lines.append("群：%s" % name)
        lines.append("扫描 %d 条群友消息，候选 %d 个"
                     "（至少 %d 条消息 / %d 个人用过）"
                     % (scanned, len(rows), MIN_MESSAGES, MIN_SENDERS))
        if not rows:
            lines.append("  （没有新词；也可能是聊天记录太少）")
            continue
        for idx, (g, sc, mc, oc, exs) in enumerate(rows, 1):
            lines.append("%3d. %-12s %d 人 / %d 条 / %d 次"
                         % (idx, g, sc, mc, oc))
            for ex in exs:
                lines.append("      例：%s" % ex)
        lines.append("")
    text = "\n".join(lines) + "\n"

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with io.open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)
    print("共 %d 个候选；已写入 %s" % (total, OUT_PATH))
    return 0


if __name__ == "__main__":
    sys.exit(main())
