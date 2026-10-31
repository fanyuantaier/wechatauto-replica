"""图片密钥必须由适用的 V2 密文验证；测试不连接微信。"""

import struct
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from wechatauto.media import MediaDownloader, V1_MAGIC, V2_MAGIC


def encrypted_image(content, key, xor_key):
    """构造包含 AES 首段、明文中段和 XOR 尾段的合成 V2 文件。"""
    first, middle, tail = content[:32], content[32:-8], content[-8:]
    pad = 16 - len(first) % 16
    encryptor = Cipher(algorithms.AES(key.encode()), modes.ECB()).encryptor()
    cipher = encryptor.update(first + bytes([pad]) * pad) + encryptor.finalize()
    return (V2_MAGIC + struct.pack("<LL", len(first), len(tail)) + b"\x00"
            + cipher + middle + bytes(value ^ xor_key for value in tail))


class ImageKeyProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = SimpleNamespace(account_dir=str(self.root), workdir=str(self.root / "work"),
                                  account="test_account", wxid="test_account", cfg_dword=0x12345678)
        self.downloader = MediaDownloader(self.db)
        self.key, self.xor = MediaDownloader.derive_image_keys(self.db.cfg_dword, self.db.wxid)
        self.plain = b"\x89PNG\r\n\x1a\n" + bytes(range(80)) + b"IEND\xaeB`\x82"

    def write(self, name, data):
        file = self.root / name
        file.write_bytes(data)
        return file

    def target(self, name="target.dat", key=None, xor=None):
        return self.write(name, encrypted_image(self.plain, key or self.key, self.xor if xor is None else xor))

    def test_default_probe_skips_other_formats_short_files_and_missing_files(self):
        legacy = self.write("old.dat", V1_MAGIC + b"x" * 64)
        unknown = self.write("unknown.dat", b"unsupported" * 8)
        short = self.write("partial.dat", V2_MAGIC + b"x" * 12)
        target = self.target()
        files = [str(legacy), str(unknown), str(short), str(self.root / "gone.dat"), str(target)]
        with patch("wechatauto.media.glob.glob", return_value=files):
            self.assertEqual(self.downloader._probe_ct(), target.read_bytes()[15:31])

    def test_explicit_probe_replaces_cached_sample(self):
        first = self.target("first.dat")
        second = self.target("second.dat", key="0123456789abcdef")
        self.assertEqual(self.downloader._probe_ct(str(first)), first.read_bytes()[15:31])
        self.assertEqual(self.downloader._probe_ct(str(second)), second.read_bytes()[15:31])

    def test_no_usable_v2_probe_returns_empty(self):
        unknown = self.write("unknown.dat", b"unsupported" * 8)
        with patch("wechatauto.media.glob.glob", return_value=[str(unknown)]):
            self.assertEqual(self.downloader._probe_ct(), b"")

    def test_download_validates_current_image_instead_of_an_unrelated_cache_file(self):
        unrelated = self.write("old.dat", b"unsupported" * 8)
        target = self.target()
        with patch("wechatauto.media.glob.glob", return_value=[str(unrelated)]), \
                patch.object(self.downloader, "_scan_aes_key", side_effect=AssertionError("不应扫描已有正确密钥")):
            self.assertEqual(self.downloader.decrypt_image(str(target)), self.plain)

    def test_stale_cached_probe_does_not_reject_current_account_key(self):
        other = self.target("other.dat", key="0123456789abcdef")
        self.downloader._probe_ct(str(other))
        target = self.target()
        with patch.object(self.downloader, "_scan_aes_key", side_effect=AssertionError("不应受旧探针影响")):
            self.assertEqual(self.downloader.decrypt_image(str(target)), self.plain)

    def test_truncated_target_is_rejected_before_key_scanning(self):
        target = self.write("partial.dat", V2_MAGIC + struct.pack("<LL", 32, 0) + b"\x00")
        with patch.object(self.downloader, "_scan_aes_key") as scan:
            with self.assertRaisesRegex(ValueError, "密文首块不完整"):
                self.downloader.decrypt_image(str(target))
        scan.assert_not_called()

    def test_supplied_target_probe_does_not_overwrite_another_calls_cached_probe(self):
        target = self.target()
        stale = self.target("stale.dat", key="0123456789abcdef").read_bytes()[15:31]
        self.downloader._key_probe = stale
        with patch.object(self.downloader, "_scan_aes_key", side_effect=AssertionError("不应使用共享旧探针")):
            self.assertEqual(self.downloader.decrypt_image(str(target)), self.plain)
        self.assertEqual(self.downloader._key_probe, stale)

    def test_constructor_key_is_validated_against_the_current_target(self):
        self.db.cfg_dword = None
        downloader = MediaDownloader(self.db, image_key=self.key)
        downloader._xor_key = self.xor
        downloader._key_probe = b"unrelated-probe!"
        with patch.object(downloader, "_scan_aes_key", side_effect=AssertionError("显式密钥不应被旧探针否定")):
            self.assertEqual(downloader.decrypt_image(str(self.target())), self.plain)

    def test_persisted_key_is_validated_against_the_current_target(self):
        self.db.cfg_dword = None
        self.downloader._xor_key = self.xor
        self.downloader._key_probe = b"unrelated-probe!"
        folder = Path(self.db.workdir)
        folder.mkdir()
        (folder / "image_keys.json").write_text(json.dumps({self.db.account: self.key}), encoding="utf-8")
        with patch.object(self.downloader, "_scan_aes_key", side_effect=AssertionError("缓存密钥不应被旧探针否定")):
            self.assertEqual(self.downloader.decrypt_image(str(self.target())), self.plain)

    def test_parallel_targets_pass_their_own_probes_to_the_scanner(self):
        self.db.cfg_dword = None
        self.downloader._xor_key = self.xor
        first = self.target("first.dat")
        second_key = "0123456789abcdef"
        second = self.target("second.dat", key=second_key)
        keys = {first.read_bytes()[15:31]: self.key, second.read_bytes()[15:31]: second_key}
        barrier = threading.Barrier(2)

        def scan(*, monitor, probe):
            self.assertTrue(monitor)
            barrier.wait(timeout=5)
            return keys[probe]

        with patch.object(self.downloader, "_scan_aes_key", side_effect=scan), \
                patch.object(self.downloader, "_persist_key"), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda file: self.downloader.decrypt_image(str(file)), [first, second]))
        self.assertEqual(results, [self.plain, self.plain])


if __name__ == "__main__":
    unittest.main()
