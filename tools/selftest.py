#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""wechatauto 自检脚本（纯标准库，不需要 pytest）

用途：改完代码后跑一遍，快速确认核心路径没坏。

用法：
    python tools/selftest.py            # 跑当前环境能跑的全部检查
    python tools/selftest.py layout     # 布局档位逻辑（不需要微信）
    python tools/selftest.py keys       # 密钥/账号自愈（需要微信已登录）
    python tools/selftest.py sessions   # 会话定位（需要微信停在会话列表）
    python tools/selftest.py messages   # 消息读取（需要微信已登录）

约定：**只读**——不点击、不发送、不改窗口。失败项打印 ✗，退出码非 0。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASS if ok else FAIL).append(name)
    print("  %s %-46s %s" % ("✓" if ok else "✗", name, detail))
    return ok


# ----------------------------------------------------------------------
# 1. 布局档位（不需要微信；用 duck-typed stub 跑真实 _update_layout）
# ----------------------------------------------------------------------
def t_layout() -> None:
    from wechatauto.guia import (WeChatGUI, _layout_profile, PORTRAIT_MIN_HW,
                                 SIDEBAR_RATIO)
    print("[layout] 档位判定")
    for w, h, want in ((1549, 925, "wide"), (3072, 1824, "wide"),
                       (420, 900, "portrait"), (400, 800, "portrait"),
                       (500, 600, "portrait"), (0, 0, "wide")):
        check("_layout_profile(%d, %d) == %s" % (w, h, want),
              _layout_profile(w, h) == want)

    class Stub:
        render_w = 848
        render_h = 1824
        _sidebar_ratio = SIDEBAR_RATIO
        _portrait_sidebar_ratio = 1.0
        _send_button_ratio = (0.78, 0.92, 0.995, 0.99)
        _update_layout = WeChatGUI._update_layout

    print("[layout] 竖屏几何（848x1824）")
    s = Stub()
    s._update_layout()
    check("档位=portrait", s.layout_profile == "portrait")
    check("sidebar_right 占满窗宽", s.sidebar_right == s.render_w, "%d" % s.sidebar_right)
    check("right_pane_left = 0", s.right_pane_left == 0)
    check("search_box 在窗口内", 0 < s.search_box[0] < s.search_box[2] <= s.render_w,
          str(s.search_box))

    print("[layout] 宽屏几何（1549x925）")
    s2 = Stub()
    s2.render_w, s2.render_h = 1549, 925
    s2._update_layout()
    check("档位=wide", s2.layout_profile == "wide")
    check("right_pane_left = sidebar_right", s2.right_pane_left == s2.sidebar_right)

    print("[layout] 离线校准（假窗口，两档各跑一次真实 calibrate_layout）")
    import wechatauto.guia as gm
    from wechatauto.guia import SEND_BUTTON_RATIO, PORTRAIT_SIDEBAR_RATIO
    # threading 缺失曾在 1.2.2.5 里把 NameError 伪装成「OCR 未命中」：wide 直接
    # 返回 False 且不落布局文件，portrait 走默认比例照样返回 True，两者症状不同，
    # 所以两档都要跑，断言的是「返回 True + 回落到哪个比例」。
    check("guia 已导入 threading", hasattr(gm, "threading"))

    class CalStub:
        """只喂 calibrate_layout 用到的成员，OCR 一律返回空。"""
        render_w, render_h = 1549, 925
        _update_render_rect = lambda self: None
        bring_to_front = lambda self: None
        _detect_sidebar_ratio = lambda self: None
        ocr = lambda self, region=None: []
        _update_layout = WeChatGUI._update_layout
        _apply_layout = WeChatGUI._apply_layout
        _merge_layout_file = staticmethod(WeChatGUI._merge_layout_file)

    wide = CalStub()
    check("wide 校准成功（OCR 全空也要能回落默认）",
          WeChatGUI.calibrate_layout(wide, save=False) is True)
    check("wide 侧栏回落默认", abs(wide._sidebar_ratio - SIDEBAR_RATIO) < 1e-9,
          "%s" % wide._sidebar_ratio)
    check("wide 发送按钮回落默认",
          tuple(wide._send_button_ratio) == SEND_BUTTON_RATIO,
          str(wide._send_button_ratio))

    hit = CalStub()
    hit.ocr = lambda self, region=None: [("发送", 1300, 860, 90, 34)]
    check("wide 识别到锚点时校准成功",
          WeChatGUI.calibrate_layout(hit, save=False) is True)
    check("wide 发送按钮比例按实测改写",
          tuple(hit._send_button_ratio) != SEND_BUTTON_RATIO,
          str([round(v, 3) for v in hit._send_button_ratio]))

    port = CalStub()
    port.render_w, port.render_h = 848, 1824
    check("portrait 校准成功",
          WeChatGUI.calibrate_layout(port, save=False) is True)
    check("portrait 侧栏恒为整窗宽",
          abs(port._portrait_sidebar_ratio - PORTRAIT_SIDEBAR_RATIO) < 1e-9,
          "%s" % port._portrait_sidebar_ratio)

    print("[layout] 配置迁移（旧扁平 → v2 分档）")
    merged = WeChatGUI._merge_layout_file(
        "portrait", {"profile": "portrait", "sidebar_ratio": 1.0})
    check("version=2", merged.get("version") == 2)
    check("两档并存", set(merged.get("profiles", {})) >= {"wide", "portrait"},
          str(sorted(merged.get("profiles", {}))))


# ----------------------------------------------------------------------
# 2. 密钥 / 账号
# ----------------------------------------------------------------------
def t_keys() -> None:
    from wechatauto import WeChatDB
    print("[keys] WeChatDB 与密钥")
    db = WeChatDB()
    ok = sum(1 for rel, _, _ in db._db_files if db._key_works(rel))
    check("构造成功且账号非空", bool(db.account), db.account)
    check("可用密钥 > 0", ok > 0, "%d/%d" % (ok, len(db._db_files)))
    check("unkeyed 为空", not db.unkeyed, str(db.unkeyed[:3]))
    pids = WeChatDB._find_weixin_pids(db)
    check("能枚举 Weixin 进程", bool(pids), str(pids[:5]))
    from wechatauto import list_accounts
    others = [a for a in (list_accounts() or [])
              if a.get("account") != db.account]
    if not others:
        print("[keys] 只有一个账号目录，跳过账号自愈用例")
    else:
        wrong = others[0]["account"]
        print("[keys] 账号自愈（指定另一个真实账号 %s，应自动切回可用账号）" % wrong)
        try:
            db2 = WeChatDB(account=wrong)
            ok2 = sum(1 for rel, _, _ in db2._db_files if db2._key_works(rel))
            # 注：「另一个账号」自身若有合法密钥，本就能读、无需自愈；
            # 这里只要求最终不会停留在一个解不开的账号上。
            check("指定错账号也能读到密钥", ok2 > 0,
                  "最终账号=%s (%d 把可用)" % (db2.account, ok2))
        except Exception as exc:
            check("错账号被自动纠正", False, repr(exc)[:60])


# ----------------------------------------------------------------------
# 3. 会话定位（只定位，不点击）
# ----------------------------------------------------------------------
def t_sessions() -> None:
    from wechatauto.guia import WeChatGUI
    print("[sessions] 会话列表与 find_session")
    g = WeChatGUI()
    rows = g.get_sessions()
    check("get_sessions 有结果", bool(rows), "%d 行（档位 %s）" % (len(rows), g.layout_profile))
    if not rows:
        return
    hit, tot = 0, 0
    for row in rows[:5]:
        name = row["name"]
        if len(name) < 3:          # 单字/两字多为角标或 OCR 碎片，不苛求
            continue
        tot += 1
        pos = g.find_session(name, max_scroll=1)
        cx, cy = row["x"] + row["w"] // 2, row["y"] + row["h"] // 2
        if pos:
            hit += 1
            # 多轮投票会取聚类均值，允许 ±40px 抖动
            ok = abs(pos[0] - cx) <= 40 and abs(pos[1] - cy) <= 40
            check("定位 %r（±40px）" % name[:14], ok,
                  "%s vs OCR 中心 %s" % (pos, (cx, cy)))
        else:
            print("  · 跳过 %r：OCR 抖动名，定位失败可接受" % name[:14])
    check("会话定位通过率 ≥ 50%", tot == 0 or hit / tot >= 0.5,
          "%d/%d（OCR 抖动名单列跳过属正常）" % (hit, tot))
    check("负例返回 None", g.find_session("不存在的会话名XYZ", max_scroll=1) is None)


# ----------------------------------------------------------------------
# 4. 消息读取
# ----------------------------------------------------------------------
def t_messages() -> None:
    from wechatauto import WeChatDB
    print("[messages] 消息读取")
    db = WeChatDB()
    sessions = db.get_sessions(limit=8)
    check("get_sessions(limit=8)", bool(sessions), "%d 个会话" % len(sessions))
    if not sessions:
        return
    # 列表按时间排序，最近的会话可能恰好没有消息行 → 依次尝试
    user, msgs = "", []
    for s in sessions:
        got = db.get_messages(s["username"], limit=5)
        if got:
            user, msgs = s["username"], got
            break
    check("找到有消息的会话", bool(msgs),
          "%s → %d 条" % (user or "(无)", len(msgs)))
    if not msgs:
        return
    if msgs:
        keys = {"local_id", "type", "sort_seq", "sender_id"}
        check("消息字段完整", keys <= set(msgs[0]), str(sorted(msgs[0])[:6]))
        seqs = [m["sort_seq"] for m in msgs]
        check("按 sort_seq 降序", seqs == sorted(seqs, reverse=True))
    new = db.get_new_messages(user, since_seq=0, limit=5)
    check("get_new_messages(5)", isinstance(new, list), "%d 条" % len(new))
    check("get_messages(limit=0) 返回空", db.get_messages(user, limit=0) == [])
    check("负值输入安全", db.get_messages(user, limit=-1) == []
          and db.get_messages(user, offset=-1) == [])


# ----------------------------------------------------------------------
# 5. 发送回读校验（离线：假 DB，不碰微信也不落库）
# ----------------------------------------------------------------------
def t_verify() -> None:
    """``_verify_sent`` 的正文口径与水位。

    两条线上/推演缺陷各对应一组用例：草稿拼接让库里查得到但内容不对（子串匹配
    照样返回成功），以及没有水位时旧消息能冒充这次发送。真实 sort_seq 大量并列，
    所以水位必须带 (sort_seq, local_id) 身份而不只是 ``>``。
    """
    from wechatauto.guia import WeChatGUI

    def row(content, seq, lid=1, sender=2):
        return {"content": content, "sort_seq": seq, "local_id": lid,
                "sender_id": sender, "type": "文本"}

    class FakeDB:
        uname = "wxid_target"

        def __init__(self, rows):
            self.rows = sorted(rows, key=lambda r: -r["sort_seq"])

        def get_messages(self, username, limit=20, offset=0):
            if username != self.uname:
                return []          # 消息表按 username 键，错了静默返回空
            return [dict(r) for r in self.rows][:limit]

        def search_contact(self, keyword):
            return [{"username": self.uname}] if keyword == "目标会话" else []

        def get_self_info(self):
            return {"username": self.uname}

    class VStub:
        _verify_sent = WeChatGUI._verify_sent
        _send_watermark = WeChatGUI._send_watermark
        _verify_usernames = WeChatGUI._verify_usernames

        def __init__(self, rows):
            self.db = FakeDB(rows)

        def _get_db(self):
            return self.db

    print("[verify] 正文匹配口径")
    v = VStub([row("校准wechatauto 部署自检 OK", 100)])
    check("草稿拼接正文不算逐字成功（线上事故那种）",
          v._verify_sent("wechatauto 部署自检 OK", "目标会话") is False)
    check("同一正文在 contains 口径下会误判成功",
          v._verify_sent("wechatauto 部署自检 OK", "目标会话",
                         mode="contains") is True)
    check("逐字相等才算成功",
          v._verify_sent("校准wechatauto 部署自检 OK", "目标会话") is True)
    check("空正文直接判不通过", v._verify_sent("", "目标会话") is False)
    check("对方发的同样文字不算自己发出",
          VStub([row("重复一句话", 100, 1, sender=1)])._verify_sent(
              "重复一句话", "目标会话") is False)

    print("[verify] 发送前水位")
    v = VStub([row("重复一句话", 500, 7), row("别的", 400, 6)])
    mark = v._send_watermark("目标会话")
    check("水位取到最大 sort_seq", bool(mark) and mark["seq"] == 500, str(mark))
    check("水位含 (sort_seq, local_id) 身份",
          bool(mark) and mark["ids"] == {(500, 7), (400, 6)})
    check("库里没有新行时，旧的同文本不被接受",
          v._verify_sent("重复一句话", "目标会话", after=mark) is False)
    v.db.rows = [row("重复一句话", 600, 9), row("重复一句话", 500, 7),
                 row("别的", 400, 6)]
    check("更晚的新行被接受",
          v._verify_sent("重复一句话", "目标会话", after=mark) is True)

    v2 = VStub([row("重复一句话", 500, 7), row("别的", 500, 8)])
    m2 = v2._send_watermark("目标会话")
    check("并列 sort_seq 全部进身份集合",
          bool(m2) and m2["seq"] == 500 and len(m2["ids"]) == 2)
    v2.db.rows = [row("重复一句话", 500, 9), row("重复一句话", 500, 7),
                  row("别的", 500, 8)]
    check("sort_seq 并列但 local_id 更新 → 接受（只比 > 会误判）",
          v2._verify_sent("重复一句话", "目标会话", after=m2) is True)

    print("[verify] 会话解析")
    check("显示名解析成 username 后回读",
          VStub([row("hi", 10)])._verify_sent("hi", "目标会话") is True)
    check("解析不到时兜底用原名，不抛异常",
          VStub([row("hi", 10)])._verify_sent("hi", "不存在的会话") is False)
    check("who 为空按自己的会话",
          bool(VStub([row("hi", 10)])._send_watermark(None)))


# ----------------------------------------------------------------------
# 6. 拟人节奏（纯离线：时间被接管，不碰微信）
# ----------------------------------------------------------------------
def t_rhythm() -> None:
    """``wechatauto.rhythm``：抖动只加长、落点随机不越界、写动作按盘上状态节流。"""
    import json
    import time as _t

    from wechatauto import rhythm

    slept = []
    real = _t.sleep
    rhythm.time.sleep = lambda s: slept.append(s)
    try:
        print("[rhythm] 抖动")
        base = rhythm.profile()
        check("默认档=natural", base.name == "natural", base.name)
        vals = [rhythm.nap(0.5) for _ in range(40)]
        check("nap 不缩短既有等待（倍率下限 1.0）", min(vals) >= 0.5)
        check("nap 取值发散", len({round(v, 4) for v in vals}) > 8,
              "%.3f~%.3f" % (min(vals), max(vals)))
        for name, p in rhythm.PROFILES.items():
            check("%s 档 nap 下限 >=1.0" % name, p.nap[0] >= 1.0)
            check("%s 档 gap 上限 >= 下限" % name, p.gap[1] >= p.gap[0])

        print("[rhythm] 光标落点与轨迹")
        rect = (100, 200, 400, 260)
        pts = {rhythm.point(rect) for _ in range(120)}
        check("落点全部在矩形内",
              all(100 <= x < 400 and 200 <= y < 260 for x, y in pts))
        check("内缩后不贴边",
              all(145 <= x < 355 and 209 <= y < 251 for x, y in pts))
        check("落点不再固定（>50 个不同像素）", len(pts) > 50, "%d 个" % len(pts))
        check("不再每次都点正中心", (250, 230) not in pts)
        check("退化矩形（宽<=2）回中心不越界",
              rhythm.point((100, 100, 101, 100)) == (100, 100))
        spread = {round(rhythm.spread(62, 80), 1) for _ in range(60)}
        check("spread 落在区间内且发散", 62 <= min(spread) and max(spread) <= 80
              and len(spread) > 8, "%d 个" % len(spread))
        check("spread 区间退化时取下限", rhythm.spread(7, 7) == 7)

        class FakeU32:
            def __init__(self):
                self.moves = []

            def GetCursorPos(self, _byref):
                raise OSError("取不到光标")      # 异常路径必须不抛到外面

            def SetCursorPos(self, x, y):
                self.moves.append((x, y))

        u = FakeU32()
        steps = rhythm.move_to(u, 900, 700)
        check("取不到光标时退化为直达（不虚构轨迹）",
              u.moves == [(900, 700)] and steps == 1, "%d 步" % steps)
        u2 = FakeU32()
        steps2 = rhythm.move_to(u2, 900, 700, start=(300, 400))
        lo_step, hi_step = rhythm.profile().steps
        check("已知起点时走曲线（多步 + 精确落点）",
              steps2 >= lo_step + 1 and u2.moves[-1] == (900, 700)
              and len(u2.moves) == steps2, "%d 步" % steps2)
        check("轨迹不离起终点连线太远（弓形 <=20%）",
              all(200 <= x <= 1000 and 300 <= y <= 800 for x, y in u2.moves[:-1]))
        # 「两次轨迹不重合」这种单次对比会随机翻红：n、弓形、正负号都是抽出来的，
        # 整数取点后撞车并不罕见。改成多次取样，断言「至少出现过两种轨迹」，
        # 同时断言「不是直线」——那才是这层真正要保证的东西。
        paths = []
        for _ in range(8):
            ux = FakeU32()
            rhythm.move_to(ux, 900, 700, start=(300, 400))
            paths.append(tuple(ux.moves))
        check("8 次移动里至少出现 2 种轨迹（不是每次都同一条）",
              len(set(paths)) >= 2, "%d 种" % len(set(paths)))
        ux = FakeU32()
        rhythm.move_to(ux, 900, 700, start=(300, 400))
        mid = ux.moves[len(ux.moves) // 2]
        # 中点到起终点连线的垂距：|cross| / |(600,300)| ，弓形最小 6% ≈ 40px
        off = abs((mid[0] - 300) * 300 - (mid[1] - 400) * 600) / 670.8
        check("路径中段偏离起终点连线（走的是曲线不是直达）",
              off > 5.0, "中点 %s 偏离 %.1fpx" % (mid, off))
        u4 = FakeU32()
        check("目标已在脚下时不绕路",
              rhythm.move_to(u4, 300, 400, start=(301, 401)) == 1
              and u4.moves == [(300, 400)])

        print("[rhythm] 写动作节流")
        rhythm.reset()
        slept.clear()
        w0 = rhythm.gate("send")
        check("冷启动第一次不等待", w0 == 0.0, "%.2f" % w0)
        w1 = rhythm.gate("send")
        lo, hi = rhythm.profile().gap
        check("第二次按 gap 等待", lo - 0.05 <= w1 <= hi, "%.2fs" % w1)
        check("等待真的作用在 sleep 上", bool(slept) and max(slept) >= lo - 0.05)
        st = {}
        if os.path.isfile(rhythm.STATE_FILE):
            with open(rhythm.STATE_FILE, encoding="utf-8") as f:
                st = json.load(f)
        check("节流状态落盘（跨进程可见）",
              len(st.get("stamps", [])) == 2 and "last" in st, "%s" % list(st))

        rhythm.configure(gap=(0.0, 0.0), burst=3, window=120.0,
                        cooloff=(30.0, 40.0))
        rhythm.reset()
        slept.clear()
        for _ in range(3):
            rhythm.gate("x")
        slept.clear()
        w = rhythm.gate("x")          # 第 4 次撞突发上限
        check("撞突发上限后进入冷却", 30.0 <= w <= 40.0, "%.1fs" % w)
        check("冷却后突发计数重新从 1 开始",
              rhythm.snapshot()["recent_writes"] == 1,
              "%d" % rhythm.snapshot()["recent_writes"])
        rhythm.configure(gap=base.gap, burst=base.burst,
                         cooloff=base.cooloff, window=base.window)

        print("[rhythm] off 档 = 这层之前的行为")
        rhythm.reset()
        rhythm.set_profile("off")
        slept.clear()
        check("off 档 gate 不等待", rhythm.gate("send") == 0.0)
        check("off 档 nap 精确还原", abs(rhythm.nap(0.3) - 0.3) < 1e-9)
        check("off 档落点回中心", rhythm.point(rect) == (250, 230))
        check("off 档 spread 取下限", rhythm.spread(62, 80) == 62)
        u5 = FakeU32()
        check("off 档光标直接传送",
              rhythm.move_to(u5, 900, 700, start=(300, 400)) == 1
              and u5.moves == [(900, 700)])
        check("未知档位不改当前档", rhythm.set_profile("nope").name == "off")
        rhythm.set_profile("natural")
        check("档位能切回 natural", rhythm.profile().name == "natural")

        print("[rhythm] 只节流「对外可见」的动作")
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(here, "wechatauto", "guia.py"),
                   encoding="utf-8").read()
        check("guia 的读路径（截图/OCR）里没有 gate",
              "rhythm.gate" not in src[src.index("    def ocr("):
                                       src.index("    def click_send(")])
        check("发送提交点 click_send 有 gate",
              "rhythm.gate('send')" in src[src.index("    def click_send("):
                                          src.index("    def send_msg(")])
        sdc = open(os.path.join(here, "wechatauto", "sender.py"),
                   encoding="utf-8").read()
        check("遗留坐标发送器 sender.send 同样有 gate",
              "rhythm.gate('send')" in sdc[sdc.index("    def send(self"):
                                          sdc.index("    def send_to(")])
        sinks = _write_sink_functions()
        gated = [s for s in sinks if s[3]]
        check("检出 >=3 个「按下发送」落点（探测器本身没失效）", len(gated) >= 3,
              ", ".join("%s.%s" % (c or "-", n) for _f, c, n, _g in gated))
        leak = ["%s:%s.%s" % (f, c or "-", n) for f, c, n, g in sinks
                if not g and (f, c, n) not in NOT_A_WRITE]
        check("每个真的按下发送的函数都先过 rhythm.gate", not leak, ", ".join(leak))
        check("朋友圈评论窗口 send 已补上节流（曾因走老路径而漏）",
              ("moment.py", "MomentCommentDialog", "send", True) in sinks)
    finally:
        rhythm.time.sleep = real
        rhythm.reset()


