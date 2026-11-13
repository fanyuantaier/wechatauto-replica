# -*- coding: utf-8 -*-
"""issue #32 回归：微信数据目录配在盘符根（d:\\）时，自动检测不得依赖启动目录。

跑法：python tools/test_db_path_root.py

关键在于**要咬得住旧写法**：旧代码 ``root.rstrip("\\\\/")`` 把 ``d:\\`` 变成 ``d:``，
``os.path.join('d:', 'xwechat_files')`` = ``d:xwechat_files``（盘符相对路径）。
本文件用假文件系统只认「真正的绝对路径」，所以旧代码会一个都找不到。
"""
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)

from wechatauto import db as dbmod                          # noqa: E402
from wechatauto.db import _abs_root, _extract_path_from_config  # noqa: E402
from wechatauto.db import _locate_account_root              # noqa: E402

SEP = os.sep
PASSED, FAILED = [], []


def check(name, got, want):
    ok = got == want
    (PASSED if ok else FAILED).append(name)
    print("%-4s %-52s got=%-30r want=%r" % ("ok" if ok else "FAIL", name, got, want))


print("--- A. _abs_root 本身 ---")
check("盘根保留：d:" + SEP, _abs_root("d:" + SEP), "d:" + SEP)
check("只给盘符 → 补成盘根", _abs_root("d:"), "d:" + SEP)
check("普通目录去掉尾分隔符", _abs_root("d:" + SEP + "a" + SEP), "d:" + SEP + "a")
check("双重尾分隔符", _abs_root("d:" + SEP + SEP), "d:" + SEP)
check("结果必须是绝对路径", bool(os.path.isabs(_abs_root("d:" + SEP))), True)
check("UNC 根不被啃成相对路径（normpath 保留尾分隔符）",
      _abs_root(SEP + SEP + "srv" + SEP + "share" + SEP),
      SEP + SEP + "srv" + SEP + "share" + SEP)
check("空串不炸", _abs_root(""), ".")

print("\n--- B. join 之后还是绝对路径（旧写法在这翻车）---")
root = _abs_root("d:" + SEP)
cand = os.path.join(root, "xwechat_files")
check("join 结果是绝对路径", os.path.isabs(cand), True)
check("join 结果等于盘根下的目录", cand, "d:" + SEP + "xwechat_files")


def make_fake_fs(known_dirs, listing):
    """替身：只有 known_dirs 里的绝对路径算存在，且必须是绝对路径才算数。"""
    def fake_isdir(p):
        return os.path.isabs(p) and p in known_dirs

    def fake_listdir(p):
        return listing[p]

    return fake_isdir, fake_listdir


print("\n--- C. 盘符根布局：从不同启动目录都要能定位到同一个地方 ---")
REAL_ISDIR, REAL_LISTDIR = os.path.isdir, os.listdir
TRUE_ROOT = "d:" + SEP + "xwechat_files"
fake_isdir, fake_listdir = make_fake_fs(
    {"d:" + SEP, TRUE_ROOT, TRUE_ROOT + SEP + "wxid_abc" + SEP + "db_storage"},
    {"d:" + SEP: ["xwechat_files", "Windows", "$RECYCLE.BIN"],   # 盘根：只有目录名，
     # 没有 db_storage，必须被跳过（旧写法根本走不到这一步）
     TRUE_ROOT: ["wxid_abc"]})
dbmod.os.path.isdir = fake_isdir
dbmod.os.listdir = fake_listdir
try:
    for cwd_marker in ("启动目录=盘根", "启动目录=子目录"):
        got = _locate_account_root("d:" + SEP)
        check("假 fs 下定位到 %s（%s）" % (TRUE_ROOT, cwd_marker), got, TRUE_ROOT)
    # 旧写法会算出 'd:xwechat_files'，在这里必然找不到（非绝对 → isabs 为 False）
    joined = os.path.join("d:", "xwechat_files")
    check("旧写法的候选不是绝对路径（这就是 bug 本体）", os.path.isabs(joined), False)
finally:
    dbmod.os.path.isdir = REAL_ISDIR
    dbmod.os.listdir = REAL_LISTDIR

print("\n--- D. 配置文件内容解析 ---")
BS = chr(92)          # 反斜杠，避开一切转义歧义
check("内容就是 d:" + BS + " → 原样返回",
      _extract_path_from_config("d:" + BS), "d:" + BS)
check("正则抽到的双重尾分隔符 → 规范化成盘根（旧写法会变 d:）",
      _extract_path_from_config("savePath = d:" + BS + BS), "d:" + BS)
check("正则抽到的普通路径", _extract_path_from_config("savePath = c:" + BS + "Users" + BS + "me"),
      "c:" + BS + "Users" + BS + "me")
check("JSON dataDir", _extract_path_from_config('{"dataDir": "e:' + BS + 'wx"}'),
      "e:" + BS + "wx")
check("解析结果必须仍是绝对路径",
      os.path.isabs(_extract_path_from_config("savePath = d:" + BS + BS)), True)

