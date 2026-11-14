# -*- coding: utf-8 -*-
"""群友长期记忆：记下每个人自己说过的稳定信息，回复时按人带上。

群聊上下文只保留最近几十条，跨天就忘。有人自我介绍过一次（「叫我小鱼」
「我不吃香菜」），过两天再聊完全没印象，很像换了个号。

原则：
- 只从本人原话里抽，抽不到就当没有；不做推断、绝不编造。
- 只存本地 JSON，不外传、不发群。
- 每人有上限，久了没再提就自然淡出，文件不会无限长。
"""

from __future__ import annotations

import io
import math
import json
import os
import re
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DEFAULT_PATH = Path(os.environ.get("CHATBOT_MEMORY_PATH")
                    or (BASE_DIR / "data" / "memory.json"))

MAX_FACTS = 8
MAX_FACT_LEN = 40
MAX_PEOPLE = 800
EXPIRE_SECONDS = 400 * 86400


FACT_MAX_AGE_DAYS = 45


_CONFLICT_KINDS = {
    "喜欢": ("讨厌", "忌口"),
    "讨厌": ("喜欢", "忌口"),
    "忌口": ("喜欢", "讨厌"),
    "称呼": ("称呼",),
}


_lock = threading.RLock()
_data = None
_path = DEFAULT_PATH


def set_path(path, reload: bool = True) -> None:
    """换存储位置（测试用临时文件；线上就是 data/memory.json）。"""
    global _path, _data
    with _lock:
        _path = Path(path)
        if reload:
            _data = None


def path() -> str:
    return str(_path)


def _key(chat: str, wxid: str) -> str:
    """按「群 + wxid」分开记：同一个人在不同群可以有不同称呼。"""
    return f"{chat or ''}|{wxid or ''}"


def _timestamp(value) -> float:
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _sanitize_people(people: dict) -> dict:
    out = {}
    for key, rec in people.items():
        if not isinstance(rec, dict):
            continue
        rec = dict(rec)
        rec["ts"] = _timestamp(rec.get("ts"))
        facts = rec.get("facts")
        rec["facts"] = [
            {"t": str(it.get("t") or "")[:MAX_FACT_LEN],
             "ts": _timestamp(it.get("ts"))}
            for it in facts if isinstance(it, dict) and it.get("t")
        ][-MAX_FACTS:] if isinstance(facts, list) else []
        out[str(key)] = rec
    return out


def _load() -> dict:
    global _data
    if _data is not None:
        return _data
    data = {"version": 1, "people": {}}
    try:
        with io.open(_path, encoding="utf-8") as f:
            raw = f.read()
        obj = json.loads(raw)
        if isinstance(obj, dict) and isinstance(obj.get("people"), dict):
            data = {"version": 1, "people": _sanitize_people(obj["people"])}
    except FileNotFoundError:
        pass
    except Exception:

        try:
            os.replace(str(_path), str(_path) + ".bad")
        except OSError:
            pass
    _data = data
    return data


def _save() -> None:
    data = _data or {"version": 1, "people": {}}
    try:
        _path.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(_path) + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, str(_path))
    except Exception:

        pass


def _norm(text: str) -> str:
    """去重用的归一化：忽略标点、空格和大小写差异。"""
    return re.sub(r"[\s，,。.！!？?；;：:、~～…·\"'“”‘’（）()]+", "", str(text or "")).lower()


def _prune(data: dict, now: float) -> None:
    people = data.get("people") or {}
    for k in [k for k, v in people.items()
              if not isinstance(v, dict) or now - float(v.get("ts") or 0) > EXPIRE_SECONDS]:
        people.pop(k, None)
    if len(people) > MAX_PEOPLE:
        order = sorted(people.items(), key=lambda kv: float((kv[1] or {}).get("ts") or 0))
        for k, _ in order[: len(people) - MAX_PEOPLE]:
            people.pop(k, None)


