# -*- coding: utf-8 -*-
"""Chatbot 微信群聊机器人（基于 wechatauto-replica 1.2.8.1，仅 Windows）。

- 消息读取：wechatauto 的本地数据库监听（Listener）
- 发送：wechatauto.guia.quick_send（UIA 优先，坐标/OCR 兜底）
- 前台：微信已在前台就直接发；在后台/被遮挡时临时唤出，发完立刻切回原窗口
- 发送者身份：msg["sender_username"]（新版按分片解析出的真实 wxid）
- 图片：下载 + easyocr 识别文字后交给模型
"""

from __future__ import annotations

import ctypes
import faulthandler
import html
import json
import os
import random
import re
import sys
import threading
import time
import traceback
from ctypes import wintypes
from pathlib import Path

if os.name != "nt":
    sys.stderr.write(
        "此程序只能在 Windows 10/11 上运行：wechatauto-replica 需要 Windows 微信 4.x 客户端。\n"
        "在 Fedora 上请放进 Windows 虚拟机里运行。\n"
    )
    sys.exit(1)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


_SENT_SPLIT = re.compile(r"(?<=[\u3002\uff01\uff1f!?\u2026~\uff5e\uff1b;\n])")

_COMMA_BREAK = re.compile(r"(?<=[\uff0c,\u3001])")

_SPLIT_TAIL_PUNCT = "\uff0c,\u3001\uff1b;\uff1a:"


_ACTION_WORDS = (
    "尾巴", "眼睛", "耳朵", "脸颊", "嘴角", "眉头", "脑袋", "头发", "小手",
    "身子", "身体", "鳞片", "胡子",
    "拍", "甩", "抖", "颤", "鼓", "眨", "瞪", "眯", "捏", "挠", "摸", "揉",
    "抱", "扭", "跳", "蹦", "扑", "晃", "摇", "蹭", "缩", "探", "噘", "撅",
    "捂", "擦", "跺", "踢", "转身", "吐舌", "歪", "愣", "挥手", "招手",
    "点头", "摇头", "叉腰", "耸肩", "摊手",
    "表情", "动作", "笑了笑", "叹气", "嘀咕", "嘟囔", "小声", "低声", "心想",
    "暗想", "喃喃",
)
_ACTION_PAT = re.compile(r"[\uff08(][^\uff08\uff09()]{1,24}[\uff09)]")


_STICKER_MARK = re.compile(r"\[(?:表情|贴纸|表情包)?[:：]?\s*([^\[\]（）()\u3010\u3011]{1,20})\]")


_STICKER_PREFIX = re.compile(r"^\[(?:表情|贴纸|表情包)\s*[:：]")


_LABEL_ALIASES = {}
_BLOCKED_LABELS = set()


def _label_alias(raw: str) -> str:
    """把模型编的标签映射到词表里的标签；映射不到返回空串。"""
    if raw in _LABEL_ALIASES:
        return _LABEL_ALIASES[raw]
    for key, val in _LABEL_ALIASES.items():
        if key and key in raw:
            return val
    return ""


_STICKER_FALLBACKS = (
    "哈哈哈这个表情包有点意思～",
    "这表情包我喜欢，收了收了～",
    "诶？这个表情包好可爱！",
    "收到收到，这表情太传神了吧～",
    "噗，你这表情包绝了～",
)


def _sticker_fallback() -> str:
    return random.choice(_STICKER_FALLBACKS)


def strip_action_parens(text: str) -> str:
    """删掉回复里的动作/神态旁白括号，例如“（尾巴拍打水面）”“（歪着头想了想）”。

    只匹配全角括号，且括号内必须命中动作词，避免误删“（GPT-4）”“（4=2+2）”
    之类正常注释（它们基本不含动作词）。
    """
    def _rep(m):
        inner = m.group(0)[1:-1]
        return "" if any(w in inner for w in _ACTION_WORDS) else m.group(0)

    out = _ACTION_PAT.sub(_rep, text)
    out = re.sub(r"[ \u3000]{2,}", " ", out)
    out = re.sub(r"(?<=[，,。！？!?；;])\s+", "", out)
    return out.strip()


_IDENTITY_WORDS = (
    "超高级人工智能体", "超级人工智能体", "超级人工智能", "人工智能体",
    "人工智能", "超级智慧体", "超级AI", "超级 AI", "大语言模型",
    "语言模型", "大模型", "AI助手", "AI 助手", "智能体", "机器人",
)
_IDENTITY_RE = re.compile("|".join(re.escape(w) for w in
                                   sorted(_IDENTITY_WORDS, key=len, reverse=True)))
_IDENTITY_AI_RE = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])")


def enforce_persona(text: str) -> str:
    """可选身份名称改写；默认保留模型原文，不携带私人人设。"""
    name = str(CFG.get("persona_identity") or "").strip()
    if not text or not name:
        return text
    return _IDENTITY_AI_RE.sub(name, _IDENTITY_RE.sub(name, text))


import requests

from wechatauto import WeChatDB, MediaDownloader
from wechatauto.db import Listener
from wechatauto.guia import quick_send, quick_quote_uia

import memory
import aliases
import asr
import sticker_sender
import sticker_read
import web_search

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
PROMPT_PATH = BASE_DIR / "prompt.txt"
IMAGE_DIR = BASE_DIR / "images"
OCR_MODEL_DIR = BASE_DIR / "ocr_models" / "model"


LOG_MAX_BYTES = 8 * 1024 * 1024


def log(*args) -> None:
    line = time.strftime("[%Y-%m-%d %H:%M:%S]") + " " + " ".join(str(a) for a in args)
    print(line, flush=True)

    try:

        env_dir = os.environ.get("CHATBOT_LOG_DIR")
        log_dir = Path(env_dir) if env_dir else (BASE_DIR / "logs")
        stem = time.strftime("bot_%Y%m%d")
        path = log_dir / (stem + ".log")
        n = 2

        while path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            path = log_dir / ("%s_%d.log" % (stem, n))
            n += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


_CRASH_FILE = None


def _install_crash_hooks() -> None:
    """把硬崩/线程未捕获异常写到 logs/crash_*.log，方便查重启原因。"""
    global _CRASH_FILE
    try:
        env_dir = os.environ.get("CHATBOT_LOG_DIR")
        log_dir = Path(env_dir) if env_dir else (BASE_DIR / "logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / time.strftime("crash_%Y%m%d.log")
        _CRASH_FILE = open(path, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(file=_CRASH_FILE, all_threads=True)
        log(f"崩溃日志：{path}")
    except Exception as e:
        log("崩溃日志初始化失败：", repr(e))

    def _hook(args):
        try:
            name = getattr(args.thread, "name", "?") if args.thread else "?"
            log(f"线程未捕获异常（{name}）：\n"
                + "".join(traceback.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback)))
        except Exception:
            pass

    threading.excepthook = _hook


def _prevent_sleep(on: bool) -> bool:
    """阻止系统空闲睡眠；屏幕仍然可以关闭。"""
    try:
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception as e:
        log("设置防睡眠失败：", repr(e))
        return False


asr.set_logger(log)


def _env(name: str, default: str = "") -> str:
    """从厂商中立的 CHATBOT_ 环境变量读取配置。"""
    return os.environ.get("CHATBOT_" + name, default)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit("请先复制 config.example.json 为 config.json 并填写配置")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["api_key"] = _env("API_KEY", cfg["api_key"])
    cfg["base_url"] = _env("BASE_URL", cfg["base_url"]).rstrip("/")
    cfg["model"] = _env("MODEL", cfg["model"])
    return cfg


CFG = load_config()
if os.environ.get("CHATBOT_REPLY_ALWAYS") == "1":

    CFG["reply_decision_enabled"] = False


OWNER_WXIDS = set(CFG.get("owner_wxids") or [])
OWNER_CHAT = "__owner__"
DATA_DIR = BASE_DIR / "data"
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_KEEP_DAYS = 7
PUSH_STATE_PATH = DATA_DIR / "slang_push_state.json"
PENDING_PATH = DATA_DIR / "pending_sends.json"
SENSITIVE_PATH = DATA_DIR / "sensitive_words.txt"
LOG_KEEP_DAYS = 30
START_TS = time.time()
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8").strip()
_STYLE_FILE = BASE_DIR / "prompt_style.txt"


def _style_prompt() -> str:
    """追加的「说话方式」约束，只在该 AI 调用的系统提示里生效。

    部分接口不支持
    独立 system 角色的接口，两段拼成一条 system 和原来完全等价。
    """
    try:
        return _STYLE_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _sticker_hint() -> str:
    """提示模型可以在回复末尾附一个 [表情:标签]，标签只能从词表里选。

    只在情绪/情景非常贴合、确实想发时才加；拿不准就不加。语言模型只出标签，
    真正点哪个贴纸由 sticker_sender 拿标签查缩略图 + 模板匹配决定。
    """
    try:
        labels = sticker_sender.available_labels()
    except Exception as e:
        log("读取贴纸词表失败：", repr(e))
        return ""
    if not labels:
        return ""
    joined = "、".join(labels)
    return (
        "你可以在回复的最末尾附加一个表情包标记，格式严格为半角 [表情:标签]，"
        "标签只能从下面这个词表里选一个："
        f"{joined}。"
        "只有在情绪或情景非常贴合、确实想发时才加，一条回复最多加一个，拿不准就绝对不加。"
        "能换个更贴切的标签就别老用同一张。"
        "当这句话带着明显情绪（生气、委屈、困、累、惊讶、认错、得意等）时，"
        "请挑一个最贴切的标签加上；平淡的陈述不要加。"
        "这个标记不是你要说给群里的话，不能出现在正文中间，只能出现在整条回复的最末尾。"
    )


_SEARCH_MARK = re.compile(r"\[搜索[:：]\s*([^\]\n]{1,200})\]", re.I)


def _search_hint() -> str:
    """提示模型：需要最新信息时只输出一个 [搜索:关键词]，由程序去查。

    关键词必须短、能直接搜；普通闲聊和它自己知道的事不许触发。
    要搜时整条回复只留标记，避免半截话先发到群里。
    """
    return (
        "如果你要回答的事需要最新、具体、可能已经变化的信息"
        "（新闻、价格、版本、比赛结果、天气、人物近况、某个东西是什么），"
        "而你确定不了，就在回复最后单独写一个 [搜索:关键词]，"
        "格式严格为半角 [搜索:关键词]，关键词 1~20 个字、能直接拿去搜。"
        "这一条只输出这个标记，不要写正文，也不要加表情标记；"
        "程序会去查，再把结果交给你重新回答。"
        "普通闲聊、观点、吐槽、你自己知道的事，绝对不要加这个标记。"
        "但问题里出现「最新」「最近」「前几天」「新出」「刚发布」「新闻」"
        "「价格」「比分」「天气」这类词时，只要你不是百分百确定，就必须先搜，"
        "不能凭记忆编。"
        "不要自己编搜索结果，也不要假装已经查过。"
    )


_SEARCH_EXPLICIT = re.compile(
    r"(搜一下|搜一搜|搜索一下|搜索|查一下|查一查|查查|"
    r"百度一下|谷歌一下|必应一下|上网查)", re.I)
_SEARCH_FRESH = re.compile(
    r"(最新|最近|这几天|前几天|前两天|今天|昨天|刚刚|刚才|今年|这周|本周|"
    r"本月|目前|现在).{0,16}"
    r"(新闻|消息|发布|出了|上线|新出|更新|价格|多少钱|股价|汇率|比分|"
    r"天气|版本|是什么|有什么)", re.S)
_SEARCH_PRODUCT = re.compile(
    r"(新出|新发布|刚发布|刚出|新上线|最新).{0,12}"
    r"([A-Za-z][A-Za-z0-9._-]{1,}|模型|版本|手机|游戏|功能|软件|应用|"
    r"电影|电视剧|歌|agent)", re.I | re.S)
_SEARCH_NEWS_Q = re.compile(
    r"(新闻|头条|快讯).{0,8}(有|什么|哪些|最新|吗|呢|？|\?)"
    r"|(有|什么|哪些|最新|吗|呢|？|\?).{0,8}(新闻|头条|快讯)", re.S)
_SEARCH_ALWAYS = re.compile(r"(天气预报|股价|汇率|比分|彩票|限号)", re.I)
_SEARCH_ASK = re.compile(
    r"(？|\?|吗|呢|什么|哪些|哪个|多少|怎么|怎样|如何|"
    r"有没有|是不是|帮我|搜一下|查一下|看看|想看|来点|说说|讲讲)")

_SEARCH_FILLERS = re.compile(
    r"(给我|帮我|请你|请|麻烦|顺便|能不能|可不可以|可以|"
    r"简介一下|简单介绍|介绍一下|讲解一下|解释一下|科普一下|"
    r"讲讲|说说|聊聊|问一下|想问一下|"
    r"什么是|是什么意思|是什么|有什么用|干什么的|做什么的|"
    r"怎么用|如何使用|怎么样|如何|为什么|为啥|有什么|有哪些|是啥)")
_SEARCH_TIME = re.compile(
    r"(前几天|前两天|这几天|今天|昨天|刚刚|刚才|"
    r"新出的|新发布|刚发布|刚出的|新上线的)")
_NEWS_TIME = re.compile(
    r"(最近|今天|昨天|刚刚|刚才|这几天|前几天|前两天|本周|这周|本月|今年)")
_SEARCH_PUNCT = re.compile(
    r"[，。！？、；：,.!?;:()\[\]【】<>《》\s]+")
_SEARCH_ALIASES = ()


def _needs_search(text: str) -> bool:
    """这条消息是不是明显需要最新信息（关键词兜底，不替代模型判断）。"""
    t = (text or "").strip()
    if not t:
        return False
    if _SEARCH_EXPLICIT.search(t):
        return True
    ask = bool(_SEARCH_ASK.search(t))

    if _SEARCH_ALWAYS.search(t) and ask:
        return True
    if _SEARCH_FRESH.search(t) and ask:
        return True
    if _SEARCH_PRODUCT.search(t) and ask:
        return True
    if _SEARCH_NEWS_Q.search(t):
        return True
    return False


def _build_search_query(text: str) -> str:
    """把口语问题压成短搜索词；太长的问句直接丢给 Bing 反而搜不准。"""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    t = re.sub(r"@\S+\s*", " ", t)
    t = _SEARCH_EXPLICIT.sub(" ", t)

    if re.search(r"新闻|头条|快讯", t):
        topic = _SEARCH_FILLERS.sub(" ", t)
        topic = re.sub(r"新闻|头条|快讯", " ", topic)
        topic = _NEWS_TIME.sub(" ", topic)
        topic = _SEARCH_PUNCT.sub(" ", topic)
        topic = re.sub(r"\s+", " ", topic).strip()
        if not topic:
            return "今日新闻"
        return re.sub(r"\s+", " ", f"{topic} 新闻").strip()[:60]

    t = _SEARCH_FILLERS.sub(" ", t)
    t = _SEARCH_TIME.sub(" ", t)
    for old, new in _SEARCH_ALIASES:
        t = t.replace(old, new)
    t = _SEARCH_PUNCT.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:60]


def _auto_search_query(text: str) -> str:
    """需要预搜时返回搜索词，否则空串。"""
    if not _needs_search(text):
        return ""
    q = _build_search_query(text)
    return q if len(q) >= 2 else ""


def _run_search(query: str) -> list:
    """按配置跑一次搜索；异常一律吞掉返回 []，绝不打断正常回复。"""
    try:
        return web_search.search(
            query,
            limit=int(CFG.get("web_search_max_results", 5) or 5),
            timeout=float(CFG.get("web_search_timeout", 8) or 8),
            cache_seconds=float(
                CFG.get("web_search_cache_seconds", 300) or 300),
        )
    except Exception as e:
        log("联网搜索异常：", repr(e))
        return []


def _detect_search(text: str):
    """从回复里取出 [搜索:关键词]；没有就返回 None。多个时取最后一个。"""
    if not text:
        return None
    hits = _SEARCH_MARK.findall(text)
    if not hits:
        return None
    q = re.sub(r"\s+", " ", hits[-1]).strip()
    return q[:60] or None


def _strip_search_marks(text: str) -> str:
    """把搜索标记从正文里清掉，避免流到群里。"""
    if not text:
        return ""
    return _SEARCH_MARK.sub("", text).strip()


def _search_answer(query: str, results: list, history: list,
                   user_text: str, serious: bool) -> str:
    """带着搜索结果再问模型一次；失败返回 None。"""
    block = web_search.format_context(query, results)
    messages = (
        [_system_message(with_stickers=not serious, serious=serious,
                         allow_search=False)]
        + list(history)
        + [{"role": "user", "content": user_text},
           {"role": "user", "content": block}]
    )
    log(f"联网搜索「{query}」：{len(results)} 条结果，交给模型二次回答")
    return _post_chat(CFG["model"], messages,
                      int(CFG.get("max_tokens", 300)), retries=2)


def _extract_sticker(text: str):
    """把回复里的表情标记剥离掉，返回 (纯净文字, 标签或 None)。

    兼容 [表情:标签] 与裸 [标签]；标签必须命中贴纸词表才当表情指令，
    否则原样保留文字（宁可不发错）。识别到多个时取最后一个，全部剥离。
    """
    text = (text or "").strip()
    try:
        valid = set(sticker_sender.available_labels())
    except Exception:
        valid = set()

    labels_found = []
    out = []
    last = 0
    changed = False
    for m in _STICKER_MARK.finditer(text):

        raw = re.sub(r"\s+", "", m.group(1)).replace("?", "？").replace("!", "！")
        explicit = _STICKER_PREFIX.match(m.group(0)) is not None
        if raw in valid:
            label = raw
        elif explicit:

            label = _label_alias(raw)
        else:
            continue
        changed = True
        out.append(text[last:m.start()])
        last = m.end()
        if label in _BLOCKED_LABELS:
            label = ""
        if label:
            labels_found.append(label)
    if not changed:
        return text, None

    out.append(text[last:])
    cleaned = "".join(out).strip()

    cleaned = cleaned.lstrip("，,、。；; \t")
    if cleaned and cleaned[-1] in "，。！？；、":
        cleaned = cleaned[:-1].strip()

    return cleaned, (labels_found[-1] if labels_found else None)


_SPEAKER_RULES = (
    "【认人与认图，最高优先级】\n"
    "1. 每条用户消息开头的【本条发言者：xxx】才是正在跟你说话的人。"
    "历史里出现过的其它名字都属于别人：不要把那些名字安到这个人头上，"
    "也不要以为现在是别人在说话。\n"
    "2. 发言者后面括号里的内部ID、以及名字末尾的尾号（比如“群友1234”里的"
    "1234）都只用来区分同名的人，绝对不要写进回复。\n"
    "3. 拿不准对方是谁、或者对方没有名字时，直接用“你”，不要硬套一个名字。\n"
    "4. 看图不要猜角色名、作品名、人名、性别、身份。图上没有明确写出名字，"
    "就只说“这个角色”“这位coser”“图里这个人”；别人报一个名字问你对不对，"
    "你看不出来就说不确定，绝对不要顺着确认。\n"
    "5. 你之前说过的判断也可能是错的。别人纠正你时直接改口，不要为了圆前面的"
    "话硬撑，也不要把自己上一句猜的名字当成事实再重复一遍。\n"
    "6. 仅本条身份提示明确标注管理员时才使用管理员称呼。"
    "本条发言者不是他时，绝对不要叫对方管理员/管理员，也不要把管理员的身份"
    "安到别人头上；叫对方的名字，或者直接用“你”。"
)


_SERIOUS_WORDS = (
    "国家", "国歌", "国旗", "国徽", "祖国", "爱国", "中华",
    "政治", "政府", "共产党", "党员", "主席", "总书记", "总统", "总理",
    "首相", "领导人", "书记", "人大", "政协", "两会", "政策", "外交",
    "制裁", "领土", "台湾", "香港", "澳门", "新疆", "西藏", "南海",
    "钓鱼岛", "战争", "军事", "军队", "解放军", "导弹", "核武", "阅兵",
    "烈士", "英雄", "抗战", "抗日", "革命", "文革", "六四", "天安门",
    "民族", "宗教", "宪法", "判决", "死刑", "游行", "抗议", "示威",
    "民主", "人权", "选举", "疫情", "地震", "洪水", "台风", "事故",
    "遇难", "伤亡", "灾难", "灾情",
    "义勇军进行曲", "血肉筑成", "不愿做奴隶", "最危险的时候",
    "冒着敌人的炮火",
)

_SERIOUS_RULES = (
    "【严肃话题，绝对最高优先级，覆盖前面所有人设和语气设定】\n"
    "1. 话题涉及国家、政治、政府、政党、领导人、政策、时事、历史事件、"
    "战争、军事、领土、民族、宗教、法律、公共安全、灾难事故、疫情、伤亡、"
    "国旗、国歌、国徽、烈士、英雄时，立刻收起人设，用最庄重、最准确、"
    "最平实的话回答，像正常人认真讲话那样。\n"
    "2. 这时禁止颜文字、表情包标签、玩梗、网络流行语、夸张语气、调侃、"
    "反问、卖萌、括号旁白；能一句答完就只答一句。\n"
    "3. 歌词、国歌、诗词、名言、法律条文、事实数据必须按原文或公认事实"
    "准确回答，不擅自改写、续写或添加调侃。\n"
    "4. 不评价、不猜测、不站队、不调侃政治人物、政策、政权、国际冲突，"
    "不传播未经证实的消息；被追问看法就说「这个我不聊」。\n"
    "5. 不确定就说「这个我不清楚」，绝不编造，也不用玩笑糊过去。"
)

_SERIOUS_UNTIL = {}


def is_serious_topic(text: str) -> bool:
    """这条内容算不算严肃话题（涉政 / 国家象征 / 灾难伤亡等）。"""
    t = (text or "").strip()
    if not t:
        return False
    return any(w in t for w in _SERIOUS_WORDS)


def _serious_mode(text: str, chat: str = None) -> bool:
    """严肃话题判定 + 冷却保持：跟进的「？」「然后呢」也不会突然开始玩梗。"""
    if not CFG.get("enable_serious_guard", True):
        return False
    key = _hist_key(chat)
    now = time.time()
    hold = float(CFG.get("serious_hold_seconds", 300) or 0)
    if is_serious_topic(text):
        _SERIOUS_UNTIL[key] = now + hold
        log("严肃话题，切换庄重模式：", (text or "")[:30])
        return True
    return now < _SERIOUS_UNTIL.get(key, 0)


def _drop_kaomoji(text: str) -> str:
    """只删括号里的纯符号颜文字；括号里的正经注释（如「（明天）」）保留。"""
    def _rep(m):
        inner = m.group(0)[1:-1]
        return "" if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", inner) else m.group(0)

    return _KAOMOJI_RE.sub(_rep, text)


def _serious_scrub(text: str) -> str:
    """庄重模式发送前清洗：去动作旁白、颜文字、表情标记、emoji、多余空白。"""
    t = strip_action_parens(text or "")
    t = _drop_kaomoji(t)
    t, _label = _extract_sticker(t)
    t = re.sub(r"\[表情[:：][^\]]*\]", "", t)
    t = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]", "", t)

    t = re.sub(r"[\u309d\u309e\uff9f\uff40\u00b4\uffe3\u266a\u2661\u2605\u2606]", "", t)
    t = re.sub(r"[ \t\u3000]{2,}", " ", t)
    t = re.sub(r"(?<=[，,。！？!?；;])\s+", "", t)
    return t.strip()


def _now_prompt() -> str:
    """把当前时间告诉模型：被问「现在几点/今天几号」时别瞎编。"""
    now = time.localtime()
    week = "一二三四五六日"[now.tm_wday]
    return (f"现在是 {time.strftime('%Y-%m-%d %H:%M', now)}（星期{week}）。"
            "有人问时间、日期、星期几，就按这个回答，不要编造别的时间。")


def _system_message(with_stickers: bool = False, serious: bool = False,
                    allow_search: bool = True,
                    alias_text: str = "") -> dict:
    style = _style_prompt()
    content = f"{SYSTEM_PROMPT}\n\n{style}" if style else SYSTEM_PROMPT

    content = f"{content}\n\n{_SPEAKER_RULES}\n\n{_now_prompt()}"
    if allow_search and not serious and CFG.get("web_search_enabled", True):
        content = f"{content}\n\n{_search_hint()}"
    if serious:

        content = f"{content}\n\n{_SERIOUS_RULES}"
    if with_stickers and not serious:
        hint = _sticker_hint()
        if hint:
            content = f"{content}\n\n{hint}"


    if alias_text:
        content = f"{content}\n\n{alias_text}"
    return {"role": "system", "content": content}


db = None
md = None
self_wxid = ""
group_username = ""

GROUP_BY_USERNAME = {}

PRIVATE_CHATS = {}


GROUP_MEMBERS = {}
_roster_ts = {}


def _chat_who(chat: str = None) -> str:
    """内部会话 username → 发送时用的群显示名；未知/空回主群。"""
    if chat == OWNER_CHAT:
        return str(CFG.get("owner_remark") or "")
    if chat in PRIVATE_CHATS:
        return PRIVATE_CHATS[chat]
    return GROUP_BY_USERNAME.get(chat or "", CFG.get("group_name", ""))


def _hist_key(chat: str = None) -> str:
    """上下文/去重按会话分开；没传会话就按主群算。"""
    return chat or group_username or ""


def _chat_tag(chat: str = None) -> str:
    """日志里标出这条属于哪个群；多群以后不标就分不清来源。"""
    name = GROUP_BY_USERNAME.get(chat or "", "")
    if not name and chat in PRIVATE_CHATS:
        name = "私聊:" + PRIVATE_CHATS[chat]
    return f"[{name}]" if name else ""


def _is_private_chat(chat: str = None) -> bool:
    """这个会话是不是私聊（不是群）。私聊永远接话，也不参与群功能。"""
    return bool(chat) and chat in PRIVATE_CHATS


def _listener_chats() -> list:
    """所有监听目标：托管的群 + 配置里的私聊。"""
    out = list(GROUP_BY_USERNAME)
    for uid in PRIVATE_CHATS:
        if uid not in out:
            out.append(uid)
    return out


