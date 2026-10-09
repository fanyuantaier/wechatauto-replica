# -*- coding: utf-8 -*-
"""issue #20 回归：跨分片消息的可寻址性（shard）与分片读失败的可见性。

跑法：python tools/test_db_shard.py

背景：同一会话的 ``Msg_<md5>`` 表横跨多个 ``message_N.db`` 分片，而
``local_id`` **只在分片内唯一**。合并视图里 ``(user, local_id)`` 会命中多行，
调用方（媒体下载等）没法说清"我要哪一条"。本文件用两份临时 sqlite 库造出
可控的跨分片会话，钉住四件事：

1. 读取覆盖全部分片（issue #20 说"只读第一个分片"，这条断言防它复发）；
2. 每行带 ``shard``，且 ``shard`` 能把 ``(user, local_id)`` 钉到唯一一行；
3. 新加的 ``? AS shard`` 不能挤掉 WHERE 里的查询参数；
4. 单个分片读失败不再被静默当成"这个分片没消息"。
"""
import os
import shutil
import sqlite3
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)

from wechatauto import db as dbmod                          # noqa: E402
from wechatauto.db import WeChatDB, _md5_hex                # noqa: E402

PASSED, FAILED, UNTESTED = [], [], []


def check(name, got, want):
    ok = got == want
    (PASSED if ok else FAILED).append(name)
    print("%-4s %-54s got=%-24r want=%r" % ("ok" if ok else "FAIL", name, got, want))


def untested(name, why):
    UNTESTED.append(name)
    print("%-6s %s（%s）" % ("未测", name, why))


USER = "wxid_shard_fixture"
TABLE = "Msg_" + _md5_hex(USER.encode())
COLS = ("local_id, local_type, real_sender_id, create_time, message_content, "
        "source, packed_info_data, compress_content, server_id, sort_seq")
CREATE = ("CREATE TABLE %s (local_id INTEGER, local_type INTEGER, "
          "real_sender_id INTEGER, create_time INTEGER, message_content TEXT, "
          "source TEXT, packed_info_data BLOB, compress_content BLOB, "
          "server_id INTEGER, sort_seq INTEGER)" % TABLE)

S0 = "message__message_0.db"
S1 = "message__message_1.db"


def row(lid, ltype, content, seq, sender=2):
    return (lid, ltype, sender, 1700000000 + seq, content, None, None, None,
            100000 + seq, seq)


class Recorder:
    """替身 logger：把 warning/debug 收进列表，用来证明「歧义会被说出来」。"""

    def __init__(self):
        self.warnings, self.debugs = [], []

    def _fmt(self, msg, a):
        try:
            return msg % a if a else str(msg)
        except Exception:
            return str(msg)

    def warning(self, msg, *a):
        self.warnings.append(self._fmt(msg, a))

    def error(self, msg, *a):
        self.warnings.append(self._fmt(msg, a))

    def debug(self, msg, *a):
        self.debugs.append(self._fmt(msg, a))

    def info(self, msg, *a):
        pass


def make_db(rows_by_shard, tmpdir):
    """造一个只有消息读取路径的 WeChatDB：分片是真的临时 sqlite 文件。

    每次调用开一个**子目录**（文件名保持一致，`_shard_label` 取的是 basename），
    这样同一 tmpdir 下可以摆多份互不干扰的样本库。

    ``_msg_conns`` 每次都**新开连接**——真实实现里调用方读完就 close，
    复用同一个连接会让第二次查询测的是「闭库报错」而不是被测逻辑。
    """
    make_db.n += 1
    sub = os.path.join(tmpdir, "case%d" % make_db.n)
    os.makedirs(sub)
    paths = []
    for name, rows in rows_by_shard:
        p = os.path.join(sub, name)
        conn = sqlite3.connect(p)
        conn.execute(CREATE)
        conn.executemany("INSERT INTO %s (%s) VALUES (%s)" % (
            TABLE, COLS, ",".join("?" * 10)), rows)
        conn.commit()
        conn.close()
        paths.append(p)

    d = object.__new__(WeChatDB)
    d.workdir = sub
    d._sender_id_index = lambda: {}

    def _msg_conns(user, _retry=True):
        if user != USER:
            return []
        out = []
        for p in paths:
            c = sqlite3.connect(p)
            c.row_factory = sqlite3.Row
            out.append((c, TABLE))
        return out

    d._msg_conns = _msg_conns
    d.invalidated = []
    d._invalidate_cache = lambda: d.invalidated.append(True)
    return d


