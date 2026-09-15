# -*- coding: utf-8 -*-
"""密钥提取失败诊断脚本。

在微信【已登录】状态下运行（务必让微信窗口保持打开）：

    python -m wechatauto.diagnose_keys

或直接：

    python wechatauto/diagnose_keys.py

把输出完整发给维护者。
"""
import json
import os
import subprocess
import sys
import tempfile
import traceback

print("=" * 60)
print("wechatauto-replica key diagnostic")
print("=" * 60)
print("Python:", sys.version.split()[0])
print("Python bits:", 64 if sys.maxsize > 2**32 else 32)
print("OS:", sys.platform)

# 1. library version
try:
    import wechatauto
    from wechatauto import WeChatDB
    from wechatauto.db import _find_account_dirs
    print("lib version:", getattr(wechatauto, "__version__", "?"))
    import wechatauto.db as dbmod
    print("db.py:", dbmod.__file__)
except Exception as e:
    print("import error:", repr(e))
    traceback.print_exc()

# 2. Weixin processes
print("\n--- Weixin processes ---")
weixin_pids = []
try:
    r = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True)
    print("tasklist:\n%s" % (r.stdout.strip() or "(no Weixin.exe)"))
    for line in r.stdout.strip().splitlines():
        parts = line.strip('"').split('","')
        if len(parts) >= 2 and parts[1].isdigit():
            weixin_pids.append(int(parts[1]))
except Exception as e:
    print("tasklist failed:", repr(e))

# 2b. per-PID permission / read test (key root-cause for silent 0-key extraction)
print("\n--- per-PID access test ---")
if not weixin_pids:
    print("(no Weixin.exe running - open WeChat and log in first)")
else:
    import ctypes
    from ctypes import wintypes
    from wechatauto.db import _MBI
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t)]
    k32.VirtualQueryEx.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MBI), ctypes.c_size_t]
    for pid in weixin_pids:
        h = k32.OpenProcess(0x0010 | 0x0400, False, pid)  # VM_READ | QUERY_INFORMATION
        if not h:
            err = ctypes.get_last_error()
            print("PID %d: OpenProcess FAILED (error %d - likely needs admin / same-elevation)" % (pid, err))
            continue
        ok = 0
        first_region = None
        addr = ctypes.c_void_p(0)
        for _ in range(4000):
            mbi = _MBI()
            n = k32.VirtualQueryEx(h, addr, ctypes.byref(mbi), ctypes.sizeof(_MBI))
            if n == 0:
                break
            if (mbi.State == 0x1000 and (mbi.Protect & 0xFF) & 0xE6
                    and not (mbi.Protect & 0x100) and 0 < mbi.RegionSize < 0x10000000):
                buf = ctypes.create_string_buffer(8)
                br = ctypes.c_size_t(0)
                if k32.ReadProcessMemory(h, ctypes.c_void_p(mbi.BaseAddress or 0), buf, 8, ctypes.byref(br)) and br.value == 8:
                    ok += 1
                    if first_region is None:
                        first_region = mbi.BaseAddress or 0
                    break
            addr = ctypes.c_void_p((mbi.BaseAddress or 0) + mbi.RegionSize)
        ctypes.windll.kernel32.CloseHandle(h)
        print("PID %d: OpenProcess OK, first readable region: %s (readable-region check %s)"
              % (pid, "0x%x" % first_region if first_region else "NONE", "OK" if ok else "FAILED"))

# 3. data dir detection
print("\n--- data dir ---")
try:
    from wechatauto.db import auto_detect_db_dir
    d = auto_detect_db_dir()
    print("auto_detect_db_dir:", d)
    if d and os.path.isdir(d):
        import time as _t
        for x in sorted(os.listdir(d)):
            sub = os.path.join(d, x, "db_storage")
            if not os.path.isdir(sub):
                continue
            newest = max((os.path.getmtime(os.path.join(r, f))
                          for r, _, fs in os.walk(sub) for f in fs
                          if f.endswith(".db")), default=0)
            print("  account dir: %-34s 最近修改: %s"
                  % (x, _t.strftime("%Y-%m-%d %H:%M", _t.localtime(newest))
                     if newest else "?"))
except Exception as e:
    print("dir detect error:", repr(e))

# 4. WeChatDB init (uses cached keys)
print("\n--- WeChatDB init ---")
db = None
try:
    db = WeChatDB()
    print("workdir:", db.workdir)
    print("keys_file:", db.keys_file, "exists:", os.path.exists(db.keys_file))
    print("account:", db.account)
    if os.path.exists(db.keys_file):
        with open(db.keys_file, encoding="utf-8") as f:
            keys = json.load(f)
        print("keys cached:", len(keys))
        for k in sorted(keys):
            print("   ", k)
    else:
        print("keys cached: (file not found)")
    print("db_files:", len(db._db_files))
    print("keys loaded:", len(db._keys))
    missing = [rel for rel, _, _ in db._db_files if rel not in db._keys]
    print("missing:", missing)
    print("unkeyed:", db.unkeyed)
    # which account dirs exist vs which one was picked
    print("accounts on disk:", sorted(
        os.path.basename(x) for x in _find_account_dirs(db.db_dir)))
    print("picked account:  ", db.account)
except Exception as e:
    print("init error:", repr(e))
    traceback.print_exc()