def remember(chat: str, wxid: str, name: str = "", facts=(),
             group: str = "") -> list:
    """记下这个人这次说的稳定信息，返回真正新存进去的条目。

    只更新名字、或全是重复内容时不落盘，避免每句话都写文件。
    """
    if not wxid:
        return []
    facts = [str(f).strip() for f in (facts or ()) if str(f).strip()]
    if not facts and not name:
        return []
    group = str(group or "").strip()
    now = time.time()
    with _lock:
        data = _load()
        key = _key(chat, wxid)
        rec = data["people"].get(key)
        changed = False
        if not isinstance(rec, dict):
            if not facts:

                return []
            rec = {"chat": chat or "", "wxid": wxid, "name": "", "facts": [], "ts": now}
            data["people"][key] = rec
            changed = True
        name = str(name or "").strip()
        if name and rec.get("name") != name:
            rec["name"] = name
            changed = True
        if group and rec.get("group") != group:
            rec["group"] = group
            changed = True
        items = rec.setdefault("facts", [])
        if not isinstance(items, list):
            items = rec["facts"] = []
        seen = {_norm(it.get("t")) for it in items if isinstance(it, dict)}
        added = []
        for f in facts:
            t = f[:MAX_FACT_LEN]
            n = _norm(t)
            if not n or n in seen:
                continue
            items.append({"t": t, "ts": now})
            seen.add(n)
            added.append(t)
            changed = True
        if added:


            fresh = []
            for f in added:
                kind, _, val = f.partition("：")
                fresh.append((kind.strip(), _norm(val)))
            drop = set()
            for it in items:
                if not isinstance(it, dict):
                    continue
                old = str(it.get("t") or "")
                if old in added:
                    continue
                okind, _, oval = old.partition("：")
                okind = okind.strip()
                for kind, val in fresh:
                    if okind not in _CONFLICT_KINDS.get(kind, ()):
                        continue
                    if okind == "称呼" or _norm(oval) == val:
                        drop.add(old)
                        break
            if drop:
                items = [it for it in items if isinstance(it, dict)
                         and str(it.get("t") or "") not in drop]
                rec["facts"] = items
                changed = True
        if changed:
            rec["ts"] = now
            items.sort(key=lambda it: float((it or {}).get("ts") or 0))
            while len(items) > MAX_FACTS:
                items.pop(0)
            _prune(data, now)
            _save()
        return added


def recall(chat: str, wxid: str, limit: int = None,
           max_age_days: int = None) -> list:
    """取这个人记过的事实（旧→新）；给回复用，所以只取最近的几条。

    超过 FACT_MAX_AGE_DAYS 的旧事不再拿出来说，但仍留在文件里：
    有点忘性才像人，什么都记得一清二楚反而吓人。
    """
    if not wxid:
        return []
    days = FACT_MAX_AGE_DAYS if max_age_days is None else max_age_days
    try:
        days = float(days)
    except (TypeError, ValueError):
        days = FACT_MAX_AGE_DAYS
    cutoff = time.time() - days * 86400 if days > 0 else 0
    with _lock:
        data = _load()
        rec = data["people"].get(_key(chat, wxid))
        if not isinstance(rec, dict):
            return []
        raw = [(str(it.get("t") or "").strip(), float(it.get("ts") or 0))
               for it in rec.get("facts") or [] if isinstance(it, dict)]
    items = [t for t, ts in raw if t and (not cutoff or ts >= cutoff)]
    if limit and len(items) > limit:
        items = items[-int(limit):]
    return items


def name_of(chat: str, wxid: str) -> str:
    with _lock:
        data = _load()
        rec = data["people"].get(_key(chat, wxid))
        return str((rec or {}).get("name") or "").strip() if isinstance(rec, dict) else ""