make_db.n = 0


ROWS_A = [                      # 分片 0：local_id 1/2/3/7
    row(1, 1, "S0-文本一", 10),
    row(2, 1, "S0-文本二", 20),
    row(3, 34, "S0-语音A", 15),
    row(7, 3, "S0-图片A md5=aaaa", 30),
]
ROWS_B = [                      # 分片 1：1/3/7 与首片撞号，2 只在本片有
    row(1, 1, "S1-文本一（同号）", 40),
    row(2, 1, "S1-只在第二片", 50),
    row(3, 34, "S1-语音B", 45),
    row(7, 3, "S1-图片B md5=bbbb", 60),
]

rec = Recorder()
REAL_LOG = dbmod.wxlog
dbmod.wxlog = rec
tmp = tempfile.mkdtemp(prefix="wxshard_")
try:
    print("--- A. 合并读取覆盖全部分片（issue #20 的说法在这里复发就会 FAIL）---")
    d = make_db([(S0, ROWS_A), (S1, ROWS_B)], tmp)
    msgs = d.get_messages(USER, limit=50)
    check("两个分片都读到了（8 行而不是首片的 4 行）", len(msgs), 8)
    check("首片单独贡献的行数（回归哨兵：等于全部就是漏了次片）",
          sum(1 for m in msgs if m["shard"] == S0), 4)
    check("次片单独贡献的行数", sum(1 for m in msgs if m["shard"] == S1), 4)
    check("只存在于次片的行也在结果里",
          any(m["content"] == "S1-只在第二片" for m in msgs), True)
    check("按 sort_seq 降序", [m["sort_seq"] for m in msgs],
          [60, 50, 45, 40, 30, 20, 15, 10])
    check("每行都带 shard", sorted({m["shard"] for m in msgs}), [S0, S1])
    check("shard 与内容对得上",
          [(m["content"], m["shard"]) for m in msgs if m["sort_seq"] in (30, 60)],
          [("S1-图片B md5=bbbb", S1), ("S0-图片A md5=aaaa", S0)])

    print("\n--- B. shard 是可用的寻址键 ---")
    check("不指定 shard：同号命中 2 片 → 取 sort_seq 最新（保留旧行为）",
          d.get_message_row(USER, 1)["content"], "S1-文本一（同号）")
    check("不指定 shard 时明确告警（不许静默猜）", len(rec.warnings), 1)
    check("告警里点名了两个分片和 shard 这个出口",
          ("shard" in rec.warnings[0] and S0 in rec.warnings[0]
           and S1 in rec.warnings[0]), True)
    rec.warnings.clear()
    check("shard=首片 → 拿回首片那一行",
          d.get_message_row(USER, 1, shard=S0)["content"], "S0-文本一")
    check("shard=次片 → 拿回次片那一行",
          d.get_message_row(USER, 1, shard=S1)["content"], "S1-文本一（同号）")
    check("钉死分片后不再告警", len(rec.warnings), 0)
    check("返回行里带 shard，可以原样传回",
          d.get_message_row(USER, 1, shard=S0)["shard"], S0)
    check("分片名写错 → 返回 None 而不是瞎猜",
          d.get_message_row(USER, 1, shard="message__message_9.db"), None)
    check("分片名写错要告警（不能把写错当成没有这条）", len(rec.warnings), 1)
    rec.warnings.clear()
    check("不撞号的行不受影响（local_id=2 在首片唯一）",
          d.get_message_row(USER, 2, shard=S0)["content"], "S0-文本二")

    print("\n--- C. 同号 + 同类型（媒体那一类，local_type 过滤救不了）---")
    check("local_id=7 在 2 片都有", len(d.get_message_rows_for_media(USER, 7)), 2)
    check("不带 shard → 取最新（这就是会下错图的那一步）",
          d.get_message_row(USER, 7, local_type=3)["content"], "S1-图片B md5=bbbb")
    check("带 shard → 精确拿回首片那张图",
          d.get_message_row(USER, 7, local_type=3, shard=S0)["content"],
          "S0-图片A md5=aaaa")
    check("候选行按 sort_seq 降序且各带自己的 shard",
          [(r["sort_seq"], r["shard"], r["local_type"])
           for r in d.get_message_rows_for_media(USER, 7)],
          [(60, S1, 3), (30, S0, 3)])

    print("\n--- D. WHERE 带 ? 时，新加的 ? AS shard 不能挤掉查询参数 ---")
    conns = d._msg_conns(USER)
    try:
        got = sorted((r["local_id"], r["shard"]) for r in d._shard_rows(
            conns, "WHERE local_id=? AND local_type=?", (7, 3)))
        check("两个 ? 各自落位（只返回 local_id=7）", got, [(7, S0), (7, S1)])
    finally:
        for c, _ in conns:
            c.close()
    conns = d._msg_conns(USER)
    try:
        got = sorted((r["local_id"], r["shard"]) for r in d._shard_rows(
            conns, "WHERE local_id=?", (1,)))
        check("单 ? 也只返回 local_id=1", got, [(1, S0), (1, S1)])
    finally:
        for c, _ in conns:
            c.close()
    check("get_new_messages 的 sort_seq>? 没被挤错位置",
          [(m["sort_seq"], m["shard"]) for m in d.get_new_messages(USER, 30, 10)],
          [(40, S1), (45, S1), (50, S1), (60, S1)])
    check("分片内 LIMIT 与 ? 并存时窗口仍然正确",
          [m["sort_seq"] for m in d.get_messages(USER, limit=2, offset=2)], [45, 40])
    check("非法入参仍旧空返回（没被新列带偏）",
          (d.get_messages(USER, limit=0), d.get_messages(USER, limit=5, offset=-1)),
          ([], []))

    print("\n--- E. 窄查询（语音/图片列表）也带 shard ---")
    check("语音行里能拿到 shard（列表 → 下载不回捞错行）",
          sorted((r["shard"], r["sort_seq"])
                 for r in d.get_voice_rows(USER, limit=10, local_id=3)),
          [(S0, 15), (S1, 45)])
    check("图片行里能拿到 shard",
          sorted((r["shard"], r["local_id"]) for r in d.get_image_rows(USER, 10)),
          [(S0, 7), (S1, 7)])

    print("\n--- F. 单个分片读坏不再被静默当成「这个分片没消息」---")
    # F1 库损坏：必须冒到 _run_msg_query 走清缓存重建，而不是少一半还「成功」
    broken = make_db([(S0, ROWS_A), (S1, ROWS_B)], tmp)

    class MalformedConn(sqlite3.Connection):
        def execute(self, *a, **kw):
            raise sqlite3.DatabaseError("database disk image is malformed")

    def _conns_bad(user, _retry=True):
        if user != USER:
            return []
        return [(MalformedConn(os.path.join(broken.workdir, S0)), TABLE),
                (MalformedConn(os.path.join(broken.workdir, S1)), TABLE)]

    broken._msg_conns = _conns_bad
    raised = None
    try:
        broken.get_messages(USER, limit=5)
    except sqlite3.DatabaseError as exc:
        raised = str(exc)
    check("malformed 不再被吞掉（抛出 → 由 _run_msg_query 重建重试）",
          bool(raised) and "malformed" in raised, True)
    check("重建确实被触发（清缓存 1 次）", len(broken.invalidated), 1)

    # F2 非损坏类错误（老库少表/少列）→ 跳过该片，但必须说话
    partial = make_db([(S0, ROWS_A), (S1, ROWS_B)], tmp)
    real_conns = partial._msg_conns

    def _conns_missing_table(user, _retry=True):
        out = real_conns(user, _retry)
        if out:
            out[0][0].execute("DROP TABLE %s" % TABLE)
        return out

    partial._msg_conns = _conns_missing_table
    rec.warnings.clear()
    got = partial.get_messages(USER, limit=50)
    check("少表的分片被跳过，另一片照常返回（4 行）", len(got), 4)
    check("跳过的分片写进日志了（不再静默少 4 条）", len(rec.warnings), 1)
    check("日志点名了是哪个分片", S0 in rec.warnings[0], True)

    print("\n--- G. 只返回首片的旧接口已经删掉（issue #20 的误读源头）---")
    check("_find_msg_table（单片版）已删", hasattr(WeChatDB, "_find_msg_table"), False)
    check("_msg_conn（单片版）已删", hasattr(WeChatDB, "_msg_conn"), False)
    check("_find_msg_tables（全片版）还在", hasattr(WeChatDB, "_find_msg_tables"), True)
    check("_msg_conns（全片版）还在", hasattr(WeChatDB, "_msg_conns"), True)
    _src = open(os.path.join(SRC, "wechatauto", "db.py"), encoding="utf-8").read()
    check("源码里不再有「只返回第一个命中分片」这句话",
          "只返回第一个命中分片" in _src, False)