def _chat_display(chat: str) -> str:
    """日志里显示的名字：群名或私聊备注。"""
    return (GROUP_BY_USERNAME.get(chat or "")
            or PRIVATE_CHATS.get(chat or "") or chat or "")


def _load_group_members(chat: str) -> int:
    """读某个群的成员昵称表（纯读库，不碰微信窗口）。

    返回读到的成员数；失败只当没有这张表，不影响收发。
    """
    if not chat or db is None:
        return 0
    try:
        rows = db.get_group_members(chat)
    except Exception as e:
        log(f"读取群成员失败（{chat}）：{e!r}")
        return 0
    table = {}
    for r in rows or []:
        w = str(r.get("username") or "").strip()
        if not w:
            continue
        nm = str(r.get("remark") or r.get("nick_name") or "").strip()
        if nm:
            table[w] = nm
    GROUP_MEMBERS[chat] = table
    _roster_ts[chat] = time.time()
    return len(table)


def _roster_name(wxid: str, chat: str = None) -> str:
    """按群查昵称；表里没有就重读一次（最多 60 秒一次），再退回全局昵称。"""
    if not wxid:
        return ""
    key = _hist_key(chat)
    nm = (GROUP_MEMBERS.get(key) or {}).get(wxid) or ""
    if not nm and key and time.time() - _roster_ts.get(key, 0.0) > 60:
        _load_group_members(key)
        nm = (GROUP_MEMBERS.get(key) or {}).get(wxid) or ""
    if not nm:
        try:
            nm = db.get_nickname(wxid) or ""
        except Exception:
            nm = ""
    return str(nm).strip()


_send_lock = threading.Lock()
_last_self_send = 0.0

SEND_WINDOW_ERROR = "未找到微信主窗口"
_pending_lock = threading.Lock()
_pending_sends = []
_last_self_send_by_chat = {}
_recent_replies = {}
_recent_lock = threading.Lock()


_sticker_lock = threading.Lock()
_last_sticker_send = {}

_last_sticker_label = {}


_self_stickers = {}
_self_sticker_lock = threading.Lock()
_sticker_send_start = {}


_handle_lock = threading.Lock()
_seen_msgs = {}
_seen_lock = threading.Lock()
SEEN_TTL = 600.0


def _msg_seen(msg: dict) -> bool:
    """记录 (会话, local_id, create_time)；返回 True 表示这条已经处理过。

    键里不再放 shard：同一条消息在分片切换/重放时可能带不同 shard，之前会
    因此漏判、对同一条消息回两次。local_id 与时间戳都相同就按同一条处理。
    """
    lid = msg.get("local_id")
    if lid is None:
        return False
    try:
        ct = int(msg.get("create_time") or 0)
    except Exception:
        ct = 0
    key = (msg.get("username") or group_username, str(lid), ct)
    now = time.time()
    with _seen_lock:
        if len(_seen_msgs) > 500:
            for k in [k for k, t in _seen_msgs.items() if now - t > SEEN_TTL]:
                _seen_msgs.pop(k, None)
        ts = _seen_msgs.get(key)
        if ts is not None and now - ts <= SEEN_TTL:
            return True
        _seen_msgs[key] = now
        return False

_ocr_reader = None
_ocr_failed = False
_ocr_lock = threading.Lock()

SKIP_MARKERS = (
    "[文本]", "[图片]", "[视频]", "[语音]", "[文件]", "[链接]",
    "[动画表情]", "[系统消息]", "[位置]", "[音视频通话]",
)


_round_lock = threading.Lock()
_round_buffers = {}
_round_thread = {"thread": None, "lock": threading.Lock()}


def _round_window() -> float:
    try:
        return max(0.3, float(CFG.get("aggregate_window_seconds", 2.5) or 2.5))
    except (TypeError, ValueError):
        return 2.5


def _round_max_wait() -> float:
    try:
        return max(_round_window(),
                   float(CFG.get("aggregate_max_seconds", 6.0) or 6.0))
    except (TypeError, ValueError):
        return max(_round_window(), 6.0)


def _round_max_batch() -> int:
    try:
        return max(2, int(CFG.get("aggregate_max_messages", 10) or 10))
    except (TypeError, ValueError):
        return 10


def _round_line(msg: dict, chat: str) -> str:
    """把一条消息压成一行上下文；认不出的类型返回空串。"""
    mtype = msg.get("type")
    content = str(msg.get("content") or "").strip()
    wxid = sender_wxid(msg)
    nick = prompt_nick(display_name(wxid, chat))
    tag = _speaker_tag(nick, wxid)
    if mtype == "文本":
        content = re.sub(r"^(wxid_[A-Za-z0-9]+):\s*", "", content, count=1)
        if not content or content in SKIP_MARKERS:
            return ""
        if any(content.startswith(m) for m in SKIP_MARKERS):
            return ""
        return f"{tag}\n{nick}说：{content}"
    if mtype == "图片":
        return f"{tag}\n{nick}发来一张图片"
    if mtype == "动画表情":
        known = None
        try:
            md5 = sticker_read.emoji_md5(str(msg.get("content") or ""))
            known = sticker_read.label_for_md5(md5) if md5 else None
        except Exception:
            known = None
        if known:
            return f"{tag}\n{nick}发了一个表情包，已认出「{known}」"
        return f"{tag}\n{nick}发了一个表情包"
    if mtype == "语音":
        try:
            said = transcribe_voice(msg, chat)
        except Exception:
            said = ""
        if said:
            return f"{tag}\n{nick}说：{said}（语音）"
        return f"{tag}\n{nick}发来一条语音（没听清）"
    if mtype == "视频":
        return f"{tag}\n{nick}发来一条视频"
    if mtype in ("文件/链接/卡片", "位置", "引用"):
        info = _parse_appmsg(content) if mtype != "位置" else {}
        refer = info.get("refer")
        title = (info.get("title") or "").strip()
        if refer:
            said = title
            if not said:
                return f"{tag}\n{nick}引用了一条消息"
            return (f"{tag}\n{nick}说：{said}"
                    f"（他引用了 {refer['name']} 的「{refer['text'][:40]}」）")
        if mtype == "位置":
            return f"{tag}\n{nick}发来一个位置"
        if not title:
            return ""
        return f"{tag}\n{nick}分享了一条{mtype}：{title[:40]}"
    return ""


def _round_remember(msg: dict, chat: str) -> None:
    """把这一轮里较早的文本也过一遍记忆抽取，和逐条处理时一致。"""
    if msg.get("type") not in ("文本", "引用"):
        return
    try:
        content = str(msg.get("content") or "")
        if msg.get("type") == "文本":
            content = re.sub(r"^(wxid_[A-Za-z0-9]+):\s*", "", content, count=1)
        else:
            info = _parse_appmsg(content)
            content = (info.get("title") or "").strip()
        if not content:
            return
        wxid = sender_wxid(msg)
        _remember_from_text(content, prompt_nick(display_name(wxid, chat)),
                            wxid, chat)
    except Exception:
        pass


def _process_round(msgs: list) -> None:
    """处理一批连续消息：前面的进上下文，最后一条走完整的回复流程。"""
    fresh = []
    now = time.time()
    limit = float(CFG.get("max_reply_age_seconds", 90) or 0)
    for msg in sorted(msgs, key=lambda m: (m.get("sort_seq") or 0,
                                           m.get("create_time") or 0)):
        try:
            if is_self_message(msg):
                continue
        except Exception:
            pass
        if msg.get("type") == "系统消息":
            continue
        ct = msg.get("create_time")
        if limit > 0 and ct and now - float(ct) > limit:
            continue
        fresh.append(msg)
    if not fresh:
        return
    if len(fresh) == 1:
        handle_message(fresh[0])
        return

    chat = fresh[0].get("username") or group_username
    lines = []
    for msg in fresh[:-1]:
        try:
            line = _round_line(msg, chat)
        except Exception:
            log("这一轮里有条消息解析失败，跳过：\n" + traceback.format_exc())
            line = ""
        if not line:
            continue
        _round_remember(msg, chat)
        _history_add("user", line, chat)
        lines.append(line)
    extra = "\n".join(lines)
    log(f"{_chat_tag(chat)}合成一轮：{len(fresh)} 条消息，"
        f"{len(lines)} 条先记上下文，最后一条再判断")
    handle_message(fresh[-1], round_extra=extra,
                   round_msgs=fresh[:-1])


def _round_worker() -> None:
    while True:
        try:
            time.sleep(0.25)
            due = []
            now = time.time()
            with _round_lock:
                for chat, buf in list(_round_buffers.items()):
                    if not buf.get("msgs"):
                        _round_buffers.pop(chat, None)
                        continue
                    quiet = now - buf["last"]
                    age = now - buf["first"]
                    if (quiet >= _round_window() or age >= _round_max_wait()
                            or len(buf["msgs"]) >= _round_max_batch()):
                        due.append((chat, buf["msgs"]))
                        _round_buffers.pop(chat, None)
            for chat, msgs in due:
                try:
                    with _handle_lock:
                        _process_round(msgs)
                except Exception:
                    log("处理这一轮消息异常：\n" + traceback.format_exc())
        except Exception:
            log("消息聚合线程异常：\n" + traceback.format_exc())
            time.sleep(1.0)


def _ensure_round_worker() -> None:
    with _round_thread["lock"]:
        t = _round_thread.get("thread")
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=_round_worker, name="round-worker",
                             daemon=True)
        t.start()
        _round_thread["thread"] = t


def _handle_video_now(msg: dict, chat: str) -> None:
    """视频立即处理，不等聚合。

    点缩略图让微信下载本体只对看得见的最新一条有效，等 2.5 秒很
    可能被后一条文字盖过去；而且用户希望视频本身也被回应，而不是
    被后一条文字顶掉。前面刚收的文字先当这一轮的上下文。
    """
    with _round_lock:
        buf = _round_buffers.pop(chat, None)
    prior = list((buf or {}).get("msgs") or [])
    lines = []
    for m in prior:
        try:
            if is_self_message(m) or m.get("type") == "系统消息":
                continue
            line = _round_line(m, chat)
            if not line:
                continue
            _round_remember(m, chat)
            _history_add("user", line, chat)
            lines.append(line)
        except Exception:
            log("视频前一条消息解析失败，跳过：\n" + traceback.format_exc())
    if lines:
        log(f"{_chat_tag(chat)}收到视频，前面 {len(lines)} 条先记上下文")
    with _handle_lock:
        try:
            handle_message(msg, round_extra="\n".join(lines),
                           round_msgs=prior)
        except Exception:
            log("处理视频消息异常：\n" + traceback.format_exc())


def _enqueue_round(msg: dict) -> None:
    """收消息先入缓冲；等这一小段说完再统一判断，避免逐条点名回复。"""
    if not CFG.get("aggregate_enabled", True):
        with _handle_lock:
            try:
                handle_message(msg)
            except Exception:
                log("处理消息异常：\n" + traceback.format_exc())
        return
    chat = msg.get("username") or group_username
    if msg.get("type") == "视频":
        _handle_video_now(msg, chat)
        return
    now = time.time()
    with _round_lock:
        buf = _round_buffers.get(chat)
        if buf is None:
            buf = {"msgs": [], "first": now, "last": now}
            _round_buffers[chat] = buf
        buf["msgs"].append(msg)
        buf["last"] = now
    _ensure_round_worker()


def get_ocr():
    global _ocr_reader, _ocr_failed
    if not CFG.get("enable_ocr", True) or _ocr_failed:
        return None
    if _ocr_reader is not None:
        return _ocr_reader
    with _ocr_lock:
        if _ocr_reader is not None:
            return _ocr_reader
        try:
            import easyocr
            langs = CFG.get("ocr_langs") or ["ch_sim", "en"]
            log("正在初始化 OCR（首次会下载模型，可能较慢）...")
            _ocr_reader = easyocr.Reader(
                langs, gpu=False,
                model_storage_directory=str(OCR_MODEL_DIR))
            log("OCR 初始化完成")
        except Exception as e:
            _ocr_failed = True
            log("OCR 初始化失败，图片只按原样说明处理：", repr(e))
            return None
    return _ocr_reader


def _ocr_filter(result, min_score: float = 0.5) -> str:
    """从 OCR 原始结果里只取置信度够高的文字，散字噪声丢掉。"""
    out = []
    for item in result or []:
        try:
            text = item[1]
            score = float(item[2])
        except Exception:
            continue
        if not text or score < min_score:
            continue
        out.append(str(text))
    return " ".join(out).strip()


def ocr_text(path: str) -> str:
    reader = get_ocr()
    if reader is None:
        return ""
    min_score = float(CFG.get("ocr_min_score", 0.5) or 0.5)
    try:
        result = reader.readtext(path, detail=1)
        return _ocr_filter(result, min_score)
    except TypeError:

        try:
            result = reader.readtext(path, detail=0)
            return " ".join(str(x) for x in result).strip()
        except Exception as e:
            log("OCR 识别失败：", repr(e))
            return ""
    except Exception as e:
        log("OCR 识别失败：", repr(e))
        return ""


def _rate_limited(message: str) -> bool:
    s = str(message).lower()
    return any(k in s for k in ("rate", "tpm", "rpm", "throttl", "limit"))


def _retry_after(resp) -> int:
    try:
        return int(resp.headers.get("Retry-After", "0"))
    except (TypeError, ValueError):
        return 0


_history = {}
_history_lock = threading.Lock()


def _history_add(role: str, content: str, chat: str = "") -> None:
    limit = max(1, int(CFG.get("history_messages", 10)))
    key = _hist_key(chat)
    with _history_lock:
        buf = _history.setdefault(key, [])
        buf.append((time.time(), role, content))
        while len(buf) > limit:
            buf.pop(0)


def _history_window(chat: str = "") -> list:
    limit = max(1, int(CFG.get("history_messages", 10)))
    gap = float(CFG.get("history_reset_gap_seconds", 1800))
    now = time.time()
    key = _hist_key(chat)
    with _history_lock:
        buf = _history.setdefault(key, [])
        if buf and now - buf[-1][0] > gap:
            buf.clear()
        items = list(buf)[-limit:]
    return [{"role": r, "content": c} for _, r, c in items]