print("\n--- E. 真机：本机布局在任意启动目录下结果一致 ---")
os.chdir(SRC)
first = dbmod.auto_detect_db_dir()
check("本机自动检测有结果", bool(first), True)
if first:
    check("返回值是绝对路径", os.path.isabs(first), True)
    for target in (os.path.expanduser("~"), "c:" + SEP, SRC):
        try:
            old = os.getcwd()
            os.chdir(target)
            again = dbmod.auto_detect_db_dir()
            os.chdir(old)
            check("换启动目录 %s 结果不变" % target, again, first)
        except Exception as exc:
            print("     跳过（目录不可用）:", type(exc).__name__, exc)
    # 直接构造「盘符根 + 尾分隔符」这一形态喂进去（issue 里的真实输入形状）
    parent_like = os.path.dirname(os.path.abspath(first)) + SEP   # 带尾分隔符的父目录
    hit = _locate_account_root(parent_like)
    check("以带尾分隔符的父目录为 root 能定位到同一处",
          os.path.normcase(hit or ""), os.path.normcase(os.path.abspath(first)))
    check("以带尾分隔符的父目录为 root 结果仍是绝对路径", bool(hit) and os.path.isabs(hit), True)

print("\n--- F. 反向验：换回旧写法（rstrip）必须让定位失败 ---")
real_abs_root = dbmod._abs_root
FAKE_DIRS = {"d:" + SEP, TRUE_ROOT, TRUE_ROOT + SEP + "wxid_abc" + SEP + "db_storage"}
FAKE_LIST = {"d:" + SEP: ["xwechat_files"], TRUE_ROOT: ["wxid_abc"]}
f_isdir, f_listdir = make_fake_fs(FAKE_DIRS, FAKE_LIST)


def probe_under_old_impl():
    dbmod._abs_root = lambda p: (p or "").rstrip("\\/")      # 旧实现
    dbmod.os.path.isdir = f_isdir
    dbmod.os.listdir = f_listdir
    try:
        return dbmod._locate_account_root("d:" + SEP)
    finally:
        dbmod._abs_root = real_abs_root
        dbmod.os.path.isdir = REAL_ISDIR
        dbmod.os.listdir = REAL_LISTDIR


def probe_under_new_impl():
    dbmod.os.path.isdir = f_isdir
    dbmod.os.listdir = f_listdir
    try:
        return dbmod._locate_account_root("d:" + SEP)
    finally:
        dbmod.os.path.isdir = REAL_ISDIR
        dbmod.os.listdir = REAL_LISTDIR


old_style = probe_under_old_impl()
check("旧写法定位不到（证明这套断言真能咬住回归）", old_style, None)
check("换回新写法立刻又能定位", probe_under_new_impl(), TRUE_ROOT)

print("\n--- G. 盘符无关：本机每个真实盘 + 假想的 E:/Z: 都要一视同仁 ---")
import string                                    # noqa: F403
for letter in string.ascii_uppercase[:6] + "Z":  # A-F 与 Z，含本机存在的 C/D
    drv = letter + ":"
    check("%s:\\ 规范化后仍是盘根且绝对" % letter,
          (_abs_root(drv + SEP), os.path.isabs(_abs_root(drv + SEP))),
          (drv + SEP, True))
    only = _abs_root(drv)                        # 只给盘符（旧写法在这必翻车）
    check("%s: 裸盘符补成分隔符后 join 是绝对路径" % letter,
          os.path.isabs(os.path.join(only, "xwechat_files")), True)

# 旧写法在真实盘上的解析目标确实会随「该盘当前目录」漂移（E:\ 不存在时跳过）
for letter in ("c", "d"):
    drv = letter + ":" + SEP
    if not os.path.isdir(drv):
        continue
    old_cand = os.path.join(letter + ":", "xwechat_files")
    subs = [p for p in os.listdir(letter + ":") if os.path.isdir(letter + SEP + p)]
    if not subs:
        continue
    probe_a, probe_b = None, None
    old_cwd = os.getcwd()
    try:
        os.chdir(letter + ":" + SEP + subs[0])
        probe_a = os.path.abspath(old_cand)
    finally:
        os.chdir(old_cwd)
    try:
        os.chdir(drv)
        probe_b = os.path.abspath(old_cand)
    finally:
        os.chdir(old_cwd)
    check("%s 盘上旧写法的解析目标随该盘当前目录漂移（%s ≠ %s）"
          % (letter.upper(), probe_a, probe_b), probe_a == probe_b, False)
    check("%s 盘上新写法恒为 %sxwechat_files" % (letter.upper(), drv),
          os.path.join(_abs_root(drv), "xwechat_files"), drv + "xwechat_files")

print("\n--- H. Windows 文档重定向：保留注册表优先级和原有回退路径 ---")
with TemporaryDirectory() as directory:
    redirected = Path(directory) / "redirected"
    profile = Path(directory) / "profile"
    redirected_root = redirected / "xwechat_files"
    legacy_root = profile / "Documents" / "xwechat_files"
    registry_root = Path(directory) / "configured"
    for root in (redirected_root, legacy_root, registry_root):
        (root / "test_account" / "db_storage").mkdir(parents=True)
    with patch.dict(os.environ, {"USERPROFILE": str(profile)}), \
         patch.object(dbmod, "_config_candidates", return_value=[]), \
         patch.object(dbmod, "_registry_data_dirs", return_value=[]) as registry, \
         patch.object(dbmod, "_windows_documents_dir", return_value=str(redirected)) as documents:
        check("从重定向的文档目录定位", dbmod.auto_detect_db_dir(), str(redirected_root))
        registry.return_value = [str(registry_root)]
        check("微信的注册表设置优先", dbmod.auto_detect_db_dir(), str(registry_root))
        registry.return_value = []
        documents.return_value = None
        check("系统目录不可用时回退原路径", dbmod.auto_detect_db_dir(), str(legacy_root))

print("\n结果：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
for n in FAILED:
    print("  失败:", n)
sys.exit(1 if FAILED else 0)
