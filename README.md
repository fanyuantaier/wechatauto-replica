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

> **Current version:** 1.2.3 · Windows 10/11 · Python 3.9+ (verified on 3.12) · WeChat **4.1.12+**
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
3. **Sending is a GUI operation** — fails cleanly when the window is locked/unresponsive (operations return a clear failure).
4. **Videos** are downloadable only when the mp4 already exists on disk (`msg/video/`).
5. **Group-chat image originals** are stored locally only after being opened (viewed) in WeChat; until then only the thumbnail (`_t.dat`) exists — `download_image` falls back to the thumbnail (marked `_thumb` in the filename). Use `download_image_original()` to trigger WeChat to fetch the original via a UI click on the image message.
6. **Moments likes/comments** go through the UI (server-side actions) and need the hot-activated `mmui` UIA tree plus an unlocked desktop; they fail cleanly when the tree is unavailable. **Moments posting stays dropped** (4.x self-drawn UI, unreliable).
7. **Quote-message sending (BETA)** goes through a coordinate + OCR + `SendInput` pipeline that depends on WeChat 4.1.x self-drawn layout; positioning may drift with window size / DPI / chat content — test flow on a throwaway account only.

## 🗺️ Roadmap

- Calibrate and verify file/image/reply/@ sending on unlocked desktops
- Video message download (4.x storage location TBD)
- Performance: parallel export / first-scan, incremental memory-scan cache

## 📝 Changelog

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

## 📄 License & Disclaimer

Apache-2.0. This project is for personal learning and automation research only — please respect the WeChat software license agreement and applicable laws.

Contact: fanyuantaier@163.com
