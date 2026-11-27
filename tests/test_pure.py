"""wechatauto 纯逻辑层最小单测（不触微信、不开窗口、不动鼠标键盘）。

运行方式（任意装有依赖的 Python，在本目录）::

    python -m unittest discover -s tests -v

覆盖对象都是「输入→输出确定」的纯函数：密钥校验、内容格式化、
AutomationId 容错匹配、点赞名单解析、节奏档位不变量。
UI 驱动、DB 解密等需要真机/真库的部分不在本文件范围。
"""

import hashlib
import hmac as hmac_mod
import os
import struct
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from wechatauto.db import (  # noqa: E402
    PAGE_SZ,
    RESERVE_SZ,
    WeChatDB,
    _protect_key_cache,
    _replace_cache_file,
    _unprotect_key_cache,
    _verify_enc_key,
)
from wechatauto.uia_driver import _aid_hit  # noqa: E402
from wechatauto import rhythm  # noqa: E402
from wechatauto.media import MediaDownloader


def _make_page1(key: bytes, salt: bytes, body_seed: int = 7) -> bytes:
    """按 _verify_enc_key 的算法合成一页「合法」的 page1。

    布局：salt(16) + 密文区(至 PAGE_SZ-RESERVE_SZ+16) + ... + 尾部 HMAC(64)。
    """
    page = bytearray(PAGE_SZ)
    page[0:16] = salt
    for i in range(16, PAGE_SZ - RESERVE_SZ + 16):
        page[i] = (i * body_seed + 3) % 256
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", key, mac_salt, 2, dklen=32)
    hm = hmac_mod.new(mac_key, bytes(page[16: PAGE_SZ - RESERVE_SZ + 16]),
                      hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    page[PAGE_SZ - 64: PAGE_SZ] = hm.digest()
    return bytes(page)


class TestVerifyEncKey(unittest.TestCase):
    KEY = bytes(range(32))
    SALT = bytes(range(16, 32))

    def setUp(self):
        self.page = _make_page1(self.KEY, self.SALT)

    def test_correct_key_and_header_salt(self):
        self.assertTrue(_verify_enc_key(self.KEY, self.page))

    def test_wrong_key_rejected(self):
        bad = bytes(range(1, 33))
        self.assertFalse(_verify_enc_key(bad, self.page))

    def test_48byte_raw_key_with_explicit_salt(self):
        self.assertTrue(_verify_enc_key(self.KEY + self.SALT, self.page))

    def test_explicit_salt_overrides_header(self):
        # 头部 salt 故意放垃圾，靠显式 salt 参数校验通过
        bad_header = bytearray(self.page)
        bad_header[0:16] = b"\x00" * 16
        self.assertTrue(_verify_enc_key(self.KEY, bytes(bad_header),
                                        salt=self.SALT))
        # 显式 salt 长度不对 ⇒ 直接拒绝
        self.assertFalse(_verify_enc_key(self.KEY, self.page, salt=b"\x00" * 8))

    def test_short_page_rejected(self):
        self.assertFalse(_verify_enc_key(self.KEY, self.page[:PAGE_SZ - 1]))


class TestProbableKey(unittest.TestCase):
    def test_good_key(self):
        self.assertTrue(WeChatDB._probable_key(bytes(range(32))))

    def test_wrong_length(self):
        self.assertFalse(WeChatDB._probable_key(bytes(range(31))))

    def test_low_entropy(self):
        self.assertFalse(WeChatDB._probable_key(b"\x01" * 32))


class TestAidHit(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(_aid_hit("session_list", ["session_list"]))

    def test_case_and_whitespace_insensitive(self):
        self.assertTrue(_aid_hit("  Session_List  ", ["SESSION_LIST"]))

    def test_dot_path_endswith(self):
        self.assertTrue(_aid_hit("MainView.main_tabbar.session_list",
                                 ["session_list"]))

    def test_candidate_is_dot_segment(self):
        # 方向：aid 为点分全路径，候选为其中一个短名段
        self.assertTrue(_aid_hit("MainView.main_tabbar.extra", ["main_tabbar"]))

    def test_no_hit(self):
        self.assertFalse(_aid_hit("session_list", ["chat_input_field", "search_list"]))

    def test_empty_inputs(self):
        self.assertFalse(_aid_hit("", ["session_list"]))
        self.assertFalse(_aid_hit("session_list", [None, ""]))


class TestFriendlyContent(unittest.TestCase):
    def test_plain_utf8(self):
        self.assertEqual(WeChatDB._friendly_content("你好".encode(), "文本"), "你好")

    def test_image_md5_extracted(self):
        # md5 提取只在 UTF-8 解码失败的二进制 blob 上进行（真实图片消息即如此）
        blob = b"\xff\xfe<msg><img md5=\"aabbccdd00112233445566778899eeff\"/>\x00"
        self.assertEqual(
            WeChatDB._friendly_content(blob, "图片"),
            "[图片 md5=aabbccdd00112233445566778899eeff]")

    def test_undecodable_falls_back_to_type(self):
        self.assertEqual(WeChatDB._friendly_content(b"\xff\xfe\x00\x01", "语音"),
                         "[语音]")


class TestSplitLikeNames(unittest.TestCase):
    def setUp(self):
        from wechatauto import moment
        self.moment = moment
        self.sep = moment._lang(moment.MOMENTS, "分隔符_点赞") or "，"

    def _names_text(self, names):
        return "赞：" + self.sep.join(names)

    def test_basic(self):
        self.assertEqual(self.moment._split_like_names(self._names_text(["张三", "李四"])),
                         ["张三", "李四"])

    def test_empty(self):
        self.assertEqual(self.moment._split_like_names(""), [])
        self.assertEqual(self.moment._split_like_names("   "), [])

    def test_drops_empty_parts(self):
        text = self.sep.join(["张三", "", "  ", "李四"])
        self.assertEqual(self.moment._split_like_names(text), ["张三", "李四"])

    def test_no_prefix_still_parses(self):
        self.assertEqual(self.moment._split_like_names(self.sep.join(["张三", "李四"])),
                         ["张三", "李四"])


class TestKeyCacheCrypto(unittest.TestCase):
    """密钥缓存 DPAPI 封装：往返、明文兼容、损坏拒绝。"""

    def test_round_trip(self):
        payload = b'{"chat_a.db": "aabb", "chat_b.db": "ccdd"}'
        blob = _protect_key_cache(payload)
        self.assertTrue(blob.startswith(b"dpapi1:"))
        self.assertNotEqual(blob, payload)
        self.assertEqual(_unprotect_key_cache(blob), payload)

    def _fixture(self, folder):
        db = object.__new__(WeChatDB)
        db.account = "fixture"
        db.workdir = folder
        db.keys_file = str(Path(folder) / "keys.json")
        db._keys = {"example.db": b"test data"}
        db._stable_key_file = lambda: str(Path(folder) / "stable.json")
        media = object.__new__(MediaDownloader)
        media.db = db
        return db, media

    def test_failure_preserves_existing_files(self):
        with TemporaryDirectory() as folder:
            db, media = self._fixture(folder)
            db._save_keys()
            db._save_keys()
            media._persist_key("fixture-image-key")

            def snapshot():
                return {p.name: p.read_bytes() for p in Path(folder).iterdir()}

            before = snapshot()
            with patch("win32crypt.CryptProtectData", side_effect=OSError("fixture")):
                with self.assertRaisesRegex(RuntimeError, "DPAPI"):
                    db._save_keys()
                with self.assertRaisesRegex(RuntimeError, "DPAPI"):
                    media._persist_key("replacement")
            self.assertEqual(snapshot(), before)

    def test_failed_replace_cleans_up_and_keeps_previous_cache(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "stable.json"
            old = _protect_key_cache(b"old fixture")
            path.write_bytes(old)
            with patch("os.replace", side_effect=PermissionError("fixture")):
                with self.assertRaises(PermissionError):
                    _replace_cache_file(str(path), _protect_key_cache(b"new fixture"))
            self.assertEqual(list(Path(folder).iterdir()), [path])
            self.assertEqual(path.read_bytes(), old)

    def test_missing_dpapi_writes_nothing(self):
        with TemporaryDirectory() as folder, patch.dict(sys.modules, {"win32crypt": None}):
            db, media = self._fixture(folder)
            for save in (db._save_keys, lambda: media._persist_key("fixture")):
                with self.assertRaisesRegex(RuntimeError, "DPAPI"):
                    save()
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_migrate_legacy_backup_and_image_store(self):
        for source in ("keys.json", "keys.json.bak"):
            with self.subTest(source=source), TemporaryDirectory() as folder:
                db, media = self._fixture(folder)
                old = b'{"previous.db":"aabb"}'
                (Path(folder) / source).write_bytes(old)
                Path(media._key_store()).write_bytes(b'{"other":"previous image key"}')
                db._save_keys()
                media._persist_key("fixture-image-key")
                for p in Path(folder).iterdir():
                    self.assertTrue(p.read_bytes().startswith(b"dpapi1:"))
                backup = (Path(folder) / "keys.json.bak").read_bytes()
                self.assertEqual(_unprotect_key_cache(backup), old)
                self.assertEqual(db._load_key_cache(db._stable_key_file()), db._keys)
                self.assertEqual(media._read_key_store(),
                                 {"other": "previous image key", "fixture": "fixture-image-key"})

    def test_plaintext_passthrough(self):
        old = b'{"chat.db": "eeff"}'
        self.assertEqual(_unprotect_key_cache(old), old)

    def test_corrupt_prefixed_rejected(self):
        self.assertEqual(_unprotect_key_cache(b"dpapi1:not-base64!!"), b"")
        self.assertEqual(_unprotect_key_cache(b"dpapi1:AAAA"), b"")


class TestRhythmInvariants(unittest.TestCase):
    """节奏层「只加长不缩短」的不变量（rhythm.py 注释承诺）。"""

    def test_every_profile_nap_floor_ge_1(self):
        for name, prof in rhythm.PROFILES.items():
            lo, _hi = prof.nap
            self.assertGreaterEqual(lo, 1.0, f"档位 {name} 的 nap 下限 < 1.0")

    def test_off_profile_is_identity(self):
        old = rhythm.profile().name
        try:
            rhythm.set_profile("off")
            self.assertEqual(rhythm.spread(2.0, 5.0), 2.0)
            self.assertEqual(rhythm.nap(0.0), 0.0)  # base=0 不触发 sleep
        finally:
            rhythm.set_profile(old)

    def test_type_gap_pauses_then_returns(self):
        old = rhythm.profile().name
        try:
            rhythm.set_profile("off")
            self.assertEqual(rhythm.type_gap(), 0.04)  # off 档 = 原始固定值
        finally:
            rhythm.set_profile(old)


if __name__ == "__main__":
    unittest.main(verbosity=2)
