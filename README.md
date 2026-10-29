# SUIT 校园网自动认证 · 弹窗选网版（Windows）

深圳信息职业技术大学锐捷 ePortal 校园网自动认证工具 —— **每次认证前弹一个 Windows 原生小窗，
问你这次要连哪一个网**，选完才继续。

这是 [`main` 分支](https://github.com/aRay4403/suit-portal-win/tree/main)（原版）的另一个版本，
认证逻辑完全一致，**只多了「每次问一次」这一件事**。

---

## 两个分支

| 分支 | 版本 | 端口 | 认证前 | 适合谁 |
|---|---|---|---|---|
| [`main`](https://github.com/aRay4403/suit-portal-win/tree/main) | 原版 | 8791 | 直接用界面里选好的运营商 | 运营商固定、不想被打扰 |
| **`ask`** | 弹窗选网版 | 8792 | 弹一个 Windows 原生小窗问「连哪个网」 | 经常在几个出口之间切换 |

两版**可以同时装**：端口不同、配置目录不同（`%APPDATA%\SuitPortal` / `%APPDATA%\SuitPortalAsk`）、
开机自启的注册表值也各自独立。

---

## 快速开始

1. 下载仓库根目录的 `SuitPortalAsk.exe`，双击 —— 浏览器会自动打开 <http://127.0.0.1:8792/>
2. 填账号、密码，点「保存设置」
3. 点「立即认证」→ 桌面弹出原生小窗，选这次要连的网 → 点「确定」
4. 顶部标签变绿「在线」就好了

> 想先看看弹窗长什么样：界面里点 **「试一下选网弹窗」**，只弹不保存。
> 完整说明见 **[使用说明.md](使用说明.md)**。

---

## 和原版的差别（只有这几处）

| | 原版 | 这一版 |
|---|---|---|
| 运营商怎么定 | 界面里选一次，一直用 | **每次认证都弹窗问**，选完才认证 |
| 掉线自动重连 | 直接用存的那个 | 也会先弹窗问 |
| 弹窗期间 | —— | **后台检测暂停**：不探外网、不重试、不认证 |
| 弹窗超时 | —— | **没有倒计时**，必须手动选，不选就一直等 |
| 点「取消」 | —— | 这一轮不认证，60 秒后再问 |
| 字号 | —— | 随系统 DPI 等比缩放（125% / 150% / 200%） |

弹窗用 `ctypes` 直接调 Win32 API 画的（`CreateWindowExW` + `BS_AUTORADIOBUTTON` + 消息循环），
**没有用 tkinter** —— 目标机器上的 Python 可能是精简版没带 tkinter，而且它会让 exe 体积翻倍。

---

## 目录结构

```
suit-portal-win-ask/
├─ SuitPortalAsk.exe       单文件可执行程序（含 Python 运行时）
├─ SuitPortalAsk.spec      PyInstaller 打包配置
├─ main.py                 入口（--daemon / --test-ask / 默认带界面）
├─ suitportal/
│  ├─ askdlg.py            ★ 原生选网对话框（ctypes，无 tkinter）
│  ├─ monitor.py           ★ _asking 暂停检测 + 每次认证前询问
│  ├─ config.py            ★ ask_service / last_service / service_options
│  ├─ webui.py             ★ 端口 8792、/api/ask/test
│  ├─ auth.py              认证流程（5 步）
│  ├─ httpc.py             HTTP 客户端（不跟随 302、手工管 cookie）
│  ├─ aes.py               纯 Python AES-128-ECB/PKCS7
│  ├─ dpapi.py             密码加密（Windows DPAPI）
│  ├─ assets.py            界面 HTML/CSS/JS（内嵌进 exe）
│  └─ autostart.py         开机自启（HKCU Run 键）
├─ tools/
│  ├─ make_icon.py         用 Edge 无头渲染 SVG 生成多尺寸 ico
│  └─ privacy_scan.py      发布前隐私扫描
├─ suit.ico                图标
└─ 使用说明.md              面向使用者的完整说明
```

---

## 从源码跑 / 重新打包

```bash
# 直接跑源码（需要 Python 3.9+，无第三方依赖）
python main.py

# 打包成单文件 exe
python -m PyInstaller --onefile --noconsole --name SuitPortalAsk \
    --icon "$(pwd)/suit.ico" main.py
```

> `--icon` 必须给**绝对路径**。用了 `--specpath` 的话，相对路径会按 spec 所在目录解析，
> 报 `Icon input file ... not found`。

---

## 隐私

仓库已脱敏，并带一道自动闸门：

- 账号、密码、设备 MAC、会话 ID、入口 URL **全部是占位符或空值**；
- 不含登录抓包（HAR）、截图、`%APPDATA%` 下的真实配置；
- `10.10.16.56` 是学校 ePortal 服务器的默认地址（全校共用，非个人设备标识）。

发布前自查：

```bash
python tools/privacy_scan.py
```

命中「密文+密钥同时出现」「真实票据」「设备 MAC」「个人目录路径」「内网 IP」等模式就退出码非 0。

---

## 声明

仅用于自动化**你本人已拥有账号**的认证流程，不绕过计费、不绕过管理规定。
请遵守学校网络使用规定。
