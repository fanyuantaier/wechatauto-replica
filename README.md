[**English**](README.md) | [**中文**](README.zh-CN.md)

# wechatauto-replica — WeChat 4.x Windows Automation (wxauto-compatible)

![PyPI version](https://img.shields.io/pypi/v/wechatauto-replica)
![PyPI downloads](https://img.shields.io/pypi/dw/wechatauto-replica)
![Python](https://img.shields.io/pypi/pyversions/wechatauto-replica)
![License](https://img.shields.io/github/license/fanyuantaier/wechatauto-replica)
![GitHub stars](https://img.shields.io/github/stars/fanyuantaier/wechatauto-replica)

> [!NOTE]
> **📢 维护状态 / Maintenance Notice**
> 本人因今年升高一，开学后几乎没有时间继续更新本项目（如果有时间，争取周日更新）。遇到问题请自行在 Issues 区讨论，或询问 AI 协助解决。感谢支持！
>
> I'm starting senior high school and will register tomorrow (Aug 23). After school starts I'll have almost no time to keep updating (Sundays if possible). Please discuss issues in the Issues section or ask an AI. Thanks for your support!


Automate the **WeChat 4.x Windows desktop client** (not the web version): read messages, listen in real time, download media, export full history, read Moments (朋友圈), and send messages — by driving the local client directly.

> **Current version:** 1.2.6.1 · Windows 10/11 · Python 3.9+ (verified on 3.12) · WeChat **4.1.12+** (verified on 4.1.15.13)
>
> **Why this project exists:** the classic [wxauto](https://github.com/cluic/wxauto) relies on the UI Automation tree, which WeChat 4.x broke with self-drawn rendering (no accessibility nodes). wechatauto-replica is a drop-in-style replacement: messages are read through **local database decryption** (SQLCipher 4), and sending uses a **UIA + OCR hybrid** driver that auto-falls back between engines.

![Reading encrypted WeChat 4.x databases](docs/demo_db_files.gif)

*Reading the encrypted `contact.db` / `message_*.db` / `sns.db` files directly from `xwechat_files/.../db_storage/` — no web API, all local.*

## ✨ Features

| Capability | Status | How |
|---|---|---|
| Read messages | ✅ verified | Local SQLCipher 4 DB decryption (`wechatauto/db.py`) |
| Real-time message listening | ✅ verified | `Listener` incremental polling, per-chat worker threads |
| Emoji message capture | ✅ verified | Screen capture + direction-aware bubble auto-cropping |
| Full history export | ✅ verified | JSON / SQLite |
| Media download (image / voice / file) | ✅ verified | `MediaDownloader`: image v2 AES decryption, SILK voice, files |
| Image tiers on this machine (original / compressed / thumbnail) | ✅ verified | `download_image(..., tier='original'/'best'/'mid'/'thumb')`; `image_status()` / `list_image_status()` give the `reason` (4271 rows measured) |
| Trigger WeChat to fetch the original | ✅ implemented; the live UI path was **not** re-verified this round | `download_image_original()`: UI click triggers the download, then polls `_h.dat` until it stops growing (skipped entirely when a qualified original is already on disk) |
| "UIA tree is blocked" now names the cause | ✅ verified | `uia_driver` warns per scenario (32-bit interpreter / privileges / not running), once per process |
| Moments (朋友圈) read | ✅ verified | Direct `sns.db` reads (3382 feeds verified) |
| Multi-account | ✅ verified | `list_accounts()` + `account=` |
| Send text / file / image / reply / @member | ✅ verified | UIA-first, coordinate + OCR fallback |
| Voice call / Poke (拍一拍) | ✅ verified | UIA buttons + OCR menus |
| UIAutomation tree | ✅ after hot-activation | Writes the Qt accessibility gate inside Weixin.dll |
| Contact labels / forward a message | ✅ implemented; the live batch send is **not** verified | `ListLabels` / `CreateLabel` / `AddLabelMembers` / `SendToLabel(dry_run=)` / `ForwardMessage`, CLI `labels` / `forward` |
| Read-back gate before Enter + master OCR switch | ✅ verified (89 offline checks + verbatim live read-back) | `WxParam.SEND_CONTENT_RATIO` (0.6), `WxParam.ENABLE_OCR` (on) |

## 🚀 Quick Start

> 📖 **Full usage guide**: [GUIDE.md](GUIDE.md) (中英对照 / bilingual)

```bash
pip install -e .
# extra deps for the OCR sending path:
pip install winsdk pypinyin
```

### One command (CLI)

No Python required — after `pip install`, just run commands (asked for in issue #31;
**the existing Python API is unchanged**, these are plain callers of it):

**Easiest one:** type `wechatauto` (or `python -m wechatauto`) with nothing else — it runs a
health check, then shows a numbered menu (read messages / export / download images / send /
listen / moments), so you never have to remember a flag.

```bash
wechatauto                        # follow the prompts (menu only when stdin is a terminal)
wechatauto doctor                 # account / DB keys / image key / UIA tree at a glance
wechatauto sessions               # session list
wechatauto messages 文件传输助手   # recent messages (--type image, --json)
wechatauto export 文件传输助手     # dump to a UTF-8 text file
wechatauto images GroupName --out D:\img
wechatauto send "hello" --to 文件传输助手 --verify    # drives the real client
wechatauto listen GroupName       # print new messages until Ctrl+C
wechatauto moments --me
```

Subcommands work in **Chinese or English** (`消息` = `messages`, `导出` = `export`,
`图片` = `images`, `发` = `send`, `听` = `listen`, `体检` = `doctor`). Any handle works for a
chat — nickname, remark, wxid, group id or display name: the command resolves the display
name into the `username` the DB layer needs (typing a wxid into WeChat's search box is the
classic beginner trap: no hit, ~60 s of retries, then a bare `None`). `images` checks the
image AES key first and tells you what to do when it is missing instead of returning a wall
of `None`. Only `--original` drives the real client (**it moves your WeChat window**).

### Read messages

```python
from wechatauto import WeChatDB

db = WeChatDB()  # auto-detects account & data dir (WeChat must be logged in)

info = db.get_self_info()                    # current account
for s in db.get_sessions(limit=10):          # session list
    print(db.get_nickname(s["username"]), s["unread"])

hits = db.search_contact("Ayi")              # search contacts
for m in db.get_messages("filehelper", limit=10):   # recent messages
    print(m["create_time"], m["sender_id"], m["type"], m["content"])
```

### Send a message

```python
from wechatauto.guia import quick_send, quick_send_file

quick_send("Hello", "filehelper", verify=True)   # verify=True reads back from DB
quick_send_file(r"D:\report.pdf", "filehelper")
```

### Real-time listening

```python
from wechatauto import WeChatDB
from wechatauto.db import Listener

db = WeChatDB()
lst = Listener(db, interval=1.0)
lst.add_listener("filehelper", lambda msg, lst: print("new:", msg["content"]))
lst.start()
# ... your code ...
lst.stop()
```

Callbacks run on dedicated per-chat worker threads: messages in one chat are processed in order, different chats in parallel; slow callbacks (AI calls, image recognition) never block the poller.

### Media & Moments

```python
from wechatauto import WeChatDB, MediaDownloader, MomentDB

db = WeChatDB()
md = MediaDownloader(db)
md.detect_image_key()          # scan process memory for the image AES key (persisted after first hit)
for m in db.get_messages("filehelper", limit=50):
    out = md.download_media("filehelper", m["local_id"])
    if out:
        print("downloaded:", out)

moments = MomentDB(db)
for feed in moments.get_moments(limit=10):
    print(feed["nickname"], feed["text"])
    print("  images:", [i["md5"] for i in feed["images"]])
    print("  likes:", [l["nickname"] for l in feed["likes"]])
    print("  comments:", [(c["nickname"], c["content"]) for c in feed["comments"]])
    # download this feed's pictures & videos (local cache first, then CDN url)
    saved = moments.download_moment_media(feed, save_dir=r"D:\moments")
    print("  saved:", saved)
```

See `wechatauto/demo_moments_download.py` for a runnable download demo
(`python -m wechatauto.demo_moments_download [N] --out 目录`).

**Like & comment (UIA controls)** — Moments like/comment are server-side
actions done through the client UI, so they use the UIA-tree route (not the
local DB). `WeChat` hot-activates the `mmui` UIA tree and clicks the
朋友圈 nav button, then likes/comments a feed via its UIA controls:

```python
from wechatauto import WeChat

wx = WeChat()
moments = wx.Moment            # None if the UIA tree is unavailable
if moments is None:
    raise SystemExit("UIA tree unavailable — can't like/comment")
wx.SwitchToMoments()           # click 朋友圈 in the nav bar
items = moments.GetMoments()   # list feed items as UIA controls
first = items[0]
moments.Like(first)                            # thumb up
moments.Like(first, cancel=True)               # undo
moments.Comment(first, "Nice!")                # comment
moments.Comment(first, "Thanks!", reply_to="张三")  # reply to a comment
```

Runnable demo: `python -m wechatauto.demo_moments_interact [--like N | --unlike N | --comment N 文字]`
(plain run lists the latest feeds without touching the UI).

> **⚠️ Comment/reply automation is experimental — testing only.** The
> reply-to-a-comment feature (`ReplyComment`) locates the comment row on screen
> via OCR (WeChat's comment area is self-drawn) and then drives the UI to
> click / paste / send. Layout varies across versions and it is not
> production-grade — use it only on a test account to validate the pipeline.

## 🧠 How It Works

- **Reading** — WeChat 4.x stores everything in SQLCipher 4 encrypted SQLite databases under `xwechat_files/<wxid>/db_storage/` (`contact.db`, `message_*.db`, `media_0.db`, `sns.db`, …). Each DB has its own 32-byte key living in the Weixin.exe process memory (`com.Tencent.WCDB.Config.Cipher` config objects). The library locates them with a **read-only memory scan**, validates candidates with SQLCipher HMAC rules, decrypts pages to a temp dir and caches the result (first decrypt ~6s, then instant). WAL incremental merging with frame-salt filtering prevents `database disk image is malformed` corruption.
- **Sending** — WeChat 4.x chat UI is self-drawn (no accessibility nodes), so sending uses a hybrid driver: hot-activate the **Qt accessibility gate** inside Weixin.dll (RVA scan, writes the screen-reader flag) to materialize the `mmui::*` UIA tree — search box, `chat_input_field`, etc. Sending is **UIA-first, coordinate + OCR fallback**: auto-calibrating layout (`~/.wechatauto/layout-<machine>.json`), zoomed OCR (3x) with multi-round voting for rare Chinese characters, clipboard + Ctrl+V input to dodge IME interception.
- **Media** — image `.dat` files are `[6B sig][4B aes_size][4B xor_size] + AES-ECB + plaintext + xor` chunks. The account-level AES key is transient (only resident in memory while viewing an image); `MediaDownloader` scans for it, validates via JPEG/PNG magic, and **persists it to `image_keys.json`** so later runs need no scanning (or pass `image_key=` explicitly). Voice is plain SILK read from `media_0.db`; files are read from `msg/file/` with original names resolved from `message_resource.db`.

## ⚖️ vs wxauto

| | wxauto | wechatauto-replica |
|---|---|---|
| WeChat 4.x | ❌ UIA tree gone → broken | ✅ DB decryption + UIA hot-activation |
| Message reading | via UI tree | via local DB (full history, faster) |
| Sending | UIA clicks | UIA-first + OCR fallback |
| Media | limited | image AES decrypt, SILK voice, files |
| Moments | read + like/comment (UIA) | read + like/comment (UIA), full history via DB |

## ⚠️ Known Limitations

1. **WeChat must be logged in** — DB keys live in process memory; cached after first extraction, re-extracted automatically after re-login.
2. **Image AES key is transient** — only resident while viewing an image; persisted to `image_keys.json` once found, or inject via `image_key=`.
3. **Sending is a GUI operation** — fails cleanly when the window is locked/unresponsive (operations return a clear failure).
4. **Videos** are downloadable only when the mp4 already exists on disk (`msg/video/`).
5. **A group-chat image exists in up to three tiers** — `<md5>_t.dat` thumbnail, `<md5>.dat` the copy WeChat pushes by default, `<md5>_h.dat` the **real original**, which is only written after the image has been opened with 「查看原图 / 图片原始大小」 in WeChat. Ask before you retry: `MediaDownloader.image_status(user, local_id)` / `list_image_status(user)` report which tiers exist and why (`ok` / `mid_only` / `only_thumbnail` / `original_partial` / …), and `download_image(user, local_id, tier='original'|'best'|'mid'|'thumb')` picks a tier explicitly — `tier='original'` returns `None` instead of quietly handing back the compressed copy, and the tier is written into the filename (`_h` / none / `_thumb`). Not passing `tier` keeps the pre-1.2.4.2 behaviour verbatim. Measured over 4271 image messages in 211 sessions here: 2210 `mid_only`, 1428 `only_thumbnail`, 626 with a real original on disk — roughly **1 in 7**, so "cannot download the original" is usually WeChat's storage policy, not a decryption failure. `download_image_original()` still exists to trigger the fetch through the UI.
6. **Moments likes/comments** go through the UI (server-side actions) and need the hot-activated `mmui` UIA tree plus an unlocked desktop; they fail cleanly when the tree is unavailable. **Moments posting stays dropped** (4.x self-drawn UI, unreliable).
7. **Quote-message sending (BETA)** goes through a coordinate + OCR + `SendInput` pipeline that depends on WeChat 4.1.x self-drawn layout; positioning may drift with window size / DPI / chat content — test flow on a throwaway account only.
8. **Voice messages can be missing their audio locally** — WeChat only writes `voice_data` into `media_*.db` after a voice has been played/received on that machine, so `download_voice()` returns `None` for the rest, with no way to tell "not on disk" from "library broke". `MediaDownloader.list_voice_status()` / `voice_status()` now return the reason: `audio_not_downloaded` (play it once in WeChat) vs `audio_missing_from_media_db` (worth an issue). Measured over 958 voices in 20 sessions: 898 available, 54 flagged `download_status=0` and indeed absent, 6 with no media index entry — `download_status != 0` matched "audio on disk" with no exceptions.
9. **"The UIA tree is blocked" is usually a readable cause now, not a mystery** — the `mmui` tree only exists after one byte inside `Weixin.dll` is flipped, and that byte resets every time WeChat restarts, updates or you re-login. The library hot-writes it, but when it cannot, it used to return an *empty* tree in silence. It now names the reason once per process (no visible window / window found but its modules unreadable — with 32-bit Python called out separately, since reading a 64-bit process's module list from 32-bit always fails — pywin32 missing), so the fix is obvious instead of guesswork.

## 🗺️ Roadmap

- Calibrate and verify file/image/reply/@ sending on unlocked desktops
- Video message download (4.x storage location TBD)
- Performance: parallel export / first-scan, incremental memory-scan cache

## 📝 Changelog

### v1.2.6.1 (2026-10-10)

- ⚠️ **Important fix: group messages credited the sender to an unrelated contact — `real_sender_id` must be resolved through the `Name2Id` table of the shard the row lives in.** ([issue #34](https://github.com/fanyuantaier/wechatauto-replica/issues/34))
  - Symptom (found by a user in real use): sender names come out wrong, showing an unrelated contact or a group name.
  - Cause 1 (wrong table for the id space): `real_sender_id` is a rowid into **the `Name2Id` table of the `message_N.db` shard that holds the row**, while the code resolved it against `SenderName2Id` in `message_resource.db`. The two numbering spaces are unrelated, so the same number is a different person.
  - Cause 2 (the hard-coded "id 2 means me" is false): on this machine the local account sits at rowid **2/4/1** in `message_0/1/2.db` respectively, so `== 2` only holds in one of them — and the "fall back to 1 when nothing resolves" rule added in 1.2.4 is wrong on the other two.
  - Oracle (depends on no id table): WeChat writes the sender itself as a `sender:` prefix inside group text rows. Across 12 groups and 3662 comparable rows: **shard-based resolution matched 3662/3662, the old global table matched 0** (3656 attributed to someone else, 6 unresolved). All 856 文件传输助手 rows were sent from this machine: the new path returns the local wxid for 855 of them (the remaining row really is sent by `filehelper` itself), while the old path attributed **639** of them to a frequent contact or to a group.
  - **Scope: every release from v1.0.0 through v1.2.6** (`_sender_id_index()`, which reads the wrong table, is present in the initial commit; the earliest tagged 1.0.3 already has it). What it touched widened over time: `export_history`'s `sender_name` always used it, `get_messages` / listener `sender_username` since v1.1.8, and "who sent this non-text group message" since v1.2.4.1.
  - Fix: the per-shard `Name2Id` is now fetched together with the rows (`_name2id_map()` / `_shard_maps()`), and `_msg_row_to_dict`, `get_message_row` and the export-side `_resolve_sender` look the id up in the map of that row's `shard`; `_sender_id_index()` is deleted; new public `WeChatDB.sender_username(shard, sender_id)`; `wx._db_row_to_message` and `MediaDownloader._sent_by_self` now decide "is this mine" by comparing the resolved username with the local wxid, with no `sender_id == 2` and no "fall back to 1". When nothing resolves the field stays an empty string (unknown) instead of guessing from another database — exactly what the report asked for.
  - Behaviour change: `sender_username` used to be populated for almost every row (often wrongly); now about **0.2% (129 of 60243 rows, concentrated in one shard)** are empty; the same `sender_id` may legitimately resolve to a different person in another shard; and when it cannot be resolved `_sent_by_self` treats the row as not-mine and logs a debug line.
  - Tests: new `tools/test_sender_namespace.py`, **36 checks** — the two-shard fixture deliberately maps the same number to different people in each shard and collides the row ids, so the assertions can only pass if resolution follows the row's shard; includes a **negative control** (reverting to "both shards share one global table" turns `sid=2` into the same stranger in both) and a live section requiring 文件传输助手 senders ⊆ {local account, `filehelper`} with ≥95% local, plus zero disagreements against the body-prefix oracle in group chats.

- Everything else carries over from v1.2.6 (addressable cross-shard rows via `shard`, and shard read failures no longer treated as "this shard has no messages").

### v1.2.6 (2026-10-09)

- ⚠️ **Important fix: within a sharded chat, `(chat, local_id)` is not unique, so looking a row up by id could return a message from a different shard.** ([issue #20](https://github.com/fanyuantaier/wechatauto-replica/issues/20))
  - Background: WeChat splits one chat's history **horizontally across several `message_N.db` shards**, and `local_id` **restarts at 1 in every shard**. Once reads were merged (v1.2.2), `(user, local_id)` stopped being a unique key.
  - Measured locally: of 200 session tables, 54 span multiple shards; **20008** `(chat, local_id)` pairs hit more than one shard, and **10477** of those share the same `local_type` while carrying different content (a sample of image rows alone contained **35** groups with the same id but different md5).
  - Impact: `get_message_row()` used to pick the highest `sort_seq` silently. Voice, image, video and file downloads all go through it, so the observable result was "**asked for A's id, downloaded B's media**".
  - Fix: every message row now carries **`shard`** (the shard's database file name) — in the merged view `(local_id, shard)` is the unique key. `get_message_row(user, local_id, local_type=…, shard=…)` pins one shard; when several shards match and no `shard` was given it **warns first** and then falls back to the previous behaviour; a wrong `shard` name returns `None` with a warning instead of guessing.
  - Spread through the API: `list_voice_status()` / `list_image_status()` rows carry `shard` too, and `image_status` / `download_image` / `download_image_original` / `download_voice` / `download_video` / `download_file` all accept `shard`; `download_media()` passes the candidate row's own `shard` when it dispatches.
  - Compatibility: only **additive** — a new `shard` key in returned dicts and new **optional** keyword arguments; every existing call shape is unchanged.
  - Tests: new `tools/test_db_shard.py`, **46 checks** — a two-shard temporary-database fixture covers merged reads, `shard` addressing, that the new `? AS shard` placeholder cannot steal a WHERE parameter, and shard-failure visibility; the live section re-reads a real same-id, same-type, cross-shard row per shard (without `shard` you get the newest shard, with it each shard returns its own row) and asserts that `get_messages()` returns exactly the sum of the per-shard `count(*)` across all of the chat's shards.

- **Fix: a shard whose query raised `database disk image is malformed` was treated as "this shard has no messages".** Corruption now propagates to `_run_msg_query`, which rebuilds the decrypted cache and retries; other errors still skip that shard but name it in a warning (logged once per key, so a poll loop doesn't flood). This was the second silent source of "incomplete reads" — returning success with half the rows missing.

- **Removed: the `_find_msg_table()` / `_msg_conn()` single-shard stubs** (no callers, docstring saying "returns only the first matching shard"). The line-by-line analysis in [issue #20](https://github.com/fanyuantaier/wechatauto-replica/issues/20) was written against them — that code has been unreachable since v1.2.2, but the tombstones were still in the file.

- Everything else carries over from v1.2.5.1 (the drive-root data-directory fix, the read-back gate before Enter, the `WxParam.ENABLE_OCR` master switch, contact labels + right-click forwarding).

### v1.2.5.1 (2026-10-07)

- ⚠️ **Important fix: when WeChat's storage location is a drive root (the config holds `d:\`), auto-detection started depending on the working directory.** ([issue #32](https://github.com/fanyuantaier/wechatauto-replica/issues/32))
  - Symptom: same Python, same dependencies, same WeChat config — just a different working directory turns "works" into `RuntimeError: 未找到微信数据库目录，请通过 db_dir 参数手动指定`.
  - Cause: `_locate_account_root()` stripped the trailing separator with `rstrip("\/")`, turning `d:\` into `d:`. On Windows `d:` is a **drive-relative** path, so `os.path.join('d:', 'xwechat_files')` yields `d:xwechat_files`, resolved against **that drive's own current directory** — not the process working directory.
  - **Scope: every release from v1.0.0 through v1.2.5** (that line has been unchanged since the initial commit; this is not a regression).
  - Why most people never hit it: two conditions must hold together — ① the storage location is a bare drive root (a value like `E:\xwechat_files` is untouched by the strip), and ② work happens somewhere on that same drive (keep the data on E: while always running Python from C: and E:'s per-drive current directory stays at `E:\`, so the malformed path happens to resolve correctly). That is why it looks like "nothing changed, but yesterday it worked".
  - Fix: new `_abs_root()` keeps root semantics via `os.path.normpath` and also repairs a drive letter written without a separator (`d:` → `d:\`); all three sites of this pattern in `_locate_account_root()` and `_extract_path_from_config()` now use it, so candidates are always absolute. UNC roots are unaffected.
  - **Workaround without upgrading**: pass the account directory's parent explicitly as `WeChatDB(db_dir=...)`, or don't put WeChat's storage at a bare drive root.
  - Tests: new `tools/test_db_path_root.py`, **40 checks** — under a fake filesystem a `d:\` root must resolve to `d:\xwechat_files` (**with a negative control**: reverting the implementation to the old `rstrip` makes the same check return `None`, proving the assertions actually bite); each of drive letters A–F and Z checked individually; the per-drive-current-directory drift is demonstrated live on the real drives; the live database is detected identically from 4 working directories.
- Everything else is as in v1.2.5 (contact labels + forwarding, the read-back gate before Enter, the `WxParam.ENABLE_OCR` master switch, the chat-name placeholder tail, and three silently-broken window behaviours).

### v1.2.5 (2026-10-07)

- **New: contact labels + forwarding a message**, implemented against the anchors measured on the live 4.1.15.13 client (Contacts → Contact management → Labels).
  - 12 new `WeChat` methods: `ListLabels(prefer='db'/'ui')` / `CreateLabel` / `RenameLabel` / `AddLabelMembers` / `RemoveLabelMembers` / `DeleteLabel` / `LabelMembers` / `SendToLabel` / `ForwardMessage` / `ProbeLabels`; wxids are resolved to display names automatically and `RenameLabel` changes only the name (members and `label_id` untouched).
  - **Rehearse before you send**: `SendToLabel(text, label, dry_run=True)` only resolves recipients and never touches the window, `limit=1` tries one person first; `WeChatDB().list_labels()` is a pure DB read. `ProbeLabels` navigates without writing and reports which step breaks.
  - The CLI gains `labels` / `forward` subcommands plus two menu entries, accepting Chinese and English aliases alike. Guide sections §10.5 and §10.6 added.
- **New: read-back gate before Enter — `WxParam.SEND_CONTENT_RATIO` (default 0.6, `<=0` disables).** `Ctrl+V` can miss entirely (WeChat not in the foreground, focus not on the input field), and the keystroke used to go through anyway, sending an empty message or the previous draft — while the exception was swallowed and success was reported. Both the UIA path and the coordinate/OCR path now read the input box back before pressing Enter; on a mismatch they clear the box, return failure for the caller to retry, and do not consume the `rhythm` write budget.
  - Why 0.6 rather than a higher number: measured on the live client, an ordinary message containing an emoji codes scores **0.884**, and one wrong character in an 8-character Chinese sentence scores **0.875** — 0.9 would block good sends. An empty read-back is 0.0 and a stale draft 0.11, both caught.
  - Emoji codes (measured): `[微笑]` becomes a single `U+FFFC` inside the input box, while the stored message keeps the literal text (WeChat converts on send). Both sides are folded before comparing, giving 1.0. **Trade-off**: after folding, `[微笑]` and `[发怒]` are indistinguishable, so a wrong emoji code is not caught by this gate.
- **New: `WxParam.ENABLE_OCR` (default `True`, behaviour unchanged).** One switch to stop OCR when recognition is unreliable. It sits at the engine entry `ScreenOCR.recognize()` (plus short-circuits in `ocr()` / `ocr_zoomed()` that skip the screenshot itself), because 20 call sites are spread over `guia`, `moment` and `uia_driver` — a static assertion now pins that `guia.py` is the only module referencing `OcrEngine`. With it off, anything that can go through the UIA tree does; operations with **no UIA equivalent fail explicitly instead of clicking on guessed coordinates**: sidebar session lookup, search dropdown, the "send" button fallback, Moments elements, forward/multi-select menus. Pixel probes (input-box location, non-blank pane, highlighted row) are not OCR and keep working.
- **New: foreground cleanup API.** `ensure_visible(keep_topmost=…)` is now a parameter; `_minimize_blockers()` records the foreign windows it minimized, with new `restore_blockers()` (restores only what it actually minimized, without stealing focus) and `release_foreground()` (un-topmost + restore blockers).
- **Fixed: `current_chat()` used to carry the input box's placeholder tail.** WeChat puts "session name + placeholder" in the control's `Name` (measured: `文件传输助手` → `文件传输助手按住鼠标 语音输入文字`), so **every `current_chat() == who` comparison in the library could never be true**: an already-open chat still made `open_chat` return False, sends fell to the slow, error-prone coordinate path, and the consecutive-send fast path never triggered. Known tails are now stripped; unknown ones return the name unchanged (behaviour as before).
- **Fixed: topmost never actually worked on 64-bit Python.** `SetWindowPos(hwnd, -1, …)` passed `-1` as a 32-bit integer into an `HWND` parameter, so the call returned 0 (`last_error 1400`); it now uses `ctypes.c_void_p(-1)` / `c_void_p(-2)`. ⚠️ **Behaviour change**: topmost now really applies — `ensure_visible()` defaults to `keep_topmost=True`, so WeChat sits above every other window during a batch; call `release_foreground()` when the batch is done.
- **Fixed: the foreground lock was set to 0 and never restored.** `SPI_SETFOREGROUNDLOCKTIMEOUT` is a **machine-wide** setting; leaving it at 0 weakens foreground-stealing protection for all processes, not just WeChat. The original value is now read back and restored on return — and it turned out the value only takes effect in `pvParam` (passing it in `uiParam` returns FALSE and changes nothing).
- **Fixed: a clipboard held by another process raised straight through `send_msg` as a traceback** (`PyperclipWindowsException: Error calling OpenClipboard`, hit live once). `set_clipboard()` now returns a `bool` and contains the exception; callers treat a failure as "this input attempt failed" and retry or fail cleanly.
- **Improved: `_chat_is_open` prefers UIA and falls back to OCR** — when UIA answers, no magnified OCR pass and no extra screenshot are spent.
- **Compatibility**: no signature changes; `set_clipboard()` returns `bool` instead of `None`, and call sites test `is False` rather than `not …`, so overrides that still return `None` are not misread as failures. No new dependencies.
- **Regressions**: `tools/selftest.py` **496 checks / 0 failures**. Four new offline suites — `test_send_verify` 21, `test_ocr_send_gate` 26, `test_ocr_switch` 21, `test_chat_identity` 21; labels/forward keep `test_labels` 98 and `test_forward` 59. Live: one send through the UIA path and one through UIA-only (`ENABLE_OCR=False`) both read back **verbatim** from the database; the simulated "paste into thin air" scored 0.000 and pressed no Enter; the topmost set/release cycle was verified on the live window.
- **Not verified (stated plainly)**: placeholder tails on other WeChat builds (unknown ones fall back to old behaviour, no crash); the actual degraded behaviour of Moments/forward with `ENABLE_OCR=False` (offline only proves OCR is not called); `SendToLabel` in a real batch (`--go`) because it writes to other people; `send_msg` still spends 3 attempts (measured 56.9 s) before failing when the gate keeps rejecting; the foreground lock is not restored if `bring_to_front` raises mid-loop.

### v1.2.4.4 (2026-10-03)

- **New: one command — `wechatauto` / `python -m wechatauto`** (issue #31: "can you make calling it simpler? it's too much, e.g. one command"). **The existing Python API is unchanged** — the CLI is just a caller of `WeChatDB` / `MediaDownloader` / `guia.quick_send` / `MomentDB`; anyone who prefers Python keeps doing exactly that.
  - **Remember one thing only**: after installing, type `wechatauto` with no arguments. It runs a health check first (account / DB keys / image key / UIA tree — whichever one is zero is stated with the concrete next step), then shows a numbered menu: read messages / export / download images / send / listen / moments / list sessions / health check. Picking a chat lists your 10 most recent sessions (nickname + unread count) to choose by number, **so you never need to know what a `username` looks like**. The menu only opens when stdin is a terminal; in scripts and pipes `python -m wechatauto` still prints help and exits 2, so it can never hang a CI on `input()`.
  - Subcommands work in **Chinese or English**: `消息`=`messages`, `导出`=`export`, `图片`=`images`, `发`=`send`, `听`=`listen`, `体检`=`doctor`, `会话`=`sessions`, `朋友圈`=`moments`. That is a one-line lookup table — the argument definitions still live in exactly one place (argparse).
  - Three traps it absorbs for you, which is what "too complicated" actually was: ① the DB layer needs `username` while the UI needs the display name — `resolve_chat()` accepts nickname / remark / wxid / group id, and when two contacts share a name it asks for a wxid instead of silently picking one (typing a wxid into WeChat's search box is the number-one cause of "no hit, ~60 s of retries, then a bare `None`"); ② the image AES key is checked before downloading images, and a missing key is reported as what to do next rather than a wall of `None` that looks like a broken library; ③ the Windows console is GBK and Chinese bodies raise `UnicodeEncodeError` — the entry point reconfigures stdout/stderr to UTF-8 first, and `export` always writes UTF-8.
  - Two display fixes: video/file/quote bodies are whole XML documents, now collapsed to one line such as `[非文本正文] playlength=151 length=4211669` (use `--json` for the raw text); `--type` accepts both English and the Chinese type names stored in the DB (Chinese-only would make people conclude "this chat has no images").
  - `pyproject.toml` gained `[project.scripts]`, so installing gives you a real `wechatauto` command without `python -m`. Sending defaults to `--verify` (the send is read back from the database).
- **Not verified (stated plainly)**: the menu's real keystroke path — automation can only feed scripted input, so nobody pressed keys yet; and `send` against the live client (it drives the window and needs the real client behind the `rhythm` gate). Everything else has offline regressions: `tools/selftest.py` **490 checks / 0 failures**, including a new `cli` group of 41 (one of them pins "cli must not import the UI stack at module level" — verified in reverse by hoisting `guia` up, which turns it red).


### v1.2.4.3 (2026-10-01)

- **Fixed: "it says my own images have no original" / "it keeps clicking around and then reports failure" / "what I downloaded is still the preview"** — one batch of reports, four independent causes, **none of them decryption**:
  - **"Did I send this?" used to be hard-coded as `real_sender_id == 2`.** `real_sender_id` is a rowid into `SenderName2Id` in `message_resource.db`, and **which rowid your own account sits on differs per machine** — the `2` came from someone else's box back in 1.1.8. Measured here: self = **1** (all 400 messages in File Transfer Helper), while `2` is a frequent contact (512 image messages). The cost of the constant is direct: your own images were treated as incoming ones, the code clicked the left side first, and never hit the bubble. The check is now "does the resolved username equal my own wxid", with a fallback only when the index cannot be read.
  - **`download_image_original()` now targets the real original `_h.dat` and drives the UI whenever it is missing.** The middle tier `.dat` is no longer accepted as a result: it is "whatever WeChat pushed", which may be a full image **or itself a preview version** — live example: a `.dat` of 44,002 bytes next to a 2,961-byte preview, and that `.dat` was still a preview. No tier on disk can prove "this is not a preview version", so we also stopped pretending there is an answer for it (`has_full` is gone; `has_mid` says only whether a `.dat` exists). Callers who want "whatever is on this machine" use `download_image(tier='full'/'best')`, which never touches the UI.
  - **WeChat writes the file later than our judgement.** "It reported failure, then the next run said it was already local" and "it kept clicking bubbles and finally failed" are the same cause. Every decision point (on entry, before clicking the next bubble, after the tier wait times out, before declaring failure) now re-checks the disk and stops the remaining UI actions when the file is there.
  - **The download button in the preview window opens a standard Windows Save dialog** (titled 「保存」, not 「另存为」), and **the first save may write out exactly the preview currently shown** (user report: saving from the UI still produced the preview; re-downloading produced the real image). The saved artifact is now size-checked against the local tiers; when it looks like a preview, the code clicks 「图片原始大小」 and saves again, keeping the largest of the attempts. Button selection verifies `ControlTypeName` really is a button (the file-format combo box is also named 「保存」 in UIA — hit live once), the dialog must actually close for the click to count, and every failure path clicks 「取消」 so no modal is left on screen.
- **Added: scroll to the target image when it is off-screen** (`download_image_original(..., scroll=True, max_scrolls=6)`). "Which row is this image" is resolved by sliding-window alignment between the visible UIA rows and the database text/image sequence, with three gates: at least two text anchors and score ≥0.6 (a one-row viewport yields a fake 1.00 for every offset), tied best offsets must agree on a single answer, and the aligned row must actually be an image row in UIA. Rows-per-notch is measured after each scroll rather than hard-coded for a DPI. The hard precondition for scrolling is **knowing the WeChat pid** — the wheel is delivered at the cursor position at dequeue time, so an unverifiable landing point can hit whatever sits over WeChat; without a pid the code degrades to "current screen only, never scroll" and warns. `scroll-stuck` / `scroll-limit` are reported separately, and the fallback is counting newer images below, then trying bubbles one by one — it never scrolls or clicks forever.
- **Two narrow-window (portrait) click fixes:**
  - **One click on the 「微信」 nav tab does not leave the conversation.** Portrait is a single-pane layout: while a conversation is open, `session_list` and the search box are **absent from the tree entirely**, so the first click only brings the main window back to the chat page and a second click is needed to exit the conversation. The old single click made the `open_chat()` search-box lookup that follows it fail by construction — reported out as "cannot open the chat". `back_to_chat_tab()` now clicks → checks whether the chat page rendered → clicks again if not (`max_clicks=2`), and **clicks nothing when it is already there** (that click would yank the conversation the user is looking at).
  - **Image bubbles could not be hit.** The bubble position does not scale with row width (the avatar column plus margin is a fixed pixel count): on a 2598 px row the bubble sits at +44..+632, so "12% of row width" = +311 lands inside; on a 720 px portrait row the bubble sits at +141..+459 while the same formula gives +86 — background. Candidates are now "absolute anchor 300 px from the sender's edge → the old 12%", which collapse to the same point in a wide window, so **behaviour is byte-identical to the released version there**.
  - Two shape facts on the same path, now handled: **the preview window comes in two shapes** (either the desktop's direct child is `mmui::PreviewWindow`, or the top level is `Qt51514QWindowIcon` titled 「图片和视频」 with `mmui::PreviewWindow` one level inside — filtering only top-level children never finds the second), and **the zoom button's `Name` toggles** between 「图片原始大小」 and 「图片适应窗口大小」, so searching one hard-coded name bailed early and the 「保存」 fallback never ran.
- **Fixed:** `demo_original_image.py` still carried the deleted `> 100KB` rule as its "already downloaded" pre-check, which would report a compressed copy as an original.
- **Not re-verified on the live client this round** (stated plainly): the click-bubble → preview-window → button → `_h.dat` stretch needs the real client behind the `rhythm` gate, and the portrait bubble band for **self-sent** images is still a mirrored inference. Everything above it (tier judgement, row alignment, scroll decisions, dialog clicking, click geometry) has offline regressions.
- **Regression coverage:** `tools/selftest.py` **449 checks / 0 failures** (`image` 196, `tree` 25). Mutation testing: 69 arms on `media.py` plus 6 on `uia_driver.py`; the 15 arms run this round all turned checks red (relaxing `_harvest` to accept `.dat` → 5 red; self-sent early return back → 1; `has_mid` always true → 1; waiting on `.dat` → 1; dropping the entry disk-check → 1; bubble click back to pure percentage → 2; removing the row-width clamp → 1, an extremely narrow row would click x=0; ignoring the sender side → 5; no candidate de-duplication → 1; the six nav-tab arms 1–5 red each).

### v1.2.4.2 (2026-09-30)

- **Fixed: "I can only download the thumbnail, never the original"** (reported with a bot log: 「只要原图，先不用缩略图」 followed by retries that never converged). Three separate causes, none of them decryption:
  - **`download_image()` never looked at `_h.dat`** — the real original — and when it returned the compressed copy the filename was byte-for-byte the one an original would have (`<user>_<local_id>.jpg`), so a caller that only wants originals could only guess from the file size and retry forever. There is now an explicit `tier=` argument: `'original'` (returns `None` when the machine has no original — it will **not** quietly degrade), `'best'` (original → compressed → thumbnail), `'mid'`, `'thumb'`, and the tier is written into the filename (`_h` / none / `_thumb`). Omitting `tier` reproduces the old behaviour verbatim.
  - **`download_image_original()` had an absolute size gate, `getsize(_h.dat) > 102400`,** which is how a compressed copy was judged "not downloaded yet". Measured over the 705 `_h.dat` files on this machine: min 575 B, p10 4.3 KB, **p50 91.5 KB**, p75 563 KB, max 18 MB — i.e. **51.3% of real originals were being rejected as incomplete**. The judgement is now relative (non-empty, ≥1 KB, and not more than 10% smaller than the compressed copy), and it is exposed as `MediaDownloader.original_ready()` so callers can apply the same rule. The same function's `timeout` argument was decorative: it clicked and then slept a fixed 3 s, once. It now polls until the file is both usable and no longer growing, or until `timeout` — and if a qualified `_h.dat` already exists, the file is decrypted straight from disk **without touching the UI at all**.
  - **The tier report was lying because the query was missing a column.** An image's local filename comes from a 32-hex digest inside the message body, and the generic message `SELECT` does not carry `packed_info_data`, so every row came back "not an image". `WeChatDB.get_image_rows()` is a narrow query for `local_type=3` that does, with field names aligned to `get_message_row()`.
  - **New `image_status(user, local_id)` / `list_image_status(user, limit)`** — same shape as the 1.2.4.1 voice report: per image, the byte size of each tier present, the best tier available, and a `reason` (`ok`, `mid_only`, `only_thumbnail`, `original_partial`, `no_local_copy`, `no_md5`, `no_message_row`), one message-table query and one directory walk per digest. Measured here over **4271 image messages across 211 sessions**: 2210 `mid_only`, 1428 `only_thumbnail`, 626 `ok`, 5 `original_partial`, 2 `no_local_copy` — **about 1 in 7 has an original on disk**, so most of these reports are WeChat's storage policy, not a library bug, and asking first removes the retry storm.
  - **The UI path is now honest about which step failed**: no 「图片原始大小」 button in the preview window (the image already *is* the original, or WeChat offers no entry) is a distinct warning pointing at `tier='best'`, bubbles that are not in the visible area are reported instead of guessed at, and when several image bubbles are visible the one most likely to be the target is tried first — computed as "how many newer images does this one have below it in the session" — with the digest re-checked after every click, so a wrong bubble cannot pass as the right one.
  - **Not verified live this round**: the click → preview-window → button path needs the real client and the `rhythm` gate, so it stayed untested; everything above it (tier judgement, routing, waiting, ordering, the DB query) is covered offline.
- **Fixed: an empty UIA tree was silent** (reported as 「控件树被屏蔽」). `uia_driver` filters out windows whose process has no `Weixin.dll` module, `ensure_materialized()` returned `False` on the empty list, and `ensure_window()` quietly switched to the "set the screen-reader flag and wake" branch — **without logging a single line**. "Every window got filtered out" is precisely the signature of a 32-bit interpreter enumerating a 64-bit process's modules (`TH32CS_SNAPMODULE` returns `ERROR_PARTIAL_COPY`), but the user had no clue and could only say "blocked". Added `gate_block_hint(diag, bits=None)` (pure function, so it is testable offline) and `_warn_gate_blocked()`, which pick from four actionable wordings: no visible window at all (not started / not logged in / minimised to tray), window found but modules unreadable (split by bitness — 32-bit is told to switch to 64-bit Python, 64-bit is pointed at privileges and the security-software allow-list), pywin32 missing, and — when at least one window survives — **no output at all**. Each reason warns once per process, because a long-running listener scans every round and spam is worse than silence. Filtering logic and return values are unchanged, so existing callers are unaffected.
- **Regression coverage**: `tools/selftest.py` gained an `image` group (39 checks, pure offline: temp attach trees + fake DBs — the 90% boundary on both sides, all seven `reason` values, all five `tier` routes and their filenames, no-silent-degrade, bubble ordering, the poll-until-stable wait, `get_image_rows` column alignment, and the one-walk-per-digest cache) and a `tree` group (16 checks with fake `win32gui` + fake pid/module enumeration: scan counts, four wordings, zero output in the healthy case, de-duplication, and that both real call sites actually consult the diagnostic). Whole suite **282 checks / 0 fail**. Mutations: restoring the `>102400` gate → 5 red; deleting the relative-size rule → 2 red; not marking `_h` in the filename → 2 red; ignoring "how many newer images" when ordering bubbles → 3 red; letting `tier='original'` silently fall back → 1 red; scanning the same digest twice → 1 red; dropping the diagnostic call in `ensure_materialized` → 2 red.

### v1.2.4.1 (2026-09-25)

- **Fixed: a group message could not tell you who sent it unless it was text** (issue #20 territory, reported as "who is talking"). `real_sender_id` is a numeric rowid; `wechatauto/db.py` already resolved it through `message_resource.db`'s `SenderName2Id` into a real wxid and put it in `sender_username` — but `wx.py:_db_row_to_message` never passed that on, `msg.wxid` stored the number, and the only working path was scraping the `wxid_xxx:\n` prefix out of **text** bodies, so images / voice / files / stickers were anonymous. Now `msg.sender_wxid` carries the real wxid (falling back to the body prefix, then to the old numeric value so nothing that worked breaks), `msg.sender` / `sender_remark` give the remark-or-nickname, and `msg.wxid` for your own messages is your real wxid instead of the constant `2`. New `Chat.GetGroupMembers()` (`{username, nick_name, remark, is_owner}`) and `WeChatDB.nickname_map()` (cached wxid→name) cover people who are not in your contact list. Measured over 8 real groups / 1249 stored messages: **95.8%** carry a sender identity, and **non-text messages went from 0% to 99.6%** (451/453). The remaining 4.2% have no identity anywhere in WeChat's own data — `SenderName2Id` has 655 rows and does not contain those ids, and sampling 20 of them found no username tag in `source` either. Nothing is invented: `sender_wxid` stays empty. A stale fallback that fed the numeric rowid into `contact.username` (which can never match, and made `get_nickname` echo the number back as if it were a username) is gone.
- **Fixed: `download_voice()` returning `None` told you nothing** (issue #20, "26 voice messages but only 19 came through"). Reproduced, and the cause is **not** a broken lookup: an independent re-implementation agreed with the library on **958/958** voices, and the "svr_id exists but under a different `chat_name_id`" case was **0**. WeChat only writes `voice_data` into `media_*.db` after a voice has been played or received on that machine, so most misses mean the audio was never on disk — and the silent `None` made that indistinguishable from a library bug. The oracle was already there: the message table's **`download_status`**. Over 975 voices the correlation is exact — `download_status != 0` ⇔ "audio on disk" (515 rows `ds=1` + 400 `ds=5`, 0 exceptions), and all 60 rows with `ds=0` were genuinely absent. New `MediaDownloader.list_voice_status(user)` / `voice_status(user, local_id)` return `available` / `bytes` / `download_status` / `self_sent` / `reason`, where `reason` is `ok`, `audio_not_downloaded` (nothing on disk — play it once in WeChat), `audio_missing_from_media_db` (the flag says it should be there and it is not — *that* one is worth an issue), `session_not_in_media_index`, `no_server_id` or `no_voice_row`. `download_voice()` keeps its signature and behaviour; on failure it now logs the reason. Measured availability here: **93.7%** (898 ok / 54 not downloaded / 6 with no media index entry). The new narrow query also guards a real trap: `download_status` is deliberately **not** in the shared message `SELECT`, and where a message table lacks the column the query degrades and returns `None` for it — the shared path's `except: continue` would have silently dropped **every row of that shard**, turning "cannot read" into "there are no voices", which is worse than the bug being fixed.
- **Behaviour change to know about**: in groups, `msg.sender` used to be the raw `wxid_xxx` scraped from the body; it is now the remark/nickname. Read `msg.sender_wxid` for the raw id.
- **Regression coverage**: `tools/selftest.py` gained a `sender` group (22 checks: the numeric-rowid masquerade, the text-prefix fallback, your own messages, no-db and nickname-query-failure paths, cache semantics, three group-member failure modes, and both listener paths actually passing `db` through) and a `voice` group (28 checks, built on real in-memory / temp-file SQLite message and media databases, including the missing-column degradation that must not drop rows). Whole suite **226 checks / 0 fail**. Mutations confirm both groups bite: reverting the sender-identity resolution → 5 red; putting the fake numeric fallback back → source check red; making the voice query use the shared `continue` on a missing column → "does not lose rows" goes red (0 rows came back) and the group crashes.

### v1.2.4 (2026-09-24)

- **Adapted to WeChat 4.1.15.13** (the client auto-updated mid-round; `Weixin.dll` 198,060,584 → 201,552,944 bytes). Three separate things had to change, and each one looked like the others from the outside:
  - **The search entry is now collapsed by default.** What used to be an edit box is a `mmui::XButton` named 搜索 (measured `[314,84,370,140]`); the `mmui::XValidatorTextEdit` only appears *after* that button is clicked. `_search_box(expand=True)` clicks it once and waits for the box; `search_box_rect()` still resolves on the collapsed button (it returns the button rect as a read-only anchor, deliberately **without** clicking — an anchor must not have side effects) while `open_chat()` asks for the expanding variant. On builds where the box is already resident, nothing extra is clicked. 4.1.13.x keeps working; which branch is taken is asserted offline.
  - **The accessibility gate RVA moved** to `0xb135c38` and is in the version table, so the vectorised scan from 1.2.3 is not needed on the common path (it still is for unlisted versions, and results are cached in `~/.wechatauto/gate_cache.json`).
  - **Entering Moments needed an explicit wake-up.** When the `mmui` tree had not materialised yet, `_switch_to_moments_new_style` failed with "找不到导航按钮" — which reads exactly like "this WeChat version is unsupported" and sent me down the wrong path first. It now checks tree readiness, calls `ensure_materialized(timeout=6.0)`, and **re-anchors the root control afterwards** (the old handle points at the empty shell from before the wake-up).
- **Fixed: Moments scrolling never actually scrolled — `find_moment` only appeared to work.** `_send_scroll()` posted a `MOVE` and a `WHEEL` `SendInput` back to back with **zero pause** between them. Wheel events are delivered to whatever window is under the cursor *when they are dequeued*, so the wheel landed on the old position, i.e. on the wrong window entirely; and the mmui timeline ignores the wheel unless WeChat is the foreground window. Now cursor placement is verified (`SetCursorPos` → `GetCursorPos` poll, ≤8 tries ≈0.4 s) before a 0.3 s settle, and `_scroll()` brings the window to the foreground first. Measured on the live client: `find_moment` hit 2/2 runs (51.4 s / 14.2 s), later 17.2 s — before this it was a coin flip that looked like a timing fluke.
- **Moments like / comment now have one implementation.** There were two parallel routes; the one actually exercised by the demos (locate the 「…」 button, then click the 赞 / 评论 in the float layer) was not what `LikeMoment` / `CommentMoment` used. `Like` and `Comment` now take the float route first and fall back to the legacy right-click menu only when it fails; `LikeMoment` / `CommentMoment` delegate instead of carrying their own copies. The legacy comment window (`MomentCommentDialog.send`: click box → `Ctrl+A` → paste → 发送) also **bypassed the 1.2.3 throttle entirely** — it is gated now, placed after the preconditions so a rejected comment does not consume a write slot. Verified on the live client: like and comment both landed, and the comment is present when the post is read back from the local Moments database. **Lesson kept: two implementations of one feature means the audited one is not the shipped one.**
- **Fixed: `find_moment` could hand back a cell that nothing can be clicked on.** UIA recycles a cell handle once its row scrolls out of the viewport, and the recycled node reports `BoundingRectangle == (0,0,0,0)`; every coordinate action on it then raises `Can not move cursor`, which surfaced as "未能打开朋友圈操作菜单" **after a successful locate**. A hit is now checked before it is returned: `_rect_usable()` (unreadable / degenerate rect), `_reattach_item()` re-claims the same post among the currently visible cells by nickname + content prefix + time and swaps the handle **in place**, so the object the caller is holding stays valid, and `_settle_item()` scrolls it back into view once if no live twin is on screen. Re-claiming is deliberately conservative: a *different* post by the same publisher is never adopted (that would like the wrong thing), and a post with neither text nor timestamp — bare nickname — is refused outright. Content is compared by "first 10 characters contain each other" rather than by exact signature, because the UIA summary truncates the body while the DB-corrected body is longer; an exact signature would fail to recognise its own post. Both `find_moment` hit sites, `_locate_more_click` (per retry) and `_invoke_action_menu` (before it touches anything) go through this.
- **Fixed: a real concurrency crash while watching all conversations.** Two threads reaching the same database at once wrote to the same `dst.tmp` scratch file: `FileNotFoundError` from `os.replace`, thrown out of the listener. `db._open()` now takes a per-database build lock, names its scratch files uniquely (`dst.<pid>.<thread-id>.tmp`) and sweeps scratch left behind by a killed process (older than 600 s).
- **Fixed: `get_messages()` was not fail-closed on bad paging arguments.** `limit=0` returned a full page and `offset=-1` returned the **last** row (Python slicing, not a database error), which silently produced wrong answers instead of an empty list — found while the live listener was paging a short session. `limit <= 0` or `offset < 0` now return `[]`.
- **Added: `AddListenAll()` really watches every session.** The global callback used to be registered **per session**, so any conversation that already had its own `AddListenChat` handler was skipped and never saw the global one; de-duplication is now by callback, newly discovered sessions are attached during polling, the pseudo-chat handed to the callback is usable (its `nickname` resolves, `chat` is created lazily, `SendMsg` works), the global callback is re-attached when the listener restarts, and `RemoveListenAll()` detaches for real. Documented in `GUIDE.md` §4.3. **Known cost** (not fixed this round): on an account with ~200 sessions, registering takes ~11 s and each poll round ~10 s, because every session is opened and read; that also makes it a suspect for the high-I/O report in issue #25.
- **Live verification** (real client, `calm` rhythm profile, actions spaced out): send to File Transfer Helper with verbatim database read-back twice (new `sort_seq` matched exactly); `back_to_chat_tab()` + `open_chat()` end to end (7.2% / 8.3% whole-window pixel change as the objective "the page did switch" oracle); Moments locate → like → comment confirmed on screen and in the database.
- **Regression coverage**: `tools/selftest.py` gained a `moment` group (30 checks: fake controls **and** real `MomentItem` instances, all offline — no WeChat, no clicking), plus the `click` (8) and `listen` (7) groups added during this round; the whole suite is **175 checks / 0 fail** (147 immediately before this fix; the sessions/messages groups report a few more or fewer depending on account data). Two mutations confirm the new group bites: make `_rect_usable` always return `True` → 15 checks red; make post re-claiming compare only the nickname → 5 red, and they are the "must not adopt a neighbour" ones.

### v1.2.3 (2026-09-22)

- **Added: a human-pacing layer, `wechatauto/rhythm.py` — on by default, and it changes default visible behaviour.** This account tripped WeChat risk control once (2026-09-21, forced re-login mid-session), so from now on every action that drives the real client has to move like a person, and it had to be enforced in the library rather than in a demo script. What risk control sees is the **time distribution of actions**, not coordinates: constant intervals, a cursor that teleports, clicks that always land dead centre, perfectly uniform typing, several writes per second. Now `nap()` jitters every wait (multiplier floor pinned at 1.0 — it only ever *lengthens* the fixed sleeps that hold render stability), `move_to()` walks the cursor along a quadratic bezier and `point()` picks a random interior target after insetting all four edges (no more `BoundingRectangle` centre hits), `type_gap()`/`key_hold()` break uniform keystrokes, and `gate()` throttles **outward-visible writes only** (send / send-file / like / comment / recall / poke / voice call) with a minimum interval plus a rolling-window burst cap. Read paths (database, screenshots, OCR, control lookup) are never throttled. Profiles `natural` (default) / `calm` / `fast` / `off`; `off` reproduces pre-rhythm values exactly and is for control experiments only. Override with `WECHATAUTO_RHYTHM`, `WECHATAUTO_WRITE_GAP`, `WECHATAUTO_WRITE_BURST`. Throttle state is persisted to `~/.wechatauto/rhythm.json` so it also holds **across processes** — every demo script is a fresh Python process, which makes in-memory rate limiting worthless. On the default profile two sends are at least 2.5–6 s apart and the 7th write inside 120 s enters a 30–75 s cool-off. Cross-process is documented last-writer-wins; no named mutex was added on purpose.
- **Fixed: the UIA gate scan could stall `quick_send` for ~8 s and could abort it outright.** A user reported hanging inside `_rip_xrefs_to_rva`'s byte loop (198 MB `Weixin.dll`; measured 6.6–9.8 s here, which looks like a deadlock on slower machines), and nothing on that path caught errors — any exception propagated out of `ensure_window` and killed the whole `quick_send` call. The scan is now numpy-vectorised: identical candidates on the real DLL (`0xae2b0c8`) at 0.56–0.77 s (~12x), with the old byte loop kept as the no-numpy fallback (`numpy` arrives via `opencv-python`, so a normal install never takes it; if it is missing the scan is only slower, never absent). Scan results are cached on disk in `~/.wechatauto/gate_cache.json` keyed by DLL identity (version directory + size + mtime), **including negative results** ("scanned, no candidates" — unsupported versions were paying the full rescan every launch); a second scan in the same process is 0.000 s, and a verified gate RVA is restored from disk and put first in the candidate list. Unreadable file / not-a-PE now return an empty sequence instead of `None` (callers iterate directly) and are not written to disk. Exceptions during hot activation now degrade to "skip this round, fall back to OCR/coordinates" instead of breaking the send; `KeyboardInterrupt` is a `BaseException` and still propagates, so Ctrl+C keeps working.
- **Fixed: the older Moments comment path bypassed the throttle entirely.** Comments have two parallel implementations; the previous round gated only the newer template-matching one (`_click_comment_send`), while `Moment.Comment` still went through the old UIA comment window (`MomentCommentDialog.send`): click the edit box → `Ctrl+A` → paste → click 发送, with not one step throttled. That is now gated, placed after all precondition checks so a failed precheck does not consume a write slot. **The lesson: audit outward writes by the action they perform, never by function name.** `tools/selftest.py` now scans for the action itself (`SendKeys('{Enter}')` / `press_enter` / `keybd_event` / clicking 发送) and fails if such a function has no `rhythm.gate`, with an explicit whitelist for functions that merely reference the send button or produce nothing outward (button lookup, pinyin candidate selection, OCR matching, Enter-to-open-chat). Deleting this one gate turns 3 checks red and names `moment.py:MomentCommentDialog.send`. Every remaining write entry point was traced to a gated sink: `quick_send`→`send_msg`→`click_send`, `quick_quote`/`at_member`→`click_send`, `send_text_to`→`send_text`, `ReplyCommentMoment`→`ReplyComment`→`_click_comment_send`, `wx.SendMsg`→`send_msg`, `sender.send_to`→`send`.
- **Live re-verification (2026-09-22, real client, `calm` profile, actions spaced out)**: a send to File Transfer Helper passed with verbatim database read-back (new `sort_seq` matched exactly, throttle ledger recorded one write, hybrid-path log confirms the UIA driver was active); `back_to_chat_tab()` + `open_chat('文件传输助手')` passed end to end.
- **Known issue (not fixed this round, and it corrects a v1.2.2.6 claim)**: v1.2.2.6 stated that clearing `WS_EX_TRANSPARENT` around the event makes UIA clicks land. **That effect did not reproduce on this machine.** Measured: the style is genuinely cleared (`ex=0x80000`), yet `WindowFromPoint` still returns the plain `Qt51514QWindowIcon` main window instead of the render sub-window at the nav-tab point, and the click changes nothing (0.0% whole-window pixel diff); `Control.Click()` and `Invoke()` are equally inert. When that style was left cleared across processes, the very same point *did* switch pages (15.3%) — so the hit-test decision involves more than the `WS_EX_TRANSPARENT` bit (`MMUIRenderSubWindowHW` is a layered window, where per-pixel alpha plausibly matters). Search-box / `ValuePattern` routes are unaffected, so sending, searching and opening chats all work; **coordinate clicks on the nav bar remain unresolved**. This round changed no code in that area, and the "fixed" claim has been removed from the record rather than left standing.
- **Regression coverage**: `tools/selftest.py` gained a `gate` group of 35 checks (synthetic PE fragments + temporary cache files + fake window handles, fully offline, never touches WeChat) and the `rhythm` group grew to 42 with the action-based throttle audit; whole gate 131 checks. Three mutation checks confirm the tests bite: delete the no-numpy fallback → the group crashes; drop the displacement sign-extension → 6 fail; delete the comment gate → 3 fail and name the function.

### v1.2.2.6 (2026-09-21)

- **Fixed: `calibrate_layout()` always raised `NameError` in the published 1.2.2.5.** The timeout wrapper `_run_with_timeout` in `guia.py` uses `threading.Thread`, but the module never imported `threading` (checked against the 1.2.2.5 wheel: `threading.Thread` present, `import threading` absent). Layout calibration is the entry path of the UIA driver, so every pip-installed user hit it on the first calibration; local checkouts were synced separately and hid it. The two layout profiles failed **differently**, which is why one report was not enough: on `wide` calibration returned `False` and wrote no layout file at all, while on `portrait` the send-button probe swallowed the same error and calibration returned `True` on default ratios. Both shapes were reproduced by deleting the module attribute from a synced copy. A probe that errors inside the timeout wrapper now leaves a log line, and the outer handler separates code defects (`NameError`/`UnboundLocalError`/`AttributeError`/`TypeError`/`ImportError` → `wxlog.error`, reaches the console) from recoverable misses (OCR anchor simply not found → debug, falls back to defaults by design). Adding the import does not make OCR find the anchor — that stays a fallback, not a failure.
- **Fixed: send verification could confirm a message that was never actually sent** (`_verify_sent`). Two independent holes: it matched on **substring**, so a draft left in the chat input (the body that actually went out read `校准wechatauto 部署自检 OK`) still verified a call for `wechatauto 部署自检 OK`; and with **no watermark**, an older self-message containing the target text among the last rows validated even when this send produced no row at all — the UI returning success only leads to polling, never to a resend, so a stale row is accepted on the first check. Plain-text sends now require a **verbatim** body match (`strip()` is not verbatim); reply / quote / `at_member` keep substring matching because WeChat wraps those bodies, and that rule is now explicit per call site. Every verified send first takes a **pre-send watermark** of the target chat: the max `sort_seq` *plus* the `(sort_seq, local_id)` identity set of the top rows, because real `sort_seq` values tie heavily (up to 8 rows in one chat) and a bare `>` would reject a genuine send. Verification also resolves the display name to a `username` before reading: message tables are keyed by `username` and a wrong one returns `[]` **silently**, so group-chat verification had been failing closed for the wrong reason. When no watermark can be taken (fresh chat, DB unavailable) verification falls back to the unwatermarked check rather than reporting failure.
- **Regression coverage**: `tools/selftest.py` gained an offline `verify` group (14 checks against a fake DB) and an offline `calibrate_layout` group covering both profiles and the anchor-hit path (8 checks against a fake window) — 36 offline checks, full gate 55 pass / 0 fail. The verifier's rules were additionally replayed read-only against the live decrypted database (no message was sent); the end-to-end send path still needs a real take.
- **Fixed: a UIA click could land on whichever window sits behind WeChat.** WeChat's content window (`MMUIRenderSubWindowHW`) carries `WS_EX_TRANSPARENT` (measured `exStyle=00080020`), so a raw `mouse_event` at its coordinates is skipped by hit-testing and delivered to the plain `Qt51514QWindowIcon` window behind it — this is why UIA clicks looked ignored on 4.1.13+. `uia_driver` now clears the extended style around the event and restores it immediately (`_click_at` / `_click_ctrl`), the way `guia.wx_click` already did. The mouse wheel is the exception: it works on that window unchanged.
- **Fixed: `open_chat` could not recover when WeChat was parked on the Moments page.** The session list does not exist there, so the search-based path never resolves — measured as an 8+ minute spin at high CPU, and separately as a ~90 s give-up that returns `None` with nothing on screen explaining it (it cost a whole recording take). `WeChatUIA.back_to_chat_tab()` clicks the `mmui::MainTabBar` 「微信」 item. It cannot ask which page is showing: on this build `XTabBarItem` exposes no selection state (plain `ButtonControl`, no `SelectionItem` pattern, `LegacyIAccessible.State` always 0) and the per-page controls stay in the tree after a switch, so the tab is clicked unconditionally — clicking the already-selected tab only scrolls the session list back to the top.
- **Fixed: the search-box calibration ratio could paste a chat name into a live conversation's input box.** The stale `SEARCH_BOX_RATIO` resolved to a click point off the real box (computed center 212,120 vs a measured box at 250,152–412,192), so the clipboard paste went to whichever chat was open. `_search_chat` now prefers the UIA `search_box_rect()` and self-checks afterwards: if the text did land in the chat input it is cleared through `ValuePattern.SetValue('')` and the search fallback is abandoned — nothing is sent.
- **Added to the UIA driver**: `search_box_rect()` and `_set_text()`. `ValuePattern.SetValue` drives WeChat's live search with no keystrokes and no clipboard, but it does **not** give the Qt widget focus, so it is used for the search box only — the send path still needs a focused input and `{Enter}`.
- **Privacy fix**: `media.py` no longer prints 12 bare `[DBG]` lines to stdout, one of which carried the absolute `.dat` path containing the account **wxid**. They are `wxlog.debug` now (the console handler is INFO by default), and the two silent `return None` bail-outs became warnings.
- **Still unverified on a live client this round**: whether `_click_ctrl` and `back_to_chat_tab` actually land the click on WeChat's content area (the relogin interrupted that check). The `WS_EX_TRANSPARENT` measurement and the search-box rect numbers are real; the end-to-end effect of the two new click paths is not yet demonstrated.
- **Thanks to [wenjiavv](https://github.com/wenjiavv)** for reporting both of the above with reproductions, a per-profile symptom split and fix proposals ([#28](https://github.com/fanyuantaier/wechatauto-replica/issues/28), [#29](https://github.com/fanyuantaier/wechatauto-replica/issues/29)).
- **Fixed: `RecallGuard` could not see a single revoke — two independent faults.**
  1. **Revoke rows were never delivered.** WeChat rewrites the original row in place (across 208 local sessions, 64 `revokemsg` rows: `revoketime - create_time` lands in 1-30 s for 10 of them, 31-300 s for 54, and zero for none), and `local_id` / `create_time` stay those of the original message. `Listener` increments by `sort_seq > watermark`, which never changes on a rewrite, so no revoke event is emitted. `watch()` now runs a `wxrecall-scan` daemon thread that re-reads the last `scan_limit` (default 30) rows per session every `scan_interval` (default 2.0 s) and diffs `local_id` against the mirror: normal in the mirror, `revokemsg` in the live DB = one revoke. `scan_now()` is exposed for scripts.
  2. **Even when delivered, the original was never found.** `_find_original` used `create_time < revoke_time`, where `revoke_time` is the revoke row's own `create_time` (= the original's timestamp) — the strict `<` excluded the only matching row. Lookup is now exact by `(chat, local_id)` first (the rewritten row keeps its `local_id`, so the mirror row with that id is necessarily the original), with the time window only as a fallback and relaxed to `<=`.
  3. `on_msg` no longer mirrors revoke rows — storing one would overwrite the original it is meant to rescue.
  4. The revoke timestamp now parses `<revoketime>` (it used to print the original send time); `(chat, revoke_time)` is the dedup key, so the Listener and polling paths cannot double-report and a restart cannot re-report history.
  5. `close()` now stops the polling thread.
  Measured: offline 3/3 real revoke rows (local_id 62/64/88) restored, a second `scan_now()` returns 0 rows (dedup works), only `MainThread` left after `close()`; the live path (send → mirror 20→21 → revoke → restore) passes too.
- **Security fix: `demo_media.py --list` printed media XML verbatim**, exposing `aeskey`, `cdnthumbaeskey`, `cdnthumburl` and `md5`. It now prints only dimensions, byte size, duration and file name.
- **Added: OCR fallback for Moments likes/comments under WeChat 4.1.13's merged layout** (`Moment._read_comment_cell_ocr`). Likes and comments now live in a sibling `mmui::TimelineCommentCell` that never enters the UIA tree, so parsing the text cell always came back empty; an empty UIA result now falls back to "comment-box region screenshot + OCR". The comment-box search area also moved to 320 px above the viewport bottom (the old `bottom+8` band landed on the taskbar and never matched on 4.1.13).
- **Cleanup**: `demo_send.py`'s `pick_default_image` docstring is now a raw string, silencing a `\W` escape warning.

### v1.2.2.5 (2026-09-19)

- **Fixed: cached Moments pictures decrypted into files nothing could decode.** Two independent causes on the same `.dat` v2 path:
  1. **Wrong single-byte XOR key for cache containers.** That key is the low byte of the account's config dword, but the code derived it per file from the plaintext's last two bytes (`tail ^ 0xFF == FF D9`). WeChat appends a **24-byte footer after the image end marker** in Sns cache containers (189/295 measured here), so the check failed and it silently fell back to a wrong key — the whole tail segment came out garbled. Resolution order is now **config dword (authoritative) -> thumbnail statistics -> fallback**, resolved once per account.
  2. **The footer was kept as image data.** Decrypted output is now trimmed at the JPEG/PNG end marker, so a strict decoder no longer rejects an otherwise valid picture over trailing bytes.
  Measured: Sns cache containers passing `MediaDownloader.decrypt_image()` **118/295 -> 295/295**; the Moments cache index's decrypt failures **177 -> 0**; a 15,577-file chat-image sample **429 JPEG + 171 wxgf, 0 failures** (chat media unaffected, wxgf/WXAM containers untouched).
- **Fixed: `MomentDB.find_local_media`'s size guard never ran on its most common path.** The "reject an impostor by size deviation" check only existed on the multi-candidate branch; with exactly one same-dimensions candidate the code returned without comparing anything, so a 66 KB mismatch passed silently. It now logs the deviation and deliberately still **does not** reject: the declared `totalSize` is the CDN original while the cache holds WeChat's re-encoded copy, so a large delta is normal and is not evidence of a wrong image (the multi-candidate rule is unchanged).

### v1.2.2.4 (2026-09-18)

- **Fixed: a wrong key form could make an entire message shard unreadable.** A cached key could be stored as 48 bytes (32B key + 16B explicit salt), but decryption picks its branch by **key length** — 48 bytes takes the “plaintext header” branch and produces a file whose header is not SQLite (`file is not a database`), making that shard (a 96 MB `message_0.db` in practice) completely unreadable. Three guards now: **verify the standard form first when storing** (store a bare 32-byte key unless the DB really uses a plaintext header), **normalize on read**, and **auto-correct legacy entries when loading the cache**.
- **Fixed: “database merge failed” was raised outright while WeChat keeps writing.** The old code wrote decrypt results straight onto the cache file and raised on failure, destroying the last usable copy. Now: build a **self-consistent main-DB snapshot** as a floor (verified with `quick_check`, re-read up to 4 times) → then try merging WAL frames on a copy (fall back to the main snapshot with a warning) → all intermediate files are written to a temp path and **atomically replaced only on success**, so a failure never destroys the previous usable copy.
- **Fixed: leftover cache entries for databases that no longer exist crashed construction** (`KeyError`) — now fully tolerated.
- **Layout: added a phone-style portrait profile** (dual profiles `wide` / `portrait`), auto-selected by window aspect ratio, each calibrated and stored independently (old flat files migrate automatically). Also fixed **session lookup in portrait mode** (the name-column filter discarded every session name as an “avatar area”, so `find_session` always returned None).
- **Cleanup**: removed 10 unused imports; added debug logs to 8 silently-swallowing handlers (a probe failure must not masquerade as a normal result); `demo_send.py` no longer hardcodes another user’s path or a real wxid (default image auto-discovers RWTemp); real wxids in READMEs replaced with placeholders.
- **New `tools/selftest.py`**: read-only self-check (layout / keys / sessions / messages), run in one command.

### v1.2.2.3 (2026-09-16)

- **Fixed: constant ~50 MB/s disk read + write while the library runs.** The decrypt-cache stamp compared mtimes with exact float equality while writing them with `%f` (6 decimals) against Windows' 7-decimal mtimes — so every poll (~1s) looked like a changed database and re-decrypted everything (WAL merge + cache rewrite included). Now `STAMP_VERSION 3` with `%r` (exact round-trip): one rebuild after upgrading, then stable.
- **Message reads now LIMIT inside each shard before merging** (**5.5×** on a 48k-message group: 1.053s → 0.191s; `get_new_messages` ≈6×). Huge chats no longer materialize every shard's rows in Python. Public APIs (`get_messages`, `get_new_messages`, `get_message_row(..., local_type=)`, `get_message_rows_for_media`) keep identical signatures **and** results (verified across 6 chats × 71 cases).
- **Fixed “cannot get keys” under UTF-8 mode**: four `tasklist` calls decoded GBK output with the default codec; under `python -X utf8` / `PYTHONUTF8=1` the decode failed, left `stdout` as None and raised `AttributeError`, killing key extraction. All four now use `encoding="gbk", errors="replace"` with a None guard.
- **`WeChatUIA.is_running()` is now multi-criterion**: it used to be one probe wrapped in `except → False`, so any error silently became “WeChat is not running”. It now checks tasklist / main-window title / psutil, and only writes an explicit stderr note when every probe *errors*.
- **Real contact/group names removed from demos and docs** (replaced with 「文件传输助手」; 「兔仔仔」/「送你挖银子」 kept as sample defaults).

### v1.2.2.2 (2026-09-13)

- **Key handling hardened: no more recurring failure after every WeChat update.** Three layers:
  - **The cache can no longer be wiped**: `_save_keys()` never persists an empty result (atomic write + `.bak` kept). Previously a transient extraction failure (wrong account / permission) **overwrote a good cache with an empty file**, so every later start reported "0 keys" — that is exactly the `keys cached: 0` seen in the field.
  - **Durable key copy**: a copy is kept at `%LOCALAPPDATA%\wechatauto_keys\<account>.json` (override the directory with the `WECHATAUTO_KEYS_DIR` env var, e.g. your project workspace), surviving TEMP cleanup and WeChat updates. On startup the caches are **merged from several locations** (durable copy → work cache → `.bak` → other accounts' caches) and every entry is verified against page-1 HMAC, keeping only working keys.
  - **Account selection is now decided by key verification**, not by "most recently modified .db" (a WeChat update rewrites every .db, shifting mtimes and picking the wrong account → 0 keys). One memory scan now collects candidate key material and scores **every account directory** by page-1 HMAC, switching to the one that unlocks (log: `已按密钥校验选定账号目录: …`).
- **cfg master-key warning**: on WeChat 4.1.13+ the cfg path returns an **untrustworthy master key** (demoted to a fallback since v1.1.9); it now logs an explicit warning when it cannot reproduce any database key instead of silently succeeding.
- **Better diagnostics (`diagnose_keys`)**: now prints the WeChat client **FileVersion**, per-account "cache / derived" availability and a **master-key consistency check** (which tells you which account the keys belong to); the `_open` error text now lists the three classic causes (32-bit Python / permission mismatch / wrong account among several) plus the `account=` hint.

### v1.2.2.1 (2026-09-12)

- **Compatibility with the new WeChat UI (verified on 4.1.13.65)**: the new build changed `AutomationId` from short names into **dotted paths** (old `session_list` / `chat_input_field` → new `MainView.main_tabbar`, `MainView….main_window_sub_splitter_view…`), which broke exact-equality matching. AutomationIds are now matched as exact / dotted-segment / suffix (`_aid_hit()`), so both the old short names and the new paths resolve.
- **Relaxed window-title matching**: the new main window title is `Weixin`, and becomes `微信(3)` when there are unread counts; `_title_is_main()` now matches by containment and still rejects unrelated titles such as `WeChat`.
- **Anchor candidate lists + structural fallbacks**: the main window / login window / search box now match against candidate tuples (single-value constants kept for backward compatibility); the search box, chat input and search-result list each gained a structural fallback (an EditControl whose Name contains 搜索, an EditControl inside the chat area, attribute-based search from the root), so a renamed class or AID in a future build no longer breaks the whole path.
- **New layout self-check `WeChatUIA.describe_layout()`**: one call returns the main class name, window title, layout kind (`merged` / `legacy` / `chat`) and the resolution result of every anchor (main_window, search_box, session_list, chat_input, main_tabbar, sns_list). Run it first when a new WeChat build changes the UI.
- Note: the Moments anchors were already dual-layout (standalone `mmui::SNSWindow` / merged `mmui::SNSContentView`); 4.1.13.65 keeps those class names, so no change was needed there.

### v1.2.2 (2026-09-12)

- **Fix cross-shard message reads (missing messages / voice)**: a conversation's `Msg_<md5>` table actually spans several `message_*.db` shards, but `get_messages` only hit the first one — e.g. a chat with 8,904 real messages (24 voice notes) reported just 1. New `_find_msg_tables()` / `_msg_conns()` / `_shard_rows()` merge reads across all shards and sort by `sort_seq`; `get_messages`, `get_new_messages` and `_find_media_rows` now use the merged view. `get_message_row` gained a `local_type` filter (a `local_id` is **not** unique across shards) and new `get_message_rows_for_media()` returns every shard row; media downloaders pass their type code so the right shard row is selected.
- **Reliable listener delivery (behavior change)**: the watermark now advances **only after callbacks succeed** (a new `_inflight` boundary prevents re-dispatching unconfirmed messages), callbacks are retried (`max_retries`, default 3) before being logged as dropped, and the watermark is persisted to `listener_watermark.json`. Messages that arrive while your process is down are delivered on the next start instead of being skipped. Pass `watermark_file=""` to disable persistence.
- **Text restore no longer requires CJK**: pure English / digits / URLs / emoji container-format messages are decoded instead of degrading to `[文本]`.
- **`Chat.GetNewMessage()` no longer drops backlog**: batches are pulled until caught up (>200 messages) and the watermark only moves to the last message actually returned, instead of jumping to the newest DB position.
- **UIA materialization self-heal (no child controls after a WeChat restart/upgrade)**: after a WeChat restart or upgrade the Qt accessibility gate byte resets to 0 and the `mmui::` tree degrades to an empty Qt shell (`Qt51514QWindowIcon` + 2 nodes). The driver now hot-writes the gate, **verifies that `mmui::` controls actually materialized**, and retries other candidate RVAs on failure (the RVA that worked is cached per DLL identity). `_get_uia()` self-heals on a 30s throttle — no more "one failed wake and OCR forever", and no manual `refresh=True`. Fallback table gained `4.1.13.65 → 0x0AE2B0C8`.
- **Moments scroll-positioning fixes**: bounded reversals (at most one per run, then downward-only) and stall detection (the top-cell fingerprint now includes geometry — merged-layout ListItems can share the same Name, which previously looked like a stall and aborted mid-scroll); skip "scroll to top" when the DB ruler says the target is below; the stop criterion is now **"the next moment's UIA control appeared"**; direction/distance fixes (clipped pixels → wheel notches) plus a bottom margin so the "…" button is reachable.
- **Message type table**: 4.x composite `local_type` is decomposed by its low 32 bits; added `50 音视频通话` (VoIP bubble), `11000 动画表情`, `8594229559345 红包` (the library previously mislabeled it as an appmsg/file card via the low-byte mapping); empty bodies (stickers) now show `[动画表情]` instead of a blank line; `demo_group_messages` decodes zstd for every type and prints one-line summaries.
- **`demo_listen.py --all`** now auto-discovers new sessions (previously limited to the 30 most recent at startup).
- **New anti-recall listener `RecallGuard` (BETA)**: after `watch(listener)` every new message is mirrored into a local sqlite DB and attachments (image/voice/video/file) are backed up to `media/`; on a `revokemsg` it prints `[撤回] <revoker> → <original text>` and records it in `recall_events`. **Not fully field-tested — shipped as BETA.**
- **New `MomentObserver` (BETA)**: observe-and-freeze snapshots of Moments cache keys via `snapshot()` / `diff()` (cache keys have no derivable mapping to feed md5 and the cache is evictable, so observing is the only way to keep them). **Not fully field-tested — shipped as BETA.**

### v1.2.1 (2026-09-06)

- **New "quote & send" message feature (BETA)**: `WeChatGUI.quote_msg(text, who, target_text=None, verify=False)` right-clicks the target message → picks「引用」from the popup menu → types the content → sends; omitting `target_text` quotes the most recent message. `quick_quote()` is a one-liner entry point, demo script `wechatauto/demo_quote.py`.
  - **BETA disclaimer**: the feature uses a coordinate + OCR + `SendInput` pipeline that depends on WeChat 4.1.x self-drawn layout; positioning may drift with window size / DPI / chat content. The right-click uses `SendInput` injection (the render window ignores `mouse_event` right-clicks), and the cursor is first moved with `SetCursorPos` before injecting the click to avoid "moves but doesn't click / clicks but doesn't move" drift.
- **Removed the `desktop_available()` white-pixel screen check**: control targeting is fully UIA-based now, so the full-window screenshot white-ratio sampling was dropped — it could falsely report "window not visible" while WeChat was fine. `ensure_visible()` now treats a live window handle as visible and keeps its "minimize blockers + bring-to-front" actions.

### v1.2.0.1 (2026-08-31)

- **Fix WAL-merged database cache corruption causing infinite loop**: `_check_merged` previously used `SELECT count(*) FROM sqlite_master` which only checks the schema tree — corrupted data pages still passed validation, causing the cache stamp to mark the bad cache as "up-to-date" and every subsequent poll to reuse it, throwing `database disk image is malformed` on a dead loop. Now uses `PRAGMA quick_check` for full database validation (data + index pages). New `_invalidate_cache()` clears all decrypted `.db`/`.stamp` files. New `_run_msg_query()` unified entry point auto-retries once on `malformed` (clear cache → rebuild → retry). `_msg_conn` now closes shard connections immediately to avoid Windows file-lock issues during cache cleanup.

### v1.2.0 (2026-08-30)
> Note: this release merges all changes made after 1.1.10.2 that were not yet published (1.1.10.3 → 1.1.10.7).

- **Smart Moments positioning + auto like**: `Moment.find_moment(publisher, keyword, ...)` uses a hybrid of the **DB route (computing the target offset)** + **UIA route (scrolling by offset)** — it derives how many feeds the target is from the current view using the local `sns.db` ruler, then scrolls adaptively in the correct direction to land on the moment by author/keyword, eliminating blind downward scrolling and false "not found" results.
- **"…" overlay recognition**: `Moment._locate_more_click` / `_find_more_button` locate the "…" button (bottom-right of a feed) via template matching (light/dark templates shipped in `assets/`) and click it; if not found it keeps nudging the scroll and retrying to pop up the like/comment overlay.
- **One-shot Like**: `Moment.LikeMoment(publisher, keyword, ...)` does "locate → tap "…" → like in the overlay"; the "赞/Comment" buttons in the overlay are found by a global deep traversal from the UIA root (matching by name) and clicked at their center.
- **Moments like/comment via UIA controls**: `WeChat` now exposes a `Moment` property and `SwitchToMoments()` that hot-activate the `mmui` UIA tree and click the 朋友圈 nav button. `Moment.Like(item, cancel=False)` and `Moment.Comment(item, content, reply_to=None)` operate on UIA feed items — likes/comments are server-side actions, so they need the UI (the DB route stays read-only). `WeChat.Moment` is `None` when the UIA tree is unavailable. Demo `wechatauto/demo_moments_interact.py`.
- **Moments media download**: new `MomentDB.download_media(media, save_dir, kind)` copies a single picture/video from the local cache first (byte-for-byte, offline) and falls back to the CDN url; `MomentDB.download_moment_media(feed, save_dir, ...)` fetches all pictures/videos of one feed into a folder. `find_local_media(md5, kind, size)` locates the cache file by md5 and, for videos, by `totalSize` across the whole `Sns/Video` tree (the video cache name is a content-hash unrelated to the feed md5, so size matching recovers real MP4s). `parse_feed` now distinguishes pictures vs videos via `videomd5`/`videoDuration`/`type` and records each media's `size`. Demo `wechatauto/demo_moments_download.py`.
- **Moments read API (DB route)**: `MomentDB.get_moments()` now supports `since` / `until` (Unix-seconds time filter) and `keyword` (text filter), plus `limit=0` to return every row. New incremental-sync helpers `latest_tid()` / `get_moments_since()` make it easy to poll for new moments. New interaction notifier `get_interactions()` / `interactions_unread_count()` read the "likes/comments on my moments" table (`SnsMessage_tmp3`). New `comment_tree()` / `comment_reply_to()` organize a feed's comments into reply chains (built from `comment_id`/`ref_comment_id`).
- **Add group name ↔ ID lookup**: `get_groups()` now returns each group's real `name` (from `contact` table, falling back to its wxid). New `group_name_to_id(name)` (exact match first, then substring/fuzzy) and `group_id_to_name(chatroom_wxid)` let you resolve a group's wxid from its display name and vice versa — handy for combining with `get_group_members()` and `at_member()`.
- **Add group member enumeration & change watch (read-only, no UI)**: New `WeChatDB.get_groups()` / `get_group_members(chatroom_wxid)` read `chat_room` + `chatroom_member` + `contact` from `contact.db` to return each group's members (username / nick_name / remark / is_owner). New `GroupMemberWatcher` (via `get_group_member_watcher`) snapshots membership and `poll()` diffs against the baseline to report `joined` / `left` members, enabling polling-based membership-change monitoring. Useful together with the existing UI-automation `at_member()`.
- New runnable demos `wechatauto/demo_moment_find.py`, `demo_moment_more.py`, `demo_moment_like.py`; new deps `pyautogui`, `opencv-python`.

### v1.1.10.2 (2026-08-30)
- **Fix long text still showing `[文本]` on fresh installs: add required `zstandard` dependency**: WeChat 4.x stores long-text `message_content` as a zstd-compressed frame, decoded in `_friendly_content` via `import zstandard`. That import silently failed when `zstandard` was absent (it was **not** in `pyproject.toml` required deps), so long text degraded to the `[文本]` placeholder while listening worked normally. `zstandard` is now a required dependency; `_friendly_content` also gained lazy dual-package import (`zstandard`/`zstd`) via new `_get_zstd_module()` / `_zstd_decompress()` helpers.

### v1.1.10.1 (2026-08-29)
- **Fix `AttributeError: 'sqlite3.Row' object has no attribute 'get'` in message reading**: `_msg_row_to_dict` called `.get("compress_content")` on a `sqlite3.Row`, which only supports `[]` access. Messages whose content stays a placeholder (e.g. emoji/special types) hit this code path and crashed the real-time `Listener` polling loop. Now uses `[]` access with a fallback, fixing `get_messages` / `get_new_messages` / `get_message_row`.

### v1.1.10 (2026-08-27)
- **Add original image download via UI automation**: New `MediaDownloader.download_image_original()` method triggers WeChat to download original images by simulating UI clicks on image messages. This solves the limitation where group chat images only have thumbnails available.
- **Fix long text message content extraction**: Added zstd decompression support, `compress_content` fallback, and fixed newline character handling.

### v1.1.9 (2026-08-27)
- **Fix key extraction for WeChat 4.1.13+**: Prioritized `Config.Cipher` memory scan over `extract_master_key_from_cfg` for key extraction. The cfg-based extraction returns incorrect master keys on WeChat 4.1.13.12, while the Config.Cipher scan (which reads raw `enc_key` values from XOR-decoded blobs) works correctly. This fixes the "0/24 keys verified" issue reported on newer WeChat versions.

### v1.1.8 (2026-08-25)
- **Fix missing `_derive_xor_key` method in MediaDownloader**: v1.1.7 release accidentally omitted the `_derive_xor_key()` method while code paths (`_decrypt_v2`, `detect_image_key`) still referenced it, causing `AttributeError` when decrypting images. Restored the method for XOR key derivation from thumbnail `_t.dat` / `_h.dat` files.
- **Fix group-chat `sender_id` → `sender_username` resolution**: `Listener` callbacks now receive `sender_username` (wxid format) in the message dict, resolved from `message_resource.SenderName2Id` mapping. Previously, `sender_id` was a numeric ID that could not be used directly with `search_contact()`.
- **Thanks [uiharukazari0105](https://github.com/uiharukazari0105)** for reporting the missing _derive_xor_key issue in v1.1.7.

### v1.1.6.1 (2026-08-20)
- **PyPI description fix**: v1.1.6 was uploaded without the synced `README_pypi.md` (description still showed 1.1.5.1); this patch restores the full v1.1.6 changelog and bumps the version marker.

### v1.1.6 (2026-08-20)
- **Auto-diagnosis on missing key**: `数据库无可用密钥` now runs a built-in check before raising — Python bitness (32-bit can't read 64-bit Weixin memory), per-PID `OpenProcess`/`ReadProcessMemory` permission, and multi-account mismatch (all `wxid_*` dirs vs. picked account, suggesting `WeChatDB(account=...)`). No need to run `diagnose_keys` first.
- **New diagnostic tool**: `wechatauto/diagnose_keys.py` (`python -m wechatauto.diagnose_keys`, WeChat logged in) dumps lib version, Python bitness, Weixin PIDs with per-process read-permission checks, all accounts vs. picked account, cached keys, fresh in-memory extraction, and key verification — paste the output when reporting key-extraction failures.
- **Skip `migrate\unspportmsg.db`**: WeChat's reserved "unsupported message" DB has no in-memory key and is never queried; it was forcing a full process-memory scan on every init.

### v1.1.5.1 (2026-08-18) — beta
- **Fix real-time listening**: `WeChatDB.get_new_messages()` referenced an undefined `found` (NameError swallowed by `Listener._poll_once`), so **no** message callbacks ever fired — including first messages from contacts you had never chatted with.
- **Dynamic message shards**: `_message_dbs()` now re-scans the disk so shards WeChat creates at runtime (e.g. `message_5.db`) are picked up and their keys extracted automatically.

### v1.1.5 (2026-08-18)
- **Version cleanup**: normalized the patch version (1.1.4.2 → 1.1.5) after the `media_*.db` voice fix.

### v1.1.4.2 (2026-08-18)
- **PyPI description cleanup**: removed the demo default-group changelog line from the PyPI description.

### v1.1.4.1 (2026-08-18)
- **PyPI readme bilingual**: merged the Chinese (`README.zh-CN.md`) and English (`README.md`) into one PyPI description so the Chinese version is visible on the package page.

### v1.1.4 (2026-08-18)
- **Voice download across all media databases**: `download_voice()` now searches every `media_*.db` (not just `media_0.db`) — WeChat shards voice data across multiple media DBs; previously voices stored in `media_1.db` etc. could not be found (thanks uiharukazari0105).
- **`demo_media.py --images N`**: download the latest N images of a chat directly from the DB (by local_type), bypassing the total-message `--limit` — no more "only a few images listed" when a group has thousands of messages.
- **`WeChatDB._find_media_rows(user, types)`**: new helper returning all media local_ids of a chat for a set of local_types (batch download).
- **Group-chat image thumbnail fallback**: original images in group chats are only downloaded after being opened in WeChat; `download_image` now falls back to the thumbnail (`_t.dat`) when the original is missing, saving it with a `_thumb` suffix.

### v1.1.3 (2026-08-17)

### v1.1.2 (2026-08-16)
- **UIA driver thread-safety**: `WeChatUIA` now initializes COM on the current thread (`CoInitializeEx`, idempotent) — fixes crashes when instantiated from background threads / host apps (e.g. WeChatBot) with "CoInitialize not called / cannot load UIAutomationCore.dll" errors.
- **Main-window filtering**: only windows whose process loaded `Weixin.dll` are considered — auxiliary processes without the DLL (whose hot-activation always fails) no longer produce noise warnings.
- **Forward-voice fix**: `Chat.ForwardVoiceMessage` uses `self` when no target is given (the previous `_cur()` could resolve the wrong chat).
- **Re-entrant UI lock**: `LockManager` is now re-entrant per thread — `@uilock` functions calling each other (e.g. `ForwardVoiceMessage` → `VoiceMessage.forward_to`) no longer deadlock.

### v1.1.1 (2026-08-16)
- **Recall last message** (`Chat.RecallLastMessage` / `uia_driver.recall_last_message`): right-click the latest own message → UIA-first menu-item click (`mmui::XMenuView` found inside the main-window subtree), OCR fallback; fails cleanly when the 2-minute recall window has passed (menu only shows "Delete").
- UIA robustness: menu-item lookup scoped to the main-window subtree (avoids the Windows UIA root-traversal hang), removed the fragile `WindowControl(ClassName=...)` fallback.
- Media fix: video id bytes→str decoding in `MediaDownloader`.
- `demo_media.py --photos` default 3 → 10.

### v1.1.0 (2026-08-15)
- **Image AES key auto-capture** (`media.py`): the V2 image key is only resident in memory while viewing an image (~5 min). `_scan_aes_key()` gained a `monitor` mode — polls continuously and persists the key to `image_keys.json` once found; users just open one image to finish setup.
- Fixed the process-ordering scan bug (removed the memory-usage sort that pushed the main process last).
- **Forward voice messages**: SILK extraction from `media_0.db` + file-message send (`demo_forward_voice.py`).
- New demos: `demo_group_messages.py` (group + red-packet ZSTD parsing), `demo_robust.py`.

## 🤝 Acknowledgments

Thanks to [vesio](https://github.com/vesio) for sharing the WeChat 4.1.12 UIA control-tree approach and debugging ideas in [issue #1](https://github.com/fanyuantaier/wechatauto-replica/issues/1) — it made the UIA hybrid driver (v1.0.8) possible.

Thanks to [nanshanjack](https://github.com/nanshanjack) for finding the UI-lock re-entrancy problem (fixed in v1.1.2).

Thanks to [maozhitao12450](https://github.com/maozhitao12450) for reporting the WXAM (wxgf) image download issue (fixed in v1.1.3).

Thanks to [uiharukazari0105](https://github.com/uiharukazari0105) for finding that voice data stored in `media_1.db` (and later) was never searched (fixed in v1.1.4).

Thanks to [wenjiavv](https://github.com/wenjiavv) for reporting the missing `threading` import that broke layout calibration in the published 1.2.2.5 ([#28](https://github.com/fanyuantaier/wechatauto-replica/issues/28)) and the substring/no-watermark hole in send verification ([#29](https://github.com/fanyuantaier/wechatauto-replica/issues/29)), both with reproductions and fix proposals (fixed in v1.2.2.6).

Thanks to [dhz1145](https://github.com/dhz1145) for reporting [issue #32](https://github.com/fanyuantaier/wechatauto-replica/issues/32) — auto-detection depended on the working directory whenever WeChat's storage location was a drive root (`d:\`), complete with step-by-step reproductions, the root cause, a `GetFullPathNameW` cross-check and a fix proposal (fixed in v1.2.5.1).

Thanks to [WrenZephyrSol](https://github.com/WrenZephyrSol) for the follow-up on [issue #20](https://github.com/fanyuantaier/wechatauto-replica/issues/20): on the same chat `list_message_chats()` counted 2654 messages while `get_messages()` returned 763, and they pasted a line-by-line source comparison plus a workaround that avoided `get_message_row` for downloads by carrying `server_id` over themselves. The analysis was written against v1.2.1, but following it uncovered the real hole — `local_id` is not unique across shards (fixed in v1.2.6).

Thanks to [tryqylz](https://github.com/tryqylz) for reporting [issue #34](https://github.com/fanyuantaier/wechatauto-replica/issues/34) after noticing it in real use: sender names came out misaligned. Both findings held up — `_sender_id_index()` resolved `real_sender_id` through `SenderName2Id` in the resource database when the number belongs to the shard's own `Name2Id`, and `_resolve_sender()` treated id 2 as the local account unconditionally. The proposed fix (join on `Name2Id.rowid` inside the shard the row lives in, and keep an unknown state when no mapping exists rather than guessing from another database) is exactly what shipped in v1.2.6.1.

## 📄 License & Disclaimer

Apache-2.0. This project is for personal learning and automation research only — please respect the WeChat software license agreement and applicable laws.

Contact: fanyuantaier@163.com
