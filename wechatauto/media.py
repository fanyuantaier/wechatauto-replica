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
    - XOR 密钥: 单字节，等于 cfgDword 低字节（本机 2026-09-19 实测
      295/295 个 Sns 缓存容器与聊天 .dat 一致）；拿不到 cfg 时才退回
      缩略图尾部统计反推（``key = tail[0] ^ 0xFF``）。
    - 尾部页脚: Sns 缓存有 189/295 个容器在 ``FF D9`` 之后还追加 24 字节
      页脚，所以「明文末尾 == FF D9」不能当硬判据，解密后按结束标记裁剪。

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
from wechatauto.logger import wxlog

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


# Sns 缓存容器会在图片结束标记之后再追加一段页脚（实测 24 字节）。只按
# 「明文末尾 == FF D9」判定密钥，会让这类容器退回错误密钥并留下坏尾。
_FOOTER_MAX = 32

_IMG_END_MARK = (
    (b"\xff\xd8", b"\xff\xd9"),
    (b"\x89PNG\r\n\x1a\n", b"\x49\x45\x4e\x44\xae\x42\x60\x82"),
)


def strip_container_footer(plain: bytes) -> Tuple[bytes, int]:
    """按图片结束标记裁掉尾部页脚，返回 (明文, 被裁掉的字节数)。"""
    for soi, end in _IMG_END_MARK:
        if not plain.startswith(soi) or len(plain) <= len(end):
            continue
        if plain.endswith(end):
            return plain, 0
        i = plain.rfind(end)
        if i > 0 and len(plain) - i - len(end) <= _FOOTER_MAX:
            return plain[:i + len(end)], len(plain) - i - len(end)
    return plain, 0


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

    def _derive_xor_key(self, dat_path: str) -> int:
        """从同图缩略图 <md5>_t.dat 尾部 FF D9 反推单字节 XOR 密钥。

        结束标记允许出现在文件末尾之前（最多回看 ``_FOOTER_MAX`` 字节）——
        Sns 缓存在 FF D9 之后还挂着页脚，只比对末两字节会退化成错误密钥。
        """
        for cand in (
            dat_path[:-4] + "_t.dat",
            dat_path[:-4] + "_h.dat",
            dat_path,
        ):
            if not os.path.exists(cand):
                continue
            try:
                with open(cand, "rb") as f:
                    f.seek(0, 2)
                    back = min(f.tell(), 2 + _FOOTER_MAX)
                    f.seek(-back, 2)
                    tail = f.read()
            except OSError:
                continue
            for off in range(len(tail) - 1):
                key = tail[-2 - off] ^ 0xFF
                if tail[-1 - off] ^ key == 0xD9:
                    return key

        return 0x88

    def _install_xor_key(self) -> int:
        """账号级单字节 XOR 密钥：cfgDword 低字节(权威) → 缩略图统计 → 0x88。

        缓存到 ``self._xor_key``。逐文件猜尾部只作兜底，因为带页脚的容器
        会让尾部判据失效。
        """
        if self._xor_key is not None:
            return self._xor_key
        derived = self._derive_cfg_key()
        if derived:
            self._xor_key = derived[1]
        else:
            self._xor_key = self._get_xor_key(self._collect_templates())
        if self._xor_key is None:
            self._xor_key = 0x88
        return self._xor_key

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
            except Exception as exc:
                wxlog.debug(f'图片密钥试解失败：{exc!r}')
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
            self._xor_key = xor_key = derived[1]
            self._img_key = (derived[0], derived[1])
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
        """统一密钥解析：显式注入 → cfgDword派生 → 本地缓存 → 内存扫描"""
        if self._image_key and self._validate_key(self._image_key):
            return self._image_key
        # 尝试cfgDword派生
        derived = self._derive_cfg_key()
        if derived:
            return derived[0]
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
            xor_key = self._install_xor_key()
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
        out = pt + raw_data + bytes(b ^ (xor_key & 0xFF) for b in xor_data)
        out, footer = strip_container_footer(out)
        if footer:
            wxlog.debug("V2 尾部 %d 字节页脚已按结束标记裁剪: %s"
                        % (footer, dat_path))
        return out

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
    # 三档副本：_h.dat(原件) / .dat(微信下发的那一份，可能是完整图也可能是预览版) / _t.dat(预览图)
    # ------------------------------------------------------------------
    IMAGE_TIERS = ("original", "mid", "thumb")

    # 预览窗里那颗缩放切换键的两个状态名（同一颗按钮，Name 随显示状态变）：
    #   「图片原始大小」  = 当前是缩放显示，点它会去要/显示原件（可能触发下载）
    #   「图片适应窗口大小」= 原件已经在显示中，点它只会缩回去，不该点
    ZOOM_REQUEST = "图片原始大小"
    ZOOM_SHOWN = "图片适应窗口大小"

    def _image_files(self, user: str, md5: str) -> dict:
        """这条图片在本机有哪几档副本：``{tier: (path, bytes)}``，缺的档不出现。

        一次目录扫描出全部三档。原来每查一档就 ``os.walk`` 一整棵会话附件树，
        批量状态接口如果也这么写，300 张图就是 900 次全树遍历。
        """
        want = {md5 + "_h.dat": "original", md5 + ".dat": "mid",
                md5 + "_t.dat": "thumb"}
        base = os.path.join(self.db.account_dir, "msg", "attach", self._chat_md5(user))
        out = {}
        for root, _, files in os.walk(base):
            for f in files:
                tier = want.get(f)
                if tier and tier not in out:
                    p = os.path.join(root, f)
                    try:
                        out[tier] = (p, os.path.getsize(p))
                    except OSError:
                        continue
        return out

    @staticmethod
    def original_ready(h_size: Optional[int], mid_size: Optional[int] = None,
                       min_bytes: int = 1024) -> bool:
        """``_h.dat`` 存不存在、是不是空壳。**不比大小**——尺寸不能当「下完没下完」的判据。

        两代错判都在这儿：
        - 1.2.4.2 之前是硬编码 ``> 102400``：本机 705 个 ``_h.dat`` 中位数只有 91.5KB，
          51.3% 的真原图被当成「还没下载」。
        - 1.2.4.2 换成「不比压缩版 ``.dat`` 小 10% 以上」，用户实机证明**这条也是错的**：
          同一张图的 ``.dat`` 有时就是比 ``_h.dat`` 大（实测 h/mid 有 0.55、0.82 的，
          两种编码各存一份），于是真原图又被判成没下完，代码去点「图片原始大小」——
          而原图本来就在盘上，那个按钮只是切显示缩放，点了自然没有任何反应。

        判「完整」改看**解密后的结构**（:meth:`_image_complete`：JPEG 有没有 ``FF D9``、
        PNG 有没有 ``IEND``）；正在下载中的截断由 :meth:`_wait_h_dat` 的「大小不再变化」
        轮询负责，不靠尺寸猜。``mid_size`` 参数保留不动签名（1.2.4.2 已发布），
        但不再参与判断。
        """
        return bool(h_size) and int(h_size) >= int(min_bytes)

    @staticmethod
    def _image_complete(data) -> bool:
        """解密出来的字节看起来是不是**一张完整的图**，而不是下到一半的截断文件。

        认不出格式（wxgf 动画、别的容器）时返回 ``True``——不拿「看不懂」当「不完整」，
        否则会重演误杀。
        """
        if not data or len(data) < 16:
            return False
        head = data[:8]
        if head[:3] == b"\xff\xd8\xff":          # JPEG
            return b"\xff\xd9" in data[-64:]
        if head[:4] == b"\x89PNG":               # PNG
            return b"IEND" in data[-16:]
        if head[:6] in (b"GIF87a", b"GIF89a"):   # GIF 结束符 0x3B，可能补零
            return b"\x3b" in data[-16:]
        return True

    def _wait_tier(self, user: str, md5: str, min_bytes: int = 1024,
                   until: Optional[float] = None, tiers=("original",)):
        """轮询等某一档出现**并且不再变大**。返回 ``(档位, 路径, 字节)``，没有就 ``None``。

        以前是点完固定 ``sleep(3)`` 再取一次——慢机上文件还在长就被判失败，
        快机上白等。「下完没」这里只看**大小不再变化**，不看它和压缩版谁大
        （见 :meth:`original_ready` 的说明：尺寸比例不是判据）。

        ``tiers`` 是优先级：``('original',)`` 只等原件；``('original', 'mid')``
        是「非预览图那一份先到算哪个都行」，先稳定的交出来，原件随后到了也不回头。
        """
        last = {}
        while True:
            files = self._image_files(user, md5)
            expired = until is not None and time.time() >= until
            for t in tiers:                      # 按优先级问，只有一处排序
                hit = files.get(t)
                if not hit or not self.original_ready(hit[1], min_bytes=min_bytes):
                    continue
                # 到点了就不再要求「不再变大」：慢机上文件已经可用，交出去比报失败有用
                if last.get(t) == hit[1] or expired:
                    return (t, hit[0], hit[1])
                last[t] = hit[1]
            if expired:
                return None
            time.sleep(0.5)

    def _newer_image_count(self, user: str, sort_seq) -> int:
        """会话里比这条更新的图片有几张（数不出来返回 0，退化成逐个试）。"""
        if sort_seq is None:
            return 0
        try:
            rows = self.db.get_image_rows(user, limit=400)
        except Exception:
            return 0
        return sum(1 for r in rows if (r.get('sort_seq') or 0) > sort_seq)

    @staticmethod
    def _order_bubbles(pairs, n_newer: int) -> list:
        """把最可能是目标的那个气泡排到第一个试。

        可视区从上到下就是消息从旧到新；这条图下面压着 ``n_newer`` 张更新的图，
        所以它应当是「从下往上数第 n_newer+1 张」。数不到（目标在可视区之外）时
        原样返回——不盲猜，调用方每点一张都会回来核对 ``_h.dat`` 是不是这条的。

        Args:
            pairs: ``[(气泡控件, BoundingRectangle.top), ...]``
        """
        ordered = sorted(pairs, key=lambda p: p[1])
        if not ordered:
            return []
        idx = len(ordered) - 1 - max(0, int(n_newer))
        if idx < 0 or idx >= len(ordered):
            return [ch for ch, _ in ordered]
        chosen = ordered.pop(idx)
        return [chosen[0]] + [ch for ch, _ in ordered]

    # 可视行类名 → 参与对齐的种类。实机对着数据库核过一遍（同一会话同一屏）：
    #   ``mmui::ChatTextItemView``          = 文本（Name 是被截断的正文）
    #   ``mmui::ChatBubbleReferItemView``   = 图片气泡，但**只有 Name=='图片' 的那几行**
    #       （同一个类名还用来渲染引用卡片等，Name 是摘要文字）
    #   ``mmui::ChatBubbleItemView``        = 文件/链接/卡片（库里正文 ~1900 字 XML，
    #       UIA 只给 43 字摘要）——**不是文本行**，按直觉映射成 text 会把整段对齐带偏
    #   ``mmui::ChatSystemInfoItemView``    = 系统消息
    #   ``mmui::ChatItemView``              = 时间分隔行（Name 形如 ``11:31``）
    # 后三类不参与对齐：微信的时间文案规则（今天/昨天/M月D日/星期）不值得复刻，
    # 而两侧同时丢掉同一种行不影响对齐结果。
    _UI_ROW_KIND = {"mmui::ChatBubbleReferItemView": "image",
                    "mmui::ChatTextItemView": "text"}
    _DB_ROW_KIND = {"图片": "image", "文本": "text"}

    def _visible_rows(self, lst) -> list:
        """可视区的消息行 ``[(种类, Name, 控件), ...]``，自上而下＝自旧到新。

        矩形中心不在列表内的行不要：虚拟化列表会报出还没画出来的行，点上去只会
        ``Can not move cursor``。
        """
        out = []
        try:
            children = lst.GetChildren()
            lr = lst.BoundingRectangle
        except Exception as e:
            wxlog.debug("读消息列表子节点失败：%s", e)
            return out
        for ch in children:
            try:
                kind = self._UI_ROW_KIND.get(ch.ClassName or "")
                if kind is None:
                    continue
                if kind == "image" and (ch.Name or "") != "图片":
                    continue      # 引用卡片等也用这个类名，但不是图片气泡
                r = ch.BoundingRectangle
                cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
                if not (lr.left <= cx <= lr.right and lr.top <= cy <= lr.bottom):
                    wxlog.debug("%s行矩形在列表外（未渲染）：(%d,%d,%d,%d)",
                                kind, r.left, r.top, r.right, r.bottom)
                    continue
                out.append((kind, ch.Name or "", ch))
            except Exception as e:
                wxlog.debug("读列表子节点失败：%s", e)
                continue
        return out

    @staticmethod
    def _same_text(a: str, b: str) -> bool:
        """UIA 摘要会截断正文，所以按「前 10 字互相包含」判同一条消息。"""
        a, b = (a or "").strip(), (b or "").strip()
        if not a or not b:
            return False
        return a == b or a[:10] in b or b[:10] in a

    def _align_window(self, ui, db, min_score: float = 0.6, min_text_anchors: int = 2):
        """滑窗对齐可视行与数据库行，返回 ``{'offset','score','anchors','ties'}`` 或 None。

        ``offset`` 是 ``ui[0]`` 对应到 ``db`` 里的下标；``ties`` 是**并列最优**偏移列表。
        可视区只有寥寥几行，「T I I T」这种形状在整段历史里会出现很多次，多个偏移同时
        拿满分是常态而不是意外——所以要把它们都交出去，由调用方判断答案是否唯一。
        """
        n, m = len(ui), len(db)
        if not n or m < n:
            return None
        scored = []
        for o in range(0, m - n + 1):
            hit = anchors = 0
            for i in range(n):
                kind, name, _c = ui[i]
                d = db[o + i]
                if d["kind"] != kind:
                    continue
                if kind == "text":
                    if not self._same_text(name, d.get("content") or ""):
                        continue
                    anchors += 1
                hit += 1
            scored.append((hit / float(n), anchors, o))
        scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
        best_score, best_anchors, best_off = scored[0]
        if best_score < float(min_score) or best_anchors < int(min_text_anchors):
            return None
        ties = [o for sc, an, o in scored
                if sc >= best_score - 1e-9 and an >= best_anchors]
        return {"offset": best_off, "score": best_score,
                "anchors": best_anchors, "ties": sorted(ties)}

    def _align_index(self, ui, db, target_local_id, min_score: float = 0.6,
                     min_text_anchors: int = 2):
        """目标消息在**可视行**里的下标；认不出、或认得不唯一，一律 ``None``。

        实机撞过可视区只剩一行时「吻合度 1.00」的假高分（那种情况任何偏移都算完美吻
        合），所以要求至少 ``min_text_anchors`` 条正文对得上的文本行 + 吻合度
        ≥ ``min_score``；再要求**所有并列最优偏移都指向同一行**——否则宁可退回
        :meth:`_order_bubbles` 的计数法，也不要点到别人的图上。

        Args:
            ui: :meth:`_visible_rows` 的结果。
            db: ``[{'kind','content','local_id'}, ...]``，**自旧到新**。
        """
        w = self._align_window(ui, db, min_score, min_text_anchors)
        if not w:
            return None
        try:
            ti = next(i for i, d in enumerate(db)
                      if d.get("local_id") == target_local_id)
        except StopIteration:
            return None
        idx = None
        for o in w["ties"]:
            j = ti - o
            if not (0 <= j < len(ui)) or ui[j][0] != "image":
                return None          # 有并列偏移认为目标压根不在屏上 → 认不得
            if idx is None:
                idx = j
            elif idx != j:
                return None          # 并列偏移给出不同答案 → 歧义，不动手
        return idx

    @staticmethod
    def _plan_scroll(delta_rows: int, rows_per_notch: float = 1.0, max_notches: int = 12):
        """把「还差几行」换算成滚轮：返回 ``(delta, 格数)``，不需要滚时 ``(0, 0)``。

        聊天列表**最新在下方**，所以要把窗口往更新推（``delta_rows > 0``）得向下滚 =
        **负** delta；往更早推是正。这个方向和朋友圈时间线相反（那边最新在上方），
        别照抄。
        """
        if not delta_rows:
            return (0, 0)
        per = max(0.2, float(rows_per_notch))
        notches = min(int(max_notches), max(1, int(abs(int(delta_rows)) / per + 0.999)))
        return (-120 if delta_rows > 0 else 120, notches)

    # 气泡离「发送方那一侧边缘」的绝对锚点（物理像素）。
    # 为什么用绝对值而不是百分比：气泡的位置**不随行宽等比缩放**——头像列加边距是
    # 固定像素，气泡本身也有个上限宽度。实测两种布局：
    #   宽窗口 行宽 2598 → 气泡在左边缘 +44~+632（+1.7%~+24.3%）
    #   竖屏   行宽  720 → 气泡在左边缘 +141~+459（+19.6%~+63.8%）
    # 「行宽的 12%」在前者是 311px（命中），在后者只有 86px（落在底色上，点不中）。
    # 300px 这个锚点在两种布局里都落在气泡内（竖屏 300∈[141,459]，宽窗 300∈[44,632]）。
    BUBBLE_ANCHOR_PX = 300

    @classmethod
    def _bubble_click_xs(cls, rect, is_self) -> list:
        """缩略图在这一行里的可点 x：先发送方那一侧，再试另一侧。

        行矩形是**整行宽**，气泡只占其中一块，而且这块位置不随行宽等比缩放
        （见 :attr:`BUBBLE_ANCHOR_PX` 上面那两组实测数）。所以候选按「绝对锚点 →
        旧的行宽 12%」排，两侧各给一次：宽窗口里两个数算出同一个 x（311 与 300 取
        大 = 311），行为和以前逐字一致；竖屏里绝对锚点先命中，12% 那个只是后备。
        每个候选都有「有没有新弹出预览窗」这个可观察事件兜着，点空不算成功，
        也不可能点到别的行（y 一直是这一行的中线）。
        """
        w = int(rect.right) - int(rect.left)
        pct = int(w * 0.12)
        anchor = min(max(cls.BUBBLE_ANCHOR_PX, pct), int(w * 0.45))
        if is_self:
            cands = [int(rect.right) - anchor, int(rect.right) - pct,
                     int(rect.left) + anchor, int(rect.left) + pct]
        else:
            cands = [int(rect.left) + anchor, int(rect.left) + pct,
                     int(rect.right) - anchor, int(rect.right) - pct]
        xs = []
        for x in cands:
            if x not in xs:
                xs.append(x)
        return xs

    def _sent_by_self(self, row) -> bool:
        """这条消息是不是本机账号自己发的。

        ``real_sender_id`` 是 ``message_resource.db`` 里 ``SenderName2Id`` 的 rowid，
        **本机账号落在哪个 rowid 不是一定的**：代码里原本写死 ``== 2``（1.1.8 从
        别人那台机器带过来的取值），本机实测自己是 1——文件传输助手 400 条消息
        全是 ``sender_id=1``，而 ``SenderName2Id`` 里解析成本机 wxid 的 rowid 也是 1，
        ``2`` 反而是某个常联系的好友（图片消息里有 512 条）。写错这一条的代价很直接：
        自己发的图被当成别人发的，先去点左边那块，永远点不中气泡，看起来就是
        「我发的图片提示找不到原图」。
        """
        sid = row.get("sender_id")
        if sid is None:
            sid = row.get("real_sender_id")
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            return False
        own = ""
        try:
            own = (getattr(self.db, "wxid", "") or "").strip()
            who = self.db._sender_id_index().get(sid)
        except Exception:
            who = None
        if own and who:
            return who == own
        return sid == 1                  # 索引没覆盖（多库账号）时兜底：自己一般是 1

    # 预览窗的顶层形状实测有两种（同一台机、同一个微信版本）：
    #   ① 桌面的直接子节点就是 ``mmui::PreviewWindow``（Name 是 'Weixin'）；
    #   ② 顶层是 ``Qt51514QWindowIcon``、标题「图片和视频」，``mmui::PreviewWindow``
    #      在它**里面一层**。
    # 只按直接子节点的类名筛的话，第 ② 种永远找不到——于是「图片原始大小」按钮
    # 一次都没被点过，看起来就像「点击不对 / 比例错了」。
    _PREVIEW_TITLES = ("图片和视频",)

    @staticmethod
    def _preview_windows():
        """当前所有预览窗 ``[(窗口句柄, 控件), ...]``。

        句柄用来分辨「新出现的」和「本来就开着的」；控件一律给 ``mmui::PreviewWindow``
        那一层（两种形状都能对齐到同一层，调用方按子树找按钮）。
        """
        import uiautomation as auto
        out = []
        try:
            tops = auto.GetRootControl().GetChildren()
        except Exception:
            return out
        for w in tops:
            cls = w.ClassName or ""
            inner = None
            if "PreviewWindow" in cls:
                inner = w
            elif (w.Name or "").strip() in MediaDownloader._PREVIEW_TITLES \
                    and cls.startswith("Qt"):
                try:
                    kids = w.GetChildren()
                except Exception:
                    kids = []
                inner = next((k for k in kids
                              if "PreviewWindow" in (k.ClassName or "")), w)
            if inner is None:
                continue
            try:
                h = w.NativeWindowHandle or id(w)
            except Exception:
                h = id(w)
            out.append((h, inner))
        return out

    def _write_decrypted(self, data: bytes, stem: str, save_dir: Optional[str]) -> str:
        """按文件头判格式落盘（wxgf 先试转码），返回路径。"""
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
                out = self._out(save_dir, stem + ".jpg")
                with open(out, "wb") as f:
                    f.write(jpg)
                return out
            ext = "wxgf"
        else:
            ext = "img"
        out = self._out(save_dir, stem + "." + ext)
        with open(out, "wb") as f:
            f.write(data)
        return out

    def image_status(self, user: str, local_id: int, verify: bool = False,
                     shard: Optional[str] = None) -> dict:
        """一条图片消息在本机的副本情况——让「只要原图」的调用方不必靠猜。

        Args:
            verify: True 时会把 ``_h.dat`` **解密看一眼**再判完整（JPEG 有没有
                ``FF D9``、PNG 有没有 ``IEND``）。批量调用不要开：那等于把每张原图
                都解一遍。拿不到图片密钥时不否决，按尺寸结论返回。

        Returns:
            dict：``local_id`` / ``md5`` / ``tiers``（``{'original': 字节, 'mid':…,
            'thumb':…}``，没有的档不出现）/ ``best``（本机最高一档）/
            ``available`` / ``has_mid`` / ``reason``。

            - ``available``：有没有**原件** ``_h.dat``（且不是空壳）。**只有这一档是
              确定的原图**。
            - ``has_mid``：有没有 ``.dat`` 这一份。注意它**不保证是"完整图"**——
              ``.dat`` 是"微信下发的那一份"，可能是完整图，也可能本身就是预览版
              （实测例：某条 ``.dat`` 44,002 字节、预览图 2,961 字节，那张 ``.dat``
              仍是预览图）。本机分不出来，只有预览窗里有没有「图片原始大小」那颗键
              能回答。
            ``shard``：把消息行里的 ``shard``（分片库文件名）原样传回，用于跨分片
                同号时锁定那一条；不传则按 ``sort_seq`` 取最新并告警。
            ``reason`` 取值：``ok``、``no_message_row``、``not_image``、``no_md5``、
            ``no_local_copy``、``only_thumbnail``、``mid_only``、``original_partial``
            （有 ``_h.dat`` 但结构不完整，即原图下载中断）。
        """
        row = self.db.get_message_row(user, local_id, local_type=3, shard=shard)
        if not row or row.get("local_type") != 3:
            return {"local_id": local_id, "md5": None, "tiers": {},
                    "best": None, "available": False, "reason": "no_message_row"}
        md5 = self._img_md5(row)
        if not md5:
            return {"local_id": local_id, "md5": None, "tiers": {},
                    "best": None, "available": False, "reason": "no_md5"}
        return self._image_status_for(user, local_id, md5, verify=verify)

    def _image_status_for(self, user: str, local_id: int, md5: str,
                          verify: bool = False) -> dict:
        files = self._image_files(user, md5)
        tiers = {t: sz for t, (_p, sz) in files.items()}
        best = next((t for t in self.IMAGE_TIERS if t in tiers), None)
        reason = "no_local_copy"
        if "original" in tiers:
            ok = self.original_ready(tiers["original"])
            if ok and verify:
                complete = self._decrypted_complete(user, md5)
                if complete is False:
                    ok = False
            reason = "ok" if ok else "original_partial"
            if not ok:
                best = next((t for t in ("mid", "thumb") if t in tiers), None)
        elif "mid" in tiers:
            reason = "mid_only"
        elif "thumb" in tiers:
            reason = "only_thumbnail"
        return {"local_id": local_id, "md5": md5, "tiers": tiers,
                "best": best, "available": reason == "ok", "reason": reason,
                # 有没有 .dat 这一份。注意这不等于「有完整图」——.dat 有时本身就是预览版。
                "has_mid": ("mid" in tiers) or ("original" in tiers)}

    def _decrypted_complete(self, user: str, md5: str):
        """解密看一眼 ``_h.dat`` 完不完整；``True``/``False``/``None``（判不了，不否决）。"""
        path = (self._image_files(user, md5).get("original") or (None, 0))[0]
        if not path:
            return None
        try:
            data = self.decrypt_image(path)
        except Exception:
            return None
        if not data:
            return None
        return self._image_complete(data)

    def list_image_status(self, user: str, limit: int = 300) -> List[dict]:
        """会话里所有图片消息的档位一览（按时间降序）。

        和 :meth:`list_voice_status` 同一个形状：调用方一次拿到「哪几条真有原图」，
        而不是逐条 ``download_image`` 之后凭文件名猜。消息表只查一次、附件目录只扫一次。
        """
        rows = self.db.get_image_rows(user, limit=max(1, int(limit)))
        out = []
        cache = {}
        for r in rows:
            md5 = self._img_md5(r)
            if not md5:
                out.append({"local_id": r.get("local_id"), "md5": None, "tiers": {},
                            "best": None, "available": False, "reason": "no_md5",
                            "shard": r.get("shard")})
                continue
            st = cache.get(md5)
            if st is None:
                st = self._image_status_for(user, r.get("local_id"), md5)
                cache[md5] = st
            out.append(dict(st, local_id=r.get("local_id"),
                            create_time=r.get("create_time"),
                            shard=r.get("shard")))
        return out

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

    @staticmethod
    def _pick_full(files, mid_sz):
        """本机「不是 ``_t.dat`」的那一份：``_h.dat`` > ``.dat``；只有预览图时 ``None``。

        别把这里的 ``.dat`` 理解成"完整图"：它是微信下发的那一份，可能是完整图也可能
        是预览版，**本机分不出来**。这个方法只保证一件事——**不会拿预览图档交差**。
        要确定的原件就别用它，用 :meth:`download_image_original`（它会走界面去要
        ``_h.dat``）。
        """
        hit = files.get('original')
        if hit and MediaDownloader.original_ready(hit[1], mid_sz):
            return hit, '_h'
        if 'mid' in files:
            return files['mid'], ''
        return None

    def download_image(self, user: str, local_id: int, save_dir: Optional[str] = None,
                       aes_key: Optional[str] = None, xor_key: Optional[int] = None,
                       tier: Optional[str] = None,
                       shard: Optional[str] = None) -> Optional[str]:
        """下载图片消息并解密为 jpg/png/gif，返回落盘路径。

        一条图片在本地最多有三档：``_h.dat`` 原图（点过「查看原图」才有）、
        ``.dat`` 微信默认下发的压缩版、``_t.dat`` 缩略图。

        Args:
            tier: ``None``（默认，**行为与旧版逐字一致**）先取压缩版、没有再退缩略图；
                ``'original'`` 只要原图，本机没有就返回 ``None`` 不悄悄降级；
                ``'mid'`` 只要压缩版；``'thumb'`` 只要缩略图；
                ``'best'`` 原图 > 压缩版 > 缩略图，并把档位标在文件名上
                （``_h`` / 无 / ``_thumb``）；
                ``'full'`` **只要「不是 ``_t.dat`」的那一份**：``_h.dat`` > ``.dat``，
                绝不用预览图档交差；本机两档都没有时返回 ``None``（这个方法只读本机，
                不碰界面）。**别把这里的 ``.dat`` 当成"完整图"**：它是微信下发的那一份，
                可能是完整图，也可能本身就是预览版，在本机看不出来——要**确定的原件**用
                :meth:`download_image_original`（本机没有 ``_h.dat`` 就驱动界面去下载）。
                想要「本机有什么就给什么」的调用方应该用这个或 ``'best'``，而不是
                ``'original'``——``'original'`` 要的是"发的时候勾了原图"那一档，本机多数图
                根本没有（实测自发图 554 条里 19 条有、别人发的 3401 条里 611 条有）。
                以前 ``tier=None`` 拿到压缩版时文件名不带任何标记，调用方分不清
                自己拿到的是原图还是压缩版，只能靠大小猜——「只要原图」的调用方
                因此要么误收、要么反复重试。
            ``shard``：把消息行里的 ``shard``（分片库文件名）原样传回，用于跨分片
                同号时锁定那一条；不传则按 ``sort_seq`` 取最新并告警。
        """
        row = self.db.get_message_row(user, local_id, local_type=3, shard=shard)
        if not row or row["local_type"] != 3:
            return None
        md5 = self._img_md5(row)
        if not md5:
            return None
        files = self._image_files(user, md5)
        if not files:
            return None
        mid_sz = (files.get('mid') or (0, 0))[1]
        if tier is None:                       # 老行为：压缩版 → 缩略图
            if 'mid' in files:
                pick, suffix = files['mid'], ''
            elif 'thumb' in files:
                pick, suffix = files['thumb'], '_thumb'
            else:
                return None
        elif tier == 'original':
            hit = files.get('original')
            if not hit or not self.original_ready(hit[1], mid_sz):
                return None
            pick, suffix = hit, '_h'
        elif tier == 'best':
            hit = files.get('original')
            if hit and self.original_ready(hit[1], mid_sz):
                pick, suffix = hit, '_h'
            elif 'mid' in files:
                pick, suffix = files['mid'], ''
            elif 'thumb' in files:
                pick, suffix = files['thumb'], '_thumb'
            else:
                return None
        elif tier == 'full':
            # 「不是 _t.dat 的那一份」，**只读本机**：download_image 的口径就是本机有什么
            # 就交什么，绝不碰界面。要确定的原件（本机没有就驱动界面去下载）用
            # download_image_original()。
            got = self._pick_full(files, mid_sz)
            if got is None:
                wxlog.debug("图片 %s/%s 本机只有预览图（或压根没下过）：download_image 不碰"
                            "界面，要让微信去下请用 download_image_original()", user, local_id)
                return None
            pick, suffix = got
        elif tier in ('mid', 'thumb'):
            hit = files.get(tier)
            if not hit:
                return None
            pick, suffix = hit, ('' if tier == 'mid' else '_thumb')
        else:
            return None
        data = self.decrypt_image(pick[0], aes_key, xor_key)
        return self._write_decrypted(data, "%s_%s%s" % (user, local_id, suffix), save_dir)

    @staticmethod
    def _scroll_list(lst, delta: int, times: int, uia, hwnd, pid) -> bool:
        """在消息列表中央滚 ``times`` 格；落点不属于微信或置不上前台就**不滚**。

        滚轮是按光标出队那一刻的位置投递的，而且微信不是前台窗口时 mmui 根本不接收
        ——所以先置前台，再交给 :func:`moment._send_scroll`（它带落点归属校验：曾经
        有一次自检把滚轮打进了压在微信上面的 IDE）。
        """
        from .moment import _send_scroll
        r = lst.BoundingRectangle
        x = int((r.left + r.right) / 2)
        y = int((r.top + r.bottom) / 2)
        if hwnd:
            try:
                uia._force_foreground(hwnd)
            except Exception as e:
                wxlog.debug("置前台失败：%s", type(e).__name__)
        return bool(_send_scroll(x, y, delta=delta, times=int(times), expect_pid=pid))

    def _locate_image_row(self, local_id, lst, db_seq, uia, hwnd, pid,
                          deadline, max_scrolls: int = 6):
        """认出目标图片行；不在可视区时**按数据库算出的行差滚过去**再认。

        返回 ``(控件, 说明)``。认不出时控件为 ``None``，说明写清卡在哪一步，调用方据此
        决定是退回计数法还是直接放弃：``align`` 锚点不够或对不齐 · ``not-in-db`` 目标不
        在取回的历史窗口里（要加 ``get_messages`` 的 limit）· ``scroll-blocked`` 滚轮没
        发出去（非前台 / 落点不是微信）· ``scroll-stuck`` 滚了但窗口纹丝不动 ·
        ``scroll-limit`` 滚到次数上限还没滚到。

        每格滚多少行是**实测出来的**：先按 1 行/格 假设，滚完看偏移真的挪了几行再修正，
        所以不同 DPI、不同窗口高度都不需要预设常量。
        """
        rows_per_notch = 1.0
        last = None                       # 上一轮的 (偏移, 格数)，用来量实际推进
        for attempt in range(int(max_scrolls) + 1):
            if time.time() >= deadline:
                return None, "align"
            ui = self._visible_rows(lst)
            w = self._align_window(ui, db_seq)
            if not w:
                return None, "align"
            try:
                ti = next(i for i, d in enumerate(db_seq)
                          if d.get("local_id") == local_id)
            except StopIteration:
                return None, "not-in-db"
            idx = self._align_index(ui, db_seq, local_id)
            if idx is not None:
                return ui[idx][2], ("aligned" if attempt == 0 else "aligned-after-%d" % attempt)
            if last is not None:
                if w["offset"] == last[0]:
                    return None, "scroll-stuck"
                rows_per_notch = max(0.2, abs(w["offset"] - last[0]) / float(max(1, last[1])))
                last = None
                wxlog.debug("实测每格滚 %.1f 行", rows_per_notch)
            if attempt >= int(max_scrolls):
                return None, "scroll-limit"
            o, n = w["offset"], len(ui)
            delta_rows = (ti - (o + n - 1)) if ti >= o + n else (ti - o)
            sign, notches = self._plan_scroll(delta_rows, rows_per_notch)
            if not notches:
                return None, "align"
            wxlog.debug("目标在可视窗口%s %d 行处，滚 %d 格（每格≈%.1f 行）",
                        "之后（更新）" if delta_rows > 0 else "之前（更早）",
                        abs(delta_rows), notches, rows_per_notch)
            if not self._scroll_list(lst, sign, notches, uia, hwnd, pid):
                return None, "scroll-blocked"
            last = (o, notches)
            time.sleep(0.9)
        return None, "scroll-limit"

    # ------------------------------------------------------------------
    # 「保存」兜底：预览窗的保存按钮 → 微信自己写出的明文文件
    # ------------------------------------------------------------------
    _SAVE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")

    def _recent_images(self, root, since):
        """``root`` 下 mtime ≥ ``since`` 的图片文件 ``{路径: (mtime, 字节)}``。

        只进「最近被改过」的目录：账号目录有上 GB 的附件树，全量走两遍太贵，
        而微信写新文件时沿途目录的 mtime 一定会变。
        """
        out = {}
        stack = [root]
        while stack:
            d = stack.pop()
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if e.stat(follow_symlinks=False).st_mtime >= since:
                            stack.append(e.path)
                    elif e.name.lower().endswith(self._SAVE_EXT):
                        st = e.stat()
                        if st.st_mtime >= since:
                            out[e.path] = (st.st_mtime, st.st_size)
                except OSError:
                    continue
        return out

    @staticmethod
    def _new_images(before, after):
        """点「保存」之后新增/变大的图片文件路径。"""
        return [p for p, (mt, sz) in after.items()
                if p not in before or before[p][0] < mt - 1e-6]

    @staticmethod
    def _find_by_name(ctrl, pred, max_depth=14, _d=0):
        """深度优先找第一个 Name 满足 ``pred`` 的控件。"""
        if _d > max_depth:
            return None
        for k in ctrl.GetChildren():
            try:
                if pred((k.Name or "").strip()):
                    return k
            except Exception:
                continue
            got = MediaDownloader._find_by_name(k, pred, max_depth, _d + 1)
            if got is not None:
                return got
        return None

    @staticmethod
    def _save_dialog():
        """找微信「保存」弹出的 Windows 通用另存为对话框。

        实机结构：顶层类名 ``#32770``，但**标题是「保存」而不是「另存为」**——
        按标题里有没有「另存」去匹配会认不出。判据改成「``#32770`` + 里面有以
        「保存」开头的按钮」，标题只当辅助。
        """
        import uiautomation as auto
        try:
            tops = auto.GetRootControl().GetChildren()
        except Exception:
            return None
        for w in tops:
            try:
                if (w.ClassName or "") != "#32770":
                    continue
                if MediaDownloader._find_button(
                        w, lambda n: n.startswith("保存")) is None:
                    continue
                return w
            except Exception:
                continue
        return None

    # 「保存」对话框里**不能往里写值**的那几颗 Edit：文件格式选择器（保存类型:）里面
    # 也有一颗 Edit，实机就把目标路径写进过那儿；右上角搜索栏也是 Edit。
    _BAD_EDIT_WORDS = ("类型", "格式", "搜索")

    @staticmethod
    def _find_button(ctrl, pred, max_depth=14, _d=0):
        """深度优先找第一个**真的是按钮**且 Name 满足 ``pred`` 的控件。"""
        if _d > max_depth:
            return None
        for k in ctrl.GetChildren():
            try:
                ctype = k.ControlTypeName or ""
                name = (k.Name or "").strip()
            except Exception:
                continue
            if "Button" in ctype and pred(name):
                return k
            got = MediaDownloader._find_button(k, pred, max_depth, _d + 1)
            if got is not None:
                return got
        return None

    @staticmethod
    def _dialog_button(dlg, names):
        """对话框里文案以 ``names`` 任一开头的**按钮**（``保存(S)`` 带助记符后缀）。

        以前只按 Name 找，实机点到了「选择文件格式」那颗下拉框——它在 UIA 里的 Name
        也叫「保存」，``Invoke`` 它只会把格式列表展开，保存根本没发生、模态框还留着。
        所以命中名字之后还要看控件类型：``ControlTypeName`` 里没有 Button 的一律不算。
        """
        return MediaDownloader._find_button(
            dlg, lambda n: any(n == x or n.startswith(x + "(") or n == x + "(S)"
                               for x in names))

    @staticmethod
    def _first_edit(root, max_depth=14):
        """子树里第一个**能用**的 Edit：跳过「搜索框」和「保存类型」那两颗假的。

        对话框刚关掉/切页时 UIA 树会在遍历中途失效，读一颗节点的属性就抛——那种
        节点跳过就好，不能把异常抛出整条「保存」路线。
        """
        found = []

        def walk(c, d=0):
            if d > max_depth:
                return
            try:
                kids = list(c.GetChildren())
            except Exception:
                return
            for k in kids:
                try:
                    is_edit = "Edit" in (k.ControlTypeName or "")
                except Exception:
                    is_edit = False
                if is_edit:
                    found.append(k)
                walk(k, d + 1)

        walk(root)
        for e in found:
            try:
                blob = (e.Name or "") + " " + (e.ClassName or "")
            except Exception:
                blob = ""
            if any(w in blob for w in MediaDownloader._BAD_EDIT_WORDS):
                # 「保存类型」那颗下拉框里也有一颗 Edit（当前格式就显示在那儿）。
                # 实机撞到过：文件名框没认出来、值写进了格式选择器，保存当然没发生。
                continue
            return e
        return None

    @staticmethod
    def _file_name_edit(dlg):
        """文件名输入框：优先 Name 含「文件名」。

        实机对话框里还有一个 ``Name='搜索框'`` 的 Edit（右上角搜索栏），拿「第一个
        Edit」会填到那里去；而且「文件名:」在树上出现**两层**——外层是
        ``ComboBoxControl(Name='文件名:')``，真正可写值的是它里面那个
        ``EditControl(Name='文件名:')``，所以命中容器之后还要再往里找 Edit。
        """
        hit = MediaDownloader._find_by_name(dlg, lambda n: "文件名" in n)
        if hit is not None:
            if "Edit" in (hit.ControlTypeName or ""):
                return hit
            inner = MediaDownloader._first_edit(hit)
            if inner is not None:
                return inner
        return MediaDownloader._first_edit(dlg)

    def _fill_save_dialog(self, dlg, target):
        """在「保存」对话框里填完整路径并点 ``保存(S)``：ValuePattern + UIA Invoke，
        **不发键盘**。填不进/找不到按钮返回 False（调用方负责点「取消」收尾）。"""
        edit = self._file_name_edit(dlg)
        btn = self._dialog_button(dlg, ("保存", "Save"))
        if edit is None or btn is None:
            return False
        try:
            edit.GetValuePattern().SetValue(target)
        except Exception:
            return False
        try:
            btn.GetInvokePattern().Invoke()
            return True
        except Exception:
            return False

    @staticmethod
    def _cancel_save_dialog(dlg):
        """点「取消」收尾：模态框不关掉会一直挡着预览窗，后面每张都失败。"""
        btn = MediaDownloader._dialog_button(dlg, ("取消", "Cancel"))
        if btn is None:
            return False
        try:
            btn.GetInvokePattern().Invoke()
            return True
        except Exception:
            return False

    @staticmethod
    def _looks_preview(size, ref_thumb=None, ref_mid=None):
        """「保存」写出来的这一份像不像**预览图**（拿本机已有档位的尺寸当参照）。

        实机反馈：在预览窗直接点「保存」，微信写出的是**当前显示的那一幅**——原件
        还没进显示时它就是预览版，点「图片原始大小」再存一次才是更大的那幅。参照不足
        （本机本来就没有 ``.dat``/``_t.dat``）时**不猜**：返回 ``False``，宁可少点一轮
        界面。
        """
        if not size:
            return False
        if ref_mid and size < 0.8 * float(ref_mid):
            return True
        if ref_thumb and not ref_mid and size <= 1.3 * float(ref_thumb):
            return True
        return False

    def _click_zoom_to_load(self, win):
        """点预览窗的「图片原始大小」，让原件进入显示（下一次「保存」才是原件）。"""
        btn = self._find_preview_button(win, (self.ZOOM_REQUEST, self.ZOOM_SHOWN))
        if btn is None or (btn.Name or "").strip() != self.ZOOM_REQUEST:
            return False
        try:
            btn.Click()
        except Exception:
            try:
                btn.GetInvokePattern().Invoke()
            except Exception:
                return False
        return True

    def _save_via_button(self, win, save_dir, stem, deadline, t0,
                         ref_thumb=None, ref_mid=None, attempts=2):
        """点预览窗的「保存」，把微信写出的明文图片收进 ``save_dir``。

        实机确认：微信「保存」**弹的是 Windows 通用保存对话框**（标题「保存」，
        默认目录 ``temp\\InputTemp``，文件名预填 ``微信图片_YYYYMMDDHHMMSS_***.jpg``），
        不是静默落盘，所以路径不固定——直接把目标全路径填进「文件名:」再点「保存(S)」，
        一步到位。万一以后版本改成静默保存，仍然用「点之前拍快照、点之后找新文件」兜底。
        **任何失败路径都会点「取消」把模态框关掉**，不留在屏幕上挡事。

        ``ref_thumb`` / ``ref_mid`` 传本机那两档的字节数：第一次「保存」拿回来的如果
        按尺寸看就是预览图，会先点「图片原始大小」让原件进显示，**再点一次「保存」**
        （用户实测：第一遍点出来的确实是预览图，得重新下载才是原图），两次里取大的那份。
        参照不足就不重试，也不无脑多点一轮界面。
        """
        target = self._out(save_dir, stem + ".jpg")
        best = 0
        for n in range(1, max(1, int(attempts)) + 1):
            size = self._save_once(win, target, deadline, t0)
            if size is None:
                return self._restore_best(target, best)
            if size > best:
                best = size
                self._stash_best(target)
            if not self._looks_preview(size, ref_thumb, ref_mid):
                wxlog.info("已用预览窗「保存」拿到明文图：%s（%d KB）",
                           os.path.basename(target), size // 1024)
                return self._restore_best(target, best)
            if n < max(1, int(attempts)):
                wxlog.info("第 %d 次「保存」写出的还是预览图那份（%d KB，本机 .dat 是 %s KB），"
                           "点「图片原始大小」再存一次", n, size // 1024,
                           int(ref_mid) // 1024 if ref_mid else "?")
                self._click_zoom_to_load(win)
                time.sleep(1.5)
        wxlog.warning("点了 %d 次「保存」，写出来的仍是预览图那份（%d KB）——"
                      "这张在微信侧最多就是这个尺寸，要真原件只能让对方重发原图",
                      max(1, int(attempts)), best // 1024)
        return self._restore_best(target, best)

    @staticmethod
    def _stash_best(target):
        """把当前这份留一份副本（``.best``），用来在"后一遍反而更小"时换回去。"""
        try:
            with open(target, "rb") as s:
                data = s.read()
            with open(target + ".best", "wb") as d:
                d.write(data)
        except OSError as e:
            wxlog.debug("留保存产物副本失败：%s", e)

    @staticmethod
    def _restore_best(target, best):
        """几遍里最大那份才是能给的那份：最后一遍可能反而更小，这时把留的那份换回去。

        不这么做的话「两遍取大的」只是句空话——``target`` 里躺的是最后一遍写的那一幅。
        """
        keep = target + ".best"
        try:
            if best and os.path.isfile(keep) and (
                    not os.path.isfile(target) or os.path.getsize(target) < best):
                with open(keep, "rb") as s:
                    data = s.read()
                with open(target, "wb") as d:
                    d.write(data)
                wxlog.debug("后一遍「保存」写出的更小，换回最大的那份（%d KB）",
                            len(data) // 1024)
        except OSError as e:
            wxlog.debug("换回最大那份失败：%s", e)
        finally:
            try:
                if os.path.isfile(keep):
                    os.remove(keep)
            except OSError:
                pass
        return target if best else None

    def _save_once(self, win, target, deadline, t0):
        """点一次「保存」并处理对话框。返回写出的字节数，没拿到返回 ``None``。"""
        btn = self._find_preview_button(win, "保存")
        if btn is None:
            wxlog.debug("预览窗里没有「保存」按钮，保存兜底跳过")
            return None
        acc = getattr(self.db, "account_dir", None)
        home = os.path.expanduser("~")
        roots = [p for p in (acc, os.path.join(home, "Downloads"),
                             os.path.join(home, "Pictures"),
                             os.path.join(home, "Documents", "WeChat Files"))
                 if p and os.path.isdir(p)]
        since = max(t0 - 2.0, time.time() - 120.0)
        before = {}
        for r in roots:
            before.update(self._recent_images(r, since))
        try:
            btn.Click()
        except Exception:
            try:
                btn.GetInvokePattern().Invoke()
            except Exception as e:
                wxlog.debug("「保存」点不动：%s", type(e).__name__)
                return None
        until = min(deadline, time.time() + 12.0)
        dialog_seen = False
        while time.time() < until:
            dlg = self._save_dialog()
            if dlg is not None:
                dialog_seen = True
                if not self._fill_save_dialog(dlg, target):
                    wxlog.warning("「保存」对话框里找不到可写的「文件名:」框或「保存」按钮")
                    self._cancel_save_dialog(dlg)
                    return None
                # 点完「保存」，对话框**必须关掉**——这是唯一能证明那一下点中的是按钮的
                # 信号。实机就点到过旁边那颗「选择文件格式」下拉框：模态框还开着、文件
                # 根本没写，而那时目标文件要等 8 秒才判失败，看起来像「点了没反应」。
                closed = False
                end2 = time.time() + 4.0
                while time.time() < end2:
                    if self._save_dialog() is None:
                        closed = True
                        break
                    time.sleep(0.3)
                if not closed:
                    wxlog.warning("点了「保存」之后对话框还开着：那一下没点中按钮"
                                  "（旁边就是「选择文件格式」那颗下拉框），点「取消」收尾")
                    self._cancel_save_dialog(dlg)
                    return None
                end = time.time() + 8.0
                while time.time() < end:
                    if os.path.isfile(target) and os.path.getsize(target) > 0:
                        return os.path.getsize(target)
                    time.sleep(0.4)
                wxlog.warning("对话框已关，但目标目录里没出现该文件：%s",
                              os.path.dirname(target))
                return None
            new = self._new_images(
                before, {p: v for r in roots
                         for p, v in self._recent_images(r, since).items()})
            if new:
                src = max(new, key=lambda p: (os.path.getmtime(p)
                                              if os.path.exists(p) else 0))
                try:
                    with open(src, "rb") as f:
                        data = f.read()
                    if data:
                        with open(target, "wb") as f:
                            f.write(data)
                        return len(data)
                except OSError as e:
                    wxlog.debug("复制保存产物失败：%s", e)
                return None
            time.sleep(0.5)
        wxlog.warning("点了「保存」之后既没出现对话框、也没有新文件（12 秒）"
                      if not dialog_seen else "「保存」对话框处理完仍然没有文件")
        return None


    def _voice_index(self, user: str):
        """该会话在各 media 分片里的 ``(chat_name_id, svr_id) -> 音频字节数``。

        一次性按会话取，别每条语音都全表扫一遍。返回 ``(已知会话数, 索引)``：
        已知会话数为 0 说明这个会话在 media 库里连 Name2Id 都没有。
        """
        known = 0
        idx = {}
        for rel, path, _ in self.db._db_files:
            if not os.path.basename(path).startswith("media_"):
                continue
            conn = self.db._open(rel)
            try:
                cids = [r[0] for r in conn.execute(
                    "SELECT rowid FROM Name2Id WHERE user_name=?", (user,))]
                if not cids:
                    continue
                known += 1
                marks = ",".join("?" * len(cids))
                for cid, svr, ln in conn.execute(
                        "SELECT chat_name_id, svr_id, length(voice_data) "
                        "FROM VoiceInfo WHERE chat_name_id IN (%s)" % marks,
                        tuple(cids)):
                    idx[str(svr)] = max(idx.get(str(svr), 0), ln or 0)
            except Exception:
                continue
            finally:
                conn.close()
        return known, idx

    @staticmethod
    def _voice_reason(svr: str, size: int, ds, known: int) -> str:
        """把「取到什么」归成一个可回答的原因（单条与批量共用一份判据）。"""
        if not svr or svr == "0":
            return "no_server_id"
        if size > 0:
            return "ok"
        if not known:
            return "session_not_in_media_index"
        if ds == 0:
            return "audio_not_downloaded"      # 微信没把音频落盘，读库无能为力
        if ds is None:
            # 这张消息表没有 download_status 列（版本差异），只能保守判断
            return "audio_not_downloaded"
        return "audio_missing_from_media_db"   # 状态说该有，VoiceInfo 里却没有

    def list_voice_status(self, user: str,
                          limit: int = 500) -> List[dict]:
        """列出会话里的语音消息，并逐条说明**音频到底在不在本地**。

        ``download_voice()`` 取不到时只返回 ``None``，调用方分不清「微信本地根本没
        存这段音频」和「库读挂了」——issue #20「26 条语音只识别到 19 条」就是被这个
        歧义卡住的。本机 975 条语音实测：``download_status != 0`` 与「音频在本地」
        完全一一对应（914 条在 / 59 条 ds=0 且确实不在），所以这个字段就是判据。

        Returns:
            按时间降序的 dict 列表，字段：

            - ``local_id`` / ``server_id`` / ``create_time`` / ``self_sent`` /
              ``shard``（分片库文件名：``local_id`` 跨分片会重复，要下载同一条时把它
              传给 :meth:`download_voice`）
            - ``download_status``：消息表原值；老版本表没这列时为 ``None``
            - ``available``：能否取到非空音频
            - ``bytes``：音频字节数（不可用时为 0）
            - ``reason``：``ok`` / ``audio_not_downloaded``（微信没把这段音频
              落盘，读取路径无能为力，只能在界面上播放一次）/
              ``audio_missing_from_media_db``（``download_status`` 说该有，但
              ``VoiceInfo`` 里查不到——这才可能是库的问题）/
              ``session_not_in_media_index`` / ``no_server_id`` / ``empty_blob``
        """
        rows = self.db.get_voice_rows(user, limit=limit)
        if not rows:
            return []
        known, idx = self._voice_index(user)
        out = []
        for r in rows:
            svr = str(r.get("server_id") or "")
            ds = r.get("download_status")
            size = idx.get(svr, 0)
            reason = self._voice_reason(svr, size, ds, known)
            out.append({
                "local_id": r.get("local_id"),
                "server_id": r.get("server_id"),
                "create_time": r.get("create_time"),
                "self_sent": r.get("real_sender_id") == 2,
                "download_status": ds,
                "available": reason == "ok",
                "bytes": size,
                "reason": reason,
                "shard": r.get("shard"),
            })
        return out

    def voice_status(self, user: str, local_id: int) -> dict:
        """单条语音的可用性说明（字段同 :meth:`list_voice_status`）。

        取不到这条语音时返回 ``{'available': False, 'reason': 'no_voice_row'}``。
        """
        rows = self.db.get_voice_rows(user, limit=1, local_id=local_id)
        if not rows:
            return {"local_id": local_id, "available": False,
                    "reason": "no_voice_row", "bytes": 0,
                    "download_status": None, "server_id": None,
                    "self_sent": False, "create_time": None}
        known, idx = self._voice_index(user)
        r = rows[0]
        svr = str(r.get("server_id") or "")
        size = idx.get(svr, 0)
        ds = r.get("download_status")
        reason = self._voice_reason(svr, size, ds, known)
        return {"local_id": r.get("local_id"), "server_id": r.get("server_id"),
                "create_time": r.get("create_time"),
                "self_sent": r.get("real_sender_id") == 2,
                "download_status": ds, "available": reason == "ok",
                "bytes": size, "reason": reason}

    def download_voice(self, user: str, local_id: int, save_dir: Optional[str] = None,
                       shard: Optional[str] = None) -> Optional[str]:
        """语音：media_*.db VoiceInfo.voice_data（SILK 二进制），落盘 .silk

        ``shard``：把消息行里的 ``shard``（分片库文件名）原样传回，用于跨分片
            同号时锁定那一条；不传则按 ``sort_seq`` 取最新并告警。

        微信按账号/时间把语音分片存到多个 media_*.db，逐个搜索直到找到。

        返回 ``None`` 不等于库读坏了：微信只把**在界面上播放/接收过**的语音写进
        ``VoiceInfo``，没落盘的那条再怎么试都没有。想知道具体是哪一种，用
        :meth:`voice_status`（单条）或 :meth:`list_voice_status`（整个会话），
        失败时这里也会把原因写进 debug 日志。
        """
        row = self.db.get_message_row(user, local_id, local_type=34, shard=shard)
        if not row or row["local_type"] != 34 or not row["server_id"]:
            wxlog.debug("语音 %s/%s 取不到：消息行缺失或没有 server_id" % (user, local_id))
            return None
        out_path = None
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
                out_path = self._out(save_dir, "%s_%s.silk" % (user, local_id))
                with open(out_path, "wb") as f:
                    f.write(v["voice_data"])
                return out_path
        st = self.voice_status(user, local_id)
        wxlog.debug("语音 %s/%s 取不到音频，原因=%s（download_status=%s）"
                    % (user, local_id, st.get("reason"), st.get("download_status")))
        return None

    def download_video(self, user: str, local_id: int, save_dir: Optional[str] = None,
                       shard: Optional[str] = None) -> Optional[str]:
        """视频：按 packed_info 中的 id 在 msg/video 下查找 <id>.mp4

        ``shard``：把消息行里的 ``shard``（分片库文件名）原样传回，用于跨分片
            同号时锁定那一条；不传则按 ``sort_seq`` 取最新并告警。
        """
        row = self.db.get_message_row(user, local_id, local_type=43, shard=shard)
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

    def download_file(self, user: str, local_id: int, save_dir: Optional[str] = None,
                      shard: Optional[str] = None) -> Optional[str]:
        """文件：msg/file/<YYYY-MM>/<原文件名>，原文件名来自 message_resource

        ``shard``：把消息行里的 ``shard``（分片库文件名）原样传回，用于跨分片
            同号时锁定那一条；不传则按 ``sort_seq`` 取最新并告警。
        """
        row = self.db.get_message_row(user, local_id, local_type=49, shard=shard)
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

    @staticmethod
    def _find_preview_button(ctrl, name, max_depth=8):
        """在预览窗里按 Name 找按钮。``name`` 可以是字符串，也可以是**候选名元组**
        ——那颗缩放键的 Name 会随显示状态在「图片原始大小」/「图片适应窗口大小」之间
        切换，只写死一个就会有一半时间找不到。"""
        if max_depth <= 0:
            return None
        names = (name,) if isinstance(name, str) else tuple(name or ())
        for kid in ctrl.GetChildren():
            try:
                if (kid.Name or "").strip() in names:
                    return kid
                found = MediaDownloader._find_preview_button(kid, name, max_depth - 1)
                if found:
                    return found
            except Exception:
                pass
        return None

    def _harvest(self, user, md5, local_id, save_dir=None, aes_key=None,
                 xor_key=None, min_bytes=1024, note=""):
        """回头看一眼本机 ``_h.dat`` 在不在：在就直接解密交出去。

        只认 ``_h.dat``——**.dat 不算拿到**：它是"微信下发的那一份"，可能是完整图，
        也可能本身就是预览版（本机实测一张 ``.dat`` 44KB 的仍是预览图）。所以本机
        只有 ``.dat`` 时这里返回 ``None``，让调用方继续走点击路径去要原件。

        存在的意义是堵「第一遍明明已经拿到、却报下载失败；第二遍一起来说已在本地」：
        微信经常在我们要它的那一档**之后**才把文件写完。所以每个决策点（进门、点下一张
        气泡之前、等档没等到之后、判失败之前）都查一次盘——查到就交，既不再多点一张
        （用户说的「瞎点」），也不会误报失败。

        完整与否看**解密后的结构**（:meth:`_image_complete`），半截文件不算拿到。
        """
        files = self._image_files(user, md5)
        hit = files.get('original')
        if not hit or not self.original_ready(hit[1], min_bytes=min_bytes):
            return None
        try:
            data = self.decrypt_image(hit[0], aes_key, xor_key)
        except Exception as e:
            wxlog.debug("解密本机已有副本失败（%s）%s", type(e).__name__, note)
            return None
        if not data:
            return None
        if not self._image_complete(data):
            wxlog.warning("本机 ``_h.dat`` 解密后缺 JPEG/PNG 收尾标记（下到一半），"
                          "按没拿到处理%s：%d 字节", note, len(data))
            return None
        wxlog.info("本机已经有原件（%d KB），直接解密落盘%s：%s",
                   len(data) // 1024, note, hit[0])
        return self._write_decrypted(data, "%s_%s" % (user, local_id), save_dir)

    def download_image_original(self, user: str, local_id: int, save_dir: Optional[str] = None,
                              aes_key: Optional[str] = None, xor_key: Optional[int] = None,
                              timeout: float = 30.0, chat_name: Optional[str] = None,
                              min_bytes: int = 1024, scroll: bool = True,
                              max_scrolls: int = 6,
                              shard: Optional[str] = None) -> Optional[str]:
        """要这条图片的**原件 ``_h.dat``**：本机没有就一定驱动界面去下载。

        一条图片在本地最多三档，但**别把 ``.dat`` 当"完整图"**：

        - ``_t.dat`` 预览图/缩略图；
        - ``.dat`` 微信下发的那一份 —— 可能是完整图，也可能**本身就是预览版**
          （本机实测例：一条 ``.dat`` 44,002 字节、``_t.dat`` 2,961 字节，那张仍是预览图）；
          在本机**看不出来**是哪一种；
        - ``_h.dat`` 真正的原件（发的时候勾了「原图」，或点过「查看原图」才会落盘）。

        所以本方法的口径是：**没有 ``_h.dat`` 就强制走点击路径**（点开气泡 → 需要时点
        「图片原始大小」→ 等 ``_h.dat`` 落盘），绝不拿 ``.dat`` 冒充成果。只想"本机有
        什么就给什么"的用 :meth:`download_image`（``tier='full'`` / ``'best'``），
        那条路一次界面都不碰。

        Args:
            timeout: 点击之后等 ``_h.dat`` 出现并下完的**总**上限（秒）。
                以前这个参数是摆设（代码里只 ``sleep(3)`` 一次就判失败）。
            min_bytes: ``_h.dat`` 的下限字节数（挡空壳）；「下完没」看的是
                :meth:`original_ready`（非空 + 不小于 ``min_bytes``）加上大小不再变化，
                **不拿它和别的档比大小**（那条比例判据被实测否掉了）。
            chat_name: 用于 UI 搜索的会话名称（微信里显示的名字，默认用 user）
            scroll: 目标图片**不在可视区**时，是否按数据库算出的行差把消息列表滚过去
                （默认 True）。设为 False 就只在当前屏上找，绝不滚动界面。
            max_scrolls: 最多滚几轮（每轮滚完重新认一次行）。滚不动或滚满会明确报
                ``scroll-stuck`` / ``scroll-limit``，不会一直滚。

        Returns:
            解密后的原件路径（文件名沿用 ``<user>_<local_id>.<ext>``，不加档位后缀，
            与老版本一致）；拿不到返回 ``None``，并打一条说明**卡在哪一步**的日志。
            本机三档的实际情况随时可以用 :meth:`image_status` 查到（``best`` 是本地最高
            一档，但 ``best == 'mid'`` **不代表那就是完整图** —— 见上面 ``.dat`` 的说明）。

            ``shard``：把消息行里的 ``shard``（分片库文件名）原样传回，用于跨分片
                同号时锁定那一条；不传则按 ``sort_seq`` 取最新并告警。

            「本机只有 ``.dat``」不是提前返回的理由：那条一路都会走界面；只有本机已经有
            ``_h.dat`` 时才跳过界面（那种点也不需要点）。
        """
        row = self.db.get_message_row(user, local_id, local_type=3, shard=shard)
        if not row or row["local_type"] != 3:
            wxlog.warning("原图取不到：消息 %s/%s 不是图片行" % (user, local_id))
            return None
        md5 = self._img_md5(row)
        if not md5:
            wxlog.warning("原图取不到：%s/%s 的内容里找不到图片指纹"
                          "（packed_info/content 无 32 位 hex）" % (user, local_id))
            return None
        files = self._image_files(user, md5)
        mid_sz = (files.get('mid') or (0, 0))[1]
        # 进门先查一次盘：原件常常早就在本地了——那种情况既不用把微信弄到前台，也不用
        # 要求目标气泡在可视区里。**只认 _h.dat**：本机只有 .dat 时照样往下走点击路径。
        got0 = self._harvest(user, md5, local_id, save_dir, aes_key, xor_key,
                             min_bytes=min_bytes, note="（不触发界面）")
        if got0:
            return got0

        if self._sent_by_self(row) and mid_sz and not files.get('original'):
            # 自己发出去的图，本机一般不存在原件（实测 554 条自发图片里只有 19 条带
            # _h.dat，而那 19 条**都没有** .dat —— 只有发的时候勾了「原图」才留原件）。
            # 但这里**不提前返回**：.dat 有可能就是预览版，到底有没有更大的那份，
            # 只能让界面去回答（点开后看有没有「图片原始大小」那颗键）。
            wxlog.info("这条图是自己发出去的，本机只有 .dat（实测 554 条自发图片里只有 "
                       "19 条有 _h.dat）；仍然走点击路径确认一下 —— .dat 有时本身就是"
                       "预览版。只想拿本机那份的话用 download_image(tier='full')。")

        from .uia_driver import WeChatUIA
        from .guia import WinInput

        def give_up(why, *args):
            """界面这条路走不通 / 等不到时，**先再查一次盘**再说失败。

            用户实测的两个现象都出在这里：点完第一张之后微信才把文件写完，于是
            「明明已经拿到了还在一张一张瞎点，最后报下载失败；重新运行又说已在本地」。
            查到了就交文件（并说明是在哪一步查到的），查不到才把失败原因原样报出去。
            """
            wxlog.warning(why, *args)
            return self._harvest(user, md5, local_id, save_dir, aes_key, xor_key,
                                 min_bytes=min_bytes,
                                 note="（界面这条路不通时补查到的）")

        _uia = WeChatUIA()
        if not _uia.ensure_window():
            return give_up("原图取不到：UIA 主窗拿不到（微信没登录/没前台，"
                           "或控件树没物化——看日志里有没有「控件树拿不到」那句）")
        time.sleep(1.0)

        # 批量下载同一个会话的多张图时，每张都重新搜索进会话是没必要的开销（也是
        # 一次真实的界面写动作）。已经在目标会话里就跳过搜索——用输入框的 Name 判，
        # 它一直是会话标题，不是正文。
        want_chat = (chat_name or user or "").strip()
        opened = False
        cur = None
        try:
            cur = (_uia.current_chat() or "").strip()
        except Exception:
            cur = None
        if want_chat and cur == want_chat:
            opened = True
            wxlog.debug("已在会话 %r 里，跳过搜索进入", want_chat)
        wx = None
        if not opened:
            for _retry in range(3):
                try:
                    from .wx import WeChat
                    wx = WeChat()
                    opened = wx.ChatWith(want_chat)
                    time.sleep(2.0)
                    break
                except Exception as e:
                    wxlog.debug("ChatWith 第 %d 次抛错：%s: %s",
                                _retry, type(e).__name__, str(e)[:100])
                    time.sleep(1.0)
        hwnd = pid = None
        try:                              # 滚动定位要用的两样：主窗句柄 + 微信进程 id
            hwnd = wx._gui.main_hwnd or None
            pid = wx._gui.pid or None
        except Exception:
            pass
        if not pid:
            # 跳过搜索时没有 WeChat 对象。滚轮的落点校验不能因此省掉——
            # 2026-09-25 就有一次自检把滚轮打进了压在微信上面的 IDE。
            try:
                hs = _uia._wechat_hwnds()
                if hs:
                    hwnd = hs[0]
                    pid = _uia._pid_from_hwnd(hwnd)
            except Exception:
                pass
        if scroll and not pid:
            wxlog.warning("拿不到微信进程 id，本轮不滚动界面（避免把滚轮打进别的程序）；"
                          "要强行只在当前屏找图可以传 scroll=False")
        # 滚动的唯一硬前提：能校验落点窗口属于微信。拿不到 pid 就退化成「只认当前屏」。
        scroll_ok = bool(scroll and pid)

        clicked = False
        path_got = None           # _wait_tier 交回来的那一档路径（解密后就落盘）
        saved_plain = None        # 「保存」兜底拿到的明文文件
        try:
            time.sleep(1.0)
            lst = _uia._message_list()
            if lst is None:
                # 主窗停在朋友圈页时 `mmui::RecyclerListView` **真的不在树里**
                # （实测各导航页里只有朋友圈会掉，通讯录/收藏/发现那几页树是一样的），
                # 于是这里拿到 None。以前只警告一句就返回，看起来就是「UIA 没有任何操作」。
                wxlog.debug("消息列表不在树里（主窗大概率停在朋友圈页），"
                            "点「微信」标签回聊天页再试一次")
                try:
                    # 点几下、要不要点是 back_to_chat_tab 自己的事：窄窗口里开着会话时
                    # 点一次只切到聊天页、退不出会话，它内部会补第二下。这里不管它
                    # 返回什么都不猜，直接重新读一次列表——读得到就继续走。
                    _uia.back_to_chat_tab()
                    time.sleep(1.0)
                    try:
                        from .wx import WeChat
                        (wx if wx is not None else WeChat()).ChatWith(want_chat)
                    except Exception as e:
                        wxlog.debug("回聊天页后重新进会话失败：%s", type(e).__name__)
                    time.sleep(1.5)
                    lst = _uia._message_list()
                except Exception as e:
                    wxlog.debug("back_to_chat_tab 抛错：%s", type(e).__name__)
            if lst is None:
                return give_up(
                    "消息列表没渲染出来，原图取不到：会话 %r 大概率没打开"
                    "（主窗停在朋友圈页且点回「微信」标签也没能恢复，"
                    "或 user 传成了显示名导致搜索不命中）" % (chat_name or user,))
            inp = WinInput()

            lst_rect = lst.BoundingRectangle
            wxlog.debug("消息列表 rect=(%d,%d,%d,%d) ChatWith=%s",
                        lst_rect.left, lst_rect.top, lst_rect.right,
                        lst_rect.bottom, opened)
            # 「哪一行才是这条图」：文本行的 Name 就是真实正文，所以整段可视行序列可以和
            # 数据库对齐 → 直接点名目标行；目标不在可视区时按数据库算出的行差**滚过去**
            # 再认。对不齐（或并列偏移给不出唯一答案）才退回「数它下面压了几张更新的图」。
            deadline = time.time() + max(3.0, float(timeout))
            db_seq = []
            try:
                recent = self.db.get_messages(user, limit=400)       # 降序
                db_seq = [{"kind": self._DB_ROW_KIND.get(r.get("type") or ""),
                           "content": r.get("content") or "",
                           "local_id": r.get("local_id")}
                          for r in reversed(recent)]                 # 自旧到新
                db_seq = [d for d in db_seq if d["kind"]]
            except Exception as e:
                wxlog.debug("取历史消息失败（%s），只能按图片计数尝试", type(e).__name__)
            target_ch = None
            note = "no-db"
            if db_seq:
                try:
                    target_ch, note = self._locate_image_row(
                        local_id, lst, db_seq, _uia, hwnd, pid,
                        min(deadline, time.time() + max(3.0, float(timeout) * 0.5)),
                        max_scrolls=max(0, int(max_scrolls)) if scroll_ok else 0)
                except Exception as e:
                    wxlog.debug("定位图片行抛错（%s），退回按图片计数", type(e).__name__)
                    note = "error"
            ui = self._visible_rows(lst)     # 滚过一轮之后可视区已经变了，重读
            images = [_c for k, _n, _c in ui if k == "image"]
            wxlog.debug("可视消息行 %d 行（图片行 %d 行）定位结果=%s ChatWith=%s",
                        len(ui), len(images), note, opened)
            if not images:
                # RecyclerListView 是虚拟化的，只实例化可视区那十来行；滚过一轮还是扫不到
                # 就只报不猜。
                return give_up("可视区里没有图片气泡（定位结果：%s）：消息表有这条图，"
                               "但它没渲染出来——把窗口滚到那条消息附近再试" % note)
            if target_ch is not None and any(target_ch is c for _k, _n, c in ui):
                order = [target_ch] + [c for c in images if c is not target_ch]
                wxlog.debug("按数据库行序列认出目标：可视第 %d/%d 行（比对窗口 %d 行）",
                            [c for _k, _n, c in ui].index(target_ch) + 1,
                            len(ui), len(db_seq))
            else:
                n_newer = self._newer_image_count(user, row.get("sort_seq"))
                order = self._order_bubbles(
                    [(ch, ch.BoundingRectangle.top) for ch in images], n_newer)
                wxlog.debug("没认出具体哪一行（%s）：目标下面有 %d 张更新的图，可视气泡 %d 个，"
                            "按此排定尝试顺序", note, n_newer, len(order))
            is_self = self._sent_by_self(row)
            # 屏幕上已经开着预览窗时，「有没有新窗口」这个判据会失真（实测撞上过一次
            # 假成功），所以记下已有句柄，只把**新出现**的窗口算作点开。
            before = {h for h, _w in self._preview_windows()}
            if before:
                wxlog.warning("屏幕上已有 %d 个预览窗：本轮只把新出现的窗口算作点开成功"
                              "（先手动关掉再跑，判据更干净）", len(before))

            for img_ch in order:
                if time.time() >= deadline:
                    wxlog.warning("原件取不到：等待超过 timeout=%.0fs，还剩 %d 个气泡没试",
                                  timeout, len(order))
                    break
                # 点下一张之前先回头看一眼本机：微信常常在我们点上一张之后才把文件写完。
                # 不查这一步的话就是「明明已经拿到了还在一张一张瞎点，最后报失败」。
                done = self._harvest(user, md5, local_id, save_dir, aes_key, xor_key,
                                     min_bytes=min_bytes,
                                     note="（点下一张之前查到的，不再多点了）")
                if done:
                    return done
                r = img_ch.BoundingRectangle
                cy = int((r.top + r.bottom) / 2)
                preview_win = btn = None
                # 行矩形是**整行宽**（实测 2598px），缩略图只占其中一小块，而且
                # 自己发的图气泡在右边——先点该中的那一侧，没点开再试另一侧。
                for cx in self._bubble_click_xs(r, is_self):
                    if time.time() >= deadline:
                        break
                    wxlog.debug("点击图缩略图 (%d,%d) 行=(%d,%d,%d,%d) 自己发的=%s",
                                cx, cy, r.left, r.top, r.right, r.bottom, is_self)
                    inp.real_click(cx, cy)
                    until = min(deadline, time.time() + 8.0)
                    while time.time() < until:
                        fresh = [(h, w) for h, w in self._preview_windows()
                                 if h not in before]
                        for _h, w in fresh:
                            b = self._find_preview_button(
                                w, (self.ZOOM_REQUEST, self.ZOOM_SHOWN))
                            if b is not None:
                                preview_win, btn = w, b
                                break
                            if preview_win is None:
                                preview_win = w    # 窗先出来、按钮可能还在渲染
                        if btn is not None:
                            break
                        time.sleep(0.5)
                    if preview_win is not None:
                        break
                    if time.time() < deadline:
                        wxlog.debug("这一侧没点开，试该行的另一侧")

                if preview_win is None:
                    wxlog.debug("点击后没出现新的预览窗（点偏了 / 点的不是这张图），换下一个气泡")
                    continue
                # 缩放键只在「当前是适应窗口、点一下要看原始大小」时才有意义；
                # Name 已经是「图片适应窗口大小」说明原件就在显示中，再点只会缩回去。
                zoom_name = (btn.Name or "").strip() if btn is not None else ""
                if zoom_name == self.ZOOM_REQUEST:
                    try:
                        btn.Click()
                    except Exception:
                        from wechatauto import rhythm
                        btn_r = btn.BoundingRectangle
                        zx, zy = rhythm.point((btn_r.left, btn_r.top,
                                               btn_r.right, btn_r.bottom))
                        inp.real_click(zx, zy)
                else:
                    wxlog.debug("预览窗缩放键状态=%r（不是「%s」），不点它，等本机出现那一档",
                                zoom_name or "没有这颗按钮", self.ZOOM_REQUEST)

                got = self._wait_tier(user, md5, min_bytes,
                                      min(deadline, time.time() + max(5.0, timeout / 3.0)),
                                      tiers=("original",))
                if got:
                    wxlog.debug("已拿到原件那一档：%s（%d KB）", got[1], got[2] // 1024)
                    path_got = got[1]
                    clicked = True
                    break
                # 等档没等到 ≠ 本机没有：微信经常在超时那一瞬间才把文件写完。
                # 先再查一次盘，别急着去点「保存」（点出来的常常只是预览图那一幅）。
                done2 = self._harvest(user, md5, local_id, save_dir, aes_key, xor_key,
                                      min_bytes=min_bytes,
                                      note="（等档超时之后查到的）")
                if done2:
                    return done2
                # 「图片原始大小」没让要的那一档出现。它本质是查看器的一颗按钮，
                # 在有些图上只是切显示缩放、并不触发下载——退一步点预览窗的「保存」，
                # 收微信自己写出的明文文件兜底。
                saved = self._save_via_button(
                    preview_win, save_dir, "%s_%s" % (user, local_id),
                    deadline, time.time(),
                    ref_thumb=(self._image_files(user, md5).get('thumb') or (0, 0))[1],
                    ref_mid=mid_sz)
                if saved:
                    saved_plain = saved
                    clicked = True
                    break
                wxlog.debug("点过之后要的那一档仍没出现（或还是半截文件），换下一个气泡")
        except Exception as e:
            wxlog.warning("原图流程异常：%s: %s（本轮按取不到处理）",
                          type(e).__name__, str(e)[:200])

        if not clicked:
            st = self.image_status(user, local_id)
            return give_up("原件取不到：本机档位=%s reason=%s；"
                           "可能是气泡不在可视区、预览窗没弹、或微信侧没回数据",
                           st.get('tiers'), st.get('reason'))
        if saved_plain:
            return saved_plain
        if not path_got:
            return give_up("点过界面但没拿到文件路径：%s/%s", user, local_id)
        # 解密 _wait_tier 交回来的那一档，文件名保持老样子，不加档位后缀。
        return self._write_decrypted(self.decrypt_image(path_got, aes_key, xor_key),
                                     "%s_%s" % (user, local_id), save_dir)

    def download_media(self, user: str, local_id: int, save_dir: Optional[str] = None) -> Optional[str]:
        """按消息类型自动分发：3 图片 / 34 语音 / 43 视频 / 49 文件。

        跨分片下 local_id 可能对应多类型，逐个尝试下载直到成功。
        分发时会带上候选行的 ``shard``，所以拿到的是候选行本身而不是别的分片的同号行。
        """
        rows = self.db.get_message_rows_for_media(user, local_id)
        for row in rows:
            t = row["local_type"]
            # 带上候选行的 shard：同号跨分片时不指定分片就会取到别的那一条
            sd = row.get("shard") or None
            if t == 3:
                return self.download_image(user, local_id, save_dir, shard=sd)
            if t == 34:
                return self.download_voice(user, local_id, save_dir, shard=sd)
            if t == 43:
                return self.download_video(user, local_id, save_dir, shard=sd)
            if t == 49:
                return self.download_file(user, local_id, save_dir, shard=sd)
        return None
