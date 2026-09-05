import struct

import wechatauto.db as db_module
from wechatauto.db import PAGE_SZ, WeChatDB


def _page(fill: int, logical_pages: int = 0) -> bytes:
    page = bytearray([fill]) * PAGE_SZ
    if logical_pages:
        page[28:32] = struct.pack(">I", logical_pages)
    return bytes(page)


def _wal(salt: bytes, frames: list[tuple[int, int, bytes, int]]) -> bytes:
    header = bytearray(32)
    header[16:24] = salt
    data = bytes(header)
    for page_number, commit_pages, frame_salt, fill in frames:
        frame_header = struct.pack(">II", page_number, commit_pages)
        data += frame_header + frame_salt + bytes(8) + _page(fill)
    return data


def test_decrypt_file_uses_sqlite_logical_size(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "_decrypt_page", lambda _key, page, _pgno: page)
    source = tmp_path / "source.db"
    output = tmp_path / "cache" / "output.db"
    source.write_bytes(_page(1, 2) + b"".join(_page(i) for i in range(2, 6)))

    database = object.__new__(WeChatDB)
    database._decrypt_file(str(source), str(output), b"key")

    assert output.stat().st_size == 2 * PAGE_SZ


def test_merge_wal_stops_at_last_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "_decrypt_page", lambda _key, page, _pgno: page)
    salt = b"current!"
    output = tmp_path / "output.db"
    wal = tmp_path / "output.db-wal"
    output.write_bytes(_page(1, 2) + _page(2))
    wal.write_bytes(
        _wal(salt, [(3, 3, salt, 13), (4, 0, salt, 10)])
    )

    database = object.__new__(WeChatDB)
    applied = database._merge_wal(str(output), str(wal), b"key", 0)

    assert applied == 1
    assert output.stat().st_size == 3 * PAGE_SZ
    assert output.read_bytes()[2 * PAGE_SZ] == 13
    assert struct.unpack(">I", output.read_bytes()[28:32])[0] == 3