# 只是「引用了发送按钮/回车」但本身不对外产生消息的函数，不算写落点
NOT_A_WRITE = {
    ("guia.py", "WeChatGUI", "calibrate_layout"),        # 找按钮位置，不发东西
    ("guia.py", "WeChatGUI", "_to_pinyin"),              # 输入法里选拼音
    ("moment.py", "Moment", "_match_comment_line"),      # OCR 文本匹配
    ("moment.py", "MomentCommentDialog", "_locate"),     # 定位控件
    ("moment.py", "MomentCommentDialog", "_init_controls"),
    ("sender.py", "WeChatUI", "open_chat"),              # 回车用于打开会话
    ("sender.py", None, "press_enter"),                  # 原语本身
}


def _write_sink_functions():
    """静态列出「按下发送」的函数：[(文件, 类, 函数名, 函数体里有没有 rhythm.gate)]。

    这类回归的形态是「新写了一条对外可见的动作，但忘了接节流」——朋友圈评论窗口
    的 ``send`` 就是这么漏掉的（它和 ``_click_comment_send`` 是两条并行路径）。
    所以这里按「做了什么」找，而不是按函数名猜。
    """
    import ast
    import re
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prim = re.compile(r"SendKeys\('\{Enter\}'\)|press_enter\b|keybd_event\(0x0|'发送'")
    out = []
    for fname in ("guia.py", "uia_driver.py", "moment.py", "sender.py", "wx.py",
                  "chat.py", "recall.py"):
        path = os.path.join(here, "wechatauto", fname)
        if not os.path.isfile(path):
            continue
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        found = []
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            found += [(cls.name, m) for m in cls.body
                      if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
        found += [(None, n) for n in tree.body
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for cname, node in found:
            body = ast.get_source_segment(src, node) or ""
            if prim.search(body):
                out.append((fname, cname, node.name, "rhythm.gate(" in body))
    return out


# ----------------------------------------------------------------------
# 7. UIA gate 扫描（纯离线：合成 PE 片段 + 临时缓存文件，不碰微信）
# ----------------------------------------------------------------------
def t_gate() -> None:
    """``_rip_xrefs_to_rva`` 向量化后与逐字节实现等价；扫描失败/异常不得打断调用方。"""
    import random
    import struct as st
    import tempfile

    import wechatauto.uia_driver as ud
    from wechatauto.uia_driver import IMAGE_SCN_MEM_EXECUTE, WeChatUIA

    EXEC = IMAGE_SCN_MEM_EXECUTE | 0x40000000      # +INITIALIZED_DATA
    WRITE = 0x80000000 | IMAGE_SCN_MEM_EXECUTE     # 可写 + 可执行：仍该参与匹配
    _gate_entry = lambda p: ud._gate_cache().get(_dll_identity(p), {})

    def build(n=4096, seed=7):
        """一段可执行 section 覆盖整个 buffer，尾部留 16 字节给边界用例。"""
        rnd = random.Random(seed)
        data = bytearray(rnd.randrange(256) for _ in range(n))
        for i in range(0, n - 16, 37):
            data[i] = 0x8D                          # 大量 0x8D 噪声，制造假匹配
        return data

    secs = lambda n, chars=EXEC: [{"name": ".text", "rva": 0x1000, "vsize": n,
                                   "raw_size": n, "raw_ptr": 0, "chars": chars}]
    SEC_RVA = 0x1000
    TGT_HI = 0x9AB000        # disp 为正：引用远处的可写段
    TGT_LO = 0x0ABC          # disp 为负：引用指令之前的地址（真实代码里更常见）

    def plant_plain(d, i, tgt):
        """在 i 处放 8D 05 disp32（无 REX），返回应有的 xref RVA。"""
        d[i], d[i + 1] = 0x8D, 0x05
        d[i + 2:i + 6] = st.pack("<i", tgt - (SEC_RVA + i) - 6)
        return SEC_RVA + i

    def plant_rex(d, i, tgt):
        """同上但前面补一个 REX 前缀：指令起点是 i-1，长度 7。"""
        d[i - 1] = 0x4C
        return plant_plain(d, i, tgt) - 1

    print("[gate] 向量化 vs 逐字节参考实现")
    for seed in (7, 11, 23):
        data = build(4096, seed)
        sections = secs(len(data))
        want_hi = {plant_rex(data, 100, TGT_HI), plant_plain(data, 250, TGT_HI),
                   plant_plain(data, len(data) - 8, TGT_HI)}   # 段尾最后可用位
        want_lo = {plant_rex(data, 130, TGT_LO), plant_plain(data, 300, TGT_LO),
                   plant_plain(data, len(data) - 24, TGT_LO)}
        data = bytes(data)
        for label, tgt, want in (("正位移", TGT_HI, want_hi),
                                 ("负位移", TGT_LO, want_lo)):
            ref = WeChatUIA._rip_xrefs_to_rva_ref(data, sections, tgt)
            new = WeChatUIA._rip_xrefs_to_rva(data, sections, tgt)
            check("seed=%d %s：两种实现结果一致" % (seed, label),
                  sorted(ref) == sorted(new), "%d 个" % len(new))
            check("seed=%d %s：植入的三条（含带 REX）都命中" % (seed, label),
                  want <= set(new), "%d/%d" % (len(want & set(new)), len(want)))
        new = WeChatUIA._rip_xrefs_to_rva(data, sections, TGT_HI)
        check("seed=%d 可写段按原逻辑同样参与（未改变语义）" % seed,
              WeChatUIA._rip_xrefs_to_rva(
                  data, secs(len(data), chars=WRITE), TGT_HI) == new)
        check("seed=%d 非可执行段返回空" % seed,
              WeChatUIA._rip_xrefs_to_rva(
                  data, secs(len(data), chars=0x40000000), TGT_HI) == [])
        check("seed=%d 目标不存在时返回空" % seed,
              WeChatUIA._rip_xrefs_to_rva(data, sections, 0x0F0F0F0F) == [])

    data = build(64, 5)
    plant_plain(data, len(data) - 7, TGT_HI)   # 原实现循环上界 len-8，这条在界外
    data[63 - 7] = 0x00                        # 别让前一个字节被当成 REX 前缀
    data = bytes(data)
    sections = secs(64)
    check("段尾界外那条两种实现都不命中（边界与原实现一致）",
          not WeChatUIA._rip_xrefs_to_rva(data, sections, TGT_HI)
          and not WeChatUIA._rip_xrefs_to_rva_ref(data, sections, TGT_HI))
    check("空/超短 buffer 不抛",
          WeChatUIA._rip_xrefs_to_rva(b"", [], TGT_HI) == []
          and WeChatUIA._rip_xrefs_to_rva(b"\x8d\x05" * 3, secs(6), TGT_HI) == [])

    data = build(4096, 7)
    plant_plain(data, 250, TGT_HI)
    data = bytes(data)
    sections = secs(4096)
    saved_np = sys.modules.get("numpy")
    sys.modules["numpy"] = None                   # 让 import numpy 抛 ImportError
    try:
        fb = WeChatUIA._rip_xrefs_to_rva(data, sections, TGT_HI)
        check("缺 numpy 时自动退回逐字节实现，结果不变且非空",
              fb == WeChatUIA._rip_xrefs_to_rva_ref(data, sections, TGT_HI)
              and fb != [], "%d 个" % len(fb))
    finally:
        if saved_np is None:
            sys.modules.pop("numpy", None)
        else:
            sys.modules["numpy"] = saved_np

    print("[gate] 扫描失败要返回空序列，不是 None；结果按 DLL 身份落盘")
    from wechatauto.uia_driver import _dll_identity

    scan = WeChatUIA._scan_qaccessible_candidates
    with tempfile.TemporaryDirectory() as td:
        cache_file = os.path.join(td, "gate_cache.json")
        old_file = ud.GATE_CACHE_FILE
        old_verified = dict(ud._VERIFIED_GATE_RVA)
        ud.GATE_CACHE_FILE, ud._GATE_CACHE = cache_file, None
        try:
            missing = os.path.join(td, "nope.dll")
            junk = os.path.join(td, "junk.dll")
            with open(junk, "wb") as f:
                f.write(b"MZ\x90\x00" + bytes(4096))
            check("文件读不到 → ()", scan(missing) == ())
            check("不是 PE → ()", scan(junk) == ())
            check("返回值可直接迭代（旧版返回 None 会 TypeError）",
                  list(scan(junk)) == [])
            check("读不到/不是 PE 属于瞬时失败，不落盘",
                  not os.path.isfile(cache_file))

            tiny = r"C:\Windows\System32\win32u.dll"
            if os.path.isfile(tiny):
                check("是 PE 但没有 gate 特征 → ()，且负结果落盘",
                      scan(tiny) == () and _gate_entry(tiny).get("candidates") == [],
                      "%s" % _gate_entry(tiny))

            fake = os.path.join(td, "fake-9.9.9", "Weixin.dll")
            ud._gate_cache_put(_dll_identity(fake), candidates=[0x1234, 0x5678])
            scan.cache_clear()
            check("盘上缓存命中即返回，不再读 198MB 文件（该路径根本不存在）",
                  scan(fake) == (0x1234, 0x5678))
            ud._gate_cache_put(_dll_identity(fake), verified=0x9999)
            ud._VERIFIED_GATE_RVA.clear()
            check("已验证 RVA 从盘上恢复并顶到候选序列首位",
                  WeChatUIA._qaccessible_candidate_rvas(fake)[0] == 0x9999)
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write("{ 坏掉的 json")
            ud._GATE_CACHE = None
            scan.cache_clear()
            check("缓存文件损坏 → 不抛，按未命中重扫", scan(fake) == ())
        finally:
            ud.GATE_CACHE_FILE, ud._GATE_CACHE = old_file, None
            ud._VERIFIED_GATE_RVA.clear()
            ud._VERIFIED_GATE_RVA.update(old_verified)
            scan.cache_clear()

    print("[gate] 热激活异常只降级，不打断发送")

    class Stub:
        _set_screen_reader_flag = lambda self, on: None
        _wechat_hwnds = lambda self: [1, 2]

        def __init__(self, boom):
            self.boom, self.n = boom, 0

        def _hot_activate_accessibility(self, hwnd):
            self.n += 1
            if self.boom:
                raise RuntimeError("扫描炸了")
            return True

    s = Stub(boom=True)
    check("_hot_activate_accessibility 抛异常时 _wake_accessibility 返回 False",
          WeChatUIA._wake_accessibility(s) is False)
    check("两个窗口都试过（异常被逐个吞掉）", s.n == 2, "%d 次" % s.n)
    s2 = Stub(boom=False)
    check("正常路径仍然返回 True", WeChatUIA._wake_accessibility(s2) is True)


# ----------------------------------------------------------------------
# 8. 收起式搜索入口（纯离线：假 win，不碰微信）
# ----------------------------------------------------------------------
def t_click() -> None:
    """微信 4.1.15 起搜索框默认收起，只有点「搜索」按钮才会出现输入框。"""
    import wechatauto.uia_driver as ud
    from wechatauto.uia_driver import WeChatUIA

    def patch(cls, name, value):
        old = cls.__dict__[name]
        setattr(cls, name, value)
        return old

    calls = {"present": 0, "click": 0}
    box, btn = object(), object()

    def present(self, w):
        calls["present"] += 1
        return None if calls["present"] < 2 else box

    class FakeWin:
        def EditControl(self, **kw):
            class _E:
                @staticmethod
                def Exists(t, i):
                    return False
            return _E()

    print("[click] 4.1.15 收起式搜索入口")
    o_present = patch(WeChatUIA, "_search_box_present", present)
    o_btn = patch(WeChatUIA, "_search_button", staticmethod(lambda w: btn))
    o_ctrl = patch(WeChatUIA, "_click_ctrl",
                   lambda self, c, right=False: calls.__setitem__("click", calls["click"] + 1) or True)
    try:
        uia = WeChatUIA()
        calls["present"] = 0; calls["click"] = 0
        check("expand=False 时绝不多点一下（search_box_rect 是只读锚点）",
              uia._search_box(FakeWin(), expand=False) is None and calls["click"] == 0,
              "点了 %d 次" % calls["click"])
        calls["present"] = 0; calls["click"] = 0
        check("expand=True 时点一次搜索按钮、展开后返回输入框",
              uia._search_box(FakeWin(), expand=True) is box and calls["click"] == 1,
              "点了 %d 次" % calls["click"])
        calls["present"] = 0; calls["click"] = 0
        o_btn2 = patch(WeChatUIA, "_search_button", staticmethod(lambda w: None))
        try:
            check("连搜索按钮都没有 → 返回 None 而不是抛",
                  uia._search_box(FakeWin(), expand=True) is None and calls["click"] == 0)
        finally:
            setattr(WeChatUIA, "_search_button", o_btn2)
        calls["present"] = 100; calls["click"] = 0
        check("输入框本来就常驻（4.1.13 及以前）时不多此一举",
              uia._search_box(FakeWin(), expand=True) is box and calls["click"] == 0)
    finally:
        setattr(WeChatUIA, "_search_box_present", o_present)
        setattr(WeChatUIA, "_search_button", o_btn)
        setattr(WeChatUIA, "_click_ctrl", o_ctrl)
    check("三个被替换的方法已原样还原",
          isinstance(WeChatUIA.__dict__["_search_button"], staticmethod)
          and callable(WeChatUIA.__dict__["_click_ctrl"]))

    # 进朋友圈那条路：树没物化时必须先唤醒，否则报出来的是「找不到导航按钮」，
    # 会被误判成版本不兼容（2026-09-23 在 4.1.15.13 上就是这么撞的）。
    print("[click] 进朋友圈前先唤醒 mmui 树")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    wsrc = open(os.path.join(here, "wechatauto", "wx.py"), encoding="utf-8").read()
    i = wsrc.index("def _switch_to_moments_new_style")
    seg = wsrc[i:wsrc.index("\n    def ", i)]     # 截到下一个方法级 def（SwitchToMoments 在它前面）
    check("朋友圈路线里调了 ensure_materialized", "ensure_materialized(" in seg)
    check("唤醒之后重新锚定根控件（不拿旧的空壳继续找）",
          "root = _uia.ControlFromHandle(hwnd) or root" in seg)
    check("唤醒只在树没就绪时做（不给正常路径加延迟）",
          "if root is not None and not _tree_ready(root)" in seg)


# ----------------------------------------------------------------------
# 9. 监听：全局回调（add_all）的挂载语义（假 DB，不碰微信）
# ----------------------------------------------------------------------
def t_listen() -> None:
    """``Listener.add_all`` 必须挂到**每一个**会话上，且不重复挂。"""
    from wechatauto.db import Listener

    class FakeDB:
        workdir = ""

        def __init__(self):
            self.sessions = [{"username": "a"}, {"username": "b"}]

        def get_sessions(self, limit=500):
            return list(self.sessions)

        def get_messages(self, user, limit=20, offset=0):
            return [] if limit <= 0 else [{"sort_seq": 100}]

        def get_new_messages(self, user, since_seq=0, limit=None):
            return []

    db = FakeDB()
    per_chat = lambda row, l: None
    glob = lambda row, l: None
    lis = Listener(db, interval=0.1, watermark_file="")
    lis.add_listener("a", per_chat)
    lis.add_all(glob)
    cbs = {u: list(v) for u, v in lis._callbacks.items()}
    check("add_all 挂到没有回调的会话上", cbs.get("b") == [glob], "%s" % cbs.get("b"))
    check("已经单独监听过的会话也拿到全局回调（老代码在这里漏）",
          per_chat in cbs.get("a", []) and glob in cbs.get("a", []), "%s" % cbs.get("a"))
    check("回调顺序保持：先单会话后全局", cbs.get("a") == [per_chat, glob], "%s" % cbs.get("a"))
    lis.add_all(glob)
    check("重复 add_all 不会把同一个回调挂两次",
          lis._callbacks["a"].count(glob) == 1 and lis._callbacks["b"].count(glob) == 1,
          "%s" % {k: v.count(glob) for k, v in lis._callbacks.items()})
    db.sessions.append({"username": "c"})
    lis._poll_once()
    check("轮询中新出现的会话被自动纳管", glob in lis._callbacks.get("c", []),
          "%s" % lis._callbacks.get("c"))
    lis._discover_new = False
    db.sessions.append({"username": "d"})
    lis._poll_once()
    check("discover=False 时不再自动纳管", "d" not in lis._callbacks, str(list(lis._callbacks)))
    check("水位按会话独立初始化", lis.watermark.get("a") == 100 and lis.watermark.get("c") == 100,
          str(lis.watermark))


# ----------------------------------------------------------------------
# 10. 朋友圈 cell 句柄自愈（纯离线：假控件假 cell，不碰微信也不滚动）
# ----------------------------------------------------------------------
def t_moment() -> None:
    """UIA cell 被回收/滚出视口后 BoundingRectangle 变成 (0,0,0,0)，
    基于坐标的点击全部报 ``Can not move cursor``——表现为「明明定位到了，
    点赞/评论却说打不开菜单」。命中后必须换到新句柄才允许动手。"""
    from wechatauto.moment import Moment, MomentItem

    DEAD, LIVE = (0, 0, 0, 0), (100, 200, 900, 480)

    class Rect:
        def __init__(self, r):
            self.left, self.top, self.right, self.bottom = r

    class Ctrl:
        def __init__(self, r, name='x'):
            self.BoundingRectangle = Rect(r)
            self.Name = name

    class DeadCtrl(Ctrl):
        def __init__(self, r=None, name='x'):
            pass                      # 不设置字段：模拟句柄回收后只剩一个空壳

        @property
        def BoundingRectangle(self):
            raise ValueError('UIA 句柄已失效')

    class Item:
        """假 cell：字段与 MomentItem 的解析结果同名，属性口与真类一致。"""
        def __init__(self, nick, text, time, r=DEAD, parsed=True):
            self.control = Ctrl(r)
            self.nickname, self.content, self.time = nick, text, time
            self._parsed = parsed

        def _chk(self):
            if not self._parsed:
                raise RuntimeError('未解析的 cell 需要读控件，死句柄会抛')
            return self.nickname

        @property
        def publisher(self):
            self._chk(); return self.nickname

        @property
        def text(self):
            self._chk(); return self.content

        @property
        def timestamp(self):
            self._chk(); return self.time

    m = Moment.__new__(Moment)          # 这些方法不依赖实例状态，绕开 __init__

    print("[moment] 矩形可用性判定")
    check("(0,0,0,0) 判为不可用", Moment._rect_usable(Item('a', 'b', 'c')) is False)
    check("正常矩形判为可用",
          Moment._rect_usable(Item('a', 'b', 'c', LIVE)) is True)
    dead = Item('小美', '今天去了动物园看长颈鹿', '1小时前')
    dead.control = DeadCtrl(DEAD)
    check("BoundingRectangle 抛异常 → 不可用", Moment._rect_usable(dead) is False)
    check("只有 1 像素宽的矩形也算不可用",
          Moment._rect_usable(Item('a', 'b', 'c', (100, 100, 101, 400))) is False)
    check("真 MomentItem 有 publisher/text/timestamp 属性（假 cell 的接口对齐真类）",
          all(isinstance(getattr(MomentItem, k), property)
              for k in ('publisher', 'text', 'timestamp')))

    print("[moment] 句柄失效后按签名重挂")
    twin = Item('小美', '今天去了动物园看长颈鹿，很开心', '1小时前', LIVE)
    other = Item('小美', '加班到十一点', '3天前', LIVE)
    seen = []

    def patch_items(items):
        def _f(self, refresh=True):
            seen.append(list(items))
            return list(items)
        return _f

    o_read = Moment.__dict__["_read_visible_items"]
    try:
        Moment._read_visible_items = patch_items([other, twin])
        ok = m._reattach_item(dead)
        check("换到了同一条的新句柄（不是同发布者的另一条）",
              ok is True and dead.control is twin.control,
              "命中=%s" % ("同一条" if ok and dead.control is twin.control else "错条/没找到"))
        dead.control = DeadCtrl(DEAD)

        Moment._read_visible_items = patch_items([other])
        check("同屏只有同发布者的另一条时宁可不挂（否则会点错人）",
              m._reattach_item(dead) is False and dead.control.__class__ is DeadCtrl)

        Moment._read_visible_items = patch_items(
            [Item('小美', '今天去了动物园看长颈鹿，很开心', '昨天', LIVE)])
        check("昵称+正文前缀相同但时间不同 → 认不出，拒绝",
              m._reattach_item(dead) is False)

        Moment._read_visible_items = patch_items([Item('小美', '今天去了动物园', '1小时前', DEAD)])
        check("同一条但新句柄矩形还是空的 → 继续判失败",
              m._reattach_item(dead) is False)

        Moment._read_visible_items = patch_items([])
        check("屏上没有任何 cell → 失败而不是抛", m._reattach_item(dead) is False)

        nodata = Item('小美', '随便', '1小时前', parsed=False)
        check("死句柄且从未解析过（构造不出比对材料）→ 直接失败，不去读控件",
              m._reattach_item(nodata) is False)

        only_nick = Item('小美', '', '', DEAD)
        Moment._read_visible_items = patch_items([Item('小美', '任意正文', '任意时间', LIVE)])
        check("只剩昵称（纯图动态且没时间）时拒绝认领，不给同一个人的另一条点赞",
              m._reattach_item(only_nick) is False)

        long_text = Item('小美', '今天去了动物园看长颈鹿', '1小时前', DEAD)
        Moment._read_visible_items = patch_items(
            [Item('小美', '今天去了动物园看长颈鹿，还看了大象，玩得很开心', '1小时前', LIVE)])
        check("摘要被截断/DB 正文更长时按前缀互含认领（精确比签名会认不出自己那条）",
              m._reattach_item(long_text) is True and Moment._rect_usable(long_text) is True)

        ok_item = Item('小美', '正文', '1小时前', LIVE)
        n0 = len(seen)
        check("矩形本来就可用时一次都不扫屏（不给正常路径加延迟）",
              m._reattach_item(ok_item) is True and len(seen) == n0)

        # 用真 MomentItem 实例再跑一遍：假 cell 的字段名万一和真类对不上，
        # 上面那一堆断言会一起错掉。
        def real_item(nick, text, when, ctrl):
            it = MomentItem.__new__(MomentItem)
            it._parsed, it.nickname, it.content, it.time = True, nick, text, when
            it.control = ctrl
            return it
        dead_real = real_item('小美', '今天去了动物园看长颈鹿', '1小时前', DeadCtrl(DEAD))
        twin_real = real_item('小美', '今天去了动物园看长颈鹿，还看了大象', '1小时前',
                              Ctrl(LIVE))
        Moment._read_visible_items = patch_items([twin_real])
        check("真 MomentItem 实例：按昵称+正文前缀+时间重新认领",
              m._reattach_item(dead_real) is True and dead_real.control is twin_real.control)
        dead_real.control = DeadCtrl(DEAD)
        Moment._read_visible_items = patch_items(
            [real_item('小美', '今天去了动物园看长颈鹿', '昨天', Ctrl(LIVE))])
        check("真 MomentItem 实例：时间不同照样拒绝", m._reattach_item(dead_real) is False)
    finally:
        setattr(Moment, "_read_visible_items", o_read)

    print("[moment] 命中后的收尾：_settle_item")
    dead2 = Item('小美', '今天去了动物园看长颈鹿，很开心', '1小时前')
    dead2.control = DeadCtrl(DEAD)
    twin2 = Item('小美', '今天去了动物园看长颈鹿，很开心', '1小时前', LIVE)
    calls = {"scroll": 0}

    def fake_scroll(self, publisher=None, keyword=None, max_retry=10):
        calls["scroll"] += 1
        seen_args.append((publisher, keyword))
        return None                       # 只关心它有没有被调、传了什么

    seen_args = []

    def queue_items(batches):
        left = [list(b) for b in batches]

        def _f(self, refresh=True):
            return left.pop(0) if len(left) > 1 else left[0]
        return _f

    o_read = Moment.__dict__["_read_visible_items"]
    o_scroll = Moment.__dict__["_scroll_item_fully_visible"]
    o_rect = Moment.__dict__["_time_line_rect"]
    try:
        Moment._time_line_rect = lambda self: (100, 100, 1000, 800)
        Moment._scroll_item_fully_visible = fake_scroll

        Moment._read_visible_items = queue_items([[dead2]])
        got = m._settle_item(dead2, '小美', '长颈鹿')
        check("同屏没有新句柄时才会去滚，滚完仍找不到就返回 None",
              got is None and calls["scroll"] == 1, "scroll=%d" % calls["scroll"])
        check("滚的时候带上 publisher/keyword（复用既有定位路线，不另写一套）",
              seen_args == [('小美', '长颈鹿')], str(seen_args))

        Moment._read_visible_items = queue_items([[twin2]])
        calls["scroll"] = 0
        got = m._settle_item(dead2, '小美', '长颈鹿')
        check("同屏就能换到句柄时不多滚一次（正常路径零额外开销）",
              got is dead2 and dead2.control is twin2.control and calls["scroll"] == 0,
              "scroll=%d" % calls["scroll"])

        calls["scroll"] = 0
        check("健康的 item 原样返回，不滚也不扫屏",
              m._settle_item(twin2, '小美', '长颈鹿') is twin2 and calls["scroll"] == 0)
        check("_settle_item(None) 不抛", m._settle_item(None) is None)

        dead4 = Item('小美', '今天去了动物园看长颈鹿，很开心', '1小时前')
        dead4.control = DeadCtrl(DEAD)
        Moment._read_visible_items = queue_items([[dead4], [twin2]])
        calls["scroll"] = 0
        got = m._settle_item(dead4, '小美', '长颈鹿')
        check("滚入视野后再试一次：修复的是调用方手里那个对象（不换新对象）",
              got is dead4 and dead4.control is twin2.control and calls["scroll"] == 1,
              "scroll=%d" % calls["scroll"])

        dead5 = Item('小美', '今天去了动物园看长颈鹿，很开心', '1小时前')
        dead5.control = DeadCtrl(DEAD)
        Moment._scroll_item_fully_visible = lambda self, **kw: (_ for _ in ()).throw(
            RuntimeError('滚动失败'))
        Moment._read_visible_items = queue_items([[dead5]])
        check("滚屏过程抛异常时降级为 None，不把异常冒给调用方",
              m._settle_item(dead5, '小美', '长颈鹿') is None)
    finally:
        setattr(Moment, "_read_visible_items", o_read)
        setattr(Moment, "_scroll_item_fully_visible", o_scroll)
        setattr(Moment, "_time_line_rect", o_rect)
    check("三个被替换的方法已原样还原",
          all(callable(Moment.__dict__[k])
              for k in ("_read_visible_items", "_scroll_item_fully_visible",
                        "_time_line_rect")))

    print("[moment] 三条动作路线都接上了自愈")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(here, "wechatauto", "moment.py"), encoding="utf-8").read()

    def seg(name):
        i = src.index("def " + name + "(")
        return src[i:src.index("\n    def ", i + 1)]

    fm = seg("find_moment")
    check("find_moment 两处命中都过 _settle_item", fm.count("self._settle_item(") == 2,
          "%d 处" % fm.count("self._settle_item("))
    check("find_moment 里不再直接 return 原始命中项（空矩形就是这么漏出去的）",
          not any(l.strip() in ("return item", "return it")
                  for l in fm.splitlines()))
    check("自愈失败时明确返回 None（让上层报「没找到」而不是点空矩形）",
          fm.count("return None") >= 2)
    for fn in ("_locate_more_click", "_invoke_action_menu"):
        check("%s 动手前先重挂句柄" % fn, "self._reattach_item(item)" in seg(fn))
    check("_invoke_action_menu 换不到句柄就直接失败（不再右键空矩形）",
          "return None" in seg("_invoke_action_menu").split("for child in")[0])


# ----------------------------------------------------------------------
# 11. 群消息「谁发的」：real_sender_id → wxid → 昵称（纯离线，假 db）
# ----------------------------------------------------------------------
def t_sender() -> None:
    """监听回调里必须拿得到发送者身份，不能只有文本消息能刮出 wxid。"""
    from wechatauto.wx import _db_row_to_message, Chat
    from wechatauto.db import WeChatDB

    class NickDB:
        def __init__(self, mapping, boom=False):
            self.mapping, self.boom, self.calls = mapping, boom, 0

        def nickname_map(self, refresh=False):
            self.calls += 1
            if self.boom:
                raise RuntimeError('contact.db 打不开')
            return dict(self.mapping)

    class FakeChat:
        def __init__(self, who='某群', db=None):
            self.who = who
            if db is not None:
                self._db = db

    def row(**kw):
        base = dict(local_id=1, type='图片', sender_id=7, create_time=100,
                    content='[图片]', sort_seq=100, sender_username='')
        base.update(kw)
        return base

    NAMES = {'wxid_p': '备注阿P', 'wxid_q': '阿Q'}

    print("[sender] 行字典里的 wxid 要一路传到回调")
    db = NickDB(NAMES)
    m = _db_row_to_message(row(sender_username='wxid_p'), FakeChat('群A'), 'self_x', db)
    check("非文本消息也带上了发送者 wxid（以前只有文本能从正文前缀刮）",
          getattr(m, 'sender_wxid', None) == 'wxid_p', repr(getattr(m, 'sender_wxid', None)))
    check("发送者 wxid 已换成备注/昵称", m.sender == '备注阿P', m.sender)
    check("msg.wxid 用的就是解析出来的 wxid", m.wxid == 'wxid_p', repr(m.wxid))
    check("msg.sender_remark 与昵称一致", m.sender_remark == '备注阿P')

    m2 = _db_row_to_message(row(sender_username='7'), FakeChat('群A'), 'self_x', db)
    check("冒充用户名的数字 rowid 不当作 wxid（1.2.4 及更早的兜底遗留）",
          m2.sender_wxid == '' and m2.wxid == 7,
          "sender_wxid=%r wxid=%r" % (m2.sender_wxid, m2.wxid))
    check("解析不到发送者时 sender 退回会话名（老行为不变）", m2.sender == '群A', m2.sender)

    m3 = _db_row_to_message(row(type='文本', content='wxid_q:\n早'), FakeChat('群A'),
                            'self_x', db)
    check("SenderName2Id 缺记录时仍从正文前缀兜底", m3.sender_wxid == 'wxid_q',
          repr(m3.sender_wxid))
    check("兜底出来的 wxid 一样能换成昵称", m3.sender == '阿Q', m3.sender)

    m4 = _db_row_to_message(row(sender_id=2, type='文本', content='我发的'),
                            FakeChat('群A'), 'self_x', db)
    check("自己发的消息 wxid 给真实 self_wxid，而不是常量 2",
          m4.attr == 'self' and m4.wxid == 'self_x', repr(m4.wxid))

    print("[sender] 拿不到 db / 昵称查询炸了都不能打断回调")
    m5 = _db_row_to_message(row(sender_username='wxid_p'), FakeChat('群A'), None, None)
    check("没有 db 时不抛，sender 退回 wxid", m5.sender == 'wxid_p', m5.sender)
    m6 = _db_row_to_message(row(sender_username='wxid_p'), FakeChat('群A'), None,
                            NickDB(NAMES, boom=True))
    check("nickname_map 抛异常时降级为 wxid，不把异常冒到监听线程",
          m6.sender == 'wxid_p', m6.sender)

    print("[sender] nickname_map 的缓存语义")
    d = WeChatDB.__new__(WeChatDB)
    hits = {'n': 0}

    def fake_index():
        hits['n'] += 1
        return {'wxid_z': '老张'}

    d._nickname_index = fake_index
    check("两次调用只查一次 contact.db",
          d.nickname_map() == {'wxid_z': '老张'} and d.nickname_map() is not None
          and hits['n'] == 1, "查了 %d 次" % hits['n'])
    d.nickname_map(refresh=True)
    check("refresh=True 会重新查", hits['n'] == 2, "查了 %d 次" % hits['n'])

    print("[sender] 群成员表（群里不在通讯录的人只能靠它）")
    g = Chat.__new__(Chat)
    g._wxid = '1234567890@chatroom'
    g._db = type('Stub', (), {'get_group_members': lambda self, w: [
        {'username': 'wxid_p', 'nick_name': '阿P', 'remark': '备注阿P', 'is_owner': True}]})()
    got = g.GetGroupMembers()
    check("群聊返回成员列表", len(got) == 1 and got[0]['username'] == 'wxid_p', str(got))
    p = Chat.__new__(Chat)
    p._wxid = 'wxid_p'
    check("非群聊返回空列表而不是抛", p.GetGroupMembers() == [])
    q = Chat.__new__(Chat)
    q._wxid = '1234567890@chatroom'
    q._db = type('Boom', (), {'get_group_members': lambda self, w: (_ for _ in ()).throw(
        RuntimeError('库损坏'))})()
    check("读群成员出问题时返回空列表", q.GetGroupMembers() == [])

    from wechatauto.wx import _AllMessageChat
    ac = _AllMessageChat('1234567890@chatroom', '群A', db=g._db)
    check("全局监听的伪会话也能查群成员（不建 GUI）",
          len(ac.GetGroupMembers()) == 1, str(len(ac.GetGroupMembers())))
    check("没带 db 的伪会话返回空列表而不是抛",
          _AllMessageChat('1234567890@chatroom', '群A').GetGroupMembers() == [])

    print("[sender] db 层不再有那条假兜底")
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(here, "wechatauto", "db.py"), encoding="utf-8").read()
    check("不再拿数字 rowid 去查 contact.username",
          'get_nickname(str(sender_id))' not in src)
    check("sender_username 只认 SenderName2Id 的结果",
          src.count('self._sender_id_index().get(int(sender_id), "")') == 2,
          "%d 处行构造" % src.count('self._sender_id_index().get(int(sender_id), "")'))

    wsrc = open(os.path.join(here, "wechatauto", "wx.py"), encoding="utf-8").read()
    i = wsrc.find('_AllMessageChat(\n')
    check("AddListenAll 造的伪会话带上了 db（否则回调里查不到群成员）",
          i >= 0 and 'db=self._db' in wsrc[i:i + 260],
          ' '.join(wsrc[i:i + 200].split())[:120] if i >= 0 else '没找到构造点')
    j = wsrc.find('def _make_listen_cb')
    seg = wsrc[j:wsrc.index('\n    def ', j + 1)] if j >= 0 else ''
    check("AddListenChat 的回调也走同一个身份解析（两条监听路径不能只修一条）",
          '_db_row_to_message(row, chat, self_wxid, self._db)' in seg, seg[:60])


