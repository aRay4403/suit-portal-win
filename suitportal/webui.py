"""本地 Web 界面服务。

为什么用本地网页当界面，而不是 tkinter：
    这台机器上的 Python 是精简版，没带 tkinter；而且网页界面在
    高分屏、缩放、字体上表现更好，也方便以后改成手机看。
    服务只监听 127.0.0.1，外面访问不到。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import autostart
from .assets import render_index
from .config import ConfigStore, log_path
from .monitor import LogBuffer, Monitor

HOST = "127.0.0.1"
DEFAULT_PORT = 8792          # 和原版（8791）错开，两版可以同时开，互不干扰
APP_ID = "SuitPortalAsk"     # /api/state 里的身份标记，避免误认成原版实例


class SingleInstanceServer(ThreadingHTTPServer):
    """保证同一端口只能被一个进程占用。

    【为什么必须特殊处理 · 实机踩坑】
    HTTPServer 默认 allow_reuse_address = 1，在 Windows 上它对应 SO_REUSEADDR，
    而 Windows 的语义和 Linux 不一样：**它允许另一个进程绑到同一个端口上**。
    后果很严重 —— 用户重复双击图标，会起第二个守护进程，两个进程同时去认证，
    互相踢会话，表现成「时好时坏」。

    所以这里关掉 SO_REUSEADDR，改用 Windows 专有的 SO_EXCLUSIVEADDRUSE
    （必须在 bind 之前设置）。这样第二次 bind 会正常抛 WSAEADDRINUSE，
    上层据此判断「已经有实例在跑」。
    """

    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        super().server_bind()


class App:
    """把配置、日志、守护进程串起来，供 HTTP 层调用。"""

    def __init__(self) -> None:
        self.store = ConfigStore()
        self.logs = LogBuffer(log_path())
        self.monitor = Monitor(self.store, self.logs)
        self.shutdown_cb = None

    def start(self) -> None:
        self.monitor.start()

    def stop(self) -> None:
        self.monitor.stop()


class Handler(BaseHTTPRequestHandler):
    server_version = "SuitPortalAsk/1.0"
    app: App = None          # 由 serve() 注入

    # -- 基础输出 ------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:                                         # noqa: BLE001
            return {}

    def log_message(self, fmt, *args) -> None:                    # noqa: A003
        pass                                                      # 别刷控制台

    # -- 路由 ----------------------------------------------------------
    def do_GET(self) -> None:                                     # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, render_index().encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/config":
            self._json(self.app.store.get().to_public_dict())
        elif path == "/api/state":
            st = self.app.monitor.snapshot_status()
            st["logs"] = self.app.logs.tail(200)
            st["app"] = APP_ID
            self._json(st)
        elif path == "/api/autostart":
            self._json({"enabled": autostart.is_enabled()})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:                                    # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/config":
                self._json(self._save_config())
            elif path == "/api/auth":
                force = bool(self._body().get("force"))
                self._json(self.app.monitor.auth_now(force=force))
            elif path == "/api/ask/test":
                self._json(self._test_ask())
            elif path == "/api/autostart":
                want = bool(self._body().get("enabled"))
                ok, msg = autostart.enable() if want else autostart.disable()
                self._json({"ok": ok, "message": msg})
            elif path == "/api/logs/clear":
                self.app.logs.clear()
                self._json({"ok": True})
            elif path == "/api/openlog":
                self._open_log()
                self._json({"ok": True})
            elif path == "/api/quit":
                self._json({"ok": True})
                threading.Timer(0.3, self.app.shutdown_cb or (lambda: None)).start()
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception as exc:                                  # noqa: BLE001
            self._json({"ok": False, "message": "%s: %s" % (type(exc).__name__, exc)}, 500)

    # -- 具体动作 ------------------------------------------------------
    def _save_config(self) -> dict:
        patch = self._body()

        # 密码留空 = 不修改，避免用户只改运营商时把密码清掉
        if not patch.get("password"):
            patch.pop("password", None)

        # 数字字段做范围收敛，防止前端传个离谱值把守护循环卡死
        limits = {
            "interval": (5, 600), "interval_offline": (1, 300),
            "interval_offline_max": (5, 600), "timeout": (3, 60),
        }
        for key, (lo, hi) in limits.items():
            if key in patch:
                try:
                    patch[key] = max(lo, min(hi, int(patch[key])))
                except (TypeError, ValueError):
                    patch.pop(key)

        for key in ("username", "service", "portal_host", "entry_url",
                    "probe_urls", "online_check_urls", "last_service",
                    "service_options"):
            if key in patch and isinstance(patch[key], str):
                patch[key] = patch[key].strip()

        cfg = self.app.store.update(patch)
        self.app.monitor.wake()          # 配置变了，让循环立刻用新值重来
        return cfg.to_public_dict()

    def _test_ask(self) -> dict:
        """界面上的「试一下弹窗」：只弹不保存，让用户先看看长什么样。"""
        return self.app.monitor.preview_ask()

    def _open_log(self) -> None:
        path = log_path()
        if not path.exists():
            path.write_text("", encoding="utf-8")
        if os.name == "nt":
            os.startfile(str(path))      # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(path)])


def serve(port: int = DEFAULT_PORT, app: App | None = None):
    """启动服务。端口被占用时返回 None（说明已经有一个实例在跑）。"""
    app = app or App()
    Handler.app = app
    try:
        httpd = SingleInstanceServer((HOST, port), Handler)
    except OSError:
        return None
    httpd.daemon_threads = True
    return httpd, app