finally:
    dbmod.wxlog = REAL_LOG
    shutil.rmtree(tmp, ignore_errors=True)

print("\n--- H. 真机：拿一个真的同号同类型跨分片会话验一遍（微信没开就记 未测）---")
try:
    live = WeChatDB()
    if not live._message_dbs():
        raise RuntimeError("没有消息分片（微信未登录或数据目录没找到）")
    idx = live._build_md5_index()
    hit = None          # (user, local_id, local_type, {分片: sort_seq})
    scanned = 0
    for md5, user in list(idx.items()):
        conns = live._msg_conns(user)
        try:
            if len(conns) < 2:
                continue
            scanned += 1
            seen = {}
            for c, t in conns:
                lab = live._shard_label(c)
                for r in c.execute(
                        "SELECT local_id, local_type, sort_seq FROM %s" % t):
                    seen.setdefault((r["local_id"], r["local_type"], lab), r["sort_seq"])
            by_key = {}
            for (lid, ltype, lab), seq in seen.items():
                by_key.setdefault((lid, ltype), {})[lab] = seq
            for (lid, ltype), per in by_key.items():
                if len(per) > 1 and len(set(per.values())) == len(per):
                    hit = (user, lid, ltype, per)
                    break
        finally:
            for c, _ in conns:
                c.close()
        if hit:
            break
    if hit is None:
        untested("真机同号同类型跨分片回读",
                 "扫了 %d 个跨分片会话没找到 sort_seq 也不同的样本" % scanned)
    else:
        user, lid, ltype, per = hit
        print("     样本：%s local_id=%s type=%s 分布=%s" % (user, lid, ltype, per))
        check("样本确实横跨 ≥2 个分片且各行可区分", len(per) >= 2, True)
        newest = max(per.values())
        unpinned = live.get_message_row(user, lid, local_type=ltype)
        check("不带 shard 时拿到的是「最新」那一条（旧行为的实证）",
              unpinned["sort_seq"], newest)
        ok_each = True
        wrong_before = 0
        for lab, seq in per.items():
            got = live.get_message_row(user, lid, local_type=ltype, shard=lab)
            if got is None or got["sort_seq"] != seq:
                ok_each = False
            elif got["sort_seq"] != newest:
                wrong_before += 1      # 不带 shard 时这一条本来会被取错
        check("真机 shard= 逐片回读都拿到本片那一行（%s/%s）" % (user, lid),
              ok_each, True)
        check("不指定 shard 确实会取错行（本样本里有 %d 片不是最新）" % wrong_before,
              wrong_before > 0, True)
        mm = live.get_messages(user, limit=300)
        check("真机 get_messages 的行都带 shard",
              bool(mm) and all(m.get("shard") for m in mm), True)
        labels = set()
        conns = live._msg_conns(user)
        try:
            labels = {live._shard_label(c) for c, _ in conns}
            total = sum(c.execute("SELECT count(*) FROM %s" % t).fetchone()[0]
                        for c, t in conns)
        finally:
            for c, _ in conns:
                c.close()
        check("真机 shard 取值就是该会话的分片库名（不空、不编造）",
              {m["shard"] for m in mm} <= labels and bool({m["shard"] for m in mm}), True)
        allm = live.get_messages(user, limit=total)
        check("真机合并读取 = 各分片行数之和（issue #20 的正面回答，会话 %s 共 %d 行）"
              % (user, total), (len(allm), total > 0), (total, True))
        check("合并结果覆盖该会话的全部分片",
              len({m["shard"] for m in allm}), len(labels))
except Exception as exc:
    untested("真机跨分片回读", "%s: %s" % (type(exc).__name__, str(exc)[:90]))

print("\n结果：%d 通过 / %d 失败 / %d 未测"
      % (len(PASSED), len(FAILED), len(UNTESTED)))
for n in FAILED:
    print("  失败:", n)
for n in UNTESTED:
    print("  未测:", n)
sys.exit(1 if FAILED else 0)