# 5. fresh key extraction from memory
print("\n--- extract_keys from Weixin.exe memory ---")
try:
    if db is None:
        db = WeChatDB()
    pids = db._find_weixin_pids()
    print("Weixin PIDs:", pids)
    if not pids:
        print("no Weixin.exe running - open WeChat and log in first")
    else:
        keys = db.extract_keys()
        print("extracted:", len(keys))
        for k in sorted(keys):
            print("   ", k)
        missing2 = [rel for rel, _, _ in db._db_files if rel not in keys]
        print("still missing:", missing2)
except Exception as e:
    print("extract error:", repr(e))
    traceback.print_exc()

# 5b. WeChat client version（区分“新版微信/旧版微信”）
print("\n--- WeChat client version ---")
try:
    import psutil
    seen = set()
    try:
        import win32api
    except Exception:
        win32api = None
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if (proc.info.get("name") or "").lower() != "weixin.exe":
                continue
            exe = proc.info.get("exe")
            if not exe or exe in seen:
                continue
            seen.add(exe)
            print("PID %s: %s" % (proc.info.get("pid"), exe))
            if win32api is None:
                continue
            try:
                info = win32api.GetFileVersionInfo(exe, "\\")
                ms, ls = info["FileVersionMS"], info["FileVersionLS"]
                print("   FileVersion: %d.%d.%d.%d"
                      % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF))
            except Exception as e:
                print("   FileVersion: 读取失败", repr(e))
        except Exception:
            continue
    if not seen:
        print("(未找到 Weixin.exe)")
except Exception as e:
    print("version check error:", repr(e))

# 5c. 主密钥 + 逐账号可用性（判定“密钥到底属于哪个账号”/“主密钥是否有效”）
print("\n--- master key / per-account verification ---")
try:
    if db is None:
        db = WeChatDB()
    master = getattr(db, "master_key", None)
    if master:
        print("extract_master_key: 已由构造流程取得")
    else:
        got = None
        try:
            got = db.extract_master_key()
        except Exception as e:
            print("extract_master_key error:", repr(e))
        if got:
            master = got[0]
            print("extract_master_key: OK (cfg_dword=%s, wxid=%s)" % (got[1], got[2]))
        else:
            print("extract_master_key: FAILED（微信未运行 / 权限不足 / 版本改动）")
    print("主密钥: %s" % ("已取得（不打印内容）" if master else "无"))
    print("各账号目录密钥可用性（缓存 / 主密钥现派生）:")
    for d in _find_account_dirs(db.db_dir):
        acct = os.path.basename(d)
        saved = (db.account, db.account_dir, db._db_files, db._keys,
                 db.workdir, db.keys_file)
        try:
            db.account, db.account_dir = acct, d
            db.workdir = os.path.join(tempfile.gettempdir(), "wechatauto_db", acct)
            db.keys_file = os.path.join(db.workdir, "keys.json")
            db._db_files = db._collect_db_files()
            db._keys = {}
            if os.path.exists(db.keys_file):
                try:
                    with open(db.keys_file, encoding="utf-8") as f:
                        for rel, hk in json.load(f).items():
                            try:
                                db._keys[rel] = bytes.fromhex(hk)
                            except ValueError:
                                pass
                except Exception:
                    pass
            ok_cache = sum(1 for rel, _, _ in db._db_files if db._key_works(rel))
            ok_derived = 0
            if master:
                db._keys = db.derive_keys_from_master(master)
                ok_derived = sum(1 for rel, _, _ in db._db_files if db._key_works(rel))
            print("   %-34s 库数 %2d  缓存可用 %2d  主密钥派生 %2d %s"
                  % (acct, len(db._db_files), ok_cache, ok_derived,
                     "<== 属于此账号" if (ok_cache or ok_derived) else ""))
        finally:
            (db.account, db.account_dir, db._db_files, db._keys,
             db.workdir, db.keys_file) = saved
    print("当前使用的账号: %s" % db.account)

    # 一致性：cfg 主密钥能否复现缓存里的库密钥（判定主密钥是否有效）
    try:
        from wechatauto.db import _pbkdf2, PAGE_SZ
        cf = os.path.join(tempfile.gettempdir(), "wechatauto_db", db.account, "keys.json")
        cached = {}
        if os.path.exists(cf):
            with open(cf, encoding="utf-8") as f:
                cached = json.load(f)
        rel0 = next((r for r, _, _ in db._db_files if r in cached), None)
        if master and rel0:
            with open(db._db_path(rel0), "rb") as f:
                p1 = f.read(PAGE_SZ)
            cand = _pbkdf2(bytes.fromhex(master), p1[:16], WeChatDB.KDF_ITER)
            same = cand.hex() == str(cached[rel0]).lower()
            print("一致性: cfg 主密钥 %s 复现缓存密钥(%s)"
                  % ("能" if same else "不能", rel0))
            if not same:
                print("         → 说明主密钥回退路径对该版本已失效（不影响内存扫描路径）")
        else:
            print("一致性: 缺少主密钥或缓存，跳过")
    except Exception as e:
        print("一致性检查失败:", repr(e))

    if not master:
        print("建议: 确认微信已登录且窗口打开；“以管理员运行”要与本脚本一致")
except Exception as e:
    print("master key check error:", repr(e))
    traceback.print_exc()

# 6. verify cached keys actually work
print("\n--- verify cached keys ---")
try:
    if db is None:
        db = WeChatDB()
    works = 0
    for rel, _, _ in db._db_files:
        if db._key_works(rel):
            works += 1
    print("keys that verify: %d / %d" % (works, len(db._db_files)))
except Exception as e:
    print("verify error:", repr(e))

print("=" * 60)
print("done. send the full output to the maintainer.")
print("=" * 60)