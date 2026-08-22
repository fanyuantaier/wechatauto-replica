# -*- coding: utf-8 -*-
"""微信 4.x 媒体文件读取与下载（图片解密、语音、视频、文件）。

与 :mod:`wechatauto.db` 配合使用：``db`` 提供解密后的消息行（含 local_type、
server_id、packed_info），本模块负责把媒体内容从本地取回/解密/落地。

支持的媒体（local_type 见 :data:`wechatauto.db.MSG_TYPE_NAMES`）::

    local_type 3   图片     → 会话目录 msg/attach/<会话md5>/<YYYY-MM>/Img/<md5>.dat
    local_type 34  语音     → message/media_0.db VoiceInfo.voice_data（SILK 二进制）
    local_type 43  视频     → msg/video/<YYYY-MM>/<id>.mp4（未落地时返回 None）
    local_type 49  文件     → msg/file/<YYYY-MM>/<原文件名>，原名取自
                            message_resource.db MessageResourceDetail.packed_info

图片加密（v2 格式，本库已在本机验证）::

    结构: [6B sig 07 08 56 32 08 07][4B aes_size LE][4B xor_size LE][1B pad]
          [aes 密文(ECB, PKCS7, 对齐 16B)][raw 明文][xor 密文]

    - AES 密钥: 16 字节 ASCII（字母/数字），仅在 Weixin.exe 进程内存中。
      通过 AES-ECB 解首块密文、校验 JPEG/PNG 魔数反推出（内存正则扫描）。
    - XOR 密钥: 单字节，从同图缩略图 ``<md5>_t.dat`` 尾部 JPEG 结束标记
      ``FF D9`` 反推（``key = tail[0] ^ 0xFF``）。

用法::

    from wechatauto import WeChatDB, MediaDownloader
    db = WeChatDB()
    md = MediaDownloader(db)
    md.download_media(chat_user, msg_row["local_id"])   # 按类型自动分发
"""

from __future__ import annotations

import ctypes
import glob
import hashlib
import json
import os
import re
import struct
import tempfile
import time
from typing import List, Optional, Tuple

V1_MAGIC = b"\x07\x08\x05\x56\x02\x05"
V2_MAGIC = b"\x07\x08\x56\x32\x08\x07"
V1_HEADER_SZ = 22  # 6B sig + 16B xor key
AES16_RE = re.compile(rb"[0-9a-zA-Z]{16,32}")
DEFAULT_SAVE_PATH = os.path.join(os.path.expanduser("~"), "Documents", "wechatauto_media")


def _jpeg_like(pt: bytes) -> bool:
    return (
        (pt[:3] == b"\xff\xd8\xff")
        or pt[:4] in (b"\x89PNG", b"GIF8", b"RIFF")
        or pt[:4] == b"wxgf"  # 微信动画表情容器
    )


def aligned_aes_block_size(aes_size: int) -> int:
    return aes_size + (16 - aes_size % 16) if aes_size % 16 else aes_size + 16


