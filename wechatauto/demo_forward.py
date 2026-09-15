# -*- coding: utf-8 -*-
"""多账号消息自动转发演示 —— 监听指定聊天的新消息并自动转发给指定联系人

适用场景：
    多微信客户端（多账号同时登录）环境下，可指定监听账号 ``--account``，
    监听其某个聊天对象（好友 / 群聊）的新消息；一旦出现新消息，就自动通过
    GUI 转发给指定的联系人（默认「文件传输助手」，安全演示）。

用法：
    python -m wechatauto.demo_forward --list                    # 列出本机所有账号，不启动监听
    python -m wechatauto.demo_forward                            # 最近活跃账号，监听 文件传输助手
    python -m wechatauto.demo_forward --account wxid_xxx --from 群名 --to 中转站
    python -m wechatauto.demo_forward --from 兔仔仔 --to 文件传输助手 --dry-run
    python -m wechatauto.demo_forward --from 我的群 --to 小号 --verify --interval 2

参数：
    --list            只列出本机可用的微信账号，不启动监听
    --account         监听哪个账号（``list_accounts`` 的 account，wxid_xxx 或
                      完整目录名 wxid_xxx_abcd 均可）；缺省选最近活跃的账号
    --from            被转发的来源聊天对象（昵称/备注/群名/username），默认 文件传输助手
    --to              转发目标联系人（昵称/备注/username），默认 文件传输助手
    --interval        轮询间隔秒数，默认 1.0
    --prefix          转发消息前缀，默认「[来自 <来源名>]」
    --dry-run         只打印不真正发送（安全演练）
    --verify          发送后从所选账号的数据库读回确认（多账号下比 GUI 自带 verify 更准）
    --include-self    也转发自己发送的消息（默认跳过，避免回声循环）
    --media-out       媒体（图片/视频/语音/文件）还原后的下载目录，
                      默认 %TEMP%\\wechatauto_forward_media
    --hwnd            手动指定要操作的微信主窗口句柄（十进制或 0x 十六进制）；
                      多账号登录且自动选错窗口时使用

原理：
    监听用 :class:`wechatauto.db.Listener` 增量轮询本地 SQLCipher 解密数据库
    （按账号各自独立、互不干扰）；转发用 :class:`wechatauto.guia.WeChatGUI`
    在对应账号的微信主窗口上完成「打开会话 → 粘贴文本 → 点发送」。图片、
    视频、语音、文件等媒体消息会先用 :class:`wechatauto.media.MediaDownloader`
    从数据库还原出原始文件，再通过 GUI 本地路径原样发送（避免把微信消息
    XML 当文本转发）；还原失败时退回发送该消息的可读摘要（含标题/文件名）。

注意：
    转发是 GUI 操作，需要微信已登录、窗口可见且桌面未锁屏；多个微信同时
    登录时本脚本只自动定位「面积最大的主窗口」，若定位到了错误的账号窗口，
    请把 ``--list``/窗口提示中的正确句柄传给 ``--hwnd``。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import re
import sys
import tempfile
import time
from ctypes import wintypes

try:
    os.system("chcp 65001 >nul 2>&1")
except Exception:
    pass
# 监听回调在工作线程里 print；必须按行实时刷新，否则整段输出会被 stdout
# 缓冲住，按 Ctrl+C 退出后才一次性刷出来。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except AttributeError:
        pass

from wechatauto import WeChatDB, list_accounts
from wechatauto.db import Listener
from wechatauto.guia import (
    MAIN_TITLE_KEYWORDS,
    MIN_WINDOW_SIZE,
    PROCESS_NAME,
    WX_MAIN_WIN_CLASS_PREFIX,
    WeChatGUI,
)
from wechatauto.media import MediaDownloader


def _get_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _process_name(pid: int) -> str:
    """返回 pid 对应进程的可执行文件名（小写），失败返回空串。"""
    if not pid:
        return ""
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return os.path.basename(buf.value).lower() if ok else ""
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def find_main_windows() -> list:
    """枚举所有属于 weixin.exe 进程的可见顶层微信主窗口。

    返回按面积降序的 ``[{"hwnd", "pid", "title", "area"}]``，
    与 WeChatGUI._find_main_window 的判定口径一致（进程名 weixin.exe
    + 主窗口类名前缀 + 可见 + 尺寸足够大）。
    """
    u = ctypes.windll.user32
    found = []
    cb_ref = []

    def _cb(h, lp):
        if not u.IsWindowVisible(h):
            return True
        pid = _get_pid(h)
        if not pid or _process_name(pid) != PROCESS_NAME:
            return True
        cls = ctypes.create_unicode_buffer(256)
        title = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, cls, 256)
        u.GetWindowTextW(h, title, 256)
        if not cls.value.startswith(WX_MAIN_WIN_CLASS_PREFIX) \
                and not any(k in title.value for k in MAIN_TITLE_KEYWORDS):
            return True
        rect = wintypes.RECT()
        u.GetWindowRect(h, ctypes.byref(rect))
        w, ht = rect.right - rect.left, rect.bottom - rect.top
        if w <= 0 or ht <= 0 or max(w, ht) < MIN_WINDOW_SIZE:
            return True
        found.append({"hwnd": h, "pid": pid, "title": title.value.strip(), "area": w * ht})
        return True

    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    cb_ref.append(CB(_cb))
    u.EnumWindows(cb_ref[0], 0)
    found.sort(key=lambda x: -x["area"])
    return found


def build_gui(hwnd: int = None) -> WeChatGUI:
    """构造用于转发的 GUI，多账号时优先自动定位唯一的主窗口。

    显式传入 ``--hwnd`` 时严格用它；只有一个微信主窗口时自动用它；
    多个窗口时取面积最大的并打印全部候选（应对多账号选错窗口的情况）。
    """
    if hwnd:
        return WeChatGUI(hwnd=hwnd)
    wins = find_main_windows()
    if not wins:
        return WeChatGUI()  # 与库默认一致，找不到会给出明确报错
    if len(wins) > 1:
        print("⚠️  检测到多个微信主窗口，自动选择面积最大者（可用 --hwnd 指定）：")
        for i, w in enumerate(wins):
            mark = "  ← 选中" if i == 0 else ""
            print(f"    [{i}] hwnd={w['hwnd']} pid={w['pid']} title={w['title']}{mark}")
    return WeChatGUI(hwnd=wins[0]["hwnd"])


def resolve_account(raw: str, accounts: list) -> str:
    """把用户输入（wxid_xxx 或完整目录名）解析为账号目录名。"""
    if not raw:
        return accounts[0]["account"]
    for a in accounts:
        if raw in (a["account"], a["wxid"]):
            return a["account"]
    names = ", ".join(f"{a['wxid']} ({a['account']})" for a in accounts)
    raise SystemExit(f"未找到账号「{raw}」，本机可用账号：{names}")


def resolve_chat_username(db: WeChatDB, raw: str) -> str:
    """把输入（username / 昵称 / 备注 / 群名）解析成监听用的会话 username。"""
    if raw in ("filehelper", "文件传输助手"):
        return "filehelper"
    hits = db.search_contact(raw)
    if hits:
        return hits[0]["username"]
    gid = db.group_name_to_id(raw)
    if gid:
        return gid
    return raw


def resolve_target(db: WeChatDB, raw: str):
    """解析转发目标，返回 (GUI 搜索用显示名, 数据库 username)。

    GUI 按显示名（备注 > 昵称）在会话列表里 OCR 检索，username 留给
    ``verify=True`` 时的数据库读回确认。
    """
    if raw in ("filehelper", "文件传输助手"):
        return "文件传输助手", "filehelper"
    hits = db.search_contact(raw)
    if hits:
        h = hits[0]
        return (h.get("remark") or h.get("nick_name") or h.get("username")), h["username"]
    gid = db.group_name_to_id(raw)
    if gid:
        return db.group_id_to_name(gid) or raw, gid
    return raw, raw


def sender_name(db: WeChatDB, msg: dict) -> str:
    """把消息的发送者解析成可读昵称。"""
    if msg.get("sender_id") == 2:
        return "我"
    su = msg.get("sender_username") or msg.get("sender_id")
    if isinstance(su, str):
        return db.get_nickname(su) or su
    return str(su)


def verify_sent(db: WeChatDB, tgt_username: str, text: str, timeout: float = 5.0) -> bool:
    """从「所选账号」的数据库读回，确认转发的文本已作为自己发送的消息落库。

    GUI 层自带的 verify 用的是自动检测账号，多账号场景下可能读错账号，
    因此这里基于本 demo 指定的 db 自行读回确认。
    """
    first_line = (text.splitlines()[0] or text)[:40]
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            for m in db.get_messages(tgt_username, limit=5):
                if m.get("sender_id") == 2 and first_line in (m.get("content") or ""):
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def media_summary(body: str) -> str:
    """把微信消息 XML（图片/文档/链接卡片）压缩成可读摘要，避免整段 XML 被当文本转发。"""
    if not body:
        return ""
    if not body.startswith("<msg"):
        return body
    name = ""
    m = re.search(r"<title>([^<]*)</title>", body)
    if m:
        name = m.group(1).strip()
    if "<img " in body:
        md5m = re.search(r'md5="([0-9a-fA-F]{32})"', body)
        return "[图片]" + ((" md5=%s" % md5m.group(1)) if md5m else "")
    if name:
        return "[文件/链接] %s" % name
    return "[非文本消息]"


def _target_seq(db: WeChatDB, tgt_username: str) -> int:
    """目标会话当前最大 sort_seq，作为发送后读回校验的基线。"""
    try:
        msgs = db.get_messages(tgt_username, limit=1)
        return msgs[0]["sort_seq"] if msgs else 0
    except Exception:
        return 0


def verify_media_sent(db: WeChatDB, tgt_username: str, before_seq: int,
                      is_image: bool, timeout: float = 12.0) -> bool:
    """从「所选账号」的数据库读回，确认媒体已作为自己发送的图片/文件消息落库。

    发送媒体后微信异步落库较慢，轮询放宽到 timeout 秒。
    """
    expect = "图片" if is_image else "文件/链接/卡片"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for m in db.get_new_messages(tgt_username, since_seq=before_seq, limit=8):
                if m.get("sender_id") == 2 and m.get("type") == expect:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def make_forwarder(db, gui_holder, downloader, src_username, src_display,
                   tgt_display, tgt_username, prefix, dry_run, verify, include_self):
    """生成转发回调。callback(msg: dict, listener)"""

    def on_msg(msg: dict, lst: Listener):
        if not include_self and msg.get("sender_id") == 2:
            return  # 默认跳过自己发的消息，避免回声循环
        t = time.strftime("%H:%M:%S", time.localtime(msg["create_time"]))
        sender = sender_name(db, msg)
        body = (msg.get("content") or "").strip()
        mtype = msg.get("type", "")
        print(f"[转发] {src_display} | {sender}（{mtype}）")

        # 图片/视频/语音/文件：先把 XML 消息还原成原始媒体文件，避免把整段 XML 当文本转发
        media_path = None
        if mtype in ("图片", "视频", "语音", "文件/链接/卡片"):
            try:
                media_path = downloader.download_media(src_username, msg["local_id"])
            except Exception as e:
                print(f"  ⚠  媒体下载失败：{e}")
            if media_path:
                print(f"  已还原媒体：{os.path.basename(media_path)}")
            else:
                print(f"  内容：{media_summary(body)[:60]}")

        if dry_run:
            what = (f"发送媒体「{os.path.basename(media_path)}」" if media_path
                    else f"发送文本「{media_summary(body)[:30]}」")
            print(f"  [dry-run] 不发送，{what}，目标：{tgt_display}\n{'-' * 50}")
            return
        try:
            gui = gui_holder["gui"]
            if gui is None:
                gui = gui_holder["gui"] = build_gui()
                print(f"  已连接微信主窗口（hwnd={gui.main_hwnd}）")
            head = f"{prefix}{sender}（{t}）"
            if media_path:
                # 先发一段说明文字，再发原始附件
                before_seq = _target_seq(db, tgt_username)
                r1 = gui.send_msg(head, tgt_display)
                print(f"  → {tgt_display} 说明 => {r1.get('status')}")
                is_image = mtype == "图片"
                r2 = (gui.send_image if is_image else gui.send_file)(media_path, tgt_display)
                print(f"  → {tgt_display} 媒体（{os.path.basename(media_path)}）"
                      f"=> {r2.get('status')} {r2.get('message')}")
                if verify and r2.get("status") == "成功":
                    ok = verify_media_sent(db, tgt_username, before_seq, is_image)
                    print(f"  => 已读回确认" if ok else "  => 媒体读回未确认")
                return r2
            text = head
            summary = media_summary(body)
            if summary:
                text += "\n" + summary
            print(f"  → {tgt_display}（{sender}-{t}）")
            r = gui.send_msg(text, tgt_display)
            ok = r.get("status") == "成功"
            if ok and verify:
                ok = verify_sent(db, tgt_username, text)
            print(f"  => {r.get('status')} {r.get('message')}"
                  + ("（已读回确认）" if ok else "（读回未确认）"))
            return r
        except Exception as e:
            print(f"  ✗ 转发失败：{e}")
            return None

    return on_msg


def parse_args():
    p = argparse.ArgumentParser(description="多账号消息自动转发（监听指定聊天 → 转发给指定联系人）")
    p.add_argument("--list", action="store_true", help="列出本机所有微信账号后退出")
    p.add_argument("--account", default=None, help="监听账号（wxid 或账号目录名），缺省为最近活跃账号")
    p.add_argument("--from", dest="src", default="文件传输助手", help="来源聊天对象，默认 文件传输助手")
    p.add_argument("--to", dest="tgt", default="文件传输助手", help="转发目标联系人，默认 文件传输助手")
    p.add_argument("--interval", type=float, default=1.0, help="轮询间隔秒数，默认 1.0")
    p.add_argument("--prefix", default=None, help="转发前缀，默认「[来自 <来源名>]」")
    p.add_argument("--dry-run", action="store_true", help="只打印不发送，安全演练")
    p.add_argument("--verify", action="store_true", help="发送后从所选账号的数据库读回确认")
    p.add_argument("--include-self", action="store_true", help="也转发自己发送的消息")
    p.add_argument("--media-out", default=None, help="媒体下载目录（默认 %TEMP%\\wechatauto_forward_media）")
    p.add_argument("--hwnd", type=lambda v: int(v, 0), default=None, help="指定微信主窗口句柄（十进制或 0x 十六进制）")
    return p.parse_args()


def main():
    args = parse_args()

    accounts = list_accounts()
    if args.list:
        if not accounts:
            print("未找到任何微信账号（请确认微信已登录）")
            return
        print("本机微信账号列表：")
        for i, a in enumerate(accounts):
            t = time.strftime("%Y-%m-%d %H:%M:%S",
                              time.localtime(a["last_activity"])) if a["last_activity"] else "-"
            print(f"  [{i}] wxid={a['wxid']}")
            print(f"       account={a['account']}  最近活跃={t}")
        return
    if not accounts:
        print("未找到任何微信账号，请确认微信已登录且保持窗口打开")
        sys.exit(1)

    account = resolve_account(args.account, accounts)
    db = WeChatDB(account=account)
    self_info = db.get_self_info()
    print(f"账号：{self_info.get('nick_name') or self_info.get('username')}（{account}）")

    src_username = resolve_chat_username(db, args.src)
    src_display = args.src if src_username == args.src else src_username
    tgt_display, tgt_username = resolve_target(db, args.tgt)
    print(f"监听来源：{args.src} -> {src_username}")
    print(f"转发目标：{tgt_display} -> {tgt_username}")

    prefix = args.prefix if args.prefix is not None else f"[来自 {src_display}] "
    media_out = args.media_out or os.path.join(
        tempfile.gettempdir(), "wechatauto_forward_media")
    downloader = MediaDownloader(db, save_dir=media_out)
    gui_holder = {"gui": None}
    lst = Listener(db, interval=args.interval)
    lst.add_listener(src_username, make_forwarder(
        db, gui_holder, downloader, src_username, src_display,
        tgt_display, tgt_username,
        prefix, args.dry_run, args.verify, args.include_self,
    ))
    print("监听中（Ctrl+C 停止）...")
    lst.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        lst.stop()
        print("\n已停止监听。")


if __name__ == "__main__":
    main()