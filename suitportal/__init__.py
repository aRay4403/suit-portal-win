"""SUIT 校园网自动认证（Windows 版）。

模块划分对照原版 OpenWrt 插件：
    aes.py        <- openssl enc -aes-128-ecb + openssl base64
    httpc.py      <- nc_get / nc_post / cookie_jar / uclient-fetch
    auth.py       <- suit-portal.sh 的 auth_* 系列函数
    monitor.py    <- /etc/init.d/suit-portal 里的自适应间隔循环
    config.py     <- /etc/config/suit-portal (UCI)
    webui.py      <- www/luci-static/resources/view/suit-portal/portal.js
    autostart.py  <- procd 的开机自启
"""

__version__ = "1.0.0"