class MediaDownloader:
    """微信 4.x 媒体下载器"""

    def __init__(self, db, save_dir: Optional[str] = None,
                 image_key: Optional[str] = None,
                 cfg_dword: Optional[int] = None):
        self.db = db
        self.save_dir = save_dir or DEFAULT_SAVE_PATH
        self._image_key = image_key  # 显式注入的图片 AES 密钥
        self._cfg_dword = cfg_dword  # cfg+0x40, 派生图片密钥(最佳方案)
        self._xor_key: Optional[int] = None
        self._img_key: Optional[Tuple[str, int]] = None
        self._key_probe: Optional[bytes] = None

    @staticmethod
    def derive_image_keys(cfg_dword: int, wxid: str) -> Tuple[str, int]:
        """cfgDword 派生图片密钥(微信 4.x 最佳方案, 实测 3000/3000 验证)。

        imageXorKey = cfgDword & 0xFF
        imageAesKey = MD5(str(cfgDword) + wxid)[:16]   # 前 16 位即真 AES-128 密钥
        """
        xor_key = cfg_dword & 0xFF
        aes_key = hashlib.md5(
            ("%d" % cfg_dword + wxid).encode("utf-8")).hexdigest()[:16]
        return aes_key, xor_key

    def _derive_cfg_key(self) -> Optional[Tuple[str, int]]:
        """cfgDword 派生并验证; 优先显式注入, 否则用 db.cfg_dword(自动提取)。"""
        cfg_dword = self._cfg_dword
        if cfg_dword is None:
            cfg_dword = getattr(self.db, "cfg_dword", None)
        if not cfg_dword:
            return None
        aes_key, xor_key = self.derive_image_keys(cfg_dword, self.db.wxid)
        if self._validate_key(aes_key):
            return aes_key, xor_key
        return None

    # ------------------------------------------------------------------
    # 图片密钥（内存扫描 + 缩略图反推）
    # ------------------------------------------------------------------
    def _probe_ct(self, dat_path: Optional[str] = None) -> bytes:
        """取一张 V2 图片的密文首块，作为密钥反测试样"""
        if self._key_probe is not None:
            return self._key_probe
        if dat_path is None:
            base = os.path.join(self.db.account_dir, "msg", "attach")
            hits = glob.glob(os.path.join(base, "*", "*", "Img", "*.dat"))
            if not hits:
                return b""
            dat_path = hits[0]
        with open(dat_path, "rb") as f:
            head = f.read(32)
        if head[:6] == V2_MAGIC:
            self._key_probe = head[15:31]
        else:
            self._key_probe = head[V1_HEADER_SZ: V1_HEADER_SZ + 16]
        return self._key_probe

    def _validate_key(self, aes_key: str) -> bool:
        """用真实密文首块反测密钥是否有效"""
        probe = self._probe_ct()
        if not probe:
            return False
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            dec = Cipher(algorithms.AES(aes_key.encode()), modes.ECB()).decryptor()
            return _jpeg_like(dec.update(probe) + dec.finalize())
        except Exception:
            return False

    def _key_store(self) -> str:
        return os.path.join(self.db.workdir, "image_keys.json")

    def _load_persisted_key(self) -> Optional[str]:
        try:
            with open(self._key_store(), "r", encoding="utf-8") as f:
                saved = json.load(f)
            key = saved.get(self.db.account)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if key and self._validate_key(key):
            return key
        return None

    def _persist_key(self, aes_key: str) -> None:
        try:
            with open(self._key_store(), "r", encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError):
            saved = {}
        saved[self.db.account] = aes_key
        try:
            os.makedirs(os.path.dirname(self._key_store()), exist_ok=True)
            with open(self._key_store(), "w", encoding="utf-8") as f:
                json.dump(saved, f, indent=2)
        except OSError:
            pass

    def _collect_templates(self, limit: int = 32, keep: int = 16) -> List[str]:
        """递归收集 *_t.dat 缩略图模板: 按修改时间降序取前 keep 个"""
        base = os.path.join(self.db.account_dir, "msg", "attach")
        hits = glob.glob(os.path.join(base, "*", "*", "Img", "*_t.dat"))
        hits.sort(key=os.path.getmtime, reverse=True)
        return hits[:keep]

    def _get_xor_key(self, templates: List[str]) -> Optional[int]:
        """文件尾统计推 XOR 密钥: 缩略图明文为 JPEG, 尾部固定 FF D9。

        读每个模板最后 2 字节 (x, y), 统计出现最多的组合;
        xorKey = x ^ 0xFF 且校验 y ^ 0xD9 == xorKey 才返回。
        """
        tails: Dict[Tuple[int, int], int] = {}
        for p in templates:
            try:
                with open(p, "rb") as f:
                    f.seek(-2, 2)
                    tail = f.read(2)
            except OSError:
                continue
            if len(tail) == 2:
                tails[(tail[0], tail[1])] = tails.get((tail[0], tail[1]), 0) + 1
        for (x, y), _ in sorted(tails.items(), key=lambda kv: -kv[1]):
            key = x ^ 0xFF
            if y ^ 0xD9 == key:
                return key
        return None

    def _scan_aes_key(self, monitor: bool = False,
                      monitor_timeout: float = 120.0) -> Optional[str]:
        """扫描 Weixin.exe 内存穷举候选密钥, AES-ECB 解密探针验证。

        候选两类模式(YARA 思路):
        - ASCII: 非字母数字 + 连续 32 个 [a-zA-Z0-9] + 非字母数字结尾,
          每候选取前 16 字节作 AES-128 key 解密探针, 明文为 JPEG SOI 命中;
        - UTF-16LE: 字母数字与 0x00 交错形式。
        """
        probe = self._probe_ct()
        if not probe:
            return None
        pids = self.db._find_weixin_pids()
        if not pids:
            return None
        from . import db as _dbmod
        k32 = _dbmod._k32
        MBI = _dbmod._MBI

        ASCII32_RE = re.compile(rb"[^a-zA-Z0-9]([a-zA-Z0-9]{32})[^a-zA-Z0-9]")
        U16_RE = re.compile(rb"(?:[a-zA-Z0-9]\x00){32}")

        def read_mem(h, addr: int, n: int):
            buf = ctypes.create_string_buffer(n)
            br = ctypes.c_size_t(0)
            if k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, n, ctypes.byref(br)) and br.value:
                return buf.raw[: br.value]
            return None

        def _try_key(key16: bytes) -> bool:
            try:
                from cryptography.hazmat.primitives.ciphers import (
                    Cipher, algorithms, modes,
                )
                pt = Cipher(algorithms.AES(key16), modes.ECB()).decryptor()
                out = pt.update(probe) + pt.finalize()
            except Exception:
                return False
            return out[:3] == b"\xff\xd8\xff" or _jpeg_like(out)

        def _candidates(buf: bytes):
            for m in ASCII32_RE.finditer(buf):
                yield m.group(1)[:16].encode() if isinstance(m.group(1), str) else m.group(1)[:16]
            for m in U16_RE.finditer(buf):
                yield bytes(b for i, b in enumerate(m.group()) if i % 2 == 0)[:16]

        def _scan_once() -> Optional[str]:
            # 保持微信进程原顺序扫描(主进程靠前, 命中率高)
            for pid in pids:
                h = k32.OpenProcess(0x0010 | 0x0400, False, pid)
                if not h:
                    continue
                try:
                    addr = 0
                    while True:
                        mbi = MBI()
                        r = k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
                        if r == 0:
                            break
                        if (
                            mbi.State == 0x1000
                            and (mbi.Protect & 0xFF) & 0xE6
                            and not (mbi.Protect & 0x100)
                            and 0 < mbi.RegionSize < 0x2000000
                        ):
                            buf = read_mem(h, mbi.BaseAddress or 0, mbi.RegionSize)
                            if buf:
                                for key in _candidates(buf):
                                    if _try_key(key):
                                        return key.decode("ascii", "replace")
                        addr = (mbi.BaseAddress or 0) + mbi.RegionSize
                finally:
                    k32.CloseHandle(h)
            return None

        found = _scan_once()
        if found or not monitor:
            return found

        print(
            "未在微信进程内存中找到图片 AES 密钥。\n"
            "请现在打开微信，进入任意聊天，点击一张图片查看大图，\n"
            f"本程序将在 {monitor_timeout:.0f} 秒内自动捕获密钥..."
        )
        start = time.time()
        while time.time() - start < monitor_timeout:
            time.sleep(2.0)
            found = _scan_once()
            if found:
                return found
        return None

    def detect_image_key(self, refresh: bool = False) -> Optional[Tuple[str, int]]:
        """返回 (AES 密钥, XOR 密钥)；失败返回 None。结果缓存，refresh=True 强制重扫。

        总流程:
          定位缓存目录 → 收集 *_t.dat 模板 → 文件尾推 XOR 密钥(众数统计)
          → 文件头取 AES 密文 → cfgDword 派生 / 扫描 Weixin.exe 内存穷举
          候选密钥 → AES-ECB 解密验证(JPEG SOI)。

        密钥来源优先级：cfgDword 派生(确定性离线, 免看图驻留) → 显式注入
        image_key → 本地缓存 → 进程内存扫描。命中后持久化, 下次免扫。
        """
        if self._img_key and not refresh:
            return self._img_key
        probe = self._probe_ct()
        if not probe:
            return None
        templates = self._collect_templates()
        # 1) XOR: 模板文件尾众数统计
        xor_key = self._get_xor_key(templates)
        if xor_key is None:
            dat = self._dbg_last_dat()
            xor_key = self._derive_xor_key(dat) if dat else 0x88
        # 2) AES: cfgDword 派生优先
        derived = self._derive_cfg_key()
        if derived:
            self._img_key = (derived[0], xor_key)
            return self._img_key
        aes_key = None
        if self._image_key and self._validate_key(self._image_key):
            aes_key = self._image_key
        if not aes_key:
            aes_key = self._load_persisted_key()
        if not aes_key:
            aes_key = self._scan_aes_key(monitor=True)
            if aes_key:
                self._persist_key(aes_key)
        if not aes_key:
            return None
        self._img_key = (aes_key, xor_key)
        return self._img_key

    # ------------------------------------------------------------------
    # 图片解密
    # ------------------------------------------------------------------
    def decrypt_image(self, dat_path: str, aes_key: Optional[str] = None,
                      xor_key: Optional[int] = None) -> bytes:
        """解密单个 .dat 为图片字节（自动识别 v1/v2 格式）"""
        with open(dat_path, "rb") as f:
            data = f.read()
        if not data:
            raise ValueError("空文件: %s" % dat_path)
        magic = data[:6]
        if magic == V2_MAGIC:
            return self._decrypt_v2(data, dat_path, aes_key, xor_key)
        if magic == V1_MAGIC:
            if xor_key is None:
                xor_key = self._derive_xor_key(dat_path)
            key = data[6:22]
            body = data[22:]
            return bytes(b ^ (xor_key & 0xFF) for b in body)
        # 早期纯异或格式：逐字节 ^ 0xFF（无签名），按 JPEG/PNG 魔数回退判断
        for cand in (0x88, 0x30, 0xFF, 0xE9):
            out = bytes(b ^ cand for b in data)
            if out[:3] == b"\xff\xd8\xff" or out[:4] == b"\x89PNG":
                return out
        raise ValueError("无法识别的图片加密格式: %s" % dat_path)

    def _resolve_aes_key(self) -> Optional[str]:
        """统一密钥解析：显式注入 → 本地缓存 → 内存扫描"""
        if self._image_key and self._validate_key(self._image_key):
            return self._image_key
        cached = self._load_persisted_key()
        if cached:
            return cached
        key = self._scan_aes_key(monitor=True)
        if key:
            self._persist_key(key)
        return key

    def _decrypt_v2(self, data: bytes, dat_path: str,
                    aes_key: Optional[str], xor_key: Optional[int]) -> bytes:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        aes_size, xor_size = struct.unpack_from("<LL", data, 6)
        if xor_key is None:
            xor_key = self._derive_xor_key(dat_path)
        if aes_key is None:
            aes_key = self._resolve_aes_key()
            if not aes_key:
                raise RuntimeError(
                    "无法获取图片 AES 密钥：请保持微信登录，并先在微信聊天中"
                    "打开（点击查看大图）任意一张图片，再重试 detect_image_key()；"
                    "或通过 MediaDownloader(image_key='...') 手动传入密钥。"
                )
        aes_blk = aligned_aes_block_size(aes_size)
        off = 15
        aes_data = data[off: off + aes_blk]
        off += aes_blk
        raw_data = data[off: len(data) - xor_size]
        xor_data = data[len(data) - xor_size:]
        dec = Cipher(algorithms.AES(aes_key.encode()), modes.ECB()).decryptor()
        pt = dec.update(aes_data) + dec.finalize()
        pad = pt[-1] if pt else 0
        if 1 <= pad <= 16 and all(b == pad for b in pt[-pad:]):
            pt = pt[:-pad]
        return pt + raw_data + bytes(b ^ (xor_key & 0xFF) for b in xor_data)

    # ------------------------------------------------------------------
    # 定位本地文件
    # ------------------------------------------------------------------
    def _chat_md5(self, user: str) -> str:
        import hashlib
        return hashlib.md5(user.encode()).hexdigest()

    def _month_of(self, create_time: int) -> str:
        return time.strftime("%Y-%m", time.localtime(create_time))

    def _find_dat(self, user: str, md5: str, create_time: int,
                  thumbnail: bool = False) -> Optional[str]:
        base = os.path.join(self.db.account_dir, "msg", "attach", self._chat_md5(user))
        target = md5 + ("_t.dat" if thumbnail else ".dat")
        for root, _, files in os.walk(base):
            for f in files:
                if f == target:
                    return os.path.join(root, f)
        return None

    # ------------------------------------------------------------------
    # 各类媒体下载
    # ------------------------------------------------------------------
    def _out(self, save_dir: Optional[str], name: str) -> str:
        d = save_dir or self.save_dir
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, name)

    # ------------------------------------------------------------------
    # WXAM (wxgf) 解码：微信 4.x 普通图片的新存储格式，内部为 HEVC 裸流
    # ------------------------------------------------------------------
    def _extract_hevc(self, data: bytes) -> Optional[bytes]:
        """从 wxgf 容器提取 HEVC Annex-B 裸流（自首个 NALU 起始码起）。"""
        start = data.find(b"\x00\x00\x00\x01")
        return data[start:] if start >= 0 else None

    @staticmethod
    def _ffmpeg_exe() -> Optional[str]:
        import shutil
        exe = shutil.which("ffmpeg")
        if exe:
            return exe
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None

    def _wxgf_to_jpg(self, data: bytes) -> Optional[bytes]:
        """用 ffmpeg 把 wxgf 内的 HEVC 裸流转码为 jpg。失败返回 None。"""
        exe = self._ffmpeg_exe()
        if exe is None:
            return None
        hevc = self._extract_hevc(data)
        if not hevc:
            return None
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in.hevc")
            dst = os.path.join(td, "out.jpg")
            with open(src, "wb") as f:
                f.write(hevc)
            try:
                r = subprocess.run(
                    [exe, "-y", "-v", "error", "-i", src, "-frames:v", "1", dst],
                    capture_output=True, timeout=30,
                )
            except Exception:
                return None
            if r.returncode == 0:
                try:
                    with open(dst, "rb") as f:
                        out = f.read()
                    return out if out[:3] == b"\xff\xd8\xff" else None
                except OSError:
                    return None
        return None

    def _img_md5(self, row: dict) -> Optional[str]:
        pi = row.get("packed_info")
        content = row.get("content")
        for blob in (pi, content):
            if isinstance(blob, bytes):
                m = re.search(rb"([0-9a-fA-F]{32})", blob)
                if m:
                    return m.group(1).decode().lower()
        return None

    def download_image(self, user: str, local_id: int, save_dir: Optional[str] = None,
                       aes_key: Optional[str] = None, xor_key: Optional[int] = None) -> Optional[str]:
        """下载图片消息并解密为 jpg/png/gif，返回落盘路径"""
        row = self.db.get_message_row(user, local_id)
        if not row or row["local_type"] != 3:
            return None
        md5 = self._img_md5(row)
        if not md5:
            return None
        dat_path = self._find_dat(user, md5, row["create_time"])
        thumb = False
        if not dat_path:
            # 群聊图片默认只有缩略图（原图未在微信中点开查看时不下发），回退缩略图
            dat_path = self._find_dat(user, md5, row["create_time"], thumbnail=True)
            if not dat_path:
                return None
            thumb = True
        data = self.decrypt_image(dat_path, aes_key, xor_key)
        suffix = "_thumb" if thumb else ""
        if data[:3] == b"\xff\xd8\xff":
            ext = "jpg"
        elif data[:4] == b"\x89PNG":
            ext = "png"
        elif data[:3] == b"GIF":
            ext = "gif"
        elif data[:4] == b"wxgf":
            # WXAM 格式：微信 4.x 普通图片也用 HEVC 编码存储（含动画表情）。
            # 优先用 ffmpeg 转码为 jpg；不可用时把原始解密数据落盘为 .wxgf 兜底。
            jpg = self._wxgf_to_jpg(data)
            if jpg is not None:
                out = self._out(save_dir, "%s_%s%s.%s" % (user, local_id, suffix, "jpg"))
                with open(out, "wb") as f:
                    f.write(jpg)
                return out
            out = self._out(save_dir, "%s_%s%s.wxgf" % (user, local_id, suffix))
            with open(out, "wb") as f:
                f.write(data)
            return out
        else:
            ext = "img"
        out = self._out(save_dir, "%s_%s%s.%s" % (user, local_id, suffix, ext))
        with open(out, "wb") as f:
            f.write(data)
        return out

    def download_voice(self, user: str, local_id: int, save_dir: Optional[str] = None) -> Optional[str]:
        """语音：media_*.db VoiceInfo.voice_data（SILK 二进制），落盘 .silk

        微信按账号/时间把语音分片存到多个 media_*.db，逐个搜索直到找到。
        """
        row = self.db.get_message_row(user, local_id)
        if not row or row["local_type"] != 34 or not row["server_id"]:
            return None
        for rel, path, _ in self.db._db_files:
            if not os.path.basename(path).startswith("media_"):
                continue
            conn = self.db._open(rel)
            try:
                cid = conn.execute(
                    "SELECT rowid FROM Name2Id WHERE user_name=?", (user,)
                ).fetchone()
                chat_id = cid[0] if cid else None
                if chat_id is None:
                    continue
                v = conn.execute(
                    "SELECT voice_data FROM VoiceInfo WHERE chat_name_id=? AND svr_id=? "
                    "ORDER BY create_time DESC LIMIT 1",
                    (chat_id, row["server_id"]),
                ).fetchone()
            finally:
                conn.close()
            if v and v["voice_data"]:
                out = self._out(save_dir, "%s_%s.silk" % (user, local_id))
                with open(out, "wb") as f:
                    f.write(v["voice_data"])
                return out
        return None

    def download_video(self, user: str, local_id: int, save_dir: Optional[str] = None) -> Optional[str]:
        """视频：按 packed_info 中的 id 在 msg/video 下查找 <id>.mp4"""
        row = self.db.get_message_row(user, local_id)
        if not row or row["local_type"] != 43:
            return None
        pi = row.get("packed_info")
        if not isinstance(pi, bytes):
            return None
        m = re.search(rb"([0-9a-fA-F]{32})", pi)
        vid = m.group(1).decode().lower() if m else None
        base = os.path.join(self.db.account_dir, "msg", "video")
        for root, _, files in os.walk(base):
            for f in files:
                if vid and f == vid + ".mp4":
                    out = self._out(save_dir, "%s_%s.mp4" % (user, local_id))
                    with open(out, "wb") as w:
                        with open(os.path.join(root, f), "rb") as r:
                            w.write(r.read())
                    return out
        return None

    def _file_name(self, row: dict) -> Optional[str]:
        if not row["server_id"]:
            return None
        for rel, path, _ in self.db._db_files:
            if os.path.basename(path) != "message_resource.db":
                continue
            conn = self.db._open(rel)
            try:
                r = conn.execute(
                    "SELECT d.packed_info FROM MessageResourceDetail d "
                    "LEFT JOIN MessageResourceInfo i ON d.message_id=i.message_id "
                    "WHERE i.message_svr_id=? LIMIT 1",
                    (row["server_id"],),
                ).fetchone()
            finally:
                conn.close()
            if r and r["packed_info"]:
                name = r["packed_info"].decode("utf-8", "replace").strip()
                name = re.sub(r"[\r\n\x00]+", "", name)
                if "/" in name or "\\" in name:
                    name = name.split("/")[-1].split("\\")[-1]
                return name or None
            break
        return None

    def download_file(self, user: str, local_id: int, save_dir: Optional[str] = None) -> Optional[str]:
        """文件：msg/file/<YYYY-MM>/<原文件名>，原文件名来自 message_resource"""
        row = self.db.get_message_row(user, local_id)
        if not row or row["local_type"] != 49:
            return None
        name = self._file_name(row)
        if not name:
            return None
        base = os.path.join(self.db.account_dir, "msg", "file")
        for root, _, files in os.walk(base):
            for f in files:
                if f == name:
                    out = self._out(save_dir, "%s_%s_%s" % (user, local_id, name))
                    with open(out, "wb") as w:
                        with open(os.path.join(root, f), "rb") as r:
                            w.write(r.read())
                    return out
        return None

    def download_media(self, user: str, local_id: int, save_dir: Optional[str] = None) -> Optional[str]:
        """按消息类型自动分发：3 图片 / 34 语音 / 43 视频 / 49 文件"""
        row = self.db.get_message_row(user, local_id)
        if not row:
            return None
        t = row["local_type"]
        if t == 3:
            return self.download_image(user, local_id, save_dir)
        if t == 34:
            return self.download_voice(user, local_id, save_dir)
        if t == 43:
            return self.download_video(user, local_id, save_dir)
        if t == 49:
            return self.download_file(user, local_id, save_dir)
        return None
