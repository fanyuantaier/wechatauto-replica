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
    check("会话定位通过率 ≥ 50%%", tot == 0 or hit / tot >= 0.5,
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


TESTS = {"layout": t_layout, "verify": t_verify, "rhythm": t_rhythm,
         "gate": t_gate,
         "keys": t_keys, "sessions": t_sessions, "messages": t_messages}


def main() -> int:
    want = sys.argv[1:] or ["layout", "verify", "rhythm", "gate",
                            "keys", "sessions", "messages"]
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
