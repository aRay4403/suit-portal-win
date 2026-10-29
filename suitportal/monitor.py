"""守护进程：自适应间隔 + 连续失败退避。

调度逻辑照搬原版 /etc/init.d/suit-portal 里的那段 shell 循环：

    在线            -> 每 interval 秒检查一次
    掉线 / 认证失败 -> 每 interval_offline 秒快速重试（默认 5 秒）
    连续失败超过 12 次 -> 逐步退避，上限 interval_offline_max（默认 60 秒）

退避很重要：长时间连不上时如果还每 5 秒砸一次认证服务器，容易被风控。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .auth import check_online, do_auth
from .config import ConfigStore
from .httpc import local_ip_for

FAST_RETRY_LIMIT = 12          # 前 N 次失败保持快速重试


@dataclass
class Status:
    state: str = "idle"              # idle / online / offline / authing / disabled / error
    message: str = ""
    local_ip: str = ""
    last_check: float = 0.0
    last_auth: float = 0.0
    last_auth_ok: bool = False
    fail_count: int = 0
    next_wait: int = 0
    auth_count: int = 0
    manual_busy: bool = False

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "message": self.message,
            "local_ip": self.local_ip,
            "last_check": self.last_check,
            "last_auth": self.last_auth,
            "last_auth_ok": self.last_auth_ok,
            "fail_count": self.fail_count,
            "next_wait": self.next_wait,
            "auth_count": self.auth_count,
            "manual_busy": self.manual_busy,
        }


class LogBuffer:
    """环形日志：最近 N 条给界面看，同时落盘（超过 64KB 只留尾部）。"""

    MAX_LINES = 400
    MAX_FILE = 65536
    KEEP_FILE = 16384

    def __init__(self, path) -> None:
        self.path = path
        self._lines: list[tuple[float, str]] = []
        self._lock = threading.Lock()

    def add(self, msg: str) -> None:
        now = time.time()
        with self._lock:
            self._lines.append((now, msg))
            if len(self._lines) > self.MAX_LINES:
                del self._lines[:-self.MAX_LINES]
            self._write(msg)

    def _write(self, msg: str) -> None:
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            line = "[%s] %s\n" % (stamp, msg)
            if self.path.exists() and self.path.stat().st_size > self.MAX_FILE:
                tail = self.path.read_bytes()[-self.KEEP_FILE:]
                self.path.write_bytes(tail)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception:                                        # noqa: BLE001
            pass

    def tail(self, n: int = 200) -> list[dict]:
        with self._lock:
            return [{"t": t, "m": m} for t, m in self._lines[-n:]]

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()


class Monitor:
    def __init__(self, store: ConfigStore, logs: LogBuffer) -> None:
        self.store = store
        self.logs = logs
        self.status = Status()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._auth_lock = threading.Lock()

    # ------------------------------------------------------------------
    def log(self, msg: str) -> None:
        self.logs.add(msg)

    def _set(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self.status, k, v)

    def snapshot_status(self) -> dict:
        with self._lock:
            return self.status.as_dict()

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        """让循环立刻醒来（用户改了配置 / 点了立即认证）。"""
        self._wake.set()

    # ------------------------------------------------------------------
    def _sleep(self, seconds: int) -> None:
        self._wake.wait(timeout=seconds)
        self._wake.clear()

    def _loop(self) -> None:
        fail = 0
        self.log("守护进程已启动")
        while not self._stop.is_set():
            cfg = self.store.snapshot()

            if not cfg.enabled:
                self._set(state="disabled", message="自动认证已关闭")
                self._sleep(3)
                continue

            if not cfg.username or not cfg.password:
                self._set(state="error", message="未配置账号或密码")
                self._sleep(10)
                continue

            # ---- 1) 先看在线 ----
            self._set(state="checking", message="检测网络状态...")
            online = check_online(cfg, self.log)
            ip = local_ip_for(cfg.portal_host.split(":")[0], 80)
            self._set(last_check=time.time(), local_ip=ip)

            if online:
                if fail:
                    self.log("网络已恢复在线")
                fail = 0
                self._set(state="online", message="在线", fail_count=0, next_wait=cfg.interval)
                self._sleep(cfg.interval)
                continue

            # ---- 2) 离线 -> 认证 ----
            self._set(state="offline", message="离线，正在认证...")
            self.log("离线，开始认证...")
            with self._auth_lock:
                self._set(state="authing")
                result = do_auth(cfg, self.log)
            self._set(last_auth=time.time(), last_auth_ok=result.ok, auth_count=self.status.auth_count + 1)

            if result.ok:
                fail = 0
                self._set(state="online", message=result.message, fail_count=0, next_wait=cfg.interval)
                self._sleep(cfg.interval)
                continue

            # ---- 3) 失败 -> 快速重试，超限后退避 ----
            fail += 1
            if fail <= FAST_RETRY_LIMIT:
                wait = max(1, cfg.interval_offline)
            else:
                wait = cfg.interval_offline * (fail - FAST_RETRY_LIMIT + 1)
                wait = min(wait, max(cfg.interval_offline, cfg.interval_offline_max))
            self._set(
                state="offline",
                message="认证失败：%s" % result.message,
                fail_count=fail,
                next_wait=wait,
            )
            if fail == 1:
                self.log("认证失败，先每 %d 秒重试，连续失败后逐步退避到 %d 秒"
                         % (cfg.interval_offline, cfg.interval_offline_max))
            elif fail == FAST_RETRY_LIMIT + 1:
                self.log("已连续失败 %d 次，开始退避（当前间隔 %d 秒）" % (fail, wait))
            self._sleep(wait)

        self._set(state="idle", message="守护进程已停止")
        self.log("守护进程已停止")

    # ------------------------------------------------------------------
    def auth_now(self, force: bool = False) -> dict:
        """界面上的「立即认证」/「强制重新认证」。

        force=False 时先查在线，已在线就跳过 —— 和原版 --auth 一致。
        为什么不让它无脑强制：已经在线时再跑一遍完整流程要十几秒，
        而且 AC 对重复认证常常直接回 ACK_AUTH_REFUSE，看着像失败。
        """
        cfg = self.store.snapshot()
        if not cfg.username or not cfg.password:
            return {"ok": False, "message": "还没填账号或密码"}

        if self._auth_lock.locked():
            return {"ok": False, "message": "上一轮认证还在进行中"}

        with self._auth_lock:
            self._set(manual_busy=True)
            try:
                if not force and check_online(cfg, self.log):
                    self.log("已在线，无需重复认证。")
                    self._set(state="online", message="在线", manual_busy=False,
                              last_check=time.time())
                    return {"ok": True, "message": "已在线，无需重复认证"}

                self.log("手动认证开始%s" % ("（强制）" if force else ""))
                self._set(state="authing")
                result = do_auth(cfg, self.log)
                self._set(
                    last_auth=time.time(), last_auth_ok=result.ok,
                    state="online" if result.ok else "offline",
                    message=result.message, manual_busy=False,
                )
                self._wake.set()     # 让主循环重新评估，别等满一个间隔
                return {"ok": result.ok, "message": result.message}
            finally:
                self._set(manual_busy=False)
