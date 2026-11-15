# -*- coding: utf-8 -*-
"""免费 Bing 网页搜索：抓标题和摘要给模型当参考，不要 API key。

只做两件事：
- search()：按关键词取前几条结果，失败一律返回 []，绝不抛给主流程；
- format_context()：把结果拼成一段给模型的参考文字。

没有新增依赖，用 requests + 标准库正则解析 Bing 的 <li class="b_algo">。
带 5 分钟缓存，避免同一个人连着问同一件事时反复请求。
"""

from __future__ import annotations

import html as _html
import re
import threading
import time

import requests

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_SEARCH_URLS = ("https://cn.bing.com/search", "https://www.bing.com/search")
_CHUNK_SPLIT = '<li class="b_algo"'
_TITLE_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S | re.I)
_HREF_RE = re.compile(r'<h2[^>]*>\s*<a[^>]*?href="([^"]+)"', re.S | re.I)
_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_cache = {}
_cache_lock = threading.Lock()


def _clean(raw: str) -> str:
    """去标签、反转义、压空白；Bing 的摘要里常混着 <strong> 和 &nbsp;。"""
    text = _TAG_RE.sub("", raw or "")
    text = _html.unescape(text).replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


def parse_results(page: str, limit: int = 5) -> list:
    """从 Bing 搜索页 HTML 里提取前 limit 条：标题 / 摘要 / 链接。"""
    out = []
    if not page:
        return out
    chunks = page.split(_CHUNK_SPLIT)[1:]
    for chunk in chunks:
        chunk = chunk.split("</li>", 1)[0]
        title_m = _TITLE_RE.search(chunk)
        if not title_m:
            continue
        title = _clean(title_m.group(1))
        if not title:
            continue
        href_m = _HREF_RE.search(chunk)
        snip_m = _P_RE.search(chunk)
        out.append({
            "title": title,
            "url": _html.unescape(href_m.group(1)) if href_m else "",
            "snippet": _clean(snip_m.group(1)) if snip_m else "",
        })
        if len(out) >= max(1, int(limit)):
            break
    return out


def _fetch(url: str, query: str, timeout: float) -> str:
    resp = requests.get(
        url,
        params={"q": query, "setlang": "zh-Hans", "cc": "cn"},
        headers={
            "User-Agent": _UA,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=timeout,
    )
    if resp.status_code != 200:
        return ""
    if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "latin-1"):
        resp.encoding = "utf-8"
    return resp.text


def search(query: str, limit: int = 5, timeout: float = 8,
           cache_seconds: float = 300) -> list:
    """搜 Bing 前几条；任何异常都吞掉返回 []，由调用方决定怎么兜底。"""
    query = _WS_RE.sub(" ", (query or "")).strip()
    if not query:
        return []
    limit = max(1, min(10, int(limit or 5)))
    timeout = max(2.0, float(timeout or 8))
    now = time.time()
    key = (query.lower(), limit)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] <= max(0.0, float(cache_seconds or 0)):
            return [dict(x) for x in hit[1]]

    results = []
    for url in _SEARCH_URLS:
        try:
            page = _fetch(url, query, timeout)
        except Exception:
            page = ""
        if page and _CHUNK_SPLIT in page:
            results = parse_results(page, limit)
        if results:
            break

    if results:

        with _cache_lock:
            _cache[key] = (now, [dict(x) for x in results])
            if len(_cache) > 64:
                for old in sorted(_cache, key=lambda k: _cache[k][0])[:16]:
                    _cache.pop(old, None)
    return results


def format_context(query: str, results: list) -> str:
    """拼成给模型的参考块；没结果也明确说没查到，方便它老实回答。"""
    query = (query or "").strip()
    if not results:
        return (
            f"【联网搜索】用关键词「{query}」没有查到任何结果。\n"
            "请用你平时的说话方式老实说没查到、不清楚，不要编造，"
            "不要再输出 [搜索:...] 标记，也不要提什么搜索引擎。"
        )
    lines = [f"【联网搜索】用关键词「{query}」查到这些结果："]
    for i, item in enumerate(results, 1):
        title = (item.get("title") or "").strip()
        snippet = (item.get("snippet") or "").strip()
        lines.append(f"{i}. {title}")
        if snippet:
            lines.append(f"   {snippet}")
    lines.append(
        "只根据上面这些内容回答，用你平时的短句和语气；"
        "结果里没有的别猜，查不到的部分就说没查到。"
        "如果结果涉及政治、政府、领导人、战争、灾难、伤亡等严肃内容，"
        "就按最庄重、最准确的方式说，不玩梗、不加颜文字。"
        "不要把网址念出来，不要说“根据搜索结果”，"
        "也不要再输出 [搜索:...] 标记。"
    )
    return "\n".join(lines)
