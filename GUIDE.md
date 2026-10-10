# wechatauto-replica 详细使用指南 / Detailed Usage Guide

> 面向**微信 4.x Windows 客户端**（非网页版）的自动化库。本文档覆盖从安装、
> 数据库读取、实时监听、消息发送、媒体下载、朋友圈到多账号与常见问题的
> 全部用法，并附带可直接运行的示例。
>
> Automation for the **WeChat 4.x Windows desktop client** (not the web version).
> This guide covers everything: installation, database reading, real-time
> listening, sending, media download, Moments, multi-account, and FAQ — with
> runnable examples throughout.

---

## 目录 / Table of Contents

1. [安装与准备 / Installation & Setup](#1-安装与准备--installation--setup)
1.5 [一条命令 / One-command CLI](#15-一条命令--one-command-cli)
2. [整体架构 / Architecture Overview](#2-整体架构--architecture-overview)
3. [数据库读取 / Database Reading (WeChatDB)](#3-数据库读取--database-reading-wechatdb)
4. [实时消息监听 / Real-time Listening](#4-实时消息监听--real-time-message-listening)
5. [发送消息 / Sending Messages](#5-发送消息--sending-messages)
6. [媒体下载 / Media Download](#6-媒体下载--media-download)
7. [朋友圈 / Moments](#7-朋友圈--moments)
8. [多账号 / Multi-account](#8-多账号--multi-account)
9. [导出聊天记录 / Export](#9-导出聊天记录--exporting-chat-history)
10. [群聊操作 / Group Chat](#10-群聊操作专题--group-chat-operations)
10.5 [通讯录标签 / Contact Labels](#105-通讯录标签--contact-labels)
10.6 [右键转发 / Forward a Message](#106-右键转发--forward-a-message)
11. [常见问题与排错 / FAQ](#11-常见问题与排错--faq--troubleshooting)
12. [API 速查表 / Quick Reference](#12-api-速查表--api-quick-reference)

---

## 1. 安装与准备 / Installation & Setup

### 1.1 环境要求 / Requirements

| 项目 / Item | 要求 / Requirement |
|---|---|
| 系统 / OS | Windows 10 / 11 |
| Python | 3.9+（已在 3.12 验证 / verified on 3.12） |
| 微信 / WeChat | 4.1.12+（数据库读取对版本不敏感 / DB reading is version-insensitive） |
| 登录状态 / Login | 微信必须**已登录**（数据库密钥在进程内存中）/ WeChat must be **logged in** (DB keys live in process memory) |

### 1.2 安装 / Install

```bash
pip install wechatauto-replica

# 发送路径需要额外依赖（OCR 兜底 + 拼音输入）/ Sending path needs extra deps (OCR fallback + pinyin IME):
pip install winsdk pypinyin
```

从源码开发 / From source:

```bash
git clone <仓库地址 / repo>
cd wechatauto-replica
pip install -e .
```

### 1.3 验证安装 / Verify

```python
import wechatauto
print(wechatauto.__version__)   # 1.1.5.1 (beta)
```

> ⚠️ 首次运行 `WeChatDB()` 会扫描微信进程内存提取数据库密钥，首次约 6 秒，
> 之后密钥缓存到本地，秒开。
> The first `WeChatDB()` call scans the WeChat process memory to extract DB keys
> (~6s). Keys are cached locally afterwards, so later runs are instant.

---

## 1.5 一条命令 / One-command CLI

issue #31：「能不能把代码调用搞简单一点？太麻烦了，比如一条命令」。`wechatauto/cli.py`
给了一层命令行入口，**原有 Python 接口一字未改**——CLI 只是下面那些对象的调用方，
不想写 Python 的人不必先学会 `WeChatDB` / `MediaDownloader` / `WeChatGUI` / `MomentDB`
四个对象怎么拼。

**新手只记一条**：`wechatauto`（或 `python -m wechatauto`）**不带任何参数**。它先做一次体检
（账号 / 数据库密钥 / 图片密钥 / 控件树），再把「看消息 / 导出 / 下载图片 / 发消息 / 监听 /
朋友圈」列成编号菜单；挑会话时直接列出最近 10 个会话（昵称 + 未读数）让你按序号选，不需要
知道 `username` 长什么样。菜单只在 stdin 是终端时进入——脚本/管道里 `python -m wechatauto`
照旧打帮助退 2，不会把 CI 挂在输入上。

`pip install` 之后还带一个控制台脚本 `wechatauto`（`pyproject.toml` 的 `[project.scripts]`），
所以连 `python -m` 都不用敲。子命令**中英都认**（`消息`=`messages`、`导出`=`export`、
`图片`=`images`、`发`=`send`、`听`=`listen`、`体检`=`doctor`、`会话`=`sessions`）——只是
一层查表翻译，参数定义仍然只有 argparse 那一份，不会两处漂移。

```bash
python -m wechatauto doctor                          # 账号 / 数据库密钥 / 图片密钥 / 控件树
python -m wechatauto sessions --limit 20             # 会话列表（--json 出结构化）
python -m wechatauto messages 文件传输助手 --limit 20 [--type image] [--json]
python -m wechatauto export 文件传输助手 --limit 500 [--out history.txt]
python -m wechatauto images 某群 --limit 50 [--out D:\img] [--tier best] [--original]
python -m wechatauto send 你好 --to 文件传输助手 [--verify] [--file x.pdf | --image x.png]
python -m wechatauto listen 某群 [--all] [--interval 1]
python -m wechatauto moments [--limit 10] [--me] [--json]
```

它替用户吸收的三件事，正是新手最容易卡住的地方：

| 坑 | 不吸收会怎样 | CLI 怎么做 |
|---|---|---|
| 读库要 `username`，驱动界面要显示名（微信搜索框不认 wxid） | 把 wxid 填进搜索框 → 搜不到 → 白等几十秒返回 `None`，日志里也看不出为什么 | `resolve_chat()`：`filehelper` / `wxid_*` / `*@chatroom` 直通；给昵称/备注就查 `contact.db` 补成 username；**同名多人时明确报错要你用 wxid，不随便挑一个**；通讯录里没有（群）就照原样传 |
| 下载图片要先有图片 AES 密钥 | 每张都返回 `None`，看起来像「库坏了」 | `images` 进门先 `detect_image_key()`，拿不到就直接说「在微信里点开任意一张图后重跑」，并提示 `doctor` 能看状态 |
| Windows 控制台是 GBK | 中文正文/箭头直接 `UnicodeEncodeError`，或打印成 `?` | `main()` 第一件事把 stdout/stderr 重设为 UTF-8（`errors="replace"`）；`export` 永远写 UTF-8 文件 |

另外两条是显示层的：视频/文件/引用这类正文是整段 XML，CLI 刮成 `[非文本正文] playlength=151
length=4211669` 这种一行提示（原文要用 `--json` 看）；`--type` 中英文都收（`image` 和 `图片`
等价），因为库里类型名是中文，只收中文会让人以为「这个会话没有图片」。

**边界要说清**：`send` / `listen` / `images --original` 会驱动真实客户端，走 `rhythm`
节流档（默认 `natural`），和直接用那些对象没有区别——CLI 不会绕过任何闸门，也不会替你
点「进入微信」。退出码：0 成功、非 0 失败、2 是没给子命令（打印帮助）。
`doctor` 是排查入口：它把「数据库密钥几张能用 / 图片密钥有没有 / 控件树物化没有」三件事
一次说完，这三件里任何一件为 0，后面的命令都会失败，而且失败原因各不相同。

---

## 2. 整体架构 / Architecture Overview

| 能力 / Capability | 技术路线 / Tech | 模块 / Module |
|---|---|---|
| **读消息 / Read** | 本地 SQLCipher 4 数据库解密 / Local SQLCipher 4 DB decryption | `db.py` (WeChatDB) |
| **实时监听 / Listen** | 数据库增量轮询 + 每会话工作线程 / DB incremental polling + per-chat workers | `db.py` (Listener) |
| **发消息 / Send** | UIA 优先，坐标 + OCR 兜底 / UIA-first, coordinate + OCR fallback | `guia.py`, `wx.py` |
| **媒体下载 / Media** | `.dat` AES 解密 / SILK 语音 / 文件复制 / `.dat` AES decrypt / SILK voice / file copy | `media.py` (MediaDownloader) |
| **朋友圈 / Moments** | `sns.db` 直读 + UIA / direct read + UIA | `moment.py` |

核心对象 / Core objects：

- `WeChatDB` —— 一切数据读取的入口（解密数据库、查消息、查联系人）/ entry point for all data reading
- `Listener` —— 实时监听器（轮询 + 工作线程）/ real-time listener
- `MediaDownloader` —— 媒体下载 / media download
- `WeChat` / `Chat` —— 面向发送的 wxauto 风格接口 / wxauto-style sending API
- `WeChatGUI` / `quick_send` —— 底层 GUI 驱动与便捷函数 / low-level GUI driver + convenience functions

---

## 3. 数据库读取 / Database Reading (WeChatDB)

### 3.1 初始化 / Init

```python
from wechatauto import WeChatDB

db = WeChatDB()          # 自动检测账号与数据目录 / auto-detect account & data dir
# db = WeChatDB(account="wxid_xxx")   # 多账号时指定 / specify account for multi-account
```

### 3.2 会话（聊天列表）/ Sessions (chat list)

```python
info = db.get_self_info()                  # 当前账号信息 / current account info
for s in db.get_sessions(limit=10):        # 会话列表 / session list
    print(s["username"], s["unread"], s["summary"])
```

`get_sessions()` 返回的 `username` 是**会话唯一标识** / unique session identifier：

- 私聊 / Private chat：`wxid_xxx`
- 群聊 / Group chat：`xxx@chatroom`

> ⚠️ 后续所有 API 都认 `username` 而非昵称。可用 `search_contact()` 转换。
> All APIs take `username`, not nickname. Use `search_contact()` to convert.

### 3.3 搜索联系人 / Search

```python
hits = db.search_contact("Ayi")            # 按昵称/备注/微信号模糊搜索 / fuzzy search
print(hits[0]["username"])                 # -> wxid_xxx 或 xxx@chatroom

nick = db.get_nickname("wxid_xxx")         # 反查昵称 / reverse lookup nickname
```

### 3.4 读取消息 / Read messages

```python
# 最近 N 条（按 sort_seq 降序）/ latest N (sort_seq desc)
msgs = db.get_messages("filehelper", limit=10)
for m in msgs:
    print(m["local_id"], m["type"], m["sender_id"], m["content"], m["create_time"])

# 单条原始行（媒体下载用，含 server_id / packed_info）/ single raw row
row = db.get_message_row("filehelper", 123)
```

消息 dict 字段 / Message dict fields：

| 字段 / Field | 含义 / Meaning |
|---|---|
| `local_id` | 消息 ID（下载媒体用）/ message ID (media download) |
| `type` | 中文类型：文本/图片/语音/视频/动画表情/文件/系统消息 |
| `sender_id` | 发送者 ID（`2` 表示自己；群聊是成员 ID）/ sender (`2` = self) |
| `content` | 内容（图片等已转换为可读摘要）/ content |
| `create_time` | 时间戳 / timestamp |
| `sort_seq` | 全局排序序号（增量监听用）/ global ordering |

### 3.5 增量消息（供轮询监听）/ Incremental messages

```python
new = db.get_new_messages("filehelper", since_seq=12345, limit=200)
```

### 3.6 按类型批量取媒体 ID / Batch media IDs

```python
# 返回该会话全部图片 local_id（不受总消息分页限制）/ all image IDs, ignores msg-limit
img_ids = db._find_media_rows("群名", {3})
# 类型码 / type codes：1文本 3图片 34语音 43视频 47动画表情 49文件
# 1 text, 3 image, 34 voice, 43 video, 47 emoji, 49 file
```

---

## 4. 实时消息监听 / Real-time Message Listening

两种方式：**db.Listener**（推荐，纯数据库轮询）和 **WeChat.AddListenChat**（wxauto 风格封装）。
Two ways: **db.Listener** (recommended, pure DB polling) and **WeChat.AddListenChat** (wxauto-style).

### 4.1 db.Listener（推荐 / recommended）

```python
from wechatauto import WeChatDB
from wechatauto.db import Listener

db = WeChatDB()
lst = Listener(db, interval=1.0)          # 每秒轮询一次 / poll every second

def on_msg(msg, lst):
    print(f"[{msg['type']}] {msg['sender_id']}: {msg['content']}")
    # 可在此扩展业务：关键词回复、媒体下载、通知推送等 / extend here

lst.add_listener("filehelper", on_msg)    # 参数是会话 username
lst.start()                               # 启动（后台线程）/ background thread
# ... 你的主程序逻辑 / your main logic ...
lst.stop()                                # 停止 / stop
```

**并发模型 / Concurrency model**：

- 轮询线程只读库 + 分派，不会被慢回调阻塞 / the poller never blocks on slow callbacks
- 每个会话一条独立工作线程：**同会话保序、跨会话并行** / per-chat worker: in-order per chat, parallel across chats
- 慢回调（AI 调用、图片识别）不影响整体监听 / slow callbacks don't affect polling

**监听无聊天记录的联系人 / Contact with no history**：消息表按需创建，对方发第一条消息后下次轮询即可捕获，只需 `add_listener("wxid_xxx", cb)`。

**watermark 持久化 / Watermark persistence**：监听器记录已消费的 `sort_seq`，下次启动可传入避免重复推送。

**跨分片读取 / Cross-shard reads**：会话消息表 `Msg_<md5>` 横跨多个 `message_*.db` 分片，`get_messages` / `get_new_messages` 已合并全部分片并按 `sort_seq` 排序，增量监听无重放。注意 `local_id` 跨分片不唯一，精确取单条时给 `get_message_row(user, local_id, local_type=类型码)`。

### 4.2 防撤回监听 / Anti-recall listener（`RecallGuard`）

```python
from wechatauto import WeChatDB, RecallGuard
from wechatauto.db import Listener

db = WeChatDB()
guard = RecallGuard(db)                    # 镜像库 + 媒体备份目录自动创建
lst = Listener(db, interval=1.0)
guard.watch(lst, backfill=50)              # 挂到监听器：镜像+备份+撤回检测一体
lst.start()
```

- 每收到一条**新消息**即写入独立镜像库（`mirror` 表），图片/语音/视频/文件附件增量备份到 `media/` 目录；
- 检测到撤回系统消息（`revokemsg`）时，终端打印 `[撤回] 撤回者 撤回了一条消息` + 原文（镜像反查窗口内最近消息）+ 媒体备份路径；
- 撤回事件记入 `recall_events` 表，事后用 `guard.get_recalled(chat=None, limit=50)` 查询；`watch(backfill=N)` 预回填当前库最近 N 条历史提升回溯成功率（历史原文常因微信本地删行已不在，能恢复多少取决于回填时机）；
- 一行式示例：`python -m wechatauto.demo_recall`（`--backlog=N` 预回填 N 条，`--all` 监听全部会话）。

### 4.2 WeChat.AddListenChat（wxauto 风格 / wxauto-style）

```python
from wechatauto import WeChat
from wechatauto.msgs import TextMessage, ImageMessage

wc = WeChat()

def on_msg(msg, chat):
    print(f"[{msg.type}] {chat.who}: {msg.content}")
    if isinstance(msg, ImageMessage):
        md = MediaDownloader(chat._db)
        out = md.download_image(chat._wxid, msg.local_id)

wc.AddListenChat(nickname="群名", callback=on_msg)   # 传昵称即可，内部解析
wc.GetListenMessage()        # 阻塞监听循环（Ctrl+C 退出）/ blocking listen loop
# 或 / or wc.KeepRunning()
```

**回调里的「谁发的」** / who sent it

`msg` 由数据库行转换而来，发送者身份分三步解析：`messages.real_sender_id`
（数字 rowid）→ **该行所在分片** `message_N.db` 的 `Name2Id` → 真 wxid → `contact.db`
的备注/昵称。

> issue #34：这一步以前用的是 `message_resource.db` 的 `SenderName2Id`，那是一张**编号空间
> 完全不同的表**——同一个数字解析成无关的人，所以群消息的发送者名字会整体错位。本机复验：
> 12 个群里 3662 条带「发送者:」前缀的文本行，按分片解析 **3662 条全对**，按那张全局表
> **0 条对**（3656 条指到别人身上）。自己账号在各分片 `Name2Id` 里的 rowid 实测是 **2/4/1**，
> 所以也不存在「编号 2 就是自己」。

```python
def on_msg(msg, chat):
    msg.sender_wxid     # 真 wxid（群消息也能拿到，不再只有文本能刮正文前缀）
    msg.sender          # 备注或昵称；解析不到时退回 wxid，再退回会话名
    msg.sender_remark   # 同上（备注优先）
    msg.wxid            # 自己发的消息 = 自己的 wxid（不再是常量 2）
    msg.attr            # 'self' / 'friend'
    msg.local_id, msg.sort_seq, msg.create_time
```

群里那些**不在你通讯录**的人只有成员表能给名字：

```python
members = chat.GetGroupMembers()   # [{username, nick_name, remark, is_owner}, ...]
wc.GetGroupMembers()               # 当前会话是群时同样可用；非群返回 []
db.get_nickname("wxid_xxx")        # 备注 > 昵称 > 原样返回
db.nickname_map()                  # 一次性 wxid→昵称 字典，带缓存（批量场景用它）
db.username_by_nickname("阿Q")      # 反查 wxid
```

> 注：`msg.sender` 在群里以前返回的是正文里的 `wxid_xxx`，现在返回昵称；需要原始
> wxid 时用 `msg.sender_wxid`。`Name2Id`（本分片那张）里查不到这个编号时仍会从正文
> `wxid_xxx:\n` 前缀兜底，所以文本消息的行为不变。
>
> 覆盖率（8 个真实群、1249 条历史消息实测）：能拿到一个发送者映射的行 **95.8%**，非文本
> **99.6%**。**这两个数衡量的是「有没有拿到一个 wxid」，不是「拿到的对不对」**——那批编号
> 当时是按全局表解析的，人基本都指错了（见上面 issue #34 的 3662 对 0）。
> 换成按分片解析后本机重测：60243 行里 **99.8%** 解析得出，剩下 0.2%（129 行，全在同一个
> 分片）留在「未知」；解析得到的部分与「正文里的发送者前缀」这条独立判据 **100% 一致**。
> 解析不到的那些是微信自己没留身份的行：`real_sender_id` 在本片 `Name2Id` 里查不到，
> `source` 里也没有任何用户名标签，库里造不出来的东西就不造——`msg.sender_wxid` 保持空串，
> `msg.sender` 退回会话名。

`WeChat` 还提供 / also offers：

```python
wc.GetSession()            # 会话列表 / session list [SessionItem]
wc.ChatWith("filehelper")  # 切换当前会话 / switch current chat
wc.GetAllSubWindow()       # 所有会话窗口 / all chat windows
```

### 4.3 监听所有会话 / Listen to every chat (`AddListenAll`)

不想一个个点名时用全局监听。/ Register one callback for **all** sessions instead of naming them.

```python
from wechatauto import WeChat

wc = WeChat()

def on_any(msg, chat):
    print(f"[{chat.nickname}] {msg.content}")
    # 需要直接回话也可以：（第一次用时才构造真正的 Chat，并缓存下来）
    # chat.SendMsg("收到")

wc.AddListenAll(on_any)      # discover=True（默认）：之后新出现的会话也自动纳管
wc.GetListenMessage()        # 或 wc.KeepRunning()
# wc.RemoveListenAll()      # 停止全局监听
```

- **和 `AddListenChat` 可以并存**：一个会话能挂多个回调。已经单独监听过的会话
  同样会收到全局回调（早期实现会跳过它们，等于全局监听漏掉最活跃那批会话）。
- `chat.who` 是会话 `username`，`chat.nickname` 是显示名；`chat.SendMsg(...)`
  走的是普通发送路线。
- 只读数据库轮询，不点界面、不抢前台，因此和 UI 自动化互不影响。
- 更底层（自定义轮询间隔、水位持久化）用 `db.Listener`：

```python
from wechatauto.db import WeChatDB, Listener
lis = Listener(WeChatDB(), interval=1.0)   # 水位默认存到 workdir/listener_watermark.json
lis.add_all(on_any, discover=True)         # 与 AddListenAll 同一套语义
lis.start(); ...; lis.stop()
```

---

## 5. 发送消息 / Sending Messages

### 5.1 快速函数 / Quick functions (guia)

```python
from wechatauto.guia import (
    quick_send, quick_send_file, quick_send_image, quick_reply,
)

quick_send("你好", "filehelper", verify=True)          # 文本，verify=True 从库回读确认
quick_send_file(r"D:\report.pdf", "filehelper")         # 文件 / file
quick_send_image(r"D:\photo.png", "filehelper")         # 图片 / image
quick_reply("回复内容", "filehelper", 123)              # 回复某条消息 / reply
```

### 5.2 WeChat / Chat 对象（wxauto 风格 / wxauto-style）

```python
from wechatauto import WeChat

wc = WeChat()
chat = wc.ChatWith("filehelper")          # 或 / or Chat("filehelper", wc._gui, wc._db)

resp = chat.SendMsg("你好")                # 发送到当前会话 / send to current chat
resp = chat.SendMsg("大家好", "群名", at=["@张三", "@李四"])  # 群聊 @ 成员 / group @members
resp = chat.SendFiles([r"D:\a.pdf", r"D:\b.docx"])          # 多个文件 / multiple files
```

### 5.3 消息对象操作 / Message objects

```python
msgs = chat.GetAllMessage()               # 全部消息 / all messages
new = chat.GetNewMessage()                # 新消息 / new messages
last = chat.GetLastMessage()              # 最后一条 / last message

for m in msgs:
    print(m.type, m.content, m.sender, m.create_time)
```

### 5.4 语音通话 / 拍一拍 / 撤回 / Voice call / Poke / Recall

```python
chat.VoiceCall()                          # 语音通话 / voice call
chat.VoiceCall(video=True)                # 视频通话 / video call
chat.Poke()                               # 拍一拍 / poke
chat.RecallLastMessage()                  # 撤回最近一条自己发的消息 / recall latest own message
```

### 5.5 转发语音 / Forward voice

```python
chat.ForwardVoiceMessage(target="群名")    # 从当前会话提取语音转成文件发送
```

### 5.6 发送的验证机制（防误发）/ Anti-misdelivery verification

`send_msg` 链路带**目标对象三重校验**（UIA 路径）/ triple target verification (UIA path)：

1. `open_chat` 打开后从 UIA 树读回输入框名称比对 / reads back input-box name after opening
2. 发送前确认 `current_chat() == 目标` / confirms current chat is the target
3. `verify=True` 时从数据库回读确认消息落库 / reads back from the DB to confirm

**目标不在好友/会话列表时安全失败**，不会误发给当前打开的会话（区别于旧版 wxauto3）。
**If the target isn't in your list, sending fails safely** — never falls back to the current chat.

---

### 5.7 拟人节奏与写动作节流 / Human pacing & write throttling (`rhythm`)

微信风控看到的是**动作的时间分布**（固定间隔、光标传送、永远命中控件正中、匀速键入、
几秒内连发），所以这层做在库里、默认生效。/ WeChat risk control sees the *time
distribution* of actions, so pacing lives in the library and is on by default.

```python
from wechatauto import rhythm
rhythm.set_profile('natural')   # 默认 / default：间隔 2.5-6s，120s 内 6 次，突发后冷却 30-75s
rhythm.set_profile('calm')      # 长时间挂机 / 真人会话：间隔 6-14s，300s 内 3 次
rhythm.set_profile('fast')      # 录屏赶时间：仍然非匀速，只是贴近原速
rhythm.set_profile('off')       # 精确还原这层之前的行为，只用于对照实验
print(rhythm.snapshot())        # 当前档位、距上次写动作秒数、窗口内计数
```

- **只管写动作**：发消息 / 发文件 / 点赞 / 评论 / 撤回 / 拍一拍 / 语音通话。读库、截图、
  OCR、定位控件不节流。/ Reads are never throttled.
- 环境变量单项覆盖（无需改代码）/ single-field overrides via env：
  `WECHATAUTO_RHYTHM=natural|calm|fast|off`、`WECHATAUTO_WRITE_GAP=秒`、
  `WECHATAUTO_WRITE_BURST=次`。
- 节流状态在 `~/.wechatauto/rhythm.json`，**跨进程生效**（每个脚本都是新进程）。档位本身
  不落盘，`rhythm.reset()` 只清计数，所以误设的 `off` 不会串到下一次会话。
- 想给某个等待加抖动：`rhythm.nap(0.5)` 而不是 `time.sleep(0.5)`——倍率下限 1.0，只会等得更久，
  不会把原来撑渲染稳定性的等待缩短。

## 6. 媒体下载 / Media Download

### 6.1 初始化与密钥 / Init & keys

```python
from wechatauto import WeChatDB, MediaDownloader

db = WeChatDB()
md = MediaDownloader(db)                       # 默认保存到 ~/Documents/wechatauto_media
# md = MediaDownloader(db, save_dir=r"D:\media")   # 指定保存目录 / specify save dir
```

图片 AES 密钥处理 / Image AES key handling：

```python
md.detect_image_key()          # 扫描进程内存提取密钥（首次需要，之后持久化）
# md = MediaDownloader(db, image_key="16位密钥")   # 或手动注入 / inject manually
```

> ⚠️ 图片 AES 密钥仅在**微信中点开图片查看**时驻留内存约 5 分钟。首次运行请先在
> 微信里点开任意一张图；`detect_image_key(monitor=True)` 可自动轮询等待；找到后
> 持久化到 `image_keys.json`，之后无需再扫。
> The image AES key is only resident while **viewing an image in WeChat** (~5 min).

### 6.2 下载 API / Download API

```python
# 按类型自动分发（3图片 34语音 43视频 49文件）/ auto-dispatch by type
out = md.download_media("filehelper", 123, save_dir=r"D:\media")

out = md.download_image("filehelper", 123)      # jpg/png/gif（旧行为）
out = md.download_image("filehelper", 123, tier="best")    # 原件 > .dat > 预览图，档位标在文件名（§6.2.2）
out = md.download_image("filehelper", 123, tier="full")    # 只要「不是 _t.dat」的那一份，本机没有就 None（不碰界面）
out = md.download_voice("filehelper", 123)      # .silk
out = md.download_video("filehelper", 123)      # .mp4
out = md.download_file("filehelper", 123)       # 原文件 / original file

# 要**确定的原件 `_h.dat`**：本机有就解密落盘，没有就驱动界面去下载
out = md.download_image_original("filehelper", 123, timeout=30)
```

返回落盘路径，失败返回 `None`。 / Returns the saved path, or `None` on failure.

### 6.2.1 语音取不到时，先分清是谁的问题 / Why some voices have no audio

`download_voice()` 返回 `None` 有两种完全不同的原因，以前分不出来
（issue #20「26 条语音只识别到 19 条」就卡在这个歧义上）：

```python
for v in md.list_voice_status("wxid_xxx", limit=500):
    v['available'], v['reason'], v['bytes'], v['download_status'], v['self_sent']

md.voice_status("wxid_xxx", local_id)     # 单条，字段同上
```

`reason` 取值：

| reason | 含义 | 能怎么办 |
|---|---|---|
| `ok` | 音频在 `media_*.db` 的 `VoiceInfo` 里，能取字节 | 正常下载 |
| `audio_not_downloaded` | 微信**没把这段音频落盘**（`download_status=0`） | 读库无能为力；在微信里播放一次就会落盘 |
| `audio_missing_from_media_db` | `download_status` 说该有，`VoiceInfo` 里却没有 | 这才是可能的库侧问题，值得开 issue |
| `session_not_in_media_index` | 该会话在 media 库里连 `Name2Id` 条目都没有 | 同上，通常也是没落盘 |
| `no_server_id` / `no_voice_row` | 消息行缺 `server_id` / 这条不是语音 | 数据本身的问题 |

判据来自实测：本机 20 个会话 958 条语音里，`download_status != 0` 与「音频在本地」
**一一对应，无一例外**（898 可用 / 54 未落盘 / 6 会话无索引，可用率 93.7%），
所以 `download_status` 可以直接当「微信有没有下载」的标志用；新实现与独立复算
在这 958 条上逐条一致。

> 想补齐那 6%，只能在界面上把语音播放一遍（微信随后会把 `voice_data` 写进
> `VoiceInfo`）。那属于驱动真实客户端的写动作，要走 `rhythm`，本库没有自动化它。

### 6.2.2 图片有三档，先问清楚本机有没有原图 / Which image tier is on this machine

一条图片消息在 `msg/attach/<md5(会话)>/<YYYY-MM>/` 下最多落三个文件。**本库里「原图」一词
只指 `_h.dat`**（确定的原件），因为中间那一档在本机**判不出来**是什么：

| 文件 | 是什么 | 什么时候落盘 |
|---|---|---|
| `<md5>_t.dat` | **预览图**（缩略图） | 收到消息就有 |
| `<md5>.dat` | **微信下发的那一份** —— 可能是完整图，也可能**本身就是预览版**；本机看不出来 | 点开过 / 收的时候本机就有 |
| `<md5>_h.dat` | **原件**（发的时候勾了「原图」，或点过「查看原图」才会落盘） | 只有那两种情况 |

`.dat` 判不出来的实测例：一条消息本机档位 `{mid: 44002, thumb: 2961}`，那张 44KB 的
`.dat` **仍是预览图**——尺寸大不代表就是完整图，所以本库不再把 `.dat` 叫「完整图」，
也不拿它冒充成果。唯一能回答「还有没有更大那份」的是预览窗里有没有那颗「图片原始大小」
按钮（真机点一下才有答案）。

「只能下到缩略图、下不到原图」的反馈基本都出在这三档的歧义上——以前
`download_image` 压根不看 `_h.dat`，而且拿到 `.dat` 时文件名和原件一模一样，
调用方只能靠大小猜，于是反复重试。

```python
out = md.download_image("群名", 123, tier="full")       # 「不是 _t.dat」的那一份：_h.dat > .dat
out = md.download_image("群名", 123, tier="original")   # 只要原件，没有就 None，不降级
out = md.download_image("群名", 123, tier="best")       # 原件 > .dat > 预览图
out = md.download_image("群名", 123)                    # 不传 tier = 旧行为，逐字不变
```

| tier | 取哪一档 | 落盘文件名 | 本机没有时 |
|---|---|---|---|
| `None`（默认） | `.dat` → 预览图 | `<user>_<lid>.jpg` / `..._thumb.jpg` | 退预览图 |
| `'original'` | 只有 `_h.dat`（原件） | `..._h.jpg` | `None`（**不悄悄降级**） |
| `'full'` | `_h.dat` → `.dat`，**绝不用预览图档交差**；**只读本机，不碰界面**。注意交回来的 `.dat` 有可能本身就是预览版 | 按档位标 `_h` / 无 | 本机只有预览图时 `None`（要追原件用 `download_image_original()`） |
| `'best'` | 原件 → `.dat` → 预览图 | 按档位标 `_h` / 无 / `_thumb` | `None` |
| `'mid'` / `'thumb'` | 只要这一档 | 无 / `_thumb` | `None` |

**`download_image` 的口径就一句话：本机有什么就交什么，一次界面都不碰。** 要「确定的原件」
（本机没有就驱动界面去下载）用 `download_image_original()`；`'full'` 和 `'best'` 只保证
「不是预览图**档**」，不保证那张图不是预览版。

批量前先问一遍，别逐条试：

```python
for im in md.list_image_status("wxid_xxx", limit=300):
    im['reason'], im['best'], im['tiers'], im['local_id']

md.image_status("wxid_xxx", local_id)      # 单条，字段同上
```

| reason | 含义 | 能怎么办 |
|---|---|---|
| `ok` | `_h.dat`（原件）在、不是空壳；`verify=True` 时还要求解密后结构完整 | `tier='original'` 直接拿 |
| `mid_only` | 有 `.dat` 这一份（`has_mid=True`），只是没有原件那一档。**`.dat` 是完整图还是预览版本机判不出来** | 只想「本机有什么就交什么」→ `tier='full'`/`'best'`（只读本机，一步界面都不碰）；要确定的原件 → `download_image_original()`，它会走界面去问微信还有没有更大那份；**自己发的图本机一般没有原件**（见下表） |
| `only_thumbnail` | 只有预览图（群聊图从没点开过，`has_mid=False`） | `download_image_original()`：点开预览本身就会让微信把那份下下来 |
| `original_partial` | 有 `_h.dat` 但是空壳（<1KB），或 `verify=True` 时解密后**缺 JPEG/PNG 收尾标记**（下到一半） | 再触发一次 |
| `no_local_copy` / `no_md5` / `no_message_row` | 目录里一份都没有 / 取不到图片指纹 / 这条不是图片 | 核对 `local_id` 与账号目录 |

字段里**没有** `has_full` 那种东西：本机既判不出 `.dat` 是不是完整图，就不假装有这个答案。
只有 `available`（有没有 `_h.dat`）和 `has_mid`（有没有 `.dat` 这一份）两个事实位。

「原图下好了没」看的是**结构和空壳**，不是尺寸比例：非空、≥1KB；`image_status(..., verify=True)`
会再解密看一眼有没有 `FF D9` / `IEND` 收尾（批量接口不要开，那等于把每张原图都解一遍）。
**两代按尺寸猜的判据都被实测否掉了**：

- 1.2.4.2 之前是 `> 102400`：本机 705 个 `_h.dat` 中位数只有 91.5KB、51.3% 在 100KB 以下，
  一半真原图被当成「还没下载」。
- 1.2.4.2 换成「不比压缩版小 10% 以上」，**用户实机证明这条也是错的**：同一张图的 `.dat`
  有时就是比 `_h.dat` 大（实测 h/mid 有 0.55、0.82 的，两种编码各存一份），于是真原图又被
  判成没下完，代码去点「图片原始大小」——而那颗按钮在查看器里只是切显示缩放，
  原图本来就在盘上时点了自然没有任何反应。

正在下载中的截断由 `_wait_tier` 的「大小不再变化」轮询负责，不靠尺寸猜。

本机 211 个会话 4825 条图片消息的实测分布（每会话取最近 400 条）：`mid_only` 2410、
`only_thumbnail` 1773、`ok` 636、`original_partial` 3（三条都是 <1KB 的空壳）、
`no_local_copy` 3。也就是说**多数图片本来就没有原图可下**（每 7 条里只有 1 条有），
这是微信的存储策略，不是解密失败；先查状态再决定要不要触发下载，能省掉大量无效重试。

**按发送方拆开看，差距非常大**（同一次扫描，按 `real_sender_id` 是不是本机账号分类）：

| 发送方 | 条数 | 有原件 `_h.dat` | 有 `.dat` 这一档 | 只有预览图 |
|---|---|---|---|---|
| 我自己发的 | 554 | **19（3.4%）** | 527（95.1%） | 8 |
| 别人发的 | 3401 | 611（18.0%） | 1594 | 1192 |

而且**那 19 条自发图片的 `_h.dat` 全都没有同名的 `.dat`**（别人发的 617 个 `_h.dat` 里
373 个是两档都有的）。合起来读：**自己发出去的图，只有当时勾了「原图」才在本机留下原件**；
没勾的话本机最好的一份就是那个 `.dat`。所以「我发的图片提示找不到原图」这句话里，图其实
一直在盘上（527/554 有 `.dat`）——只是那一档是不是完整图，本机答不了。

这一节以前写的是「本机有 `.dat` 就算拿到非预览图，`download_image_original()` 一步界面都不碰」。
**那条已经被实测推翻**（就是上面那张 44,002 字节的预览版 `.dat`），现在的口径是：
`download_image_original()` **只要本机没有 `_h.dat` 就强制走点击路径**，自己发的图也一样
（只打一条提示，不再提前返回）——「到底还有没有更大的那份」只有界面能回答。
`download_image()` 保持「本机有什么就交什么」，两条路的分工从此不重叠。
尺寸上也印证：自发图 `.dat` 中位 60KB／最大 770KB，别人发的 `.dat` 中位 58KB，
而 `_h.dat` 中位 66KB、p75 351KB、最大 15MB——**中位数这么小，任何按绝对大小的
判据都会误杀**。

**两类现象是同一个原因**：文件是在我们**超时之后**才落盘的。
- 「报错说取不到，但下一轮同一句代码又说已在本地」
- 「界面上看起来一直在乱点」
现在这条路上每个决策点都会先回头看一眼本机（`_harvest`：进门、点下一张之前、等档超时之后、
判失败之前）：**只认 `_h.dat`**（解密后结构完整才算拿到），盘上有了就交出去并停止后续界面
动作，查不到才明确报 `None`。补查这一步是为了停止瞎点，不是为了换个档位交差。

### 6.3 群聊图片 / Group chat images

- 群聊图片原图**只有点开查看过才落盘**；否则只有缩略图 / originals only stored after being opened
- `download_image` 会自动回退缩略图，文件名带 `_thumb` 标记 / auto-falls back to thumbnail (`_thumb`)；
  不想回退就传 `tier='original'`，本机没有原图时返回 `None`（见 §6.2.2）
- `download_image_original` 要的是**确定的原件 `_h.dat`**：本机有就解密落盘，**没有就强制走点击路径** / it targets `_h.dat` only, and drives the UI whenever that tier is missing
  - **`.dat` 不算拿到**：它可能是完整图也可能本身就是预览版（实测例见 §6.2.2），本机分不出来，所以本方法绝不拿它冒充成果。只想「本机有什么就交什么」请直接用 `download_image(tier='full')` / `tier='best'`
  - 自己发出去的图也照样走界面（只打一条提示，不提前返回）——那 527 条「本机只有 `.dat`」的自发图里到底有没有更大那份，只有界面能回答
  - 本机已经有 `_h.dat` 时**直接解密落盘，不动界面** / an existing original is decrypted without touching the UI
  - 会切到对应会话；目标那条图**不在可视区时会按数据库算出的行差滚过去**（`scroll=True`，默认开；`scroll=False` 就只在当前屏上找，绝不动界面）
  - **「哪一行才是这条图」用数据库定位**：可视区里文本行的 `Name` 就是真实正文（会被截断，所以按前 10 字互相包含来比），图片行的 `Name` 恒为「图片」，整段序列与数据库的「文本/图片」序列滑窗对齐，对得上就直接点名目标行。三道门槛缺一不可：
    - 至少**两条文本锚点**且吻合度 ≥0.6——实机撞过可视区只剩一行时「吻合度 1.00」的假高分，那种情况任何偏移都算完美吻合，等于没有信息；
    - **并列最优偏移必须给出唯一答案**。「T I I T」这种形状在整段历史里会重复出现，多个偏移同时满分是常态；只要并列偏移对「目标是第几行」答案不同就拒绝动手（`_align_window` 会把 `ties` 全部交出来）；
    - 对齐结果那一行在 UIA 里**确实是图片行**。
  - 滚过去的步长是**实测出来的**：先按「每格 1 行」假设滚，滚完看对齐偏移真的挪了几行再修正每格行数，所以不同 DPI、不同窗口高度都不需要预设常量。滚轮走 `moment._send_scroll`（先置前台 + 落点归属校验：曾经有一次自检把滚轮打进了压在微信上面的 IDE），发不出去就报 `scroll-blocked`，滚了但纹丝不动报 `scroll-stuck`，滚满 `max_scrolls`（默认 6 轮）报 `scroll-limit`——**不会无限滚**。认不出时退回「数它下面压了几张更新的图」，再不行逐个试
  - 类名映射是**对着数据库逐行核过的**，别按直觉改：`mmui::ChatTextItemView` 才是文本行；`mmui::ChatBubbleItemView` 是文件/链接/卡片（库里正文近 1900 字 XML，UIA 只给 43 字摘要），把它当文本行会把整段对齐带偏；`mmui::ChatItemView` 是时间行、`mmui::ChatSystemInfoItemView` 是系统消息——两侧同时都不参与对齐
  - **点气泡仍然只能用坐标**：`mmui::ChatBubbleReferItemView` 在 UIA 里是**叶子行**（用原始视图 `ControlFromPoint` 从行首横扫到 80% 行宽，返回的始终是这一行本身，没有缩略图子控件；竖屏下再按几何扫整棵树，**落在这行内部且比它小的节点是 0 个**），而它虽然挂着 `InvokePattern`，**Invoke 是空操作**（实机 0 预览窗起步、Invoke 后 8 秒不出现，和 4.1.15 那个搜索按钮的 `Invoke()` 空操作同一个形状）。所以只能按坐标点，而行矩形是**整行宽**
  - **气泡的位置不随行宽等比缩放，所以「按行宽取百分比」在窄窗口必然点空**（用户报「竖屏下点不中图片」）。两组实测（PrintWindow 拍窗口本体 + 像素扫描，行宽不同、气泡区间是**绝对像素**偏移）：

    | 布局 | 行矩形 | 行宽 | 气泡实际占（距行左边） | 行宽 12% 算出的点 |
    |---|---|---|---|---|
    | 宽窗口 | 466..3064 | 2598 | +44 ~ +632（1.7%~24.3%） | +311 ✅ 在带内 |
    | 竖屏 | 414..1134 | 720 | +141 ~ +459（19.6%~63.8%） | +86 ❌ 落在底色上 |

    现在 `_bubble_click_xs()` 的候选是「**离发送方那一侧边缘 300px 的绝对锚点** → 旧的 12%」，两侧各给一次再去重：宽窗口里两个数算出同一个点（311 与 300 取大 = 311），行为与以前逐字一致；竖屏里绝对锚点先命中（别人发的 +300 ∈ [141,459]；自己发的距右边缘 300，镜像区间 [261,579] 同样命中）。锚点再被 `0.45×行宽` 夹一道，行很窄时也不会点到行外面。每个候选都由「有没有**新弹出**预览窗」这个可观察事件验收，所以点空不会当成成功，也不可能点到别的消息（y 一直是这一行的中线）
  - **量屏幕坐标不要用屏幕截图**：这台机器上 Qoder/终端会压在微信上面，`ImageGrab` 拍到的「色块」其实是 IDE 面板（同一轮 `[sessions]` 那条 OCR 检查也因此把终端文字当成会话名）。要量窗口内容就按句柄 `PrintWindow`（`PW_RENDERFULLCONTENT`），不需要置前台、不改变遮挡关系。注意 `GetWindowRect` 含 DWM 那圈不可见边框，本机比 UIA 的 `BoundingRectangle` 左右各多约 13px，映射要按窗口原点算
  - **自己发出的图气泡在右边**：只按「左边缘」点就会点空——这是「点击打开图片时错位」最直接的一种形状。现在按发送方决定先点哪一侧，第一侧没点开再点该行另一侧
  - **「是不是我发的」既不写死常数、也不写死「1」**：`real_sender_id` 是**消息所在分片** `message_N.db` 里 `Name2Id` 的 rowid（issue #34）。本机自己账号在 message_0/1/2.db 的 rowid 分别是 **2/4/1**——所以「`== 2` 是自己」（1.1.8 从别人那台机器带来的取值）和「查不到就兜底按 1」（1.2.4 的写法）都会在部分分片里指到别人身上。实测形状：文件传输助手 856 行**全部**是这台机器发的，按分片解析 855 行给出本机 wxid（另 1 行的发送者确实就是 `filehelper` 自己）；旧的全局表会把其中 **639 行**说成某个常联系的好友或一个群。判错方向的代价很直接——自己发的图被当成别人发的，先去点左边，永远点不中气泡，看起来就是「我发的图片提示找不到原图」。现在按「**本分片解析出来的 username 等不等于本机 wxid**」判；解析不到就按对方处理并打一条 debug，不猜编号。
  - 「有没有点开」只把**新出现**的预览窗算作成功（按 `NativeWindowHandle` 分辨）；屏幕上本来就开着预览窗时会先警告一句，因为旧窗口会被误判成刚点开的
  - **预览窗有两种形状**（同一台机、同一版本实测都出现过）：① 桌面的直接子节点就是 `mmui::PreviewWindow`；② 顶层是 `Qt51514QWindowIcon`、标题「图片和视频」，`mmui::PreviewWindow` 在**它里面一层**。只按「顶层子节点的类名含 PreviewWindow」筛，第 ② 种永远找不到——表现就是「点了没反应 / 按钮点不对」，而实际一次都没走到找按钮那步
  - 预览窗工具栏按钮全是真 UIA 控件，按名字取：`置顶 / 上一张 / 下一张 / 预览 / 放大 / 缩小 / 图片原始大小⇄图片适应窗口大小 / 旋转 / 编辑 / 翻译 / 提取文字 / 保存 / 更多`，加标题栏 `最小化 / 最大化 / 关闭`
  - **那颗缩放键的 Name 会随显示状态变**（实测同一颗按钮在「图片原始大小」和「图片适应窗口大小」之间切换）。只写死一个名字找，找不到就提前返回 → 「保存」兜底根本没机会跑，用户看到的就是「点了缩放那颗，没点下载那颗」。现在按**两个候选名**找，并且**只有当前是「图片原始大小」时才点它**（已是「图片适应窗口大小」说明原件就在显示中，再点只会缩回去，直接走「保存」）
  - **真正下载原件的是「保存」**（↓ 图标，在缩放那颗 ▣ 的右边）。找不到缩放键**不再提前返回**，一律继续走「保存」
  - **没有 `want` 这个参数了**：以前它能选「等 `.dat` 就行」，但 `.dat` 可能就是预览版，那个选项等于让调用方拿预览图当成果。现在目标固定为 `_h.dat`，「本机有什么就交什么」交给 `download_image(tier='full')`
  - **批量下载同一会话的多张图不再反复搜索进入**：进函数先读当前会话标题（`current_chat()`，取自输入框的 Name——它一直是会话标题而不是正文），已经是目标会话就跳过搜索；读不到标题时保守照常进入
  - **窄窗口里点一次「微信」栏退不出会话，要再点一次**（用户实测；主窗 848×1274 时最容易碰到）。窄窗口是单栏布局：开着会话时 `session_list` 和搜索框**整片不在树里**（实测只有 `chat_message_list`、`ChatMessagePage`），点第一下导航栏只把主窗带回聊天页、当前会话还开着；第二下才退出会话。以前 `back_to_chat_tab()` 无条件只点一下，于是紧接着找搜索框的 `open_chat()` 必然落空——报出来就是「会话打不开」，而原图下载这条路会退化成在**别人那个会话**里认行、瞎点。现在 `back_to_chat_tab(settle=1.0, max_clicks=2, require_list=False)`：点一下 → 等 → 看 `session_list`（或搜索框）出来没有 → 没出来且还有额度就再点一次；**本来就出来就一点都不点**（那一下会把用户正在看的会话切走）。点满还不出来就返回 `False` 并警告「大概率还开着会话」，不再和「没找到导航栏」混成同一种失败。判据为什么不能用消息列表：开着会话时它本来就在树里；为什么不能用导航栏选中态：4 颗 `XTabBarItem` 的 `IsSelected`/`SelectionItemPattern` 实测一个都读不出来
  - **每个决策点都先回头看一眼本机**（`_harvest`，**只认 `_h.dat`** 且解密后结构完整才算拿到）：进门、点下一张气泡之前、等档超时之后、界面这条路不通要报失败之前。微信经常在我们要的那一档**之后**才把文件写完——不补这几眼就会出现用户实测的两个现象：「明明已经拿到了还在一张一张瞎点，最后报下载失败」和「重新运行又说已在本地」。补查到了就直接交文件（不再去点「保存」，也少一次真实界面动作）；查不到才明确报 `None`。**补查不是换个档位交差**：本机那份 `.dat` 再大也不会在这里被当成成果
  - 滚动有一个硬前提：**拿得到微信进程 id**。拿不到就退化成「只认当前屏、绝不滚」（`max_scrolls=0`）并打一条警告——落点无法校验时滚轮可能打进压在微信上面的别的程序
  - 点完之后是**轮询等** `_h.dat` 出现并停止变大（到 `timeout` 为止），不再固定睡 3 秒取一次：只等原件那一档，`.dat` 长得再快也不算拿到
  - 点击坐标依赖 WeChat 4.x 的 `mmui::ChatBubbleReferItemView` 布局（DPI 感知进程下按物理像素定位），不同窗口宽度/DPI 用相对偏移自动适配 / click coords rely on the `mmui::*` layout (physical pixels under a DPI-aware process); relative offset adapts to window width/DPI
  - 预览窗口内的「图片原始大小」按钮是完整 UIA 控件，用 `Click()` 点击 / the preview-window button is a real UIA control and is clicked via `Click()`
  - **「图片原始大小」点了没反应时改点「保存」兜底**：那颗按钮本质是查看器的缩放档，原图已在盘上（或这张根本没有更大的原件）时它不触发任何下载。所以 `_h.dat` 等不出来之后，会点预览窗的「保存」。**实机确认「保存」不是静默落盘，而是弹 Windows 通用保存对话框**：顶层类名 `#32770`，**标题是「保存」而不是「另存为」**（按标题里有没有「另存」去匹配会认不出，现在按「`#32770` + 里面真有一颗以「保存」开头的按钮」认）；默认目录是 `<账号>\temp\InputTemp`，**路径不固定**，文件名预填 `微信图片_<时间戳>_*.jpg`
  - 对话框里的控件形状（逐层 dump 出来的）：`ComboBox name='文件名:'` 里面那层 `Edit name='文件名:'` 才是可写值的——**命中容器还要再往里找 Edit**，直接对外层 ComboBox `SetValue` 是写不进去的；右上角还有一颗 `SearchEditBox name='搜索框'` 的 Edit，**拿「第一个 Edit」会填到这里**；按钮文案带助记符（`保存(S)` / `取消`），所以按前缀匹配。填目标全路径用 `ValuePattern.SetValue`、点按钮用 `InvokePattern.Invoke`，**全程不发键盘**
  - **第一遍「保存」拿到的可能还是预览图那一幅**（用户实机反馈：UI 点下载存下来的仍然是预览图，要重新下载才得到原图）。微信「保存」写出的是**当前显示的那一幅**，原件没进显示时它就是预览版。所以拿到保存产物之后要**按尺寸核一遍**：本机已有 `.dat` 而产物比它小两成以上 → 判为预览图；本机只有 `_t.dat` 而产物不超过它 1.3 倍 → 判为预览图。判为预览图时先点「图片原始大小」逼原件进显示，**再点一次「保存」**，两遍里取大的那份；两遍都还是预览图就照交本机最好的那份，并在日志里说明「只能让对方重发原图」。参照不足（两档都不在）时**不猜、也不多等一轮界面**
  - **任何失败路径都会点「取消」把模态框关掉**——那是模态窗，不关掉就一直压着预览窗，后面每张图都点不到。**点完「保存」还要确认窗口真的关了**：窗还开着就说明那一下没点中按钮，立刻判失败并点「取消」，不再白等 8 秒然后报一句「目标目录里没出现该文件」（那句指不出真正的原因）。遍历对话框时 UIA 节点可能在中途失效（切页/关窗），坏一颗就跳过那一支，不能让异常把整条兜底路线打死
  - 万一以后版本改成静默保存，仍保留「点之前拍快照、点之后找新文件」的收法（快照只走「最近改过」的目录，不整树扫）。两条路都不通才返回 `None`
  - **「保存」拿到的那份不在加密附件树里，`image_status` 永远看不见它**：`image_status` 只看 `msg/attach/<md5(会话)>/<月份>/` 下的 `_t.dat`/`.dat`/`_h.dat` 三档，「保存」写出去的是明文字节（默认落在 `temp\InputTemp`，用户手点的话落在你随手选的那个文件夹）。所以「我之前明明保存过，怎么还提示取不到原图」不是查错了目录——那一档本来就不存在。要「本机最好的那份」用 `download_image(tier='best')`，它直接从 `.dat` 解密，一步界面都不碰
  - 预览窗里找不到「图片原始大小」按钮（这张本来就是原图／微信没给这个入口）会**明确警告并返回 `None`**，指引改用 `tier='best'`，不再和「下载没完成」混成同一种失败
- 无 ffmpeg 时 wxgf 格式存为 `.wxgf` 原始数据兜底 / without ffmpeg, wxgf saved as `.wxgf`

### 6.4 批量下载全部图片 / Batch download all images

```python
ids = db._find_media_rows("群名", {3})   # 全部图片 ID，不管会话消息总量多大
for lid in ids:
    out = md.download_image("群名", lid)
    if out:
        print("downloaded:", out)
```

命令行也有现成脚本 / There is also a CLI demo：

```bash
python demo_media.py 群名 --images 100        # 下载该群最近 100 张图片
python demo_media.py 群名 --images 100000     # 超过总数即全部 / all if > total
python demo_media.py 文件传输助手 --filter 图片,文件
```

---

## 7. 朋友圈 / Moments

```python
from wechatauto import MomentDB

moments = MomentDB(db)                        # 基于 sns.db 直读 / direct sns.db reads
for feed in moments.get_moments(limit=10):
    print(feed["nickname"], feed["text"])
    print("  images:", [i["md5"] for i in feed["images"]])
    print("  likes:", [l["nickname"] for l in feed["likes"]])
    print("  comments:", [(c["nickname"], c["content"]) for c in feed["comments"]])
    # 下载本条动态的图片/视频（优先本地缓存，其次 CDN url）/ download media
    saved = moments.download_moment_media(feed, save_dir=r"D:\moments")
    print("  saved:", saved)
```

朋友圈图片/视频下载的完整命令行示例见 `wechatauto/demo_moments_download.py`
（`python -m wechatauto.demo_moments_download [N] --out 目录`）。

**点赞 / 评论（UIA 控件路线）**：点赞/评论属服务端行为，需走界面（数据库路线只读）。
`WeChat` 会热激活 `mmui` UIA 树并点击导航栏“朋友圈”后，基于 UIA 控件操作：

```python
from wechatauto import WeChat

wx = WeChat()
moments = wx.Moment                 # UIA 树不可用时为 None
if moments is None:
    raise SystemExit("UIA 树不可用，无法点赞/评论")
wx.SwitchToMoments()                # 点击导航栏“朋友圈”
items = moments.GetMoments()
moments.Like(items[0])                            # 点赞
moments.Like(items[0], cancel=True)               # 取消赞
moments.Comment(items[0], "不错！")                # 评论
moments.Comment(items[0], "谢谢！", reply_to="张三")   # 回复某人评论
```

命令行示例：`python -m wechatauto.demo_moments_interact [--like N | --unlike N | --comment N 文字]`
（直接运行则只列出最新动态，不操作界面）。

---

## 8. 多账号 / Multi-account

```python
from wechatauto import list_accounts, WeChatDB

accts = list_accounts()                       # 列出本机所有微信账号 / list all accounts
for a in accts:
    print(a)

db = WeChatDB(account="wxid_xxx")             # 指定账号 / pick an account
```

---

## 9. 导出聊天记录 / Exporting Chat History

```python
db.export_history(
    out_dir=r"D:\export",
    out_format="json",        # json / sqlite
    include_media=True,
)

for chat in db.list_message_chats():          # 有消息的会话 / chats that have messages
    print(chat)
```

---

## 10. 群聊操作专题 / Group Chat Operations

### 10.1 获取群信息 / Group info

```python
info = chat.ChatInfo()                        # 群成员、群主等 / members, owner, etc.
```

### 10.2 群聊发消息并 @ 成员 / Send & @ members

```python
chat.SendMsg("大家看这个", at=["张三", "李四"])
# 或指定群 / or
wc.ChatWith("群名")
wc.SendMsg("开会了", at=["全体成员"])
```

### 10.3 群聊监听 / Listen to a group

```python
lst.add_listener("44054166277@chatroom", on_msg)   # 用群 username
```

### 10.4 群聊图片 / 语音 / Group images & voice

```python
md.download_image("群名", local_id)      # 自动缩略图回退 / auto thumbnail fallback
md.download_voice("群名", local_id)      # 自动搜索所有 media_*.db / searches all media_*.db
```

---

## 10.5 通讯录标签 / Contact Labels

界面上的走法（微信 4.x PC，**全部按 4.1.15.13 真机实测的锚点实现**）：
**通讯录 → 通讯录管理 → 标签**，左栏建标签，右键标签行「添加成员」勾人，
选中成员后点底部「移出标签」。

```python
from wechatauto import WeChat, WeChatDB

WeChatDB().list_labels()            # [{label_id, name, sort_order}] —— 只读库，不动窗口
wx = WeChat()
wx.ListLabels()                     # 同上（WxResponse 包装）
wx.ListLabels(prefer="ui")          # 读管理窗左栏：是准数，还带人数（会动窗口）
wx.CreateLabel("同事")              # 微信是「先建空标签、再改名」两步；已存在时直接算成功
wx.RenameLabel("同事", "老同事")     # 只改名字：右键 →「修改标签名」→ 贴 → 回车（成员/label_id 不动）
wx.AddLabelMembers("同事", ["小明", "wxid_abc123"])   # wxid 会自动换成界面显示名
wx.RemoveLabelMembers("同事", ["小明"])               # 只去标签，不删好友
wx.DeleteLabel("同事")              # 删标签（确认框文案就是「相关联系人不会被删除」）
wx.LabelMembers("同事")             # 该标签的**全部**成员（靠滚动取全量，见 10.5.2）
wx.SendToLabel("周五校庆放假", "同事", dry_run=True)   # 按标签批量发：先只解析收件人
wx.SendToLabel("周五校庆放假", "同事", limit=1)        # 真发，先试一个人
wx.ProbeLabels(dump_dir="label_probe")                # 只导航不写，报断在哪一步
```

一条命令版（中文子命令同样认）：

```bash
wechatauto 标签 list                          # 只读库，不动窗口
wechatauto 标签 list --ui                     # 走界面核对（含人数；库里的删除会滞后）
wechatauto 标签 create --label 同事
wechatauto 标签 add    --label 同事 --members 小明,小红
wechatauto 标签 remove --label 同事 --members 小明
wechatauto 标签 delete --label 同事
wechatauto 标签 rename --label 同事 --to 老同事
wechatauto 标签 members --label 同学           # 滚动取全量：「显示 63 人，读到 63 人」
wechatauto 标签 send    --label 同学 --text "…" --dry-run   # 只解析收件人，一笔都不发
wechatauto 标签 send    --label 同学 --text "…" --go --limit 1
wechatauto 标签 probe --dump label_probe      # 界面改版后先跑这个
```

`add` / `remove` 完成后会回报成员数变化（`成员数：63 → 65`）——那个数字是标签行
Name 自带的（`同学(63)`），也是这件事最便宜的真值。

### 10.5.1 八条实测出来的界面事实 / Eight measured facts

1. **「通讯录管理」是独立顶层窗口**，不在主窗子树里；右键菜单、「微信添加成员」
   选择框、删除确认框各是另一个小窗。所以查找的根按标题换：
   `FindWindowW('Qt51514QWindowIcon', '通讯录管理')`。按主窗子树找 = 永远找不到，
   表现就是「点了没反应」（同一个根因也解释了「搜索下拉点不中」那一类老问题）。
2. **每次点击前抬窗 + 校验落点归属**（`WindowFromPoint` 的 root 必须就是目标窗口），
   不匹配就返回 `occluded` 不投。用户的浏览器或图片预览窗盖住微信时，注入点击会
   真的落到那个窗口上，而 UIA 读回来的还是微信的树——「没反应」和「点错地方」在
   日志里长得一模一样。两个例外：**弹层不抬窗**（右键菜单一失焦就自己关掉），
   而残留的「微信添加成员」**必须抬窗**才取消得掉（它是管理窗的 owned window，
   抬管理窗抬不过它）。
3. **行内改名没有可寻址的编辑框**：点「修改标签名」之后整棵树里只有搜索框，
   `SetFocus` 反而会把编辑态取消。实测唯一可行的是「剪贴板 + Ctrl+V + 回车」。
4. **管理窗留着上一次的滚动位置**：再点一次同一个标签行**不会**把右侧列表拨回
   顶部。第一次真机跑 `标签 members` 就栽在这——它从中间开始数，数到「下面没新
   行」就报「到底了」，63 人只读到 22 个。现在先 `_rewind()` 往上倒到不动为止。
5. **滚轮只认小 delta，而且不等速**：一把 `-600` 发下去列表经常一动不动；每格
   `120` 实测走 1~2 行，同一轮里 5~10 行不等。所以每轮只给 3 格（≈4 行，一屏
   10~11 行 → 屏与屏还剩 6 行重叠），照 `find_in_message_list` 那条老路写。
6. **「新建标签」产出的那一行是空名字、而且已经在行内编辑态**：实测新行 Name 是
   `'(0)'`（要等提交才变成 `未命名(0)`）。两个坑叠在一起：① 用「滤掉空名」的
   `label_rows()` 判有没有多出一行，永远看不见它 → `no-new-row`；② 这时候右键
   **弹不出菜单** → 兜底的「修改标签名」也走不通。所以 `CreateLabel` 是先直接
   「贴 + 回车」（`via='editing'`），没生效才退回右键改名（`via='menu'`）。
7. **左栏「标签」那一组会被折叠起来，折叠时整组都不在 UIA 树里**：微信记住折叠
   状态，折叠后左栏只剩 `全部 / 筛选 / 朋友权限 / 标签 / 最近群聊` 五个标题，
   `无标签`、每个标签、`新建标签` **一行都读不到**——表现就是 `no-create` /
   `no-label` / 空列表（用户报的）。点一下分组标题「标签」就全回来了，而且实测
   **再点一次不会收起**，所以 `ensure_labels_open()` 里「已经展开就一下都不点、
   没展开就点一次」是安全可重复的动作。`open_manager()` 每开一次窗都会先过它。
8. **「窗口出现了」不等于「左栏画出来了」**：`FindWindowW` 拿到「通讯录管理」的
   那一刻左栏可能还是空的——实测就这么把 4 个标签念成「共 0 个标签」还报了成功。
   它和第 7 条撞在一起才真危险：**「读不到」既可能是没画完、也可能是折叠了**，
   而按折叠去点标题会把一组只是晚画的行**折叠掉**。所以 `open_manager()` 在
   `ensure_labels_open()` **之前**先等左栏出现内容（看见标签行、或看见「新建标签」
   那一格都算画过了），等不到才按折叠处理。

### 10.5.2 为什么成员只能按界面判 / Why membership is UI-only

- 标签**定义**在 `contact.db:contact_label(label_id_, label_name_, sort_order_)`，
  本机实测能直接读到，所以 `list_labels()` 完全不碰窗口。
- 标签**成员**在本地库里读不到。全库扫过一遍：带 label/tag 字样的表只有
  `contact_label`（只有定义）、收藏的 `fav_*_tag_*`、`general.db:FMessageTable.label_ids_`
  （「新的朋友」验证消息上的字段，不是通讯录标签）。又按 protobuf 逐字段解过
  `contact.extra_buffer` 里有值的 409 个联系人，没有取值 ⊆ 已知 label_id 的字段。
  **定向差分**再验一次：给某个好友贴上 `演示标签`（label_id=6）之后，他在所有人里
  独有的字段只有一个 `(field 41, varint 1753781804)`——那是个日期，不是 6。
- 而且 `contact_label` **滞后于界面**：删掉标签后界面上那行立刻没了，解密库里还留着。
  所以它只当辅助读，**校验一律看界面**（标签行的人数、选择框的「已选择N个联系人」）。

**取全量怎么数才算数**（`LabelMembers` / `标签 members`）：倒回顶部 → 每轮 3 格往下滚
→ 把每一屏**按重叠拼接**成一条序列。拼接而不是「按名字去重」是必需的：标签里真有
重名的人，按名字去重时 63 人会只剩 54 行（实测）。最后一屏对不上上一屏（`gapped`）
也只是线索，不当判据——整屏正好跳过去时两屏一个名字都接不上，却一行都没漏；**「取全
了没有」只认微信自己在标签行上写的那个数**（`同学(63)` 对上 63 才算 `complete`），
对不上就报 `incomplete` 并判失败，绝不当成功返回。

### 10.5.3 按标签批量发送 / Send to a label

```python
wx.SendToLabel("周五校庆放假", "同学", dry_run=True)   # 只解析收件人，一笔都不发
wx.SendToLabel("周五校庆放假", "同学", limit=1)        # 真发，先发一个人试水
wx.ForwardToLabel("同学", text="周五校庆放假", dry_run=True)   # 先发给第一个人，再把这条转发给剩下的
wx.ForwardMessage(["甲", "乙"], chat="文件传输助手", match="测试文本")  # 转发已有的一条
```

**转发要有物料**：右键转发的目标必须**已经在界面上存在**。所以给了 `text` 时走的是
「先老老实实发给名单里第一个人 → 再回到那个会话，按正文定位那一条 → 右键转发给
剩下的人」：第一个人拿**原件**、其余拿**转发件**，每人恰好一条，不会重发。第一条
没发出去就整链停住（`seed-fail`）——不然右键到的会是会话里**别的**一条消息。
中途失败时响应里点名「已发出 N 条」，因为照提示直接重跑会给第一个人发第二遍。

三道闸，都是故意加的：

1. **先读成员**（上面那套滚动枚举），读不全（`incomplete`）就直接失败——批量发送
   把差掉的人默默跳过是最贵的一种错。
2. **每个人都要在通讯录里唯一命中一个 wxid** 才进 `recipients`；重名的、查不到的
   进 `data['skipped']` 交回来（真机实测：`同学` 63 人里有 4 个人的昵称只是一个
   省略号、一个句号、一个感叹号、一个表情——通讯录里对着好几个人，所以一律不发，
   不猜人）。
3. `dry_run` 是 CLI 的默认，`--go` 才真发；菜单里跑 `send` 会先预演、把收件人念
   出来，问过 `y` 才带 `--go` 走第二遍。发送本身复用 `Chat.SendMsg` 那条通道，每一笔
   都过 `rhythm` 的拟人节流，`_send_cost_hint()` 会按当前档位先报一遍预计耗时。

**失败一定带原因**：每一步返回结构化 `reason`（`no-tab` / `no-page` /
`no-manager-row` / `no-manager-win` / `occluded` / `no-menu` / `no-create` /
`no-new-row` / `not-renamed` / `no-picker` / `no-member` / `count-unchanged` /
`still-there` …），并把当层能读到的控件名带回 `WxResponse.data`。界面文案随版本漂移时
先跑 `标签 probe`，它把每一步的控件树写进 `--dump` 目录。

> 状态：**建标签 / 加成员 / 移成员 / 删标签 / 读全量成员 / 按标签预演已在本机真机
> 跑通**（2026-10-06：`同学` 微信显示 63 人 → 读到 63 人 `complete`，其中 59 人能
> 唯一对上 wxid、4 个重名交回 `skipped`，`send --dry-run` 一笔都没发；探测时留下的
> 两个空标签 `未命名` / `未命名1` 已真的删掉，账号回到 `同学(63)`、`大人(18)`）。
> **真 `--go` 那一段仍未测**——它要往真人身上发消息，得由本人点头。离线自测
> `python tools/test_labels.py`（98 项 + 20 个变异，每个变异都必须让某条判据变红）
> 钉逻辑与失败原因；真机那一段需要解锁可见的桌面（见 AGENTS.md）。第 8 条（左栏晚画）
> 是**真机撞出来的**，但修完那一刻微信已退出，只有离线判据 —— 复验要重开微信跑
> `标签 list --ui`，在那之前算 未测。

---

## 10.6 右键转发 / Forward a Message

把**已经在聊天里的一条消息**分别转发给若干人或整个标签，走的是界面右键那条路
（不是重新打一遍正文，所以图片/文件/链接卡片都能原样转）。

```python
from wechatauto import WeChat

wx = WeChat()
wx.ForwardMessage(["文件传输助手", "送你挖银子"], chat="文件传输助手", dry_run=True)
wx.ForwardMessage("小明", chat="某群", match="测试文本123")     # 按文字定位那一条
wx.ForwardToLabel("同学", chat="文件传输助手", dry_run=True)     # 按标签：默认只预演
wx.ForwardToLabel("同学", chat="文件传输助手", dry_run=False, chunk=9, limit=20)
```

```bash
wechatauto 转发 --label 同学 --text "周五校庆放假" --dry-run     # 先发第一个人→再转发给剩下的人
wechatauto 转发 --label 同学 --text "周五校庆放假" --go --chunk 9
wechatauto 转发 --to 文件传输助手,送你挖银子 --chat 文件传输助手 --dry-run
wechatauto forward --label 同学 --chat 文件传输助手 --match "测试文本" --go   # 转发已有的一条
```

节奏是**分小块多选**：一个「微信发送给」窗口勾 `chunk` 个人（默认 9），块与块之间过
`rhythm`。63 人的标签不在同一个窗口里一次勾满——那等于一次点击把 63 条消息全砸出去，
中间没有任何节流间隔。

### 10.6.1 实测锚点（4.1.15.13）

| 环节 | 控件 |
|---|---|
| 右键菜单 | 独立小窗（`Qt51514QWindowToolSaveBits`，标题 `Weixin`），项物化成主窗子树里的 `mmui::XMenuView`，Name=**`转发...`** |
| 转发窗口 | **独立顶层窗口**，标题 `微信发送给`，根控件 `mmui::SessionPickerWindow` |
| 左栏行 | `mmui::SPSelectionContactRow`（Name=显示名）；搜索后换成 `mmui::SearchContactCellView` |
| 搜索框 | `mmui::XValidatorTextEdit` Name=「搜索」（和标签选择框同族，复选框也在行左缘 +70px） |
| 已选收件人 | 右栏 `mmui::SPChoiceContactRow`，Name=**`移除<显示名>`** |
| 发送按钮 | `mmui::XButton` aid=`confirm_btn`，Name=**`发送` / `分别发送(N)`**；取消是 aid=`cancel_btn` |
| 留言框 | `mmui::ChatInputField` aid=`leave_message_view.chat_input_field` |

### 10.6.2 发送前为什么要三个数对齐

点一次「发送」= 往 N 个人各发一条，发出去收不回来。所以 `send()` 要三处读数一致才放行：

1. **发送按钮上写的数字**（`分别发送(2)`；单选时是 `发送`，按 1 个人算）；
2. **右侧已选栏里的人头**（`移除<显示名>` 的行数）；
3. 调用方**预期**的人数（这一块实际勾中几个）。

对不上就报 `count-mismatch` 停手；一个人都没选报 `no-recipient`。发出去之后还要
**回读每个人的会话**：有比发送时刻更新的一行才算 `sent`，没有就进 `failed`——
「窗口关了」不等于「每个人都收到了」。

另外两条实测事实：**「分别发送」是给每个人单独发一条**（实测两个收件人会话里各新增
一行同刻消息），以及**这个选择框和「标签→添加成员」是同一族控件**，所以 `forward.py`
直接复用 `labels.py` 的窗口层与点击层。

> 状态：开窗 / 勾选 / 人数闸门 / 取消 / 分块 / 预演 / 先发的原件 已由
> `python tools/test_forward.py`（59 项 + 7 个变异）钉住；真机上生产代码这条路也跑通了
> ——`forward --to 文件传输助手,送你挖银子 --go` 勾 2 人 → 按钮写 `分别发送(2)` → 发送 →
> 两个会话各落一条同刻的图片消息（独立回读复核过，不是自测自己发的那条）。
> 「先发给第一个人再转发给剩下的人」这条**只跑过离线判据**：要真机验得开着微信，
> 写这一段时微信已经退出了 —— 算 未测。
> **往别人身上批量转发没有自动试过**，需要本人明确下达 `--go`。

---

## 11. 常见问题与排错 / FAQ & Troubleshooting

### Q1: `RuntimeError: 数据库无可用密钥` / no usable DB key

- 确认微信**已登录**（密钥在进程内存）/ make sure WeChat is **logged in**
- 确认运行账号有权限读取微信进程（同用户运行）/ run as the same user
- 微信版本差异可能影响内存扫描，升级微信或查看 issue / some versions differ in memory layout

### Q2: 图片下载失败 / 无法获取 AES 密钥 / image key not found

- 先在微信里**点开一张图片看大图**，立即重试 / open any image in WeChat first
- 或 `md.detect_image_key(monitor=True)` 持续等待
- 或手动传 `image_key="16位"` 给 `MediaDownloader`

### Q3: 群聊图片只有几张 / 很多下不了 / group chat only a few images

- 群聊图片原图未点开查看时只有缩略图，`download_image` 会自动回退
- 若要全部，用 `_find_media_rows` + 遍历（6.4），或用 `--images` 参数
- **1.2.4.2 起先问一句「本机到底有哪一档」**：`md.list_image_status(user)` /
  `md.image_status(user, local_id)` 给每条图片 `tiers`（三档字节）/ `best` /
  `reason`。`only_thumbnail`、`mid_only` 是微信的存储策略（原图从没点开过），
  不是解密失败；`original_partial` 才是原图下载中断 / `list_image_status()`
  tells you which tier actually exists before you retry anything
- 只想要原图就写 `download_image(user, lid, tier='original')`：没有原图时返回
  `None` 而不是悄悄给你压缩版；想要「能拿到的最好一档」用 `tier='best'`，
  档位会标在文件名上（`_h` / 无 / `_thumb`）

### Q4: 发送失败 / sending fails

- 微信窗口需可见（不能锁屏/最小化到托盘）/ window must be visible
- 锁屏或窗口不可响应时发送会安全失败
- 换用 `verify=True` 获得回读确认

### Q5: 语音下载不到 / voice not downloading

- 1.1.4+ 已支持搜索所有 `media_*.db`（微信分片存储）/ 1.1.4+ searches all media_*.db
- **1.2.4.1 起先问一句「到底为什么」**：`md.list_voice_status(user)` /
  `md.voice_status(user, local_id)` 会给每条语音 `available` / `bytes` /
  `download_status` / `reason`。`reason=audio_not_downloaded` 表示微信没把这段音频
  落盘（在界面里播放一次即可），`audio_missing_from_media_db` 才是可能的库侧问题
  / `list_voice_status()` tells you *why*: not-downloaded vs a real lookup bug

### Q6: 监听无聊天记录的联系人 / contact with no history

- 消息表按需创建，对方发第一条消息后轮询即捕获
- 需要知道对方 wxid（用 `search_contact`）

### Q7: `WeChatAuto` 导入报错 / ImportError

- 本库入口类是 **`WeChat`**，不存在 `WeChatAuto`
- 教程代码若用旧类名，把 `WeChatAuto()` 换成 `WeChat()`

### Q8: 「控件树被屏蔽」/ UIA tree is empty

先分清两件事：**微信自绘界面，UIA 树只有在 Qt accessibility gate 打开后才存在**，
而那个 gate 是 `Weixin.dll` 在进程里的一个字节，微信**每次重启/更新/重登都会归零**。
所以它不是被谁屏蔽，而是没人写它的时候树就不存在。库在每次走 UIA 入口时会自己热写并校验
（日志：`热激活 UIA：PID=… Weixin.dll+0x…: 0 -> 1`）。

判据（一行）：看微信窗口的 ClassName —— `mmui::MainWindow` = 树在；
`Qt51514QWindowIcon` = 树没建 / class is `Qt51514QWindowIcon` means no tree。

扫不到可用窗口时，库现在会明确警告原因（每种原因一个进程只说一次，不刷屏）：

| 警告里的说法 | 真实原因 |
|---|---|
| 没扫到可见的微信主窗口 | 微信未启动/未登录/最小化到托盘，或标题变了 |
| 扫到 N 个窗口但都定位不到 Weixin.dll，**32 位** | 32 位 Python 无法枚举 64 位进程模块，换 64 位 Python |
| 同上但是 **64 位** | 权限/完整性级别不一致（别「以管理员运行」）或安全软件拦进程读取 |
| `pywin32` 不可用 | 没装 pywin32，UIA 路线整条不可用 |

热激活本身失败的三种日志原文分别对应：`不支持的 Weixin.dll 版本路径`（新版本 gate RVA
漂移且扫不出候选，需要加 RVA）、`无法打开 Weixin.exe PID`（权限/安全软件）、
`N 个候选均未使 mmui 树物化`（写进去了但 Qt 不建树）。
另外：跑在没有交互桌面的会话里（服务、非交互计划任务、RDP 已断开）永远不会有树。

---

## 12. API 速查表 / API Quick Reference

### WeChatDB（数据读取 / data）

| 方法 / Method | 说明 / Description |
|---|---|
| `get_self_info()` | 当前账号信息 / current account info |
| `get_sessions(limit)` | 会话列表 / session list |
| `search_contact(kw)` | 搜索联系人 / search contacts |
| `get_nickname(user)` | 反查昵称 / reverse nickname lookup |
| `get_messages(user, limit, offset)` | 最近消息（跨分片合并，sort_seq 降序）/ recent (cross-shard) |
| `get_message_row(user, local_id, local_type=None)` | 单条原始行（跨分片；类型码定位分片）/ single row |
| `get_message_rows_for_media(user, local_id)` | 该 id 全部 shard 行 / all shard rows for an id |
| `get_new_messages(user, since_seq)` | 增量消息（跨分片，watermark 无重放）/ incremental |
| `_find_media_rows(user, types)` | 按类型取全部媒体 ID / all media IDs by type |
| `list_message_chats()` | 有消息的会话 / chats that have messages |
| `export_history(...)` | 导出聊天记录 / export history |
| `list_accounts()` | 列出账号（模块级）/ list accounts (module-level) |

### Listener（实时监听 / realtime）

| 方法 / Method | 说明 / Description |
|---|---|
| `add_listener(user, cb)` | 注册回调 / register callback |
| `remove_listener(user, cb)` | 移除回调 / remove callback |
| `start()` / `stop()` | 启停 / start / stop |
| `watermark` | 已消费序号 / consumed seq |

### MediaDownloader（媒体 / media）

| 方法 / Method | 说明 / Description |
|---|---|
| `detect_image_key(monitor)` | 提取图片密钥 / extract image key |
| `download_image(user, lid, tier)` | 图片，**只读本机不碰界面**，本机有什么就交什么（三档：`_h.dat` 原件 / `.dat` 微信下发的那一份，可能是完整图也可能是预览版 / `_t.dat` 预览图；wxgf 转码）/ image, 3 tiers, local only |
| `download_image_original(user, lid, timeout, min_bytes, scroll, max_scrolls)` | 要**确定的原件 `_h.dat`**：本机有就解密落盘，**没有就强制走点击路径**；绝不拿 `.dat` 冒充成果 |
| `image_status(user, lid)` | 单条图片本机有哪一档 + `available`（有没有 `_h.dat`）/ `has_mid`（有没有 `.dat`，**不代表它是完整图**）+ 原因 |
| `list_image_status(user, limit)` | 整个会话的档位一览（§6.2.2）/ per-session tier report |
| `download_voice(user, lid)` | 语音 .silk / voice |
| `voice_status(user, lid)` | 单条语音取不到的原因 / why a voice has no audio |
| `list_voice_status(user, limit)` | 整个会话的语音可用性一览（§6.2.1）/ per-session voice report |
| `download_video(user, lid)` | 视频 .mp4 / video |
| `download_file(user, lid)` | 原文件 / original file |
| `download_media(user, lid)` | 按类型自动分发 / auto-dispatch by type |

### RecallGuard（防撤回 / anti-recall）

| 方法 / Method | 说明 / Description |
|---|---|
| `RecallGuard(db, mirror_dir, media_dir, window=120)` | 镜像库目录 / 媒体备份目录 / 回溯窗口 |
| `watch(listener, users=None, backfill=50)` | 挂到 Listener（users=None 监听全部会话）/ attach |
| `backfill(user, limit)` | 手动补镜像历史 / pre-fill mirror |
| `get_recalled(chat=None, limit=50)` | 查询撤回事件 / query recall events |
| `close()` | 关闭镜像库 / close |

### WeChat / Chat（发送，wxauto 风格 / sending）

| 方法 / Method | 说明 / Description |
|---|---|
| `ChatWith(who)` | 切换会话 / switch chat |
| `SendMsg(msg, who, at)` | 发文本（支持群 @）/ send text |
| `SendFiles(paths, who)` | 发文件 / send files |
| `GetAllMessage()` / `GetNewMessage()` | 读消息 / read messages |
| `VoiceCall(video)` | 语音/视频通话 / voice/video call |
| `Poke()` | 拍一拍 / poke |
| `RecallLastMessage()` | 撤回最近消息 / recall latest message |
| `ForwardVoiceMessage(target)` | 转发语音 / forward voice |
| `ListLabels()` / `CreateLabel(name)` | 标签：读（走库）/ 建（走界面）/ list & create labels |
| `AddLabelMembers(label, who)` / `RemoveLabelMembers(label, who)` | 批量加/移成员（只去标签，不删好友）|
| `LabelMembers(label, limit)` | 某标签的**全部**成员（滚动取全量，带 `complete`）|
| `SendToLabel(text, label, dry_run, limit)` | 按标签批量发；`dry_run` 只解析收件人 |
| `ForwardMessage(to, chat, match, dry_run)` | 右键「转发」一条消息给若干人（分小块多选）|
| `ForwardToLabel(label, chat, dry_run, chunk)` | 按标签分别转发；默认只预演 |
| `ProbeLabels(dump_dir)` | 只导航不写，报断在哪一步 / path probe |
| `AddListenChat(nickname, cb)` | 监听（WeChat）/ listen |
| `KeepRunning()` | 阻塞保持运行 / block & stay alive |

### guia（快捷函数 / convenience）

| 函数 / Function | 说明 / Description |
|---|---|
| `quick_send(text, who, verify)` | 发文本 / send text |
| `quick_send_file(path, who)` | 发文件 / send file |
| `quick_send_image(path, who)` | 发图片 / send image |
| `quick_reply(text, who, msg_id)` | 回复消息 / reply to a message |

### 消息对象 / Message objects (msgs)

`TextMessage` `ImageMessage` `VoiceMessage` `VideoMessage` `FileMessage`
`QuoteMessage` `LinkMessage` `LocationMessage` `SystemMessage` `FriendMessage` `SelfMessage`

常用属性 / Common attributes：`.type` `.content` `.sender` `.create_time` `.local_id`

---

## 参考 / References

- [README（英文 / English）](README.md)
- [README（中文 / 中文）](README.zh-CN.md)
- `wechatauto/demo_*.py` —— 各功能的可运行示例 / runnable demos