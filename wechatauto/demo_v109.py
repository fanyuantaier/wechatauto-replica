# -*- coding: utf-8 -*-
"""wechatauto v1.0.9 新功能演示 —— open_chat 账号搜索 / 语音通话 / 拍一拍 / UIA 表情截图

覆盖本次新增/优化能力：
    1. open_chat 账号（wxid）搜索修复：微信搜索框不认 wxid，自动经 DB
       映射为昵称/备注/微信号再搜索；
    2. Chat.VoiceCall() 发起语音通话（UIA 定位标题栏通话按钮）；
    3. Chat.Poke() 发起拍一拍（右键头像 + OCR 定位「拍一拍」菜单）；
    4. UIA 表情包精确截图（EmojiMessage.capture() 优先走 UIA 定位）。

用法：
    python demo_v109.py                          # 全部顺序演示（交互确认）
    python demo_v109.py 豆芽                     # 指定演示对象
    python demo_v109.py --only open_chat 豆芽    # 只跑某一步

注意：语音通话 / 拍一拍会真实触发操作，请确认目标联系人可接受。
"""
from __future__ import annotations

import os
import sys
import time

try:
    os.system("chcp 65001 >nul 2>&1")
except Exception:
    pass
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from wechatauto import WeChat

WHO_DEFAULT = "送你挖银子"
EMOJI_SAVE_DIR = os.path.join(os.path.expanduser("~"), "emoji_capture")


def _is_ok(r) -> bool:
    """ChatWith 成功返回会话显示名（str），失败返回 None。"""
    return isinstance(r, str) and bool(r)


def _confirm(prompt: str) -> bool:
    """交互确认，返回是否继续。"""
    try:
        return input(prompt + " [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return True


def demo_open_chat_by_wxid(wx: WeChat) -> None:
    """按账号（wxid）打开会话 —— 验证搜索框不认 wxid 的映射修复。"""
    print("\n" + "=" * 60)
    print("[1/4] open_chat 账号搜索（wxid 映射）")
    print("=" * 60)
    db = wx._db
    info = db.get_self_info()
    self_username = info.get("username", "")
    print(f"本机账号：{self_username}")
    print("从通讯录中找一个联系人账号（wxid）来测试：")
    contacts = db.search_contact("")[:20] or []
    if not contacts:
        print("  通讯录为空，跳过。")
        return
    for i, c in enumerate(contacts[:10]):
        name = c.get("nick_name") or c.get("remark") or c.get("username")
        print(f"  [{i}] {name}  (wxid={c.get('username')})")
    try:
        pick = int(input("  输入序号：").strip())
    except (EOFError, KeyboardInterrupt, ValueError):
        return
    contact = contacts[pick]
    username = contact.get("username", "")
    display = contact.get("nick_name") or contact.get("remark") or username
    print(f"  尝试 open_chat(wxid={username}) …")
    t0 = time.time()
    ok = wx._gui.open_chat(username)
    print(f"  => 打开 {display}（按 wxid）{'成功' if ok else '失败'} {time.time()-t0:.1f}s")
    if ok:
        uia = wx._gui._get_uia()
        cur = uia.current_chat() if uia is not None else None
        print(f"  当前会话确认：{cur}")


def demo_voice_call(wx: WeChat, who: str) -> None:
    """发起语音通话。"""
    print("\n" + "=" * 60)
    print("[2/4] 语音通话 VoiceCall")
    print("=" * 60)
    print(f"  将向「{who}」发起语音通话（真实呼出）…")
    if not _confirm("  确认拨出？"):
        print("  已跳过。")
        return
    r = wx.ChatWith(who)
    if not _is_ok(r):
        print(f"  打开会话失败：{r}")
        return
    t0 = time.time()
    r = wx._cur().VoiceCall()
    print(f"  VoiceCall => {r['status']} {r['message']} ({time.time()-t0:.1f}s)")
    print("  （请手动挂断结束通话）")


def demo_poke(wx: WeChat, who: str) -> None:
    """发起拍一拍。"""
    print("\n" + "=" * 60)
    print("[3/4] 拍一拍 Poke")
    print("=" * 60)
    print(f"  将对「{who}」发起拍一拍（真实触发）…")
    if not _confirm("  确认发送？"):
        print("  已跳过。")
        return
    r = wx.ChatWith(who)
    if not _is_ok(r):
        print(f"  打开会话失败：{r}")
        return
    t0 = time.time()
    r = wx._cur().Poke()
    print(f"  Poke => {r['status']} {r['message']} ({time.time()-t0:.1f}s)")


def demo_emoji_capture(wx: WeChat, who: str) -> None:
    """UIA 表情截图：找会话最新一条动画表情并截图。"""
    print("\n" + "=" * 60)
    print("[4/4] UIA 表情包精确截图")
    print("=" * 60)
    r = wx.ChatWith(who)
    if not _is_ok(r):
        print(f"  打开会话失败：{r.message}")
        return
    os.makedirs(EMOJI_SAVE_DIR, exist_ok=True)
    chat = wx._cur()
    msgs = chat.GetAllMessage()
    emo = [m for m in msgs if getattr(m, "type", None) == "emotion"]
    if not emo:
        print(f"  会话「{who}」最近 50 条无表情消息，跳过（可先发一个表情再跑）。")
        return
    msg = emo[0]
    print(f"  找到表情消息：方向={msg.attr}，开始 UIA 截图…")
    t0 = time.time()
    path = msg.capture(save_dir=EMOJI_SAVE_DIR)
    if path:
        size = None
        try:
            from PIL import Image
            size = Image.open(path).size
        except Exception:
            pass
        print(f"  => 截图成功 {size if size else ''} {time.time()-t0:.1f}s")
        print(f"     路径：{path}")
    else:
        print(f"  => 截图失败（{time.time()-t0:.1f}s）")


def main():
    args = [a for a in sys.argv[1:]]
    only = None
    if "--only" in args:
        i = args.index("--only")
        only = args[i + 1]
        args = args[:i] + args[i + 2:]
    names = [a for a in args if not a.startswith("--")]
    who = names[0] if names else WHO_DEFAULT

    wx = WeChat()
    print(f"当前登录：{wx.nickname}")

    steps = {
        #"open_chat": demo_open_chat_by_wxid,
        #"voice": demo_voice_call,
        "poke": demo_poke,
        #"emoji": demo_emoji_capture,
    }
    if only:
        fn = steps.get(only)
        if not fn:
            print(f"未知步骤：{only}（可选：{', '.join(steps)}）")
            sys.exit(1)
        fn(wx, who) if only != "open_chat" else fn(wx)
        return

    #demo_open_chat_by_wxid(wx)
    #demo_voice_call(wx, who)
    demo_poke(wx, who)
    #demo_emoji_capture(wx, who)
    #print("\n全部演示完成。")


if __name__ == "__main__":
    main()
