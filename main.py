"""SUIT 校园网自动认证 —— Windows 版入口。

用法：
    SuitPortal.exe            打开界面（双击就是这个）
    SuitPortal.exe --daemon   后台静默运行，不弹浏览器（开机自启用这个）

单实例：如果端口已被占用，说明已经有一个在跑，
直接把浏览器指向它，不会再起第二个守护进程去重复认证。
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

# 允许直接 `python main.py` 跑，也允许被 PyInstaller 打包后跑
sys.path.insert(0, str(Path(__file__).resolve().parent))

from suitportal import webui                                  # noqa: E402
from suitportal.config import log_path                        # noqa: E402

# 端口被占用时往后找的候选范围
PORT_SCAN = 10


def _is_our_instance(port: int, timeout: float = 0.6) -> bool:
    """判断这个端口上跑的是不是本程序。

    只判断「端口通不通」是不够的：8791 完全可能被别的软件占着，
    那时盲目打开浏览器会把用户带到一个无关的页面，看起来像程序坏了。
    这里请求 /api/state，能解析出含 state 字段的 JSON 才认定是自己人。
    """
    try:
        url = "http://127.0.0.1:%d/api/state" % port
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return isinstance(data, dict) and "state" in data
    except Exception:                                             # noqa: BLE001
        return False


def _port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _find_running(start: int) -> int | None:
    """在候选范围内找已经在跑的实例。"""
    for port in range(start, start + PORT_SCAN):
        if _is_our_instance(port):
            return port
    return None


def _find_free(start: int) -> int | None:
    for port in range(start, start + PORT_SCAN):
        if not _is_our_instance(port) and _port_free(port):
            return port
    return None


def _log_startup_error(exc: BaseException) -> None:
    """没有控制台时，异常只能写文件，否则用户什么都看不到。"""
    try:
        with log_path().open("a", encoding="utf-8") as fh:
            fh.write("[%s] 启动失败: %s\n%s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), exc,
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            ))
    except Exception:                                             # noqa: BLE001
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SUIT 校园网自动认证")
    parser.add_argument("--daemon", action="store_true",
                        help="后台静默运行，不打开浏览器（开机自启用）")
    parser.add_argument("--port", type=int, default=webui.DEFAULT_PORT)
    args = parser.parse_args(argv)

    # ---- 已有实例在跑？----
    # 重复双击时不再起第二个守护进程（否则两个进程同时认证会互相踢会话）。
    # 直接复用已有实例：双击就开浏览器指过去，后台自启模式则静默退出。
    running = _find_running(args.port)
    if running is not None:
        if not args.daemon:
            webbrowser.open("http://127.0.0.1:%d/" % running)
        return 0

    # ---- 选一个真正能用的端口 ----
    # 默认端口可能被别的软件占着，那就往后找；找不到就报错退出，
    # 绝不硬起一个半残的服务。
    port = _find_free(args.port)
    if port is None:
        _log_startup_error(
            RuntimeError("端口 %d~%d 全被占用，无法启动"
                         % (args.port, args.port + PORT_SCAN - 1))
        )
        return 1

    url = "http://127.0.0.1:%d/" % port

    result = webui.serve(port)
    if result is None:
        # 极端竞态：探测时还空着，绑定时被抢了
        if not args.daemon:
            webbrowser.open(url)
        return 0

    httpd, app = result

    def shutdown() -> None:
        app.stop()
        httpd.shutdown()

    app.shutdown_cb = shutdown
    app.start()

    if not args.daemon:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        try:
            httpd.server_close()
        except Exception:                                         # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:                                  # noqa: BLE001
        _log_startup_error(exc)
        raise
