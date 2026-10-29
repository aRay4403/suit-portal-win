"""纯 Python 实现的 AES-128-ECB / PKCS7。

为什么不用 pycryptodome / cryptography：
    这个工具要在同学的 Windows 电脑上「解压即用」，不该让用户先去装 pip 包。
    AES 本身不复杂，标准库 base64 就够了。

对应原版（OpenWrt）里的：
    printf '%s' "$plain" | openssl enc -aes-128-ecb -K "$key_hex" -nosalt | openssl base64 -A
也就是前端 CryptoJS 的：
    AES.encrypt(plain, Base64.parse(croypto), {mode: ECB, padding: Pkcs7})
"""

from __future__ import annotations

import base64

# --------------------------------------------------------------------------
# 常量表
# --------------------------------------------------------------------------
SBOX = (
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
)

RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


# --------------------------------------------------------------------------
# GF(2^8) 乘法与轮函数
# --------------------------------------------------------------------------
def _xtime(a: int) -> int:
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x1B) & 0xFF
    return a


def _mul(a: int, b: int) -> int:
    """GF(2^8) 上的乘法（AES 用的不可约多项式 0x11B）。"""
    out = 0
    while b:
        if b & 1:
            out ^= a
        a = _xtime(a)
        b >>= 1
    return out


def _add_round_key(state: list[int], rk: list[int]) -> None:
    for i in range(16):
        state[i] ^= rk[i]


def _sub_bytes(state: list[int]) -> None:
    for i in range(16):
        state[i] = SBOX[state[i]]


def _shift_rows(state: list[int]) -> None:
    """state 是列主序：state[4*col + row]。第 row 行循环左移 row 个字节。"""
    src = state[:]
    for row in range(1, 4):
        for col in range(4):
            state[4 * col + row] = src[4 * ((col + row) % 4) + row]


def _mix_columns(state: list[int]) -> None:
    for c in range(4):
        i = 4 * c
        a0, a1, a2, a3 = state[i], state[i + 1], state[i + 2], state[i + 3]
        state[i + 0] = _mul(a0, 2) ^ _mul(a1, 3) ^ a2 ^ a3
        state[i + 1] = a0 ^ _mul(a1, 2) ^ _mul(a2, 3) ^ a3
        state[i + 2] = a0 ^ a1 ^ _mul(a2, 2) ^ _mul(a3, 3)
        state[i + 3] = _mul(a0, 3) ^ a1 ^ a2 ^ _mul(a3, 2)


def _expand_key(key: bytes) -> list[list[int]]:
    """AES-128 密钥扩展：16 字节主密钥 -> 11 组轮密钥（每组 16 字节）。"""
    if len(key) != 16:
        raise ValueError("AES-128 需要 16 字节密钥，实际 %d 字节" % len(key))

    w = [list(key[i * 4:(i + 1) * 4]) for i in range(4)]
    for i in range(4, 44):
        temp = list(w[i - 1])
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]              # RotWord
            temp = [SBOX[b] for b in temp]          # SubWord
            temp[0] ^= RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ temp[j] for j in range(4)])

    round_keys: list[list[int]] = []
    for r in range(11):
        rk: list[int] = []
        for c in range(4):
            rk.extend(w[4 * r + c])
        round_keys.append(rk)
    return round_keys


def _encrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    state = list(block)
    _add_round_key(state, round_keys[0])
    for r in range(1, 10):
        _sub_bytes(state)
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, round_keys[r])
    _sub_bytes(state)
    _shift_rows(state)
    _add_round_key(state, round_keys[10])
    return bytes(state)


# --------------------------------------------------------------------------
# 对外接口
# --------------------------------------------------------------------------
def pkcs7_pad(data: bytes) -> bytes:
    n = 16 - (len(data) % 16)
    return data + bytes([n]) * n


def encrypt_ecb_b64(plaintext: str | bytes, key_b64: str) -> str:
    """AES-128-ECB/PKCS7 加密，返回 base64 字符串。

    参数 key_b64 是服务端下发的 croypto（base64 编码的 16 字节密钥）。
    """
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")

    key = base64.b64decode(key_b64)
    round_keys = _expand_key(key)

    padded = pkcs7_pad(plaintext)
    out = bytearray()
    for i in range(0, len(padded), 16):
        out += _encrypt_block(padded[i:i + 16], round_keys)
    return base64.b64encode(bytes(out)).decode("ascii")
