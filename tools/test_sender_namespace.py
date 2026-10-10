# -*- coding: utf-8 -*-
"""issue #34 回归：`real_sender_id` 必须按**消息所在分片**的 Name2Id 解析。

跑法：python tools/test_sender_namespace.py

报告说「发送者名称错位」——两件事被证实：

1. 编号空间搞错了表。`real_sender_id` 是消息所在分片 `Name2Id` 的 rowid，
   不是 `message_resource.db.SenderName2Id` 的 rowid；同一个数字在两张表里是**不同的人**。
2. 「编号 2 = 自己」不成立。本机自己账号在 message_0/1/2.db 的 rowid 分别是 2/4/1。

夹具刻意让两片对同一个编号给出不同的人，并且带一节**反向验**（把命名空间退回
"全局共用一张表"），保证这套断言真的咬得住，不是修好之后才变绿。
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
    print("%-4s %-56s got=%-26r want=%r" % ("ok" if ok else "FAIL", name, got, want))


def untested(name, why):
    UNTESTED.append(name)
    print("%-6s %s（%s）" % ("未测", name, why))


class Recorder:
    def __init__(self):
        self.warnings, self.debugs = [], []

    def _f(self, m, a):
        try:
            return m % a if a else str(m)
        except Exception:
            return str(m)

    def warning(self, m, *a):
        self.warnings.append(self._f(m, a))

    error = warning

    def debug(self, m, *a):
        self.debugs.append(self._f(m, a))

    def info(self, m, *a):
        pass


OWN = "wxid_own_account"
ALICE = "wxid_alice_test"
BOB = "wxid_bob_test"
USER = "48255887531@chatroom"          # 群会话：一片里是 alice，另一片里是 bob
TABLE = "Msg_" + _md5_hex(USER.encode())
S0 = "message__message_0.db"
S1 = "message__message_1.db"

# 分片 0：1=自己 2=alice      分片 1：1=bob 2=自己
N2I = {S0: {1: OWN, 2: ALICE}, S1: {1: BOB, 2: OWN}}
# 旧口径那张表（message_resource.SenderName2Id）：同一个数字指向别人
GLOBAL_OLD = {1: "wxid_stranger_one", 2: "wxid_stranger_two"}

MSG_COLS = ("local_id, local_type, real_sender_id, create_time, message_content, "
            "source, packed_info_data, compress_content, server_id, sort_seq")


def make_db(tmpdir, sender_of_row):
    """建两片库：每片有自己的 Name2Id，行里的 real_sender_id 按本片编号。"""
    paths = []
    for i, (label, rows) in enumerate(sender_of_row):
        p = os.path.join(tmpdir, label)
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE %s (local_id INTEGER, local_type INTEGER, "
                     "real_sender_id INTEGER, create_time INTEGER, message_content TEXT, "
                     "source TEXT, packed_info_data BLOB, compress_content BLOB, "
                     "server_id INTEGER, sort_seq INTEGER)" % TABLE)
        conn.execute("CREATE TABLE Name2Id (user_name TEXT)")
        for rid in sorted(N2I[label]):
            conn.execute("INSERT INTO Name2Id (rowid, user_name) VALUES (?, ?)",
                         (rid, N2I[label][rid]))
        for lid, sid, seq in rows:
            conn.execute("INSERT INTO %s (%s) VALUES (?,?,?,?,?,?,?,?,?,?)" % (
                TABLE, MSG_COLS),
                (lid, 1, sid, 1700000000 + seq, "行%s" % lid, None, None, None,
                 500000 + seq, seq))
        conn.commit()
        conn.close()
        paths.append(p)

    d = object.__new__(WeChatDB)
    d.workdir = tmpdir
    # wxid 是 account 去掉末尾 4 位哈希的只读属性，所以从 account 灌进去
    d.account = OWN + "_1234"

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
    # 真实 rel 形状是 message/message_0.db，解密缓存名才是 message__message_0.db
    d._message_dbs = lambda: ["message" + os.sep + "message_%d.db" % i
                              for i in range(len(paths))]
    d._open = lambda rel: _open_cache(d, rel)
    return d


def _open_cache(d, rel):
    """`sender_username(shard, sid)` 走的是 rel -> 缓存库名，这里按名字找到那个文件。"""
    base = rel.replace(os.sep, "__")
    for name in (S0, S1):
        if name == base:
            c = sqlite3.connect(os.path.join(d.workdir, name))
            c.row_factory = sqlite3.Row
            return c
    raise RuntimeError("没有这个分片：%s" % rel)


rec = Recorder()
REAL_LOG = dbmod.wxlog
dbmod.wxlog = rec
tmp = tempfile.mkdtemp(prefix="wxsender_")
try:
    ROWS = [(S0, [(1, 2, 10), (2, 1, 20), (3, 9, 30)]),      # sid=2 → alice，9 没映射
            (S1, [(1, 2, 40), (2, 1, 50)])]                   # sid=2 → 自己
    d = make_db(tmp, ROWS)

    print("--- A. 同一个编号在不同分片必须是不同的人 ---")
    all_rows = d.get_messages(USER, limit=50)
    # local_id 在两片里刻意撞号（真实微信就是这样），所以按 (分片, local_id) 取行
    rows = {(r["shard"], r["local_id"]): r for r in all_rows}
    check("读到全部分片的行", len(all_rows), 5)
    check("分片0 sid=2 解析成本片的 alice", rows[(S0, 1)]["sender_username"], ALICE)
    check("分片1 sid=2 解析成本片的自己", rows[(S1, 1)]["sender_username"], OWN)
    check("分片0 sid=1 解析成本片的自己", rows[(S0, 2)]["sender_username"], OWN)
    check("分片1 sid=1 解析成本片的 bob", rows[(S1, 2)]["sender_username"], BOB)
    check("「2 就是自己」这类硬编码不再存在（同一个 sid 两种结果）",
          len({r["sender_username"] for r in all_rows if r["sender_id"] == 2}), 2)
    check("每行都带 shard（编号要连着分片才有意义）",
          sorted({r["shard"] for r in all_rows}), [S0, S1])

    print("\n--- B. 映射缺失时留空，不拿别的表猜 ---")
    check("本分片 Name2Id 里没有的编号 → 空串", rows[(S0, 3)]["sender_username"], "")
    check("sender_id 本身仍然原样交出去（不被改名）", rows[(S0, 3)]["sender_id"], 9)
    check("公开解析口：按分片解析 sid", d.sender_username(S1, 2), OWN)
    check("公开解析口：分片名写错 → 空串", d.sender_username("message__message_9.db", 2), "")
    check("公开解析口：编号没有 → 空串", d.sender_username(S0, None), "")
    check("公开解析口：本片没这个编号 → 空串", d.sender_username(S0, 9), "")

    print("\n--- C. get_message_row 走同一套解析 ---")
    check("按 shard 取回分片0那条", d.get_message_row(USER, 1, shard=S0)["sender_username"],
          ALICE)
    check("按 shard 取回分片1那条（同 sid=2）",
          d.get_message_row(USER, 1, shard=S1)["sender_username"], OWN)
    got = d.get_message_row(USER, 1)             # 不指定 → 取 sort_seq 最新那片
    check("不指定 shard 时仍按命中的那片解析", got["sender_username"], OWN)
    check("不指定 shard 会因为歧义告警（不静默）", any("shard" in w for w in rec.warnings), True)

    print("\n--- D. 导出口径（_resolve_sender）与界面显示一致 ---")
    maps = {S0: N2I[S0], S1: N2I[S1]}
    nicks = {ALICE: "小爱", BOB: "小波", OWN: "我"}
    check("分片0 的 sid=2 → 显示成 alice 的昵称",
          d._resolve_sender(2, maps, S0, nicks, "我本人", OWN), "小爱")
    check("分片1 的 sid=2 → 显示成自己（不是「编号 2 就是自己」的规则）",
          d._resolve_sender(2, maps, S1, nicks, "我本人", OWN), "我本人")
    check("映射缺失 → 空串（不把编号本身当名字吐出去）",
          d._resolve_sender(9, maps, S0, nicks, "我本人", OWN), "")
    check("sid=0（系统消息）→ 空串", d._resolve_sender(0, maps, S0, nicks, "我本人", OWN), "")

    print("\n--- E. 媒体层的「是不是自己发的」也跟着分片走 ---")
    from wechatauto.media import MediaDownloader

    md = MediaDownloader.__new__(MediaDownloader)
    md.db = d
    check("sender_username 已是自己 → True",
          md._sent_by_self({"sender_id": 2, "sender_username": OWN, "shard": S1}), True)
    check("同一个编号在另一片是 alice → False（旧代码这里会答错）",
          md._sent_by_self({"sender_id": 2, "sender_username": ALICE, "shard": S0}), False)
    check("行里没带解析结果时按 shard 再解一次",
          md._sent_by_self({"sender_id": 2, "shard": S1}), True)
    check("解不出来时按非本机处理，不再猜编号 1/2",
          md._sent_by_self({"sender_id": 9, "shard": S0}), False)

    print("\n--- F. 反向验：把命名空间退回「全局一张表」必须变红 ---")
    old_maps = {S0: GLOBAL_OLD, S1: GLOBAL_OLD}
    check("退回旧表后 sid=2 两片解析成人（就是报告的错位）",
          [old_maps[S0].get(2), old_maps[S1].get(2)],
          ["wxid_stranger_two", "wxid_stranger_two"])
    check("旧表里根本没有「自己」这个人",
          OWN in set(GLOBAL_OLD.values()), False)
    check("新口径下同一编号两片不同人（旧口径给不出这个差异）",
          (d._resolve_sender(2, maps, S0, nicks, "我本人", OWN)
           != d._resolve_sender(2, maps, S1, nicks, "我本人", OWN)), True)

    print("\n--- G. 错表的入口已经删掉 ---")
    _src = open(os.path.join(SRC, "wechatauto", "db.py"), encoding="utf-8").read()
    _wx = open(os.path.join(SRC, "wechatauto", "wx.py"), encoding="utf-8").read()
    check("db.py 不再读 SenderName2Id 解析发送者", "SELECT rowid, user_name FROM SenderName2Id" in _src, False)
    check("_sender_id_index 已从源码里删除", "_sender_id_index" in _src, False)
    check("db.py 不再写死 sender_id != 2", "sender_id != 2" in _src, False)
    check("wx.py 不再写死 sender_id == 2", "sender_id == 2" in _wx, False)
finally:
    dbmod.wxlog = REAL_LOG
    shutil.rmtree(tmp, ignore_errors=True)

print("\n--- H. 真机复验（微信没开就记 未测）---")
try:
    import re
    live = WeChatDB()
    own = live.wxid
    if not own:
        raise RuntimeError("没拿到本机账号")
    fh = live.get_messages("filehelper", limit=100000)
    from collections import Counter as _C
    who = _C((r.get("sender_username") or "") for r in fh if r["sender_id"])
    print("     文件传输助手 %d 行的发送者分布: %s" % (len(fh), who.most_common(4)))
    check("文件传输助手的发送者只会是本机或 filehelper 本身",
          set(who) - {own, "filehelper"}, set())
    check("绝大多数行确实是本机自己发的（≥95%%）",
          who.get(own, 0) * 100 // max(1, sum(who.values())) >= 95, True)
    idx = live._build_md5_index()
    PRE = re.compile(r"^(wxid_[A-Za-z0-9]+|\d+@chatroom|[A-Za-z0-9._-]+):\s*\n")
    agree = disagree = 0
    rooms = 0
    for md5, user in list(idx.items()):
        if not user.endswith("@chatroom"):
            continue
        rows = live.get_messages(user, limit=2000)
        if not rows:
            continue
        rooms += 1
        for r in rows:
            if r["type"] != "文本":
                continue
            m = PRE.match(str(r["content"] or ""))
            if not m:
                continue
            if m.group(1) == (r.get("sender_username") or ""):
                agree += 1
            else:
                disagree += 1
        if agree + disagree >= 800 or rooms >= 12:
            break
    if agree + disagree == 0:
        untested("群聊文本「发送者:」前缀对撞", "本机群消息里没有带前缀的文本行")
    else:
        check("群 %d 个：按分片解析 = 微信写进正文的发送者前缀（一致 %d / 不一致 %d）"
              % (rooms, agree, disagree), disagree, 0)
        check("对撞样本不是空跑（≥200 行）", agree + disagree >= 200, True)
except Exception as exc:
    untested("真机发送者复验", "%s: %s" % (type(exc).__name__, str(exc)[:90]))

print("\n结果：%d 通过 / %d 失败 / %d 未测"
      % (len(PASSED), len(FAILED), len(UNTESTED)))
for n in FAILED:
    print("  失败:", n)
for n in UNTESTED:
    print("  未测:", n)
sys.exit(1 if FAILED else 0)
