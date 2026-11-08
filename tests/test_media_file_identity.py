"""同名附件必须按消息元数据取回正确字节；只用临时 SQLite 与真实文件。"""

import hashlib
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

from wechatauto.db import WeChatDB
from wechatauto.media import MediaDownloader


class FileIdentityTests(unittest.TestCase):
    USER = "wxid_file_identity_fixture"
    LOCAL_ID = 17
    FILE_TYPE = (6 << 32) | 49
    TITLE = "中文附件 [验收],一.txt"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "out"
        self.shards = []
        self.message_count = 0
        self.resources = self.root / "message_resource.db"
        with closing(sqlite3.connect(self.resources)) as conn, conn:
            conn.execute("CREATE TABLE MessageResourceInfo (message_id, message_svr_id)")
            conn.execute("CREATE TABLE MessageResourceDetail (message_id, packed_info)")
        self.db = object.__new__(WeChatDB)
        self.db.account_dir = str(self.root)
        self.db._db_files = [("resource", str(self.resources), 0)]
        self.db._open = lambda _rel: self.connect(self.resources)
        self.db._msg_conns = lambda user: [
            (self.connect(path), "Msg_fixture") for path in self.shards if user == self.USER
        ]
        self.downloader = MediaDownloader(self.db, save_dir=str(self.output))

    @staticmethod
    def connect(path):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def digest(data):
        return hashlib.md5(data).hexdigest()

    @classmethod
    def xml(cls, title, data=None, size=None, md5=None):
        if data is not None:
            size, md5 = len(data), cls.digest(data)
        attachment = ("<totallen>%s</totallen>" % size) if size is not None else ""
        digest = ("<md5>%s</md5>" % md5) if md5 is not None else ""
        return ("<msg><appmsg><title>%s</title><type>6</type>"
                "<appattach>%s</appattach>%s</appmsg></msg>") % (
                    escape(title), attachment, digest)

    def message(self, content="", local_type=None, seq=1, resource=None):
        shard = self.root / ("message__message_%d.db" % self.message_count)
        self.message_count += 1
        server_id = self.message_count
        with closing(sqlite3.connect(shard)) as conn, conn:
            conn.execute(
                "CREATE TABLE Msg_fixture (local_id, local_type, real_sender_id, create_time, "
                "message_content, source, packed_info_data, compress_content, server_id, sort_seq)"
            )
            conn.execute("INSERT INTO Msg_fixture VALUES (?, ?, 0, 0, ?, '', NULL, NULL, ?, ?)",
                         (self.LOCAL_ID, self.FILE_TYPE if local_type is None else local_type,
                          content, server_id, seq))
        self.shards.append(shard)
        with closing(sqlite3.connect(self.resources)) as conn, conn:
            conn.execute("INSERT INTO MessageResourceInfo VALUES (?, ?)", (server_id, server_id))
            conn.execute("INSERT INTO MessageResourceDetail VALUES (?, ?)",
                         (server_id, self.TITLE.encode() if resource is None else resource))
        return shard.name

    def cache(self, name, data, month="2026-01"):
        source = self.root / "msg" / "file" / month / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(data)
        return source

    def published_files(self):
        return [path for path in self.output.rglob("*") if path.is_file()]

    def assert_content(self, method, expected, title=None, **kwargs):
        result = method(self.USER, self.LOCAL_ID, **kwargs)
        self.assertIsNotNone(result)
        result = Path(result)
        self.assertEqual(result.read_bytes(), expected)
        self.assertEqual(self.digest(result.read_bytes()), self.digest(expected))
        self.assertEqual(result.name, "%s_%s_%s" % (
            self.USER, self.LOCAL_ID, self.TITLE if title is None else title))
        return result

    def assert_both(self, expected):
        for method in (self.downloader.download_file, self.downloader.download_media):
            with self.subTest(entry=method.__name__):
                self.assert_content(method, expected)

    def assert_rejected(self):
        for method in (self.downloader.download_file, self.downloader.download_media):
            with self.subTest(entry=method.__name__):
                self.assertIsNone(method(self.USER, self.LOCAL_ID))
        self.assertEqual(self.published_files(), [])

    def test_original_and_numbered_cache_return_the_target_content_with_literal_brackets(self):
        target = b"this is the later attachment"
        self.message(self.xml(self.TITLE, target))
        self.cache(self.TITLE, b"the older attachment has a different length")
        self.cache("中文附件 [验收],一(1).txt", target)
        self.assert_both(target)

    def test_equal_size_different_md5_never_selects_the_first_name(self):
        target, other = b"right bytes", b"wrong bytes"
        self.assertEqual(len(target), len(other))
        self.message(self.xml(self.TITLE, target))
        self.cache(self.TITLE, other)
        self.cache("中文附件 [验收],一(1).txt", target)
        self.assert_both(target)

    def test_ordinary_49_with_legacy_resource_name_still_uses_the_message_digest(self):
        target = b"ordinary type can reach the same-name selector"
        self.message(self.xml(self.TITLE, target), local_type=49)
        self.cache(self.TITLE, b"the older file with the exact original name")
        self.cache("中文附件 [验收],一(1).txt", target)
        self.assert_both(target)

    def test_cross_month_same_basename_is_selected_by_digest_not_month_or_mtime(self):
        target = b"wanted from the older month"
        self.message(self.xml(self.TITLE, target))
        self.cache(self.TITLE, b"first directory is not evidence", month="2025-12")
        correct = self.cache(self.TITLE, target, month="2026-01")
        other = self.cache(self.TITLE, b"newer month is not evidence", month="2026-11")
        os.utime(correct, (100, 100))
        os.utime(other, (2000000000, 2000000000))
        self.assert_both(target)

    def test_multiple_copies_of_the_verified_content_remain_downloadable(self):
        target = b"the same verified attachment"
        self.message(self.xml(self.TITLE, target))
        self.cache(self.TITLE, target)
        self.cache("中文附件 [验收],一(1).txt", target)
        self.cache(self.TITLE, target, month="2026-02")
        self.assert_both(target)

    def test_valid_digest_without_optional_size_is_sufficient(self):
        target = b"md5 is present but totallen is absent"
        self.message(self.xml(self.TITLE, md5=self.digest(target)))
        self.cache(self.TITLE, b"old bytes")
        self.cache("中文附件 [验收],一(1).txt", target)
        self.assert_both(target)

    def test_digest_mismatch_does_not_fall_back_to_an_exact_original_name(self):
        self.message(self.xml(self.TITLE, b"wanted"))
        self.cache(self.TITLE, b"wrong!")
        self.assert_rejected()

    def test_matching_digest_with_wrong_declared_size_is_rejected(self):
        target = b"the digest matches"
        self.message(self.xml(self.TITLE, size=len(target) + 1, md5=self.digest(target)))
        self.cache(self.TITLE, target)
        self.assert_rejected()

    def test_absent_digest_does_not_resolve_a_numbered_family_by_size(self):
        self.message(self.xml(self.TITLE, size=5))
        self.cache(self.TITLE, b"short")
        self.cache("中文附件 [验收],一(1).txt", b"a much larger alternative")
        self.assert_rejected()

    def test_absent_digest_does_not_resolve_cross_month_exact_names(self):
        self.message(self.xml(self.TITLE))
        self.cache(self.TITLE, b"January")
        self.cache(self.TITLE, b"February", month="2026-02")
        self.assert_rejected()

    def test_absent_digest_does_not_treat_a_lone_numbered_copy_as_the_original(self):
        self.message(self.xml(self.TITLE))
        self.cache("中文附件 [验收],一(1).txt", b"unverified numbered attachment")
        self.assert_rejected()

    def test_unique_exact_file_without_digest_accepts_a_matching_optional_size(self):
        target = b"the unique exact file can still be read"
        self.message(self.xml(self.TITLE, size=len(target)))
        self.cache(self.TITLE, target)
        self.assert_both(target)

    def test_unique_exact_file_without_digest_rejects_a_wrong_optional_size(self):
        target = b"size is only an exclusion check"
        self.message(self.xml(self.TITLE, size=len(target) + 1))
        self.cache(self.TITLE, target)
        self.assert_rejected()

    def test_verified_zero_byte_file_is_not_mistaken_for_missing_metadata(self):
        self.message(self.xml(self.TITLE, b""))
        self.cache(self.TITLE, b"old nonempty file")
        self.cache("中文附件 [验收],一(1).txt", b"")
        self.assert_both(b"")

    def test_numbered_copy_of_a_title_containing_parentheses_uses_its_own_family(self):
        title, target = "报告(2026).txt", b"the requested report"
        self.message(self.xml(title, target))
        self.cache(title, b"earlier report")
        self.cache("报告(2026)(1).txt", target)
        self.cache("报告2026(1).txt", b"a different filename family")
        for method in (self.downloader.download_file, self.downloader.download_media):
            with self.subTest(entry=method.__name__):
                self.assert_content(method, target, title=title)

    def test_a_legal_leading_space_in_the_message_filename_is_preserved(self):
        title, target = " 前导空格.txt", b"leading spaces belong to the real filename"
        self.message(self.xml(title, target))
        self.cache(title, target)
        for method in (self.downloader.download_file, self.downloader.download_media):
            with self.subTest(entry=method.__name__):
                self.assert_content(method, target, title=title)

    def test_unique_legacy_ordinary_file_without_metadata_keeps_working(self):
        target = b"legacy file with a unique exact cache name"
        self.message(local_type=49)
        self.cache(self.TITLE, target)
        self.assert_both(target)

    def test_invalid_or_duplicate_metadata_never_downgrades_to_legacy_name(self):
        target = b"metadata fixture"
        valid = self.xml(self.TITLE, target)
        digest = self.digest(target)
        variants = {
            "invalid-md5": valid.replace(digest, "not-a-digest"),
            "empty-md5": valid.replace(digest, ""),
            "negative-size": valid.replace("<totallen>%d</totallen>" % len(target),
                                            "<totallen>-1</totallen>"),
            "nonnumeric-size": valid.replace("<totallen>%d</totallen>" % len(target),
                                              "<totallen>invalid</totallen>"),
            "empty-size": valid.replace("<totallen>%d</totallen>" % len(target),
                                         "<totallen></totallen>"),
            "duplicate-md5": valid.replace("</md5>", "</md5><md5>%s</md5>" % digest),
            "conflicting-md5": valid.replace("</md5>", "</md5><md5>%s</md5>" % ("0" * 32)),
            "duplicate-size": valid.replace("</totallen>", "</totallen><totallen>%d</totallen>"
                                               % len(target)),
            "duplicate-title": valid.replace("</title>", "</title><title>%s</title>"
                                                % escape(self.TITLE)),
            "duplicate-type": valid.replace("</type>", "</type><type>6</type>"),
            "duplicate-attachment": valid.replace("</appattach>",
                "</appattach><appattach><totallen>%d</totallen></appattach>" % len(target)),
            "duplicate-appmsg": valid.replace("</msg>",
                "<appmsg><title>%s</title><type>6</type></appmsg></msg>" % escape(self.TITLE)),
            "empty-title": valid.replace(escape(self.TITLE), ""),
        }
        self.cache(self.TITLE, target)
        for label, xml in variants.items():
            with self.subTest(metadata=label):
                self.shards.clear()
                self.message(xml)
                self.assert_rejected()

    def test_nested_reference_metadata_does_not_override_the_file_metadata(self):
        target = b"top-level attachment"
        reference = self.xml("other.txt", b"reference is not the attachment")
        xml = self.xml(self.TITLE, target).replace("</appmsg>",
            "<refermsg>%s</refermsg></appmsg>" % reference)
        self.message(xml)
        self.cache(self.TITLE, b"old bytes")
        self.cache("中文附件 [验收],一(1).txt", target)
        self.assert_both(target)

    def test_corrupted_existing_output_is_replaced_with_current_verified_content(self):
        target = b"the source is still correct"
        self.message(self.xml(self.TITLE, target))
        self.cache(self.TITLE, target)
        path = self.assert_content(self.downloader.download_file, target)
        for method in (self.downloader.download_file, self.downloader.download_media):
            with self.subTest(entry=method.__name__):
                path.write_bytes(b"stale output must not be trusted")
                path = self.assert_content(method, target)

    def test_explicit_shards_with_same_title_keep_both_verified_results(self):
        first, second = b"first shard bytes", b"other shard bytes"
        one = self.message(self.xml(self.TITLE, first), seq=1)
        two = self.message(self.xml(self.TITLE, second), seq=2)
        self.cache(self.TITLE, first)
        self.cache("中文附件 [验收],一(1).txt", second)
        first_out = self.assert_content(self.downloader.download_file, first, shard=one)
        second_out = self.assert_content(self.downloader.download_file, second, shard=two)
        self.assertNotEqual(first_out, second_out)
        self.assertEqual(first_out.read_bytes(), first)
        self.assertEqual(second_out.read_bytes(), second)
        self.assert_content(self.downloader.download_media, second)

    def test_copy_failure_never_publishes_partial_file_or_leaves_temporary_output(self):
        target = b"complete bytes before an injected copy failure"
        self.message(self.xml(self.TITLE, target))
        self.cache(self.TITLE, target)

        def fail_after_partial_copy(_source, destination, *_args, **_kwargs):
            destination.write(b"partial")
            raise OSError("injected disk write failure")

        for method in (self.downloader.download_file, self.downloader.download_media):
            with self.subTest(entry=method.__name__), patch(
                    "shutil.copyfileobj", side_effect=fail_after_partial_copy) as copy:
                try:
                    result = method(self.USER, self.LOCAL_ID)
                except OSError as error:
                    self.assertIn("injected disk write failure", str(error))
                else:
                    self.assertIsNone(result)
                copy.assert_called_once()
                self.assertEqual(self.published_files(), [])

    def test_copy_failure_preserves_an_existing_verified_output(self):
        target = b"a complete previous export must survive"
        self.message(self.xml(self.TITLE, target))
        self.cache(self.TITLE, target)
        existing = self.assert_content(self.downloader.download_file, target)

        def fail_after_partial_copy(_source, destination, *_args, **_kwargs):
            destination.write(b"partial")
            raise OSError("injected disk write failure")

        with patch("shutil.copyfileobj", side_effect=fail_after_partial_copy):
            try:
                result = self.downloader.download_file(self.USER, self.LOCAL_ID)
            except OSError as error:
                self.assertIn("injected disk write failure", str(error))
            else:
                # 已验证的正确导出可以安全复用，无需强迫实现再复制一次。
                if result is not None:
                    self.assertEqual(Path(result).read_bytes(), target)
        self.assertEqual(existing.read_bytes(), target)
        self.assertEqual(self.published_files(), [existing])

    def test_bytes_changed_during_copy_are_rejected_before_publication(self):
        target = b"the verified source before the copy"
        self.message(self.xml(self.TITLE, target))
        self.cache(self.TITLE, target)

        def copy_changed_bytes(_source, destination, *_args, **_kwargs):
            destination.write(b"cache changed after selection")

        with patch("shutil.copyfileobj", side_effect=copy_changed_bytes) as copy:
            self.assertIsNone(self.downloader.download_file(self.USER, self.LOCAL_ID))
            copy.assert_called_once()
        self.assertEqual(self.published_files(), [])


if __name__ == "__main__":
    unittest.main()
