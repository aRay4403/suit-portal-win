# SUIT 校园网自动认证 · Windows 版

深圳信息职业技术大学锐捷 ePortal 校园网**自动认证**工具，Windows 桌面版。

原版 [luci-app-suit-portal](https://github.com/aRay4403/luci-app-suit-portal) 是给 OpenWrt 路由器用的插件；
这个版本把同样的认证逻辑搬到了 Windows 电脑上 —— **在电脑上直接跑，不需要路由器**。

单文件 exe，零第三方依赖（只用 Python 标准库），密码用 Windows DPAPI 加密落盘。

---

## 两个分支

| 分支 | 版本 | 端口 | 认证前 | 适合谁 |
|---|---|---|---|---|
| **`main`** | 原版 | 8791 | 直接用界面里选好的运营商 | 运营商固定、不想被打扰 |
| **`ask`** | 弹窗选网版 | 8792 | 弹一个 Windows 原生小窗问「连哪个网」 | 经常在几个出口之间切换 |

两版**可以同时装**：端口不同（8791 / 8792）、配置目录不同（`%APPDATA%\SuitPortal` / `%APPDATA%\SuitPortalAsk`）、
开机自启的注册表值也各自独立。

切换分支：

```bash
git clone -b ask https://github.com/aRay4403/suit-portal-win.git suit-portal-win-ask
```

---

## 快速开始

1. 下载仓库根目录的 `SuitPortal.exe`（`ask` 分支是 `SuitPortalAsk.exe`）
2. 双击运行 —— 会自己弹出浏览器打开界面
3. 填校园网账号、密码，选运营商，点「保存设置」
4. 点「立即认证」，顶部标签变绿「在线」就好了

之后只要程序开着，掉线会自动重连。想开机自启就在界面里打开开关。

> 详细的操作、排错、原理说明见 **[使用说明.md](使用说明.md)**。

---

## 目录结构

```
suit-portal-win/
├─ SuitPortal.exe          单文件可执行程序（PyInstaller 打包，含 Python 运行时）
├─ main.py                 入口（--daemon 后台守护 / 默认带界面启动）
├─ suitportal/
│  ├─ auth.py              认证流程（5 步：探测 → CAS 登录 → 选运营商 → 上线）
│  ├─ httpc.py             HTTP 客户端（不跟随 302、手工管 cookie）
│  ├─ aes.py               纯 Python AES-128-ECB/PKCS7
│  ├─ config.py            配置读写（%APPDATA%）
│  ├─ dpapi.py             密码加密（Windows DPAPI）
│  ├─ monitor.py           守护循环、自适应重试间隔
│  ├─ webui.py             本地 Web 界面（127.0.0.1，仅本机可访问）
│  ├─ assets.py            界面 HTML/CSS/JS（内嵌进 exe）
│  └─ autostart.py         开机自启（HKCU Run 键）
├─ tools/
│  ├─ make_icon.py         用 Edge 无头渲染 SVG 生成多尺寸 ico
│  └─ privacy_scan.py      发布前隐私扫描（见下）
├─ suit.ico                图标
└─ 使用说明.md              面向使用者的完整说明
```

---

## 从源码跑 / 重新打包

```bash
# 直接跑源码（需要 Python 3.9+，无第三方依赖）
python main.py

# 打包成单文件 exe
python -m PyInstaller --onefile --noconsole --name SuitPortal \
    --icon "$(pwd)/suit.ico" main.py

# 重新生成图标（改了设计才需要，要装 Pillow）
python tools/make_icon.py
```

> `--icon` 必须给**绝对路径**。用了 `--specpath` 的话，相对路径会按 spec 所在目录解析，
> 报 `Icon input file ... not found`。

---

## 隐私

这个工具需要你的校园网账号密码，所以仓库本身做了脱敏，并且带一道自动闸门：

- 仓库里的账号、密码、设备 MAC、会话 ID、入口 URL **全部是占位符或空值**；
- 不含任何登录抓包（HAR）、截图、`%APPDATA%` 下的真实配置；
- `10.10.16.56` 是学校 ePortal 服务器的默认地址（所有使用者共用，非个人设备标识）。

发布前自查：

```bash
python tools/privacy_scan.py
```

它会扫全仓库的文本文件，命中「密文+密钥同时出现」「真实票据」「设备 MAC」
「个人目录路径」「内网 IP」等模式就退出码非 0。**提交前跑一遍**。

---

## 声明

仅用于自动化**你本人已拥有账号**的认证流程，不绕过计费、不绕过管理规定。
请遵守学校网络使用规定。同一账号多设备同时认证通常会被互相踢下线，
这是校园网的单账号策略，与本工具无关。
