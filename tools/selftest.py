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
        arc = {tuple(m) for m in u2.moves}
        u3 = FakeU32()
        rhythm.move_to(u3, 900, 700, start=(300, 400))
        check("两次移动的轨迹不重合", arc != {tuple(m) for m in u3.moves})
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
    finally:
        rhythm.time.sleep = real
        rhythm.reset()


TESTS = {"layout": t_layout, "verify": t_verify, "rhythm": t_rhythm,
         "keys": t_keys, "sessions": t_sessions, "messages": t_messages}


def main() -> int:
    want = sys.argv[1:] or ["layout", "verify", "rhythm",
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
