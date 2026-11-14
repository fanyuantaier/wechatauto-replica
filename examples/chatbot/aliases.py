# -*- coding: utf-8 -*-
"""群里的叫法和黑话：手写表 + 从聊天里自动学到的「别名 -> wxid」。

手写表（data/aliases.json 的 global / groups）随时改，热加载。
自动表（同文件 auto 段）由 bot 写：引用消息里带 <chatusr> + <displayname>，
微信自己给的对应关系，比按聊天内容猜可靠得多。同名不会重复写。

只做「怎么理解」的提示，不改群友的原话，也不往外发。
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PATH = Path(os.environ.get("CHATBOT_ALIASES_PATH")
                    or (BASE_DIR / "data" / "aliases.json"))

MAX_AUTO_PER_GROUP = 200
MAX_NAME_LEN = 24


BAD_ALIASES = {
    "某人", "未知", "群友", "群成员", "某成员", "匿名", "对方", "用户",
    "网友", "朋友", "消息", "转载", "微信用户", "微信团队",
}
AUTO_MAX_LEN = 12
_PURE_JUNK = re.compile(r"^[\W\d_]+$")
_SENTENCE_PUNCT = re.compile(r"[，。！？；：、,.!?;:]")

_lock = threading.RLock()
_path = DEFAULT_PATH
_cache = {"mtime": None, "data": {}}

persist = {"on": False}


def set_path(path, persist_too: bool = False) -> None:
    global _path
    with _lock:
        _path = Path(path)
        _cache["mtime"] = None
        _cache["data"] = None
        if persist_too:
            persist["on"] = True


def set_persist(on: bool) -> None:
    persist["on"] = bool(on)


def path() -> str:
    return str(_path)


def _clean(name) -> str:
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    return name[:MAX_NAME_LEN]


def _read() -> dict:
    """写前读取最新内容；坏文件拒绝覆盖。"""
    try:
        with io.open(_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("别名表必须是 JSON 对象")
        return data
    except FileNotFoundError:
        return {}
    except Exception as e:
        sys.stderr.write("别名表读取失败：%r\n" % (e,))
        raise ValueError("别名表损坏，拒绝覆盖") from e


def _load() -> dict:
    """带 mtime 缓存；文件被改过就重读。"""
    with _lock:
        try:
            stat = _path.stat()
            mtime = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return _cache["data"] or {}
        if _cache["mtime"] == mtime:
            return _cache["data"]
        try:
            data = _read()
        except ValueError:
            return _cache["data"] or {}
        _cache["mtime"] = mtime
        _cache["data"] = data
        return data


def _save(data: dict) -> None:
    _path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(_path) + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, _path)
    with _lock:
        _cache["mtime"] = None


def _pick(data: dict, group: str, key: str) -> dict:
    out = {}
    src = data.get("global") or {}
    if isinstance(src, dict) and isinstance(src.get(key), dict):
        out.update({str(k): str(v) for k, v in src[key].items() if k and v})
    groups = data.get("groups") or {}
    if group and isinstance(groups, dict):
        g = groups.get(group)
        if isinstance(g, dict) and isinstance(g.get(key), dict):
            out.update({str(k): str(v) for k, v in g[key].items() if k and v})
    return out


def _blocked(data: dict, group: str) -> set:
    """不想学的名字：全局 blocked + 本群 blocked。"""
    out = set()
    global_box = data.get("global")
    g = global_box.get("blocked") if isinstance(global_box, dict) else None
    if isinstance(g, list):
        out.update(str(x) for x in g if x)
    groups = data.get("groups")
    box = groups.get(group) if isinstance(groups, dict) else None
    row = box.get("blocked") if isinstance(box, dict) else None
    if isinstance(row, list):
        out.update(str(x) for x in row if x)
    return out


def _auto_box(data: dict, group: str) -> dict:
    auto = data.get("auto")
    if not isinstance(auto, dict):
        auto = data["auto"] = {}
    box = auto.get("groups")
    if not isinstance(box, dict):
        box = auto["groups"] = {}
    row = box.get(group)
    if not isinstance(row, dict):
        row = box[group] = {}
    return row


def remember(group: str, alias: str, wxid: str, is_bot: bool = False) -> bool:
    """记下「有人这么叫他/她」。返回是否真的新写入。

    group 用微信群名；is_bot 表示这个别名说的是机器人自己。
    """
    alias = _clean(alias)
    wxid = str(wxid or "").strip()
    if not group or not alias or not wxid or len(alias) < 2:
        return False

    if alias in BAD_ALIASES or len(alias) > AUTO_MAX_LEN:
        return False
    if _PURE_JUNK.match(alias) or any(ch.isspace() for ch in alias):
        return False

    if alias == group or _SENTENCE_PUNCT.search(alias):
        return False
    with _lock:
        existed = os.path.exists(_path)
        try:
            data = _read()
        except ValueError:
            return False
        if alias in _blocked(data, group):
            return False
        if is_bot:
            auto = data.get("auto")
            if not isinstance(auto, dict):
                auto = data["auto"] = {}
            names = auto.get("bot")
            if not isinstance(names, dict):
                names = auto["bot"] = {}
            row = names.get(group)
            if not isinstance(row, list):
                row = names[group] = []
            if alias in row:
                return False
            row.append(alias)
            del row[:-MAX_AUTO_PER_GROUP]
        else:
            row = _auto_box(data, group)
            if row.get(alias) == wxid:
                return False
            row[alias] = wxid
            if len(row) > MAX_AUTO_PER_GROUP:
                for k in list(row)[:len(row) - MAX_AUTO_PER_GROUP]:
                    row.pop(k, None)


        ok_to_write = bool(data) or not existed
        if persist["on"] and ok_to_write:
            try:
                _save(data)
            except Exception as e:
                sys.stderr.write("别名表写入失败：%r\n" % (e,))
                return False
        else:

            with _lock:
                try:
                    _cache["mtime"] = os.path.getmtime(_path)
                except OSError:
                    _cache["mtime"] = None
                _cache["data"] = data
        return True


def _commit(data: dict) -> None:
    """把改过的表写回；persist 关闭时只更新内存缓存（测试用）。"""
    if persist["on"]:
        _save(data)
    else:
        with _lock:
            try:
                _cache["mtime"] = os.path.getmtime(_path)
            except OSError:
                _cache["mtime"] = None
            _cache["data"] = data


def _group_box(data: dict, group: str) -> dict:
    groups = data.get("groups")
    if not isinstance(groups, dict):
        groups = data["groups"] = {}
    box = groups.get(group)
    if not isinstance(box, dict):
        box = groups[group] = {}
    return box


def block(group: str, alias: str) -> bool:
    """永久禁学一个称呼；已经学到的也从 auto 里删掉。"""
    alias = _clean(alias)
    if not group or not alias:
        return False
    with _lock:
        data = _read()
        box = _group_box(data, group)
        blocked = box.get("blocked")
        if not isinstance(blocked, list):
            blocked = box["blocked"] = []
        added = alias not in blocked
        if added:
            blocked.append(alias)

        auto = data.get("auto")
        if isinstance(auto, dict):
            rows = auto.get("groups")
            if isinstance(rows, dict) and isinstance(rows.get(group), dict):
                rows[group].pop(alias, None)
            bots = auto.get("bot")
            if isinstance(bots, dict) and isinstance(bots.get(group), list):
                bots[group] = [n for n in bots[group] if n != alias]
        if added:
            _commit(data)
        return added


def unblock(group: str, alias: str) -> bool:
    """解除禁学，之后引用里再遇到就会重新学。"""
    alias = _clean(alias)
    if not group or not alias:
        return False
    with _lock:
        data = _read()
        box = _group_box(data, group)
        blocked = box.get("blocked")
        if not isinstance(blocked, list) or alias not in blocked:
            return False
        box["blocked"] = [n for n in blocked if n != alias]
        _commit(data)
        return True


def auto_map(group: str) -> dict:
    """自动学到的 别名 -> wxid（只取本群）。"""
    data = _load()
    auto = data.get("auto") or {}
    groups = auto.get("groups") if isinstance(auto, dict) else None
    row = (groups or {}).get(group) if isinstance(groups, dict) else None
    if not isinstance(row, dict):
        return {}
    return {str(k): str(v) for k, v in row.items() if k and v}


def bot_names(group: str) -> list:
    data = _load()
    auto = data.get("auto") or {}
    names = auto.get("bot") if isinstance(auto, dict) else None
    row = (names or {}).get(group) if isinstance(names, dict) else None
    return [str(n) for n in row if n] if isinstance(row, list) else []


def blocked(group: str) -> list:
    """本群禁学的称呼列表。"""
    data = _load()
    groups = data.get("groups") if isinstance(data, dict) else None
    box = (groups or {}).get(group) if isinstance(groups, dict) else None
    row = (box or {}).get("blocked") if isinstance(box, dict) else None
    return [str(n) for n in row or [] if n] if isinstance(row, list) else []


def self_mentioned(group: str, text: str) -> bool:
    """这句话里有没有用群友的叫法喊她（自动学到的那些）。"""
    if not text:
        return False
    for name in bot_names(group):
        if name and name in text:
            return True
    return False


def hint(group: str = "", text: str = "", resolve=None) -> str:
    """给模型的一段附注：本群的叫法 + 这句话里出现的黑话。

    resolve(wxid) -> 现在该叫他什么（由 bot 提供）。没有可说的返回空串。
    """
    data = _load()
    if not data:
        return ""
    manual = _pick(data, group, "aliases")
    slang = _pick(data, group, "slang")

    lines = []
    if manual:
        lines.append("；".join("「%s」就是%s" % (k, v)
                               for k, v in manual.items()) + "。")

    auto = auto_map(group)
    auto_hits = []
    for alias, wxid in auto.items():
        if text and alias not in text:
            continue
        who = ""
        if resolve is not None:
            try:
                who = str(resolve(wxid) or "").strip()
            except Exception:
                who = ""
        if not who or who == alias:
            continue
        auto_hits.append("「%s」就是%s" % (alias, who))
        if len(auto_hits) >= 6:
            break
    if auto_hits:
        lines.append("；".join(auto_hits) + "。")

    mine = [n for n in bot_names(group) if n and (not text or n in text)]
    if mine:
        lines.append("也有人叫你%s，那是在喊你。" % "、".join(
            "「%s」" % n for n in mine[:4]))

    if slang:
        hit = [(k, v) for k, v in slang.items() if text and k in text]
        if hit:
            lines.append("；".join("「%s」= %s" % (k, v)
                                   for k, v in hit) + "。")
    if not lines:
        return ""
    return (
        "【群里的叫法，看懂就行，别解释】\n"
        + "\n".join("- " + s for s in lines)
        + "\n"
        "这些是同一个群里叫顺口的说法，你自己知道就好，"
        "不要专门给谁解释这些词是什么意思，也别把它们当新话题。"
    )


def main(argv=None) -> int:
    """命令行：python aliases.py [群名] [这句话] —— 看看现在会喂给模型什么。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    group = argv[0] if argv else ""
    text = argv[1] if len(argv) > 1 else ""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(hint(group, text) or "(没有可用的叫法/黑话)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
