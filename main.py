"""SUIT 校园网自动认证 · 弹窗选网版 —— Windows 版入口。

和原版的唯一区别：**每次认证前都弹一个原生小窗问你「这次连哪一个网」**，
选完才继续，掉线自动重连、手动点「立即认证」都会问。

用法：
    SuitPortalAsk.exe            打开界面（双击就是这个）
    SuitPortalAsk.exe --daemon   后台静默运行，不弹浏览器（开机自启用这个）
    SuitPortalAsk.exe --test-ask 只弹一次选网框，用于自检

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

# 探测本机实例时必须绕开系统代理。
# 【实机踩坑】装了 Clash / 公司代理的机器会设 http_proxy 环境变量，
# urllib 默认会照着走，于是连 127.0.0.1 也被送去代理，白白等满超时。
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _is_our_instance(port: int, timeout: float = 0.35) -> bool:
    """判断这个端口上跑的是不是本程序。

    只判断「端口通不通」是不够的：8792 完全可能被别的软件占着，
    那时盲目打开浏览器会把用户带到一个无关的页面，看起来像程序坏了。
    这里请求 /api/state，能解析出带本程序标记的 JSON 才认定是自己人。

    超时给得短：真在跑的实例是毫秒级响应，0.35 秒还没回来说明不是它。
    """
    try:
        url = "http://127.0.0.1:%d/api/state" % port
        with _LOCAL_OPENER.open(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # 带 app 标记才算自己人：8792 也可能被别的软件（甚至原版）占着
        return isinstance(data, dict) and data.get("app") == webui.APP_ID
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
    """往后找一个能绑上的端口。

    能 bind 就说明没人在用（本程序用的是 SO_EXCLUSIVEADDRUSE 独占绑定），
    所以这里不需要再发 HTTP 探测 —— 那一步很贵，见 _is_our_instance 的注释。
    """
    for port in range(start, start + PORT_SCAN):
        if _port_free(port):
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


def _test_ask() -> int:
    """自检：只弹一次选网框，把结果写进日志（打包后没有控制台也能看）。"""
    from suitportal.askdlg import ask_service
    from suitportal.config import Config

    cfg = Config.load()
    options = [s for s in cfg.service_options.split() if s]
    res = ask_service(options, default=cfg.last_service or cfg.service)
    line = "选网弹窗自检: %s" % res.as_dict()
    try:
        with log_path().open("a", encoding="utf-8") as fh:
            fh.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except Exception:                                             # noqa: BLE001
        pass
    print(line)
    return 0 if res.choice else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SUIT 校园网自动认证")
    parser.add_argument("--daemon", action="store_true",
                        help="后台静默运行，不打开浏览器（开机自启用）")
    parser.add_argument("--port", type=int, default=webui.DEFAULT_PORT)
    parser.add_argument("--test-ask", action="store_true",
                        help="只弹一次选网对话框并打印结果（自检用，不启动服务）")
    args = parser.parse_args(argv)

    if args.test_ask:
        return _test_ask()

    # ---- 选端口 ----
    # 【性能坑 · 实测】这台机器上连一个没人监听的本地端口不会立刻 RST，
    # 而是静默丢包，于是每次探测都要等满超时。原版先扫 10 个端口再 bind，
    # 启动就白等 6 秒（实测 7.2 秒才可用）。
    # 现在反过来：先 bind 判断，只有目标端口真被占着，才去问一句"是不是自己人"。
    # 正常双击 = 一次 bind，零 HTTP 探测，启动约 1 秒。
    if _port_free(args.port):
        port = args.port
    elif _is_our_instance(args.port):
        # 端口被自己占着 = 已经有一个在跑，直接开浏览器指过去
        if not args.daemon:
            webbrowser.open("http://127.0.0.1:%d/" % args.port)
        return 0
    else:
        # 被别的软件占着：往后找空端口，找不到就报错退出，绝不硬起半残服务
        port = _find_free(args.port + 1)
        if port is None:
            _log_startup_error(
                RuntimeError("端口 %d~%d 全被占用，无法启动"
                             % (args.port, args.port + PORT_SCAN - 1))
            )
            return 1

    url = "http://127.0.0.1:%d/" % port

    result = webui.serve(port)
    if result is None:
        # 极端竞态：刚判断还空着，绑定时被抢了。这时才需要探测是谁
        running = _find_running(port)
        if running is not None:
            if not args.daemon:
                webbrowser.open("http://127.0.0.1:%d/" % running)
            return 0
        _log_startup_error(RuntimeError("端口 %d 绑定失败" % port))
        return 1

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