def people() -> list:
    """所有记过的人，按群、名字排好序；给查看/清理用。"""
    with _lock:
        data = _load()
        rows = []
        for rec in (data.get("people") or {}).values():
            if not isinstance(rec, dict):
                continue
            rows.append({
                "group": str(rec.get("group") or rec.get("chat") or ""),
                "chat": str(rec.get("chat") or ""),
                "wxid": str(rec.get("wxid") or ""),
                "name": str(rec.get("name") or ""),
                "facts": [str(it.get("t") or "") for it in rec.get("facts") or []
                          if isinstance(it, dict)],
                "ts": float(rec.get("ts") or 0),
            })
    rows.sort(key=lambda r: (r["group"], r["name"], r["wxid"]))
    return rows


def forget_kind(chat: str, wxid: str, kinds) -> list:
    """删掉这个人某几类旧事实（他改口、或让他忘掉时用）。

    返回真正删掉的条目；一条都没动就返回空列表。
    """
    want = {str(k).strip() for k in (kinds or ()) if str(k).strip()}
    if not wxid or not want:
        return []
    removed = []
    with _lock:
        data = _load()
        rec = data["people"].get(_key(chat, wxid))
        if not isinstance(rec, dict):
            return []
        items = rec.get("facts")
        if not isinstance(items, list):
            return []
        keep = []
        for it in items:
            if isinstance(it, dict):
                kind = str(it.get("t") or "").partition("：")[0].strip()
                if kind in want:
                    removed.append(str(it.get("t") or ""))
                    continue
            keep.append(it)
        if removed:
            rec["facts"] = keep
            rec["ts"] = time.time()
            _save()
    return removed


def forget(chat: str, wxid: str) -> bool:
    with _lock:
        data = _load()
        key = _key(chat, wxid)
        if key in data["people"]:
            data["people"].pop(key, None)
            _save()
            return True
    return False


def learned_name(chat: str, wxid: str, max_age_days: int = None) -> str:
    """他让人怎么称呼自己（「叫我小鱼就行」），取最新一条没过期的。

    只认本人亲口说过的；没说过就返回空串，外面继续按群昵称表叫。
    """
    for fact in reversed(recall(chat, wxid, max_age_days=max_age_days)):
        kind, sep, val = fact.partition("：")
        if sep and kind.strip() == "称呼" and val.strip():
            return val.strip()
    return ""


def hint(chat: str, wxid: str, display_name: str = "", limit: int = 6,
         max_age_days: int = None) -> str:
    """拼出一段「你对这个人的印象」，塞进这次请求里。

    写成零碎印象而不是档案：不挨条念，相关才顺口带一句。
    """
    facts = recall(chat, wxid, limit=limit, max_age_days=max_age_days)
    if not facts:
        return ""
    who = str(display_name or name_of(chat, wxid) or "").strip() or "这个人"
    lines = "\n".join("- " + f for f in facts)
    return (
        f"【你对{who}的印象】\n{lines}\n"
        "这些是你以前自己留意的零碎印象，不是档案，也不是这一轮的新消息。"
        "只有跟眼前这句话自然相关时，才顺口提一句，说不顺就别提；"
        "不要把这几条挨个复述，也不要张口就是「你以前说过…」「我记得你…」。"
        "他要是自己先说到了，你就顺着聊，别再往回倒。"
        "他说的跟你记得的不一样，就以他现在说的为准，别拿旧印象跟他争。"
        "没记在这里的事就当你不知道，绝对不要编。"
    )


_END = r"(?=[，,。.！!？?；;~～…\s]|$)"
_CHARS = r"([^，,。.！!？?；;~～…\s]{1,%d})"
_ASK_WORDS = ("吗", "?", "？", "啥", "什么", "谁", "哪个", "咋", "多少", "几点",
              "怎么", "是不是", "有没有", "几", "来着", "不出来", "不知道", "忘了")

_VAGUE = ("公司", "单位", "学校", "上班", "这边", "那边", "附近", "外面", "家里",
          "这里", "那里", "网上", "线上", "别人", "家")

