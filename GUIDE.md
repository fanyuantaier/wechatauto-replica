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
2. [整体架构 / Architecture Overview](#2-整体架构--architecture-overview)
3. [数据库读取 / Database Reading (WeChatDB)](#3-数据库读取--database-reading-wechatdb)
4. [实时消息监听 / Real-time Listening](#4-实时消息监听--real-time-message-listening)
5. [发送消息 / Sending Messages](#5-发送消息--sending-messages)
6. [媒体下载 / Media Download](#6-媒体下载--media-download)
7. [朋友圈 / Moments](#7-朋友圈--moments)
8. [多账号 / Multi-account](#8-多账号--multi-account)
9. [导出聊天记录 / Export](#9-导出聊天记录--exporting-chat-history)
10. [群聊操作 / Group Chat](#10-群聊操作专题--group-chat-operations)
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

`WeChat` 还提供 / also offers：

```python
wc.GetSession()            # 会话列表 / session list [SessionItem]
wc.ChatWith("filehelper")  # 切换当前会话 / switch current chat
wc.GetAllSubWindow()       # 所有会话窗口 / all chat windows
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

out = md.download_image("filehelper", 123)      # jpg/png/gif
out = md.download_voice("filehelper", 123)      # .silk
out = md.download_video("filehelper", 123)      # .mp4
out = md.download_file("filehelper", 123)       # 原文件 / original file

# 下载原图（通过UI点击触发微信下载）/ download original image (UI click triggers download)
out = md.download_image_original("filehelper", 123, timeout=30)
```

返回落盘路径，失败返回 `None`。 / Returns the saved path, or `None` on failure.

### 6.3 群聊图片 / Group chat images

- 群聊图片原图**只有点开查看过才落盘**；否则只有缩略图 / originals only stored after being opened
- `download_image` 会自动回退缩略图，文件名带 `_thumb` 标记 / auto-falls back to thumbnail (`_thumb`)
- `download_image_original` 通过UI自动化点击图片消息触发微信下载原图 / `download_image_original` triggers download via UI click
  - 会切到对应会话；图片需已在消息区可见（无需滚动） / switches to the chat; images must be visible in the message area (no scrolling)
  - 点击坐标依赖 WeChat 4.x 的 `mmui::ChatBubbleReferItemView` 布局（DPI 感知进程下按物理像素定位），不同窗口宽度/DPI 用相对偏移自动适配 / click coords rely on the `mmui::*` layout (physical pixels under a DPI-aware process); relative offset adapts to window width/DPI
  - 缩略图 UIA 控件是空壳、拿不到真实位置，因此对消息列表图片用坐标点击；预览窗口内的「图片原始大小」按钮是完整 UIA 控件，用 `Click()` 点击 / image thumbnails expose no UIA children, so they are clicked by coordinate; the preview-window button is a real UIA control and is clicked via `Click()`
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
```

GUI 交互版（点赞/评论读取）使用 `Moment` 对象，见 `demo` 脚本。

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

### Q4: 发送失败 / sending fails

- 微信窗口需可见（不能锁屏/最小化到托盘）/ window must be visible
- `desktop_available()` 为 False 时发送会安全失败
- 换用 `verify=True` 获得回读确认

### Q5: 语音下载不到 / voice not downloading

- 1.1.4+ 已支持搜索所有 `media_*.db`（微信分片存储）/ 1.1.4+ searches all media_*.db
- 确认升级到最新版本 / make sure you're on the latest version

### Q6: 监听无聊天记录的联系人 / contact with no history

- 消息表按需创建，对方发第一条消息后轮询即捕获
- 需要知道对方 wxid（用 `search_contact`）

### Q7: `WeChatAuto` 导入报错 / ImportError

- 本库入口类是 **`WeChat`**，不存在 `WeChatAuto`
- 教程代码若用旧类名，把 `WeChatAuto()` 换成 `WeChat()`

---

## 12. API 速查表 / API Quick Reference

### WeChatDB（数据读取 / data）

| 方法 / Method | 说明 / Description |
|---|---|
| `get_self_info()` | 当前账号信息 / current account info |
| `get_sessions(limit)` | 会话列表 / session list |
| `search_contact(kw)` | 搜索联系人 / search contacts |
| `get_nickname(user)` | 反查昵称 / reverse nickname lookup |
| `get_messages(user, limit, offset)` | 最近消息 / recent messages |
| `get_message_row(user, local_id)` | 单条原始行 / single raw row |
| `get_new_messages(user, since_seq)` | 增量消息 / incremental messages |
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
| `download_image(user, lid)` | 图片（含缩略图/wxgf 回退）/ image |
| `download_image_original(user, lid, timeout)` | 原图（UI点击触发下载）/ original image |
| `download_voice(user, lid)` | 语音 .silk / voice |
| `download_video(user, lid)` | 视频 .mp4 / video |
| `download_file(user, lid)` | 原文件 / original file |
| `download_media(user, lid)` | 按类型自动分发 / auto-dispatch by type |

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