# -*- coding: utf-8 -*-
"""一条命令的常用入口：``python -m wechatauto <子命令>``。

存在的理由（issue #31「能不能把代码调用搞简单一点？太麻烦了，比如一条命令」）：能做的事
分散在 ``WeChatDB`` / ``MediaDownloader`` / ``WeChatGUI`` / ``MomentDB`` 四个对象上，
新用户得先搞清三件坑才跑得动——① 读库要传 username、驱动界面要传显示名（传错的表现为
搜索不命中，白等几十秒）；② 下载图片前得先有图片 AES 密钥；③ 保存目录要自己给。
这里把这三件事一次做完，**原有接口一字未改**：本模块只是这些接口的调用方，
不喜欢命令行的人照旧直接用那些对象。
"""
import argparse
import io
import json
import os
import sys


def _version():
    """版本号延迟到用时再取。

    ``from wechatauto import __version__`` 会连带把 ``guia``（UI 自动化那一整套）在
    import 时就拉进来——issue #8 报的就是「import 就把微信窗口激活/阻塞」这一类，
    命令行读个库不该碰界面。
    """
    import wechatauto
    return wechatauto.__version__

# 消息类型在库里是中文名（``文本`` / ``图片`` …），但用户在命令行里更可能打英文，
# 两种都收；否则「--type image 查不到」会被读成「这个会话没有图片」。
_TYPE_ALIASES = {
    "text": "文本", "文本": "文本",
    "image": "图片", "pic": "图片", "图片": "图片",
    "voice": "语音", "audio": "语音", "语音": "语音",
    "video": "视频", "视频": "视频",
    "file": "文件/链接/卡片", "文件": "文件/链接/卡片",
    "link": "文件/链接/卡片", "card": "文件/链接/卡片",
    "sticker": "动画表情", "表情": "动画表情",
    "system": "系统消息", "系统": "系统消息",
    "redpacket": "红包", "红包": "红包",
}


def _utf8_stdout():
    """Windows 控制台默认 GBK，中文正文和箭头会直接 UnicodeEncodeError。"""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _die(msg, code=1):
    print("× %s" % msg)
    return code


def _verify_opts(p):
    """发完回读数据库那条路的两个可调项（不传就用 WxParam 的默认值）。"""
    p.add_argument("--verify-interval", type=float,
                   help="回读轮询间隔秒，默认 1.0")
    p.add_argument("--verify-timeout", type=float,
                   help="回读等待上限秒，默认 8.0（设 0 = 只读一次）")
    return p


def _apply_verify_interval(args):
    from wechatauto.param import WxParam
    if getattr(args, "verify_interval", None) is not None:
        WxParam.DB_VERIFY_INTERVAL = args.verify_interval
    if getattr(args, "verify_timeout", None) is not None:
        WxParam.DB_VERIFY_TIMEOUT = args.verify_timeout
    return WxParam


def _db():
    from wechatauto import WeChatDB
    try:
        return WeChatDB()
    except Exception as e:
        raise SystemExit(_die(
            "打不开微信本地库：%s: %s\n"
            "  先确认微信已登录；再跑 `python -m wechatauto doctor` 看密钥。"
            % (type(e).__name__, e)))


def resolve_chat(db, who):
    """把用户给的任意一种写法补成 ``(username 给读库, display 给界面)``。

    这是 issue #31 那类「调用麻烦」的核心：``get_messages`` 认 username，而搜索框
    只认昵称/备注/微信号，拿 wxid 去搜永远不命中，表现成「白等几十秒然后返回 None」。
    """
    who = (who or "").strip()
    if not who:
        return "", ""
    if who == "filehelper" or who.endswith("@chatroom") or who.startswith("wxid_"):
        display = ""
        try:
            display = db.get_nickname(who) or ""
        except Exception:
            display = ""
        return who, (display or who)
    # 给的是显示名：先按昵称/备注/微信号查 username
    try:
        hits = db.search_contact(who) or []
    except Exception:
        hits = []
    exact = [h for h in hits if who in (h.get("nick_name"), h.get("remark"))]
    if len(exact) == 1:
        return exact[0]["username"], who
    if len(exact) > 1:
        raise SystemExit(_die(
            "「%s」同时是 %d 个联系人的昵称/备注，请改用 wxid 或群号"
            % (who, len(exact))))
    try:
        for s in db.get_sessions(limit=300):
            if s.get("username") == who:
                return who, who
    except Exception:
        pass
    # 查不到也照原样传下去：群名不在通讯录里是常态（只有群成员表有），
    # 读库那条路用的是 name2id，不依赖 contact.db。
    return who, who


