"""用 Windows DPAPI 加密保存密码。

原版（OpenWrt）把密码明文放在 /etc/config/suit-portal 里，README 还专门提醒
「建议删除含明文密码的旧配置」。Windows 版没必要照抄这个毛病：
DPAPI（CryptProtectData）能让密文只有**当前 Windows 用户**能解开，
别人拷走 config.json 也读不出密码。

如果 DPAPI 不可用（非 Windows / ctypes 被限制），退化成带标记的明文，
至少保证程序还能用，并在日志里说明。
"""

from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

_PLAIN_PREFIX = "plain:"
_DPAPI_PREFIX = "dpapi:"

_IS_WINDOWS = sys.platform == "win32"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _blob_bytes(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _crypt(data: bytes, protect: bool) -> bytes | None:
    if not _IS_WINDOWS:
        return None
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
    except Exception:
        return None

    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    blob_in = _blob(data)
    blob_out = _DataBlob()
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        return None
    try:
        return _blob_bytes(blob_out)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def encrypt_password(plain: str) -> str:
    """明文密码 -> 可写进 config.json 的字符串。"""
    if not plain:
        return ""
    enc = _crypt(plain.encode("utf-8"), protect=True)
    if enc is None:
        return _PLAIN_PREFIX + plain
    return _DPAPI_PREFIX + base64.b64encode(enc).decode("ascii")


def decrypt_password(stored: str) -> str:
    """config.json 里的字符串 -> 明文密码。"""
    if not stored:
        return ""
    if stored.startswith(_PLAIN_PREFIX):
        return stored[len(_PLAIN_PREFIX):]
    if stored.startswith(_DPAPI_PREFIX):
        raw = base64.b64decode(stored[len(_DPAPI_PREFIX):])
        dec = _crypt(raw, protect=False)
        if dec is None:
            # 换了 Windows 用户 / 换了机器就会解不开，这是 DPAPI 的设计
            raise ValueError(
                "密码无法解密：可能换过 Windows 用户或换过电脑。请在设置里重新输入密码。"
            )
        return dec.decode("utf-8")
    # 兼容手工填的明文
    return stored
