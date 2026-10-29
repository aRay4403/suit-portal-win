#!/usr/bin/env python3
"""发布前隐私扫描。

为什么要有这个脚本：
    上一版（路由器版）出过一次事 —— 作者把调试用的真实 POST 报文贴进注释，
    里面同时有 `croypto`（服务端下发的 AES 密钥）和 `password`（用它加密的密文）。
    两者放在一起等价于明文。当时只有 `.gitignore` 挡 `har/` 目录、只有发布包检查工具，
    两者都覆盖不到「被改写后放进源码树的抓包内容」。

    所以这里的规则按**数据类别**扫，不按具体值扫：
      - 密钥/密文同时出现
      - CAS 票据、TGC、execution 令牌
      - 设备 MAC、加密后的入口指纹
      - 个人目录路径
      - 私网 IP（白名单之外）
      - 邮箱 / 手机号 / 身份证号

用法:
    python tools/privacy_scan.py            # 扫当前目录（git 仓库根）
    python tools/privacy_scan.py <目录>

退出码: 0 干净，1 有命中。
"""
from __future__ import annotations

import os
import re
import sys

# 只扫文本，跳过二进制和构建产物
SKIP_DIRS = {".git", "build", "dist", "__pycache__", ".venv", "venv", "node_modules"}
SKIP_EXT = {".exe", ".ico", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".apk",
            ".dll", ".pyd", ".so", ".woff", ".woff2", ".ttf"}

# 白名单：公共服务器 / 示例地址 / 回环，出现是正常的
ALLOW_IPS = {
    "127.0.0.1", "0.0.0.0", "255.255.255.255",
    "10.10.16.56",        # 学校 ePortal 服务器，全校共用
    "114.114.114.114",    # 114 DNS
    "1.1.1.1",            # Cloudflare
    "123.123.123.123",    # 文档里的示例地址
    "144.0.0.0",          # Chrome UA 字符串里的版本号
}

RULES = [
    # (名称, 正则, 严重度)
    ("AES 密钥字面量（croypto=）",
     rb"croypto=[A-Za-z0-9%+/=_-]{16,}", "P0"),
    ("AES 密文字面量（password=）",
     rb"password=[A-Za-z0-9%+/=_-]{16,}", "P0"),
    ("CAS Service Ticket（ST-）",
     rb"ST-\d{6}-[A-Za-z0-9\-]{6,}", "P1"),
    ("CAS TGC 票据",
     rb"SOURCEID_TGC=[A-Za-z0-9._\-]{20,}", "P1"),
    ("execution 执行令牌",
     rb"execution=[A-Za-z0-9+/=_-]{40,}", "P1"),
    ("加密后的入口设备指纹",
     rb"wlanuserip=[0-9a-f]{20,}", "P1"),
    ("设备 MAC 地址",
     rb"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b", "P1"),
    ("Windows 个人目录路径",
     rb"[A-Za-z]:[\\/]{1,4}Users[\\/]{1,4}[A-Za-z0-9_.\-]+", "P1"),
    ("私网 IP",
     rb"(?<![\d.])(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\d.])", "P2"),
    ("邮箱地址",
     rb"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "P2"),
    ("手机号",
     rb"(?<!\d)1[3-9]\d{9}(?!\d)", "P2"),
    ("身份证号",
     rb"(?<!\d)\d{17}[\dXx](?!\d)", "P2"),
]


def iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in SKIP_EXT:
                continue
            yield os.path.join(dirpath, fn)


def main(argv):
    root = argv[1] if len(argv) > 1 else "."
    root = os.path.abspath(root)
    hits = []
    scanned = 0
    for path in iter_files(root):
        try:
            text = open(path, "rb").read()
        except OSError:
            continue
        scanned += 1
        rel = os.path.relpath(path, root).replace("\\", "/")
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, pat, sev in RULES:
                for m in re.finditer(pat, line):
                    value = m.group().decode("utf-8", "replace")
                    if name == "私网 IP" and value in ALLOW_IPS:
                        continue
                    if name == "设备 MAC 地址" and value.lower().startswith(("ff:ff", "00:00")):
                        continue
                    hits.append((sev, name, rel, lineno, value))

    print(f"扫描目录: {root}")
    print(f"文本文件: {scanned} 个")
    if not hits:
        print("结果: 干净，未命中任何规则。")
        return 0

    hits.sort(key=lambda h: (h[0], h[2], h[3]))
    print(f"结果: 命中 {len(hits)} 处\n")
    for sev, name, rel, lineno, value in hits:
        shown = value if len(value) <= 60 else value[:30] + "…（已截断）"
        print(f"  [{sev}] {name}\n        {rel}:{lineno}  {shown}")
    print("\n提示: 密钥和密文同时出现等价于明文；票据类需按会话 ID 处理。")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