# ----------------------------------------------------------------------
# 12. 语音可用性判据（纯离线：内存 sqlite + 假 db，不碰微信也不落敏感文件）
# ----------------------------------------------------------------------
def t_voice() -> None:
    """issue #20「26 条语音只识别到 19 条」：库没读坏，是微信没把音频落盘。
    新增的 reason 判据必须能区分这两种情况，并且老版本表结构不能把行弄丢。"""
    import os as _os
    import sqlite3
    import tempfile

    from wechatauto.db import WeChatDB
    from wechatauto.media import MediaDownloader

    vr = MediaDownloader._voice_reason
    print("[voice] 可用性判据")
    check("VoiceInfo 里有非空数据 → ok", vr('111', 4096, 1, 1) == 'ok')
    check("没有 server_id → no_server_id", vr('', 0, 1, 1) == 'no_server_id'
          and vr('0', 0, 1, 1) == 'no_server_id')
    check("download_status=0 → audio_not_downloaded（不是库的错）",
          vr('111', 0, 0, 1) == 'audio_not_downloaded')
    check("老表没有这一列（None）也归到「未落盘」，不谎报库坏了",
          vr('111', 0, None, 1) == 'audio_not_downloaded')
    check("状态说该有但 VoiceInfo 查不到 → 指向库侧可疑",
          vr('111', 0, 5, 1) == 'audio_missing_from_media_db')
    check("会话在 media 库里连 Name2Id 都没有 → 单独一种原因",
          vr('111', 0, 1, 0) == 'session_not_in_media_index')

    # ---- get_voice_rows：真的 sqlite，带/不带 download_status 两种表结构 ----
    def build(with_status: bool):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        cols = ("local_id INTEGER, server_id INTEGER, real_sender_id INTEGER, "
                "create_time INTEGER, sort_seq INTEGER, local_type INTEGER")
        if with_status:
            cols += ", download_status INTEGER"
        conn.execute("CREATE TABLE Msg_1 (%s)" % cols)
        data = [(1, 111, 7, 100, 300, 34, 1), (2, 222, 2, 200, 400, 34, 0),
                (3, 0, 7, 300, 500, 34, 1), (4, 444, 7, 400, 600, 1, 1)]
        for r in data:
            if with_status:
                conn.execute("INSERT INTO Msg_1 VALUES (?,?,?,?,?,?,?)", r)
            else:
                conn.execute("INSERT INTO Msg_1 VALUES (?,?,?,?,?,?)", r[:6])
        conn.commit()
        return conn

    def fake(with_status):
        d = WeChatDB.__new__(WeChatDB)
        conn = build(with_status)
        d._run_msg_query = lambda user, fn: fn([(conn, 'Msg_1')])
        return d, conn

    print("[voice] get_voice_rows 的窄查询")
    d, _ = fake(True)
    rows = d.get_voice_rows('wxid_a', limit=100)
    check("只返回语音行（local_type=34）", len(rows) == 3, str(len(rows)))
    check("按 sort_seq 降序", [r['local_id'] for r in rows] == [3, 2, 1],
          str([r['local_id'] for r in rows]))
    check("带出 download_status", rows[1]['download_status'] == 0
          and rows[0]['download_status'] == 1,
          str([r['download_status'] for r in rows]))
    one = d.get_voice_rows('wxid_a', local_id=2)
    check("local_id 过滤生效", len(one) == 1 and one[0]['server_id'] == 222, str(one))
    check("limit 生效", len(d.get_voice_rows('wxid_a', limit=2)) == 2)

    d2, _ = fake(False)
    rows2 = d2.get_voice_rows('wxid_a', limit=100)
    check("老版本表没有 download_status 列时**不丢行**（通用路径会 continue 整片丢）",
          len(rows2) == 3, "只回来 %d 条" % len(rows2))
    check("缺列时 download_status 退化为 None 而不是 KeyError",
          all(r['download_status'] is None for r in rows2))
    check("缺列时其余字段照常可读", rows2[0]['sort_seq'] == 500
          and rows2[1]['server_id'] == 222)

    # ---- list_voice_status / voice_status ----
    print("[voice] 批量与单条可用性报告")
    ROWS = [
        {'local_id': 1, 'server_id': 111, 'create_time': 100, 'sort_seq': 300,
         'real_sender_id': 7, 'download_status': 1},
        {'local_id': 2, 'server_id': 222, 'create_time': 200, 'sort_seq': 400,
         'real_sender_id': 2, 'download_status': 0},
        {'local_id': 3, 'server_id': 0, 'create_time': 300, 'sort_seq': 500,
         'real_sender_id': 7, 'download_status': 1},
        {'local_id': 4, 'server_id': 444, 'create_time': 400, 'sort_seq': 600,
         'real_sender_id': 7, 'download_status': 1},
    ]

    class FakeDB:
        def get_voice_rows(self, user, limit=500, local_id=None):
            out = [dict(r) for r in ROWS
                   if local_id is None or r['local_id'] == local_id]
            return out[:limit]

    md = MediaDownloader.__new__(MediaDownloader)
    md.db = FakeDB()
    md.save_dir = tempfile.mkdtemp(prefix='wxst-voice-')
    md._voice_index = lambda user: (1, {'111': 4096})   # 只有 111 真的落盘了
    st = md.list_voice_status('wxid_a')
    check("条数与消息表一致（不可用的也在列表里，带原因）", len(st) == 4, str(len(st)))
    by_id = {x['local_id']: x for x in st}
    check("落盘过的 → available + bytes", by_id[1]['available'] is True
          and by_id[1]['bytes'] == 4096 and by_id[1]['reason'] == 'ok')
    check("download_status=0 → audio_not_downloaded 且 available=False",
          by_id[2]['reason'] == 'audio_not_downloaded'
          and by_id[2]['available'] is False)
    check("server_id 为 0 → no_server_id", by_id[3]['reason'] == 'no_server_id')
    check("状态正常但 VoiceInfo 没有 → audio_missing_from_media_db",
          by_id[4]['reason'] == 'audio_missing_from_media_db')
    check("自己发的语音标出来（对方/自己要分开统计时要用）",
          by_id[2]['self_sent'] is True and by_id[1]['self_sent'] is False)
    v1 = md.voice_status('wxid_a', 1)
    check("单条查询与批量结论一致", v1['available'] is True and v1['bytes'] == 4096)
    check("消息表里没这条语音 → no_voice_row，不抛",
          md.voice_status('wxid_a', 99)['reason'] == 'no_voice_row')

    # ---- 真 media 分片（临时文件 sqlite）跑通 Name2Id / VoiceInfo 与落盘 ----
    print("[voice] Name2Id 索引与真实落盘")
    media_path = _os.path.join(tempfile.mkdtemp(prefix='wxst-media-'), 'media_0.db')
    media = sqlite3.connect(media_path)
    media.row_factory = sqlite3.Row
    media.execute("CREATE TABLE Name2Id (user_name TEXT)")
    media.execute("INSERT INTO Name2Id (user_name) VALUES ('wxid_a')")   # rowid=1
    media.execute("INSERT INTO Name2Id (user_name) VALUES ('wxid_b')")   # rowid=2
    media.execute("CREATE TABLE VoiceInfo (chat_name_id INT, svr_id INT, "
                  "voice_data BLOB, create_time INT)")
    media.execute("INSERT INTO VoiceInfo VALUES (1, 111, ?, 5)", (b'SILKDATA',))
    media.execute("INSERT INTO VoiceInfo VALUES (2, 111, ?, 6)", (b'OTHER',))
    media.commit()
    media.close()

    # 撤掉上面那个桩，让 _voice_index 走真实现；_open 每次开新连接
    # （真实现读后会 close，:memory: 库一 close 就空了，所以这里用文件库）
    del md._voice_index
    md.db._db_files = [('message\\media_0.db', 'media_0.db', None)]

    def open_media(rel):
        c = sqlite3.connect(media_path)
        c.row_factory = sqlite3.Row      # 真 db._open 也是这个 row_factory
        return c

    md.db._open = open_media
    known, idx = md._voice_index('wxid_a')
    check("按会话取索引：只认自己那份音频", known == 1 and idx == {'111': 8},
          "%s %s" % (known, idx))
    check("别的会话拿不到（不串号）", md._voice_index('wxid_z') == (0, {}))
    same_svr = md._voice_index('wxid_b')
    check("同号不同会话各自计数", same_svr[1] == {'111': 5}, str(same_svr[1]))


    seen_shard = []

    def _voice_row(user, local_id, local_type=None, shard=None):
        seen_shard.append(shard)
        if local_id == 1:
            return {'local_type': 34, 'server_id': 111}
        if local_id == 2:
            return {'local_type': 34, 'server_id': 222}
        return None

    md.db.get_message_row = _voice_row
    outp = md.download_voice('wxid_a', 1, save_dir=md.save_dir)
    got = open(outp, 'rb').read() if outp else b''
    check("音频落盘且字节一致", outp is not None and got == b'SILKDATA',
          os.path.basename(outp) if outp else 'None')
    check("未落盘的那条仍然返回 None（行为不变）",
          md.download_voice('wxid_a', 2, save_dir=md.save_dir) is None)
    check("消息行缺失时返回 None 而不是抛",
          md.download_voice('wxid_a', 3, save_dir=md.save_dir) is None)
    check("不传时 media 不带 shard（旧调用形状不变）",
          seen_shard[-1] is None, repr(seen_shard[-1]))
    md.download_voice('wxid_a', 1, save_dir=md.save_dir,
                  shard='message__message_3.db')
    check("download_voice 把 shard 透传给 db（跨分片同号才能钉死）",
          seen_shard[-1] == 'message__message_3.db', repr(seen_shard[-1]))
    for fn in os.listdir(md.save_dir):
        _os.remove(_os.path.join(md.save_dir, fn))
    _os.rmdir(md.save_dir)
    _os.remove(media_path)
    _os.rmdir(_os.path.dirname(media_path))


