"""配置读写。

字段和默认值对齐原版 /etc/config/suit-portal（UCI），
这样从路由器版迁过来的人不用重新理解一遍。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import dpapi

APP_NAME = "SuitPortal"


def app_dir() -> Path:
    """配置和日志放哪：%APPDATA%\\SuitPortal。

    不放程序目录，是因为程序可能被放到 Program Files（只读）。
    """
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return app_dir() / "config.json"


def log_path() -> Path:
    return app_dir() / "suit-portal.log"


@dataclass
class Config:
    # ---- 基础认证 ----
    enabled: bool = True                      # 是否启用自动认证守护
    username: str = ""                        # 校园网账号
    password: str = ""                        # 明文密码（仅在内存中；落盘时加密）
    service: str = "电信宽带"                  # 运营商：校园网 / 电信宽带 / 移动宽带 / 联通宽带
    portal_host: str = "10.10.16.56"          # 锐捷 ePortal 地址

    # 门户入口 URL：留空 = 每次认证都去探测（推荐）。
    # 探测到新入口会自动回写，WAN IP 变了也不会失效。
    entry_url: str = ""

    # ---- 探测 / 在线检测 ----
    # 必须用域名：实测纯 IP 在这套网络会 EPERM，且各白等 6 秒超时
    probe_urls: str = (
        "http://connect.rom.miui.com/generate_204 "
        "http://wifi.vivo.com.cn/generate_204"
    )
    online_check_urls: str = (
        "http://connect.rom.miui.com/generate_204 "
        "http://wifi.vivo.com.cn/generate_204 "
        "http://www.baidu.com/"
    )

    # ---- 自适应间隔（秒）----
    interval: int = 30                        # 在线时的检测间隔
    interval_offline: int = 5                 # 掉线时的快速重试间隔
    interval_offline_max: int = 60            # 连续失败后的退避上限

    # ---- HTTP ----
    timeout: int = 6                          # 单个请求超时（秒）

    # ---- 运行时状态（也会持久化，便于下次启动复用）----
    local_ip: str = ""                        # 上次认证时的本机 IP，仅作展示

    # 内部：配置文件路径
    _path: Path = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------------
    def save(self) -> None:
        data = asdict(self)
        data.pop("_path", None)
        data["password"] = dpapi.encrypt_password(self.password)
        path = self._path or config_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls) -> "Config":
        path = config_path()
        cfg = cls()
        cfg._path = path
        if not path.exists():
            return cfg
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return cfg

        valid = {f.name for f in fields(cls) if not f.name.startswith("_")}
        for k, v in raw.items():
            if k in valid:
                setattr(cfg, k, v)

        try:
            cfg.password = dpapi.decrypt_password(raw.get("password", ""))
        except ValueError:
            cfg.password = ""      # 解不开就让用户重填，别让程序起不来

        cfg._path = path
        return cfg

    def to_public_dict(self) -> dict:
        """给前端用的字典：密码不外发，只给一个「是否已设置」的标记。"""
        data = asdict(self)
        data.pop("_path", None)
        data["has_password"] = bool(self.password)
        data.pop("password", None)
        return data


class ConfigStore:
    """带锁的配置容器，后台线程和 Web 请求都会碰它。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cfg = Config.load()

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def get(self) -> Config:
        with self._lock:
            return self._cfg

    def snapshot(self) -> Config:
        """拿一份拷贝，避免后台线程读到一半被前端改掉。"""
        with self._lock:
            data = asdict(self._cfg)
            cfg = Config(**{k: v for k, v in data.items() if k != "_path"})
            cfg._path = self._cfg._path
            return cfg

    def update(self, patch: dict) -> Config:
        with self._lock:
            valid = {f.name for f in fields(Config) if not f.name.startswith("_")}
            for k, v in patch.items():
                if k in valid and v is not None:
                    setattr(self._cfg, k, v)
            self._cfg.save()
            return self._cfg
