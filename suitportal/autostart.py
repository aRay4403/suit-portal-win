"""开机自启（写 HKCU 的 Run 键）。

为什么用 Run 键而不是任务计划程序：
    不需要管理员权限，用户点一下就能开/关，卸载时删掉一个注册表值就干净了。
    原版在 OpenWrt 上用的是 /etc/init.d + procd 的开机自启，这里是等价物。
"""

from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SuitPortalAsk"      # 和原版（SuitPortal）分开，两版可各自开关自启


def _command() -> str:
    """生成开机要执行的命令行。"""
    if getattr(sys, "frozen", False):
        # 打包成 exe：直接调自己，带 --daemon 表示不弹浏览器
        return '"%s" --daemon' % sys.executable

    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = str(pythonw) if pythonw.exists() else str(exe)
    script = Path(sys.argv[0]).resolve()
    return '"%s" "%s" --daemon' % (runner, script)


def _winreg():
    import winreg  # 只在 Windows 上有
    return winreg


def is_enabled() -> bool:
    try:
        winreg = _winreg()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except Exception:                                             # noqa: BLE001
        return False


def enable() -> tuple[bool, str]:
    try:
        winreg = _winreg()
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
        return True, "已加入开机启动"
    except Exception as exc:                                      # noqa: BLE001
        return False, "写入注册表失败：%s" % exc


def disable() -> tuple[bool, str]:
    try:
        winreg = _winreg()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True, "已取消开机启动"
    except FileNotFoundError:
        return True, "本来就没设置开机启动"
    except Exception as exc:                                      # noqa: BLE001
        return False, "删除注册表值失败：%s" % exc
