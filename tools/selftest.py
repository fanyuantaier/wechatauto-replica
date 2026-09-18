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


TESTS = {"layout": t_layout, "keys": t_keys, "sessions": t_sessions,
         "messages": t_messages}


def main() -> int:
    want = sys.argv[1:] or ["layout", "keys", "sessions", "messages"]
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
