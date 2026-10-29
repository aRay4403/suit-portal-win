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

from .askdlg import ask_service
from .auth import check_online, do_auth
from .config import ConfigStore
from .httpc import local_ip_for

FAST_RETRY_LIMIT = 12          # 前 N 次失败保持快速重试
CANCEL_BACKOFF = 60            # 用户取消选网后，多久再问一次


@dataclass
class Status:
    state: str = "idle"              # idle / online / offline / authing / asking / disabled / error
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
        # 「弹窗正开着」的标记：只要它在，后台循环一律不检测、不认证
        self._asking = threading.Event()

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
    def _ask_service(self, cfg) -> bool:
        """弹原生对话框问「这次连哪一个网」。

        返回 False = 没有选择（取消 / 已有弹窗），本轮不认证。

        【为什么放在认证之前、而不是启动时】
        这一版的要求是"每次都问"。启动问一次的话，掉线自动重连时用的还是
        那个旧选择，等于没问；所以挂在每次认证的入口上，掉线重连、手动点
        「立即认证」都会先问一句。

        【为什么弹窗期间要暂停后台检测】
        窗口没有倒计时、必须人手动选，可能挂很久。如果后台循环还在旁边
        探测外网、甚至抢着认证，就会出现"我还没选，它已经连上了"这种
        见鬼行为。所以弹窗一开就置 _asking，主循环看到它就只等不动。
        """
        if not cfg.ask_service:
            return True

        options = [s for s in cfg.service_options.split() if s]
        if not options:
            self.log("弹窗候选为空，跳过询问")
            return True

        if self._asking.is_set():
            self.log("已经有一个选网弹窗在等你，本轮不重复弹")
            return False

        default = cfg.last_service or cfg.service
        self.log("弹窗询问要连哪一个网（必须手动选，不选就不认证）")

        self._asking.set()
        try:
            self._set(state="asking", message="等待你选择要连的网（后台检测已暂停）")
            res = ask_service(options, default=default)
        finally:
            self._asking.clear()

        if res.error:
            # 弹不出来（远程桌面断开会话、非交互桌面等）不能把认证卡死
            self.log("弹窗不可用（%s），直接沿用「%s」" % (res.error, default))
            return True

        if res.cancelled or not res.choice:
            self.log("已取消选择，本轮不认证")
            return False

        self.log("已选择：%s" % res.choice)

        # 记住这次的选择：下次弹窗默认选它
        self.store.update({"service": res.choice, "last_service": res.choice})
        cfg.service = res.choice
        cfg.last_service = res.choice
        return True

    # ------------------------------------------------------------------
    def _sleep(self, seconds: int) -> None:
        self._wake.wait(timeout=seconds)
        self._wake.clear()

    def _loop(self) -> None:
        fail = 0
        self.log("守护进程已启动")
        while not self._stop.is_set():
            cfg = self.store.snapshot()

            # 弹窗开着就什么都不做：不探外网、不认证、不重试。
            # （用户手动点「立即认证」触发的弹窗，也是靠这里挡住后台循环的）
            if self._asking.is_set():
                self._set(state="asking", message="等待你选择要连的网（后台检测已暂停）")
                self._sleep(1)
                continue

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

            # ---- 2) 离线 -> 先问连哪个网，再认证 ----
            self._set(state="offline", message="离线，正在认证...")
            self.log("离线，开始认证...")

            if not self._ask_service(cfg):
                self._set(state="offline", message="已取消选择，%d 秒后再问" % CANCEL_BACKOFF,
                          next_wait=CANCEL_BACKOFF)
                self._sleep(CANCEL_BACKOFF)
                continue

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
    def preview_ask(self) -> dict:
        """界面上的「试一下选网弹窗」：只弹不保存。

        走和真实认证同一条路（同样置 _asking），这样"弹窗期间暂停后台检测"
        在预览时也成立 —— 否则用户点试弹窗，后台还在旁边探外网，行为不一致。
        """
        cfg = self.store.snapshot()
        options = [s for s in cfg.service_options.split() if s]
        if not options:
            return {"ok": False, "message": "候选列表是空的"}
        if self._asking.is_set():
            return {"ok": False, "message": "已经有一个选网弹窗在等你了"}

        prev = self.status.state
        self._asking.set()
        try:
            self._set(state="asking", message="等待你选择要连的网（后台检测已暂停）")
            res = ask_service(options, default=cfg.last_service or cfg.service)
        finally:
            self._asking.clear()
            self._set(state=prev)

        if res.error:
            return {"ok": False, "message": "弹窗打不开：" + res.error}
        if res.cancelled:
            return {"ok": False, "message": "已取消（这次只是试弹，没改设置）"}
        return {"ok": True, "message": "选了「%s」" % res.choice}

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

                if not self._ask_service(cfg):
                    self._set(state="offline", message="已取消选择要连的网",
                              manual_busy=False)
                    return {"ok": False, "message": "已取消选择要连的网"}

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
