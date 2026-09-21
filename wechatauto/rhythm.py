"""拟人节奏与写操作节流。

微信风控看到的不是「点了哪里」，而是**动作的时间分布**：每次都相同的间隔、
光标瞬时传送、永远命中同一个像素、逐字 20ms 的匀速键入、几秒内连发多条 ——
这些是人手做不出来的签名。2026-09-21 本机账号触发过一次风控（随后被要求重新
登录），此后所有驱动真实微信界面的测试都必须按真人节奏走。

本模块管三件事：

1. :func:`nap` —— 给等待时间加抖动。**只加长、不缩短**，所以原来靠固定
   ``time.sleep()`` 撑住的渲染稳定性不会被破坏；
2. :func:`move_to` / :func:`point` —— 光标按曲线走过去、落点取控件内部随机点，
   不再每次 teleport 到同一个像素；:func:`type_gap` / :func:`key_hold` 让键入
   不再是匀速；
3. :func:`gate` —— 两次「对外可见的写动作」（发消息、发文件、点赞、评论、
   撤回、通话）之间的最小间隔 + 滚动窗口内的突发上限。

节流状态落盘到 ``~/.wechatauto/rhythm.json``，所以**跨进程也生效**：一个演示
脚本一个新 Python 进程，纯内存限速对这种用法等于没有。

调档：``rhythm.set_profile('natural')``（默认）/ ``'calm'`` / ``'fast'`` /
``'off'``，或环境变量 ``WECHATAUTO_RHYTHM=calm``。``off`` 精确还原加这层之前的行为（nap 倍率 1.0、无节流、光标直接传送、按键
取原来的固定停顿值），仅用于对照实验。

已知边界：跨进程是「最后写入者赢」，两个进程同时 gate 时可能都少等一点；单进
程内的测试脚本不受影响。要更强的一致得换命名互斥量，本层故意没做。
"""

from __future__ import annotations

import ctypes
import json
import os
import random
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

from wechatauto.logger import wxlog

STATE_DIR = os.path.join(os.path.expanduser('~'), '.wechatauto')
STATE_FILE = os.path.join(STATE_DIR, 'rhythm.json')


@dataclass(frozen=True)
class Profile:
    """一套节奏参数。数值区间都写成 ``(下限, 上限)``，运行时按均匀分布取。"""

    name: str
    nap: Tuple[float, float] = (1.0, 1.0)        # 等待时间倍率（下限须 >=1.0）
    gap: Tuple[float, float] = (0.0, 0.0)        # 两次写动作的最小间隔
    burst: int = 0                               # 窗口内写动作上限（<=0 不限）
    window: float = 120.0                        # 突发统计窗口
    cooloff: Tuple[float, float] = (0.0, 0.0)    # 撞到上限后的冷却
    steps: Tuple[int, int] = (0, 0)              # 光标插值步数（0 = 传送）
    type_gap: Tuple[float, float] = (0.04, 0.04)  # 字间停顿
    key_hold: Tuple[float, float] = (0.05, 0.05)  # 按键按下~抬起


PROFILES: Dict[str, Profile] = {
    # 日常测试用这一档：节奏松散、写动作之间至少隔几秒。
    'natural': Profile('natural', (1.0, 1.45), (2.5, 6.0), 6, 120.0,
                       (30.0, 75.0), (6, 14), (0.05, 0.16), (0.05, 0.12)),
    # 长时间挂机 / 面向真人会话时的保守档。
    'calm': Profile('calm', (1.1, 1.8), (6.0, 14.0), 3, 300.0,
                    (60.0, 150.0), (9, 20), (0.09, 0.28), (0.06, 0.16)),
    # 录屏赶时间用：仍然非恒定，但贴近原来的速度。
    'fast': Profile('fast', (1.0, 1.15), (0.6, 1.4), 20, 120.0,
                    (4.0, 9.0), (3, 6), (0.04, 0.08), (0.04, 0.08)),
    # 对照实验 / 复现这层之前的行为（下限即原取值，所以不会比原来更快）。
    'off': Profile('off'),
}

_rng = random.Random()
_lock = threading.RLock()
_profile: Optional[Profile] = None
_last_write = 0.0
_stamps: List[float] = []
_loaded = False


_env_over: Dict[str, object] = {}    # 环境变量带来的单项覆盖，切档时重新套用