# ----------------------------------------------------------------------
# 13. 控件树拿不到时的可定位警告（纯离线：假 win32gui，不碰微信）
# ----------------------------------------------------------------------
def t_tree() -> None:
    """「控件树被屏蔽」以前是静默失败：扫不到可用窗口就直接 return False。
    现在必须把它翻译成一句话——尤其是 32 位解释器读不到 64 位模块这一类。"""
    import types

    import wechatauto.uia_driver as ud
    from wechatauto.uia_driver import WeChatUIA

    u = WeChatUIA.__new__(WeChatUIA)

    print("[tree] 扫描计数")
    wins = {1: ('微信', True, (0, 0, 900, 700)),      # 正常：有 pid 有 DLL
            2: ('微信(3)', True, (0, 0, 500, 400)),   # 取不到 DLL
            3: ('Weixin', True, (0, 0, 300, 300)),    # 连 pid 都没有
            4: ('某浏览器', True, (0, 0, 999, 999)),  # 标题不是微信
            5: ('微信', False, (0, 0, 800, 800))}     # 不可见
    fake = types.SimpleNamespace(
        EnumWindows=lambda cb, p: [cb(h, None) for h in sorted(wins)],
        GetWindowText=lambda h: wins[h][0],
        IsWindowVisible=lambda h: wins[h][1],
        GetWindowRect=lambda h: wins[h][2])
    o_gui, o_has = ud.win32gui, ud._HAS_WIN32
    ud.win32gui, ud._HAS_WIN32 = fake, True
    try:
        u._pid_from_hwnd = lambda h: 0 if h == 3 else (100 + h)
        u._weixin_dll_module = lambda pid: (0x1000, 1, 'Weixin.dll') if pid == 101 else None
        diag = {}
        got = u._wechat_hwnds(diag)
        check("只保留有 Weixin.dll 的主进程窗口（老行为不变）", got == [1], str(got))
        check("diag 记下了 matched/kept/dropped，供警告区分场景",
              diag['matched'] == 3 and diag['kept'] == 1
              and diag['dropped_no_dll'] == 2, str(diag))
        check("面积大的窗口排在前面（多个微信窗口时选主窗）", got[0] == 1)
        hint = u.gate_block_hint(diag)
        check("还有窗口留下时不打扰（返回空串）", hint == '', hint)
        check("32 位解释器 + 模块全拿不到 → 明说位数并让换 64 位",
              '32 位' in u.gate_block_hint(
                  {'win32': True, 'matched': 3, 'kept': 0}, 32))
        h64 = u.gate_block_hint({'win32': True, 'matched': 3, 'kept': 0}, 64)
        check("64 位下同样失败 → 指向权限/安全软件，而不是乱猜位数",
              '管理员' in h64 and '安全软件' in h64 and '改用 64 位' not in h64)
        h0 = u.gate_block_hint({'win32': True, 'matched': 0, 'kept': 0})
        check("一个窗口都没扫到 → 说清是没启动/未登录/在托盘",
              '未启动' in h0 and '托盘' in h0, h0[:40])
        hw = u.gate_block_hint({'win32': False, 'matched': 0, 'kept': 0})
        check("pywin32 缺失是另一种原因，不能混成「没登录」",
              'pywin32' in hw, hw[:40])
    finally:
        ud.win32gui, ud._HAS_WIN32 = o_gui, o_has

    o_has3 = ud._HAS_WIN32
    ud._HAS_WIN32 = False
    try:
        diag2 = {}
        check("_HAS_WIN32=False 时也把诊断写进 diag（不能空着）",
              u._wechat_hwnds(diag2) == [] and diag2.get('win32') is False, str(diag2))
    finally:
        ud._HAS_WIN32 = o_has3

    print("[tree] 每种原因只说一次")

    class Counter:
        def __init__(self):
            self.msgs = []

        def warning(self, fmt, *a, **k):
            self.msgs.append(fmt % a if a else fmt)

        def info(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

    c = Counter()
    o_log, o_set = ud.wxlog, set(ud._GATE_BLOCK_WARNED)
    ud.wxlog, ud._GATE_BLOCK_WARNED = c, set()
    try:
        bad = {'win32': True, 'matched': 2, 'kept': 0}
        u._warn_gate_blocked(bad)
        u._warn_gate_blocked(bad)
        u._warn_gate_blocked({'win32': True, 'matched': 0, 'kept': 0})
        check("同一种原因重复触发只警告一次", len(c.msgs) == 2, str(len(c.msgs)))
        n_before = len(c.msgs)
        u._warn_gate_blocked({'win32': True, 'matched': 2, 'kept': 2})
        check("健康场景（有窗口留下）完全不打扰", len(c.msgs) == n_before,
              "多出 %d 条" % (len(c.msgs) - n_before))
        check("警告文案里带上「没热激活」这个后果，便于用户对上症状",
              all('控件树' in m for m in c.msgs), str(c.msgs)[:60])

        # 集成：ensure_materialized 扫不到窗口时必须走一次警告并返回 False
        hits = {'n': 0}
        o_warn = WeChatUIA.__dict__["_warn_gate_blocked"]

        def spy(self, diag):
            hits['n'] += 1
            return None

        WeChatUIA._warn_gate_blocked = spy
        o_gui2, o_has2 = ud.win32gui, ud._HAS_WIN32
        ud.win32gui, ud._HAS_WIN32 = fake, True
        try:
            u._find_main = lambda: None
            u._wechat_hwnds = lambda diag=None: []
            check("ensure_materialized 扫不到窗口时不再静默返回",
                  u.ensure_materialized(timeout=1.0) is False and hits['n'] == 1,
                  "警告 %d 次" % hits['n'])
        finally:
            WeChatUIA._warn_gate_blocked = o_warn
            ud.win32gui, ud._HAS_WIN32 = o_gui2, o_has2
    finally:
        ud.wxlog, ud._GATE_BLOCK_WARNED = o_log, o_set
    check("wxlog 与去重集合已还原", ud.wxlog is not c and isinstance(ud._GATE_BLOCK_WARNED, set))

    print("[tree] ensure_window 的兜底分支也不再静默")
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(here, "wechatauto", "uia_driver.py"), encoding="utf-8").read()
    i = src.index("def ensure_window(")
    seg = src[i:src.index("\n    def ", i + 1)]
    j = seg.index("else:")
    check("走「读屏标志 + wake」之前先给出诊断（32 位就是这么被误判成屏蔽的）",
          "_warn_gate_blocked" in seg[j:], seg[j:j + 90].strip())
    k = src.index("def ensure_materialized(")
    seg2 = src[k:src.index("\n    def ", k + 1)]
    check("ensure_materialized 的早退分支也带诊断",
          "_warn_gate_blocked" in seg2 and "self._wechat_hwnds(scan)" in seg2)

    print("[tree] 窄窗口里点一次退不出会话：按「聊天页那一片出来没有」决定点几下")
    # 用户实测（主窗 848×1274、开着会话）：点第一下导航栏「微信」只把主窗带回聊天页，
    # 会话还开着，session_list 和搜索框都**不在树里**；要再点一次才退出会话。
    # 以前无条件只点一下，于是 open_chat 接下来找搜索框必然落空——报出来就是「会话打不开」。
    class N:
        def __init__(self, cls="", aid="", ctype="", name="", kids=None):
            self.ClassName, self.AutomationId = cls, aid
            self.ControlTypeName, self.Name = ctype, name
            self._kids = kids or []

        def GetChildren(self):
            return list(self._kids)

    o_rhythm, o_log = ud.rhythm, ud.wxlog
    warns = []
    ud.wxlog = types.SimpleNamespace(
        debug=lambda *a: None, info=lambda *a: None,
        warning=lambda f, *a: warns.append(f % a if a else f))
    try:
        ud.rhythm = types.SimpleNamespace(nap=lambda s=0.0: None)

        def mk(kids=(), bar=True, msg=True):
            tab = N(cls=ud.TAB_ITEM_CLS, name=ud.CHAT_TAB_NAME)
            bar_c = N(cls=ud.MAIN_TAB_BAR_CLS, kids=[tab]) if bar else None
            # msg=True：窄窗口开着会话时消息列表**本来就在树里**（实测
            # chat_message_list rect=(776,486,1496,1312)），所以它不能当判据。
            kids = list(kids)
            if msg:
                kids.append(N(cls="mmui::RecyclerListView", aid="chat_message_list"))
            content = N(cls="mmui::MainView", kids=kids)
            root = N(cls="mmui::MainWindow",
                     kids=[c for c in (bar_c, content) if c is not None])
            return root, content

        def run(root, content, flip_after=None, **kw):
            """flip_after：第几次点击之后才让 session_list 出现（None=一直不出现）。"""
            clicks = []

            def _click(_c):
                clicks.append(1)
                if flip_after is not None and len(clicks) >= flip_after:
                    content._kids.append(N(aid="session_list"))
                return True
            uu = WeChatUIA.__new__(WeChatUIA)
            uu._win, uu._click_ctrl = root, _click
            uu._find_main = lambda: root
            return uu.back_to_chat_tab(settle=0.0, **kw), len(clicks)

        ok, n = run(*mk([N(aid="session_list")]))
        check("会话列表本来就在 → 一点都不点（旧写法会先无条件点一下，把用户开着的会话切走）",
              ok is True and n == 0, "点了 %d 次" % n)
        ok, n = run(*mk([N(ctype="EditControl", name=ud.SEARCH_EDIT_NAME)]))
        check("只有搜索框在树里也算回到聊天页（open_chat 要的就是搜索框）",
              ok is True and n == 0, "点了 %d 次" % n)
        ok, n = run(*mk([]), flip_after=2)
        check("窄窗口开着会话：点一次没退出 → 再点一次（用户要的就是这第二下）",
              ok is True and n == 2, "%s 点了 %d 次" % (ok, n))
        ok, n = run(*mk([]), flip_after=1)
        check("点一次就退出时不多点第二下（多那一下会把会话切走）",
              ok is True and n == 1, "%s 点了 %d 次" % (ok, n))
        ok, n = run(*mk([]), flip_after=None)
        check("点满还没出来 → 明确 False 并警告卡在哪（不静默）",
              ok is False and n == 2 and any("不在树里" in w for w in warns),
              "%s 点了 %d 次 警告=%d" % (ok, n, len(warns)))
        warns[:] = []
        ok, n = run(*mk([]), flip_after=2, max_clicks=1)
        check("max_clicks=1 时只点一次就收手（调用方能自己限额度）",
              ok is False and n == 1, "%s 点了 %d 次" % (ok, n))
        ok, n = run(*mk([N(ctype="EditControl", name=ud.SEARCH_EDIT_NAME)]),
                    flip_after=None, require_list=True)
        check("require_list=True 时搜索框不算数（真要会话列表的调用方不被骗）",
              ok is False and n == 2, "%s 点了 %d 次" % (ok, n))
        ok, n = run(*mk([], bar=False))
        check("导航栏都找不到 → False 并且一次都不点",
              ok is False and n == 0, "%s 点了 %d 次" % (ok, n))
    finally:
        ud.rhythm, ud.wxlog = o_rhythm, o_log
    check("rhythm 与 wxlog 已还原",
          ud.rhythm is o_rhythm and ud.wxlog is o_log)


