[**English**](README.md) | [**中文**](README.zh-CN.md)

# wechatauto-replica — WeChat 4.x Windows Automation (wxauto-compatible)

![PyPI version](https://img.shields.io/pypi/v/wechatauto-replica)
![PyPI downloads](https://img.shields.io/pypi/dw/wechatauto-replica)
![Python](https://img.shields.io/pypi/pyversions/wechatauto-replica)
![License](https://img.shields.io/github/license/fanyuantaier/wechatauto-replica)
![GitHub stars](https://img.shields.io/github/stars/fanyuantaier/wechatauto-replica)

Automate the **WeChat 4.x Windows desktop client** (not the web version): read messages, listen in real time, download media, export full history, read Moments (朋友圈), and send messages — by driving the local client directly.

> **Current version:** 2.2.0.1 · Windows 10/11 · Python 3.9+ (verified on 3.12) · WeChat **4.1.12+**
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
| Download original image (not thumbnail) | ✅ verified | `MediaDownloader.download_image_original()`: UI click triggers download |
| Moments (朋友圈) read | ✅ verified | Direct `sns.db` reads (3382 feeds verified) |
| Multi-account | ✅ verified | `list_accounts()` + `account=` |
| Send text / file / image / reply / @member | ✅ verified | UIA-first, coordinate + OCR fallback |
| Voice call / Poke (拍一拍) | ✅ verified | UIA buttons + OCR menus |
| UIAutomation tree | ✅ after hot-activation | Writes the Qt accessibility gate inside Weixin.dll |

## 🚀 Quick Start

> 📖 **Full usage guide**: [GUIDE.md](GUIDE.md) (中英对照 / bilingual)

```bash
pip install -e .
# extra deps for the OCR sending path:
pip install winsdk pypinyin
```

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
3. **Sending is a GUI operation** — fails cleanly when the desktop is locked (`desktop_available()` returns False).
4. **Videos** are downloadable only when the mp4 already exists on disk (`msg/video/`).
5. **Group-chat image originals** are stored locally only after being opened (viewed) in WeChat; until then only the thumbnail (`_t.dat`) exists — `download_image` falls back to the thumbnail (marked `_thumb` in the filename). Use `download_image_original()` to trigger WeChat to fetch the original via a UI click on the image message.
6. **Moments likes/comments** go through the UI (server-side actions) and need the hot-activated `mmui` UIA tree plus an unlocked desktop; they fail cleanly when the tree is unavailable. **Moments posting stays dropped** (4.x self-drawn UI, unreliable).

## 🗺️ Roadmap

- Calibrate and verify file/image/reply/@ sending on unlocked desktops
- Video message download (4.x storage location TBD)
- Performance: parallel export / first-scan, incremental memory-scan cache

## 📝 Changelog

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

## 📄 License & Disclaimer

Apache-2.0. This project is for personal learning and automation research only — please respect the WeChat software license agreement and applicable laws.

Contact: fanyuantaier@163.com