def _from_env(default: Profile) -> Profile:
    p = default
    raw = (os.environ.get('WECHATAUTO_RHYTHM') or '').strip().lower()
    if raw:
        if raw in PROFILES:
            p = PROFILES[raw]
        else:
            wxlog.warning(f'WECHATAUTO_RHYTHM={raw!r} 不是已知档位，'
                          f'按 {p.name} 执行')
    try:
        g = float(os.environ['WECHATAUTO_WRITE_GAP'])
        _env_over['gap'] = (g, g * 2.0)
        p = replace(p, gap=_env_over['gap'])
    except (KeyError, ValueError):
        pass
    try:
        b = int(os.environ['WECHATAUTO_WRITE_BURST'])
        _env_over['burst'] = b
        p = replace(p, burst=b)
    except (KeyError, ValueError):
        pass
    return p


def _apply_over(base: Profile) -> Profile:
    for field, value in _env_over.items():
        base = replace(base, **{field: value})
    return base


def profile() -> Profile:
    global _profile
    if _profile is None:
        _profile = _from_env(PROFILES['natural'])
    return _profile


def set_profile(name: str) -> Profile:
    """切换节奏档位。环境变量给的单项覆盖在切档后仍然有效。"""
    global _profile
    if name not in PROFILES:
        wxlog.warning(f'未知节奏档位 {name!r}，保持 {profile().name}')
        return profile()
    base = _apply_over(PROFILES[name])
    with _lock:
        _profile = base
    wxlog.info(f'拟人节奏档位：{base.name}')
    return base


def configure(**kw) -> Profile:
    """按字段覆盖当前档位（如 ``configure(gap=(1.0, 2.0))``），用于测试与脚本。"""
    global _profile
    with _lock:
        _profile = replace(profile(), **kw)
    return _profile


# ---------------------------------------------------------------------------
# 1) 抖动等待
# ---------------------------------------------------------------------------

def spread(lo: float, hi: float) -> float:
    """区间内取一个随机数。用于「这个数本来就该变」的地方（落点、停留）。

    ``off`` 档返回下限，即精确还原加这层之前的取值。
    """
    if hi <= lo or profile().name == 'off':
        return lo
    return _rng.uniform(lo, hi)


def nap(base: float) -> float:
    """睡掉 ``base`` 的抖动版本并返回实际秒数。

    倍率下限固定为 1.0：这层只让等待更**长**。各处已有的 ``time.sleep(x)`` 都
    是在等微信把画面渲染完，缩短就是拿稳定性换抖动，不划算。
    """
    lo, hi = profile().nap
    actual = base * _rng.uniform(max(1.0, lo), max(1.0, hi))
    if actual > 0:
        time.sleep(actual)
    return actual


def type_gap() -> float:
    """逐字键入时一个字之后的停顿（已 sleep，返回值供断言/日志）。"""
    lo, hi = profile().type_gap
    d = _rng.uniform(lo, hi)
    time.sleep(d)
    return d


def key_hold() -> float:
    """一次按键按下到抬起之间的 hold。"""
    lo, hi = profile().key_hold
    d = _rng.uniform(lo, hi)
    time.sleep(d)
    return d


# ---------------------------------------------------------------------------
# 2) 光标轨迹与落点
# ---------------------------------------------------------------------------