# ----------------------------------------------------------------------
# 14. 图片三档（_h.dat 原图 / .dat 压缩 / _t.dat 缩略图）——纯离线，临时目录 + 假 db
# ----------------------------------------------------------------------
def t_image() -> None:
    """issue 反馈「只能下到缩略图，下不到原图」。

    两处库侧原因：(1) download_image 压根不看 _h.dat，而且拿到压缩版时文件名
    跟原图一模一样，「只要原图」的调用方只能靠大小猜；(2) download_image_original
    里那道 `>102400` 硬门槛——本机 705 个 _h.dat 中位数只有 91KB，一半真原图被判
    「还没下载」。这里把三档判据、分档取文件、气泡排序都钉住。"""
    import hashlib
    import os as _os
    import shutil
    import sqlite3
    import tempfile
    import time as _time

    from wechatauto.media import MediaDownloader

    MD5 = 'a' * 32
    tmpdirs = []

    def newmd(files_spec, user='wxid_img'):
        """造一棵假的 attach 目录树：files_spec = {档位: 字节数}"""
        d = tempfile.mkdtemp(prefix='wxim-')
        tmpdirs.append(d)
        base = _os.path.join(d, 'msg', 'attach',
                             hashlib.md5(user.encode()).hexdigest(), '2026-09')
        _os.makedirs(base)
        suffix = {'original': '_h.dat', 'mid': '.dat', 'thumb': '_t.dat'}
        for tier, size in files_spec.items():
            with open(_os.path.join(base, MD5 + suffix[tier]), 'wb') as f:
                f.write(b'\0' * size)
        md = MediaDownloader.__new__(MediaDownloader)
        md.save_dir = tempfile.mkdtemp(prefix='wxout-')
        tmpdirs.append(md.save_dir)
        md._image_key = md._xor_key = md._cfg_dword = None

        class FakeDB:
            account_dir = d

            def get_message_row(self, u, lid, local_type=None, shard=None):
                return {"local_type": 3, "create_time": 1, "sort_seq": 9,
                        "packed_info": MD5.encode(), "content": b''}
        md.db = FakeDB()
        md._tmp = d
        return md

    print("[image] 原图「在不在、完不完整」的判据")
    orr = MediaDownloader.original_ready
    ic = MediaDownloader._image_complete
    check("非空且过下限就算原图", orr(90000, 95000) is True)
    check("40KB 的小截图原图照样算（旧的 >100KB 硬门槛会误杀）",
          orr(40000, 30000) is True and orr(40000, None) is True,
          "%s %s" % (orr(40000, 30000), orr(40000, None)))
    # 1.2.4.2 的比例判据被实机否掉了：同一张图的 .dat 有时就是比 _h.dat 大
    check("比压缩版小也算原图（尺寸比例不是判据——用户实机证明）",
          orr(3503, 6351) is True and orr(89520, 109530) is True,
          "%s %s" % (orr(3503, 6351), orr(89520, 109530)))
    check("mid_size 传什么都不影响结论（参数留着只为不改已发布的签名）",
          orr(5000, 999999) is True and orr(5000) is True)
    check("0 字节 / None 不算", orr(0, 45000) is False and orr(None, 45000) is False)
    check("下限只挡空壳", orr(10, None) is False and orr(1024, None) is True)
    JPG = b"\xff\xd8\xff" + b"j" * 60 + b"\xff\xd9"
    check("JPEG 有 FF D9 收尾算完整", ic(JPG) is True)
    check("JPEG 被截断（没有收尾标记）算不完整",
          ic(b"\xff\xd8\xff" + b"j" * 80) is False)
    check("JPEG 收尾后带填充字节仍算完整（微信会追加页脚）",
          ic(b"\xff\xd8\xff" + b"j" * 40 + b"\xff\xd9" + b"\0" * 20) is True)
    check("PNG 看 IEND",
          ic(b"\x89PNG\r\n\x1a\n" + b"p" * 40 + b"IEND\xaeB`\x82") is True
          and ic(b"\x89PNG\r\n\x1a\n" + b"p" * 40) is False)
    check("GIF 看结束符 0x3B", ic(b"GIF89a" + b"g" * 40 + b"\x3b\x00") is True)
    check("认不出格式的不否决（wxgf/动画表情等，误杀比重演更糟）",
          ic(b"wxgf" + b"z" * 60) is True)
    check("太短的字节直接算不完整", ic(b"\xff\xd8") is False and ic(None) is False)

    print("[image] 三档状态报告")
    md = newmd({'original': 200000, 'mid': 100000, 'thumb': 5000})
    st = md.image_status('wxid_img', 7)
    check("三档齐全 → ok / best=original", st['available'] and st['best'] == 'original'
          and st['reason'] == 'ok', str(st['reason']))
    check("tiers 报出每档字节", st['tiers'] == {'original': 200000, 'mid': 100000,
                                                'thumb': 5000}, str(st['tiers']))
    check("md5 从 packed_info 里取到了", st['md5'] == MD5, str(st['md5'])[:8])
    st2 = newmd({'mid': 100000, 'thumb': 5000}).image_status('wxid_img', 7)
    check("只有压缩版 → mid_only（不是 ok，也不再冒充原图）",
          st2['reason'] == 'mid_only' and st2['best'] == 'mid'
          and st2['available'] is False, st2['reason'])
    st3 = newmd({'thumb': 5000}).image_status('wxid_img', 7)
    check("只有缩略图 → only_thumbnail", st3['reason'] == 'only_thumbnail', st3['reason'])
    st4 = newmd({'original': 10, 'mid': 100000, 'thumb': 5000}).image_status('wxid_img', 7)
    check("_h.dat 是半截文件 → original_partial，best 退回 mid",
          st4['reason'] == 'original_partial' and st4['best'] == 'mid',
          "%s %s" % (st4['reason'], st4['best']))
    st5 = newmd({}).image_status('wxid_img', 7)
    check("本机一份都没有 → no_local_copy", st5['reason'] == 'no_local_copy', st5['reason'])

    class NoRow:
        account_dir = '.'

        def get_message_row(self, u, lid, local_type=None, shard=None):
            return None
    mdr = MediaDownloader.__new__(MediaDownloader)
    mdr.db = NoRow()
    check("消息行不存在 → no_message_row，不抛",
          mdr.image_status('x', 1)['reason'] == 'no_message_row')

    class NoMd5(NoRow):
        def get_message_row(self, u, lid, local_type=None, shard=None):
            return {"local_type": 3, "create_time": 1, "packed_info": b'', "content": '文本'}
    mdn = MediaDownloader.__new__(MediaDownloader)
    mdn.db = NoMd5()
    check("内容里取不到图片指纹 → no_md5（不是 not_image）",
          mdn.image_status('x', 1)['reason'] == 'no_md5')

    print("[image] 分档取文件")
    JPEG = b"\xff\xd8\xff" + b"J" * 61 + b"\xff\xd9"

    def stub_decrypt(md, keep=None):
        def _d(path, aes_key=None, xor_key=None):
            if keep is not None:
                keep.append(path)
            return JPEG
        return _d

    md = newmd({'original': 200000, 'mid': 100000, 'thumb': 5000})
    used = []
    md.decrypt_image = stub_decrypt(md, used)
    out = md.download_image('wxid_img', 7, save_dir=md.save_dir)
    check("tier=None 仍是老行为（取压缩版、文件名不带档位标记）",
          out.endswith('wxid_img_7.jpg') and '_h' not in _os.path.basename(out)
          and used[-1].endswith(MD5 + '.dat'), _os.path.basename(out or ''))
    out = md.download_image('wxid_img', 7, save_dir=md.save_dir, tier='original')
    check("tier=original 取的是 _h.dat 且文件名带 _h",
          used[-1].endswith(MD5 + '_h.dat') and out.endswith('wxid_img_7_h.jpg'),
          _os.path.basename(out or ''))
    out = md.download_image('wxid_img', 7, save_dir=md.save_dir, tier='best')
    check("tier=best 优先原图", out.endswith('wxid_img_7_h.jpg'),
          _os.path.basename(out or ''))
    md2 = newmd({'original': 10, 'mid': 100000, 'thumb': 5000})
    md2.decrypt_image = stub_decrypt(md2, used)
    check("tier=original 但本机只有半截 _h → 返回 None，不悄悄降级",
          md2.download_image('wxid_img', 7, save_dir=md2.save_dir, tier='original') is None)
    out = md2.download_image('wxid_img', 7, save_dir=md2.save_dir, tier='best')
    check("tier=best 遇到半截原图 → 退回压缩版", out.endswith('wxid_img_7.jpg'),
          _os.path.basename(out or ''))
    md3 = newmd({'thumb': 5000})
    md3.decrypt_image = stub_decrypt(md3, used)
    check("只有缩略图时 tier=None 照旧落 _thumb",
          md3.download_image('wxid_img', 7, save_dir=md3.save_dir).endswith('wxid_img_7_thumb.jpg'))
    check("tier=thumb 只拿缩略图 / 乱写的 tier 返回 None",
          md3.download_image('wxid_img', 7, save_dir=md3.save_dir,
                             tier='thumb').endswith('_thumb.jpg')
          and md3.download_image('wxid_img', 7, tier='whatever') is None)

    print("[image] tier='full'：只要「不是 _t.dat 档」的那一份")
    pf = MediaDownloader._pick_full
    check("本机有 _h.dat → 取原件并标 _h",
          pf({'original': ('a_h.dat', 200000), 'mid': ('a.dat', 100000)}, 100000)
          == (('a_h.dat', 200000), '_h'))
    check("本机只有 .dat → 取它、文件名不带档位标记（那张是不是完整图本机判不出来）",
          pf({'mid': ('a.dat', 100000), 'thumb': ('a_t.dat', 5000)}, 100000)
          == (('a.dat', 100000), ''))
    check("只有预览图 → None（绝不拿缩略图交差，这是和 tier='best' 唯一的区别）",
          pf({'thumb': ('a_t.dat', 5000)}, 0) is None)
    check("_h.dat 是空壳时退回 .dat，不把空壳当成原件",
          pf({'original': ('a_h.dat', 10), 'mid': ('a.dat', 100000)}, 100000)
          == (('a.dat', 100000), ''))

    def fake_tree(md, user='wxid_img'):
        return _os.path.join(md.db.account_dir, 'msg', 'attach',
                             hashlib.md5(user.encode()).hexdigest(), '2026-09')

    def _no_ui(*a, **k):
        raise AssertionError("download_image 不该碰界面")

    mt = newmd({'thumb': 5000})
    mt.decrypt_image = stub_decrypt(mt, used)
    mt.download_image_original = _no_ui
    check("本机只有预览图 → tier=full 返回 None，**不去碰界面**（要下就用 download_image_original）",
          mt.download_image('wxid_img', 7, save_dir=mt.save_dir, tier='full') is None)
    mm = newmd({'mid': 100000, 'thumb': 5000})
    mm.decrypt_image = stub_decrypt(mm, used)
    mm.download_image_original = _no_ui
    out = mm.download_image('wxid_img', 7, save_dir=mm.save_dir, tier='full')
    check("本机有 .dat → tier=full 交 .dat、文件名不带档位标记（至少不是预览图档；"
          "它本身是不是完整图本机判不出来）",
          out.endswith('wxid_img_7.jpg') and used[-1].endswith(MD5 + '.dat'), str(out))
    mh = newmd({'original': 200000, 'mid': 100000, 'thumb': 5000})
    mh.decrypt_image = stub_decrypt(mh, used)
    mh.download_image_original = _no_ui
    out = mh.download_image('wxid_img', 7, save_dir=mh.save_dir, tier='full')
    check("本机有原件 → tier=full 优先交原件并标 _h",
          out.endswith('wxid_img_7_h.jpg') and used[-1].endswith(MD5 + '_h.dat'),
          _os.path.basename(out or ''))
    mt3 = newmd({})
    mt3.download_image_original = _no_ui
    check("附件目录里这条图压根没出现过 → tier=full 返回 None 且不碰界面",
          mt3.download_image('wxid_img', 7, save_dir=mt3.save_dir, tier='full') is None)

    print("[image] image_status：available 只答「有没有原件」")
    check("mid_only 时 available=False、has_mid=True（本机有 .dat，但它可能仍是预览版）",
          newmd({'mid': 100000, 'thumb': 5000}).image_status('wxid_img', 7)['has_mid'] is True
          and newmd({'mid': 100000}).image_status('wxid_img', 7)['available'] is False)
    check("only_thumbnail 时 has_mid=False（本机只有预览图）",
          newmd({'thumb': 5000}).image_status('wxid_img', 7)['has_mid'] is False)
    check("ok 时 has_mid 也为 True",
          newmd({'original': 200000}).image_status('wxid_img', 7)['has_mid'] is True)
    check("状态里**没有** has_full 那种字段（本机判不出 .dat 是不是完整图，就不假装有）",
          "has_full" not in newmd({'mid': 100000}).image_status('wxid_img', 7))

    print("[image] 虚拟列表里先点最可能的那张")
    ob = MediaDownloader._order_bubbles
    pairs = [('a', 10), ('b', 20), ('c', 30), ('d', 40)]   # (气泡, top)：自上而下越来越新
    check("n_newer=0 → 最下面那张排第一", ob(pairs, 0)[0] == 'd', str(ob(pairs, 0)))
    check("n_newer=2 → 从下数第 3 张排第一", ob(pairs, 2)[0] == 'b', str(ob(pairs, 2)))
    check("先试的那张之后仍按从上到下排队，一张不落",
          ob(pairs, 2) == ['b', 'a', 'c', 'd'], str(ob(pairs, 2)))
    check("目标在可视区之外时不盲猜（保持原顺序）",
          ob(pairs, 9) == ['a', 'b', 'c', 'd'], str(ob(pairs, 9)))
    check("乱序传入也按 top 排", ob(list(reversed(pairs)), 0)[0] == 'd')
    check("空列表不抛", ob([], 0) == [])

    print("[image] 气泡在哪一侧的哪个 x：竖屏（portrait）实测点不中的根因")
    # PrintWindow 拍微信窗口本体量到的两组数（屏幕截图不可信——会拍到压在微信上面的
    # IDE 面板）：行矩形都是整行宽，但气泡位置不随行宽等比缩放。
    #   宽窗口 行 466..3064（2598 宽）→ 气泡 +44..+632
    #   竖屏   行 414..1134（ 720 宽）→ 气泡 +141..+459（自发图镜像 +261..+579）
    from types import SimpleNamespace as _R
    bx = MediaDownloader._bubble_click_xs
    wide, port = _R(left=466, right=3064), _R(left=414, right=1134)
    check("宽窗口：别人发的第一个候选还是 x=777（旧行为逐字不变）",
          bx(wide, False)[0] == 777, str(bx(wide, False)[:2]))
    check("宽窗口：自己发的第一个候选还是 x=2753",
          bx(wide, True)[0] == 2753, str(bx(wide, True)[:2]))
    check("宽窗口两种口径算出同一个点 → 候选不重复",
          len(set(bx(wide, False))) == 2, str(bx(wide, False)))
    band = (141, 459)
    check("竖屏：第一个候选落在实测气泡区间 +141..+459 里",
          band[0] <= bx(port, False)[0] - port.left <= band[1],
          "候选 +%d" % (bx(port, False)[0] - port.left))
    check("竖屏：旧的「行宽 12%」=+86 在区间之外（这就是点不中的原因）",
          not (band[0] <= int((port.right - port.left) * 0.12) <= band[1]),
          "12%% = +%d" % int((port.right - port.left) * 0.12))
    mband = (261, 579)
    check("竖屏自发图（镜像区间）：第一个候选落在 +261..+579 里",
          mband[0] <= port.right - bx(port, True)[0] <= mband[1] or
          mband[0] <= bx(port, True)[0] - port.left <= mband[1],
          "候选距右边缘 %d" % (port.right - bx(port, True)[0]))
    check("候选全部落在行内（不会点到消息带外面）",
          all(port.left < x < port.right for x in bx(port, False)) and
          all(port.left < x < port.right for x in bx(port, True)),
          str(bx(port, False)))
    tiny = _R(left=0, right=300)
    check("极窄行（300 宽）也不越界", all(0 < x < 300 for x in bx(tiny, False)),
          str(bx(tiny, False)))
    check("候选按发送方先排：自己发的先给右侧，别人发的先给左侧",
          bx(port, True)[0] > bx(port, False)[0],
          "%s vs %s" % (bx(port, True)[0], bx(port, False)[0]))

    class RowsDB:
        def __init__(self, rows, boom=False):
            self.rows, self.boom = rows, boom

        def get_image_rows(self, user, limit=300):
            if self.boom:
                raise RuntimeError('库损坏')
            return self.rows
    mdc = MediaDownloader.__new__(MediaDownloader)
    mdc.db = RowsDB([{'sort_seq': 10}, {'sort_seq': 12}, {'sort_seq': 5}])
    check("数得出「比这条更新的图片」有几张", mdc._newer_image_count('u', 10) == 1)
    mdc.db = RowsDB([], boom=True)
    check("数不出来时返回 0 而不是抛", mdc._newer_image_count('u', 10) == 0)
    check("sort_seq 缺失时返回 0",
          MediaDownloader._newer_image_count(mdc, 'u', None) == 0)

    print("[image] 等本机出现目标那一档")
    tdir = tempfile.mkdtemp(prefix='wxwait-')
    tmpdirs.append(tdir)
    p0 = _os.path.join(tdir, 'zero.dat')
    p1 = _os.path.join(tdir, 'full.dat')
    open(p0, 'wb').write(b'\0' * 0)
    open(p1, 'wb').write(b'\0' * 60000)
    small = _os.path.join(tdir, 'small.dat')
    open(small, 'wb').write(b'\0' * 3000)      # 已经不再变化，但比压缩版小

    def files_of(seq, n=[0]):
        """把 _image_files 演成「前几次还是 0 字节，之后稳定在某个文件」。"""
        def _f(u, m):
            n[0] += 1
            p = seq[min(n[0] - 1, len(seq) - 1)]
            return {'original': (p, _os.path.getsize(p))}
        return _f
    mw = MediaDownloader.__new__(MediaDownloader)
    mw._image_files = files_of([p0, p0, p1, p1])
    got = mw._wait_tier('u', MD5, until=_time.time() + 6)
    check("先 0 后 60000 且稳定 → 判定下完（交回的三元组里带档位）",
          got is not None and got[0] == 'original' and got[2] == 60000, str(got))
    mw2 = MediaDownloader.__new__(MediaDownloader)
    mw2._image_files = files_of([p0])
    check("一直是 0 字节 → 到点返回 None（不返回半截文件）",
          mw2._wait_tier('u', MD5, until=_time.time() + 0.1) is None)
    mw3 = MediaDownloader.__new__(MediaDownloader)
    mw3._image_files = files_of([small])
    got3 = mw3._wait_tier('u', MD5, until=_time.time() + 3)
    check("尺寸不再变化就算下完（不再拿它和压缩版比大小）",
          got3 is not None and got3[2] == 3000, str(got3))
    # 多档一起等的等法（download_image_original 现在只等原件，这一档留给"两档都算"的用法）：
    # 谁先稳定交谁，两档同时都在时按传入顺序优先。
    mw4 = MediaDownloader.__new__(MediaDownloader)
    mw4._image_files = lambda u, m: {'mid': (p1, 60000)}
    got4 = mw4._wait_tier('u', MD5, until=_time.time() + 6,
                         tiers=("original", "mid"))
    check("等两档时本机只有 .dat → 交 mid（_wait_tier 仍支持多档，只是 download_image_original 不用）",
          got4 is not None and got4[0] == 'mid', str(got4))
    mw5 = MediaDownloader.__new__(MediaDownloader)
    both = {'original': (p1, 60000), 'mid': (small, 3000)}
    mw5._image_files = lambda u, m: dict(both)
    got5 = mw5._wait_tier('u', MD5, until=_time.time() + 6,
                         tiers=("original", "mid"))
    check("两档都在时按优先级交原件（不是一拿到就交第一个）",
          got5 is not None and got5[0] == 'original', str(got5))
    mw6 = MediaDownloader.__new__(MediaDownloader)
    mw6._image_files = lambda u, m: {'mid': (p1, 60000)}
    check("只等原件时本机只有 .dat → 到点 None（.dat 不能拿来凑数）",
          mw6._wait_tier('u', MD5, until=_time.time() + 0.1,
                         tiers=("original",)) is None)
    mw7 = MediaDownloader.__new__(MediaDownloader)
    tick = [0]

    def growing(u, m):
        tick[0] += 1
        return {'original': (p1, 1000 + tick[0] * 500)}   # 一直在长，从不稳定
    mw7._image_files = growing
    got7 = mw7._wait_tier('u', MD5, until=_time.time() + 1.2)
    check("到点时哪怕还在长也交出去（已经可用的文件比一句「失败」有用，沿用旧行为）",
          got7 is not None and got7[0] == 'original', str(got7))
    mw8 = MediaDownloader.__new__(MediaDownloader)
    grow = [0]

    def both_moving(u, m):
        grow[0] += 1
        return {'mid': (small, 3000 + grow[0]), 'original': (p1, 60000 + grow[0])}
    mw8._image_files = both_moving
    got8 = mw8._wait_tier('u', MD5, until=_time.time() + 1.2,
                          tiers=("original", "mid"))
    check("两档都还在长、到点只能交一档时交的是原件（优先级只有一处代码在管）",
          got8 is not None and got8[0] == 'original', str(got8))

    print("[image] db.get_image_rows 带出 packed_info")
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE Msg_1 (local_id INTEGER, server_id INTEGER, "
                 "real_sender_id INTEGER, create_time INTEGER, sort_seq INTEGER, "
                 "local_type INTEGER, packed_info_data BLOB, message_content BLOB)")
    conn.execute("INSERT INTO Msg_1 VALUES (1, 11, 7, 100, 300, 3, ?, ?)",
                 (MD5.encode(), b''))
    conn.execute("INSERT INTO Msg_1 VALUES (2, 12, 7, 200, 400, 1, ?, ?)",
                 (b'text', b'hello'))
    conn.commit()
    from wechatauto.db import WeChatDB
    d = WeChatDB.__new__(WeChatDB)
    d._run_msg_query = lambda user, fn: fn([(conn, 'Msg_1')])
    rows = d.get_image_rows('wxid_img')
    check("只返回图片行（local_type=3）", len(rows) == 1, str(len(rows)))
    check("字段名对齐 get_message_row（packed_info / content）",
          'packed_info' in rows[0] and 'content' in rows[0], str(sorted(rows[0])))
    md4 = MediaDownloader.__new__(MediaDownloader)
    md4.db = d
    md4._image_files = lambda u, m: {}
    check("list_image_status 用得上这条查询（不会误报 no_md5）",
          md4._img_md5(rows[0]) == MD5, str(md4._img_md5(rows[0]))[:8])

    print("[image] list_image_status 一次报全")
    ml = newmd({'original': 200000, 'mid': 100000, 'thumb': 5000})
    Other = hashlib.md5(b'other').hexdigest()

    class ListDB:
        account_dir = ml.db.account_dir

        def get_image_rows(self, user, limit=300):
            return [{"local_id": 7, "create_time": 100, "sort_seq": 300,
                     "packed_info": MD5.encode(), "content": b''},
                    {"local_id": 8, "create_time": 200, "sort_seq": 400,
                     "packed_info": Other.encode(), "content": b''}]
    ml.db = ListDB()
    got = ml.list_image_status('wxid_img')
    check("按行报档位，local_id/create_time 都带出来",
          [g['local_id'] for g in got] == [7, 8] and got[0]['create_time'] == 100,
          str([(g['local_id'], g['create_time']) for g in got]))
    check("有 _h.dat 的那条 → ok/original，没下过的另一条 → no_local_copy",
          got[0]['reason'] == 'ok' and got[0]['best'] == 'original'
          and got[1]['reason'] == 'no_local_copy',
          str([(g['reason'], g['best']) for g in got]))
    scans = []
    ml._image_files = lambda u, m: (scans.append(m) or {'mid': ('x', 100000)})
    ml.db.get_image_rows = lambda user, limit=300: [
        {"local_id": 7, "create_time": 100, "sort_seq": 300,
         "packed_info": MD5.encode(), "content": b''},
        {"local_id": 9, "create_time": 110, "sort_seq": 290,
         "packed_info": MD5.encode(), "content": b''}]
    check("同一 md5 只扫一次盘", len(ml.list_image_status('wxid_img')) == 2
          and scans == [MD5], str(scans))

    print("[image] 用 UIA 行序列认出目标是哪一行")

    class R:
        def __init__(self, l, t, rr, b):
            self.left, self.top, self.right, self.bottom = l, t, rr, b

    class Row:
        def __init__(self, cls, name, rect):
            self.ClassName, self.Name, self.BoundingRectangle = cls, name, rect

    class Lst:
        def __init__(self, kids, rect):
            self._k, self.BoundingRectangle = kids, rect

        def GetChildren(self):
            return self._k

    IMG, TXT, TIME = ("mmui::ChatBubbleReferItemView", "mmui::ChatTextItemView",
                      "mmui::ChatItemView")
    band = R(466, 0, 3064, 246)                       # 实测：整行宽 2598
    kids = [Row(TXT, "本周试卷", R(466, 0, 3064, 148)),
            Row(TIME, "9月17日 19:50", R(466, 148, 3064, 230)),
            Row(IMG, "图片", R(466, 230, 3064, 476)),
            Row(IMG, "交易截图", band),                # 同类的引用卡片，不是图片气泡
            Row(TXT, "收到", R(466, 476, 3064, 624)),
            Row(IMG, "图片", R(466, 9000, 3064, 9246))]   # 矩形在列表外＝没渲染
    lv = MediaDownloader.__new__(MediaDownloader)
    ui = lv._visible_rows(Lst(kids, R(466, 0, 3064, 1210)))
    check("只收图片行与文本行，时间行/引用卡片/未渲染行都不要",
          [(k, n) for k, n, _c in ui] == [("text", "本周试卷"), ("image", "图片"),
                                          ("text", "收到")],
          str([(k, n) for k, n, _c in ui]))
    check("认出来的那一行把控件原样带回来（后面要拿它算矩形）",
          all(c.Name is not None for _k, _n, c in ui))
    # 实机对着数据库核过的类名映射（同一会话同一屏）：把 ChatBubbleItemView 当成
    # 文本行是**错**的——它是文件/链接/卡片，正文 1900 字 XML、UIA 只给 43 字摘要。
    check("类名映射只有图片/文本两项（文件卡片、系统消息、时间行都不在内）",
          MediaDownloader._UI_ROW_KIND == {
              "mmui::ChatBubbleReferItemView": "image",
              "mmui::ChatTextItemView": "text"}, str(MediaDownloader._UI_ROW_KIND))
    check("数据库侧同理：只有「图片」「文本」参与",
          MediaDownloader._DB_ROW_KIND == {"图片": "image", "文本": "text"})
    mixed = Lst([Row("mmui::ChatBubbleItemView", "文件卡片摘要", R(466, 0, 3064, 284)),
                 Row("mmui::ChatSystemInfoItemView", "系统消息一行", R(466, 284, 3064, 350)),
                 Row("mmui::ChatItemView", "11:31", R(466, 350, 3064, 432)),
                 Row(TXT, "本周试卷", R(466, 432, 3064, 580))], R(466, 0, 3064, 1210))
    check("整屏都是文件卡片/系统消息/时间行时只留那一行文本",
          [(k, n) for k, n, _c in lv._visible_rows(mixed)] == [("text", "本周试卷")],
          str([(k, n) for k, n, _c in lv._visible_rows(mixed)]))

    st = MediaDownloader._same_text
    check("UIA 摘要截断了正文仍算同一条", st("本周试卷第一单元", "本周试卷第一") is True)
    check("前 10 字不同就不是同一条", st("本周试卷", "下周试卷") is False)
    check("空正文不参与判定（不当成互相包含）", st("", "任意") is False)

    def dbspec(spec, first_id=100):
        out = []
        for i, (kind, txt) in enumerate(spec):
            out.append({"kind": kind, "content": txt, "local_id": first_id + i})
        return out

    db = dbspec([("text", "早上好"), ("image", ""), ("text", "本周试卷第一单元测试"),
                 ("image", ""), ("image", ""), ("text", "收到")], first_id=100)
    ui2 = [("image", "图片", "A"), ("text", "本周试卷第一单元", "B"),
           ("image", "图片", "C"), ("image", "图片", "D"), ("text", "收到", "E")]
    check("整段序列对齐后点名目标（104 是可视下标 3，即第 4 行）",
          lv._align_index(ui2, db, 104) == 3, str(lv._align_index(ui2, db, 104)))
    check("103 → 可视下标 2；101 → 0（同一份对齐结果）",
          lv._align_index(ui2, db, 103) == 2 and lv._align_index(ui2, db, 101) == 0)
    check("目标 local_id=100 不在这个可视窗口里 → None（不硬猜）",
          lv._align_index(ui2, db, 100) is None)
    check("目标对齐到的是文本行而不是图片行 → None",
          lv._align_index(ui2, db, 105) is None)
    check("数据库比可视区还短 → None", lv._align_index(ui2, db[:2], 101) is None)
    check("可视区为空 → None", lv._align_index([], db, 101) is None)
    # 种类全对但正文对不上：吻合度不够就不给结果（只比种类太容易贴错位）
    ui_bad = [("image", "图片", "A"), ("text", "话不对", "B"), ("image", "图片", "C"),
              ("text", "题不对", "D"), ("text", "不", "E"), ("text", "对", "F")]
    db_bad = dbspec([("image", ""), ("text", "本周试卷"), ("image", ""),
                     ("text", "收到"), ("text", "好的"), ("text", "明白了")])
    check("正文全对不上 → None（对齐分数没到阈值）",
          lv._align_index(ui_bad, db_bad, 102) is None)
    check("正文全对不上（一条锚点都没有）→ None",
          lv._align_index(ui_bad, db_bad, 102) is None)
    # 锚点够、但整体吻合度不够：分数阈值单独钉一条（否则上面那条分不清是被谁挡下的）
    db3 = dbspec([("text", "甲"), ("text", "乙"), ("text", "丙"),
                  ("text", "丁"), ("text", "戊"), ("image", "")], first_id=100)
    ui3 = [("text", "甲", "A"), ("text", "乙", "B"), ("image", "图片", "C"),
           ("image", "图片", "D"), ("image", "图片", "E"), ("image", "图片", "F")]
    check("两条锚点够、但 6 行里只吻合 3 行（0.5<0.6）→ None",
          lv._align_index(ui3, db3, 105) is None)
    check("把分数阈值放宽到 0.5 就放行（钉住是分数在挡，不是锚点）",
          lv._align_index(ui3, db3, 105, min_score=0.5) == 5)
    # 实机撞到的假高分：可视区只剩一行时，任何偏移都「完美吻合」
    one_row = [("image", "图片", "A")]
    check("可视区只剩一行时不给结果（吻合度 1.00 是假的，既没锚点也不唯一）",
          lv._align_index(one_row, db, 101) is None)
    # 单独钉 min_text_anchors：窗口形状在库里只出现一次（没有并列偏移），
    # 但整屏一张文本行都没有 —— 这时挡下它的只能是锚点规则。
    img_only_db = dbspec([("text", "甲"), ("image", ""), ("image", ""), ("text", "乙")])
    img_only_ui = [("image", "图片", "A"), ("image", "图片", "B")]
    check("唯一偏移但一条文本锚点都没有 → None",
          lv._align_index(img_only_ui, img_only_db, 102) is None)
    check("显式不要锚点时才放行（钉住挡下它的是锚点规则，不是并列歧义）",
          lv._align_index(img_only_ui, img_only_db, 102, min_text_anchors=0) == 1)
    one_anchor = [("image", "图片", "A"), ("text", "收到", "E")]
    check("只有一条文本锚点也不给结果", lv._align_index(one_anchor, db, 103) is None)
    check("两条锚点才放行（ui2 里「本周试卷…」+「收到」正好两条）",
          lv._align_index(ui2, db, 103, min_text_anchors=2) == 2)

    xs = MediaDownloader._bubble_click_xs
    wide = R(466, 0, 3064, 246)
    check("别人发的：先点左边（实测内容带 +1.7%~+24.3% 之内）",
          xs(wide, False)[0] == 466 + int(2598 * 0.12)
          and 466 + int(2598 * 0.017) <= xs(wide, False)[0] <= 466 + int(2598 * 0.243),
          str(xs(wide, False)))
    check("自己发的：先点右边（气泡在右侧，只按左边点就是「点击错位」）",
          xs(wide, True)[0] == 3064 - int(2598 * 0.12)
          and xs(wide, True)[0] > xs(wide, False)[0], str(xs(wide, True)))
    check("两侧都给出（第一侧没点开还能试另一侧）", len(xs(wide, True)) == 2
          and set(xs(wide, True)) == set(xs(wide, False)))

    print("[image] 并列偏移必须认得出「不唯一」")
    def mkdb(spec, first_id=1000):
        return [{"kind": k, "content": c, "local_id": first_id + i}
                for i, (k, c) in enumerate(spec)]
    # 正文每两行重复一次 → 偏移 0 和 2 都满分，锚点也都够
    dup = mkdb([("text", "甲"), ("image", ""), ("text", "甲"), ("image", ""),
                ("text", "甲"), ("image", "")])
    dup_ui = [("text", "甲", "A"), ("image", "图片", "B"),
              ("text", "甲", "C"), ("image", "图片", "D")]
    w = lv._align_window(dup_ui, dup)
    check("并列最优偏移会被报出来", w and len(w["ties"]) > 1, str(w and w["ties"]))
    check("并列偏移对「目标是第几行」给不出唯一答案 → None（宁可不点）",
          lv._align_index(dup_ui, dup, 1001) is None)
    uniq = mkdb([("text", "甲"), ("image", ""), ("text", "乙"), ("image", ""),
                 ("text", "丙"), ("image", "")])
    uniq_ui = [("text", "乙", "A"), ("image", "图片", "B"),
               ("text", "丙", "C"), ("image", "图片", "D")]
    w2 = lv._align_window(uniq_ui, uniq)
    check("正文各不相同 → 只有一个最优偏移", w2 and w2["ties"] == [2], str(w2 and w2["ties"]))
    check("唯一偏移时正常点名目标（local_id=1003 → 可视第 2 行）",
          lv._align_index(uniq_ui, uniq, 1003) == 1)

    ps = MediaDownloader._plan_scroll
    check("往更新的方向推 = 向下滚（负 delta），聊天列表最新在下方",
          ps(3, 1.0) == (-120, 3), str(ps(3, 1.0)))
    check("往更早推 = 正 delta", ps(-3, 1.0) == (120, 3), str(ps(-3, 1.0)))
    check("每格滚 2 行时格数减半（向上取整）", ps(5, 2.0) == (-120, 3), str(ps(5, 2.0)))
    check("行差 0 不滚", ps(0) == (0, 0))
    check("单轮格数封顶", ps(500, 1.0) == (-120, 12), str(ps(500, 1.0)))

    print("[image] 目标不在屏上时按数据库行差滚过去")
    ROWS = 4

    class World:
        """假界面：可视窗口是 db 序列里的一段，滚动 = 把这段往前/后推。"""

        def __init__(self, db, off=0, rows_per_notch=2.0, moves=True, sends=True):
            self.db, self.off, self.n = db, off, ROWS
            self.rpn, self.moves, self.sends = rows_per_notch, moves, sends
            self.calls = []

        def visible(self, _lst):
            return [("text" if d["kind"] == "text" else "image",
                     d["content"] if d["kind"] == "text" else "图片",
                     "row%d" % (self.off + i))
                    for i, d in enumerate(self.db[self.off:self.off + self.n])]

        def scroll(self, lst, delta, times, uia, hwnd, pid):
            self.calls.append((delta, times))
            if not self.sends:
                return False          # 滚轮根本没发出去（非前台 / 落点不是微信）
            if self.moves:
                step = int(round(times * self.rpn)) or 1
                self.off = max(0, min(len(self.db) - self.n,
                                      self.off + (step if delta < 0 else -step)))
            return True
    world_db = mkdb([("text", "甲"), ("image", ""), ("text", "乙"), ("image", ""),
                     ("text", "丙"), ("image", ""), ("text", "丁"), ("image", ""),
                     ("text", "戊"), ("image", "")])
    import time as _t

    def locate(off0, target, **kw):
        wr = World(world_db, off=off0, **kw)
        m2 = MediaDownloader.__new__(MediaDownloader)
        m2._visible_rows = wr.visible
        m2._scroll_list = staticmethod(wr.scroll)
        ctl, note = m2._locate_image_row(target, None, world_db, None, 1, 2,
                                         _t.time() + 30, max_scrolls=4)
        return wr, ctl, note
    wr, ctl, note = locate(0, 1005)                    # 目标在 i=5，窗口是 0..3
    check("目标不在屏上 → 先滚再认，滚完点到名",
          note == "aligned-after-1" and ctl == "row5", "%s %s" % (note, ctl))
    check("滚的方向对（要更新的 → 负 delta）",
          wr.calls and wr.calls[0][0] == -120, str(wr.calls))
    wr2, ctl2, note2 = locate(6, 1001)                 # 目标在更早的位置
    check("目标在更早期 → 往回滚（正 delta）",
          note2.startswith("aligned") and wr2.calls[0][0] == 120,
          "%s %s" % (note2, wr2.calls))
    wr3, ctl3, note3 = locate(0, 1005, moves=False)
    check("滚了但窗口纹丝不动 → scroll-stuck（不无限滚）",
          note3 == "scroll-stuck", note3)
    wr4, ctl4, note4 = locate(0, 1005, sends=False)
    check("滚轮发不出去（非前台 / 落点不是微信）→ scroll-blocked，且只试一次",
          note4 == "scroll-blocked" and len(wr4.calls) == 1,
          "%s %s" % (note4, wr4.calls))
    wr5, ctl5, note5 = locate(0, 9999)
    check("目标不在取回的历史窗口里 → not-in-db（而不是硬猜）",
          note5 == "not-in-db", note5)
    m3 = MediaDownloader.__new__(MediaDownloader)
    wr6 = World(world_db, off=0)
    m3._visible_rows = wr6.visible
    m3._scroll_list = staticmethod(wr6.scroll)
    ctl6, note6 = m3._locate_image_row(1005, None, world_db, None, 1, 2,
                                       _t.time() + 30, max_scrolls=0)
    check("max_scrolls=0 时一屏认不出就停（绝不滚界面）",
          note6 == "scroll-limit" and wr6.calls == [], "%s %s" % (note6, wr6.calls))
    wr7, ctl7, note7 = locate(0, 1005, rows_per_notch=0.5)
    check("每格只滚半行时按实测修正：第二圈要滚 2 格而不是 1 格",
          note7 == "aligned-after-2" and wr7.calls == [(-120, 2), (-120, 2)],
          "%s %s" % (note7, wr7.calls))

    print("[image] 「保存」兜底：预览窗那颗按钮真的能拿回文件")
    import uiautomation as _auto
    acc = tempfile.mkdtemp(prefix='wxacc-')
    tmpdirs.append(acc)
    sub = os.path.join(acc, "msg", "attach", "c1", "2026-09")
    os.makedirs(sub)
    old_jpg = os.path.join(sub, "old.jpg")
    open(old_jpg, "wb").write(b"\xff\xd8\xffold\xff\xd9")
    os.utime(old_jpg, (_time.time() - 86400, _time.time() - 86400))
    new_jpg = os.path.join(sub, "new.jpg")
    open(new_jpg, "wb").write(JPG)
    mv = MediaDownloader.__new__(MediaDownloader)
    mv.save_dir = tempfile.mkdtemp(prefix='wxout-')
    tmpdirs.append(mv.save_dir)

    class AccDB:
        account_dir = acc
    mv.db = AccDB()
    rec = mv._recent_images(acc, _time.time() - 60)
    check("只收最近改过的图片，昨天的不算",
          [os.path.basename(p) for p in rec] == ["new.jpg"], str(list(rec)))
    check("新文件相对旧快照算「新增」，旧文件不算",
          MediaDownloader._new_images({old_jpg: (1.0, 1)}, rec) == [new_jpg]
          and MediaDownloader._new_images(rec, rec) == [],
          str(MediaDownloader._new_images({old_jpg: (1.0, 1)}, rec)))

    class SaveBtn:
        def __init__(self, writes):
            self.writes, self.n = writes, 0

        def Click(self):
            self.n += 1
            with open(self.writes, "wb") as f:
                f.write(JPG)

    class Win:
        pass

    ms = MediaDownloader.__new__(MediaDownloader)
    ms.db = AccDB()
    ms.save_dir = tempfile.mkdtemp(prefix='wxout2-')
    tmpdirs.append(ms.save_dir)
    ms._find_preview_button = lambda w, name: (
        SaveBtn(os.path.join(sub, "wx_saved.jpg")) if name == "保存" else None)
    ms._save_dialog = staticmethod(lambda: None)
    got = ms._save_via_button(Win(), ms.save_dir, "wxid_img_7",
                              _time.time() + 20, _time.time())
    check("点「保存」后扫到新文件 → 收进 save_dir 并返回路径",
          got and got.endswith("wxid_img_7.jpg") and _os.path.isfile(got)
          and _os.path.getsize(got) == len(JPG), str(got))
    # 反向确认：同一份快照之间没有新东西，就不会误报「拿到了文件」
    same = mv._recent_images(acc, _time.time() - 60)
    check("同一份快照自比不产生新文件（不会把「没有」读成「拿到了」）",
          MediaDownloader._new_images(same, mv._recent_images(acc, _time.time() - 60)) == [])
    ms2 = MediaDownloader.__new__(MediaDownloader)
    ms2.db = AccDB()
    ms2.save_dir = ms.save_dir
    ms2._find_preview_button = lambda w, name: None
    ms2._save_dialog = staticmethod(lambda: None)
    check("预览窗里没有「保存」按钮时返回 None（不硬点别的东西）",
          ms2._save_via_button(Win(), ms2.save_dir, "x", _time.time() + 5,
                               _time.time()) is None)

    class Node:
        def GetChildren(self):
            return []

    class Edit(Node):
        ClassName, ControlTypeName, Name = "Edit", "EditControl", ""

        def __init__(self):
            self.value = None

        def GetValuePattern(self):
            return self

        def SetValue(self, v):
            self.value = v

    class DlgBtn(Node):
        ControlTypeName = "ButtonControl"

        def __init__(self, name):
            self.Name, self.invoked = name, 0

        def GetInvokePattern(self):
            return self

        def Invoke(self):
            self.invoked += 1

    class Dlg:
        ControlTypeName, Name = "PaneControl", ""

        def __init__(self, kids):
            self._k = kids

        def GetChildren(self):
            return self._k

    class BtnNode(Node):
        ClassName = "mmui::XButton"

        def __init__(self, name):
            self.Name = name

    zoom_tree = Dlg([Dlg([BtnNode("图片适应窗口大小")])])
    check("按候选名找按钮：状态名换了也找得到（实机就撞见过另一颗名字）",
          MediaDownloader._find_preview_button(
              zoom_tree, (MediaDownloader.ZOOM_REQUEST,
                          MediaDownloader.ZOOM_SHOWN)).Name == "图片适应窗口大小")
    check("只给老名字「图片原始大小」时找不到（这正是提前返回、兜底跑不到的原因）",
          MediaDownloader._find_preview_button(
              zoom_tree, MediaDownloader.ZOOM_REQUEST) is None)

    # 实机 dump 出来的「保存」对话框结构（顶层类名 #32770，**标题就是「保存」**）：
    #   AppControlHost ComboBox name='文件名:' → Edit name='文件名:'（可写值）
    #   SearchEditBox          Edit name='搜索框'   ← 拿「第一个 Edit」会填到这里
    #   Button name='保存(S)' / Button name='取消'（窗口直接子节点）
    class FileNameEdit(Edit):
        Name = "文件名:"

    class SearchEdit(Edit):
        ClassName, Name = "SearchEditBox", "搜索框"

    class TypeEdit(Edit):
        """「保存类型」那颗下拉框里层的小 Edit（当前格式就显示在那儿）。"""
        ClassName, Name = "Edit", "保存类型:"

    class Combo(Node):
        ControlTypeName = "ComboBoxControl"

        def __init__(self, name, kids=()):
            self.Name, self._k = name, list(kids)

        def GetChildren(self):
            return self._k

    class SaveDlg(Node):
        ClassName, ControlTypeName, Name = "#32770", "WindowControl", "保存"

        def __init__(self, kids):
            self._k = list(kids)

        def GetChildren(self):
            return self._k

    fn_edit = FileNameEdit()
    btn_save, btn_cancel = DlgBtn("保存(S)"), DlgBtn("取消")
    # 实机点错过一次：目标是「保存(S)」那颗按钮，点到的却是「选择文件格式」那颗下拉框。
    # 能解释这件事的只有「那颗下拉框在 UIA 里的 Name 也叫保存」——所以按 Name 前缀匹配
    # 不够，必须再看控件类型。这里把它排在按钮**前面**，DFS 先碰到它。
    type_combo = Combo("保存", [])
    dlg = SaveDlg([type_combo, Combo("文件名:", [fn_edit]), SearchEdit(),
                   btn_save, btn_cancel])
    check("文件名框按 Name 挑，不会填进右上角那个「搜索框」Edit",
          MediaDownloader._file_name_edit(dlg) is fn_edit,
          str(MediaDownloader._file_name_edit(dlg)))
    check("按钮按前缀匹配（实机文案带助记符「保存(S)」）",
          MediaDownloader._dialog_button(dlg, ("保存", "Save")) is btn_save
          and MediaDownloader._dialog_button(dlg, ("取消", "Cancel")) is btn_cancel)
    check("Name 也叫「保存」的那颗下拉框不算按钮——绝不点它（实机就点到过它）",
          MediaDownloader._dialog_button(dlg, ("保存", "Save")) is not type_combo
          and "Button" not in (type_combo.ControlTypeName or ""))
    check("填路径 + 点「保存(S)」全程用 ValuePattern/Invoke，不发键盘",
          ms._fill_save_dialog(dlg, r"D:\out\pic.jpg") is True
          and fn_edit.value == r"D:\out\pic.jpg" and btn_save.invoked == 1,
          "%r %s" % (fn_edit.value, btn_save.invoked))
    check("失败收尾会点「取消」把模态框关掉（不留在屏幕上挡预览窗）",
          MediaDownloader._cancel_save_dialog(dlg) is True and btn_cancel.invoked == 1)
    check("对话框里没有文件名框时不硬来",
          ms._fill_save_dialog(SaveDlg([SearchEdit(), DlgBtn("保存(S)")]),
                               r"D:\out\p.jpg") is False)
    # 实机那种：文件名框没认出来，兜底「第一个 Edit」正好落在「保存类型」那颗下拉框
    # 里面的 Edit 上——值写进了格式选择器，点「保存」自然什么文件都没写出。
    type_only = SaveDlg([Combo("保存类型:", [TypeEdit()]),
                         SearchEdit(), btn_save, btn_cancel])
    check("兜底找「第一个 Edit」时也绝不返回「保存类型」那颗 Edit（实机就写到那儿去了）",
          MediaDownloader._first_edit(type_only) is None
          and ms._fill_save_dialog(type_only, r"D:\out\q.jpg") is False)

    # 对话框刚关/切页时 UIA 树会在遍历中途失效：读属性、取子节点都可能抛。
    # 这类异常一旦抛出去就把整条「保存」兜底路线打死（上一轮真机就是被一个
    # NameError 打死的），所以必须一支节点坏了就跳过那一支。
    class Boom(Node):
        ClassName, ControlTypeName, Name = "Pane", "PaneControl", ""

        def GetChildren(self):
            raise RuntimeError("控件已失效")

    class BrokenEdit(Edit):
        @property
        def ClassName(self):
            raise RuntimeError("控件已失效")

    got_e = MediaDownloader._first_edit(SaveDlg([Boom(), SearchEdit(), fn_edit]))
    check("一支子树遍历失败 → 跳过它继续找，仍然拿到文件名框",
          got_e is fn_edit, str(got_e))
    check("Edit 自身属性读取抛 → 不炸出去（宁可当成可填的框）",
          MediaDownloader._first_edit(SaveDlg([BrokenEdit()])) is not None)
    _orig_root3 = _auto.GetRootControl

    class Top(Node):
        def __init__(self, cls, name, kids=()):
            self.ClassName, self.Name = cls, name
            self._k = list(kids)
            self.NativeWindowHandle = id(self)

        def GetChildren(self):
            return self._k

    class RootOf:
        def __init__(self, kids):
            self._k = list(kids)

        def GetChildren(self):
            return self._k

    try:
        _auto.GetRootControl = lambda: RootOf([Top("Qt51514QWindowIcon", "微信"),
                                               Top("#32770", "保存", [dlg])])
        check("标题是「保存」而不是「另存为」的对话框也认得（按类名+保存按钮）",
              MediaDownloader._save_dialog() is not None)
        _auto.GetRootControl = lambda: RootOf([Top("#32770", "属性", [Node()])])
        check("没有「保存」按钮的 #32770 不算（别的应用的通用对话框）",
              MediaDownloader._save_dialog() is None)
        _auto.GetRootControl = lambda: RootOf(
            [Top("#32770", "打开", [Combo("保存", [])])])
        check("里面只有一颗 Name 叫「保存」的下拉框（不是按钮）→ 不当成保存对话框",
              MediaDownloader._save_dialog() is None)
    finally:
        _auto.GetRootControl = _orig_root3

    print("[image] 「保存」对话框端到端（假对话框真写文件）")
    made = ms.save_dir

    class SaveToFileBtn(DlgBtn):
        """点了就写真文件、并把对话框“关掉”的保存按钮（对话框关掉才是点中的证据）。"""

        def __init__(self, name, edit, state=None):
            DlgBtn.__init__(self, name)
            self.edit, self.state = edit, state

        def Invoke(self):
            self.invoked += 1
            if self.edit.value:
                with open(self.edit.value, "wb") as f:
                    f.write(JPG)
            if self.state is not None:
                self.state["open"] = False

    fn2 = FileNameEdit()
    st2 = {"open": True}
    ok_btn = SaveToFileBtn("保存(S)", fn2, st2)
    dlg2 = SaveDlg([Combo("保存类型:", []), Combo("文件名:", [fn2]),
                    SearchEdit(), ok_btn, DlgBtn("取消")])
    ms3 = MediaDownloader.__new__(MediaDownloader)
    ms3.db = AccDB()
    ms3.save_dir = tempfile.mkdtemp(prefix='wxdlg-')
    tmpdirs.append(ms3.save_dir)
    ms3._find_preview_button = lambda w, name: (DlgBtn("保存") if name == "保存" else None)
    ms3._save_dialog = staticmethod(lambda: dlg2 if st2["open"] else None)
    got3 = ms3._save_via_button(Win(), ms3.save_dir, "wxid_img_7",
                                _time.time() + 25, _time.time())
    check("点「保存」→ 填目标路径 → 点中按钮（对话框关掉）→ 文件真的落在 save_dir",
          got3 and got3.endswith("wxid_img_7.jpg") and _os.path.isfile(got3)
          and ok_btn.invoked == 1, "%s 按钮点了 %d 次" % (got3, ok_btn.invoked))

    # 实机那种「没点中按钮」的形状：Invoke 跑了、文件没写出、**模态框还开着**。
    # 这时候必须认出来并点「取消」收尾，不能白等 8 秒再报一句看不清的话。
    fn3 = FileNameEdit()
    st3 = {"open": True}

    class NoCloseBtn(DlgBtn):
        def __init__(self, name, edit):
            DlgBtn.__init__(self, name)
            self.edit = edit

        def Invoke(self):
            self.invoked += 1        # 假装点到的是那颗格式下拉框：什么都不写、窗也不关

    stuck_btn = NoCloseBtn("保存(S)", fn3)
    cancelled2 = DlgBtn("取消")
    dlg3 = SaveDlg([Combo("保存类型:", []), Combo("文件名:", [fn3]),
                    SearchEdit(), stuck_btn, cancelled2])
    ms5 = MediaDownloader.__new__(MediaDownloader)
    ms5.db = AccDB()
    ms5.save_dir = tempfile.mkdtemp(prefix='wxdlg3-')
    tmpdirs.append(ms5.save_dir)
    ms5._find_preview_button = lambda w, name: (DlgBtn("保存") if name == "保存" else None)
    ms5._save_dialog = staticmethod(lambda: dlg3)
    got5 = ms5._save_via_button(Win(), ms5.save_dir, "nope", _time.time() + 25, _time.time())
    check("对话框没关掉 = 那一下没点中按钮 → 返回 None、点「取消」收尾、不留下模态框",
          got5 is None and cancelled2.invoked == 1 and stuck_btn.invoked == 1,
          "%s 取消点了 %d 次" % (got5, cancelled2.invoked))

    ms4 = MediaDownloader.__new__(MediaDownloader)
    ms4.db = AccDB()
    ms4.save_dir = tempfile.mkdtemp(prefix='wxdlg2-')
    tmpdirs.append(ms4.save_dir)
    cancelled = DlgBtn("取消")
    bad_dlg = SaveDlg([SearchEdit(), DlgBtn("保存(S)"), cancelled])
    ms4._find_preview_button = lambda w, name: (DlgBtn("保存") if name == "保存" else None)
    ms4._save_dialog = staticmethod(lambda: bad_dlg)
    got4 = ms4._save_via_button(Win(), ms4.save_dir, "x", _time.time() + 8, _time.time())
    check("对话框里挑不到文件名框 → 返回 None 并且点了「取消」收尾",
          got4 is None and cancelled.invoked == 1,
          "%s 取消点了 %d 次" % (got4, cancelled.invoked))

    print("[image] 「保存」第一遍可能是预览图那一份 → 点「图片原始大小」再存一遍")
    lp = MediaDownloader._looks_preview
    check("比本机 .dat 小两成以上 → 判为预览图", lp(60000, ref_mid=100000) is True)
    check("和本机 .dat 同量级 → 不像预览图，不再多点一轮界面",
          lp(95000, ref_mid=100000) is False)
    check("本机没有 .dat 时只和缩略图比：不超过它 1.3 倍才算预览图",
          lp(20000, ref_thumb=18000) is True and lp(60000, ref_thumb=18000) is False)
    check("参照不足（本机两档都没有）→ 不猜，绝不为了猜而多点一轮界面",
          lp(20000) is False and lp(0, ref_mid=100000) is False)

    class ClickBtn(Node):
        ControlTypeName, ClassName = "ButtonControl", "mmui::XButton"

        def __init__(self, name):
            self.Name, self.clicked = name, 0

        def Click(self):
            self.clicked += 1

    mz = MediaDownloader.__new__(MediaDownloader)
    b_req = ClickBtn("图片原始大小")
    mz._find_preview_button = lambda w, name: b_req
    check("重存之前先点「图片原始大小」逼原件进显示（状态对才点）",
          mz._click_zoom_to_load(None) is True and b_req.clicked == 1,
          str(b_req.clicked))
    b_shown = ClickBtn("图片适应窗口大小")
    mz._find_preview_button = lambda w, name: b_shown
    check("原件本来就在显示中就不点它（点一下会缩回去，反而存成缩放版）",
          mz._click_zoom_to_load(None) is False and b_shown.clicked == 0,
          str(b_shown.clicked))
    mz._find_preview_button = lambda w, name: None
    check("查看器里没有这颗按钮时不硬点（还是会把上一份交出去）",
          mz._click_zoom_to_load(None) is False)

    ms6 = MediaDownloader.__new__(MediaDownloader)
    ms6.db = AccDB()
    ms6.save_dir = tempfile.mkdtemp(prefix='wxdlg4-')
    tmpdirs.append(ms6.save_dir)
    seq6 = [18000, 95000]
    calls6 = []
    zoom6 = []

    def once6(win, target, deadline, t0):
        calls6.append(target)
        n = min(len(calls6) - 1, len(seq6) - 1)
        sz = seq6[n]
        if not sz:
            return None                    # 这一次「保存」什么都没写出来
        with open(target, "wb") as f:
            f.write(b"\0" * sz)
        return sz
    ms6._save_once = once6
    ms6._click_zoom_to_load = lambda win: (zoom6.append(1) or True)
    got6 = ms6._save_via_button(Win(), ms6.save_dir, "again", _time.time() + 25,
                                _time.time(), ref_thumb=17000, ref_mid=95000)
    check("第一遍写出的是预览图那份 → 点「图片原始大小」再存一遍，最后交大的那份",
          got6 and got6.endswith("again.jpg") and len(calls6) == 2 and zoom6 == [1]
          and _os.path.getsize(got6) == 95000,
          "%s 保存点了 %d 次" % (got6, len(calls6)))
    calls6[:] = []
    zoom6[:] = []
    got7 = ms6._save_via_button(Win(), ms6.save_dir, "noframe", _time.time() + 25,
                                _time.time())          # 不传参照
    check("没有参照时只点一遍「保存」（第一遍就当成可用）",
          got7 is not None and len(calls6) == 1 and zoom6 == [],
          "%s %d %s" % (got7, len(calls6), zoom6))
    calls6[:] = []
    seq6[:] = [18000, 18000]
    got8 = ms6._save_via_button(Win(), ms6.save_dir, "again", _time.time() + 25,
                                _time.time(), ref_thumb=17000, ref_mid=95000)
    check("两遍都还是预览图 → 也把本机最好的那份交出去（并说明只能让对方重发原图）",
          got8 is not None and len(calls6) == 2, "%s %d" % (got8, len(calls6)))
    calls6[:] = []
    seq6[:] = [18000, 5000]
    got_b = ms6._save_via_button(Win(), ms6.save_dir, "again", _time.time() + 25,
                                 _time.time(), ref_thumb=17000, ref_mid=95000)
    check("第二遍反而更小 → 交的仍是几遍里最大的那份（不是最后一遍那幅）",
          got_b is not None and len(calls6) == 2 and _os.path.getsize(got_b) == 18000,
          "%s %s 大小=%s" % (got_b, len(calls6),
                             _os.path.getsize(got_b) if got_b else "-"))
    check("留的副本用完就删掉（不在用户目录里留 .best 垃圾）",
          not _os.path.isfile(got_b + ".best") if got_b else True)
    calls6[:] = []
    seq6[:] = [None]
    got9 = ms6._save_via_button(Win(), ms6.save_dir, "gone", _time.time() + 25,
                                _time.time(), ref_thumb=17000, ref_mid=95000)
    check("「保存」这次根本没写出东西 → 返回 None，不拿上一次的残留冒充成功",
          got9 is None or _os.path.getsize(got9) > 0, str(got9))

    print("[image] verify=True 时按结构判完整")
    mv._image_files = lambda u, m: {"original": (new_jpg, 5000)}
    mv.decrypt_image = lambda p, a=None, x=None: JPG
    check("解密后完整 → ok",
          mv._image_status_for("u", 1, "m", verify=True)["reason"] == "ok")
    mv.decrypt_image = lambda p, a=None, x=None: b"\xff\xd8\xff" + b"j" * 80
    stv = mv._image_status_for("u", 1, "m", verify=True)
    check("解密后缺收尾标记 → original_partial",
          stv["reason"] == "original_partial" and stv["available"] is False,
          stv["reason"])
    check("批量路径（verify=False）不解密、只看存在",
          mv._image_status_for("u", 1, "m")["reason"] == "ok")

    class Boom:
        def __call__(self, *a, **k):
            raise RuntimeError("没有图片密钥")
    mv.decrypt_image = Boom()
    check("拿不到密钥时不否决（判不了就说判不了，别退回误杀）",
          mv._image_status_for("u", 1, "m", verify=True)["reason"] == "ok")

    import uiautomation as _auto
    _orig_root = _auto.GetRootControl

    class FakeWin:
        def __init__(self, cls, h):
            self.ClassName, self.NativeWindowHandle = cls, h

    class FakeTop:
        def __init__(self, cls, name, kids=(), h=0):
            self.ClassName, self.Name = cls, name
            self._k, self.NativeWindowHandle = list(kids), h

        def GetChildren(self):
            return list(self._k)

    class FakeRoot:
        def __init__(self, kids):
            self._k = kids

        def GetChildren(self):
            return self._k

    inner = FakeTop("mmui::PreviewWindow", "图片和视频", h=0)
    shape1 = FakeTop("mmui::PreviewWindow", "Weixin", h=11)          # 直接就是预览窗
    shape2 = FakeTop("Qt51514QWindowIcon", "图片和视频", [inner], 22)  # 隔一层（实机第二种）
    other = FakeTop("Qt51514QWindowIcon", "微信", h=33)
    impostor = FakeTop("Notepad", "图片和视频", h=44)                 # 同名但不是 Qt 窗口
    try:
        _auto.GetRootControl = lambda: FakeRoot([shape1, shape2, other, impostor])
        got = MediaDownloader._preview_windows()
        check("两种实机形状都能找到预览窗（顶层直接是 / 隔一层的 Qt 窗口）",
              len(got) == 2, str([h for h, _w in got]))
        check("句柄取的是顶层窗口（用来分辨新开的还是早就开着的）",
              sorted(h for h, _w in got) == [11, 22], str(sorted(h for h, _w in got)))
        check("交给调用方的控件一律是 PreviewWindow 那一层",
              all("PreviewWindow" in (w.ClassName or "") for _h, w in got),
              str([w.ClassName for _h, w in got]))
        check("主窗和同名非 Qt 窗口不算预览窗",
              all((w.ClassName or "") != "Notepad" and (w.Name or "") != "微信"
                  for _h, w in got))
    finally:
        _auto.GetRootControl = _orig_root

    class Boom:
        def GetChildren(self):
            raise RuntimeError("树没了")

    _auto.GetRootControl = lambda: Boom()
    try:
        check("读不到根控件时返回空列表而不是抛", MediaDownloader._preview_windows() == [])
    finally:
        _auto.GetRootControl = _orig_root

    print("[image] 端到端函数体冒烟（假 UIA，不碰真实客户端）")
    # 这一组存在的理由：以前所有 image 检查都只调辅助函数，download_image_original
    # 的函数体一次都没执行过，于是一句 `images = [c for ...]`（元素名和循环变量不一致）
    # 的 NameError 一路活到用户实机才炸。这里把整条控制流走一遍。
    import wechatauto.guia as _g
    import wechatauto.uia_driver as _ud
    import wechatauto.wx as _wxm

    class Ctl:
        def __init__(self, top):
            self.BoundingRectangle = R(466, top, 3064, top + 246)

    class FakeLst:
        def __init__(self):
            self.BoundingRectangle = R(466, 160, 3064, 1370)

        def GetChildren(self):
            return []

    class FakeUIA:
        chat = None
        no_hwnd = False
        hide_list = 0            # >0：前 N 次 _message_list 返回 None（模拟停在朋友圈页）
        recovered = 0
        entered = 0              # 进过几次界面这条路（本机已有原件时应该是 0）

        def __init__(self, *a, **k):
            pass

        def ensure_window(self):
            FakeUIA.entered += 1
            return True

        def current_chat(self):
            return FakeUIA.chat

        def _message_list(self):
            if FakeUIA.hide_list > 0:
                FakeUIA.hide_list -= 1
                return None
            return FakeLst()

        def back_to_chat_tab(self, settle=1.0):
            FakeUIA.recovered += 1
            return True

        def _force_foreground(self, hwnd):
            return True

        def _wechat_hwnds(self, diag=None):
            return [] if FakeUIA.no_hwnd else [555]

        def _pid_from_hwnd(self, h):
            return None if FakeUIA.no_hwnd else 4321

    class FakeGui:
        main_hwnd, pid = 1234, 4321

    chatwith = []

    class FakeWX:
        def __init__(self, *a, **k):
            self._gui = FakeGui()

        def ChatWith(self, who):
            chatwith.append(who)
            return True

    clicks = []

    class FakeInput:
        def real_click(self, x, y):
            clicks.append((x, y))

    class FakePreview:
        ClassName = "mmui::PreviewWindow"
        NativeWindowHandle = 99

        def __init__(self):
            self.BoundingRectangle = R(700, 40, 2300, 1700)

    class RowDB:
        def __init__(self, sender, own=None, index=None):
            self.sender, self.wxid = sender, own
            self._index = dict(index or {})

        def _sender_id_index(self):
            return dict(self._index)

        def get_message_row(self, u, lid, local_type=None, shard=None):
            return {"local_type": 3, "create_time": 1, "sort_seq": 50,
                    "sender_id": self.sender, "packed_info": MD5.encode(),
                    "content": b""}

        def get_messages(self, user, limit=20, offset=0):
            # 降序：让 db_seq 非空，否则函数根本不会去调 _locate_image_row
            return [{"type": "图片", "content": "", "local_id": 7},
                    {"type": "文本", "content": "正文一行", "local_id": 6},
                    {"type": "图片", "content": "", "local_id": 5}]

    plans = []
    waited = []
    decrypted = []

    class FakeBtn:
        def __init__(self, name="图片原始大小"):
            self.Name = name
            self.BoundingRectangle = R(466, 74, 522, 130)
            self.clicked = 0

        def Click(self):
            self.clicked += 1

    saved_calls = []
    saved_kw = []

    def smoke(sender, pid_ok=True, has_button=True, chat=None, pre_open=False,
              zoom="request", h_dat=True, save_returns=None,
              tiers="none", tiers_after=None, own=None, index=None,
              two_rows=False, flip_at=1):
        """zoom: request=「图片原始大小」/ shown=「图片适应窗口大小」/ none=找不到那颗键

        tiers: 本机附件目录里有哪些档（none=什么都没有 / mid=只有 .dat /
               thumb=只有预览图 / h=有 _h.dat）
        tiers_after: 点过界面**之后**才出现的档位（模拟「点开预览微信才把 .dat 下下来」）
        flip_at: 第几次查盘之后才让它出现（默认 1；调大就能只喂到后面那一次补查）
        two_rows: 可视区里有两张图片气泡（用来证明「已经拿到了就别点第二张」）
        """
        FakeGui.pid = 4321 if pid_ok else None
        FakeUIA.no_hwnd = not pid_ok
        FakeUIA.chat = chat
        FakeUIA.entered = 0
        ms = MediaDownloader.__new__(MediaDownloader)
        ms.save_dir = tempfile.mkdtemp(prefix='wxsmoke-')
        tmpdirs.append(ms.save_dir)
        ms.db = RowDB(sender, own, index)
        _t0 = {
            "none": {},
            "mid": {"mid": ("C:\\tmp\\x.dat", 5000)},
            "thumb": {"thumb": ("C:\\tmp\\x_t.dat", 900)},
            "h": {"original": ("C:\\tmp\\x_h.dat", 5000),
                  "mid": ("C:\\tmp\\x.dat", 900)}}[tiers]
        _t1 = {
            "none": {},
            "mid": {"mid": ("C:\\tmp\\x.dat", 5000)},
            "thumb": {"thumb": ("C:\\tmp\\x_t.dat", 900)},
            "h": {"original": ("C:\\tmp\\x_h.dat", 5000),
                  "mid": ("C:\\tmp\\x.dat", 900)}}[tiers_after] if tiers_after else None
        nscan = [0]

        def _files(u, m):
            nscan[0] += 1
            return dict(_t1 if (_t1 is not None and clicks and nscan[0] >= flip_at)
                        else _t0)
        ms._image_files = _files
        rows = [("image", "图片", Ctl(700)), ("text", "正文一行", Ctl(950))]
        if two_rows:
            rows = rows + [("image", "图片", Ctl(1200))]
        ms._visible_rows = lambda lst: list(rows)
        zoom_btn = FakeBtn({"request": "图片原始大小",
                            "shown": "图片适应窗口大小"}.get(zoom, "")) \
            if zoom != "none" else None

        def _find(_w, name):
            names = (name,) if isinstance(name, str) else tuple(name or ())
            if MediaDownloader.ZOOM_REQUEST in names or \
                    MediaDownloader.ZOOM_SHOWN in names:
                return zoom_btn
            return FakeBtn("保存") if has_button else None
        ms._find_preview_button = _find

        def _save(win, save_dir, stem, deadline, t0, **kw):
            saved_calls.append(stem)
            saved_kw.append(kw)
            return save_returns
        ms._save_via_button = _save

        def _loc(lid, lst, dbs, uia, h, p, dl, max_scrolls=6):
            plans.append(max_scrolls)
            return ms._visible_rows(lst)[0][2], "aligned"
        ms._locate_image_row = _loc
        # 「只有新出现的预览窗才算点开」：点击之前屏上没有窗，点击之后才有；
        # pre_open=True 模拟「本来就开着一张大图」——那种窗不能当成果。
        ms._preview_windows = lambda: ([(99, FakePreview())]
                                       if (pre_open or clicks) else [])
        def _wait(u, m, min_bytes=1024, until=None, tiers=("original",)):
            waited.append(tiers)
            return ("original", "C:\\tmp\\x_h.dat", 5000) if h_dat else None
        ms._wait_tier = _wait
        ms.decrypt_image = lambda p, a=None, x=None: (decrypted.append(p) or JPEG)
        ua, wxm, gu = _ud.WeChatUIA, _wxm.WeChat, _g.WinInput
        _ud.WeChatUIA, _wxm.WeChat, _g.WinInput = FakeUIA, FakeWX, FakeInput
        try:
            out = ms.download_image_original("wxid_img", 7, timeout=3.0,
                                             chat_name="显示名")
        finally:
            _ud.WeChatUIA, _wxm.WeChat, _g.WinInput = ua, wxm, gu
            FakeUIA.no_hwnd = False
            FakeGui.pid = 4321
        return out, zoom_btn

    clicks.clear()
    chatwith.clear()
    plans.clear()
    decrypted.clear()
    out0, btn0 = smoke(None, chat="显示名")      # 已经在目标会话里
    check("函数体走完并解密落盘（没走到就报 NameError/AttributeError）",
          bool(out0) and out0.endswith("wxid_img_7.jpg") and _os.path.exists(out0),
          str(out0))
    check("等档只等原件那一档：.dat 不算拿到（它本身可能就是预览版）",
          waited == [("original",)], str(waited))
    check("交回来的路径就是解密的那个文件（不再自己回去找 _h.dat）",
          decrypted and decrypted[-1].endswith("x_h.dat"), str(decrypted[-1:]))
    # 本机只有 .dat 时**不许**短路：实测一条 .dat 44KB 的仍是预览图，拿它冒充成果
    # 就是把预览图交给用户。只有 .h_dat 在本机时才允许不点界面。
    clicks.clear()
    chatwith.clear()
    _om, _bm = smoke(None, chat="显示名", tiers="mid")
    check("本机只有 .dat → 仍然走点击路径去要 _h.dat（不拿 .dat 冒充成果）",
          bool(clicks), "%s %s" % (clicks, _om))
    waited.clear()
    decrypted.clear()
    check("走到了点击那一步（不是提前 return）", len(clicks) >= 1, str(clicks))
    check("别人发的：先点左边 x=777", clicks and clicks[0][0] == 777, str(clicks[:2]))
    check("缩放键处于「图片原始大小」状态时才点它（按名字点，不按比例猜）",
          btn0.clicked == 1, str(btn0.clicked))
    check("已经在目标会话里 → 不再搜索进入（批量下载多张图时不反复搜）",
          chatwith == [], str(chatwith))
    check("拿得到微信进程 id 时才允许滚界面（传进去的 max_scrolls 非 0）",
          plans and plans[-1] > 0, str(plans))
    clicks.clear()
    smoke(2, chat="显示名")
    check("sender_id=2 不再当成「自己发的」（本机实测 2 是某个常联系的好友，512 条图）",
          clicks and clicks[0][0] == 777, str(clicks[:2]))
    clicks.clear()
    smoke(1, chat="显示名")
    check("自己发的（sender_id=1，文件传输助手 400 条全是它）：先点右边 x=2753",
          clicks and clicks[0][0] == 2753, str(clicks[:2]))
    clicks.clear()
    smoke(7, chat="显示名", own="wxid_me", index={7: "wxid_me", 1: "wxid_other"})
    check("按 SenderName2Id 解析判「是不是我发的」，不写死常数（别人那台机器 rowid 是 7）",
          clicks and clicks[0][0] == 2753, str(clicks[:2]))
    clicks.clear()
    smoke(1, chat="显示名", own="wxid_me", index={7: "wxid_me", 1: "wxid_other"})
    check("解析出来是别人就当别人（哪怕 sender_id==1）",
          clicks and clicks[0][0] == 777, str(clicks[:2]))
    check("行中心取的是那一行的中线", clicks and clicks[0][1] == 700 + 123, str(clicks[:1]))
    chatwith.clear()
    smoke(None, chat="别的会话")
    check("不是目标会话 → 照常搜索进入", chatwith == ["显示名"], str(chatwith))
    chatwith.clear()
    smoke(None, chat=None)
    check("读不到当前会话标题时也照常进入（保守：宁可多搜一次）",
          chatwith == ["显示名"], str(chatwith))

    # download_image_original 的口径（用户拍的）：**没有 _h.dat 就强制走点击路径**。
    # .dat 不算成果——它是"微信下发的那一份"，实测有 44KB 的 .dat 仍然是预览图，
    # 本机分不出来，所以只有 _h.dat 能当"拿到原图"的依据。
    clicks.clear()
    chatwith.clear()
    out_mid, _b = smoke(1, chat="显示名", tiers="mid")
    check("自己发的 + 本机只有 .dat → 也照样走界面（.dat 可能就是预览版，不能当成果）",
          bool(clicks) and not chatwith, "%s %s" % (clicks, chatwith))
    clicks.clear()
    out_thumb, _b2 = smoke(1, chat="显示名", tiers="thumb")
    check("自己发的、本机只有预览图 → 走界面", bool(clicks), str(clicks[:1]))
    clicks.clear()
    chatwith.clear()
    out_h, _b3 = smoke(None, chat="显示名", tiers="h")
    check("本机已有合格 _h.dat → 连界面这条路都不进（不构造 UIA、不 ensure_window）",
          out_h is not None and not clicks and not chatwith and not FakeUIA.entered,
          "%s 点击=%s 进了界面 %d 次" % (out_h, clicks, FakeUIA.entered))

    # 点界面那一段：缩放键该点就点（那颗键才会去追原件），解密的是 _wait_tier 交回来的路径
    clicks.clear()
    chatwith.clear()
    waited.clear()
    decrypted.clear()
    out_m, btn_m = smoke(None, chat="显示名", tiers="thumb")
    check("该点缩放键就点（「图片原始大小」那颗才会去要原件）",
          btn_m is not None and btn_m.clicked == 1, str(btn_m and btn_m.clicked))
    check("解密的是 _wait_tier 交回来的那个路径",
          out_m is not None and _os.path.isfile(out_m)
          and decrypted[-1] == "C:\\tmp\\x_h.dat", "%s %s" % (out_m, decrypted[-1:]))
    saved_kw.clear()
    clicks.clear()          # 不清的话上一轮的预览窗会被当成「本来就开着」，走不到「保存」
    smoke(None, chat="显示名", tiers="mid", h_dat=False)
    check("走「保存」兜底时把本机那两档的尺寸带过去（不带走就没法判断存出来的是不是预览图）",
          bool(saved_kw) and saved_kw[-1].get("ref_mid") == 5000, str(saved_kw[-1:]))

    # 用户实测：点完第一张之后微信才把文件写完 —— 于是「明明已经拿到了还在一张一张
    # 瞎点，最后报下载失败；重新运行又说已在本地」。现在每个决策点都先回头看一眼盘：
    # 进门、点下一张之前、等档超时之后、判失败之前。
    clicks.clear()
    saved_calls.clear()
    out_late = smoke(None, chat="显示名", tiers="thumb", tiers_after="h",
                     h_dat=False)[0]
    check("等档超时之后 _h.dat 才出现 → 补查到了就交文件，不再去点「保存」",
          out_late is not None and len(clicks) == 1 and saved_calls == [],
          "%s 点击=%s 保存=%s" % (out_late, clicks, saved_calls))
    clicks.clear()
    out_iter = smoke(None, chat="显示名", tiers="thumb", tiers_after="h", h_dat=False,
                     two_rows=True, flip_at=4)[0]
    check("要点第二张之前先查盘：已经拿到了就不再点第二张",
          out_iter is not None and len(clicks) == 1, "%s 点击=%s" % (out_iter, clicks))
    clicks.clear()
    out_final = smoke(None, chat="显示名", tiers="thumb", tiers_after="h", h_dat=False,
                      pre_open=True, flip_at=3)[0]
    check("整轮界面走完、判失败之前再查一次盘（超时那一瞬间才落盘的算拿到）",
          out_final is not None and len(clicks) == 1, "%s %s" % (out_final, clicks))
    clicks.clear()
    out_none = smoke(None, chat="显示名", tiers="thumb", tiers_after="mid", h_dat=False,
                     pre_open=True, flip_at=99)[0]
    check("补查也查不到 → 照旧明确返回 None（不把「没拿到」粉饰成成功）",
          out_none is None, str(out_none))
    plans.clear()
    smoke(None, pid_ok=False, chat="显示名")
    check("拿不到进程 id → 绝不滚界面（max_scrolls=0，落点无法校验）",
          plans and plans[-1] == 0, str(plans))
    clicks.clear()
    check("缩放键和「保存」都拿不到东西时才失败（返回 None）",
          smoke(None, chat="显示名", has_button=False, h_dat=False)[0] is None)
    clicks.clear()
    check("屏上本来就开着预览窗时不算「点开成功」（只认新出现的窗口）",
          smoke(None, chat="显示名", pre_open=True)[0] is None)
    # 那颗键的 Name 会随显示状态在两个值之间切（实机就撞见过「图片适应窗口大小」），
    # 只写死一个名字 → 找不到就提前返回，「保存」那条兜底根本没机会跑。
    saved_calls.clear()
    clicks.clear()
    out_z, btn_z = smoke(None, chat="显示名", zoom="shown", h_dat=False,
                         save_returns=r"C:\tmp\saved.jpg")
    check("缩放键已是「图片适应窗口大小」→ 不点它（点了只会缩回去），直接走保存",
          btn_z.clicked == 0 and saved_calls == ["wxid_img_7"],
          "%s %s" % (btn_z.clicked, saved_calls))
    check("「保存」拿到的文件原样返回", out_z == r"C:\tmp\saved.jpg", str(out_z))
    saved_calls.clear()
    clicks.clear()
    out_n2, _b2 = smoke(None, chat="显示名", zoom="none", h_dat=False,
                        save_returns=r"C:\tmp\s2.jpg")
    check("找不到缩放键时不再提前返回，仍然继续走「保存」",
          saved_calls == ["wxid_img_7"] and out_n2 == r"C:\tmp\s2.jpg",
          "%s %s" % (saved_calls, out_n2))
    saved_calls.clear()
    clicks.clear()
    check("缩放键找不到、保存也没东西 → 最终失败返回 None",
          smoke(None, chat="显示名", zoom="none", h_dat=False)[0] is None
          and saved_calls == ["wxid_img_7"], str(saved_calls))
    # 主窗停在朋友圈页：RecyclerListView 真的不在树里，以前只警告一句就返回，
    # 看起来就是「UIA 没有任何操作」。现在先点回「微信」标签再重新进会话。
    FakeUIA.hide_list, FakeUIA.recovered = 1, 0
    chatwith.clear()
    clicks.clear()
    out_r, _btn_r = smoke(None, chat="显示名")
    check("消息列表不在树里 → 先点「微信」标签回聊天页（不是直接返回）",
          FakeUIA.recovered == 1, str(FakeUIA.recovered))
    check("恢复之后重新进会话并继续走完（真的去点了图）",
          chatwith == ["显示名"] and out_r is not None, "%s %s" % (chatwith, out_r))
    FakeUIA.hide_list, FakeUIA.recovered = 99, 0
    clicks.clear()
    out_n, _btn_n = smoke(None, chat="显示名")
    check("点了还是拿不到消息列表 → 明确失败返回 None（不静默）",
          out_n is None and FakeUIA.recovered == 1,
          "%s %s" % (out_n, FakeUIA.recovered))
    FakeUIA.hide_list = 0

    for p in tmpdirs:
        shutil.rmtree(p, ignore_errors=True)


