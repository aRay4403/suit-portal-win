"""生成 SuitPortal 的 .ico 图标（零第三方依赖）。

思路：用系统自带的 Edge/Chrome 无头模式把 SVG 渲染成各尺寸 PNG，
再手工拼装成 ICO 容器。

为什么不直接用 Pillow：
    1) 这个项目一直坚持零依赖，图标生成工具也不该破例；
    2) 环境里装 Pillow 要下载第三方包，慢且不一定成功；
    3) 浏览器渲染 SVG 的字体抗锯齿比手工画点阵好得多，
       而且每个尺寸都是原生渲染，不是缩放大图。

输出：
    suit.ico          —— 多尺寸 ICO（16/24/32/48/64/128/256）
    icon_preview.png  —— 各尺寸拼版预览
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

SIZES = [16, 24, 32, 48, 64, 128, 256]

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str:
    for p in BROWSERS:
        if Path(p).exists():
            return p
    found = shutil.which("msedge") or shutil.which("chrome")
    if found:
        return found
    raise SystemExit("找不到 Edge / Chrome，无法渲染图标")


def svg(size: int) -> str:
    """按尺寸生成 SVG。小尺寸用更粗的字重和描边，避免糊成一团。"""
    # 字号比例：小图标要相对更大，笔画才够粗、才认得出
    if size <= 20:
        ratio, stroke_k = 0.88, 0.030
    elif size <= 32:
        ratio, stroke_k = 0.80, 0.024
    elif size <= 48:
        ratio, stroke_k = 0.72, 0.018
    else:
        ratio, stroke_k = 0.66, 0.0
    font_size = size * ratio
    stroke = size * stroke_k
    radius = size * 0.22
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}"
     viewBox="0 0 {size} {size}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#3d8bfd"/>
      <stop offset="1" stop-color="#7c4dff"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="{size}" height="{size}" rx="{radius}" ry="{radius}" fill="url(#g)"/>
  <text x="{size/2}" y="{size*0.5}" font-family="Segoe UI, Arial, sans-serif"
        font-size="{font_size}" font-weight="700" fill="#ffffff"
        stroke="#ffffff" stroke-width="{stroke}"
        text-anchor="middle" dominant-baseline="central">S</text>
</svg>"""


def render_png(browser: str, size: int, out: Path, workdir: Path) -> None:
    html = workdir / f"icon_{size}.html"
    html.write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:transparent;"
        "overflow:hidden}svg{display:block}</style></head><body>"
        + svg(size) + "</body></html>",
        encoding="utf-8",
    )
    subprocess.run(
        [
            browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--default-background-color=00000000",
            f"--window-size={size},{size}",
            f"--screenshot={out}",
            "--virtual-time-budget=1500",
            html.as_uri(),
        ],
        check=False, capture_output=True, timeout=90,
    )
    if not out.exists():
        raise SystemExit("渲染 %dpx 失败" % size)


def png_to_dib(png_path: Path) -> bytes:
    """把 PNG 转成 ICO 内嵌用的 DIB（BITMAPINFOHEADER + BGRA + AND 掩码）。

    【为什么必须转】ICO 文件里允许用 PNG 压缩数据，Windows 资源管理器也能显示；
        但 PyInstaller 是把这个图作为 RT_ICON 资源写进 exe 的，
        UpdateResource 对 RT_ICON 只认传统 DIB 格式，塞 PNG 会直接报错。
        实测就是这么失败的：exe 只出来 300KB（只剩 bootloader，归档没写进去）。
    """
    from PIL import Image   # 仅构建图标时需要，程序运行时不用

    img = Image.open(png_path).convert("RGBA")
    w, h = img.size
    px = img.load()

    # 32 位 BGRA，行序自下而上（BMP 约定）
    rows = []
    for y in range(h - 1, -1, -1):
        row = bytearray()
        for x in range(w):
            r, g, b, a = px[x, y]
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    pixels = b"".join(rows)

    # biHeight 要写两倍：图像本身 + AND 掩码
    header = struct.pack(
        "<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0
    )
    # AND 掩码：32 位图标里仍必须存在，全 0 表示不透明；每行补齐到 4 字节
    mask_row = ((w + 31) // 32) * 4
    mask = b"\x00" * (mask_row * h)
    return header + pixels + mask


def build_ico(path: Path, items: list[tuple[int, bytes]]) -> None:
    """手工拼 ICO 容器：ICONDIR + 每尺寸 ICONDIRENTRY + 图像数据。"""
    header = struct.pack("<HHH", 0, 1, len(items))
    offset = len(header) + 16 * len(items)
    entries = b""
    for size, data in items:
        w = 0 if size >= 256 else size     # ICO 规范里 0 代表 256
        entries += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(header + entries + b"".join(d for _, d in items))
    print("已生成 %s（%d 个尺寸，%d 字节）" % (path.name, len(items), path.stat().st_size))


def build_preview(path: Path, png_paths: dict[int, Path]) -> None:
    """拼一张 HTML 预览再截图，方便肉眼核对各尺寸观感。"""
    browser = find_browser()
    path = path.resolve()          # as_uri() 只接受绝对路径
    cells = "".join(
        f"<div class=c><img src='{png_paths[s].as_uri()}' width='{s}' height='{s}'>"
        f"<span>{s}px</span></div>"
        for s in SIZES
    )
    zoom = "".join(
        f"<div class=c><img src='{png_paths[s].as_uri()}' width='128' height='128'"
        f" style='image-rendering:pixelated'><span>{s}px 放大</span></div>"
        for s in (16, 32)
    )
    html = path.with_suffix(".html")
    html.write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
        "body{margin:0;background:#181c22;font-family:Segoe UI;padding:16px;"
        "display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap}"
        ".c{display:flex;flex-direction:column;align-items:center;gap:6px}"
        ".c span{color:#8b96a5;font-size:11px}"
        "</style></head><body>" + cells
        + "<div style='width:24px'></div>" + zoom + "</body></html>",
        encoding="utf-8",
    )
    subprocess.run(
        [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--window-size=1180,360", f"--screenshot={path}",
         "--virtual-time-budget=2000", html.as_uri()],
        check=False, capture_output=True, timeout=90,
    )
    print("已生成预览 %s" % path.name)


def main() -> None:
    browser = find_browser()
    print("渲染引擎:", browser)
    work = Path(tempfile.mkdtemp(prefix="icon_"))
    png_paths: dict[int, Path] = {}
    items: list[tuple[int, bytes]] = []
    for s in SIZES:
        out = work / f"s{s}.png"
        render_png(browser, s, out, work)
        png_paths[s] = out
        # 256 用 PNG 压缩（体积小得多），其余用 DIB（PyInstaller 写入资源必需）
        data = out.read_bytes() if s >= 256 else png_to_dib(out)
        items.append((s, data))
        print("  %3dpx  %-4s %d 字节" % (s, "PNG" if s >= 256 else "DIB", len(data)))

    build_ico(Path("suit.ico"), items)
    build_preview(Path("icon_preview.png"), png_paths)


if __name__ == "__main__":
    main()