def _post_chat(model: str, messages: list, max_tokens: int, retries: int = None):
    """发一次 chat/completions（含 429 退避），成功返回文本，失败返回 None。

    只负责请求，不碰上下文；上下文由 ask_ai 统一维护。
    """
    payload = {
        "model": model,
        "messages": messages,
        "temperature": CFG.get("temperature", 0.7),
        "max_tokens": max_tokens,
    }
    url = CFG["base_url"] + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {CFG['api_key']}",
        "Content-Type": "application/json",
    }

    retries = max(1, int(retries or CFG.get("retries", 3)))


    retries = max(retries, 4)
    for attempt in range(retries):
        try:
            resp = requests.post(
                url, headers=headers, json=payload,
                timeout=CFG.get("request_timeout", 120),
            )
            if resp.status_code == 429:
                wait = _retry_after(resp) or min(30, 5 * (attempt + 1))
                log(f"API 限流（429），{wait}s 后重试 {attempt + 1}/{retries}")
                time.sleep(wait)
                continue

            try:
                data = resp.json()
            except ValueError:
                data = None

            if isinstance(data, dict) and data.get("error") and not data.get("choices"):
                err = data["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                if _rate_limited(msg):
                    wait = min(30, 5 * (attempt + 1))
                    log(f"API 限流：{msg}，{wait}s 后重试 {attempt + 1}/{retries}")
                    time.sleep(wait)
                    continue
                log("API 返回错误：", msg)
                return None

            resp.raise_for_status()
            choice = data["choices"][0]
            reply = (choice["message"].get("content") or "").strip()
            if not reply:
                finish = choice.get("finish_reason") or ""
                log(f"API 返回空内容（finish_reason={finish or '?'}）")
                if finish == "length" and max_tokens < 1200:


                    bigger = min(1200, max(600, int(max_tokens) * 2))
                    log(f"推理占满 {max_tokens} tokens，扩大到 {bigger} 重试")
                    max_tokens = bigger
                    payload["max_tokens"] = bigger
                    continue
                if attempt < retries - 1:


                    time.sleep(1.0 + attempt)
                    continue
                return None
            usage = data.get("usage") or {}
            log(f"AI 回复（tokens={usage.get('total_tokens', '?')}）：{reply}")
            return reply
        except Exception as e:
            log(f"AI 调用失败（{attempt + 1}/{retries}）：", repr(e))
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


_vision_state = {"fail": 0, "skip_until": 0.0}


def _vision_note_ok() -> None:
    _vision_state["fail"] = 0
    _vision_state["skip_until"] = 0.0


def _vision_note_fail(reason: str = "") -> int:
    """记一次视觉失败；连续两次就冷却一段时间，之后还会重试。"""
    st = _vision_state
    st["fail"] = int(st.get("fail", 0)) + 1
    if st["fail"] >= 2:
        cd = int(CFG.get("vision_cooldown_seconds", 1800) or 1800)
        st["skip_until"] = time.time() + cd
        log(f"视觉模型连续失败 {st['fail']} 次，暂停 {max(1, cd // 60)} 分钟"
            f"再试（{reason or '未知原因'}）")
    return st["fail"]
_OCR_JUNK = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")


def _ocr_usable(text: str) -> bool:
    """OCR 结果是不是像话：至少两个字，排除一堆单字散开的噪声。"""
    s = str(text or "").strip()
    if not s:
        return False
    core = _OCR_JUNK.sub("", s)
    if len(core) < 2:
        return False
    tokens = [t for t in re.split(r"\s+", s) if t]
    if len(tokens) >= 6:
        avg = sum(len(_OCR_JUNK.sub("", t)) for t in tokens) / float(len(tokens))
        if avg < 1.2:
            return False
    return True


def _image_data_url(path: str) -> str:
    """把本地图片缩放后转成 data URL，供视觉模型使用。"""
    import base64
    import io

    from PIL import Image

    max_side = int(CFG.get("vision_max_side", 1280))
    with Image.open(path) as im:
        im = im.convert("RGB")
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


_OWNER_CALL_RE = re.compile(r"(管理员)(?=[你您])")
_OWNER_CALL_HEAD_RE = re.compile(r"^(管理员)(?=[，,。！？!?、\s]|$)")


def _owner_recently_spoke(chat: str = "", within: float = 300.0) -> bool:
    """管理员最近有没有在这个会话里说过话。"""
    if not OWNER_WXIDS:
        return False
    try:
        with _history_lock:
            buf = list(_history.get(_hist_key(chat)) or [])
    except Exception:
        return False
    now = time.time()
    for ts, role, content in reversed(buf[-10:]):
        if role != "user":
            continue
        if now - ts > within:
            break
        for uid in OWNER_WXIDS:
            if uid and uid in str(content or ""):
                return True
    return False


def _fix_owner_call(text: str, sender_id: str = "", sender_nick: str = "",
                    chat: str = "") -> str:
    """不是管理员就绝不叫管理员：只改直接对着对方说的那种（「管理员你」「管理员，」）。

    管理员没在本会话出现过时才动手，免得把「我管理员是管理员」这种正经提法也改掉。
    """
    if not text or not sender_id or sender_id in OWNER_WXIDS:
        return text
    nick = str(sender_nick or "").strip()
    if not nick or nick.startswith("群友") or nick in ("未知", "某人", "对方"):
        return text
    if _owner_recently_spoke(chat):
        return text
    out = _OWNER_CALL_RE.sub(nick, text)
    out = _OWNER_CALL_HEAD_RE.sub(nick, out)
    if out != text:
        log(f"{_chat_tag(chat)}称呼纠正：把「管理员」改成「{nick}」")
    return out


def ask_ai(user_message: str, sender_nick: str, image_path: str = None,
           chat: str = "", sender_id: str = "", proactive: bool = False,
           alias_extra: str = "", media_label: str = "图片"):
    """返回 (回复文本, 表情标签)。表情标签为 None 表示本条不附带贴纸。"""
    history = _history_window(chat)
    who = _speaker_tag(sender_nick, sender_id)

    serious = _serious_mode(user_message, chat)


    ident = ""
    if sender_id and sender_id in OWNER_WXIDS:
        ident = "【身份】本条发言者就是配置指定的管理员，可以使用管理员称呼。"
    elif sender_id and sender_nick:
        ident = (f"【身份】本条发言者「{sender_nick}」不是管理员。"
                 f"称呼只用「{sender_nick}」或“你”，绝不能叫管理员/管理员。")


    vision_path = image_path
    if image_path:
        left = float(_vision_state.get("skip_until", 0) or 0) - time.time()
        if left > 0:
            log(f"视觉模型冷却中（还剩 {int(left // 60) + 1} 分钟），"
                "这次直接走 OCR")
            vision_path = None
    if vision_path and CFG.get("enable_vision", True):
        try:
            data_url = _image_data_url(vision_path)
        except Exception as e:
            data_url = None
            _vision_note_fail("图片读取失败")
            log("图片读取失败，改用 OCR：", repr(e))
        if data_url:
            vision_model = CFG.get("vision_model", "YOUR_MODEL")
            if media_label == "视频":
                look = (f"{who}\n{sender_nick}发来一段视频，"
                        "这里看到的是从视频里抽出来的画面。")
            else:
                look = f"{who}\n{sender_nick}发来一张图片。"
            vision_text = (
                f"{look}{user_message}\n"
                + (ident + "\n" if ident else "")
                + "【看图的硬规矩】只说你确实看到的东西：人物外观、服装、"
                "手里拿的、背景、图上写着的文字。不要猜角色名、作品名、"
                "人名、性别、身份；图上没有明确写出名字，就只说“这个角色”"
                "“这位coser”“图里这个人”。觉得眼熟也不要把名字说出口。"
                "对方报了一个名字问你是不是，你看不出来就说不确定，"
                "不要顺着确认。"
            )
            content = [
                {"type": "text", "text": vision_text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
            log(f"用视觉模型识图：{vision_model}")
            reply = _post_chat(
                vision_model,
                [_system_message(serious=serious, allow_search=False,
                                 alias_text=_alias_text(
                                     chat, user_message + "\n"
                                     + (alias_extra or "")))]
                + history
                + [{"role": "user", "content": content}],
                int(CFG.get("vision_max_tokens", 300)),
                retries=2,
            )
            if reply:
                _vision_note_ok()
                reply = strip_action_parens(reply)
                reply = _fix_owner_call(reply, sender_id, sender_nick, chat)
                if serious:
                    reply = _serious_scrub(reply)
                else:
                    reply = enforce_persona(reply)
                if not reply:
                    log("动作描写剥离后为空，改用 OCR 文本方案")
                else:
                    if not proactive:
                        _history_add(
                            "user", f"{who}\n{sender_nick}说：{user_message}",
                            chat)
                    _history_add("assistant", reply, chat)
                    return reply, None
            n = _vision_note_fail("视觉模型没有回复")
            log(f"视觉模型没有回复，改用 OCR 文本方案（连续失败 {n} 次）")


    text = user_message
    if image_path:
        word = "画面" if media_label == "视频" else "图片"
        ocr = ocr_text(image_path)
        if _ocr_usable(ocr):
            text = f"{text}\n（{word}中的文字内容：{ocr}）"
            log("OCR 结果：", ocr)
        elif ocr:

            log("OCR 结果太碎，按识别不清处理：", ocr[:60])
            text = f"{text}\n（{word}没能看清，别猜里面的文字和内容）"
        else:
            text = f"{text}\n（{word}中没有识别到文字，或 OCR 不可用）"




    prompt_text = f"{who}\n{sender_nick}说：{text}"
    if serious:

        prompt_text += (
            "\n【严肃话题】这条必须按最庄重、最准确的方式回答：不要颜文字、"
            "不要表情包、不要玩梗、不加多余的话；涉及原文就只回原文。"
        )


    if not proactive:
        _history_add("user", prompt_text, chat)

    send_text = prompt_text
    if ident:

        send_text = send_text + "\n" + ident
    if not serious:

        ask_all = bool(_RECALL_ASK.search(user_message or ""))
        mem = _memory_hint(chat, sender_id, sender_nick,
                           limit=99 if ask_all else None)
        if mem:
            send_text = mem + "\n\n" + send_text
    pre_query = ""
    pre_results = []
    if (not serious and not image_path and not proactive
            and CFG.get("web_search_enabled", True)):
        pre_query = _auto_search_query(user_message)
        if pre_query:
            pre_results = _run_search(pre_query)
            send_text = (send_text + "\n\n"
                         + web_search.format_context(pre_query, pre_results))
            log(f"按问题判断需要联网：{pre_query}"
                f"（{len(pre_results)} 条结果）")
    reply = _post_chat(
        CFG["model"],
        [_system_message(with_stickers=not serious, serious=serious,
                         allow_search=not pre_query,
                         alias_text=_alias_text(
                             chat, send_text + "\n" + (alias_extra or "")))] + history
        + [{"role": "user", "content": send_text}],
        int(CFG.get("max_tokens", 300)),
    )
    if reply:
        query = _detect_search(reply)
        if (query and not serious and not pre_query
                and CFG.get("web_search_enabled", True)):

            results = _run_search(query)
            answer = _search_answer(query, results, history, send_text,
                                    serious)
            if answer:
                reply = answer
            else:

                reply = "这个我没查到，不瞎编 (￣▽￣)"
                log("联网搜索后二次回答失败，改用兜底回复")
        elif query:

            reply = _strip_search_marks(reply) or (
                "这个我不清楚" if serious else "这个我得查查才知道")
        reply = _strip_search_marks(reply)
        reply = strip_action_parens(reply)
        reply = _fix_owner_call(reply, sender_id, sender_nick, chat)
        if serious:

            reply, label = _serious_scrub(reply), None
        else:
            reply = enforce_persona(reply)
            reply, label = _extract_sticker(reply)
            if not label:

                label = _auto_sticker(reply, chat)
        if reply or label:

            if (proactive and not label
                    and _proactive_is_silent(reply)):
                return reply, label

            _history_add("assistant", reply if reply else f"[表情:{label}]", chat)
            return reply, label
        log("动作描写剥离后为空，本轮没有可发送的内容")
    log("本轮没有回复；这轮内容已记入上下文，用户追问时仍能接上")
    return None, None


def sender_wxid(msg: dict) -> str:
    wxid = (msg.get("sender_username") or "").strip()
    if wxid:
        return wxid
    m = re.search(r"\b(wxid_[A-Za-z0-9]+)\s*:", str(msg.get("content") or ""))
    return m.group(1) if m else ""


def _remember_reply(text: str, chat: str = "") -> None:
    now = time.time()
    ttl = float(CFG.get("recent_reply_ttl", 120))
    key = (_hist_key(chat), text)
    with _recent_lock:
        _recent_replies[key] = now
        for k in [k for k, t in _recent_replies.items() if now - t > ttl]:
            _recent_replies.pop(k, None)


def _is_recent_reply(text: str, chat: str = None) -> bool:
    """这条文字是不是我刚在同一个群发过。chat=None 表示不限群。"""
    now = time.time()
    ttl = float(CFG.get("recent_reply_ttl", 120))
    with _recent_lock:
        if chat is None:
            ts = max([t for (_c, x), t in _recent_replies.items() if x == text] or [0])
        else:
            ts = _recent_replies.get((_hist_key(chat), text))
        return bool(ts and now - ts <= ttl)


def is_self_message(msg: dict, chat: str = None) -> bool:
    """这条消息是不是本机（我）发的。

    优先用 DB 解析出的真实发送者比对：解析得出就直接采信结论。只有
    **解析不出发送者**时才退回「内容像不像我刚发过的」这一模糊判断——
    之前无条件用内容兜底，别人的话偶尔和我的回复撞字，就会被当成自己
    而漏回，甚至反过来把别人当成我。
    """

    if _is_self_sent_sticker(msg, chat):
        return True
    wxid = (msg.get("sender_username") or "").strip()
    if wxid and self_wxid:
        return wxid == self_wxid
    if wxid and not self_wxid:
        return False
    content = str(msg.get("content") or "").strip()
    return bool(content) and _is_recent_reply(
        content, chat or msg.get("username") or "")


def prompt_nick(nick: str) -> str:
    """公开示例保留使用者的昵称，不应用私人的称呼规则。"""
    return str(nick or "").strip()


def _speaker_tag(nick: str, sender_id: str = "") -> str:
    """标出这条消息是谁说的；带内部ID只为区分同名的人。"""
    tag = f"【本条发言者：{nick}】"
    if sender_id:
        tag += f"（内部ID {sender_id}）"
    return tag


def display_name(wxid: str, chat: str = None) -> str:
    """这条消息是谁发的。同一个群里每个 wxid 都要有唯一名字。

    以前所有人共用「群友酱」，模型只能靠猜，常把上一条的发送者当成这一条的。
    现在按群昵称表取名字；实在查不到就带上 wxid 尾号，至少能区分开。
    """
    if not wxid:
        return "群友"
    mapping = CFG.get("nickname_map") or {}
    if wxid in mapping:
        return mapping[wxid]


    try:
        alias = memory.learned_name(
            _hist_key(chat), wxid,
            max_age_days=CFG.get("memory_max_age_days", 45))
    except Exception:
        alias = ""
    if alias:
        return alias
    nick = _roster_name(wxid, chat)
    if nick and nick != wxid:
        return nick
    tail = re.sub(r"[^0-9A-Za-z]", "", str(wxid))[-4:] or "?"
    return f"群友{tail}"


_KAOMOJI_RE = re.compile(r"[（(][^（）()]{1,24}[）)]")


def _visible_len(s: str) -> int:
    """按“会占多宽”算字数：括号里的纯符号颜文字不算，汉字/字母数字照算。

    颜文字一多，按原始长度判断就会把一条本来一行的回复切成两条，
    和「多用颜文字」的目的人正好相反。
    """
    if not s:
        return 0

    def _drop(m):
        inner = m.group(0)[1:-1]
        if re.search(r"[\u4e00-\u9fffA-Za-z0-9]", inner):
            return m.group(0)
        return ""

    return len(_KAOMOJI_RE.sub(_drop, s))


def _split_long(s: str, hi: int) -> list:
    """一句太长又没有句末标点：按逗号就近切，尽量每段不超过 hi。"""
    parts = [p for p in _COMMA_BREAK.split(s) if p]
    if len(parts) <= 1:
        return [s]
    out, cur = [], ""
    for p in parts:
        if cur and _visible_len(cur) + _visible_len(p) > hi:
            out.append(cur)
            cur = p
        else:
            cur += p
    if cur:
        out.append(cur)
    return out


def _is_symbol_chunk(s: str) -> bool:
    """是否纯颜文字/符号（无汉字、无 ASCII 字母数字）→ 应并到前一条文本里。"""
    if not s:
        return False
    return not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", s)


def split_messages(text: str) -> list:
    """把较长回复切成一条条短消息（真人闲聊都是一小句一小句发的）。

    只在句子边界插换行；除了切点处那个收尾逗号（「早上好呀，」+「你们」
    里的逗号），不删、不改任何内容。整行没有断句标点的（典型是 Agent 式
    的换行列表）原样保留，否则会切成 “- 删除”“A” 这种半句残骸。超长整条
    再由 split_text 兜底。
    """
    limit = int(CFG.get("max_reply_chars", 400) or 0)
    lo = int(CFG.get("min_msg_chars", 4) or 4)
    hi = int(CFG.get("max_msg_chars", 26) or 26)
    max_msgs = int(CFG.get("max_msgs", 8) or 8)
    trig = int(CFG.get("split_threshold", 0) or 0) or hi * 2
    if limit > 0:
        trig = min(trig, limit)
    if _visible_len(text) <= trig:
        return [text]

    pieces = []
    for line in text.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        seg = [n for n in _SENT_SPLIT.split(line) if n and n.strip()]
        if not seg:
            continue
        if len(seg) == 1:
            groups = _split_long(seg[0], hi) if _visible_len(seg[0]) > hi else seg
        else:
            groups, cur = [], []
            for i, piece in enumerate(seg):
                cur.append(piece)
                joined = "".join(cur)
                nxt = seg[i + 1] if i + 1 < len(seg) else None
                if nxt is None:
                    groups.append(joined)
                    break
                if _visible_len(joined) >= lo and _visible_len(joined) + _visible_len(nxt) > hi:
                    groups.append(joined)
                    cur = []
        for g in groups:
            g = g.strip()
            if not g:
                continue

            pieces.extend(_split_long(g, hi) if _visible_len(g) > hi else [g])



    merged = []
    for p in pieces:
        p = p.strip()
        if not p:
            continue
        if merged and _is_symbol_chunk(p):
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    pieces = merged

    while len(pieces) > max_msgs and len(pieces) > 1:
        pieces[-2] = pieces[-2] + " " + pieces[-1]
        pieces.pop()

    out = []
    for p in pieces:
        out.extend(split_text(p, limit) if limit > 0 and len(p) > limit else [p])



    if len(out) > 1:
        tidied = []
        for i, p in enumerate(out):
            p = p.strip()
            if i < len(out) - 1:
                p = p.rstrip(_SPLIT_TAIL_PUNCT).strip()
            if i > 0:
                p = p.lstrip(_SPLIT_TAIL_PUNCT).strip()
            if p:
                tidied.append(p)
        if tidied:
            out = tidied
    return out


WECHAT_EXE_NAMES = ("weixin.exe", "wechat.exe")


def _user32():
    return ctypes.windll.user32


def _window_pid(hwnd: int) -> int:
    if not hwnd:
        return 0
    pid = wintypes.DWORD(0)
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _process_exe(pid: int) -> str:
    """pid 对应的可执行文件名（小写）；取不到返回空串。"""
    if not pid:
        return ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
    except Exception:
        return ""
    finally:
        k32.CloseHandle(h)
    return ""


def _window_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    _user32().GetWindowTextW(hwnd, buf, 256)
    return buf.value.strip()


def _window_class(hwnd: int) -> str:
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    _user32().GetClassNameW(hwnd, buf, 256)
    return buf.value.strip()


def _is_wechat_window(hwnd: int) -> bool:
    return _process_exe(_window_pid(hwnd)) in WECHAT_EXE_NAMES


_WECHAT_MAIN_CLASSES = ("Qt51514QWindowIcon", "Qt5QWindowIcon", "WeChatMainWndForPC")

_WECHAT_HELPER_CLASSES = (
    "Qt51514WxTrayIconMessageWindowClass", "IME", "MSCTFIME UI",
    "Base_PowerMessageWindow", "Chrome_SystemMessageWindow",
    "Chrome_WidgetWin_0", "Chrome_WidgetWin_1", "DisplayICC_SystemMessageWindow",
)
_WECHAT_MAIN_TITLES = ("微信", "WeChat", "Weixin")


def _is_wechat_main_window(hwnd: int) -> bool:
    """是否「微信主窗口」（聊天主界面），排除托盘窗/输入法窗等辅助窗。"""
    if not _is_wechat_window(hwnd):
        return False
    cls = _window_class(hwnd)
    if cls in _WECHAT_MAIN_CLASSES:
        return True
    if cls in _WECHAT_HELPER_CLASSES:
        return False
    return _window_title(hwnd) in _WECHAT_MAIN_TITLES


def _foreground_hwnd() -> int:
    try:
        return int(_user32().GetForegroundWindow() or 0)
    except Exception:
        return 0


def _is_wechat_foreground() -> bool:
    return _is_wechat_window(_foreground_hwnd())


def _wechat_hwnds() -> list:
    """微信进程的所有顶层窗口句柄（含最小化的）。"""
    u = _user32()
    out = []

    def _cb(h, _lp):
        try:
            if _is_wechat_window(h) and _window_title(h):
                out.append(int(h))
        except Exception:
            pass
        return True

    try:
        CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        u.EnumWindows(CB(_cb), 0)
    except Exception:
        return []
    return out


def _wechat_main_hwnds() -> list:
    """只返回微信**主窗口**句柄（排除托盘窗/输入法窗/隐藏窗）。

    最小化判断与「还原微信窗口」都必须只看主窗：托盘窗天生不可见，
    拿它判断状态会得出错误结论，进而误把主窗最小化。
    """
    return [h for h in _wechat_hwnds() if _is_wechat_main_window(h)]


def _wechat_is_minimized() -> bool:
    u = _user32()
    for h in _wechat_main_hwnds():
        try:
            if u.IsIconic(h):
                return True
        except Exception:
            continue
    return False


def _activate_window(hwnd: int, tries: int = 3) -> bool:
    """把指定窗口切回前台。

    微信刚抢过前台，系统会拦后台进程直接抢回来，所以先 AttachThreadInput
    把当前线程和前台/目标窗口线程的输入队列接在一起，再 SetForegroundWindow。
    """
    u = _user32()
    if not hwnd or not u.IsWindow(hwnd):
        return False
    k32 = ctypes.windll.kernel32
    for _ in range(max(1, tries)):
        if u.GetForegroundWindow() == hwnd:
            return True
        try:
            if u.IsIconic(hwnd):
                u.ShowWindow(hwnd, 9)
        except Exception:
            pass
        cur = k32.GetCurrentThreadId()
        fg = u.GetForegroundWindow()
        tid_fg = u.GetWindowThreadProcessId(fg, None) if fg else 0
        tid_t = u.GetWindowThreadProcessId(hwnd, None)
        attached = []
        for tid in {tid_fg, tid_t}:
            if tid and tid != cur:
                try:
                    if u.AttachThreadInput(cur, tid, True):
                        attached.append(tid)
                except Exception:
                    pass
        try:
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
            u.SetActiveWindow(hwnd)
        except Exception:
            pass
        finally:
            for tid in attached:
                try:
                    u.AttachThreadInput(cur, tid, False)
                except Exception:
                    pass
        time.sleep(0.15)
        if u.GetForegroundWindow() == hwnd:
            return True
    return False


def _capture_foreground_before_send(label: str = "发送") -> tuple:
    """发送/预取前记录用户当前的前台窗口，返回 (要切回的 hwnd, 微信是否最小化)。

    微信已经在前台时不需要做任何事，返回 (0, False) —— 直接做就好。
    """
    if not CFG.get("restore_foreground", True):
        return 0, False
    try:
        if _is_wechat_foreground():
            log(f"微信已在前台，直接{label}")
            return 0, False
        prev = _foreground_hwnd()
        minimized = _wechat_is_minimized()
        log(f"微信不在前台，{label}后切回：",
            _window_title(prev) or (f"hwnd={prev}" if prev else "未知窗口"))
        return prev, minimized
    except Exception as e:
        log("检测前台窗口失败（本轮不做前台恢复）：", repr(e))
        return 0, False


def _restore_foreground(prev: int, wechat_was_minimized: bool) -> None:
    """把发送前的前台窗口切回来，并尽量还原微信原来的窗口状态。"""
    if not prev:
        return
    u = _user32()
    try:

        if not _is_wechat_foreground():
            return


        for h in _wechat_main_hwnds():
            try:
                u.SetWindowPos(h, ctypes.c_void_p(-2), 0, 0, 0, 0,
                               0x2 | 0x1)
            except Exception:
                pass
        if wechat_was_minimized and CFG.get("restore_wechat_minimized", True):
            for h in _wechat_main_hwnds():
                try:
                    if not u.IsIconic(h):
                        u.ShowWindow(h, 6)
                except Exception:
                    pass
        ok = _activate_window(prev)
        title = _window_title(prev) or f"hwnd={prev}"
        log(("已切回原窗口：" if ok else "切回原窗口失败：") + title)
    except Exception as e:
        log("切回原窗口异常：", repr(e))


def split_text(text: str, limit: int) -> list:
    limit = int(limit or 0)
    if limit <= 0 or len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for ch in text:
        buf += ch
        if len(buf) >= limit and ch in "。！？!?；;.\n":
            parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())
    return [p for p in parts if p]


def _remember_self_sticker(row: dict, chat: str = None) -> None:
    """记住一张刚由本机发出的贴纸，供 is_self_message 识别。"""
    if not row:
        return
    key = _hist_key(chat)
    item = {
        "local_id": row.get("local_id"),
        "sort_seq": row.get("sort_seq"),
        "md5": sticker_read.emoji_md5(str(row.get("content") or "")),
        "ts": time.time(),
    }
    with _self_sticker_lock:
        buf = _self_stickers.setdefault(key, [])
        buf.append(item)

        if len(buf) > 10:
            del buf[:-10]
    log(f"已记住本次发出的贴纸：lid={item['local_id']} "
        f"seq={item['sort_seq']} md5={(item['md5'] or '?')[:12]}")


def _is_self_sent_sticker(msg: dict, chat: str = None) -> bool:
    """这张表情包是不是我刚发出去的。

    微信有时把 UI 发出的贴纸记到别人名下，sender_username 会指错人；
    这里用发送成功后回读到的 local_id / sort_seq / md5 兜底识别。
    """
    if msg.get("type") != "动画表情":
        return False
    key = _hist_key(chat or msg.get("username") or "")
    now = time.time()
    lid = msg.get("local_id")
    seq = msg.get("sort_seq")
    md5 = sticker_read.emoji_md5(str(msg.get("content") or ""))
    with _self_sticker_lock:
        items = [x for x in (_self_stickers.get(key) or [])
                 if now - float(x.get("ts") or 0) <= 600]
        _self_stickers[key] = items
        for x in reversed(items):
            if lid is not None and x.get("local_id") == lid:
                return True
            if seq is not None and x.get("sort_seq") == seq:
                return True
            if (md5 and x.get("md5") == md5
                    and now - float(x.get("ts") or 0) <= 60):
                return True
        start = _sticker_send_start.get(key)

    if start and now - float(start) <= 30:
        ct = msg.get("create_time")
        if not ct or float(ct) >= float(start) - 3:
            return True
    return False


def _bind_latest_sent_sticker(label: str, chat: str = None,
                              since: float = None) -> None:
    """记住刚发出的贴纸并顺手补 md5↔标签表。

    发完一张贴纸后回读最新一条「动画表情」。不能只看 sender_username：
    微信有时把 UI 发出的贴纸记到别人名下，这正是她回复自己贴纸的根因。
    所以优先用 since（本次发送开始时间）之后新出现的那条。
    """
    since = float(since or 0)
    cands = []
    for attempt in range(4):
        try:
            rows = db.get_messages(chat or group_username, limit=30)
        except Exception as e:
            log("读取群消息（绑定贴纸 md5）失败：", repr(e))
            return
        cands = [m for m in rows if m.get("type") == "动画表情"]
        if since:
            cands = [m for m in cands
                     if float(m.get("create_time") or 0) >= since - 5]
        if cands or not since:
            break
        time.sleep(0.4)
    if not cands:
        return
    cands.sort(key=lambda m: m.get("sort_seq") or 0)


    mine = [m for m in cands
            if (m.get("sender_username") or "") == self_wxid]
    _remember_self_sticker((mine or cands)[-1], chat)

    for m in reversed(mine or cands):
        md5 = sticker_read.emoji_md5(m.get("content"))
        if md5:
            sticker_read.bind_md5_label(md5, label)
            log(f"自我学习：已绑定 {label} <- md5={md5[:12]}...")
            return


def _sticker_ready(chat: str = None) -> bool:
    """节流没到就别挑标签，免得日志里刷「贴纸节流」。"""
    if not CFG.get("sticker_enabled", True):
        return False
    cooldown = float(CFG.get("sticker_cooldown_seconds", 240) or 0)
    if cooldown <= 0:
        return True
    key = _hist_key(chat)
    with _sticker_lock:
        last = _last_sticker_send.get(key, 0.0)
    return (time.time() - last) >= cooldown


_STICKER_MOODS = (
    ("认错", ("对不起", "抱歉", "我错了", "不好意思")),
    ("小猫哈气", ("生气", "气死", "讨厌", "不理你")),
    ("睡觉", ("睡觉", "晚安", "困了", "好困", "睡啦")),
    ("疲惫", ("好累", "太累", "累死", "疲惫")),
    ("摸鱼", ("摸鱼", "偷懒", "划水")),
    ("加油", ("加油", "你可以的", "冲鸭")),
    ("惊讶", ("居然", "竟然", "天呐", "哇塞")),
    ("难道说？", ("难道",)),
    ("吃饱了", ("吃饱", "干饭", "吃饭")),
    ("下班", ("下班", "收工")),
    ("头疼", ("头疼", "头大")),
    ("发呆", ("发呆", "走神", "愣住")),
    ("庆祝", ("庆祝", "恭喜", "赢了", "太好了")),
    ("喜欢", ("喜欢", "爱你", "抱抱")),
    ("压力", ("压力", "好卷")),
    ("寄了", ("寄了", "完了", "完蛋")),
    ("嘲讽", ("呵呵", "就这", "笑死")),
    ("生闷气", ("哼", "生气气")),
)


def _auto_sticker(text: str, chat: str = None):
    """模型没给标记时，按回复里的强情绪词补一个（节流 + 概率，避免刷屏）。"""
    text = text or ""
    if not text or not _sticker_ready(chat):
        return None
    for label, words in _STICKER_MOODS:
        for kw in words:
            if kw in text:
                chance = float(CFG.get("auto_sticker_chance", 0.5) or 0)
                if random.random() > chance:
                    return None
                log(f"自动附加表情：{label}（命中「{kw}」）")
                return label
    return None


def _try_send_sticker(label: str, chat: str = None) -> bool:
    """带节流地发一个表情贴纸；失败只记录、不影响整条回复的成败。"""
    if not label:
        return False
    if label in _BLOCKED_LABELS:
        log("该贴纸已被用户禁发，跳过：", label)
        return False
    if not CFG.get("sticker_enabled", True):
        log("贴纸已禁用，跳过：", label)
        return False
    cooldown = float(CFG.get("sticker_cooldown_seconds", 240) or 0)
    key = _hist_key(chat)
    now = time.time()
    with _sticker_lock:
        last = _last_sticker_send.get(key, 0.0)
        if cooldown > 0 and (now - last) < cooldown:
            log(f"{_chat_tag(chat)}贴纸节流：距上次发送不足 {cooldown:.0f}s，"
                f"本条跳过（{label}）")
            return False

        if label == _last_sticker_label.get(key) and (now - last) < 1800:
            log(f"这张刚发过，这次先不重复：{label}")
            return False

        _last_sticker_send[key] = now
    send_start = time.time()
    with _sticker_lock:
        _sticker_send_start[key] = send_start
    try:


        ok = bool(sticker_sender.send_sticker(
            label, who=_chat_who(chat), who_id=(chat or group_username)))
    except Exception as e:
        log("sticker_sender.send_sticker 异常：", repr(e))
        ok = False
    finally:
        try:
            sticker_sender.close_panel()
        except Exception as e:
            log("关闭表情面板异常：", repr(e))
    if ok:
        with _sticker_lock:
            _last_sticker_send[key] = time.time()
            _last_sticker_label[key] = label
        try:
            _bind_latest_sent_sticker(label, chat, since=send_start)
        except Exception as e:
            log("绑定本次发出的贴纸 md5 异常：", repr(e))
        finally:
            with _sticker_lock:
                _sticker_send_start.pop(key, None)
    else:


        with _sticker_lock:
            _sticker_send_start.pop(key, None)
            _last_sticker_send[key] = time.time() - max(0.0, cooldown - 60.0)
        detail = getattr(sticker_sender, "LAST_ERROR", "") or ""
        if detail:
            log("贴纸没发出去：", detail)
    return ok


def _latest_non_self_seq(chat: str = None) -> int:
    """群里最近一条「不是本机发的」消息 sort_seq；取不到返回 0。

    用于判断要回复的那条消息是否已被更新的消息盖过（决定要不要引用它）。
    自己的回复不算“更新”，避免 bot 回复后把自己那条误判为需要引用的更新。
    """
    try:
        rows = db.get_messages(chat or group_username, limit=30)
    except Exception:
        return 0
    seqs = [m.get("sort_seq") for m in rows
            if (m.get("sender_username") or "") != self_wxid and m.get("sort_seq")]
    return int(max(seqs)) if seqs else 0


def _batch_user_message_count(chat: str = None,
                              anchor_ct: float = None) -> int:
    """这一轮里用户一共发了几条消息（决定要不要引用）。

    规则（2026-11-06）：只有一个人发了一条消息时不引用；其余情况
    （一人多条 / 两人各一条 / 多人多条）全部先引用再回复，最后一条也引用。
    以被回复消息的 create_time 为中心看前后 quote_batch_window_seconds 秒；
    不能用「当前时间」为中心——处理慢了几秒就会把当前这条自己排除掉。
    读库失败按「只有一条」处理，宁可少引用也不要引错。
    """
    window = float(CFG.get("quote_batch_window_seconds",
                           CFG.get("max_reply_age_seconds", 90)) or 90)
    anchor = float(anchor_ct or time.time())
    try:
        rows = db.get_messages(chat or group_username, limit=50)
    except Exception:
        return 1
    n = 0
    for m in rows:
        if m.get("type") == "系统消息":
            continue
        ct = m.get("create_time")
        if ct and abs(float(ct) - anchor) > window:
            continue
        if is_self_message(m, chat or group_username):
            continue
        n += 1
    return n


def _clean_quote_text(msg: dict) -> str:
    """取一条消息里真正能拿去 UI 匹配的原文（只处理文本）。"""
    if str(msg.get("type") or "") != "文本":
        return ""
    text = str(msg.get("content") or "").strip()
    text = re.sub(r"^(wxid_[A-Za-z0-9]+):\s*", "", text, count=1).strip()
    if not text or text in SKIP_MARKERS:
        return ""
    if any(text.startswith(m) for m in SKIP_MARKERS):
        return ""
    return text


def _round_quote_target(last_msg: dict, last_text: str, chat: str = None,
                        round_msgs: list = None):
    """这一轮该引用哪条：只引「确实在回它」的那条，拿不准就不引用。

    以前一律引用最后一条：A 提问、B 又补一句时，回复明明在答 A，引用却挂
    在 B 上，看着就是认错人。现在按「同一人 → 点名 → 问句」的优先级挑，
    还是分不出来就干脆不引用。
    """
    def mk(m, text, kind):
        return {"seq": m.get("sort_seq"), "content": text,
                "create_time": m.get("create_time"), "kind": kind}

    last_kind = str(last_msg.get("type") or "")
    if not round_msgs:
        return mk(last_msg, last_text, last_kind)

    items = []
    for m in list(round_msgs) + [last_msg]:
        kind = str(m.get("type") or "")
        if kind == "文本":
            said = _clean_quote_text(m)
        elif kind == "语音" and m is last_msg:
            said = str(last_text or "").strip()
        else:
            said = ""
        if said:
            items.append((m, said, kind))
    if not items:
        return None

    senders = {sender_wxid(m) for m, _t, _k in items}
    senders.discard("")
    if len(senders) <= 1:

        return mk(*items[-1])

    called = [(m, t, k) for m, t, k in items if _mentions_bot(t, chat)]
    if len(called) == 1:
        return mk(*called[0])

    asked = [(m, t, k) for m, t, k in items if _REPLY_ASK.search(t)]
    if len(asked) == 1:
        return mk(*asked[0])

    log(f"{_chat_tag(chat)}这一轮有多人发言、看不出在回谁，不引用")
    return None


def _quote_text_unique(chat: str = None, text: str = "") -> bool:
    """最近消息里这段文字是否唯一。

    quote_by_uia 按「包含」匹配消息行、取最下面那条；A 说「哈哈」、B 说
    「哈哈哈哈哈」时，回复 A 会引到 B 头上。最近 40 条里只要还有别的文字
    包含它，就不引用，退回普通发送。
    """
    if db is None:
        return True
    target = re.sub(r"\s+", "", str(text or ""))
    if len(target) < 2:
        return False
    try:
        rows = db.get_messages(chat or group_username, limit=40)
    except Exception:
        return True
    hits = 0
    for m in rows:
        if m.get("type") != "文本":
            continue
        body = str(m.get("content") or "")
        body = re.sub(r"^(wxid_[A-Za-z0-9]+):\s*", "", body, count=1)
        body = re.sub(r"\s+", "", body)
        if target in body:
            hits += 1
            if hits > 1:
                return False
    return hits == 1


_quote_fail_state = {"n": 0, "until": 0.0}


def _quote_cooldown_active() -> bool:
    """引用是否处于冷却期（连续失败后暂停引用，避免反复折腾光标）。

    引用依赖「右键 → 菜单 → 点击」，一旦环境不对（窗口被切走、布局变化、
    菜单控件没物化）就会连续失败。失败本身无害（会自动退回普通发送），
    但每次都要移动光标、右键，用户会看到鼠标乱跳，所以连续失败 3 次就
    暂停引用 10 分钟，只发普通回复。
    """
    return time.time() < _quote_fail_state.get("until", 0.0)


def _quote_note(ok: bool, detail: str = "") -> None:
    """记录一次引用结果，连续失败 3 次进入 10 分钟冷却。"""
    if ok:
        _quote_fail_state["n"] = 0
        _quote_fail_state["until"] = 0.0
        return
    _quote_fail_state["n"] = int(_quote_fail_state.get("n", 0)) + 1
    if _quote_fail_state["n"] >= 3:
        _quote_fail_state["n"] = 0
        _quote_fail_state["until"] = time.time() + 600
        log("引用连续失败 3 次，暂停引用 10 分钟（期间只发普通回复）", detail)


def _quote_maybe_sent(resp) -> bool:
    """引用返回失败时，判断「回车是否已经按过」。

    按过回车就没法确定那条消息到底发出去没有：既可能已发出、只是没被确认，
    也可能真的没发出。这种情况下宁可少发一条，也不能补发——补发就是用户看到
    的「一条带引用 + 一条一模一样不带引用」。所以返回 True 时上层不补发。
    """
    try:
        return bool((resp.get("data") or {}).get("maybe_sent"))
    except Exception:
        return False


def _quote_menu_missed(resp) -> bool:
    """右键没命中气泡（菜单没弹出来）：这种失败确定没输入过任何文字，可以安全重试。"""
    try:
        return "未弹出" in str(resp.get("message", ""))
    except Exception:
        return False


def _quote_with_retry(chunk: str, target: str, chat: str = None,
                      wait: float = 1.2):
    """第一次右键没弹出菜单时，等列表重排稳定后整体重试一次。

    失败集中在「消息刚到达的 2~5 秒」，此时行坐标还在重排动画里。重试前已
    确认没输入过任何文字，所以不会出现重复消息；仍失败就交给上层退回普通发送。
    """
    resp = quick_quote_uia(chunk, who=_chat_who(chat),
                           target_text=target, verify=True)
    if resp:
        return resp
    if _quote_maybe_sent(resp) or not _quote_menu_missed(resp):
        return resp
    log("引用菜单没弹出（消息列表可能还在重排），稍等后重试一次")
    time.sleep(wait)
    return quick_quote_uia(chunk, who=_chat_who(chat),
                           target_text=target, verify=True)


_AT_MARK_RE = re.compile(r"\[(?:艾特|at|@)\s*[:：]\s*([^\[\]\n]{1,24})\]",
                         re.I)

_AT_LEAD_RE = re.compile(
    r"^\s*@\s*([^\s@，。！？!?、,.;；:：~～…]{1,24})\s*")
_AT_TAIL_PUNCT = re.compile(r"[\s，。！？!?、,.;；:：~～…]+$")


def _at_clean_name(name: str) -> str:
    """把模型写的名字收拾干净：去 @、去首尾括号和标点。"""
    nm = (name or "").strip().lstrip("@").strip()
    nm = nm.strip("\u3010\u3011[]（）()<>《》\"'\u201c\u201d\u2018\u2019")
    nm = _AT_TAIL_PUNCT.sub("", nm)
    return nm.strip()


def _at_candidates(chat: str = None) -> dict:
    """本群可 @ 的人：名字（含去掉结尾“酱”的短名）-> wxid 集合。

    只用 GROUP_MEMBERS / nickname_map 里的人。聊天里从没出现过、群里也
    没这个人的名字，解析不到就不 @，宁可不 @ 也不能 @ 错人。
    """
    key = _hist_key(chat)
    table = GROUP_MEMBERS.get(key) or {}
    mapping = CFG.get("nickname_map") or {}
    allowed = set(table) or set(mapping)
    out = {}

    def add(name, wxid):
        nm = _at_clean_name(name)
        if not nm or not wxid or wxid == self_wxid:
            return
        aliases = [nm, prompt_nick(nm)]
        if nm.endswith("酱") and len(nm) > 2:
            aliases.append(nm[:-1])
        for alias in aliases:
            alias = _at_clean_name(alias)
            if alias:
                out.setdefault(alias, set()).add(wxid)

    for wxid, real in table.items():
        if wxid in allowed:
            add(real, wxid)
    for wxid, alias in mapping.items():
        if wxid in allowed:
            add(alias, wxid)
    return out


def _resolve_at_target(name: str, chat: str = None):
    """名字 -> {wxid, name(文字 @ 用), real(真 @ 用)}；不唯一就返回 None。"""
    nm = _at_clean_name(name)
    if not nm or nm in ("我", "自己", self_wxid):
        return None
    cand = _at_candidates(chat)
    hits = cand.get(nm)
    if not hits:
        low = nm.lower()
        hits = set()
        for k, v in cand.items():
            if k.lower() == low:
                hits |= v
    if not hits:
        log(f"想 @「{nm}」，但群里没这个人（或名字对不上），跳过 @")
        return None
    if len(hits) > 1:
        log(f"想 @「{nm}」，群里有 {len(hits)} 个人都可能，无法确定，跳过 @")
        return None
    wxid = next(iter(hits))
    shown = prompt_nick(display_name(wxid, chat)) or nm
    real = _roster_name(wxid, chat) or display_name(wxid, chat) or shown
    return {"wxid": wxid, "name": shown, "real": real}


def _parse_at(text: str, chat: str = None):
    """拆出 [艾特:名字]/句首 @名字；返回 (正文, 目标或 None)。

    标记无论能不能解析都必须从正文里去掉，绝不能把 [艾特:xxx] 发到群里。
    """
    body = text or ""
    target = None
    marks = list(_AT_MARK_RE.finditer(body))
    if marks:
        for m in marks:
            if target is None:
                target = _resolve_at_target(m.group(1), chat)
        body = _AT_MARK_RE.sub("", body)
    if target is None:
        m = _AT_LEAD_RE.match(body)
        if m:
            cand = _resolve_at_target(m.group(1), chat)
            if cand:
                target = cand
                body = body[m.end():]
    body = re.sub(r"^[\s，。！？!?、,.;；:：~～…]+", "", body)
    return body.strip(), target


def _patch_gui_db_fallback() -> None:
    """让发送回读校验在 psutil 枚举句柄失败时仍能读库。

    微信进程持有 受保护的系统目录 这类不可访问的句柄时，
    psutil.open_files() 会整体抛 AccessDenied，guia._get_db() 只能报
    「无法唯一确认账号」。结果每条已经发出去的消息都被判成「数据库未确认」，
    后半段还会被直接丢掉。这里优先复用 bot 自己已连接的 db，其次只在
    「只有一个账号目录」时按目录兜底，并缓存结果避免反复解密。
    """
    try:
        from wechatauto.guia import WeChatGUI
    except Exception as e:
        log("发送确认补丁未生效：", repr(e))
        return
    if getattr(WeChatGUI, "_chatbot_db_fallback", False):
        return
    orig = WeChatGUI._get_db

    def _safe_get_db(self):
        try:
            return orig(self)
        except RuntimeError as e:
            if "无法唯一确认" not in str(e):
                raise
            cached = getattr(self, "_chatbot_db", None)
            if cached is None:
                cached = getattr(self, "_cached_db", None)
            if cached is not None:
                return cached
            try:
                from wechatauto.db import WeChatDB, auto_detect_db_dir
                bdb = globals().get("db")
                if bdb is not None:
                    try:
                        got = (bdb.get_self_info() or {}).get("username") or ""
                    except Exception:
                        got = ""
                    want = globals().get("self_wxid") or ""
                    if not want or not got or got == want:
                        if not getattr(self, "_chatbot_db_logged", False):
                            self._chatbot_db_logged = True
                            log("发送确认：句柄枚举被拒，复用 bot 已连接的数据库（"
                                + str(getattr(bdb, "account", "")) + "）")
                        self._chatbot_db = bdb
                        return bdb
                root = Path(auto_detect_db_dir()).resolve()
                dirs = [p.name for p in root.iterdir()
                        if p.is_dir() and (p / "db_storage").is_dir()]
            except Exception:
                raise e
            if len(dirs) != 1:
                raise e
            if not getattr(self, "_chatbot_db_logged", False):
                self._chatbot_db_logged = True
                log("发送确认：句柄枚举被拒，按唯一账号目录回读（" +
                    dirs[0] + "）")
            db2 = WeChatDB(db_dir=str(root), account=dirs[0])
            self._chatbot_db = db2
            return db2

    WeChatGUI._get_db = _safe_get_db
    WeChatGUI._chatbot_db_fallback = True


def _at_send(target: dict, text: str, chat: str = None):
    """真 @：走微信成员选择弹层。默认关闭，失败/不确定都不补发。"""
    from wechatauto.guia import WeChatGUI
    member = (target or {}).get("real") or (target or {}).get("name") or ""
    if not member:
        return None
    wx = WeChatGUI()
    resp = wx.at_member(member, text, who=_chat_who(chat), verify=True)
    detail = str(resp.get("message", ""))[:80] if resp is not None else ""
    log(("真 @ 发送成功：" if resp else "真 @ 发送失败：") + detail,
        "|", member)
    return resp


def _at_definitely_failed(resp) -> bool:
    """真 @ 返回失败时，判断是不是「确定没发出去」。

    点过发送以后（数据库没确认）一律当作可能已发出，不能补发。
    """
    if resp is None:
        return False
    try:
        if resp.get("status") == "成功":
            return False
        msg = str(resp.get("message") or "")
    except Exception:
        return False
    for hint in ("微信窗口不可见", "输入框不可用", "未在成员列表中",
                 "未找到已登录", "输入消息正文失败"):
        if hint in msg:
            return True
    return False


def _quick_send_retry(text: str, who: str, tries: int = 3,
                      delay: float = 1.5):
    """quick_send 的薄封装：微信窗口暂时找不到时重试；其它异常照旧抛出。"""
    last = None
    for i in range(max(1, int(tries))):
        try:
            return quick_send(text, who=who, verify=True)
        except RuntimeError as e:
            if SEND_WINDOW_ERROR not in str(e):
                raise
            last = e
            _ensure_wechat_visible()
            if i < tries - 1:
                log(f"微信主窗口暂时找不到，{delay:g}s 后重试发送"
                    f"（{i + 1}/{tries}）")
                time.sleep(delay)
    raise last


def _pending_save() -> None:
    with _pending_lock:
        data = list(_pending_sends)
    try:
        PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(PENDING_PATH) + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, PENDING_PATH)
    except Exception as e:
        log("待发队列写入失败：", repr(e))


def _pending_load() -> None:
    """重启后把没补发的消息捡回来；超过 30 分钟的丢掉。"""
    try:
        data = json.load(open(PENDING_PATH, encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, list):
        return
    now = time.time()
    with _pending_lock:
        _pending_sends.clear()
        for item in data:
            if not isinstance(item, dict):
                continue
            chunks = [str(c) for c in (item.get("chunks") or []) if c]
            chat = str(item.get("chat") or "")
            if not chunks or not chat:
                continue
            ts = float(item.get("ts") or now)
            if now - ts > 1800:
                continue
            _pending_sends.append({"chat": chat, "chunks": chunks,
                                   "label": item.get("label"), "ts": ts})
    if _pending_sends:
        log(f"待发队列：恢复了 {len(_pending_sends)} 条未补发的消息")


def _queue_pending(chat: str, chunks: list, label: str = None) -> None:
    """发送失败的分段先存着，微信窗口恢复后按顺序补发。"""
    chunks = [c for c in chunks if c]
    if not chunks:
        return
    key = (chat, tuple(chunks), label or "")
    with _pending_lock:
        for item in _pending_sends:
            if (item["chat"], tuple(item["chunks"]),
                    item.get("label") or "") == key:
                return
        _pending_sends.append({"chat": chat, "chunks": list(chunks),
                               "label": label, "ts": time.time()})
    _pending_save()
    log(f"已放入待发队列（{len(chunks)} 段），等微信窗口恢复再发")


def _flush_pending() -> None:
    """待发队列：按顺序把一条消息的分段补完，发一段失败就停。"""
    with _pending_lock:
        items = list(_pending_sends)
    for item in items:
        if time.time() - item["ts"] > 1800:
            with _pending_lock:
                if item in _pending_sends:
                    _pending_sends.remove(item)
            log("待发队列超时，丢弃：",
                (item["chunks"][0] if item["chunks"] else "")[:30])
            continue
        while item["chunks"]:
            chunk = item["chunks"][0]
            if _is_recent_reply(chunk, item["chat"]):

                item["chunks"].pop(0)
                continue
            try:
                with _send_lock:
                    resp = quick_send(chunk, who=_chat_who(item["chat"]),
                                      verify=True)
            except Exception as e:
                log("待发队列重试失败：", repr(e))
                if SEND_WINDOW_ERROR in str(e):
                    _ensure_wechat_visible()
                break
            if not resp:
                log("待发队列重试未确认，保留：", chunk[:30])
                break
            _remember_reply(chunk, item["chat"])
            item["chunks"].pop(0)
            if item["chunks"]:
                time.sleep(random.uniform(0.6, 1.4))
        if not item["chunks"]:
            with _pending_lock:
                if item in _pending_sends:
                    _pending_sends.remove(item)
            log("待发队列：这一段补发完成")
            if item.get("label"):
                try:
                    _try_send_sticker(item["label"], item["chat"])
                except Exception as e:
                    log("待发队列补发表情失败：", repr(e))
    _pending_save()


def _pending_loop() -> None:
    while True:
        time.sleep(60)
        try:
            _flush_pending()
        except Exception as e:
            log("待发队列异常：", repr(e))


def send_reply(text: str, sticker_label: str = None, quote: dict = None,
               chat: str = None, allow_split: bool = True) -> bool:
    global _last_self_send

    chat = _hist_key(chat)
    text = (text or "").strip()
    at_target = None
    if text and CFG.get("at_enabled", True):
        text, at_target = _parse_at(text, chat)
    if not text and not sticker_label:
        return False

    if text and _is_recent_reply(text, chat):
        log(f"跳过重复回复（刚才已发过）：{text[:30]}")
        return False




    quote_target = None
    if quote and CFG.get("quote_stale_replies", True):
        qt = (quote.get("content") or "").strip()
        n_user = _batch_user_message_count(chat, quote.get("create_time"))


        if (n_user > 1 and qt and len(qt) >= 3
                and re.search(r"[\u4e00-\u9fffA-Za-z0-9]", qt)):
            kind = str(quote.get("kind") or "")
            if kind == "文本" and not _quote_text_unique(chat, qt):
                log(f"「{qt[:20]}」在最近消息里不唯一，"
                    "引用容易认错人，改成普通回复")
            else:
                quote_target = qt
                log(f"这一轮共有 {n_user} 条消息，先引用再回复：{qt[:30]}")
        elif n_user > 1 and qt:
            log(f"这条太短（{qt[:10]}），不引用，直接回复")
        else:
            log(f"这一轮只有 {n_user} 条用户消息，不引用，直接回复")



    at_real = bool(at_target and not quote_target
                   and CFG.get("at_real_enabled", False))
    at_prefix = ""
    if at_target and not at_real:
        at_prefix = "@" + at_target["name"] + " "
        log(f"用文字 @ {at_target['name']}" + ("（本条要引用）" if quote_target else ""))



    chunks = split_messages(text) if text else []
    if text and not allow_split:
        chunks = [text]
    ok_all = True
    with _send_lock:
        prev_fg, wx_minimized = 0, False
        captured = False
        try:
            for i, chunk in enumerate(chunks):
                lo = float(CFG.get("reply_delay_min", 0) or 0)
                hi = float(CFG.get("reply_delay_max", 0) or 0)
                if hi > 0:
                    time.sleep(random.uniform(lo, max(lo, hi)))

                if i == 0:
                    prev_fg, wx_minimized = _capture_foreground_before_send()
                    captured = True
                if i == 0 and at_prefix:
                    chunk = at_prefix + chunk
                quote_this = bool(i == 0 and quote_target
                                  and not _quote_cooldown_active())
                queue_failed = False
                if i == 0 and at_real:

                    resp = None
                    try:
                        resp = _at_send(at_target, chunk, chat)
                        ok = bool(resp)
                        if not ok and _at_definitely_failed(resp):

                            chunk = "@" + at_target["name"] + " " + chunk
                            log("真 @ 没发出去，改用文字 @ 补发")
                            resp = quick_send(chunk, who=_chat_who(chat),
                                              verify=True)
                            ok = bool(resp)
                        elif not ok:

                            log("真 @ 已操作发送但结果未确认，不再补发：",
                                str(resp.get("message", ""))[:80]
                                if resp is not None else "")
                            ok = True
                    except Exception as e:

                        log("真 @ 流程异常，结果不确定，不再补发：", repr(e))
                        ok = True
                elif quote_this:
                    try:


                        resp = _quote_with_retry(chunk, quote_target, chat)
                        ok = bool(resp)
                        if ok:
                            _quote_note(True)
                            log(f"引用回复已发送（引用「{quote_target[:20]}」）：{chunk[:30]}")
                        elif _quote_maybe_sent(resp):

                            _quote_note(False, str(resp.get("message", ""))[:60])
                            log("引用已提交但结果未确认，为避免重复不再补发：",
                                str(resp.get("message", ""))[:80])
                            ok = True
                        else:


                            _quote_note(False, str(resp.get("message", ""))[:60])
                            log("引用失败（未发送），退回普通发送：", str(resp.get("message", ""))[:80])
                            try:
                                resp = _quick_send_retry(
                                    chunk, who=_chat_who(chat))
                                ok = bool(resp)
                            except Exception as e:
                                queue_failed = SEND_WINDOW_ERROR in str(e)
                                log("引用退回普通发送也失败：", repr(e))
                                ok = False
                    except Exception as e:

                        _quote_note(False, repr(e)[:60])
                        log("引用流程异常，结果不确定，不再补发：", repr(e))
                        ok = True
                else:

                    try:
                        resp = _quick_send_retry(chunk, who=_chat_who(chat))
                        ok = bool(resp)
                    except Exception as e:
                        resp = {"message": repr(e)}
                        ok = False
                        queue_failed = SEND_WINDOW_ERROR in str(e)
                        log("发送异常（已自动重试）：", repr(e))
                    log(("发送成功：" if ok else "发送失败：") + str(resp.get("message", "")),
                        "|", chunk[:40])
                ok_all = ok_all and ok
                if not ok:


                    log("这一段没发成，后面的分段不再继续：", chunk[:24])
                    if queue_failed:
                        _queue_pending(chat, chunks[i:], sticker_label)
                    break
                if ok:
                    _last_self_send = time.time()
                    _last_self_send_by_chat[chat] = _last_self_send
                    _remember_reply(chunk, chat)
                if i < len(chunks) - 1:
                    time.sleep(random.uniform(0.6, 1.4))


            if sticker_label:
                if not captured:
                    prev_fg, wx_minimized = _capture_foreground_before_send()
                    captured = True
                try:
                    sent = _try_send_sticker(sticker_label, chat)
                    log("贴纸发送：" + ("成功" if sent else "未发送"), "|", sticker_label)
                except Exception as e:
                    log("贴纸发送异常：", repr(e))
        finally:

            _restore_foreground(prev_fg, wx_minimized)
    return ok_all


def download_image(msg: dict, chat: str = None):
    local_id = msg.get("local_id")
    shard = msg.get("shard")
    try:
        return md.download_image(chat or group_username, local_id, shard=shard)
    except TypeError:
        return md.download_image(chat or group_username, local_id)
    except Exception as e:
        log("图片下载失败：", repr(e))
        return None


VOICE_DIR = IMAGE_DIR / "voice"

_voice_fail = {}


def download_voice(msg: dict, chat: str = None):
    """把语音消息的 SILK 落到 D 盘临时目录，返回路径；取不到返回 None。"""
    if md is None:
        return None
    try:
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    local_id = msg.get("local_id")
    shard = msg.get("shard")
    try:
        return md.download_voice(chat or group_username, local_id,
                                 save_dir=str(VOICE_DIR), shard=shard)
    except TypeError:
        return md.download_voice(chat or group_username, local_id,
                                 save_dir=str(VOICE_DIR))
    except Exception as e:
        log("语音下载失败：", repr(e))
        return None


def _voice_fail_text(reason: str) -> str:
    """取不到语音时回什么：没落盘就请对方点一下，别装作没听清。"""
    if reason == "audio_not_downloaded":
        return "这条语音还没加载出来，你点一下播放我就能听啦"
    if reason == "no_voice_row":
        return "这条语音我这边找不到…你再说一遍嘛"
    return "这条语音我没听清…你再说一遍嘛"


_VOICE_GUI = None

_voice_prefetch_next = {}


def _voice_gui():
    """惰性创建用于「打开会话 / 预取语音」的 WeChatGUI（只导航，不发送）。"""
    global _VOICE_GUI
    if _VOICE_GUI is None:
        from wechatauto.guia import WeChatGUI
        _VOICE_GUI = WeChatGUI()
    return _VOICE_GUI


def _voice_reason(chat: str, msg: dict) -> str:
    """查这条语音为什么取不到，返回 audio_not_downloaded 之类的短原因。"""
    try:
        if md is not None:
            st = md.voice_status(chat or group_username, msg.get("local_id"))
            return str(st.get("reason") or "")
    except Exception as e:
        log("查语音状态失败：", repr(e))
    return ""


def _prefetch_voice(msg: dict, chat: str = None) -> bool:
    """语音没落盘时，把会话打开并等微信自己下载（只打开，不播放、不出声）。

    微信在「会话正处于打开状态」时收到语音，往往会把音频预下载到本地；
    已经收到但没落盘的语音，打开会话有时也能补下载。成功返回 True。
    """
    if not CFG.get("voice_prefetch", True) or md is None:
        return False
    try:
        seconds = float(CFG.get("voice_prefetch_seconds", 6) or 6)
    except (TypeError, ValueError):
        seconds = 6.0
    seconds = max(1.0, min(seconds, 20.0))
    try:
        cooldown = float(CFG.get("voice_prefetch_cooldown_seconds", 120) or 120)
    except (TypeError, ValueError):
        cooldown = 120.0
    cooldown = max(30.0, min(cooldown, 1800.0))
    key = chat or group_username or ""
    now = time.time()
    if now < _voice_prefetch_next.get(key, 0):
        return False
    prev, wx_minimized = _capture_foreground_before_send(label="预取语音")
    try:
        try:
            _voice_gui().open_chat(_chat_who(chat))
        except Exception as e:
            log(f"{_chat_tag(chat)}语音预取：打开会话失败：{e!r}")
            _voice_prefetch_next[key] = time.time() + cooldown
            return False
        deadline = time.time() + seconds
        while time.time() < deadline:
            time.sleep(0.8)
            st = {}
            try:
                st = md.voice_status(chat or group_username,
                                     msg.get("local_id")) or {}
            except Exception:
                st = {}
            if st.get("available"):
                log(f"{_chat_tag(chat)}语音预取成功："
                    f"{int(st.get('bytes') or 0)} 字节")
                _voice_prefetch_next.pop(key, None)
                return True
        log(f"{_chat_tag(chat)}语音预取超时：微信仍未把音频落盘；"
            f"{int(cooldown)} 秒内不再尝试")
        _voice_prefetch_next[key] = time.time() + cooldown
        return False
    finally:
        _restore_foreground(prev, wx_minimized)


def transcribe_voice(msg: dict, chat: str = None) -> str:
    """语音消息 -> 文字。听不出来/模型没装/微信没落盘都返回空串。"""
    if not CFG.get("asr_enabled", True):
        return ""
    if not asr.available():
        log("语音转文字未启用：D 盘没有 faster-whisper 模型")
        return ""
    path = download_voice(msg, chat)
    if not path:

        time.sleep(1.0)
        path = download_voice(msg, chat)
    if not path:
        reason = _voice_reason(chat, msg)

        if reason == "audio_not_downloaded" and _prefetch_voice(msg, chat):
            path = download_voice(msg, chat)
            if not path:
                reason = _voice_reason(chat, msg)
        if not path:
            if len(_voice_fail) > 200:
                _voice_fail.clear()
            _voice_fail[(chat or group_username, msg.get("local_id"))] = reason
            log(f"语音取不到音频（{reason or 'unknown'}；"
                "微信只对播放过的语音落盘，需要在界面上点开一次）")
            return ""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        log("读语音文件失败：", repr(e))
        return ""
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    t0 = time.time()
    text = asr.transcribe(data)
    if text:
        log(f"{_chat_tag(chat)}语音转文字（{time.time() - t0:.1f}s）：{text}")
    else:
        log(f"{_chat_tag(chat)}语音没听出内容（{time.time() - t0:.1f}s）")
    return text


VIDEO_DIR = IMAGE_DIR / "video"
_VIDEO_BYTES_RE = re.compile(r'length="(\d+)"')
_VIDEO_SECONDS_RE = re.compile(r'playlength="(\d+)"')


def _video_meta(content: str) -> dict:
    """解析视频消息 XML 里的时长/大小；解析不到给安全默认值。"""
    text = str(content or "")

    def num(pattern):
        m = pattern.search(text)
        if not m:
            return 0
        try:
            return int(m.group(1))
        except (TypeError, ValueError):
            return 0

    size = num(_VIDEO_BYTES_RE)
    return {
        "bytes": size,
        "mb": round(size / 1024.0 / 1024.0, 1) if size else 0.0,
        "seconds": num(_VIDEO_SECONDS_RE),
    }


def _video_time_text(seconds) -> str:
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        return "时长不明"
    if seconds < 60:
        return f"{seconds} 秒"
    return f"{seconds // 60} 分 {seconds % 60} 秒"


def _video_packed_id(msg: dict, chat: str = None) -> str:
    """视频本体和封面的文件名都是 packed_info 里那个 32 位 id。"""
    if db is None:
        return ""
    user = chat or group_username
    lid = msg.get("local_id")
    row = None
    try:
        row = db.get_message_row(user, lid, local_type=43,
                                 shard=msg.get("shard"))
    except TypeError:
        try:
            row = db.get_message_row(user, lid, local_type=43)
        except Exception:
            row = None
    except Exception:
        row = None
    packed = row.get("packed_info") if isinstance(row, dict) else None
    if not isinstance(packed, bytes):
        return ""
    m = re.search(rb"([0-9a-fA-F]{32})", packed)
    return m.group(1).decode().lower() if m else ""


def _video_thumb_path(msg: dict, chat: str = None):
    """微信只落视频封面（_thumb.jpg）时，把封面找出来。"""
    vid = _video_packed_id(msg, chat)
    base = str(getattr(db, "account_dir", "") or "") if db is not None else ""
    if not vid or not base:
        return None
    import glob

    root = os.path.join(base, "msg", "video")
    for pattern in (vid + "_thumb.jpg",
                    os.path.join("*", vid + "_thumb.jpg")):
        hits = glob.glob(os.path.join(root, pattern))
        if hits:
            return hits[0]
    return None


def download_video(msg: dict, chat: str = None):
    """微信已经下过这个视频（msg/video 里真有 mp4）时，复制一份到 D 盘。"""
    if md is None:
        return None
    try:
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    user = chat or group_username
    lid = msg.get("local_id")
    try:
        return md.download_video(user, lid, save_dir=str(VIDEO_DIR),
                                 shard=msg.get("shard"))
    except TypeError:
        try:
            return md.download_video(user, lid, save_dir=str(VIDEO_DIR))
        except Exception as e:
            log("视频下载失败：", repr(e))
            return None
    except Exception as e:
        log("视频下载失败：", repr(e))
        return None


def _video_too_big(meta: dict) -> bool:
    cap = float(CFG.get("video_max_mb", 50) or 0)
    return bool(cap > 0 and float(meta.get("mb") or 0) > cap)


def _video_latest_age(msg: dict) -> float:
    try:
        return max(0.0, time.time() - float(msg.get("create_time") or 0))
    except (TypeError, ValueError):
        return 1e9


def _video_is_latest(msg: dict, chat: str = None) -> bool:
    """右键只能点「最新一条」；不是视频本身就别冒险，免得存错。"""
    if db is None:
        return False
    try:
        rows = db.get_messages(chat or group_username, limit=1)
    except Exception:
        return False
    if not rows:
        return False
    newest = rows[0] or {}
    return (str(newest.get("type") or "") == "视频"
            and newest.get("local_id") == msg.get("local_id"))


def _ffmpeg_exe() -> str:
    custom = str(CFG.get("ffmpeg_path") or "")
    if custom and os.path.isfile(custom):
        return custom
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe() or ""
    except Exception:
        pass
    import shutil
    return shutil.which("ffmpeg") or ""


def _run_ffmpeg(args, timeout: float = 60):
    exe = _ffmpeg_exe()
    if not exe:
        return None
    import subprocess
    try:
        return subprocess.run(
            [exe] + [str(a) for a in args], capture_output=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        log("ffmpeg 执行失败：", repr(e))
        return None


def _video_duration(path: str) -> float:
    r = _run_ffmpeg(["-hide_banner", "-i", path], timeout=20)
    if r is None:
        return 0.0
    text = (r.stderr or b"").decode("utf-8", "replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        return 0.0
    return (int(m.group(1)) * 3600 + int(m.group(2)) * 60
            + float(m.group(3)))


def _video_montage(path: str, seconds: float = 0.0) -> str:
    """均匀抽几帧拼成一张图（2 列）。返回图片路径；失败返回空串。"""
    if not _ffmpeg_exe():
        log("没有 ffmpeg，视频只能看封面")
        return ""
    frames = max(2, min(12, int(CFG.get("video_frames", 6) or 6)))
    width = max(160, min(960, int(CFG.get("video_frame_width", 480) or 480)))
    seconds = float(seconds or _video_duration(path) or 0)
    if seconds <= 0:
        seconds = 10.0

    long_after = float(CFG.get("video_long_seconds", 60) or 0)
    if long_after > 0 and seconds > long_after:
        frames = max(frames, int(CFG.get("video_long_frames", 12) or 12))
        frames = min(12, frames)
    if frames <= 6:
        cols, rows = 2, max(1, frames // 2)
    else:
        cols, rows = 3, max(1, frames // 3)
    frames = cols * rows
    rate = frames / max(1.0, seconds)
    out = str(VIDEO_DIR / (Path(path).stem + "_frames.jpg"))
    filt = (f"fps={rate:.8f},scale={width}:-2,"
            f"tile={cols}x{rows}:padding=6:margin=6")
    r = _run_ffmpeg(["-y", "-i", path, "-vf", filt, "-frames:v", "1",
                     "-update", "1", "-q:v", "3", out], timeout=120)
    if r is None or r.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) < 512:
        log("视频抽帧失败，改用封面")
        try:
            os.remove(out)
        except OSError:
            pass
        return ""
    log(f"视频抽帧完成：{os.path.getsize(out) // 1024} KB"
        f"（约 {seconds:.0f} 秒）")
    return out


def _video_clock(seconds: float) -> str:
    try:
        s = int(max(0, seconds))
    except (TypeError, ValueError):
        return "0:00"
    return f"{s // 60}:{s % 60:02d}"


def _video_audio_windows(seconds: float, limit: int, mid: int) -> list:
    """要转写的片段：开头 limit 秒；长视频再加中段 mid 秒。"""
    out = [(0.0, float(max(1, limit)))]
    try:
        dur = float(seconds or 0)
    except (TypeError, ValueError):
        dur = 0.0
    if mid > 0 and dur > limit + mid + 20:
        start = max(float(limit), (dur - mid) / 2.0)
        out.append((round(start, 1), float(mid)))
    return out


def _video_audio_chunk(path: str, start: float = 0.0,
                       length: float = 30.0) -> str:
    """截一小段音频本地转写；太短或基本静音返回空串。

    asr 一次最多吃 30 秒，所以长片段先切碎再拼，别让开头 40 秒只转前 30。
    """
    if length > 28.0:
        parts = []
        pos = 0.0
        while pos < length:
            seg = min(28.0, length - pos)
            text = _video_audio_chunk(path, start + pos, seg)
            if text:
                parts.append(text)
            pos += seg
        return "，".join(parts)
    wav = str(VIDEO_DIR / (Path(path).stem + "_audio.wav"))
    args = ["-y"]
    if start and start > 0.1:
        args += ["-ss", "%.1f" % start]
    args += ["-i", path, "-vn", "-ac", "1", "-ar", "16000",
             "-t", str(int(max(1, length))), "-f", "wav", wav]
    r = _run_ffmpeg(args, timeout=120)
    if r is None or r.returncode != 0 or not os.path.isfile(wav):
        try:
            os.remove(wav)
        except OSError:
            pass
        return ""
    try:
        import wave
        with wave.open(wav, "rb") as f:
            raw = f.readframes(f.getnframes())
    except Exception as e:
        log("视频音轨读取失败：", repr(e))
        return ""
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
    if not raw:
        return ""
    import numpy as np
    audio = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    if audio.size < 8000 or float(abs(audio).max() or 0) < 0.01:
        if start and start > 0.1:
            log(f"视频 {_video_clock(start)} 起这段基本没声音，跳过")
        return ""
    t0 = time.time()
    text = asr.transcribe_audio(audio)
    if text:
        log(f"视频声音转写（{time.time() - t0:.1f}s）：{text}")
    return text


def _video_audio_text(path: str, seconds: float = 0.0) -> str:
    """转写视频里的说话声：开头一段，长视频再加中段一段。"""
    if not CFG.get("video_transcribe", True) or not asr.available():
        return ""
    limit = max(1, int(CFG.get("video_transcribe_seconds", 40) or 40))
    mid = max(0, int(CFG.get("video_transcribe_middle_seconds", 20) or 0))
    dur = float(seconds or 0) or _video_duration(path)
    windows = _video_audio_windows(dur, limit, mid)
    if len(windows) > 1:
        log("视频转写片段：" + "、".join(
            f"{_video_clock(s)}–{_video_clock(s + n)}" for s, n in windows))
    texts = []
    for start, length in windows:
        text = _video_audio_chunk(path, start, length)
        if not text:
            continue
        if start and start > 0.1:
            texts.append(f"（{_video_clock(start)} 起）{text}")
        else:
            texts.append(text)
    return "；".join(texts)


def _ensure_wechat_visible() -> bool:
    """微信被 Esc 收进托盘（窗口不可见）时把它放回来，不抢前台。"""
    try:
        u = _user32()
        fixed = False
        for h in _wechat_main_hwnds():
            try:
                if u.IsWindow(h) and not u.IsWindowVisible(h):
                    u.ShowWindow(h, 4)
                    fixed = True
            except Exception:
                continue
        if fixed:
            log("微信主窗口被收起了，已重新显示（不抢前台）")
        return fixed
    except Exception as e:
        log("恢复微信主窗口失败：", repr(e))
        return False


def _video_row_child_video(row):
    """在消息行里找视频缩略图控件；找不到返回 None。"""
    best = [None]

    def walk(c, d=0):
        if d > 4:
            return
        try:
            children = c.GetChildren()
        except Exception:
            return
        for ch in children:
            try:
                cn = (ch.ClassName or "").lower()
                nm = ch.Name or ""
                r = ch.BoundingRectangle
                w = r.right - r.left
                h = r.bottom - r.top
            except Exception:
                continue
            if (("video" in cn or "sight" in cn or "视频" in nm)
                    and w >= 80 and h >= 60):
                area = w * h
                if best[0] is None or area > best[0][0]:
                    best[0] = (area, ch)
            walk(ch, d + 1)

    try:
        walk(row)
    except Exception:
        return None
    return best[0][1] if best[0] else None


def _video_row_point(row):
    """估算视频缩略图上的落点。

    微信 4.2 的消息行铺满整个聊天宽度，视频缩略图只占最左边的一小块；
    深色主题下整行和背景都是暗色，按“非白像素重心”会落到整行中心
    （实测落在视频右边）。这里改成找“颜色起伏最大的一段”，并在找不到
    时按左侧固定偏移落点——头像右边一点就是缩略图。
    """
    r = row.BoundingRectangle
    left, top = int(r.left), int(r.top)
    right, bottom = int(r.right), int(r.bottom)
    w, h = right - left, bottom - top
    xoff = min(max(56, int(w * 0.10)), 88)
    cx = left + xoff + 70
    cy = top + h // 2
    try:
        from PIL import ImageGrab
        import numpy as np
        arr = np.array(ImageGrab.grab(
            bbox=(left, top, right, bottom)).convert("RGB")).astype("float32")
        gray = arr.mean(axis=2)
        x0 = xoff
        x1 = max(x0 + 20, w - 14)
        g = gray[:, x0:x1]
        if g.size:
            k = np.ones(5, dtype="float32") / 5.0
            col = np.convolve(g.std(axis=0), k, mode="same")
            thr = max(4.0, float(col.max()) * 0.18)
            hits = col >= thr
            best = None
            i, n = 0, len(hits)
            while i < n:
                if not hits[i]:
                    i += 1
                    continue
                j, gap = i, 0
                while j < n and (hits[j] or gap < 2):
                    gap = 0 if hits[j] else gap + 1
                    j += 1
                seg = (i, j - gap)
                if seg[1] - seg[0] >= 60:
                    width = seg[1] - seg[0]
                    if best is None or width > best[1] - best[0]:
                        best = seg
                i = j
            if best is not None:
                b0, b1 = best
                cx = left + x0 + (b0 + b1) // 2

                sub = gray[:, x0 + b0:x0 + b1]
                rowstd = np.convolve(sub.std(axis=1), k, mode="same")
                rthr = max(3.0, float(rowstd.max()) * 0.15)
                ys = np.where(rowstd >= rthr)[0]
                if len(ys) >= 20:
                    cy = top + int((ys[0] + ys[-1]) / 2)
    except Exception:
        pass
    return cx, cy


def _video_points(uia, chat, meta: dict = None):
    """返回「最新一条视频」缩略图上的候选落点，认不出视频就不点。

    微信 4.2 把视频行的可访问名字写成「视频 0:05」，所以先按名字 + 时长
    认行，再要求它就是最下面那一行；认不出宁可只看封面，也绝不乱点。
    """
    who = _chat_who(chat)
    try:
        cur = uia.current_chat()
    except Exception:
        cur = None
    if who and cur != who:
        if not uia.open_chat(who):
            log(f"{_chat_tag(chat)}视频取本体：切不到这个会话")
            return []
    lst = uia._message_list()
    if lst is None:
        return []
    rows = []
    for ch in list(lst.GetChildren()):
        try:
            cn = ch.ClassName or ""
            if not cn.startswith("mmui::Chat"):
                continue
            if cn in ("mmui::ChatItemView", "mmui::ChatSystemInfoItemView"):
                continue
            r = ch.BoundingRectangle
            if r.bottom - r.top < 40:
                continue
            try:
                nm = ch.Name or ""
            except Exception:
                nm = ""
            rows.append((ch, r, nm))
        except Exception:
            continue
    if not rows:
        return []

    def _secs(nm: str) -> int:
        m = re.search(r"(\d{1,2}):(\d{2})", nm)
        if not m:
            return -1
        return int(m.group(1)) * 60 + int(m.group(2))

    want = int(float((meta or {}).get("seconds") or 0))
    cands = [t for t in rows if "视频" in t[2]]
    if want and cands:
        near = [t for t in cands if abs(_secs(t[2]) - want) <= 1]
        if not near:
            log(f"{_chat_tag(chat)}视频时长对不上（消息 {want} 秒，"
                f"界面上 {'、'.join((t[2] or '?')[:18] for t in cands[-2:])}），"
                "不点")
            return []
        cands = near
    if not cands:
        tail = " / ".join((t[2] or t[0].ClassName or "?")[:18]
                          for t in rows[-2:])
        log(f"{_chat_tag(chat)}最下面不是视频（{tail}），不点")
        return []
    rows.sort(key=lambda t: (t[1].bottom, t[1].top))


    try:
        if rows[-1][1].bottom < lst.BoundingRectangle.bottom - 60:
            log(f"{_chat_tag(chat)}聊天没滚到最底下，可能点到旧视频，先不点")
            return []
    except Exception:
        pass


    tail = rows[-3:]
    near_tail = [t for t in cands if any(t[0] is x[0] for x in tail)]
    if not near_tail:
        log(f"{_chat_tag(chat)}视频已经不在最新几条里（被后来的消息顶下去了），"
            "不点，只看封面")
        return []
    if len(near_tail) > 1:
        log(f"{_chat_tag(chat)}最近几条里有 {len(near_tail)} 个时长一样的视频，"
            "分不清是哪个，不点")
        return []
    row, r, nm = near_tail[0]
    try:
        lb = lst.BoundingRectangle
        top_vis = max(r.top, lb.top)
        bot_vis = min(r.bottom, lb.bottom)
    except Exception:
        lb = None
        top_vis, bot_vis = r.top, r.bottom
    if bot_vis - top_vis < 120:
        log(f"{_chat_tag(chat)}视频只露出一点点，不点（怕点错）")
        return []
    log(f"{_chat_tag(chat)}视频位置：{nm!r} {row.ClassName or '?'} "
        f"({r.left},{r.top},{r.right},{r.bottom})")
    try:
        for ch in list(row.GetChildren())[:8]:
            cr = ch.BoundingRectangle
            log(f"{_chat_tag(chat)}  行内控件 {ch.ClassName or '?'} "
                f"({cr.left},{cr.top},{cr.right},{cr.bottom})")
    except Exception:
        pass
    points = []
    sub = _video_row_child_video(row)
    if sub is not None:
        sr = sub.BoundingRectangle
        points.append(((sr.left + sr.right) // 2, (sr.top + sr.bottom) // 2))
    points.append(_video_row_point(row))
    w = r.right - r.left
    h = r.bottom - r.top
    xoff = min(max(56, int(w * 0.10)), 88)

    points.append((r.left + xoff + 40, r.top + h // 2))
    points.append((r.left + xoff + 100, r.top + h // 2))
    out = []
    for p in points:
        x, y = p

        y = min(max(y, top_vis + 8), bot_vis - 8)
        x = min(max(x, r.left + 4), r.right - 4)
        if (x, y) not in out:
            out.append((x, y))
    return out


def _wechat_mute_state_path() -> str:
    """静音前的原始状态存这里：万一 bot 中途崩了，下次启动能恢复。"""
    base = os.environ.get("CHATBOT_LOG_DIR") or str(BASE_DIR / "logs")
    return os.path.join(base, "wechat_mute_state.json")


def _wx_mute_state_items(path: str) -> list:
    """读静音标记里的会话快照（兼容老的纯列表格式）。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("sessions") or []
    if not isinstance(data, list):
        return []
    return [it for it in data if isinstance(it, dict)]


def _wechat_audio_sessions() -> list:
    """只取微信自己的音频会话，不碰系统音量、不碰别的程序。"""
    try:
        from pycaw.pycaw import AudioUtilities
    except Exception:
        return []
    out = []
    try:
        for s in AudioUtilities.GetAllSessions():
            try:
                p = s.Process
                name = (p.name() if p else "") or ""
            except Exception:
                name = ""
            low = name.lower()
            if "weixin" not in low and "wechat" not in low:
                continue
            out.append(s)
    except Exception as e:
        log("读微信音频会话失败（pycaw）：", repr(e))
    return out


def _wx_audio_mute(flag: bool) -> bool:
    """只静音/恢复微信自己的声音，不动系统音量。

    静音前先把每个会话原本的静音状态记到文件里；万一这次取视频把微信
    静音后 bot 崩了，下次启动会按原样恢复，不会让管理员的微信一直没声音。
    """
    if flag and not CFG.get("video_mute_wechat", True):
        return False
    sessions = _wechat_audio_sessions()
    if not sessions:
        return False
    path = _wechat_mute_state_path()
    if flag and os.path.exists(path) and not _wx_mute_state_items(path):
        log("微信原音量快照损坏，取消点开视频以免无法恢复")
        return False
    if flag and not os.path.exists(path):
        snap = []
        for s in sessions:
            try:

                snap.append({"pid": int(s.ProcessId),
                             "muted": bool(s.SimpleAudioVolume.GetMute())})
            except Exception:

                return False
        try:
            base = os.path.dirname(path)
            if base:
                os.makedirs(base, exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "sessions": snap}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(path + ".tmp", path)
        except OSError as exc:
            log("保存微信原音量失败，取消点开视频：", repr(exc))
            return False
    snap = {}
    if not flag:
        for it in _wx_mute_state_items(path):
            try:
                snap.setdefault(int(it.get("pid") or 0), []).append(
                    bool(it.get("muted")))
            except Exception:
                continue
    hit = 0
    used = {}
    for s in sessions:
        try:
            if flag:
                s.SimpleAudioVolume.SetMute(1, None)
                if not s.SimpleAudioVolume.GetMute():
                    continue
            else:
                pid = int(s.ProcessId)
                idx = used.get(pid, 0)
                vals = snap.get(pid) or []

                want = vals[idx] if idx < len(vals) else False
                used[pid] = idx + 1
                s.SimpleAudioVolume.SetMute(1 if want else 0, None)
            hit += 1
        except Exception:
            continue
    if not flag:


        for _ in range(2):
            fixed = 0
            used2 = {}
            for s in _wechat_audio_sessions():
                try:
                    pid = int(s.ProcessId)
                    idx = used2.get(pid, 0)
                    vals = snap.get(pid) or []
                    want = vals[idx] if idx < len(vals) else False
                    used2[pid] = idx + 1
                    if bool(s.SimpleAudioVolume.GetMute()) != want:
                        s.SimpleAudioVolume.SetMute(1 if want else 0, None)
                        fixed += 1
                except Exception:
                    continue
            if not fixed:
                break
            time.sleep(0.2)
        restored = True
        check_used = {}
        for session in _wechat_audio_sessions():
            try:
                pid = int(session.ProcessId)
                idx = check_used.get(pid, 0)
                vals = snap.get(pid) or []
                want = vals[idx] if idx < len(vals) else False
                check_used[pid] = idx + 1
                if bool(session.SimpleAudioVolume.GetMute()) != want:
                    restored = False
            except Exception:
                restored = False
        if restored:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        else:
            log("微信音量尚未恢复，保留恢复标记供下次重试")
            return False
    if flag and hit != len(sessions):

        _wx_audio_mute(False)
        return False
    return bool(hit)


def _wx_audio_unmute_stale() -> None:
    """上次取视频静音后 bot 崩了（标记文件还在）：启动时先恢复声音。

    看门狗崩了会很快把 bot 拉起来，所以不能按“文件太新”跳过，否则微信
    会一直没声音；改成看当初静音的那个进程还在不在。
    """
    path = _wechat_mute_state_path()
    if not os.path.exists(path):
        return
    old_pid = 0
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            old_pid = int(data.get("pid") or 0)
    except Exception:
        old_pid = 0
    if old_pid and old_pid != os.getpid() and _process_exe(old_pid):

        return
    if _wx_audio_mute(False):
        log("上次取视频把微信静音了但没恢复，已还原成原来的声音状态")


def _video_player_windows() -> list:
    """微信的视频播放窗（标题「图片和视频」）句柄。"""
    u = _user32()
    hits = []

    def _cb(h, _lp):
        try:
            if u.IsWindowVisible(h) and _window_title(h) == "图片和视频":
                pid = wintypes.DWORD()
                u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                if _process_exe(pid.value) == "weixin.exe":
                    hits.append(h)
        except Exception:
            pass
        return True

    try:
        u.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                         wintypes.LPARAM)(_cb), 0)
    except Exception:
        pass
    return hits


def _close_video_player(old=(), wait: float = 0.0) -> bool:
    """关掉我们刚点开的播放窗；用户自己开着的（old 里的）不动。

    微信有时是「先下载、下完才建播放窗」，关早了会漏掉；所以 wait > 0 时
    在 wait 秒内一直盯着，冒出来一个关一个。
    """
    seen = []
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        fresh = [h for h in _video_player_windows()
                 if h not in old and h not in seen]
        for h in fresh:
            seen.append(h)
            try:
                _user32().PostMessageW(h, 0x0010, 0, 0)
            except Exception:
                continue
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    if seen:

        time.sleep(0.3)
        for h in _video_player_windows():
            if h in old:
                continue
            try:
                _user32().PostMessageW(h, 0x0010, 0, 0)
            except Exception:
                pass
        log("已关掉视频播放窗（不出声、不挡住聊天）")
    return bool(seen)


def _video_cache_path(msg: dict, chat: str = None) -> str:
    """微信缓存里「这一条」视频的本体（<id>.mp4）；没下过返回空串。"""
    thumb = _video_thumb_path(msg, chat)
    if not thumb:
        return ""
    p = thumb[:-len("_thumb.jpg")] + ".mp4"
    try:
        if os.path.getsize(p) > 0:
            return p
    except OSError:
        pass
    return ""


def _video_delete_cache(msg: dict, chat: str = None) -> bool:
    """看完把这次下载的视频本体删掉，别让微信缓存越积越大。"""
    p = _video_cache_path(msg, chat)
    if not p:
        return False
    for attempt in range(3):
        try:
            try:
                os.chmod(p, 0o666)
            except OSError:
                pass
            os.remove(p)
            log(f"{_chat_tag(chat)}看完已删掉下载的视频本体")
            return True
        except OSError as e:
            if attempt >= 2:
                log(f"{_chat_tag(chat)}删视频本体失败：{e!r}")
                return False
            time.sleep(0.6)
    return False


def _cleanup_video_temps(max_age_seconds: float = 3600.0) -> int:
    """清掉 bot 自己 images/video 里上次崩溃留下的临时文件。"""
    removed = 0
    try:
        files = list(VIDEO_DIR.glob("*"))
    except OSError:
        return 0
    now = time.time()
    for p in files:
        try:
            if not p.is_file() or p.suffix.lower() not in (".mp4", ".jpg", ".wav"):
                continue
            if now - p.stat().st_mtime < max_age_seconds:
                continue
            p.unlink()
            removed += 1
        except OSError:
            continue
    if removed:
        log(f"清理视频临时文件：{removed} 个")
    return removed


def _video_cached_mp4(msg: dict, chat: str = None) -> str:
    """只认本条消息对应的缓存 ID；不按时间/大小猜别人的视频。"""
    p = _video_cache_path(msg, chat)
    try:
        return p if p and os.path.getsize(p) > 4096 else ""
    except OSError:
        return ""


def _video_click_download(msg: dict, chat: str = None, meta: dict = None) -> str:
    """点一下视频缩略图，让微信自己把本体下到 msg/video（静音，不右键）。

    微信 4.2 的视频右键菜单里没有「另存为...」，但点开缩略图会先下载再
    播放：点之前先把微信的声音静音，下完立刻关掉播放窗，全程不出声。
    """
    if db is None:
        return ""
    hit = _video_cached_mp4(msg, chat)
    if hit:
        return hit
    if not CFG.get("video_fetch_enabled", True):
        return ""
    age_cap = float(CFG.get("video_fetch_max_age_seconds", 600) or 0)
    if age_cap > 0 and _video_latest_age(msg) > age_cap:
        log(f"{_chat_tag(chat)}视频已经过了 {int(age_cap)} 秒，不折腾界面")
        return ""
    meta = meta or _video_meta(str(msg.get("content") or ""))
    try:
        uia = _voice_gui()._get_uia()
    except Exception as e:
        log(f"{_chat_tag(chat)}视频取本体：拿不到 UIA：{e!r}")
        return ""
    if uia is None:
        log(f"{_chat_tag(chat)}视频取本体：UIA 不可用")
        return ""
    prev, wx_min = _capture_foreground_before_send(label="取视频")
    old_players = set(_video_player_windows())
    muted = False
    clicked = False
    try:
        try:
            uia.ensure_window()
        except Exception:
            pass
        if not _is_wechat_foreground():
            log(f"{_chat_tag(chat)}视频取本体：微信没切到前台，先不点"
                "（免得点到别的窗口）")
            return ""
        points = _video_points(uia, chat, meta)
        if not points:
            log(f"{_chat_tag(chat)}视频取本体：找不到最新一条视频的位置")
            return ""
        x, y = points[0]

        if not CFG.get("video_mute_wechat", True):
            log(f"{_chat_tag(chat)}静音开关关着，为了不出声，这次只看封面"
                "（不点开视频）")
            return ""
        quiet = False
        for _ in range(10):
            if _wx_audio_mute(True):
                quiet = True
                break
            time.sleep(0.2)
        muted = quiet
        if not quiet:
            log(f"{_chat_tag(chat)}微信没有可静音的音频会话，"
                "为了不出声，这次只看封面（不点开视频）")
            return ""
        log(f"{_chat_tag(chat)}视频点开下载：({x},{y})（微信已静音）")
        clicked = True
        uia._click_at(x, y)
        timeout = float(CFG.get("video_fetch_timeout_seconds", 30) or 30)
        deadline = time.monotonic() + max(8.0, timeout)
        want = int(meta.get("bytes") or 0)
        last = -1
        stable = 0
        cache_path = _video_cache_path(msg, chat)
        if not cache_path:
            thumb = _video_thumb_path(msg, chat)
            cache_path = (thumb[:-len("_thumb.jpg")] + ".mp4") if thumb else ""
        next_resolve = 0.0
        while time.monotonic() < deadline:

            current_sessions = _wechat_audio_sessions()
            if not current_sessions or not all(bool(s.SimpleAudioVolume.GetMute())
                                               for s in current_sessions):
                log(f"{_chat_tag(chat)}静音状态发生变化，停止取视频")
                return ""
            if not cache_path and time.monotonic() >= next_resolve:
                cache_path = _video_cache_path(msg, chat)
                next_resolve = time.monotonic() + 2.0
            hit = cache_path
            size = 0
            if hit:
                try:
                    size = os.path.getsize(hit)
                except OSError:
                    size = 0
            if size > 8192:
                if size == last:
                    stable += 1
                else:
                    stable = 0
                    last = size
                if stable >= 2 and (not want or size >= want * 0.98):
                    log(f"{_chat_tag(chat)}视频本体已下载：{size // 1024} KB"
                        f"（消息里 {meta.get('mb') or '?'} MB）")
                    return hit
            time.sleep(0.35)
        log(f"{_chat_tag(chat)}视频点了也没下下来，只能看封面")
        return ""
    except Exception as e:
        log(f"{_chat_tag(chat)}视频取本体失败：{e!r}")
        return ""
    finally:
        if not _close_video_player(old_players, wait=2.5 if clicked else 0) \
                and clicked:
            log(f"{_chat_tag(chat)}没看到独立播放窗（可能是聊天内嵌播放）；"
                "微信已经静音，不会出声")
        if muted:
            _wx_audio_mute(False)
        _restore_foreground(prev, wx_min)
        _ensure_wechat_visible()


def _video_prompt(nick: str, meta: dict, has_video: bool, has_frames: bool,
                  audio_text: str) -> str:
    """把「拿到了什么」讲清楚，模型才知道能不能说“看完了”。"""
    parts = [f"{nick} 发来一段视频（{_video_time_text(meta.get('seconds'))}，"
             f"约 {meta.get('mb') or '?'} MB）。"]
    if has_frames:
        parts.append("我从视频里抽了几帧拼成一张图，"
                     "从左到右、从上到下是时间顺序。")
        if audio_text:
            parts.append("视频里的声音转成文字了（本地识别，可能有错）："
                         f"「{audio_text}」。")
        parts.append("用一两句自然地回应视频内容，别逐帧描述，也别报流水账。")
    elif has_video:
        parts.append("视频拿到了但抽帧失败，只看到一张模糊封面。"
                     "不知道里面发生了什么就别装看过。")
    else:
        parts.append("视频本体没下下来，眼前只有微信给的一张封面图"
                     "（一般是视频的第一帧）。")
        if audio_text:
            parts.append(f"声音转写（本地识别，可能有错）：「{audio_text}」。")
        parts.append("先具体说说封面里看到了什么（地方、人物、光线、天气"
                     "都行），再自然地带一句视频过程没看着；"
                     "别只说“看不到”“没看到视频内容”，"
                     "也不要把封面当成整段视频去编过程。")
    return "".join(parts)


def _video_fail_text(meta: dict) -> str:
    return (f"这个视频（{_video_time_text(meta.get('seconds'))}）我打不开…"
            "你讲讲里面是啥嘛")


_STICKER_GUI = None


def _sticker_gui():
    """惰性创建用于表情截图的 WeChatGUI（只读：截图/定位，绝不发送）。"""
    global _STICKER_GUI
    if _STICKER_GUI is None:
        from wechatauto.guia import WeChatGUI
        _STICKER_GUI = WeChatGUI()
    return _STICKER_GUI


def _sticker_is_still_latest(msg: dict, chat: str = None) -> bool:
    """这张表情包现在还是会话里最新的一条吗？

    截图只会截「最后一张表情」。等她处理这张时，如果后面
    已经有新消息（尤其是她刚补发的那张贴纸），截出来的就是新的那张，
    于是她会把自己发的表情包当成群友发的去识别。此时干脆不截。
    """
    seq = msg.get("sort_seq")
    if not seq:
        return False
    try:
        rows = db.get_messages(chat or group_username, limit=30)
    except Exception as e:
        log("读取群消息（确认表情包是否最新）失败：", repr(e))
        return False
    target = int(seq)
    found = False
    for m in rows or []:
        if m.get("type") == "系统消息":
            continue
        s = int(m.get("sort_seq") or 0)
        if s > target:
            return False
        if s == target:
            found = True
    return found


def _capture_sticker_image(msg: dict, chat: str = None) -> str:
    """把一条「动画表情」消息截图成 PNG，返回路径；失败返回 None。

    微信 4.x 表情图片在本地库里是加密的、无法离线解出原图，所以走库自带的
    ``EmojiMessage.capture()`` 屏幕截图方案：打开会话 → 滚动到底 → 对最底部
    那条表情气泡截图。**纯读操作，不发送任何内容**。依赖「该表情是会话里
    最新一条且窗口可见」，失败时上层直接回退到普通兜底回复。
    """
    save_dir = str(IMAGE_DIR / "emoji")
    try:
        from wechatauto.wx import Chat, _db_row_to_message
        chat = Chat(who=_chat_who(chat), gui=_sticker_gui(), db=db)
        obj = _db_row_to_message(msg, chat, self_wxid, db)
        path = obj.capture(save_dir=save_dir)
        if path and os.path.exists(path):
            return path
        return None
    except Exception as e:
        log("表情包截图失败：", repr(e))
        return None


_RECALL_ASK = re.compile(r"记(得|住).{0,6}(我|什么|啥)|我(说|讲|提)过(什么|啥)")


def _memory_hint(chat: str = None, sender_id: str = "", nick: str = "",
                 limit: int = None) -> str:
    """取「关于这个人的旧事」塞进这一轮请求。

    关掉开关、没有记录、文件读坏了，都返回空串：记忆永远不能影响正常回复。
    """
    if not sender_id or not CFG.get("memory_enabled", True):
        return ""
    try:


        days = CFG.get("memory_max_age_days", 45)
        if limit and int(limit) >= 99:
            days = 0
        return memory.hint(_hist_key(chat), sender_id, display_name=nick,
                           limit=int(limit or CFG.get("memory_recall", 6)),
                           max_age_days=days)
    except Exception as e:
        log("长期记忆读取失败：", repr(e))
        return ""


def _remember_from_text(text: str, nick: str, wxid: str = "", chat: str = None) -> None:
    """从这句话里抽稳定信息存下来（称呼/喜好/忌口/生日/工作等）。

    只记本人主动交代过的内容；抽不到什么都不做。写失败只记日志。
    """
    if not wxid or not CFG.get("memory_enabled", True):
        return
    try:
        if re.search(r"(忘掉|忘记|别记|不要记|删掉|清除).{0,6}(我|说过|记的)", text):

            if memory.forget(_hist_key(chat), wxid):
                log(f"{_chat_tag(chat)}按 {nick} 的要求删掉了他的记忆")
            return
        facts = memory.extract_facts(text)
        added = memory.remember(_hist_key(chat), wxid, name=nick, facts=facts,
                                group=_chat_who(chat))
        if added:
            log(f"{_chat_tag(chat)}记住 {nick}：" + "；".join(added))
    except Exception as e:
        log("长期记忆写入失败：", repr(e))


_dup_lock = threading.Lock()
_dup_text = {}


def _repeat_spam(chat: str, wxid: str, text: str, window: float = 90.0,
                 limit: int = 2) -> bool:
    """同一个人短时间把同一句话刷第 3 遍起就不回了。

    55 人的群里常有人连刷同一句，句句都回只会把群刷得更乱，还白烧 token。
    """
    if not wxid or not text:
        return False
    key = (chat or "", wxid)
    now = time.time()
    with _dup_lock:
        if len(_dup_text) > 500:
            for k in [k for k, v in _dup_text.items() if now - v[1] > window * 4]:
                _dup_text.pop(k, None)
        rec = _dup_text.get(key)
        if rec and rec[0] == text and now - rec[1] <= window:
            rec[2] += 1
            return rec[2] > limit
        _dup_text[key] = [text, now, 1]
        return False


def _refresh_hotwords(quiet: bool = False) -> str:
    """把群成员 + 自动学到的称呼一起喂给 whisper 当热词。"""
    hot_names = []


    for uid in GROUP_BY_USERNAME:
        name = GROUP_BY_USERNAME.get(uid, "")
        if name:
            hot_names.extend(aliases.auto_map(name).keys())
            hot_names.extend(aliases.bot_names(name))
    hot_names.extend(CFG.get("bot_names") or [])
    hot_names.extend((CFG.get("nickname_map") or {}).values())

    for uid in GROUP_BY_USERNAME:
        hot_names.extend((GROUP_MEMBERS.get(uid) or {}).values())
    hot = asr.set_hotwords(hot_names)
    if not quiet:
        log(f"语音热词：{len(hot.split('、')) if hot else 0} 个称呼")
    return hot


def _alias_text(chat: str, text: str = "") -> str:
    """本群的叫法/黑话提示；没有或出错都返回空串。"""
    if not CFG.get("aliases_enabled", True):
        return ""
    try:
        return aliases.hint(
            GROUP_BY_USERNAME.get(chat, "") or "", text,
            resolve=lambda w: display_name(w, chat))
    except Exception as e:
        log("群里叫法读取失败：", repr(e))
        return ""


def _alias_learn(chat: str, refer: dict) -> bool:
    """从引用里学一条叫法：被引用人的 displayname -> wxid。

    微信自己写在 refermsg 里的对应关系，不是猜的；
    拿不到（旧版客户端没有 chatusr）就当没看到。
    返回是否真的学到了新东西。
    """
    try:
        if chat not in GROUP_BY_USERNAME:
            return False
        wid = str(refer.get("wxid") or "").strip()
        nm = str(refer.get("name") or "").strip()
        if not wid or wid == chat or "@chatroom" in wid:
            return False


        if not nm or nm in ("某人", "未知", "群友", "群成员", "某成员",
                            "匿名", "对方", "用户", "网友"):
            nm = _roster_name(wid, chat)
        if not nm:
            return False
        group = GROUP_BY_USERNAME.get(chat, "") or ""
        if self_wxid and wid == self_wxid:
            learned = aliases.remember(group, nm, wid, is_bot=True)
        else:
            learned = aliases.remember(group, nm, wid)
        if learned:

            _refresh_hotwords(quiet=True)
        return learned
    except Exception as e:
        log("叫法学习失败：", repr(e))
        return False


def _learn_from_history(chat: str, limit: int = 400) -> None:
    """开机时从最近的聊天记录里补一遍叫法。

    只认带 refermsg 的消息（里面有 chatusr）；
    走后台线程，失败只记日志，不影响收发。
    """
    try:
        msgs = db.get_messages(chat, limit=limit)
    except Exception as e:
        log(f"补学叫法失败（{chat}）：{e!r}")
        return
    n = 0
    for m in msgs or []:
        c = str(m.get("content") or "")
        if "<refermsg>" not in c:
            continue
        refer = (_parse_appmsg(c) or {}).get("refer") or {}
        if _alias_learn(chat, refer):
            n += 1
    if n:
        log(f"{_chat_tag(chat)}从聊天记录里补学了 {n} 个叫法")


def _mentions_bot(content: str, chat: str = "") -> bool:
    for name in CFG.get("bot_names") or []:
        if name and name in content:
            return True

    try:
        return aliases.self_mentioned(
            GROUP_BY_USERNAME.get(chat, "") or "", content)
    except Exception:
        return False


_REPLY_ASK = re.compile(
    r"(？|\?|吗|嘛|"
    r"(?<![没不别])什么|(?<![没不别])啥|(?<![不没])怎么|怎样|"
    r"为什么|为啥|哪(?![怕])|谁|多少|几点|几个|"
    r"在吗|在不在|帮我|给我|你来|你说|你觉|你看|你能|你会|你要|"
    r"能不能|可不可以|是不是|有没有|要不要|行不行|好不好|对不对|"
    r"推荐|介绍|翻译|查一下|搜一下|来一个|来首|来段)")

_REPLY_CHANCE_DEFAULT = {
    "chat": 0.2,
    "question": 1.0,
    "image": 0.85,
    "voice": 0.9,
    "quote": 0.7,
    "card": 0.4,
    "video": 0.95,
}


def _reply_chance(kind: str, content: str = "") -> float:
    """这条消息接话的概率；都能在 config.json 里调。"""
    key = "reply_chance_" + kind
    try:
        chance = float(CFG.get(key, _REPLY_CHANCE_DEFAULT.get(kind, 0.2)))
    except (TypeError, ValueError):
        chance = _REPLY_CHANCE_DEFAULT.get(kind, 0.2)
    if kind == "chat" and content and _REPLY_ASK.search(content):
        try:
            chance = float(CFG.get("reply_chance_question", 1.0))
        except (TypeError, ValueError):
            chance = 1.0
    return max(0.0, min(1.0, chance))


def _should_reply(content: str, chat: str, kind: str = "chat",
                  extra: str = "") -> bool:
    """要不要接这条；False 表示只记上下文、不说话。

    extra 是同一轮里前面几条消息的文本：只看最后一条可能像没人叫她，
    但整轮里有人问过、叫过，就得接。
    """
    if _is_private_chat(chat):
        return True
    if not CFG.get("reply_decision_enabled", True):
        return True
    blob = content or ""
    if extra:
        blob = blob + "\n" + extra
    if blob and _mentions_bot(blob, chat):
        return True
    if extra and _REPLY_ASK.search(extra):
        log(f"{_chat_tag(chat)}这一轮里有人问话，接一句")
        return True
    chance = _reply_chance(kind, content)
    if chance >= 1.0:
        return True
    if chance <= 0.0:
        return False
    if random.random() < chance:
        return True
    log(f"{_chat_tag(chat)}这条不接（{kind} {int(chance * 100)}%）："
        f"{(content or '')[:24]}")
    return False


_APPMSG_RE = re.compile(r"<appmsg>(.*?)</appmsg>", re.S)
_REFERMSG_RE = re.compile(r"<refermsg>(.*?)</refermsg>", re.S)
_TITLE_TAG_RE = re.compile(r"<title>(.*?)</title>", re.S)
_URL_TAG_RE = re.compile(r"<url>(.*?)</url>", re.S)
_APP_TYPE_RE = re.compile(r"<type>\s*(\d+)\s*</type>")
_DISPLAYNAME_RE = re.compile(r"<displayname>(.*?)</displayname>", re.S)
_REF_CONTENT_RE = re.compile(r"<content>(.*?)</content>", re.S)


_CHATUSR_RE = re.compile(r"<chatusr>(.*?)</chatusr>", re.S)
_XML_TAG_RE = re.compile(r"<[^>]+>")

_REF_KIND = {"3": "[图片]", "34": "[语音]", "43": "[视频]", "47": "[表情包]",
             "42": "[名片]", "48": "[位置]"}


def _xml_text(raw: str) -> str:
    """XML 片段 -> 干净文字：反转义、去标签、压空白。"""
    t = html.unescape(raw or "")
    t = _XML_TAG_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _parse_appmsg(content: str) -> dict:
    """解析 文件/链接/卡片 的 XML；不是卡片返回 {}。

    返回 {"type": 卡片类型, "title": 标题/用户打的字, "url": 链接,
          "refer": {"type", "name", "text"} 或 None}
    """
    m = _APPMSG_RE.search(str(content or ""))
    if not m:
        return {}
    body = m.group(1)
    tm = _APP_TYPE_RE.search(body)
    ti = _TITLE_TAG_RE.search(body)
    um = _URL_TAG_RE.search(body)
    info = {
        "type": tm.group(1) if tm else "",
        "title": _xml_text(ti.group(1)) if ti else "",
        "url": _xml_text(um.group(1)) if um else "",
        "refer": None,
    }
    rm = _REFERMSG_RE.search(body)
    if not rm:
        return info
    ref = rm.group(1)
    rtm = _APP_TYPE_RE.search(ref)
    rtype = rtm.group(1) if rtm else ""
    dn = _DISPLAYNAME_RE.search(ref)
    name = _xml_text(dn.group(1)) if dn else "某人"
    cm = _REF_CONTENT_RE.search(ref)
    raw = cm.group(1) if cm else ""
    if rtype in _REF_KIND:
        text = _REF_KIND[rtype]
    elif rtype == "49":
        qt = _TITLE_TAG_RE.search(raw)
        text = "分享的内容「%s」" % _xml_text(qt.group(1)) if qt else "[卡片]"
    else:
        text = _xml_text(raw)
        if len(text) > 60:
            text = text[:60] + "…"
    cu = _CHATUSR_RE.search(ref)
    info["refer"] = {"type": rtype, "name": name,
                     "text": text or "[空]",
                     "wxid": _xml_text(cu.group(1)) if cu else ""}
    return info


_slang_lock = threading.Lock()


def _slang_summary(text: str, limit: int = 10):
    """从候选黑话清单里抽一句简报：返回 (总数, 前几个词)。"""
    total = 0
    words = []
    for line in str(text or "").splitlines():
        m = re.match(r"^\s*\d+\.\s+(\S+)", line)
        if not m:
            continue
        total += 1
        if len(words) < limit:
            words.append(m.group(1))
    return total, words


def _slang_seen() -> set:
    return set(str(w) for w in (_push_state_read().get("seen") or []) if w)


def _sensitive_words() -> list:
    try:
        with open(SENSITIVE_PATH, encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return []
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def _slang_safe(word: str, bad) -> bool:
    w = str(word or "").lower()
    return not any(str(b).lower() in w for b in bad if b)


def _slang_seen_add(words) -> None:
    data = _push_state_read()
    seen = set(str(w) for w in (data.get("seen") or []) if w)
    seen.update(str(w) for w in words if w)
    data["seen"] = sorted(seen)[-500:]
    _push_state_write(data)


def _slang_report(chat: str, only_new: bool = False):
    """扫候选黑话并把简报发到 chat。True=已发/无需发，False=失败，None=正在扫。"""
    if not _slang_lock.acquire(blocking=False):
        send_reply("候选黑话正在扫，等一会儿", quote=None, chat=chat,
                   allow_split=False)
        return None
    ok = False
    try:
        import slang_candidates as sc
        rc = sc.main(["2000"])
        try:
            with open(sc.OUT_PATH, encoding="utf-8") as f:
                raw = f.read()
        except Exception:
            raw = ""
        total, all_words = _slang_summary(raw, limit=1000000)
        bad = _sensitive_words()
        added = "-"
        if only_new:
            seen = _slang_seen()
            new_words = [w for w in all_words if w not in seen]
            added = len(new_words)
            if not new_words:
                log("候选黑话：本周没有新增候选，跳过发送")
                return True
            safe_new = [w for w in new_words if _slang_safe(w, bad)]
            hidden = len(new_words) - len(safe_new)
            show = safe_new[:10]
            body = f"本周新增候选 {len(new_words)} 个"
            if show:
                body += "：" + "、".join(show)
            if hidden:
                body += f"（{hidden} 个含敏感词已省略）"
            body += "。完整清单在 data/slang_candidates.txt"
        else:
            safe_all = [w for w in all_words if _slang_safe(w, bad)]
            hidden = len(all_words) - len(safe_all)
            show = safe_all[:10]
            if show:
                body = f"候选黑话 {total} 个：" + "、".join(show)
                if hidden:
                    body += f"（{hidden} 个含敏感词已省略）"
                body += "。完整清单在 data/slang_candidates.txt"
            elif all_words:
                body = (f"候选黑话 {total} 个，全部含敏感词已省略。"
                        "完整清单在 data/slang_candidates.txt")
            else:
                body = ("这次没扫出新候选；可能是聊天记录太少。"
                        "完整清单在 data/slang_candidates.txt")
        ok = bool(send_reply(body, quote=None, chat=chat, allow_split=False))
        if ok:
            _slang_seen_add(all_words)
        log(f"候选黑话：{total} 个候选，新增={added}，rc={rc}，已发={ok}")
    except Exception as e:
        log("候选黑话扫描失败：", repr(e))
        send_reply("候选黑话没扫出来，日志里有原因", quote=None, chat=chat,
                   allow_split=False)
    finally:
        _slang_lock.release()
    return ok


def _week_key(ts=None) -> str:
    """ISO 周标识（如 2026-W45），用来判断这周推没推过。"""
    return time.strftime("%G-W%V", time.localtime(ts))


def _push_state_read() -> dict:
    try:
        data = json.load(open(PUSH_STATE_PATH, encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _push_state_write(data: dict) -> None:
    try:
        PUSH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(PUSH_STATE_PATH) + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, PUSH_STATE_PATH)
    except Exception as e:
        log("候选黑话推送状态写入失败：", repr(e))


def _slang_push_due(now=None) -> bool:
    """现在是不是该推送：到点（周六 18:00 之后）且本周还没推过。"""
    if not CFG.get("slang_push_enabled", False):
        return False
    ts = time.time() if now is None else float(now)
    lt = time.localtime(ts)
    wd = int(CFG.get("slang_push_weekday", 5)) % 7
    when = (int(CFG.get("slang_push_hour", 18)),
            int(CFG.get("slang_push_minute", 0)))
    due = (lt.tm_wday == wd and (lt.tm_hour, lt.tm_min) >= when)

    catch_up = (lt.tm_wday == (wd + 1) % 7 and lt.tm_hour < 12)
    if not (due or catch_up):
        return False
    return _push_state_read().get("last_week") != _week_key(ts)


def _slang_push_mark(now=None) -> None:
    """记下「这一周已经推过」，避免重启后重复推。"""
    ts = time.time() if now is None else float(now)
    data = _push_state_read()
    data["last_week"] = _week_key(ts)
    data["last_date"] = time.strftime("%Y%m%d", time.localtime(ts))
    _push_state_write(data)


def _slang_push_loop() -> None:
    """每周固定时间跑一次候选黑话，把简报发到管理员私聊。"""
    fails = 0
    while True:
        try:
            if _slang_push_due():
                log("候选黑话定时推送：开始扫描")
                res = _slang_report(
                    OWNER_CHAT,
                    only_new=bool(CFG.get("slang_push_only_new", True)))
                if res is None:

                    time.sleep(120)
                    continue
                if res:
                    _slang_push_mark()
                    fails = 0
                    log("候选黑话定时推送：本周已完成")
                else:
                    fails += 1
                    if fails >= 3:
                        _slang_push_mark()
                        log("候选黑话定时推送：连续失败 3 次，本周放弃")
                        fails = 0
                    else:
                        log(f"候选黑话定时推送：失败 {fails}/3，1 小时后再试")
                        time.sleep(3600)
                        continue
        except Exception as e:
            log("候选黑话定时推送异常：", repr(e))
        time.sleep(300)


def backup_data(force: bool = False) -> int:
    """把 data 下的 json 每天备份一份，保留 7 天。返回备份了几个文件。"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log("备份目录建不了：", repr(e))
        return 0
    day = time.strftime("%Y%m%d")
    made = 0
    sources = (
        (DATA_DIR / "memory.json", "memory"),
        (DATA_DIR / "aliases.json", "aliases"),
        (DATA_DIR / "sensitive_words.txt", "sensitive_words"),
        (BASE_DIR / "config.json", "config"),
        (BASE_DIR / "prompt_style.txt", "prompt_style"),
    )
    for src, stem in sources:
        if not src.exists():
            continue
        dst = BACKUP_DIR / ("%s_%s%s" % (stem, day, src.suffix))
        if dst.exists() and not force:
            continue
        try:
            dst.write_bytes(src.read_bytes())
            made += 1
        except Exception as e:
            log(f"备份 {src.name} 失败：", repr(e))
    try:
        cutoff = time.time() - BACKUP_KEEP_DAYS * 86400
        for p in BACKUP_DIR.iterdir():
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
    except Exception as e:
        log("清理旧备份失败：", repr(e))
    if made:
        log(f"数据已备份：{made} 个文件 -> {BACKUP_DIR}")
    return made


def _cleanup_logs(dirs=None) -> int:
    """删掉超过 30 天的日志；返回删了几个文件。"""
    dirs = dirs or [BASE_DIR / "logs", BASE_DIR / "wechatauto_logs"]
    cutoff = time.time() - LOG_KEEP_DAYS * 86400
    removed = 0
    for d in dirs:
        try:
            p = Path(d)
            if not p.exists():
                continue
            for f in p.glob("*.log"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
                except OSError:
                    pass
        except Exception as e:
            log("清理旧日志失败：", repr(e))
    if removed:
        log(f"清理旧日志：{removed} 个（保留 {LOG_KEEP_DAYS} 天）")
    return removed


def _backup_loop() -> None:
    """启动时先备一份，之后每 6 小时看一次，跨天再备。"""
    while True:
        try:
            backup_data()
            _cleanup_logs()
        except Exception as e:
            log("备份线程异常：", repr(e))
        time.sleep(6 * 3600)


def _config_selfcheck() -> int:
    """启动时把关键配置过一遍，缺什么当场说清楚。返回问题数。"""
    bad = 0
    for key in ("api_key", "base_url", "model", "group_name"):
        if not str(CFG.get(key) or "").strip():
            log(f"配置自检：{key} 是空的")
            bad += 1
    for label, p in (("记忆", memory.path()), ("别名", aliases.path())):
        d = Path(p).parent
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log(f"配置自检：{label}文件目录不可写（{d}）：{e!r}")
            bad += 1
    labels_path = BASE_DIR / "stickers" / "labels_preview.json"
    try:
        labels = json.load(open(labels_path, encoding="utf-8"))
        names = {str((v or {}).get("label") or "")
                 for v in labels.values()} if isinstance(labels, dict) else set()
        blocked = set(names) & _BLOCKED_LABELS
        if blocked:
            log("配置自检：贴纸词表里还有禁用标签：" + "、".join(sorted(blocked)))
            bad += 1
    except Exception as e:
        log(f"配置自检：贴纸词表读不了（{labels_path}）：{e!r}")
        bad += 1
    if bad == 0:
        log("配置自检：通过")
    else:
        log(f"配置自检：发现 {bad} 个问题（能启动，但相关功能可能不正常）")
    return bad


def handle_message(msg: dict, round_extra: str = "",
                   round_msgs: list = None) -> None:
    if is_self_message(msg):
        return


    chat = msg.get("username") or group_username




    limit = float(CFG.get("max_reply_age_seconds", 90) or 0)
    ct = msg.get("create_time")
    if limit > 0 and ct:
        age = time.time() - float(ct)
        if age > limit:
            log(f"{_chat_tag(chat)}跳过处理：消息已过去 {int(age)} 秒，话题过去了")
            return

    mtype = msg.get("type")
    content = str(msg.get("content") or "").strip()

    if mtype == "系统消息":
        return

    wxid = sender_wxid(msg)


    nick = prompt_nick(display_name(wxid, chat))
    label = None
    quote = None

    if mtype == "文本":
        content = re.sub(r"^(wxid_[A-Za-z0-9]+):\s*", "", content, count=1)
        if not content:
            return
        if content in SKIP_MARKERS or any(content.startswith(m) for m in SKIP_MARKERS):
            return
        m_suggest = re.match(r"^#集思广益[\s：:]*", content.strip())
        if m_suggest:
            _handle_suggestion(content.strip()[m_suggest.end():], chat,
                               nick, wxid)
            return
        if content.strip() in ("#候选黑话", "#黑话候选"):

            if wxid in OWNER_WXIDS:
                log(f"{_chat_tag(chat)}收到管理员命令：候选黑话（私聊结果）")
                threading.Thread(target=_slang_report, args=(OWNER_CHAT,),
                                 daemon=True).start()
                if not _is_private_chat(chat):
                    send_reply("候选黑话发你私聊了", quote=None, chat=chat,
                               allow_split=False)
            else:
                log(f"{_chat_tag(chat)}非管理员用了管理命令，已忽略：{nick}")
            return
        if content.strip() in ("#帮助", "#状态", "#自检", "#查看叫法",
                               "#语音状态", "#菜单"):
            if wxid not in OWNER_WXIDS:
                log(f"{_chat_tag(chat)}非管理员用了管理命令，已忽略：{nick}")
                return
            cmd = content.strip()
            if cmd == "#帮助":
                msg = _owner_help_text()
            elif cmd == "#状态":
                msg = _owner_status_text(chat)
            elif cmd == "#自检":
                msg = _owner_selfcheck_text(chat)
            elif cmd == "#语音状态":
                msg = _owner_voice_text(chat)
            elif cmd == "#菜单":
                msg = _owner_menu_text()
            else:
                msg = _owner_alias_text(chat)
            send_reply(msg, quote=None, chat=chat, allow_split=False)
            log(f"{_chat_tag(chat)}管理员命令 {cmd}")
            return
        if content.strip().startswith("#查看记忆"):
            if wxid not in OWNER_WXIDS:
                log(f"{_chat_tag(chat)}非管理员用了管理命令，已忽略：{nick}")
                return
            arg = content.strip()[len("#查看记忆"):].strip()
            if not arg:
                send_reply("用法：#查看记忆 名字", quote=None, chat=chat,
                           allow_split=False)
                return
            rows = _memory_find(chat, arg)
            if not rows:
                send_reply(f"没找到「{arg}」的记忆", quote=None, chat=chat,
                           allow_split=False)
                return
            lines = []
            for r in rows[:8]:
                who = r.get("name") or r.get("wxid") or "?"
                facts = "；".join(r.get("facts") or []) or "(空)"
                lines.append(f"[{r.get('group') or '?'}] {who}：{facts}")
            send_reply("｜".join(lines), quote=None, chat=OWNER_CHAT,
                       allow_split=False)
            send_reply("记忆发你私聊了", quote=None, chat=chat,
                       allow_split=False)
            log(f"{_chat_tag(chat)}管理员命令 #查看记忆：{arg} -> {len(rows)} 人")
            return
        if content.strip().startswith("#记住"):
            if wxid not in OWNER_WXIDS:
                log(f"{_chat_tag(chat)}非管理员用了管理命令，已忽略：{nick}")
                return
            rest = content.strip()[len("#记住"):].strip()
            parts = rest.split(None, 1)
            if len(parts) < 2:
                send_reply("用法：#记住 名字 内容", quote=None, chat=chat,
                           allow_split=False)
                return
            msg = _remember_manual(chat, parts[0], parts[1])
            send_reply(msg, quote=None, chat=chat, allow_split=False)
            log(f"{_chat_tag(chat)}管理员命令 #记住：{parts[0]}")
            return
        if content.strip().startswith(("#忘掉", "#忘记")):
            if wxid not in OWNER_WXIDS:
                log(f"{_chat_tag(chat)}非管理员用了管理命令，已忽略：{nick}")
                return
            head = "#忘记" if content.strip().startswith("#忘记") else "#忘掉"
            arg = content.strip()[len(head):].strip()
            if not arg:
                send_reply("用法：#忘掉 名字（#忘记 也行）", quote=None, chat=chat,
                           allow_split=False)
                return
            rows = _memory_find(chat, arg)
            n = 0
            for r in rows:
                if memory.forget(r.get("chat") or chat, r.get("wxid") or ""):
                    n += 1
            msg = (f"忘掉「{arg}」的记忆了（{n} 人）" if n
                   else f"没找到「{arg}」的记忆")
            send_reply(msg, quote=None, chat=chat, allow_split=False)
            log(f"{_chat_tag(chat)}管理员命令 #忘掉：{arg} -> {n} 人")
            return
        for cmd, action in (("#禁用称呼", "block"), ("#允许称呼", "unblock")):
            if content.strip().startswith(cmd):
                arg = content.strip()[len(cmd):].strip()
                if wxid not in OWNER_WXIDS:
                    log(f"{_chat_tag(chat)}非管理员用了管理命令，已忽略：{nick}")
                    return
                if not arg:
                    send_reply(f"用法：{cmd} 名字", quote=None, chat=chat,
                               allow_split=False)
                    return
                group = GROUP_BY_USERNAME.get(chat, "") or ""
                try:
                    if action == "block":
                        done = aliases.block(group, arg)
                        msg = (f"以后不学「{arg}」这个称呼了" if done
                               else f"「{arg}」本来就没在学")
                    else:
                        done = aliases.unblock(group, arg)
                        msg = (f"可以重新学「{arg}」了" if done
                               else f"「{arg}」没有被禁用")
                except (ValueError, OSError) as exc:
                    log("称呼管理保存失败：", repr(exc))
                    send_reply("称呼表暂时无法保存，原文件没有覆盖", quote=None,
                               chat=chat, allow_split=False)
                    return
                if done:
                    _refresh_hotwords(quiet=True)
                send_reply(msg, quote=None, chat=chat, allow_split=False)
                log(f"{_chat_tag(chat)}管理员命令 {cmd}：{arg} -> {done}")
                return
        if CFG.get("reply_only_at") and not _mentions_bot(content, chat):
            return
        log(f"{_chat_tag(chat)}[文本] {nick}（{wxid or '?'}）：{content}")

        _remember_from_text(content, nick, wxid, chat)
        if _repeat_spam(chat, wxid, content):
            log(f"{_chat_tag(chat)}同一个人短时间内重复同一句，这条不回：{content[:20]}")
            return
        if not _should_reply(content, chat, "chat", extra=round_extra):

            _history_add("user", f"{_speaker_tag(nick, wxid)}\n{nick}说：{content}",
                         chat)
            return

        quote = _round_quote_target(msg, content, chat, round_msgs)
        reply, label = ask_ai(content, nick, chat=chat, sender_id=wxid,
                              alias_extra=round_extra)

    elif mtype == "图片":
        if CFG.get("reply_only_at") and not _mentions_bot(content, chat):
            return
        log(f"{_chat_tag(chat)}[图片] {nick}（{wxid or '?'}）发来一张图片 "
            f"lid={msg.get('local_id')} seq={msg.get('sort_seq')}")
        if not _should_reply(content, chat, "image", extra=round_extra):
            _history_add("user",
                         f"{_speaker_tag(nick, wxid)}\n{nick}发来一张图片", chat)
            return
        path = download_image(msg, chat)
        if not path:

            time.sleep(1.2)
            path = download_image(msg, chat)
        if not path:
            log("图片下载失败（先在微信里点开一次该图片，再让它发同一张，通常就能取到）")
            send_reply("图片没加载出来…你再发一次嘛", quote=None, chat=chat)
            return
        try:
            reply, label = ask_ai("（我发了一张图片，看看图里是什么）", nick,
                                  image_path=path, chat=chat, sender_id=wxid,
                                  alias_extra=round_extra)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    elif mtype == "动画表情":
        if not CFG.get("reply_to_stickers", True):
            log(f"{_chat_tag(chat)}[表情包] {nick} 发了一个表情包（已配置不回）")
            return
        md5 = sticker_read.emoji_md5(str(msg.get("content") or ""))
        known = sticker_read.label_for_md5(md5) if md5 else None
        image_path = None
        if known:
            log(f"{_chat_tag(chat)}[表情包] {nick} 发了一个表情包，已认出「{known}」")
            desc = f"{nick} 发了一个表情包，内容是你也会发的那张「{known}」。"
        else:
            log(f"{_chat_tag(chat)}[表情包] {nick} 发了一个表情包（未认出具体哪张）")


            if CFG.get("recognize_sticker_image", True):
                if not _sticker_is_still_latest(msg, chat):
                    log(f"{_chat_tag(chat)}这张表情包已经被新消息盖过，"
                        f"不截图，免得把新消息或她自己的贴纸当成它")
                else:
                    image_path = _capture_sticker_image(msg, chat)
                if image_path:
                    log("已截取表情包图片，交给视觉/OCR 识别")


                    ocr = ocr_text(image_path)
                    if ocr:
                        log("表情包 OCR 识别文字：", ocr)
                        desc = (f"{nick} 发了一个表情包。表情包上识别到的文字是："
                                f"「{ocr}」，这是 OCR 从表情图里读出的原话。"
                                f"请以这段话为唯一事实基础，自然地回复并呼应它。"
                                f"重要：只引用这段识别到的文字，绝对不要编造、"
                                f"添加任何不在引号里的其它文字或话来冒充表情包内容。")
                    else:
                        desc = (f"{nick} 发了一个表情包，上面没有识别到文字。"
                                f"请只看图里的画面和气氛自然地回一句；"
                                f"但不要假装表情包上写着具体文字，不要凭空编造"
                                f"引号里的原话。")
            if not image_path:
                desc = (f"{nick} 发了一个表情包，没来得及看清内容。"
                        f"请自然简单回应一句即可，不要编造表情包上的文字。")
        try:
            reply, label = ask_ai(desc, nick, image_path=image_path, chat=chat,
                                  sender_id=wxid, alias_extra=round_extra)
        finally:
            if image_path:
                try:
                    os.remove(image_path)
                except OSError:
                    pass


        if not (reply or "").strip():
            fallback = _sticker_fallback()
            log(f"AI 对表情包返回空内容，使用兜底回复：{fallback}")
            reply = fallback

    elif mtype in ("文件/链接/卡片", "位置", "引用"):
        info = _parse_appmsg(content) if mtype != "位置" else {}
        refer = info.get("refer")
        if refer:

            said = (info.get("title") or "").strip()
            if not said:
                log(f"{_chat_tag(chat)}[引用] {nick} 引用了一条消息但没打字，跳过")
                return
            if CFG.get("reply_only_at") and not _mentions_bot(said, chat):
                return
            log(f"{_chat_tag(chat)}[引用] {nick}（{wxid or '?'}）：{said}"
                f"  ← 引用 {refer['name']}：{refer['text'][:30]}")
            _alias_learn(chat, refer)
            _remember_from_text(said, nick, wxid, chat)
            if not _should_reply(said, chat, "quote", extra=round_extra):
                _history_add(
                    "user",
                    f"{_speaker_tag(nick, wxid)}\n{nick}说：{said}"
                    f"（他引用了 {refer['name']} 的「{refer['text'][:40]}」）",
                    chat)
                return
            prompt_text = (
                f"{said}\n\n"
                f"【他引用的消息】{refer['name']} 说：「{refer['text']}」。"
                f"他是在接着这条消息说话，请把两段连起来理解；"
                f"但只回复 {nick} 本人，不要冒充 {refer['name']} 说话。"
            )
            reply, label = ask_ai(prompt_text, nick, chat=chat, sender_id=wxid,
                                  alias_extra=round_extra)
            quote = None
        else:
            title = (info.get("title") or "").strip()
            if not title and mtype != "位置":
                log(f"{_chat_tag(chat)}[{mtype}] {nick}（{wxid or '?'}）"
                    f"发来一张卡片但标题为空，已忽略")
                return
            if CFG.get("reply_only_at") and not _mentions_bot(title, chat):
                return
            if mtype == "位置":
                desc = (f"{nick} 发来一个位置。自然回应一句就好，"
                        f"不要编造具体地点或距离。")
            else:
                kind = "链接" if info.get("type") in ("5", "49") else "卡片"
                desc = (f"{nick} 分享了一个{kind}，标题是「{title}」。"
                        f"请根据标题自然地回应一句；"
                        f"不要假装你点开看过里面的内容，"
                        f"也不要编造标题以外的细节。")
            log(f"{_chat_tag(chat)}[{mtype}] {nick}（{wxid or '?'}）：{title[:40]}")
            if not _should_reply(title, chat, "card", extra=round_extra):
                _history_add("user",
                             f"{_speaker_tag(nick, wxid)}\n{nick}分享了一条"
                             f"{mtype}：{title[:40]}", chat)
                return
            reply, label = ask_ai(desc, nick, chat=chat, sender_id=wxid,
                                  alias_extra=round_extra)
            quote = None

    elif mtype == "视频":
        if CFG.get("reply_only_at"):
            return
        meta = _video_meta(content)
        log(f"{_chat_tag(chat)}[视频] {nick}（{wxid or '?'}）发来一段视频 "
            f"{_video_time_text(meta.get('seconds'))}，"
            f"约 {meta.get('mb')} MB lid={msg.get('local_id')}")
        if not _should_reply("", chat, "video", extra=round_extra):
            _history_add("user",
                         f"{_speaker_tag(nick, wxid)}\n{nick}发来一段视频", chat)
            return
        video_path = None
        image_path = ""
        audio_text = ""
        temp_files = []
        fetched_video = False
        try:
            if CFG.get("video_fetch_enabled", True) and not _video_too_big(meta):
                video_path = download_video(msg, chat)
                if not video_path:
                    cached = _video_click_download(msg, chat, meta)
                    if cached:
                        video_path = download_video(msg, chat) or cached
            if video_path:
                fetched_video = True
                if Path(video_path).resolve().is_relative_to(VIDEO_DIR.resolve()):
                    temp_files.append(video_path)
                seconds = _video_duration(video_path) or meta.get("seconds") or 0
                frames = _video_montage(video_path, seconds)
                if frames:
                    temp_files.append(frames)
                    image_path = frames
                    audio_text = _video_audio_text(video_path, seconds)
            has_video = bool(video_path)
            has_frames = bool(image_path)
            if not image_path:
                thumb = _video_thumb_path(msg, chat)
                if thumb:
                    image_path = thumb
                    log(f"{_chat_tag(chat)}视频只取到封面，交给视觉模型")
                else:
                    log(f"{_chat_tag(chat)}视频封面和本体都没拿到")
            if not image_path:
                send_reply(_video_fail_text(meta), quote=None, chat=chat)
                return
            desc = _video_prompt(nick, meta, has_video, has_frames, audio_text)
            reply, label = ask_ai(desc, nick, image_path=image_path, chat=chat,
                                  sender_id=wxid, alias_extra=round_extra,
                                  media_label="视频")
        finally:
            for p in temp_files:
                try:
                    os.remove(p)
                except OSError as exc:
                    log("清理视频临时文件失败：", repr(exc))
            if fetched_video and CFG.get("video_delete_cache", True):
                _video_delete_cache(msg, chat)
        quote = None

    elif mtype == "语音":
        if CFG.get("reply_only_at"):
            return
        log(f"{_chat_tag(chat)}[语音] {nick}（{wxid or '?'}）发来一条语音 "
            f"lid={msg.get('local_id')} seq={msg.get('sort_seq')}")
        text = transcribe_voice(msg, chat)
        if not text:


            reason = _voice_fail.pop(
                (chat or group_username, msg.get("local_id")), "")
            send_reply(_voice_fail_text(reason), quote=None, chat=chat)
            return

        if not _should_reply(text, chat, "voice", extra=round_extra):
            _history_add("user", f"{_speaker_tag(nick, wxid)}\n{nick}说：{text}",
                         chat)
            return

        quote = (None if _is_private_chat(chat) else
                 {"seq": msg.get("sort_seq"), "content": text,
                  "create_time": msg.get("create_time"),
                  "kind": "语音"})
        reply, label = ask_ai(text, nick, chat=chat, sender_id=wxid,
                              alias_extra=round_extra)
    else:
        log(f"{_chat_tag(chat)}[{mtype}] {nick}（{wxid or '?'}）这条消息类型暂不支持，已忽略")
        return

    if reply or label:
        send_reply(reply or "", label, quote, chat=chat)
    elif mtype in ("文本", "语音", "视频", "文件/链接/卡片", "引用"):

        log("AI 未返回内容，使用兜底回复")
        send_reply("唔…我刚刚走神了，你再说一遍嘛？", quote=None, chat=chat)
    else:
        log("AI 未返回内容，跳过")


def on_message(msg: dict, _listener) -> None:



    try:
        max_age = float(CFG.get("max_msg_age_seconds", 120) or 0)
    except Exception:
        max_age = 120.0
    ct = msg.get("create_time")
    if max_age > 0 and ct:
        age = time.time() - float(ct)
        if age > max_age:
            log(f"跳过过期重投消息（{int(age)}s 前）：{str(msg.get('content'))[:20]}")
            return
    if _msg_seen(msg):
        log(f"跳过重复投递：local_id={msg.get('local_id')} "
            f"shard={msg.get('shard')} seq={msg.get('sort_seq')} "
            f"ct={msg.get('create_time')}")
        return


    _enqueue_round(msg)


def last_message_time() -> int:
    """所有托管群里最新一条消息的时间。

    只盯主群的话，主群安静、附加群正聊得热闹时也会被判定成「群里安静
    很久」，跑过去插一句，很容易接错话题。
    """
    latest = 0
    try:
        for chat in (list(GROUP_BY_USERNAME) or [group_username]):
            for row in db.get_messages(chat, limit=1):
                latest = max(latest, int(row.get("create_time") or 0))
        return latest
    except Exception as e:
        log("读取最后消息时间失败：", repr(e))
        return latest


def _in_quiet_hours(now=None) -> bool:
    """现在是不是免打扰时段（默认凌晨 1 点到早上 8 点）。

    深夜群里没人，主动冒泡只会把睡着的人吵醒；有人说话时照常回复，
    这里只管「没人说话时自己找话说」。
    """
    qh = CFG.get("quiet_hours") or []
    if not isinstance(qh, (list, tuple)) or len(qh) != 2:
        return False
    try:
        start, end = int(qh[0]), int(qh[1])
    except (TypeError, ValueError):
        return False
    if not (0 <= start <= 23 and 0 <= end <= 24) or start == end:
        return False
    hour = (now or time.localtime()).tm_hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _proactive_snapshot(chat: str, now: float = None,
                        window: float = 600) -> dict:
    """看一眼这个群最近的活跃度；只读库，不碰微信窗口。"""
    now = float(now or time.time())
    try:
        rows = db.get_messages(chat, limit=50)
    except Exception:
        rows = []
    recent = 0
    latest_user = 0
    last_self = 0
    for m in rows:
        if m.get("type") == "系统消息":
            continue
        ct = int(m.get("create_time") or 0)
        if not ct:
            continue
        if is_self_message(m, chat):
            last_self = max(last_self, ct)
            continue
        latest_user = max(latest_user, ct)
        if now - ct <= window:
            recent += 1
    return {"recent": recent, "latest_user": latest_user,
            "last_self": last_self}


def _proactive_interject_due(snap: dict, now: float, last_interject: float,
                             last_self: float, cooldown: float,
                             min_msgs: int, chance: float) -> bool:
    """群里正聊得热闹时偶尔插一句；刚说过话、冷却期没到都不插。"""
    if int(snap.get("recent") or 0) < int(min_msgs):
        return False
    if now - float(last_interject or 0) < cooldown:
        return False
    if now - float(last_self or 0) < cooldown:
        return False
    return random.random() < max(0.0, min(1.0, chance))


def _proactive_idle_due(snap: dict, now: float, next_idle: float,
                        idle_min: float) -> bool:
    """群真的安静够久，才轮到「自言自语」；刚有人说话就不打扰。"""
    if now < float(next_idle or 0):
        return False
    latest = float(snap.get("latest_user") or 0)
    return (not latest) or (now - latest >= idle_min)


def _proactive_is_silent(reply: str) -> bool:
    text = (reply or "").strip()
    return (not text) or ("[沉默]" in text) or text in (
        "沉默", "（沉默）", "(沉默)")


_PROACTIVE_RETRY_SECONDS = 300


def _proactive_send(prompt: str, chat: str) -> str:
    """发一句主动发言；返回 sent / silent / failed。"""
    reply, label = ask_ai(prompt, "【主动发言】", chat=chat, proactive=True)
    if _proactive_is_silent(reply) and not label:
        return "silent"
    if (reply or label) and send_reply(reply or "", label,
                                       allow_split=False, chat=chat):
        return "sent"
    return "failed"


def active_speaker_loop() -> None:
    if not CFG.get("proactive_enabled", True):
        log("主动发言已关闭")
        return
    check = max(1, int(CFG.get("check_interval_seconds", 10)))
    idle_min = max(0, int(CFG.get("proactive_idle_min_seconds", 3600)))
    idle_max = max(idle_min, int(CFG.get("proactive_idle_max_seconds", 7200)))
    active_window = max(60, int(
        CFG.get("proactive_active_window_seconds", 600)))
    active_min_msgs = max(2, int(
        CFG.get("proactive_active_min_messages", 3)))
    active_cooldown = max(60, int(
        CFG.get("proactive_active_cooldown_seconds", 600)))
    try:
        active_chance = float(CFG.get("proactive_active_chance", 0.05))
    except (TypeError, ValueError):
        active_chance = 0.05
    chats = list(GROUP_BY_USERNAME) or [group_username]
    started = time.time()
    state = {}
    for chat in chats:
        state[chat] = {
            "next_idle": started + random.uniform(idle_min, idle_max),
            "last_interject": started,
        }
    retry_after = 0.0
    qh = CFG.get("quiet_hours") or []
    note = f"，免打扰 {qh[0]} 点到 {qh[1]} 点不主动说话" if len(qh) == 2 else ""
    log(f"主动发言已启用：群里有聊天时偶尔接一句；安静 "
        f"{idle_min // 60}-{idle_max // 60} 分钟才随口说一句"
        f"（启动后重新计时）{note}")
    while True:
        time.sleep(check)
        now = time.time()
        if now < retry_after:
            continue
        if _in_quiet_hours():

            for st in state.values():
                st["next_idle"] = max(st["next_idle"], now + idle_min)
            continue
        for chat in (list(GROUP_BY_USERNAME) or [group_username]):
            state.setdefault(chat, {
                "next_idle": now + random.uniform(idle_min, idle_max),
                "last_interject": now})
        for chat, st in state.items():
            snap = _proactive_snapshot(chat, now, active_window)
            last_self = max(
                float(snap.get("last_self") or 0),
                float(_last_self_send_by_chat.get(chat, 0) or 0))
            if _proactive_interject_due(
                    snap, now, st["last_interject"], last_self,
                    active_cooldown, active_min_msgs, active_chance):
                log(f"{_chat_tag(chat)}群里正聊得热闹"
                    f"（{snap['recent']} 条 / {active_window // 60} 分钟），"
                    f"试着接一句")
                result = _proactive_send(
                    "群里正在聊天。你一直在旁边听着，现在想接一句就自然接一句。"
                    "只回一句很短的话，不要总结、不要重复别人刚说的、"
                    "不要问“在吗”；如果确实没什么好接的，就只回复：[沉默]",
                    chat)
                if result == "silent":
                    st["last_interject"] = now
                    log("她看了一眼，没接")
                    continue
                if result == "sent":
                    st["last_interject"] = time.time()
                    st["next_idle"] = time.time() + random.uniform(
                        idle_min, idle_max)
                    break
                retry_after = time.time() + _PROACTIVE_RETRY_SECONDS
                log("主动发言没发出去，5 分钟后再试")
                break
            if _proactive_idle_due(snap, now, st["next_idle"], idle_min):
                log(f"{_chat_tag(chat)}群安静 "
                    f"{int((now - (snap.get('latest_user') or now)) / 60)} 分钟，"
                    f"随口说一句")
                result = _proactive_send(
                    "群里已经安静很久了。你随口说一句生活化的话，"
                    "像在群里自言自语；不要刻意活跃气氛，不要问“有人在吗”，"
                    "只回一句短话。",
                    chat)
                if result == "sent":
                    st["next_idle"] = time.time() + random.uniform(
                        idle_min, idle_max)
                    break
                if result == "silent":
                    st["next_idle"] = time.time() + max(300, idle_min)
                    log("主动发言没内容，稍后再试")
                    break
                if result == "failed":
                    retry_after = time.time() + _PROACTIVE_RETRY_SECONDS
                    log("主动发言没发出去，5 分钟后再试")
                break


_BOT = {"listener": None, "lock": threading.Lock(), "stopped": False}


def _make_listener():
    """按当前所有群新建监听器；重启也走这里，别漏掉附加群。"""
    l = Listener(db, interval=1.0)
    for uid in _listener_chats():
        l.add_listener(uid, on_message)
    l.start()
    return l


def _listener_alive(l) -> bool:
    t = getattr(l, "_thread", None)
    return bool(t is not None and t.is_alive())


def _restart_listener(reason: str) -> None:
    """监听线程没了/卡住了：停掉重建，别让 bot 静默地再也收不到消息。"""
    with _BOT["lock"]:
        if _BOT.get("stopped"):
            return
        old = _BOT.get("listener")
        try:
            if old is not None:
                old.stop()
        except Exception as e:
            log("停止旧监听异常：", repr(e))
        _BOT["listener"] = _make_listener()
    log("监听已重建：", reason)


def _listener_stalled() -> bool:
    """库里已有新消息，但监听的水位和在途都没推进 → 轮询线程卡死。

    必须逐个群看：只盯主群的话，附加群卡住时主群可能还有消息在动，
    会一直被误判成“正常”。
    """
    l = _BOT.get("listener")
    if l is None:
        return False
    try:
        wm = getattr(l, "_watermark", {}) or {}
        inflight = getattr(l, "_inflight", {}) or {}
        for chat in _listener_chats():
            rows = db.get_messages(chat, limit=5)
            latest = max([int(m.get("sort_seq") or 0) for m in rows] or [0])
            seen = max(int(wm.get(chat, 0) or 0), int(inflight.get(chat, 0) or 0))
            if latest > seen:
                return True
    except Exception:
        return False
    return False


def _health_loop() -> None:
    """看门狗：监听死了/卡住了就重建；微信不见了就报警；每 10 分钟打心跳。"""
    last_beat = time.time()
    last_wechat_warn = 0.0
    stalled_since = 0.0
    while not _BOT.get("stopped"):
        time.sleep(20)
        if _BOT.get("stopped"):
            break
        try:
            l = _BOT.get("listener")
            if l is None or not _listener_alive(l):
                _restart_listener("监听线程已退出")
                stalled_since = 0.0
                continue
            if _listener_stalled():
                if not stalled_since:
                    stalled_since = time.time()
                elif time.time() - stalled_since >= 90:
                    _restart_listener("监听连续 90 秒没有推进水位")
                    stalled_since = 0.0
            else:
                stalled_since = 0.0

            now = time.time()
            if now - last_beat >= 600:
                last_beat = now
                try:


                    latest = 0
                    for _chat in _listener_chats():
                        _rows = db.get_messages(_chat, limit=5)
                        latest = max([latest] + [int(m.get("create_time") or 0)
                                                 for m in _rows])
                    ago = int(now - latest) if latest else -1
                except Exception:
                    ago = -1
                log(f"心跳：监听正常，微信主窗 {len(_wechat_main_hwnds())} 个，"
                    f"最后消息 {ago} 秒前")

                wm_now = getattr(l, "_watermark", {}) or {}
                inflight_now = getattr(l, "_inflight", {}) or {}
                for _chat in _listener_chats():
                    try:
                        _rows = db.get_messages(_chat, limit=5)
                        _latest = max([int(m.get("sort_seq") or 0)
                                       for m in _rows] or [0])
                    except Exception:
                        _latest = 0
                    _seen = max(int(wm_now.get(_chat, 0) or 0),
                                int(inflight_now.get(_chat, 0) or 0))
                    log("  监听：%s 水位 %d / 最新 %d（落后 %d）" % (
                        _chat_display(_chat), _seen, _latest,
                        max(0, _latest - _seen)))
            if not _wechat_main_hwnds() and now - last_wechat_warn >= 300:
                last_wechat_warn = now
                log("警告：找不到微信主窗口（微信退出/重启中），恢复前发送会失败")
        except Exception as e:
            log("健康检查异常：", repr(e))


_instance_mutex = None


def _owner_target_ok(database) -> bool:
    remark = str(CFG.get("owner_remark") or "")
    try:
        return bool(database.search_contact(remark))
    except Exception:
        return False


def _selfcheck_model() -> str:
    """真发一次最小请求：能通才说明 key 和网络都没问题。"""
    try:
        resp = requests.post(
            CFG["base_url"] + "/chat/completions",
            headers={"Authorization": f"Bearer {CFG['api_key']}",
                     "Content-Type": "application/json"},
            json={"model": CFG["model"],
                  "messages": [{"role": "user", "content": "ping"}],
                  "max_tokens": 1, "temperature": 0},
            timeout=12,
        )
    except Exception as e:
        return "不通（%s）" % type(e).__name__
    if resp.status_code == 200:
        return "正常"
    if resp.status_code in (401, 403):
        return "key 不对（HTTP %d）" % resp.status_code
    return "HTTP %d" % resp.status_code


def _selfcheck_db() -> str:
    if db is None:
        return "未连接"
    try:
        info = db.get_self_info() or {}
    except Exception as e:
        return "读取失败（%s）" % type(e).__name__
    return "正常（%s）" % (info.get("nick_name") or info.get("username") or "?")


def _owner_selfcheck_text(chat: str) -> str:
    """一条命令把关键链路都过一遍，出问题远程就能看出来。"""
    try:
        hwnds = _wechat_main_hwnds()
    except Exception:
        hwnds = []
    wechat = ("正常（%d 个窗口）" % len(hwnds)) if hwnds else "找不到微信主窗口"
    listen = "正常" if _listener_alive(_BOT.get("listener")) else "异常"
    asr_txt = ("正常（%s）" % asr.MODEL_NAME) if asr.available() else "不可用"
    ocr_txt = "已关闭"
    if CFG.get("enable_ocr", True):
        ocr_txt = "就绪" if _ocr_reader is not None else "初始化中/不可用"
    vision = "正常"
    left = int(float(_vision_state.get("skip_until", 0) or 0) - time.time())
    if left > 0:
        vision = "冷却 %d 分钟" % (left // 60 + 1)
    lines = [
        "自检：",
        "微信窗口 " + wechat,
        "数据库 " + _selfcheck_db(),
        "模型接口 " + _selfcheck_model(),
        "监听 " + listen,
        "语音转文字 " + asr_txt,
        "OCR " + ocr_txt,
        "视觉 " + vision,
        "待发队列 %d" % len(_pending_sends),
        _watchdog_status(),
    ]
    return "\n".join(lines)


def _roster_wxid(chat: str, name: str) -> str:
    """按显示名找 wxid：先群成员表，再记忆里的名字。"""
    name = str(name or "").strip()
    if not name:
        return ""
    if name in ("我", "自己") and _is_private_chat(chat):
        return chat
    members = GROUP_MEMBERS.get(chat) or {}
    for wid, nm in members.items():
        if str(nm or "").strip() == name:
            return wid
    for wid, nm in members.items():
        if name in str(nm or ""):
            return wid
    rows = _memory_find(chat, name)
    for row in rows:
        if str(row.get("name") or "").strip() == name and row.get("wxid"):
            return str(row["wxid"])
    for row in rows:
        if row.get("wxid"):
            return str(row["wxid"])
    return ""


def _remember_manual(chat: str, name: str, fact: str) -> str:
    """管理员手动记一条；返回给管理员看的回执。"""
    name = str(name or "").strip()
    fact = str(fact or "").strip()
    if not name or not fact:
        return "用法：#记住 名字 内容"
    wid = _roster_wxid(chat, name)
    if not wid:
        return f"没找到「{name}」这个人，先确认一下名字"
    if "：" not in fact and ":" not in fact:
        fact = "备注：" + fact
    group = GROUP_BY_USERNAME.get(chat, "") or PRIVATE_CHATS.get(chat, "") or ""
    try:
        added = memory.remember(chat, wid, name=name, facts=[fact], group=group)
    except Exception as e:
        log("手动记忆失败：", repr(e))
        return "记不下来（写入失败），看日志"
    return (f"记住「{name}」：{fact}" if added
            else f"「{name}」已经记过这条了")


SUGGEST_PATH = DATA_DIR / "suggestions.json"
_suggest_lock = threading.Lock()
_suggest_recent = {}


def _owner_menu_text() -> str:
    return ("可选功能：聊天（颜文字）、看图、看视频、听语音、表情包、"
            "联网搜索、群友记忆、称呼/黑话、主动发言。"
            "管理员命令：#自检 #状态 #语音状态 #查看叫法 #查看记忆 名字 "
            "#记住 名字 内容 #忘掉 名字 #禁用称呼 名字 #允许称呼 名字 "
            "#候选黑话 #菜单 #帮助。"
            "群里谁都能用 #集思广益 提建议，我会私信告诉你。")


def _load_suggestions() -> list:
    try:
        data = json.loads(SUGGEST_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _suggestion_text(raw: str) -> str:
    text = re.sub(r"\s+", " ", str(raw or "")).strip(" ：:，,。.！!")
    if text.startswith("希望"):
        text = text[2:].lstrip(" ：:")
    return text[:200]


def _save_suggestion(chat: str, nick: str, wxid: str, text: str) -> dict:
    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": round(time.time(), 1),
        "group": (GROUP_BY_USERNAME.get(chat, "")
                  or ("私聊" if chat == OWNER_CHAT else "")),
        "chat": chat or "",
        "wxid": wxid or "",
        "name": nick or "群友",
        "text": text,
    }
    with _suggest_lock:
        rows = _load_suggestions()
        rows.append(entry)
        if len(rows) > 500:
            rows = rows[-500:]
        try:
            SUGGEST_PATH.parent.mkdir(parents=True, exist_ok=True)
            SUGGEST_PATH.write_text(
                json.dumps(rows, ensure_ascii=False, indent=1),
                encoding="utf-8")
        except OSError as e:
            log("建议落盘失败：", repr(e))
    return entry


def _suggestion_report(entry: dict) -> str:
    name = entry.get("name") or "群友"
    text = entry.get("text") or ""
    where = entry.get("group") or "私聊"
    return f"{name}希望{text}（来自「{where}」）"


def _suggestion_should_dm(wxid: str, text: str) -> bool:
    """同一个人 10 分钟内提同一件事，只私信一次，别刷管理员。"""
    key = (str(wxid or "").lower(), text)
    now = time.time()
    if now - float(_suggest_recent.get(key, 0) or 0) < 600:
        return False
    _suggest_recent[key] = now
    if len(_suggest_recent) > 300:
        for k, ts in list(_suggest_recent.items()):
            if now - float(ts or 0) > 3600:
                _suggest_recent.pop(k, None)
    return True


def _handle_suggestion(raw: str, chat: str, nick: str, wxid: str) -> None:
    """#集思广益：群里回一句收到，私信管理员汇报；建议落盘备查。"""
    text = _suggestion_text(raw)
    if not text:
        send_reply("用法：#集思广益 你想加的功能", quote=None, chat=chat,
                   allow_split=False)
        return
    entry = _save_suggestion(chat, nick, wxid, text)
    send_reply("收到啦~", quote=None, chat=chat, allow_split=False)
    log(f"{_chat_tag(chat)}集思广益：{nick}：{text}")
    if wxid in OWNER_WXIDS:
        return
    if not _suggestion_should_dm(wxid, text):
        log("这条建议 10 分钟内私信过，不重复发")
        return
    ok = send_reply(_suggestion_report(entry), quote=None, chat=OWNER_CHAT,
                    allow_split=False)
    log(f"建议已私信管理员：{'成功' if ok else '失败'}")


def _owner_help_text() -> str:

    return ("管理员命令：#自检、#状态、#查看叫法、#语音状态、"
            "#查看记忆 名字、#记住 名字 内容、#忘掉 名字（#忘记 也行）、"
            "#禁用称呼 名字、#允许称呼 名字、#候选黑话（发你私聊）、#帮助")


def _watchdog_status() -> str:
    state = "未知"
    try:
        import subprocess
        r = subprocess.run(
            ["schtasks", "/query", "/tn", "chatbot-bot-watchdog",
             "/fo", "LIST"],
            capture_output=True, text=True, encoding="mbcs",
            errors="replace", timeout=5)
        out = (r.stdout or "") + (r.stderr or "")
        low = out.lower()
        if "running" in low or "正在运行" in out:
            state = "运行中"
        elif "ready" in low or "就绪" in out:
            state = "已就绪"
        elif "disabled" in low or "已禁用" in out:
            state = "已禁用"
        elif r.returncode != 0:
            state = "未安装"
    except Exception:
        state = "未知"
    last_start = last_exit = ""
    try:
        env_dir = os.environ.get("CHATBOT_LOG_DIR")
        log_dir = Path(env_dir) if env_dir else (BASE_DIR / "logs")
        for line in (log_dir / "watchdog.log").read_text(
                encoding="utf-8", errors="replace").splitlines():
            if "starting bot.py" in line:
                last_start = line
            elif "bot exited with code" in line:
                last_exit = line
    except Exception:
        pass
    parts = [f"看门狗 {state}"]
    if last_start:
        parts.append("上次启动 " + last_start.strip())
    if last_exit:
        parts.append("上次退出 " + last_exit.strip())
    return "｜".join(parts)


def _owner_status_text(chat: str) -> str:
    up = max(0, int(time.time() - START_TS))
    h, m = up // 3600, (up % 3600) // 60
    listen = _listener_alive(_BOT.get("listener"))
    last = last_message_time()
    if last:
        age = max(0, int(time.time() - last))
        if age < 60:
            last_txt = "%d 秒前" % age
        else:
            last_txt = "%d 分钟前" % (age // 60)
    else:
        last_txt = "还没有"
    queue = len(_pending_sends)
    left = int(float(_vision_state.get("skip_until", 0) or 0) - time.time())
    vision = ("冷却 %d 分钟" % (left // 60 + 1)) if left > 0 else "正常"
    extra = f"＋{len(PRIVATE_CHATS)} 私聊" if PRIVATE_CHATS else ""
    return (f"运行 {h}h{m:02d}m｜监听 {len(GROUP_BY_USERNAME)} 群{extra}"
            f"（{'正常' if listen else '异常'}）｜最后消息 {last_txt}"
            f"｜待发 {queue}｜视觉 {vision}｜{_watchdog_status()}")


def _owner_alias_text(chat: str) -> str:
    group = GROUP_BY_USERNAME.get(chat, "") or ""
    amap = aliases.auto_map(group)
    bots = aliases.bot_names(group)
    blocked = aliases.blocked(group)
    parts = []
    if amap:
        items = []
        for alias, wid in list(amap.items())[:15]:
            who = display_name(wid, chat) or wid
            items.append(f"{alias}→{who}")
        parts.append("学到的称呼：" + "、".join(items))
    else:
        parts.append("学到的称呼：暂无")
    if bots:
        parts.append("也叫你：" + "、".join(bots[:5]))
    if blocked:
        parts.append("禁学：" + "、".join(blocked[:10]))
    return "｜".join(parts)


def _owner_voice_text(chat: str) -> str:
    """列出本群最近几条语音的落盘状态，方便判断为什么听不到。"""
    if md is None:
        return "语音库没连上"
    try:
        rows = md.list_voice_status(chat or group_username, limit=200) or []
    except Exception as e:
        return f"查语音状态失败：{e!r}"
    if not rows:
        return "这个会话还没有语音"
    reason_map = {
        "ok": "有音频",
        "audio_not_downloaded": "没落盘",
        "audio_missing_from_media_db": "库里有缺失",
        "session_not_in_media_index": "会话不在索引",
        "no_server_id": "没有 server id",
        "empty_blob": "音频为空",
    }
    lines = []
    for r in rows[:8]:
        try:
            ts = time.strftime("%m-%d %H:%M",
                               time.localtime(float(r.get("create_time") or 0)))
        except (TypeError, ValueError, OSError):
            ts = "?"
        state = reason_map.get(str(r.get("reason") or ""),
                               str(r.get("reason") or "未知"))
        lines.append(f"{ts} lid={r.get('local_id')} "
                     f"{state}({int(r.get('bytes') or 0)}B)")
    return "最近语音：" + "；".join(lines)


def _memory_find(chat: str, keyword: str) -> list:
    group = GROUP_BY_USERNAME.get(chat, "") or PRIVATE_CHATS.get(chat, "") or ""
    kw = str(keyword or "").strip().lower()
    if not kw:
        return []
    out = []
    for row in memory.people():
        row_group = str(row.get("group") or "")
        row_chat = str(row.get("chat") or "")
        if group and row_group and row_group != group and row_chat != chat:
            continue
        hay = " ".join(str(row.get(k) or "") for k in
                       ("name", "wxid", "group", "chat")).lower()
        if kw in hay or any(kw in f.lower() for f in row.get("facts") or []):
            out.append(row)
    return out


def _resolve_group_uid(database, name: str, ids: dict = None):
    """先按群名找；找不到再按固定 chatroom id 兜底。返回 (uid, 依据)。"""
    try:
        hits = database.search_contact(name) or []
    except Exception:
        hits = []
    if hits:
        return (hits[0].get("username") or ""), "name"
    uid = str((ids or {}).get(name) or "").strip()
    if not uid:
        return "", ""
    try:
        members = database.get_group_members(uid)
    except Exception:
        members = None
    if members:
        return uid, "id"
    return "", ""


def single_instance_guard() -> None:
    """同一个微信账号只允许一个 bot.py 进程，否则一条消息会被回复两次。"""
    global _instance_mutex
    import ctypes
    kernel32 = ctypes.windll.kernel32
    _instance_mutex = kernel32.CreateMutexW(
        None, False, "Global\\chatbot-bot-single-instance")
    if kernel32.GetLastError() == 183:
        raise SystemExit("已经有一个 bot.py 在运行，请先关掉那个窗口再启动。")


def main() -> None:
    global db, md, self_wxid, group_username


    try:
        ctypes.windll.kernel32.SetConsoleTitleW("chatbot-bot")
    except Exception:
        pass


    if not CFG.get("api_key") or CFG["api_key"] == "YOUR_API_KEY":
        raise SystemExit("请设置 CHATBOT_API_KEY 或 config.json 的 api_key")
    if not CFG.get("base_url") or ".invalid" in CFG["base_url"]:
        raise SystemExit("请配置模型服务的 base_url，示例地址不可连接")
    if not CFG.get("model") or CFG["model"] == "YOUR_MODEL":
        raise SystemExit("请配置模型名称或 CHATBOT_MODEL")
    if not CFG.get("group_name"):
        raise SystemExit("请配置 group_name；示例不预设任何真实群或联系人")
    aliases.set_persist(True)
    single_instance_guard()
    _install_crash_hooks()
    if CFG.get("prevent_sleep", True):
        if _prevent_sleep(True):
            log("已阻止系统睡眠（屏幕仍可关闭；prevent_sleep=false 可关闭）")
    else:
        log("防睡眠未启用（prevent_sleep=false）")
    log(f"Chatbot 微信机器人启动（pid={os.getpid()}）")
    if CFG.get("enable_vision", True):
        vision_info = f"视觉模型：{CFG.get('vision_model', 'YOUR_MODEL')}（失败退回 OCR）"
    else:
        vision_info = "视觉模型：未启用（图片走 OCR）"
    log(f"接口：{CFG['base_url']}（密钥不写入日志）")
    log(f"文字模型：{CFG['model']}  |  {vision_info}")
    _config_selfcheck()


    try:
        from wechatauto.param import WxParam
        verify_timeout = float(CFG.get("send_verify_timeout", 15) or 15)
        verify_timeout = max(5.0, min(verify_timeout, 30.0))
        WxParam.DB_VERIFY_TIMEOUT = verify_timeout
        log(f"发送确认窗口：{verify_timeout:g}s")
    except Exception as e:
        log("设置发送确认窗口失败：", repr(e))
    _patch_gui_db_fallback()
    if not CFG.get("asr_enabled", True):
        log("语音转文字：未启用（config.json 的 asr_enabled=false）")
    elif not asr.available():
        log("语音转文字：不可用（D 盘 models/faster-whisper 下没有模型）")
    else:
        log(f"语音转文字：faster-whisper {asr.MODEL_NAME}"
            f"（{asr.model_path()}）")
    if not SYSTEM_PROMPT:
        raise SystemExit("prompt.txt 为空")

    db = WeChatDB()
    info = db.get_self_info()
    self_wxid = info.get("username") or ""
    log(f"当前账号：{info.get('nick_name') or self_wxid}（{self_wxid}）")
    _wx_audio_unmute_stale()
    _cleanup_video_temps()
    if CFG.get("slang_push_enabled", False) and not _owner_target_ok(db):
        log(f"警告：找不到微信备注「{CFG.get('owner_remark') or ''}」，"
            "候选黑话周报可能发不出去")

    names = [CFG["group_name"]]
    for extra in (CFG.get("extra_group_names") or []):
        extra = (extra or "").strip()
        if extra and extra not in names:
            names.append(extra)
    GROUP_BY_USERNAME.clear()
    group_ids = CFG.get("group_ids") or {}
    for name in names:
        uid, how = _resolve_group_uid(db, name, group_ids)
        if not uid:
            if name == CFG["group_name"]:
                raise SystemExit(
                    f"未找到群聊：{name}（请改成微信里显示的群名，"
                    "或在 config.json 的 group_ids 里填 chatroom id）")
            log(f"警告：找不到群聊「{name}」，已跳过（检查 config.json 的 extra_group_names / group_ids）")
            continue
        if how == "id":
            log(f"警告：按群名找不到「{name}」，已用固定 id {uid}"
                "（群可能改名了；建议顺手更新 group_name）")
        GROUP_BY_USERNAME[uid] = name
        if name == CFG["group_name"]:
            group_username = uid
    if not group_username:
        raise SystemExit("主群没有解析到会话 username，无法启动")

    PRIVATE_CHATS.clear()
    for uid in (CFG.get("private_chat_wxids") or []):
        uid = str(uid or "").strip()
        if not uid or uid in GROUP_BY_USERNAME:
            continue
        name = ""
        if uid in OWNER_WXIDS:
            name = str(CFG.get("owner_remark") or "")
        if not name:
            name = (CFG.get("nickname_map") or {}).get(uid) or uid
        PRIVATE_CHATS[uid] = name
        log(f"监听私聊：{name}（{uid}）")
    if PRIVATE_CHATS and not _owner_target_ok(db):
        log(f"警告：私聊监听的目标「{CFG.get('owner_remark') or ''}」"
            "在微信里找不到，回复可能发不出去")
    for uid, name in GROUP_BY_USERNAME.items():
        if uid == group_username:
            log(f"监听群：{name}（{uid}）")
        else:
            log(f"附加监听群：{name}（{uid}）")

    for uid, name in GROUP_BY_USERNAME.items():
        n = _load_group_members(uid)
        log(f"群成员昵称表：{name} {n} 人（查不到名字的退回微信昵称）")

    for uid in GROUP_BY_USERNAME:
        threading.Thread(target=_learn_from_history, args=(uid,),
                         daemon=True).start()



    _refresh_hotwords()

    md = MediaDownloader(db, save_dir=str(IMAGE_DIR))

    if CFG.get("enable_ocr", True):
        threading.Thread(target=get_ocr, daemon=True).start()


    if CFG.get("asr_enabled", True) and asr.available():
        threading.Thread(target=asr.warmup, daemon=True).start()


    threading.Thread(target=_backup_loop, daemon=True).start()


    _pending_load()
    threading.Thread(target=_pending_loop, daemon=True).start()


    if CFG.get("slang_push_enabled", False):
        wd = int(CFG.get("slang_push_weekday", 5)) % 7
        log(f"候选黑话定时推送：每周{'一二三四五六日'[wd]} "
            f"{int(CFG.get('slang_push_hour', 18)):02d}:"
            f"{int(CFG.get('slang_push_minute', 0)):02d} "
            f"发到「{CFG.get('owner_remark') or ''}」")
        threading.Thread(target=_slang_push_loop, daemon=True).start()
    else:
        log("候选黑话定时推送：未启用")

    _BOT["listener"] = _make_listener()
    private_note = f" + {len(PRIVATE_CHATS)} 个私聊" if PRIVATE_CHATS else ""
    log(f"监听已启动（{len(GROUP_BY_USERNAME)} 个群{private_note}，Ctrl+C 停止）")

    threading.Thread(target=active_speaker_loop, daemon=True).start()
    threading.Thread(target=_health_loop, daemon=True).start()
    log("看门狗已启动：每 20 秒检查监听，卡住 90 秒自动重建，10 分钟一次心跳")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("收到 Ctrl+C，正在停止...")
    finally:
        _BOT["stopped"] = True
        try:
            if _BOT.get("listener") is not None:
                _BOT["listener"].stop()
        except Exception as e:
            log("停止监听异常：", repr(e))
        _prevent_sleep(False)
        log("已停止")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log("进程未捕获异常：\n" + traceback.format_exc())
        raise