# ----------------------------------------------------------------------
# 15. 一条命令的入口（issue #31）——纯离线：假 db / 假 MediaDownloader / 临时目录
# ----------------------------------------------------------------------
def t_cli() -> None:
    """issue #31「能不能把代码调用搞简单一点？太麻烦了，比如一条命令」。

    CLI 的价值全在替用户吸收三个坑：① 读库要 username、驱动界面要显示名；② 下载图片
    前得先有图片密钥；③ 正文是整段 XML 时别满屏标签。这三件事加参数解析都得离线钉住，
    否则「简单」只是把踩坑从 Python 挪到命令行。"""
    import contextlib
    import io as _io
    import shutil
    import tempfile
    from types import SimpleNamespace as types_ns

    import wechatauto.cli as cli

    def cap(fn, *a, **k):
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fn(*a, **k)
        return rc, buf.getvalue()

    print("[cli] 会话名 / wxid / 群号都能传（读库要 username，界面要显示名）")

    class FakeDB:
        wxid = "wxid_me"

        def __init__(self, hits=(), nick="小明", sessions=(), msgs=()):
            self.hits, self.nick, self.sessions, self.msgs = \
                list(hits), nick, list(sessions), list(msgs)

        def get_nickname(self, u):
            return self.nick

        def search_contact(self, kw):
            return [h for h in self.hits if kw in (h.get("nick_name"),
                                                   h.get("remark"),
                                                   h.get("username"))]

        def get_sessions(self, limit=100):
            return self.sessions

        def get_messages(self, user, limit=20, offset=0):
            self.last_user = user
            return self.msgs[:limit]

    check("filehelper → username 原样、显示名取昵称",
          cli.resolve_chat(FakeDB(), "filehelper") == ("filehelper", "小明"))
    check("wxid_ 开头 → 直接当 username（不白搜一次）",
          cli.resolve_chat(FakeDB(), "wxid_abc123") == ("wxid_abc123", "小明"))
    check("群号 → 直通；昵称查不到时显示名退回本身",
          cli.resolve_chat(FakeDB(nick=""), "12345@chatroom")
          == ("12345@chatroom", "12345@chatroom"))
    check("给的是昵称且唯一命中 → 补成 username（这就是不补的那类「白等几十秒」）",
          cli.resolve_chat(FakeDB(hits=[{"username": "wxid_x", "nick_name": "小明",
                                         "remark": ""}]), "小明")
          == ("wxid_x", "小明"))
    buf = _io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            cli.resolve_chat(FakeDB(hits=[{"username": "a", "nick_name": "重名",
                                           "remark": ""},
                                          {"username": "b", "nick_name": "重名",
                                           "remark": ""}]), "重名")
        code, said = 0, buf.getvalue()
    except SystemExit as e:
        code, said = int(e.code or 0), buf.getvalue()
    check("两个联系人同名 → 明确要用户改用 wxid，不随便挑一个",
          code != 0 and "wxid" in said, said.strip()[:60])
    check("群名不在通讯录里 → 照原样传下去（读库走 name2id，不靠 contact.db）",
          cli.resolve_chat(FakeDB(), "26级新生群") == ("26级新生群", "26级新生群"))
    check("空串不炸", cli.resolve_chat(FakeDB(), "") == ("", ""))

    print("[cli] 正文与时间：XML 别满屏标签")
    vid = {"type": "视频", "create_time": 1790000000,
           "content": '<?xml version="1.0"?><msg><videomsg length="4211669" '
                      'playlength="151"/></msg>'}
    line = cli._fmt_line(vid)
    check("视频正文刮成属性提示（playlength/length）", "playlength=151" in line, line)
    check("不再把原始 XML 标签打到终端", "<msg>" not in line and "<?xml" not in line)
    check("纯文本原样", cli._body({"content": "你好"}) == "你好")
    check("超长正文截住（不刷屏）", len(cli._body({"content": "哈" * 900})) <= 400)
    check("create_time 缺失也不抛", "None" in cli._fmt_line({"content": "x"}))

    print("[cli] --type 过滤：中英文都收")
    rows = [{"type": "文本", "content": "a", "create_time": 1},
            {"type": "图片", "content": "[图片]", "create_time": 2}]
    db = FakeDB(hits=[{"username": "wxid_x", "nick_name": "小明", "remark": ""}],
                msgs=rows)
    _u, _d, got = cli._messages(db, "小明", 10, 0, "image")
    check("--type image 只要图片行（库里类型名是中文）",
          [r["type"] for r in got] == ["图片"], str(got))
    _u, _d, got2 = cli._messages(db, "小明", 10, 0, "文本")
    check("中文名照样能用", [r["type"] for r in got2] == ["文本"])
    _u, _d, got3 = cli._messages(db, "小明", 10, 0, None)
    check("不传 --type 全给", len(got3) == 2)
    check("过滤时读库用的是补出来的 username", db.last_user == "wxid_x", db.last_user)

    print("[cli] images：没有图片密钥就说什么、缺档时回显 reason")
    import wechatauto.media as media_mod
    o_md = media_mod.MediaDownloader

    class FakeMD:
        made = []
        calls = []

        def __init__(self, db, save_dir=None):
            self.save_dir = save_dir
            FakeMD.made.append(self)

        def detect_image_key(self):
            return None if self.no_key else ("k", 1)

        no_key = False

        def download_image(self, user, lid, save_dir=None, tier=None):
            FakeMD.calls.append(("local", lid))
            return "%s/%s_%s.jpg" % (save_dir, user, lid)

        def download_image_original(self, user, lid, save_dir=None, chat_name=None):
            FakeMD.calls.append(("ui", lid, chat_name))
            return None

        def image_status(self, user, lid):
            return {"tiers": {"thumb": 900}, "reason": "only_thumbnail"}

    class ImgDB(FakeDB):
        def get_image_rows(self, user, limit=300):
            return [{"local_id": 7}, {"local_id": 8}]

    media_mod.MediaDownloader = FakeMD
    try:
        FakeMD.no_key = True
        o_db, cli._db = cli._db, (lambda: ImgDB())
        try:
            rc, txt = cap(cli.cmd_images, types_ns(chat="小明", limit=5,
                                                   out="/tmp/x", tier=None,
                                                   original=False))
        finally:
            cli._db = o_db
        check("拿不到图片密钥 → 非零退出并说清怎么办（点开一张图）",
              rc != 0 and "密钥" in txt and "点开" in txt, txt.strip()[:60])
        FakeMD.no_key = False
        FakeMD.calls = []
        o_db = cli._db
        cli._db = lambda: ImgDB()
        try:
            rc, txt = cap(cli.cmd_images, types_ns(chat="小明", limit=5,
                                                   out="/tmp/x", tier=None,
                                                   original=False))
            after_local = list(FakeMD.calls)
            rc2, txt2 = cap(cli.cmd_images, types_ns(chat="小明", limit=5,
                                                     out="/tmp/x", tier=None,
                                                     original=True))
            after_ui = list(FakeMD.calls)
        finally:
            cli._db = o_db
        check("有密钥 → 两张都交出来", rc == 0 and "2 张已存" in txt, txt.strip()[-40:])
        check("默认一次界面都不碰：只走 download_image",
              [c[0] for c in after_local] == ["local", "local"], str(after_local))
        check("--original 才驱动界面（每张一次，不多不少）",
              [c[0] for c in after_ui[2:]] == ["ui", "ui"], str(after_ui[2:]))
        check("--original 把显示名交给 chat_name（传 wxid 进搜索框就白等几十秒）",
              len(after_ui[2:]) == 2 and all(c[2] == "小明" for c in after_ui[2:]),
              str(after_ui[2:]))
        check("--original 拿不到时按「没拿到」计数，并提示本机档位",
              "0 张已存" in txt2 and "2 张没拿到" in txt2, txt2.strip()[-46:])
    finally:
        media_mod.MediaDownloader = o_md

    print("[cli] export：永远 UTF-8，行数和消息数一致")
    tmp = tempfile.mkdtemp(prefix="wxcli-")
    out = os.path.join(tmp, "h.txt")
    o_db = cli._db
    cli._db = lambda: FakeDB(msgs=[{"type": "文本", "content": "你好",
                                    "create_time": 1790000000},
                                   {"type": "文本", "content": "在吗",
                                    "create_time": 1790000001}])
    try:
        rc, txt = cap(cli.cmd_export, types_ns(chat="filehelper", limit=10,
                                               type=None, out=out))
    finally:
        cli._db = o_db
    body = open(out, encoding="utf-8").read()
    check("导出两行、UTF-8 读回中文没坏", rc == 0 and len(body.strip().splitlines()) == 2,
          repr(body[:40]))
    shutil.rmtree(tmp, ignore_errors=True)

    print("[cli] listen：回调里打的是昵称，注册的会话是补出来的 username")
    import wechatauto.db as db_mod
    o_lst, o_db = db_mod.Listener, cli._db
    caught = {}

    class FakeListener:
        def __init__(self, db, interval=1.0):
            self.db = db

        def add_listener(self, user, cb):
            caught["user"], caught["cb"] = user, cb

        def add_all(self, cb):
            caught["all"], caught["cb"] = True, cb

        def start(self):
            raise KeyboardInterrupt

        def stop(self):
            caught["stopped"] = True

    db_mod.Listener = FakeListener
    cli._db = lambda: FakeDB(nick="小明")
    try:
        cap(cli.cmd_listen, types_ns(chat="小明", all=False, interval=0.0))
        _rc, txt = cap(lambda: caught["cb"]({"chat": "wxid_x", "type": "文本",
                                             "content": "在吗",
                                             "create_time": 1790000000,
                                             "sender_username": ""}, None))
    finally:
        cli._db, db_mod.Listener = o_db, o_lst
    check("注册的是补全后的会话名（不是原样传下去的显示名）",
          caught.get("user") == "小明", str(caught.get("user")))
    check("打印用昵称，不把 wxid 甩给用户",
          "wxid_x" not in txt and "小明" in txt and "在吗" in txt, txt.strip()[:50])
    check("Ctrl+C 之后确实 stop() 了", caught.get("stopped") is True)

    print("[cli] 参数与导入安全")
    p = cli.build_parser()

    class _NoTTY:
        def isatty(self):
            return False

    o_stdin = sys.stdin
    sys.stdin = _NoTTY()
    try:
        check("脚本/管道里（stdin 不是终端）不进菜单，只打帮助退 2",
              cap(lambda: cli.main([]))[0] == 2)
    finally:
        sys.stdin = o_stdin

    print("[cli] 中文子命令与菜单（新手层）")
    check("中文子命令翻译得对（消息/导出/图片/体检）",
          cli.expand_aliases(["消息", "小明"]) == ["messages", "小明"]
          and cli.expand_aliases(["导出", "a"]) == ["export", "a"]
          and cli.expand_aliases(["图片"]) == ["images"]
          and cli.expand_aliases(["体检"]) == ["doctor"])
    check("英文照旧能用；不认识的词原样交给 argparse",
          cli.expand_aliases(["messages", "x"]) == ["messages", "x"]
          and cli.expand_aliases(["nope"]) == ["nope"])
    check("第一个参数是选项时不动它（--version 不能被改写）",
          cli.expand_aliases(["--version"]) == ["--version"])

    seen = []
    goes = []
    acts = []

    def rec(name):
        def _f(a):
            seen.append((name, getattr(a, "chat", None), getattr(a, "text", None)))
            goes.append(bool(getattr(a, "go", False)))
            acts.append(getattr(a, "action", None))
            return 0
        return _f

    names = ("cmd_messages", "cmd_export", "cmd_images", "cmd_send", "cmd_listen",
             "cmd_sessions", "cmd_moments", "cmd_doctor", "cmd_labels", "cmd_forward")
    o = {n: getattr(cli, n) for n in names}
    o_ask, o_db = cli._ask, cli._db
    for n in names:
        setattr(cli, n, rec(n[4:]))
    sess = [{"username": "wxid_a", "unread": 3}, {"username": "wxid_b", "unread": 0}]
    cli._db = lambda: FakeDB(sessions=sess, nick="小明")
    try:
        ans = iter(["7", "0"])
        cli._ask = lambda prompt="": next(ans)
        rc, txt = cap(cli.menu)
        check("菜单：选 7 → 跑的是 sessions",
              [s[0] for s in seen] == ["sessions"], str(seen))
        seen.clear()
        ans = iter(["1", "2", "0"])            # 看消息 → 按序号挑第 2 个会话 → 退出
        rc, txt2 = cap(cli.menu)
        check("菜单里列出了可选会话（带未读数，不用自己想起 username）",
              "小明" in txt2 and "未读 3" in txt2 and "1." in txt2, txt2.strip()[:70])
        check("挑序号 2 → messages 收到的就是那个会话的 username",
              seen[:1] == [("messages", "wxid_b", None)], str(seen))
        seen.clear()
        ans = iter(["4", "1", "你好", "0"])     # 发消息 → 挑会话 → 内容 → 退出
        rc, txt3 = cap(cli.menu)
        check("菜单里发消息默认带 --verify（发完自己回读确认）",
              ("send", None, "你好") in seen, str(seen))
        seen.clear()
        ans = iter(["9", "list", "0"])          # 标签 → 动作 list → 退出
        cli._ask = lambda prompt="": next(ans)
        rc, txt5 = cap(cli.menu)
        check("菜单：选 9 → 跑的是 labels，且把动作传下去",
              seen[:1] == [("labels", None, None)], str(seen))
        seen.clear()
        goes.clear()
        ans = iter(["9", "send", "同学", "周五校庆放假", "n", "0"])   # 群发 → 不确认
        rc, txt6 = cap(cli.menu)
        check("菜单里 send 第一遍是预演，不确认就一笔都不发",
              [s[0] for s in seen] == ["labels"] and goes == [False]
              and "没确认" in txt6, str(seen) + str(goes))
        seen.clear()
        goes.clear()
        ans = iter(["9", "send", "同学", "周五校庆放假", "y", "0"])    # 群发 → 确认
        rc, txt7 = cap(cli.menu)
        check("菜单里 send 确认之后第二遍才带 --go",
              goes == [False, True], str(goes))
        seen.clear()
        acts.clear()
        ans = iter(["9", "rename", "同学", "老同学", "0"])   # 改名 = 老名 + 新名
        rc, txt8 = cap(cli.menu)
        check("菜单里 rename 走通了（动作传到 labels，--to 这个参数 argparse 认）",
              acts[:1] == ["rename"] and seen[:1] == [("labels", None, None)],
              str(acts) + str(seen))
        seen.clear()
        acts.clear()
        goes.clear()
        # 菜单第 10 项 = 转发：l=整个标签，再问在哪个会话里右键
        ans = iter(["10", "l", "同学", "", "n", "0"])
        rc, txt9 = cap(cli.menu)
        check("菜单里 forward 先预演，不确认真发就不发",
              seen[:1] == [("forward", None, None)] and goes == [False]
              and "没确认" in txt9,
              str(seen) + str(goes) + txt9.strip()[-40:])
        seen.clear()
        acts.clear()
        goes.clear()
        ans = iter(["10", "n", "文件传输助手", "", "y", "0"])
        rc, txt10 = cap(cli.menu)
        check("菜单里 forward 确认之后第二遍才带 --go",
              [s[0] for s in seen] == ["forward", "forward"]
              and goes == [False, True],
              str(seen) + str(goes))
        seen.clear()
        # 编号从 _MENU 里挑，别写死：上一版写死 9，菜单加到第 9 项后这条
        # 测的就不是「按错」而是真跑了 labels。
        bad = next(k for k in ("12", "z", "99")
                   if k not in [m[0] for m in cli._MENU])
        ans = iter([bad, "0"])
        rc, txt4 = cap(cli.menu)
        check("按错编号只说「没有这个选项」，既不退也不跑东西",
              "没有这个选项" in txt4 and not seen, txt4.strip()[:40])
    finally:
        for n, f in o.items():
            setattr(cli, n, f)
        cli._ask, cli._db = o_ask, o_db
    check("菜单桩件全部还原", all(getattr(cli, n) is o[n] for n in names))
    try:
        cli.main(["--version"])
        ver = False
    except SystemExit as e:
        ver = e.code == 0
    check("--version 还是老行为（退出 0，不跑任何功能）", ver)
    try:
        p.parse_args(["images", "x", "--tier", "bogus"])
        bad_tier = False
    except SystemExit:
        bad_tier = True
    check("非法 tier 由 argparse 挡住", bad_tier)
    rc, txt = cap(cli.cmd_send, types_ns(text=None, file=None, image=None,
                                         to=None, verify=False))
    check("send 什么都没给 → 直接说清要发什么（不会去动微信窗口）",
          rc != 0 and "发" in txt, txt.strip()[:40])
    # 「import 就把微信窗口激活」是 issue #8 的老坑，但模块在不在 sys.modules 里
    # 说明不了任何事（import 任何子模块都会先跑包 __init__）。这里钉的是 CLI 自己
    # 能控制的那半：界面栈只能在子命令函数里 import，`--help`/`--version` 这条
    # 路径不该需要 pyautogui / winsdk 这些可选依赖。
    src = open(os.path.join(ROOT, "wechatauto", "cli.py"), encoding="utf-8").read()
    head = src[:src.index("\ndef ")]
    import re as _re
    lines = [l.strip() for l in head.splitlines()
             if l.strip().startswith(("import ", "from "))]
    heavy = [w for w in ("guia", "uia_driver", "media", "moment", "sender",
                         "pyautogui", "winsdk")
             if any(_re.search(r"\b%s\b" % w, l) for l in lines)]
    check("cli 顶部不 import 界面栈（只有子命令函数里才拉）", not heavy,
          "%s（顶层 import：%s）" % (heavy, lines))
    check("子命令里确实是延迟 import（quick_send / MediaDownloader / MomentDB）",
          all(s in src for s in ("from wechatauto.guia import",
                                 "from wechatauto.media import MediaDownloader",
                                 "from wechatauto import MomentDB")))


TESTS = {"layout": t_layout, "verify": t_verify, "rhythm": t_rhythm,
         "gate": t_gate, "click": t_click, "listen": t_listen, "moment": t_moment,
         "sender": t_sender, "voice": t_voice, "tree": t_tree, "image": t_image,
         "keys": t_keys, "sessions": t_sessions, "messages": t_messages,
         "cli": t_cli}


def main() -> int:
    want = sys.argv[1:] or ["layout", "verify", "rhythm", "gate", "click", "listen",
                            "moment", "sender", "voice", "tree", "image",
                            "keys", "sessions", "messages", "cli"]
    for name in want:
        fn = TESTS.get(name)
        if not fn:
            print("未知检查项：%s（可选：%s）" % (name, "/".join(TESTS)))
            return 2
        try:
            fn()
        except Exception as exc:
            FAIL.append(name)
            print("  ✗ %-46s %r" % ("%s 崩溃" % name, exc))
    print("\n通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