def point(rect: Sequence[float], inset: float = 0.15) -> Tuple[int, int]:
    """取控件矩形内部的一个随机点：避开中心，也避开四边 ``inset`` 比例。

    人手几乎不可能两次点到同一个像素，更不可能每次都精确点到控件正中；
    而「永远命中 BoundingRectangle 中心」正是脚本最好认的特征。
    """
    left, top, right, bottom = (int(v) for v in rect[:4])
    w, h = right - left, bottom - top
    if w <= 2 or h <= 2:
        return (left + w // 2, top + h // 2)
    if profile().name == 'off':
        return ((left + right) // 2, (top + bottom) // 2)
    ix, iy = w * inset, h * inset
    x0, x1 = left + ix, right - ix
    y0, y1 = top + iy, bottom - iy
    return (int(_rng.uniform(x0, x1)), int(_rng.uniform(y0, y1)))


def move_to(user32, x: int, y: int,
            start: Optional[Tuple[int, int]] = None) -> int:
    """把光标**走**到 ``(x, y)``（二次贝塞尔 + 随机弓形 + 末段减速）。

    ``SetCursorPos`` 一步到位在事件流里没有停留过程，人手有。控件本身不变时
    这里只是多花几十毫秒；``steps`` 为 0（off 档）时退回直接传送。

    ``start`` 显式给出时不读真实光标（调用方已知起点，或测试要固定起点）。
    返回实际走的步数（直达 = 1）。
    """
    if start is None:
        pt = wintypes.POINT()
        try:
            user32.GetCursorPos(ctypes.byref(pt))
            sx, sy = int(pt.x), int(pt.y)
        except Exception:
            sx, sy = x, y
    else:
        sx, sy = int(start[0]), int(start[1])
    lo, hi = profile().steps
    dist = max(abs(x - sx), abs(y - sy))
    if hi <= 0 or dist < 8:
        user32.SetCursorPos(x, y)
        return 1
    n = _rng.randint(max(1, lo), max(1, hi))
    # 垂直于起终点方向的随机弓形，正负随机 = 有时从上方绕、有时从下方绕。
    dx, dy = x - sx, y - sy
    sign = 1.0 if _rng.random() < 0.5 else -1.0
    bow = dist * _rng.uniform(0.06, 0.20) * sign
    mag = (dx * dx + dy * dy) ** 0.5 or 1.0
    cx = (sx + x) / 2.0 + (-dy / mag) * bow
    cy = (sy + y) / 2.0 + (dx / mag) * bow
    for i in range(1, n + 1):
        t = i / n
        t = t * t * (3.0 - 2.0 * t)          # smoothstep：起步慢、中段快、落点慢
        px = int((1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t * t * x)
        py = int((1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t * t * y)
        user32.SetCursorPos(px, py)
        time.sleep(_rng.uniform(0.004, 0.014))
    user32.SetCursorPos(x, y)
    return n + 1


# ---------------------------------------------------------------------------
# 3) 写动作节流
# ---------------------------------------------------------------------------

def _load_locked() -> None:
    """把盘上的节流状态读进内存；时钟回拨/陈旧数据一律丢弃。"""
    global _last_write, _stamps, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        now = time.time()
        last = float(data.get('last', 0.0))
        _last_write = last if 0 <= now - last <= 86400 else 0.0
        win = profile().window
        _stamps = [float(s) for s in data.get('stamps', [])
                   if isinstance(s, (int, float)) and 0 <= now - float(s) <= max(win, 60)]
    except Exception:
        _last_write, _stamps = 0.0, []


def _save_locked() -> None:
    try:
        if not os.path.isdir(STATE_DIR):
            os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATE_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'last': _last_write, 'stamps': _stamps[-32:],
                       'profile': profile().name}, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        wxlog.debug(f'节流状态写盘失败（不影响本次动作）：{e}')


def gate(kind: str = 'write') -> float:
    """对外可见的写动作**之前**调用；该等多久就等多久，返回实际等待秒数。

    只管写动作：读数据库、截图、OCR、定位控件都不节流（那些是本机行为，
    不构成骚扰，也不改变账号对外的表现）。
    """
    global _last_write, _stamps
    p = profile()
    if p.gap[1] <= 0 and p.burst <= 0:
        return 0.0
    with _lock:
        _load_locked()
        waited = 0.0
        need = _rng.uniform(*p.gap)
        ahead = need - (time.time() - _last_write)
        if ahead > 0:
            time.sleep(ahead)
            waited += ahead
        now = time.time()
        _stamps = [s for s in _stamps if 0 <= now - s <= p.window]
        if p.burst > 0 and len(_stamps) >= p.burst:
            cool = _rng.uniform(*p.cooloff)
            if cool > 0:
                wxlog.warning(f'拟人节流：{p.window:.0f}s 内已写 {len(_stamps)} 次'
                              f'（{kind}），冷却 {cool:.0f}s 再继续')
                time.sleep(cool)
                waited += cool
                _stamps = []
        _last_write = time.time()
        _stamps.append(_last_write)
        _save_locked()
        if waited >= 1.0:
            wxlog.info(f'拟人节流：{kind} 前等待 {waited:.1f}s')
        return waited


def snapshot() -> dict:
    """当前节奏状态（排查用，也给自测断言）。"""
    with _lock:
        _load_locked()
        now = time.time()
        p = profile()
        return {
            'profile': p.name,
            'gap': list(p.gap),
            'burst': p.burst,
            'window': p.window,
            'since_last': None if not _last_write else round(now - _last_write, 3),
            'recent_writes': sum(1 for s in _stamps if 0 <= now - s <= p.window),
        }


def reset() -> None:
    """清空节流状态（内存 + 盘上）。测试之间要互不污染时用。"""
    global _last_write, _stamps, _loaded
    with _lock:
        _last_write, _stamps = 0.0, []
        _loaded = True
        try:
            if os.path.isfile(STATE_FILE):
                os.remove(STATE_FILE)
        except Exception:
            pass


__all__ = ['Profile', 'PROFILES', 'profile', 'set_profile', 'configure',
           'nap', 'spread', 'type_gap', 'key_hold', 'point', 'move_to', 'gate',
           'snapshot', 'reset', 'STATE_FILE']