def _body(m):
    """正文一行话。视频/文件/引用这类正文是整段 XML，直接打出来满屏都是标签。"""
    b = (m.get("content") or "").replace("\n", " ")
    if b.startswith("<?xml") or b.startswith("<msg>"):
        import re
        txt = " ".join(re.sub(r"<[^>]+>", " ", b).split())[:80]
        if not txt:
            # videomsg / appmsg 这一类信息全在属性上，刮标签刮不到东西
            at = dict(re.findall(r'\b(\w+)="([^"]{1,40})"', b))
            txt = " ".join("%s=%s" % (k, at[k]) for k in
                           ("playlength", "length", "size", "title", "des")
                           if k in at)[:80]
        return ("[非文本正文] %s" % txt).strip()
    return b[:400]


def _fmt_line(m, sender=""):
    t = m.get("create_time")
    try:
        import datetime
        t = datetime.datetime.fromtimestamp(float(t)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        t = str(t)
    who = sender or m.get("sender_username") or ""
    return "%s %s%s" % (t, ("%s: " % who) if who else "", _body(m))


# ------------------------------------------------------------------ 子命令
def cmd_doctor(args):
    from wechatauto import WeChatDB
    print("wechatauto %s | Python %s (%d 位) | %s"
          % (_version(), sys.version.split()[0],
             64 if sys.maxsize > 2 ** 32 else 32, sys.platform))
    try:
        db = WeChatDB()
    except Exception as e:
        return _die("没有可用账号/库（微信登录了吗？）：%s: %s"
                    % (type(e).__name__, e))
    ok = sum(1 for rel, _, _ in db._db_files if db._key_works(rel))
    print("账号：%s（wxid %s）" % (db.account, db.wxid or "未取到"))
    print("数据库密钥：%d/%d 个库可解密" % (ok, len(db._db_files)))
    if not ok:
        print("  → 一个都解不开：微信要正在运行且已登录；32 位 Python 请换 64 位。"
              "深挖跑 `python -m wechatauto.diagnose_keys`")
    from wechatauto.media import MediaDownloader
    md = MediaDownloader(db)
    try:
        key = md.detect_image_key()
    except Exception as e:
        key = None
        print("图片密钥：探测抛错 %s" % type(e).__name__)
    print("图片密钥：%s" % ("已拿到（已缓存，之后离线可用）" if key else "没有——图片下不了，"
                          "在微信里点开任意一张图后重试"))
    from wechatauto.uia_driver import WeChatUIA
    uia = WeChatUIA()
    try:
        live = uia.is_materialized()
    except Exception:
        live = False
    print("控件树（发消息/下载原图要用）：%s" % ("已物化" if live else
          "拿不到——微信需要热激活一次，见 GUIDE §「控件树被屏蔽」"))
    return 0 if ok else 1


def cmd_sessions(args):
    db = _db()
    rows = db.get_sessions(limit=args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    for s in rows:
        try:
            name = db.get_nickname(s["username"]) or s["username"]
        except Exception:
            name = s["username"]
        print("%-28s 未读 %-3s %s" % (name[:26], s.get("unread"),
                                      (s.get("summary") or "")[:40]))
    print("共 %d 个会话" % len(rows))
    return 0


def _messages(db, who, limit, offset, type_filter):
    user, display = resolve_chat(db, who)
    rows = db.get_messages(user, limit=limit, offset=offset) or []
    if type_filter:
        want = _TYPE_ALIASES.get(type_filter.lower(), type_filter)
        rows = [r for r in rows if r.get("type") == want]
    return user, display, rows


def cmd_messages(args):
    db = _db()
    user, display, rows = _messages(db, args.chat, args.limit, args.offset,
                                    args.type)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    own = getattr(db, "wxid", "") or ""
    for m in reversed(rows):                     # 终端里自上而下按时间正序
        sender = "" if m.get("sender_username") == own else (m.get("sender_username") or "")
        print("[%s] %s" % (m.get("type"), _fmt_line(m, sender)))
    print("%s（%s）共 %d 条" % (display, user, len(rows)))
    return 0


def cmd_export(args):
    db = _db()
    user, display, rows = _messages(db, args.chat, args.limit, 0, args.type)
    rows = list(reversed(rows))
    out = args.out or ("%s_%s.txt" % (display or user,
                                      __import__("time").strftime("%Y%m%d")))
    with io.open(out, "w", encoding="utf-8") as f:      # 永远 UTF-8，不跟控制台较劲
        for m in rows:
            f.write(_fmt_line(m) + "\n")
    print("已导出 %d 条 → %s" % (len(rows), os.path.abspath(out)))
    return 0


def cmd_send(args):
    if not (args.text or args.file or args.image):
        return _die("要发东西：`send 你好 --to 文件传输助手`，或 --file / --image")
    _apply_verify_interval(args)
    from wechatauto.guia import quick_send, quick_send_file, quick_send_image
    db = _db()
    _user, display = resolve_chat(db, args.to or "filehelper")
    if args.text:
        r = quick_send(args.text, display, verify=args.verify)
    elif args.file:
        r = quick_send_file(args.file, display, verify=args.verify)
    else:
        r = quick_send_image(args.image, display, verify=args.verify)
    print("%s：%s" % (getattr(r, "get", lambda *_: "")("status") or "?",
                      getattr(r, "get", lambda *_: "")("message") or ""))
    return 0 if r else 1


def cmd_images(args):
    db = _db()
    user, display = resolve_chat(db, args.chat)
    from wechatauto.media import ImageKeyUnavailable, MediaDownloader
    md = MediaDownloader(db, save_dir=args.out, image_key_scan_timeout=8.0)
    rows = db.get_image_rows(user, limit=args.limit) or []
    got = miss = bad = 0
    for r in rows:
        lid, shard = r["local_id"], r.get("shard")
        try:
            if args.original:
                out = md.download_image_original(user, lid, save_dir=args.out,
                                                 chat_name=display, shard=shard)
            else:
                out = md.download_image(user, lid, save_dir=args.out,
                                        tier=args.tier, shard=shard)
        except ImageKeyUnavailable:
            out = None
            if not args.original and args.tier is None:
                out = md.save_visible_image(user, lid, display, save_dir=args.out, shard=shard)
            if not out:
                bad += 1
                print("  · local_id=%s 未取得图片密钥，未导出" % lid)
                continue
        if out:
            got += 1
            print("  ✓ %s" % out)
        elif args.original:
            bad += 1
        else:
            miss += 1
            st = md.image_status(user, lid, shard=shard)
            print("  · local_id=%s 本机档位=%s reason=%s"
                  % (lid, st.get("tiers"), st.get("reason")))
    print("%s：%d 张已存 / %d 张本机没有 / %d 张没拿到"
          % (display or user, got, miss, bad))
    if miss and not args.original:
        print("提示：本机没有的那些，加 --original 会驱动微信界面去下载（会真的动你的微信窗口）")
    return 1 if bad else 0


def cmd_listen(args):
    db = _db()
    from wechatauto.db import Listener
    lst = Listener(db, interval=args.interval)
    nick = {}

    def cb(msg, _l):
        # 回调给的是 username（`chat` 键），终端上打 wxid 没人看得懂，昵称查不到再退回
        who = msg.get("chat") or msg.get("username") or ""
        if who not in nick:
            try:
                nick[who] = db.get_nickname(who) or who
            except Exception:
                nick[who] = who
        print("[%s] %s" % (nick[who], _fmt_line(msg,
                                                msg.get("sender_username") or "")),
              flush=True)

    if args.all:
        lst.add_all(cb)
        print("监听所有会话（Ctrl+C 退出）…", flush=True)
    else:
        user, display = resolve_chat(db, args.chat)
        lst.add_listener(user, cb)
        print("监听 %s（Ctrl+C 退出）…" % display, flush=True)
    try:
        import time
        lst.start()
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass                      # Ctrl+C 落在 start 里也要走到下面的 stop
    finally:
        lst.stop()
    return 0


def cmd_moments(args):
    db = _db()
    from wechatauto import MomentDB
    mo = MomentDB(db)
    feeds = mo.get_my_moments(limit=args.limit) if args.me else \
        mo.get_moments(limit=args.limit)
    if args.json:
        print(json.dumps(feeds, ensure_ascii=False, indent=2))
        return 0
    for f in feeds:
        print("%s | %s | 图 %d 视频 %d 赞 %d 评 %d"
              % (f.get("nickname"), (f.get("text") or "")[:40],
                 len(f.get("images") or []), len(f.get("videos") or []),
                 len(f.get("likes") or []), len(f.get("comments") or [])))
    print("共 %d 条（本机缓存里有的）" % len(feeds))
    return 0


# ------------------------------------------------------------------ 标签
def cmd_forward(args):
    """右键「转发」一条消息，分别发给指定人或整个标签（默认只预演）。"""
    from wechatauto.wx import WeChat
    _apply_verify_interval(args)
    wx = WeChat()
    dry = not args.go
    if args.fav:
        who = [w.strip() for w in (args.to or "").replace("，", ",").split(",")
               if w.strip()]
        if not who:
            return _die("转发给谁？--to 名字1,名字2")
        r = wx.ForwardFavorite(who, match=args.match, index=args.index,
                               scroll=args.scroll,
                               dry_run=dry, chunk=args.chunk,
                               verify=not args.no_verify)
        print("[%s] %s" % (r["status"], r.get("message")))
        _print_forward_data(r, dry)
        return 0 if r["status"] == "成功" else 1
    if args.label:
        r = wx.ForwardToLabel(args.label, chat=args.chat, match=args.match,
                              text=args.text,
                              dry_run=dry, limit=args.limit, chunk=args.chunk,
                              verify=not args.no_verify)
    else:
        who = [w.strip() for w in (args.to or "").replace("，", ",").split(",")
               if w.strip()]
        if not who:
            return _die("转发给谁？--to 名字1,名字2 或 --label 标签名")
        r = wx.ForwardMessage(who, chat=args.chat, match=args.match,
                              text=args.text,
                              dry_run=dry, chunk=args.chunk,
                              verify=not args.no_verify)
    print("[%s] %s" % (r["status"], r.get("message")))
    _print_forward_data(r, dry)
    return 0 if r["status"] == "成功" else 1


def _print_forward_data(r, dry):
    data = r.get("data") or {}
    if data.get("seed"):
        print("  先发给：%s（原件）" % data["seed"])
    for k in ("sent", "failed", "skipped"):
        got = data.get(k) or []
        if got:
            if k == "skipped":
                print("  跳过：%s" % "、".join(
                    "%s（%s）" % (x.get("display") or x.get("name") or x,
                                  x.get("reason", "")) if isinstance(x, dict) else x
                    for x in got[:20]))
            else:
                print("  %s：%s" % ({"sent": "已发", "failed": "失败"}[k],
                                    "、".join(
                                        (x.get("display") if isinstance(x, dict) else x)
                                        for x in got[:20])))
    if dry and r["status"] == "成功":
        print("  （预演：一条都没发出去。加 --go 才真发）")


def cmd_remark(args):
    """改联系人备注：通讯录 → 点开联系人 → 资料页「更多」→「设置备注和标签」。

    界面路径与「标签」同族（共用 LabelOps 的脚手架），实现在 wechatauto.remark。
    """
    from wechatauto.wx import WeChat
    if not (args.contact or "").strip():
        print("要改谁？`备注 小明 --to 新备注`；只想看路径通不通：`备注 小明 --probe`")
        return 2
    wx = WeChat()
    if getattr(args, "probe", False):
        r = wx.ProbeRemark(args.contact.strip())
    elif getattr(args, "clear", False):
        r = wx.RenameMember(args.contact.strip(), "", allow_empty=True,
                            verify=not getattr(args, "no_verify", False))
    else:
        new = (args.to or "").strip()
        if not new:
            print("要改成什么？--to \"新备注\"（清空备注用 --clear）")
            return 2
        r = wx.RenameMember(args.contact.strip(), new,
                            verify=not getattr(args, "no_verify", False))
    print("[%s] %s" % (r["status"], r.get("message")))
    data = r.get("data") or {}
    if data.get("who"):
        print("  联系人：%s" % data["who"])
    if data.get("before") not in (None, ""):
        print("  原备注：%s" % data["before"])
    if data.get("fields"):
        f = data["fields"] or {}
        print("  编辑窗：输入框=%s 当前备注=%r 按钮=%s"
              % (f.get("remark_edit"), f.get("current"), f.get("buttons")))
    for step, res in (data.get("steps") or []):
        print("  %-18s %s" % (step, res.get("reason") if isinstance(res, dict) else res))
    if data.get("reason"):
        print("  细节：%s" % data["reason"])
    return 0 if r["status"] == "成功" else 1


def cmd_groupfriend(args):
    """群成员加好友：打开群 → 聊天信息 → 搜索成员 → 添加到通讯录 → 发送。

    ``--dry-run`` / ``--probe`` 只走到资料卡看按钮，不点「添加到通讯录」。
    """
    members = [x.strip() for x in (args.members or "").split(",") if x.strip()]
    if not members and (args.member or "").strip():
        members = [args.member.strip()]
    if not (args.group or "").strip() or not members:
        print("要加谁？`加好友 群名 成员名` 或 `加好友 群名 --members 小明,小红`")
        return 2
    from wechatauto.wx import WeChat          # 只有真要驱动界面时才 import
    r = WeChat().AddFriendFromGroup(
        args.group.strip(), members=members, message=args.message,
        dry_run=bool(args.dry_run or args.probe))
    print("[%s] %s" % (r["status"], r.get("message")))
    data = r.get("data") or {}
    for x in data.get("results") or []:
        name = x.get("member") or x.get("selected") or "?"
        reason = x.get("reason") or ""
        extra = ""
        if reason == "already-friend":
            extra = "（已是好友，未点添加）"
        elif x.get("sent"):
            extra = "（已点发送）"
        elif x.get("add"):
            extra = "（可添加，dry-run 未点）"
        rows = x.get("results") or []
        print("  %-20s %s%s" % (name, reason, extra))
        if rows:
            print("      搜索结果（按屏幕顺序）：%s" % " / ".join(rows[:5]))
        if x.get("selected"):
            print("      选中第 1 行：%s" % x["selected"])
    return 0 if r["status"] == "成功" else 1


def cmd_labels(args):
    """通讯录标签：列表走库（不碰窗口），建/加/移走界面。"""
    action = args.action
    _apply_verify_interval(args)
    if action == "list":
        if getattr(args, "ui", False):
            from wechatauto.wx import WeChat      # 只有真要驱动界面时才 import
            r = WeChat().ListLabels(prefer="ui")
            rows = (r.get("data") or {}).get("labels") or []
            for x in rows:
                cnt = x.get("count")
                print("     %s%s" % (x["name"],
                                     "" if cnt is None else "  %d 人" % cnt))
            print("[%s] %s（读自界面左栏，含微信自己显示的人数）"
                  % (r["status"], r.get("message")))
            return 0 if r["status"] == "成功" else 1
        db = _db()
        try:
            rows = db.list_labels()
        except Exception as e:
            print("读不到标签表：%s（这个微信版本可能没有 contact_label）" % e)
            return 1
        for r in rows:
            print("%-4s %s" % (r["label_id"], r["name"]))
        print("共 %d 个标签（读自 contact.db，没有动窗口）" % len(rows))
        print("  删掉的标签在库里会滞后（实测：界面上没了，这里还留着）——"
              "要准数加 --ui")
        return 0

    from wechatauto.wx import WeChat          # 只有真要驱动界面时才 import
    wx = WeChat()
    if action == "probe":
        r = wx.ProbeLabels(dump_dir=args.dump)
    elif action == "create":
        r = wx.CreateLabel(args.label)
    elif action == "delete":
        r = wx.DeleteLabel(args.label)
    elif action == "members":
        r = wx.LabelMembers(args.label, limit=args.limit)
    elif action == "send":
        if not (args.text or "").strip():
            print("要发什么？--text \"...\"")
            return 2
        if not args.go and not args.dry_run:
            print("批量发送默认**不真发**：加 --dry-run 只看收件人，加 --go 才真的发出去。")
            return 2
        r = wx.SendToLabel(args.text, args.label, dry_run=not args.go,
                           limit=args.limit, verify=True)
    elif action == "rename":
        new = (args.to or "").strip()
        if not new:
            print("改名要给新名字：--label 老名 --to 新名（标签）或 --contact + --to（备注）")
            return 2
        # 有 contact 参数时是改备注，没给就是重命名标签
        if getattr(args, "contact", None):
            r = wx.RenameMember(args.contact, new)
        else:
            r = wx.RenameLabel(args.label, new)
    else:
        members = [m for m in (args.members or "").replace("，", ",").split(",")
                   if m.strip()]
        if not members:
            print("要 --members 谁？逗号分隔，例：--members 小明,小红")
            return 2
        if action == "remove":
            r = wx.RemoveLabelMembers(args.label, members)
        else:
            r = wx.AddLabelMembers(args.label, members)
    print("[%s] %s" % (r["status"], r.get("message")))
    data = r.get("data") or {}
    if data.get("reason"):
        print("  细节：%s" % data["reason"])
    if data.get("missing"):
        print("  没弄上：%s" % "、".join(data["missing"]))
    if data.get("count_before") is not None:
        print("  成员数：%s → %s" % (data.get("count_before"), data.get("count_after")))
    names = [x.get("display") for x in (data.get("recipients") or [])]
    if names:
        print("  收件人：%s" % "、".join(names[:30])
              + ("…（共 %d 人）" % len(names) if len(names) > 30 else ""))
    if data.get("skipped"):
        for sk in data["skipped"][:10]:
            print("  跳过：%s（%s）" % (sk.get("display"), sk.get("reason")))
    if action == "send":
        print(_send_cost_hint(len(names), bool(data.get("dry_run"))))
    for s in data.get("steps") or []:
        print("  %-18s %s%s" % (s.get("step"), "OK " if s.get("ok") else "停 ",
                                s.get("reason", "")))
        for lab in s.get("labels") or []:
            print("       %-12s %d 人" % (lab["name"], lab["count"]))
    return 0 if r["status"] == "成功" else 1


def _send_cost_hint(n: int, dry_run: bool) -> str:
    """按当前 rhythm 档位估一次批量发送要多久——不估就是骗人。"""
    if dry_run or not n:
        return "  （预演，一笔都没发）"
    try:
        from wechatauto import rhythm
        snap = rhythm.snapshot()
        glo, ghi = snap.get("gap") or [2.5, 6.0]
        burst, cool = snap.get("burst") or 6, snap.get("cooloff") or [30, 75]
    except Exception:
        return ""
    per = (glo + ghi) / 2.0 + 4.0            # 节流间隔 + 开会话/输入/回读
    rests = max(0, n // max(1, burst)) * ((cool[0] + cool[1]) / 2.0)
    total = n * per + rests
    return ("  预计耗时 ≈ %.0f 分钟（%d 人 × ~%.0fs + %d 次冷却，档位 %s）"
            % (total / 60.0, n, per, rests and n // max(1, burst),
               snap.get("profile", "natural")))


# ------------------------------------------------------------------ 装配
def build_parser():
    p = argparse.ArgumentParser(
        prog="python -m wechatauto",
        description="微信 4.x 本地库 + 界面自动化，一条命令版（原有 Python 接口不变）")
    p.add_argument("--version", "-V", "-v", action="version",
                   version="wechatauto %s" % _version())
    sub = p.add_subparsers(dest="cmd", metavar="子命令")

    def add(name, fn, help_text, **kw):
        s = sub.add_parser(name, help=help_text, description=help_text, **kw)
        s.set_defaults(func=fn)
        return s

    add("doctor", cmd_doctor, "一眼看清：账号 / 数据库密钥 / 图片密钥 / 控件树")

    s = add("sessions", cmd_sessions, "列出会话")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")

    s = add("messages", cmd_messages, "看某个会话最近的消息")
    s.add_argument("chat", help="会话名 / 昵称 / 备注 / wxid / 群号都行")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--type", help="text/image/voice/video/file/link/system 或中文名")
    s.add_argument("--json", action="store_true")

    s = add("export", cmd_export, "把一个会话的消息导出成文本文件")
    s.add_argument("chat")
    s.add_argument("--limit", type=int, default=500)
    s.add_argument("--type")
    s.add_argument("--out", help="默认 <会话名>_<日期>.txt（UTF-8）")

    s = add("send", cmd_send, "发消息 / 文件 / 图片（会驱动微信窗口）")
    s.add_argument("text", nargs="?", help="要发的文本")
    s.add_argument("--to", help="发给谁（默认文件传输助手）")
    s.add_argument("--file")
    s.add_argument("--image")
    s.add_argument("--verify", action="store_true", help="发完回读数据库确认")
    _verify_opts(s)

    s = add("images", cmd_images, "下载某个会话的图片")
    s.add_argument("chat")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--out", help="保存目录（默认库里的 DEFAULT_SAVE_PATH）")
    s.add_argument("--tier", choices=["original", "full", "best", "mid", "thumb"],
                   help="只读取指定的本机档位；省略时若未取得密钥，可从当前会话的微信预览窗保存可见图片")
    s.add_argument("--original", action="store_true",
                   help="本机没有原件就驱动微信界面去下载（真的会动你的窗口）")

    s = add("listen", cmd_listen, "监听新消息并打印（Ctrl+C 退出）")
    s.add_argument("chat", nargs="?", help="--all 时可省略")
    s.add_argument("--all", action="store_true", help="监听所有会话")
    s.add_argument("--interval", type=float, default=1.0)

    s = add("moments", cmd_moments, "看朋友圈（本机缓存里有的）")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--me", action="store_true", help="只看自己发的")
    s.add_argument("--json", action="store_true")

    s = add("labels", cmd_labels,
            "通讯录标签：list/members 读，create/add/remove/delete/rename 改，send 批量发")
    s.add_argument("action",
                   choices=["list", "create", "add", "remove", "delete", "rename",
                            "members", "send", "probe"],
                   nargs="?", default="list",
                   help="list=只看有哪些标签（不动窗口）；members=列某标签的全部成员；"
                        "send=按标签群发；probe=走一遍路径报哪步断；"
                        "rename=重命名标签 / 备注=改联系人昵称（见下）")
    s.add_argument("--label", help="标签名（除 list 外都要给）")
    s.add_argument("--to", help="rename 的新名字（或备注的 --contact + --to）")
    s.add_argument("--contact", help="改备注时指定联系人（与 --to 配合使用）")
    s.add_argument("--ui", action="store_true",
                   help="list 时读管理窗左栏（是准数，还带人数，但会动窗口）；"
                        "不给就只读库")
    s.add_argument("--members", help="逗号分隔的好友显示名或 wxid，例：小明,小红")
    s.add_argument("--text", help="send 的正文")
    s.add_argument("--dry-run", action="store_true",
                   help="send 时只解析收件人、一笔都不发（默认行为）")
    s.add_argument("--go", action="store_true", help="send 时真的发出去")
    s.add_argument("--limit", type=int, help="members/send 最多几个人")
    s.add_argument("--dump", help="probe 时把每步可见的控件名写到这个目录")
    _verify_opts(s)

    s = add("forward", cmd_forward,
            "右键「转发」一条消息：分别发给指定人或整个标签（默认只预演）")
    s.add_argument("--text", help="正文：给了就先发给名单里第一个人，再把这条转发给剩下的")
    s.add_argument("--to", help="逗号分隔的收件人，例：文件传输助手,送你挖银子")
    s.add_argument("--label", help="按标签转发：标签名（收件人=该标签全部成员）")
    s.add_argument("--fav", action="store_true",
                   help="转发收藏：点左侧「收藏」→ 右键一条收藏 → 转发"
                        "（条目用 --match 或 --index 定位，--chat 无效）")
    s.add_argument("--index", type=int, default=0,
                   help="（--fav）取可视区第几条完整露出的收藏，默认 0")
    s.add_argument("--scroll", type=int, default=0,
                   help="（--fav）先在收藏列表上滚几格再找，负数向上")
    s.add_argument("--chat", help="在哪个会话里右键；不给就用当前打开的会话")
    s.add_argument("--match", help="按文字定位那一条消息/收藏；不给就右键最新一条/第一条")
    s.add_argument("--chunk", type=int, help="一个转发窗勾几个人（默认 9）")
    s.add_argument("--limit", type=int, help="按标签时最多转发给几个人")
    s.add_argument("--dry-run", action="store_true",
                   help="只开窗勾选、核对按钮上的人数，最后点取消（默认行为）")
    s.add_argument("--go", action="store_true", help="真的发出去")
    s.add_argument("--no-verify", action="store_true",
                   help="发完不回读每个人的会话确认（默认回读）")
    _verify_opts(s)

    s = add("remark", cmd_remark, "改联系人备注（会驱动微信窗口）")
    s.add_argument("contact", nargs="?", help="联系人显示名（通讯录左栏里的名字）")
    s.add_argument("--to", help="新备注")
    s.add_argument("--probe", action="store_true",
                   help="只走一遍路径并打开编辑窗，随即关掉（不写任何东西）")
    s.add_argument("--clear", action="store_true", help="清空这个联系人的备注")
    s.add_argument("--no-verify", action="store_true", help="改完不回读数据库确认")

    s = add("addfriend", cmd_groupfriend,
            "群成员加好友：打开群 → 聊天信息 → 搜索成员 → 添加到通讯录 → 发送")
    s.add_argument("group", help="群名（界面显示名）")
    s.add_argument("member", nargs="?", help="成员名（群昵称/备注/昵称）")
    s.add_argument("--members", help="逗号分隔的多个成员名")
    s.add_argument("--message", help="好友申请留言；不给就用微信默认文案")
    s.add_argument("--dry-run", action="store_true",
                   help="只走到资料卡看能否添加/是否已是好友，不点「添加到通讯录」")
    s.add_argument("--probe", action="store_true", help="同 --dry-run")
    return p


# ------------------------------------------------------------------ 新手层
# 中文子命令：issue #31 的原话是「太麻烦了真的不会，比如一条命令」，让人先记住
# messages/export/images 这几个英文词就是第一道墙。这里只做一层查表翻译，参数定义
# 仍然只有一份（argparse），不会和英文入口漂移。
ALIASES = {
    "体检": "doctor", "诊断": "doctor",
    "会话": "sessions", "列表": "sessions", "ls": "sessions",
    "消息": "messages", "看消息": "messages", "msg": "messages", "读": "messages",
    "导出": "export", "存": "export",
    "图片": "images", "下载图片": "images", "图": "images", "dl": "images",
    "发": "send", "发送": "send", "发消息": "send",
    "听": "listen", "监听": "listen",
    "朋友圈": "moments",
    "标签": "labels", "打标签": "labels", "设标签": "labels",
    "转发": "forward", "转给": "forward",
    "收藏转发": "forward", "转收藏": "forward",
    "备注": "remark", "改备注": "remark", "设置备注和标签": "remark",
    "加好友": "addfriend", "加群友": "addfriend", "群加好友": "addfriend",
}

_MENU = (
    ("1", "看某个会话最近的消息", "messages"),
    ("2", "把一个会话导出成文本文件", "export"),
    ("3", "下载某个会话的图片", "images"),
    ("4", "发一条消息（会驱动微信窗口）", "send"),
    ("5", "盯着某个会话的新消息（Ctrl+C 停）", "listen"),
    ("6", "看朋友圈", "moments"),
    ("7", "列出会话（不知道名字时先看这个）", "sessions"),
    ("8", "体检：账号 / 密钥 / 控件树", "doctor"),
    ("9", "通讯录标签：看/建标签、给标签批量加移成员（会驱动微信窗口）", "labels"),
    ("10", "右键转发一条消息：发给指定人或整个标签（先预演）", "forward"),
    ("11", "改联系人备注（通讯录 → 资料页 → 设置备注和标签）", "remark"),
    ("12", "群成员加好友（群 → 聊天信息 → 搜索成员 → 添加到通讯录）", "addfriend"),
    ("0", "退出", None),
)


def expand_aliases(argv):
    argv = list(argv)
    for i, a in enumerate(argv):
        if a.startswith("-"):
            continue
        argv[i] = ALIASES.get(a, a)
        break
    return argv


def _ask(prompt):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return "0"


def _pick_chat(db, keep=""):
    """让新手挑会话，而不是让他想起 username 长什么样。"""
    try:
        rows = db.get_sessions(limit=10)
    except Exception:
        rows = []
    if rows:
        print("最近这些会话：")
        for i, s in enumerate(rows, 1):
            try:
                name = db.get_nickname(s["username"]) or s["username"]
            except Exception:
                name = s["username"]
            print("  %2d. %s（未读 %s）" % (i, name[:24], s.get("unread")))
        raw = _ask("要哪个？输入序号或会话名（回车=1，0=返回）"
                   + ("，上次是 %s" % keep if keep else "") + "：")
        if raw in ("", "0"):
            return None, keep
        if raw.isdigit() and 1 <= int(raw) <= len(rows):
            return rows[int(raw) - 1]["username"], rows[int(raw) - 1]["username"]
        return raw, raw
    raw = _ask("会话名（昵称 / 备注 / wxid / 群号都行，0=返回）：")
    return (None if raw in ("", "0") else raw), raw or keep


def menu():
    """不带子命令、又是人在终端前时走这里：一条命令 + 跟着提示走。"""
    _utf8_stdout()
    p = build_parser()
    db = _db()
    print("wechatauto %s —— 记不住命令就跟着提示走。输入 0 退出。" % _version())
    keep = ""
    while True:
        print()
        for key, label, _cmd in _MENU:
            print("  %s. %s" % (key, label))
        pick = _ask("要做什么？")
        if pick in ("", "0", "q", "quit"):
            return 0
        cmd = next((c for k, _l, c in _MENU if k == pick), None)
        if cmd is None:
            print("没有这个选项。")
            continue
        try:
            if cmd in ("moments", "sessions", "doctor"):
                a = p.parse_args([cmd])
                rc = int(a.func(a) or 0)
            elif cmd == "forward":
                kind = _ask("转发给谁：l=整个标签 / n=指定的人（默认 l）：").strip().lower()
                argv = ["forward"]
                if kind == "n":
                    argv += ["--to", _ask("收件人显示名，逗号分隔：")]
                else:
                    argv += ["--label", _ask("标签名：")]
                who = _ask("在哪个会话里右键？直接回车=当前打开的会话：").strip()
                if who:
                    argv += ["--chat", who]
                a = p.parse_args(argv)
                rc = int(a.func(a) or 0)
                # 菜单里也走「先预演、确认了才真发」那道闸（和 send 一样）
                if rc == 0:
                    if _ask("上面是预演，一条都没发。确认真发？(y=发)："
                            ).strip().lower() in ("y", "yes"):
                        a = p.parse_args(argv + ["--go"])
                        rc = int(a.func(a) or 0)
                    else:
                        print("没确认，这次只到预演为止。")
            elif cmd == "labels":
                act = (_ask("list=看有哪些标签(不动窗口) / members=看某标签的人 / "
                            "create=新建 / add=加成员 / remove=移成员 / "
                            "rename=改名 / delete=删标签 / send=按标签群发 / "
                            "probe=走一遍看哪步断：")
                       or "list").strip()
                if act not in ("list", "create", "add", "remove", "delete",
                               "rename", "members", "send", "probe"):
                    print("只认 list/members/create/add/remove/rename/delete/send/probe，"
                          "这次按 list 走。")
                    act = "list"
                argv = ["labels", act]
                if act != "list":
                    argv += ["--label", _ask("标签名：")]
                    if act == "rename":
                        argv += ["--to", _ask("改成什么名字：")]
                    if act in ("add", "remove"):
                        argv += ["--members",
                                 _ask("好友显示名，逗号分隔（例：小明,小红）：")]
                    if act == "send":
                        argv += ["--text", _ask("要发什么内容："), "--dry-run"]
                a = p.parse_args(argv)
                rc = int(a.func(a) or 0)
                if act == "send" and rc == 0:
                    # 群发是这个模块最贵的一档：上面那次是预演（--dry-run，一条都
                    # 没发出去），把收件人念给用户看完才问要不要真发。
                    if _ask("上面是预演，一条都没发。确认发给这些人？(y=发)："
                            ).strip().lower() in ("y", "yes"):
                        a = p.parse_args(argv + ["--go"])
                        rc = int(a.func(a) or 0)
                    else:
                        print("没确认，这次只到预演为止。")
            elif cmd == "addfriend":
                grp = _ask("群名（界面显示名）：").strip()
                ms = _ask("成员名，逗号分隔（例：小明,小红）：").strip()
                if not grp or not ms:
                    continue
                argv = ["addfriend", grp, "--members", ms, "--dry-run"]
                a = p.parse_args(argv)
                rc = int(a.func(a) or 0)
                if rc == 0:
                    if _ask("上面只打开资料卡看了按钮，没点添加。确认加好友？(y=加)："
                            ).strip().lower() in ("y", "yes"):
                        a = p.parse_args(["addfriend", grp, "--members", ms])
                        rc = int(a.func(a) or 0)
                    else:
                        print("没确认，这次只到勘察为止。")
            else:
                chat, keep = _pick_chat(db, keep)
                if not chat:
                    continue
                argv = [cmd, chat]
                if cmd == "send":
                    text = _ask("要发什么内容（0=返回）：")
                    if text in ("", "0"):
                        continue
                    argv = ["send", text, "--to", chat, "--verify"]
                args = p.parse_args(argv)
                rc = int(args.func(args) or 0)
            if rc:
                print("（这条没成功，上面有原因；输入 8 可以先体检）")
        except KeyboardInterrupt:
            print("\n已中断，回到菜单。")
        except SystemExit as e:          # 子命令里的明确报错不退出整个菜单
            if isinstance(e.code, str) and "wxid" in e.code:
                print(e.code)
            else:
                raise
    return 0


def main(argv=None):
    _utf8_stdout()
    p = build_parser()
    args = p.parse_args(expand_aliases(sys.argv[1:] if argv is None else argv))
    if not getattr(args, "cmd", None):
        # 人站在终端前 → 进菜单；脚本/管道里 → 照旧打印帮助并退 2（不能把 CI 挂住）
        if sys.stdin is not None and sys.stdin.isatty():
            return menu()
        p.print_help()
        return 2
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
