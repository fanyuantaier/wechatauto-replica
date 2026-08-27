# -*- coding: utf-8 -*-
"""诊断 sender_username 和 content 问题"""
import sys
sys.path.insert(0, '.')

from wechatauto.db import WeChatDB

print("=== 诊断 ===")

db = WeChatDB()
print(f"WeChatDB 初始化成功 (wxid={db.wxid})")

# 检查 SenderName2Id 表结构
print("\n--- SenderName2Id 表结构 ---")
for rel, path, _ in db._db_files:
    if "message_resource" not in path:
        continue
    conn = db._open(rel)
    try:
        # 查看表结构
        cursor = conn.execute("PRAGMA table_info(SenderName2Id)")
        cols = cursor.fetchall()
        print(f"  列: {[c[1] for c in cols]}")
        
        # 查看前5条数据
        rows = conn.execute("SELECT * FROM SenderName2Id LIMIT 5").fetchall()
        for r in rows:
            print(f"  数据: {r}")
    finally:
        conn.close()
    break

# 检查消息表的 real_sender_id 值
print("\n--- 消息表 real_sender_id 值 ---")
msgs = db.get_messages("filehelper", limit=3)
for m in msgs:
    print(f"  sender_id={m.get('sender_id')}, type={type(m.get('sender_id'))}")

# 测试 sender_id_index
print("\n--- sender_id_index ---")
idx = db._sender_id_index()
print(f"  总记录数: {len(idx)}")
if idx:
    sample = list(idx.items())[:5]
    for k, v in sample:
        print(f"  {k} -> {v}")