_UNCERTAIN = re.compile(r"(是不是|有没有|难道|听说|据说|好像说)")
_BAD_HEAD = ("你", "您", "他", "她", "它", "们", "我", "这", "那")
_LEAD_STRIP = "的地得在就都也又还"

_TAIL_WORDS = ("就行", "就好", "就好了", "可以了", "没问题", "吧", "啦", "啊",
               "哦", "嗯", "哈", "呀", "嘛", "呢", "了", "的")

_QUANT = re.compile(r"^(?:一|两|几)?[只条个位]")

_BAD_NAME = re.compile(r"^(?:了|个|辆|份|杯|瓶|张|次|声|下|外卖|车|人|你|他|她)")

_RULES = (
    ("称呼", re.compile(r"我(?:的)?名字(?:是|叫)" + (_CHARS % 10) + _END)),
    ("称呼", re.compile(r"(?:你们|大家|以后)?(?:可以|就)?(?<![你您他她它别])叫我"
                        + (_CHARS % 8) + _END)),
    ("称呼", re.compile(r"(?<![你您他她它别])我叫" + (_CHARS % 8) + _END)),
    ("喜欢", re.compile(r"我(?:最|很|超|特别|挺)?喜欢" + (_CHARS % 14) + _END)),
    ("讨厌", re.compile(r"我(?:最|很|超|特别)?(?:讨厌|不喜欢|受不了|烦)"
                        + (_CHARS % 14) + _END)),
    ("忌口", re.compile(r"我(?:从来)?(?:不吃|不能吃|不喝|吃不了|忌口)"
                        + (_CHARS % 10) + _END)),
    ("忌口", re.compile(r"我对" + (_CHARS % 10) + r"过敏")),
    ("生日", re.compile(r"我(?:的)?生日(?:是|在|\\s*[:：]\\s*)?"
                        + (_CHARS % 12) + _END)),
    ("住处", re.compile(r"我(?:家)?住在?" + (_CHARS % 10) + _END)),
    ("上班/上学", re.compile(r"我在" + (_CHARS % 12) + r"(?:上班|工作|上学|读书|念书|实习)")),
    ("养了", re.compile(r"我(?:家)?养了(?:一?[只条个位])?" + (_CHARS % 10) + _END)),
)


def _clean_value(v: str, kind: str = "") -> str:
    v = str(v or "").strip()
    v = v.lstrip(_LEAD_STRIP)
    v = v.strip("，,。.！!？?；;：:、~～… ")
    for _ in range(3):
        for w in _TAIL_WORDS:
            if v.endswith(w) and len(v) > len(w):
                v = v[: -len(w)]
                break
        else:
            break
    if kind == "养了":
        v = _QUANT.sub("", v, count=1)
    v = v.strip("，,。.！!？?；;：:、~～… ")
    if not v or len(v) > 14:
        return ""
    if any(w in v for w in _ASK_WORDS):
        return ""
    if v.startswith(_BAD_HEAD):
        return ""
    if v in _VAGUE:
        return ""
    if kind == "称呼" and (_BAD_NAME.match(v) or re.match(r"一[声下句]", v)
                                 or re.search(r"(试试|看看|一下)$", v)):
        return ""
    if re.search(r"(一声|一下|一句|干嘛|干什么|干嘛呢)$", v):
        return ""
    if not re.search(r"[0-9A-Za-z\u4e00-\u9fa5]", v):
        return ""
    return v


def extract_facts(text: str) -> list:
    """从一句话里抽出该记的条目，形如「称呼：小鱼」「讨厌：香菜」。"""
    text = str(text or "").strip()
    if not text or len(text) > 200:

        return []
    if _UNCERTAIN.search(text):

        return []
    out = []
    for kind, pat in _RULES:
        for m in pat.finditer(text):
            v = _clean_value(m.group(1), kind)
            if not v:
                continue
            fact = f"{kind}：{v}"
            if fact not in out:
                out.append(fact)
    return out
