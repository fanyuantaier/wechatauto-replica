# AGENTS.md

Windows-only automation library for the **WeChat 4.x desktop client** (not web). Message APIs take **usernames** (`wxid_*` or `xxx@chatroom`), **never nicknames** — convert with `db.search_contact()` / `db.group_name_to_id()`.

## Running / verifying
- No tests, no linter, no CI. Verification means running a demo against a **logged-in WeChat** desktop client; sending/GUI paths additionally need an **unlocked, visible desktop** — they cannot be exercised in a headless environment.
- Install: `pip install -e .`; add `pypinyin` for the pinyin-IME text fallback (`winsdk`, `pyautogui`, `opencv-python`, `zstandard` are already required deps).
- Run demos from the repo root as `python -m wechatauto.demo_media [chat] [--photos N]`, `python -m wechatauto.demo_moments_download [N] --out DIR`, etc. (all live in `wechatauto/demo_*.py`).
- Key-extraction failures → run `python -m wechatauto.diagnose_keys` and paste output. Python must be **64-bit**; 32-bit Python cannot read Weixin.exe memory.

## Architecture (two independent routes — don't mix them up)
- **Read** = local SQLCipher-4 DB decryption: `db.py` (`WeChatDB`), `media.py` (`MediaDownloader`), `moment.py` `MomentDB`. DB keys live in Weixin.exe process memory and are extracted by a read-only scan, cached to `%TEMP%\wechatauto_db\<account>\keys.json`. First decrypt ≈6s, later runs are instant.
- **Send / interact** = `guia.py` (coordinate + WinRT OCR, clipboard+Ctrl+V input) and `uia_driver.py` (hot-activates the Qt accessibility gate byte inside Weixin.dll to materialize the otherwise-hidden `mmui::*` UIA tree). `wx.py` `WeChat`/`Chat` dispatch between DB and GUI.
- `wechatauto.wx.Listener` is a backwards-compat abstract stub; the real listener is `wechatauto.db.Listener` (per-chat worker threads, watermark advanced by `sort_seq`).
- Moments: `MomentDB` reads `sns.db`; **posting Moments is deliberately dropped**; like/comment go through UIA only (`WeChat.Moment`, which is `None` without a hot-activated tree).
- `sender_id == 2` means self. Message type codes are in `db.MSG_TYPE_NAMES` (1 text, 3 image, 34 voice, 43 video, 47 emoji, 49 file).

## State/cache files (the reset knob for "it worked yesterday")
- `%TEMP%\wechatauto_db\<account>\keys.json` — extracted DB keys (auto re-extracted on re-login).
- `%TEMP%\wechatauto_db\<account>\image_keys.json` — image AES key; transient in memory (only resident while an image is being viewed in WeChat), persisted once found.
- `~/.wechatauto/layout-<machine>.json` — measured OCR layout ratios; delete it (or `WeChatGUI(calibrate=True)`) to force re-calibration after layout drift.
- Delete `%TEMP%\wechatauto_db\<account>` decrypted `.db`/`.stamp` files to force a full key-scan + rebuild.

## Conventions
- All module docstrings and comments are in **Chinese** (public API in English). Keep that style when editing.
- From the changelog: don't re-add removed features (`desktop_available()` white-pixel check), keep deliberately-quirky fixes (frame-salt WAL filtering, `migrate\unspportmsg.db` skip, `zstandard` for long-text).
- `guia.py` layout uses proportional `*_RATIO` constants in `_update_layout()` — never hardcode pixel offsets; when OCR breaks after a WeChat/DPI change, fix the ratios or re-run calibration, not the coords.
- The decrypted-DB cache is `db.STAMP_VERSION`-gated: if you change WAL-merge or decryption logic, bump `STAMP_VERSION` and invalidate the cache, or stale caches cause `database disk image is malformed` infinite-loop bugs.
- Version is kept in sync in three places: `pyproject.toml` `project.version`, `wechatauto/__init__.py` `__version__`, and the README/`README_pypi.md` headers (changelog is maintained inside the READMEs).