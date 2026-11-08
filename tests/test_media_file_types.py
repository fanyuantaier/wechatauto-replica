"""文件下载兼容普通和复合类型；用临时消息库与真实文件，不连接微信。"""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

import zstandard

from wechatauto.db import WeChatDB
from wechatauto.media import MediaDownloader
from wechatauto.recall import RecallGuard


class FileDownloadTypeTests(unittest.TestCase):
    USER = "wxid_file_fixture"
    LOCAL_ID = 7
    FILE_TYPE = (6 << 32) | 49

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.shards = []
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
        self.downloader = MediaDownloader(self.db, save_dir=str(self.root / "out"))

    @staticmethod
    def connect(path):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_message(self, local_type, name, seq=1, content="", compress_content=None,
                    resource=None):
        shard = self.root / ("message__message_%d.db" % len(self.shards))
        server_id = len(self.shards) + 1
        with closing(sqlite3.connect(shard)) as conn, conn:
            conn.execute(
                "CREATE TABLE Msg_fixture (local_id, local_type, real_sender_id, create_time, "
                "message_content, source, packed_info_data, compress_content, server_id, sort_seq)"
            )
            conn.execute("INSERT INTO Msg_fixture VALUES (?, ?, 0, 0, ?, '', NULL, ?, ?, ?)",
                         (self.LOCAL_ID, local_type, content, compress_content, server_id, seq))
        self.shards.append(shard)
        with closing(sqlite3.connect(self.resources)) as conn, conn:
            conn.execute("INSERT INTO MessageResourceInfo VALUES (?, ?)", (server_id, server_id))
            conn.execute("INSERT INTO MessageResourceDetail VALUES (?, ?)",
                         (server_id, name.encode() if resource is None else resource))
        source = self.root / "msg" / "file" / "2026-01" / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(("fixture:" + name).encode())
        return shard.name, source

    def assert_download(self, method, source, **kwargs):
        path = method(self.USER, self.LOCAL_ID, **kwargs)
        self.assertIsNotNone(path)
        self.assertEqual(Path(path).read_bytes(), source.read_bytes())
        self.assertEqual(Path(path).name, "%s_%s_%s" % (self.USER, self.LOCAL_ID, source.name))

    @staticmethod
    def file_xml(name, subtype="6"):
        return "<msg><appmsg><title>%s</title><type>%s</type></appmsg></msg>" % (
            escape(name), subtype)

    @staticmethod
    def nested_resource(name):
        # 脱敏后的实测结构：外层 field 1 内嵌两份字符串，不是纯文件名。
        raw = name.encode()
        inner = b"\x0a" + bytes([len(raw)]) + raw + b"\x12" + bytes([len(raw)]) + raw
        return b"\x0a" + bytes([len(inner)]) + inner

    def test_nested_resource_uses_compressed_message_title(self):
        name = "compressed.txt"
        content = zstandard.ZstdCompressor().compress(self.file_xml(name).encode())
        shard, source = self.add_message(self.FILE_TYPE, name, content=content,
                                          resource=self.nested_resource(name))
        row = self.db.get_message_row(self.USER, self.LOCAL_ID, shard=shard)
        self.assertEqual(self.downloader._file_name(row), name)
        self.assert_download(self.downloader.download_file, source, shard=shard)
        self.assert_download(self.downloader.download_media, source)

    def test_plain_file_body_works_without_resource_record(self):
        for number, binary in enumerate((False, True), start=1):
            with self.subTest(binary=binary):
                name = "plain-%s.txt" % number
                content = self.file_xml(name)
                if binary:
                    content = content.encode()
                shard, source = self.add_message(49, name, seq=number,
                                                  content=content, resource=b"")
                self.assert_download(self.downloader.download_file, source, shard=shard)

    def test_compress_content_is_tried_when_main_content_is_unusable(self):
        name = "alternate.txt"
        compressed = zstandard.ZstdCompressor().compress(self.file_xml(name).encode())
        shard, source = self.add_message(self.FILE_TYPE, name, content=b"\xff\xfe",
                                          compress_content=compressed,
                                          resource=self.nested_resource(name))
        self.assert_download(self.downloader.download_file, source, shard=shard)

    def test_group_sender_prefix_and_xml_entities_preserve_the_exact_filename(self):
        name = "中文 & 表情🐟.txt"
        content = "wxid_fixture_sender:\n" + self.file_xml(name)
        shard, source = self.add_message(self.FILE_TYPE, name, content=content,
                                          resource=self.nested_resource(name))
        self.assert_download(self.downloader.download_file, source, shard=shard)

    def test_file_title_takes_precedence_over_unrelated_resource_text(self):
        name = "body-title.txt"
        shard, source = self.add_message(self.FILE_TYPE, name, content=self.file_xml(name),
                                          resource=b"unrelated.txt")
        self.assert_download(self.downloader.download_file, source, shard=shard)

    def test_file_title_does_not_require_resource_database_or_server_id(self):
        name = "body-only.txt"
        shard, source = self.add_message(self.FILE_TYPE, name, content=self.file_xml(name))
        with closing(sqlite3.connect(self.shards[-1])) as conn, conn:
            conn.execute("UPDATE Msg_fixture SET server_id=0")
        self.db._db_files = []
        self.assert_download(self.downloader.download_file, source, shard=shard)

    def test_missing_or_nonfile_title_does_not_decode_binary_resource_as_name(self):
        name = "not-a-plain-resource.txt"
        packed = self.nested_resource(name)
        for number, content in enumerate(("", "<msg><appmsg>",
                                           self.file_xml(name, subtype="5"),
                                           "<msg><refermsg>" + self.file_xml(name)
                                           + "</refermsg></msg>"), start=1):
            with self.subTest(content=content):
                shard, _source = self.add_message(self.FILE_TYPE, name, seq=number,
                                                   content=content, resource=packed)
                row = self.db.get_message_row(self.USER, self.LOCAL_ID, shard=shard)
                self.assertIsNone(self.downloader._file_name(row))
                self.assertIsNone(self.downloader.download_file(self.USER, self.LOCAL_ID,
                                                                 shard=shard))

    def test_legacy_resource_filename_still_works_after_bad_message_xml(self):
        name = "legacy.txt"
        shard, source = self.add_message(49, name, content="<msg><appmsg>",
                                          resource=b"C:\\old\\legacy.txt\r\n\x00")
        self.assert_download(self.downloader.download_file, source, shard=shard)

    def test_ordinary_file_remains_downloadable(self):
        _shard, source = self.add_message(49, "ordinary.txt")
        self.assert_download(self.downloader.download_file, source)

    def test_composite_file_downloads_through_both_public_entry_points(self):
        shard, source = self.add_message(self.FILE_TYPE, "composite.txt")
        self.assert_download(self.downloader.download_file, source, shard=shard)
        self.assert_download(self.downloader.download_media, source)

    def test_same_local_id_newer_nonfile_does_not_hide_the_file(self):
        _shard, source = self.add_message(self.FILE_TYPE, "target.txt")
        self.add_message(1, "other.txt", seq=2)
        self.add_message((5 << 32) | 49, "link.txt", seq=3)
        self.assert_download(self.downloader.download_file, source)
        self.assert_download(self.downloader.download_media, source)

    def test_explicit_shard_selects_its_file_and_never_falls_back(self):
        shard, first = self.add_message(49, "first.txt")
        _other, second = self.add_message(self.FILE_TYPE, "second.txt", seq=2)
        self.assert_download(self.downloader.download_file, first, shard=shard)
        self.assert_download(self.downloader.download_file, second)
        self.assert_download(self.downloader.download_media, second)
        self.assertIsNone(self.downloader.download_file(self.USER, self.LOCAL_ID, shard="missing.db"))

    def test_newer_ordinary_file_is_not_hidden_by_an_older_composite_file(self):
        self.add_message(self.FILE_TYPE, "older-composite.txt")
        _shard, latest = self.add_message(49, "latest-ordinary.txt", seq=2)
        self.assert_download(self.downloader.download_file, latest)
        self.assert_download(self.downloader.download_media, latest)

    def test_equal_sequence_mixed_types_keep_the_database_stable_shard_order(self):
        _shard, first = self.add_message(self.FILE_TYPE, "first.txt")
        self.add_message(49, "second.txt")
        self.assert_download(self.downloader.download_file, first)
        self.assert_download(self.downloader.download_media, first)

    def test_explicit_nonfile_shard_does_not_select_a_file_from_another_shard(self):
        self.add_message(self.FILE_TYPE, "file.txt")
        shard, _source = self.add_message((57 << 32) | 49, "reply.txt", seq=2)
        self.assertIsNone(self.downloader.download_file(self.USER, self.LOCAL_ID, shard=shard))

    def test_same_type_cross_shard_collision_keeps_database_warning_and_latest_selection(self):
        self.add_message(49, "older.txt")
        _shard, latest = self.add_message(49, "latest.txt", seq=2)
        with patch("wechatauto.db.wxlog.warning") as warning:
            self.assert_download(self.downloader.download_file, latest)
        warning.assert_called_once()
        self.assertIn("shard", warning.call_args.args[0])

    def test_mixed_file_types_warn_once_even_when_the_selected_type_also_collides(self):
        self.add_message(49, "older-ordinary.txt")
        self.add_message(self.FILE_TYPE, "middle-composite.txt", seq=2)
        _shard, latest = self.add_message(49, "latest-ordinary.txt", seq=3)
        with patch("wechatauto.media.wxlog.warning") as warning:
            self.assert_download(self.downloader.download_file, latest)
        warning.assert_called_once()
        self.assertIn("shard", warning.call_args.args[0])

    def test_mixed_file_types_warn_once_when_each_exact_type_is_unambiguous(self):
        self.add_message(49, "older-ordinary.txt")
        _shard, latest = self.add_message(self.FILE_TYPE, "latest-composite.txt", seq=2)
        with patch("wechatauto.media.wxlog.warning") as warning:
            self.assert_download(self.downloader.download_file, latest)
        warning.assert_called_once()
        self.assertIn("shard", warning.call_args.args[0])

    def test_other_app_message_subtypes_are_not_files(self):
        for subtype in (5, 19, 33, 57, 2001):
            self.add_message((subtype << 32) | 49, "app-%s.txt" % subtype, seq=subtype)
        with patch.object(self.downloader, "_file_name") as resolve:
            self.assertIsNone(self.downloader.download_file(self.USER, self.LOCAL_ID))
            self.assertIsNone(self.downloader.download_media(self.USER, self.LOCAL_ID))
        resolve.assert_not_called()

    def test_recall_guard_receives_composite_file_bytes_from_public_dispatch(self):
        _shard, source = self.add_message(self.FILE_TYPE, "backup.txt")
        guard = RecallGuard(self.db, mirror_dir=str(self.root / "recall"),
                            downloader=self.downloader, scan=False)
        self.addCleanup(guard._conn.close)
        guard.on_msg({"username": self.USER, "local_id": self.LOCAL_ID,
                      "type": self.db._msg_type_name(self.FILE_TYPE)}, None)
        stored = guard._conn.execute(
            "SELECT media_path FROM mirror WHERE chat=? AND local_id=?",
            (self.USER, self.LOCAL_ID),
        ).fetchone()
        self.assertIsNotNone(stored[0])
        self.assertEqual(Path(stored[0]).read_bytes(), source.read_bytes())


if __name__ == "__main__":
    unittest.main()
