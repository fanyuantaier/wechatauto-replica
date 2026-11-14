# -*- coding: utf-8 -*-
"""群里的语音消息转文字（本地 faster-whisper，模型离线加载）。

链路：微信 media_*.db 里的 SILK -> pysilk 解码成 PCM(24k/单声道)
      -> numpy 线性重采样到 16k -> faster-whisper(small, int8, CPU) 识别。

两个坑写在这里，免得以后又踩：

1. ``faster_whisper.audio.decode_audio`` 调 ``av.open(..., metadata_errors=...)``，
   而本机装的 av 19.0.2 已经不认这个参数，直接 TypeError。所以这里**永远不把
   文件路径交给它**，自己解出 float32 数组再喂进去。
2. 模型必须离线加载：``download_root`` 指到 D 盘的 models 目录，并且
   ``local_files_only=True``。模型缺失时返回 None，绝不偷偷联网下载。
"""

from __future__ import annotations

import glob
import os
import re
import threading
import time

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "models", "faster-whisper")
MODEL_NAME = "small"
TARGET_RATE = 16000
SILK_RATE = 24000
_SILK_HEAD = b"#!SILK_V3"


HOTWORDS = ""


_hotwords = HOTWORDS


def set_hotwords(names=None) -> str:
    """把群成员称呼并进热词，返回新的热词串。传空则恢复默认。"""
    global _hotwords
    seen = set()
    out = []
    for w in re.split(r"[、,，\s]+", HOTWORDS) + list(names or []):
        w = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]", "", (w or ""))
        w = re.sub(r"[（）()\[\]【】<>《》]", "", w).strip()
        w = w.strip("。.，,、！!？?：:；;~～…-—_ ")
        if not w or w in seen or len(w) > 12:
            continue
        if not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", w):
            continue
        seen.add(w)
        out.append(w)
    text = "、".join(out)
    if len(text) > 240:
        text = text[:240].rstrip("、")
    _hotwords = text or HOTWORDS
    return _hotwords


def hotwords() -> str:
    return _hotwords


VAD_FILTER = True


VAD_MIN_SECONDS = 10.0


MAX_SECONDS = 30.0

_log = print


def set_logger(fn) -> None:
    """让 asr 的日志跟 bot 走同一个出口（日志文件里能看到加载/识别耗时）。"""
    global _log
    if fn is not None:
        _log = fn


_lock = threading.Lock()
_model = None
_model_error = None
_loaded_name = None


def model_path(name: str = None) -> str:
    """找本地已下好的模型快照目录；找不到返回 None。

    huggingface 的缓存结构是 ``models--<repo>/snapshots/<hash>/model.bin``，
    这里按目录深扫一遍，不依赖具体的哈希名字。
    """
    name = name or MODEL_NAME
    root = MODEL_DIR
    pattern = os.path.join(root, "models--*--faster-whisper-%s" % name,
                           "snapshots", "*", "model.bin")
    hits = glob.glob(pattern)
    if not hits:
        return None
    hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return os.path.dirname(hits[0])


def available(name: str = None) -> bool:
    """模型是否就绪（只看文件在不在，不加载）。"""
    return model_path(name) is not None


class _Model:
    """给 bot.py 用的轻量开关：enable(False) 后 transcribe 直接返回 None。"""

    enabled = True


state = _Model()


def _get_model(name: str = None):
    """懒加载 faster-whisper，失败只记一次错，之后直接返回 None。"""
    global _model, _model_error, _loaded_name
    name = name or MODEL_NAME
    if _model is not None and _loaded_name == name:
        return _model
    if _model_error is not None and _loaded_name == name:
        return None
    path = model_path(name)
    if not path:
        _loaded_name = name
        _model_error = "模型没下载：%s" % os.path.join(
            MODEL_DIR, "models--*--faster-whisper-%s" % name)
        return None
    try:
        t0 = time.time()
        from faster_whisper import WhisperModel
        m = WhisperModel(path, device="cpu", compute_type="int8",
                         local_files_only=True)
        _model = m
        _loaded_name = name
        _model_error = None
        _log("[asr] whisper-%s 就绪（%.1fs）" % (name, time.time() - t0))
        return m
    except Exception as e:
        _model = None
        _loaded_name = name
        _model_error = repr(e)
        _log("[asr] 加载模型失败：%s" % _model_error)
        return None


