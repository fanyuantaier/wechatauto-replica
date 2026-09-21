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


TESTS = {"layout": t_layout, "verify": t_verify, "keys": t_keys,
         "sessions": t_sessions, "messages": t_messages}


def main() -> int:
    want = sys.argv[1:] or ["layout", "verify", "keys", "sessions", "messages"]
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
