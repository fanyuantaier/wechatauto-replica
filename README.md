[**English**](README.md) | [**中文**](README.zh-CN.md)

> [!NOTE]
> **📢 维护状态 / Maintenance Notice**
> 本人因今年升高一，明天（8月23日）报到。开学后几乎没有时间继续更新本项目（如果有时间，争取周日更新）。遇到问题请自行在 Issues 区讨论，或询问 AI 协助解决。感谢支持！
>
> I'm starting senior high school and will register tomorrow (Aug 23). After school starts I'll have almost no time to keep updating (Sundays if possible). Please discuss issues in the Issues section or ask an AI. Thanks for your support!


# wechatauto-replica — WeChat 4.x Windows Automation (wxauto-compatible)

![PyPI version](https://img.shields.io/pypi/v/wechatauto-replica)
![PyPI downloads](https://img.shields.io/pypi/dw/wechatauto-replica)
![Python](https://img.shields.io/pypi/pyversions/wechatauto-replica)
![License](https://img.shields.io/github/license/fanyuantaier/wechatauto-replica)
![GitHub stars](https://img.shields.io/github/stars/fanyuantaier/wechatauto-replica)

Automate the **WeChat 4.x Windows desktop client** (not the web version): read messages, listen in real time, download media, export full history, read Moments (朋友圈), and send messages — by driving the local client directly.

> **Current version:** 1.1.7 · Windows 10/11 · Python 3.9+ (verified on 3.12) · WeChat **4.1.12+**
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
```

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
| Moments | read | read (posting dropped: self-drawn UI) |

## ⚠️ Known Limitations

1. **WeChat must be logged in** — DB keys live in process memory; cached after first extraction, re-extracted automatically after re-login.
2. **Image AES key is transient** — only resident while viewing an image; persisted to `image_keys.json` once found, or inject via `image_key=`.
3. **Sending is a GUI operation** — fails cleanly when the desktop is locked (`desktop_available()` returns False).
4. **Videos** are downloadable only when the mp4 already exists on disk (`msg/video/`).
5. **Group-chat image originals** are stored locally only after being opened (viewed) in WeChat; until then only the thumbnail (`_t.dat`) exists — `download_image` falls back to the thumbnail (marked `_thumb` in the filename).
6. **Moments posting is dropped** (4.x self-drawn UI, unreliable); reading/likes/comments are supported.

## 🗺️ Roadmap

- Calibrate and verify file/image/reply/@ sending on unlocked desktops
- Video message download (4.x storage location TBD)
- Performance: parallel export / first-scan, incremental memory-scan cache

## 📝 Changelog

### v1.1.7 (2026-08-22)
- **Master-key based key extraction (PR #10, thanks [NothingFumo](https://github.com/NothingFumo))**: instead of scanning process memory for per-DB `Config.Cipher` literals (which fails on WeChat 4.1.12.26+), we now extract the **single master key** from the `cfg` structure (`cfg+0x2B8` cipher XORed with 4×movabs constants from the DLL) and **derive each DB key offline** via `PBKDF2-HMAC-SHA512(master_key, db_salt, 256000)` — 27/27 SQLCipher4 DBs verified. This fixes key extraction on 4.1.12.26+ (issues #3 / #7).
- **Unified image-key pipeline**: template collection (`*_t.dat`, top 16 by mtime) → tail-byte majority XOR (replaces the old single-file probe that could wrongly fall back to `0x88`) → `cfgDword` derivation (deterministic, offline) preferred, with injected/cached/memory-scan AES fallbacks. Probe-verified on 3000/3000 real ciphertexts.
- **Account fields from cfg**: `WeChatDB` now also returns `name` / `number` / `phone` alongside the master key, matching the output format of mainstream key tools.
- New optional params `master_key` / `cfg_dword` are fully backward compatible — if not passed, the original path is used. Core decryption functions unchanged.

### v1.1.6.3 (2026-08-21)
- **Fix import hang**: `import wechatauto` no longer blocks permanently on systems where `uiautomation` / COM initialization hangs (e.g. WeChat or other Qt apps occupying COM). The `uiautomation` and `comtypes` imports are now deferred — loaded lazily on first UIA access, not at `import wechatauto` time.
- **Fix OCR hang**: `ScreenOCR.recognize` now wraps the WinRT async call in `asyncio.wait_for(..., 8s)` — a hung `Windows.Media.Ocr` async (e.g. Chinese-locale systems) previously blocked `quick_send` forever; it now times out and degrades to empty OCR results.
- **Fix first-run calibrate_layout hang**: `calibrate_layout` now runs each OCR detection step in a daemon thread with a 5-second timeout. Previously, if WinRT OCR hung on a first-run (no layout config), the entire `WeChatGUI.__init__` would block forever; now it times out and falls back to default layout ratios.
- **Support WeChat builds with plaintext-header (key+salt) DBs**: key extraction now accepts SQLCipher 4 "Raw Key with Explicit Salt" form (`x'<96hex>'` = 32B key + 16B explicit salt, used with `cipher_plaintext_header_size`). A 48-byte key (32B key + 16B salt) is verified and decrypted in plaintext-header layout (page-1 keeps its plaintext header); a 32-byte key keeps the standard file-header-salt path. This unblocks DB decryption on builds where the old offsets point at the class-name table instead of the Cipher instance (e.g. a 4.1.12.26 environment).
- **Fix `wxid_*` hardcoding**: account discovery no longer assumes directories start with `wxid_` — any subdirectory of `db_dir` containing `db_storage/` is recognized. This supports custom WeChat IDs (e.g. user-chosen usernames that don't use the `wxid_` prefix).
- **Listen to all messages**: `WeChat.AddListenAll(callback)` now monitors ALL sessions (friends, groups, file transfer, etc.) with a single call, including auto-discovery of new sessions. `WeChat.RemoveListenAll()` stops it. The callback receives `(Message, Chat)` where `Chat.who` is the session username.

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
Thanks to [NothingFumo](https://github.com/NothingFumo) for the master-key extraction design (cfg + PBKDF2-derived per-DB keys, v1.1.7).

## 📄 License & Disclaimer

Apache-2.0. This project is for personal learning and automation research only — please respect the WeChat software license agreement and applicable laws.

Contact: fanyuantaier@163.com