def is_silk(data: bytes) -> bool:
    """微信语音是不是 SILK（文件前面可能有个 0x02 前缀）。"""
    head = data[:16]
    return _SILK_HEAD in head


def silk_to_audio(data: bytes):
    """SILK 二进制 -> 16k 单声道 float32 数组（-1..1）。解不开返回 None。"""
    if not data or not is_silk(data):
        return None
    try:
        import numpy as np
        import pysilk
        pcm = pysilk.decode(data, False, sample_rate=SILK_RATE)
    except Exception as e:
        _log("[asr] SILK 解码失败：%r" % (e,))
        return None
    return pcm_to_audio(pcm)


def pcm_to_audio(pcm: bytes):
    """24k 单声道 int16 PCM -> 16k float32。"""
    if not pcm:
        return None
    import numpy as np
    src = np.frombuffer(pcm, dtype="<i2")
    if src.size < 2:
        return None
    n = int(src.size * TARGET_RATE / SILK_RATE)
    if n < 2:
        return None

    idx = np.linspace(0, src.size - 1, n)
    res = np.interp(idx, np.arange(src.size), src.astype(np.float32))
    return (res / 32768.0).astype(np.float32)


def _clean(text: str) -> str:
    """去掉 whisper 爱加的空格和标点噪声，保留中文原话。"""
    text = (text or "").strip()
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip(" ，,。、")


def transcribe_audio(audio, name: str = None) -> str:
    """float32 数组 -> 文字。模型不可用/识别为空时返回 ''。"""
    if audio is None or not state.enabled:
        return ""
    model = _get_model(name)
    if model is None:
        return ""
    try:
        with _lock:
            t0 = time.time()
            seconds = len(audio) / float(TARGET_RATE)
            if seconds > MAX_SECONDS:
                _log("[asr] 语音 %.0f 秒，只转写前 %.0f 秒"
                     % (seconds, MAX_SECONDS))
                audio = audio[:int(MAX_SECONDS * TARGET_RATE)]
                seconds = MAX_SECONDS
            vad = VAD_FILTER and seconds > VAD_MIN_SECONDS
            segments, _info = model.transcribe(
                audio, language="zh", beam_size=1, vad_filter=vad,
                condition_on_previous_text=False,
                initial_prompt=_hotwords)
            text = "".join(s.text for s in segments)
            _log("[asr] 识别 %.1fs：%s" % (time.time() - t0, text.strip()))
    except Exception as e:
        _log("[asr] 识别失败：%r" % (e,))
        return ""
    return _clean(text)


def transcribe_file(path: str, name: str = None) -> str:
    """读一个 .silk 文件并转写。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return ""
    return transcribe_audio(silk_to_audio(data), name)


def transcribe(data: bytes, name: str = None) -> str:
    """SILK 原始字节 -> 文字（bot.py 直接用这个）。"""
    return transcribe_audio(silk_to_audio(data), name)


def warmup() -> None:
    """后台预热：第一次语音来时不用等 20 秒加载模型。"""
    try:
        _get_model()
    except Exception:
        pass


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if not args or args[0] in ("-c", "--check", "check"):
        print("模型目录：%s" % model_path())
        print("可用：%s" % available())
        print("热词：%s" % _hotwords)
        m = _get_model()
        print("加载：%s" % ("OK" if m is not None else _model_error))
        sys.exit(0 if m is not None else 1)
    for p in args:
        if not os.path.isfile(p):
            print("%s：文件不存在" % p)
            continue
        text = transcribe_file(p)
        print("%s -> %r" % (os.path.basename(p), text))
