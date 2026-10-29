"""Windows 原生「这次连哪一个网」选择框。

【为什么不用 tkinter】
    目标机器上的 Python 是精简版，可能根本没带 tkinter；而且打包成 exe 时
    tkinter 会让体积翻一倍。这里直接用 ctypes 调 Win32 API 画一个最小对话框，
    纯标准库、零依赖，长相就是系统原生的。

【为什么没有倒计时】
    这一版的要求是"必须人手动选"。所以窗口不会自动关、也不会替你选：
    没人点，它就一直在那儿，认证流程也就一直停着（后台检测同时暂停）。
    宁可等着，也不拿上次的选择去猜。

【线程模型】
    对话框在调用它的线程上创建并跑消息循环（GetMessage/DispatchMessage），
    调用方一直阻塞到用户选完 / 倒计时结束。全局只允许一个弹窗同时存在：
    第二个调用者会等前一个结束，并直接复用刚刚的结果，避免连点两下弹两个窗。
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------
WM_CREATE = 0x0001
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_SETFONT = 0x0030
WM_COMMAND = 0x0111
WM_TIMER = 0x0113

WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_TABSTOP = 0x00010000
WS_GROUP = 0x00020000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000

WS_EX_TOPMOST = 0x00000008
WS_EX_DLGMODALFRAME = 0x00000001
WS_EX_APPWINDOW = 0x00040000

BS_PUSHBUTTON = 0x00000000
BS_DEFPUSHBUTTON = 0x00000001
BS_AUTORADIOBUTTON = 0x00000009

SS_LEFT = 0x00000000

BM_SETCHECK = 0x00F9
BM_GETCHECK = 0x00F0
BST_CHECKED = 1

DM_SETDEFID = 0x0402
SW_SHOWNORMAL = 1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
HWND_TOPMOST = -1

IDOK = 1
IDCANCEL = 2
ID_RADIO_BASE = 100
ID_TITLE = 200
ID_SUB = 201
ID_HINT = 202

COLOR_BTNFACE = 15

LRESULT = ctypes.c_ssize_t
UINT_PTR = ctypes.c_size_t          # 不同 Python 版本的 wintypes 不一定有它

# ----------------------------------------------------------------------
# Win32 绑定
# ----------------------------------------------------------------------
AVAILABLE = False
_LOAD_ERROR = ""

try:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", wintypes.POINT),
        ]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    _user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    _user32.RegisterClassExW.restype = wintypes.ATOM

    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND

    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
    _user32.DefWindowProcW.restype = LRESULT

    _user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM]
    _user32.SendMessageW.restype = LRESULT

    _user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT]
    _user32.GetMessageW.restype = ctypes.c_int

    _user32.IsDialogMessageW.argtypes = [wintypes.HWND, ctypes.POINTER(MSG)]
    _user32.IsDialogMessageW.restype = wintypes.BOOL

    _user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    _user32.DispatchMessageW.restype = LRESULT

    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.BringWindowToTop.argtypes = [wintypes.HWND]
    _user32.SetFocus.argtypes = [wintypes.HWND]
    _user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetDlgItem.restype = wintypes.HWND
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]

    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     wintypes.UINT]
    _user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]

    _user32.SetTimer.argtypes = [wintypes.HWND, UINT_PTR,
                                 wintypes.UINT, ctypes.c_void_p]
    _user32.SetTimer.restype = UINT_PTR
    _user32.KillTimer.argtypes = [wintypes.HWND, UINT_PTR]

    _user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    _user32.LoadCursorW.restype = wintypes.HANDLE
    _gdi32.DeleteObject.argtypes = [wintypes.HANDLE]

    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.GetSystemMetrics.restype = ctypes.c_int

    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.GetDC.restype = wintypes.HDC
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
    _gdi32.GetDeviceCaps.restype = ctypes.c_int

    _user32.AdjustWindowRectEx.argtypes = [ctypes.POINTER(RECT), wintypes.DWORD,
                                           wintypes.BOOL, wintypes.DWORD]
    _user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT,
                                              ctypes.c_void_p, wintypes.UINT]
    _user32.GetSysColorBrush.argtypes = [ctypes.c_int]
    _user32.GetSysColorBrush.restype = wintypes.HBRUSH

    _gdi32.CreateFontW.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPCWSTR,
    ]
    _gdi32.CreateFontW.restype = wintypes.HFONT

    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    AVAILABLE = True
except Exception as exc:                                          # noqa: BLE001
    _LOAD_ERROR = "%s: %s" % (type(exc).__name__, exc)


# ----------------------------------------------------------------------
# 对外结果
# ----------------------------------------------------------------------
@dataclass
class AskResult:
    choice: str | None = None      # 选中的那一项；None = 没选到
    cancelled: bool = False        # 用户点了取消 / 关了窗口
    error: str = ""                # 弹不出来（无桌面、非 Windows 等）

    def as_dict(self) -> dict:
        return {"choice": self.choice, "cancelled": self.cancelled,
                "error": self.error}


_LOCK = threading.Lock()
_LAST: tuple[float, AskResult] | None = None
_REUSE_WINDOW = 2.0        # 秒：2 秒内重复调用直接复用上一次结果
_DPI_DONE = False


def _init_dpi() -> None:
    """声明 DPI 感知，否则高分屏下字体发虚、窗口按 96dpi 拉伸。"""
    global _DPI_DONE
    if _DPI_DONE:
        return
    _DPI_DONE = True
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # Win8.1+
    except Exception:                                             # noqa: BLE001
        try:
            _user32.SetProcessDPIAware()
        except Exception:                                         # noqa: BLE001
            pass


def _system_dpi() -> int:
    """取系统 DPI（96 / 120 / 144 / 192 ...）。

    为什么必须自己算：进程声明了 DPI 感知之后，Windows **不再**替我们放大字体，
    界面里的数字全是物理像素。所以字号和控件坐标都要自己乘 dpi/96，
    否则 150% 缩放屏上弹出来会是一堆小字挤在左上角。
    """
    fn = getattr(_user32, "GetDpiForSystem", None)      # Win10 1607+
    if fn is not None:
        try:
            fn.restype = wintypes.UINT
            d = int(fn())
            if d > 0:
                return d
        except Exception:                                         # noqa: BLE001
            pass
    try:
        hdc = _user32.GetDC(None)
        if hdc:
            try:
                return int(_gdi32.GetDeviceCaps(hdc, 88))   # LOGPIXELSX
            finally:
                _user32.ReleaseDC(None, hdc)
    except Exception:                                             # noqa: BLE001
        pass
    return 96


def ask_service(options: list[str], default: str = "",
                title: str = "SUIT 校园网 · 选择要连的网",
                message: str = "这次认证要连哪一个网？",
                sub: str = "选错不会直接失败，程序会拿服务端返回的列表自动回退。") -> AskResult:
    """弹一个原生单选框，返回用户选择。

    options  : 候选名字，按显示顺序
    default  : 预选项（一般是上次选的）；不在 options 里就用第一项

    **没有倒计时、没有自动兜底**：必须由人点「确定」，否则窗口一直挂着。
    调用方（守护线程）在此期间会被阻塞，等于自动暂停了后台检测 —— 这是刻意的：
    让"选网"成为唯一的前置动作，避免程序自作主张用旧选择去认证。
    """
    global _LAST

    if not AVAILABLE:
        return AskResult(error="无法加载 Win32 接口（%s）" % _LOAD_ERROR)
    options = [o for o in options if o]
    if not options:
        return AskResult(error="没有可选项")

    with _LOCK:
        if _LAST is not None:
            ts, res = _LAST
            if time.time() - ts < _REUSE_WINDOW:
                return res
        try:
            res = _run(options, default, title, message, sub)
        except Exception as exc:                                  # noqa: BLE001
            res = AskResult(error="%s: %s" % (type(exc).__name__, exc))
        _LAST = (time.time(), res)
        return res


# ----------------------------------------------------------------------
# 内部实现
# ----------------------------------------------------------------------
def _run(options: list[str], default: str,
         title: str, message: str, sub: str) -> AskResult:
    _init_dpi()

    # 所有尺寸都写成 96dpi 下的"设计值"，再统一乘 sc。
    # 这样 125% / 150% / 200% 缩放下字号、控件、窗口一起等比放大，
    # 不会出现"字放大了但框没放大"的挤压。
    sc = max(1.0, _system_dpi() / 96.0)

    def S(v: float) -> int:
        return int(round(v * sc))

    if default not in options:
        default = options[0]

    state = {
        "hwnd": None,
        "radios": [],
        "done": False,
        "result": AskResult(),
    }

    hinst = _kernel32.GetModuleHandleW(None)
    class_name = "SuitPortalAskWnd"
    # 负值 = 字符高度（逻辑单位，DPI 感知下等于物理像素）
    font_body = _gdi32.CreateFontW(-S(13), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0,
                                   "Microsoft YaHei UI")
    font_title = _gdi32.CreateFontW(-S(17), 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 5, 0,
                                    "Microsoft YaHei UI")
    font_small = _gdi32.CreateFontW(-S(12), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0,
                                    "Microsoft YaHei UI")

    def finish(choice, cancelled=False):
        if state["done"]:
            return
        state["done"] = True
        state["result"] = AskResult(choice=choice, cancelled=cancelled)
        hwnd = state["hwnd"]
        if hwnd:
            _user32.DestroyWindow(hwnd)

    def checked_choice() -> str:
        for i, h in enumerate(state["radios"]):
            if _user32.SendMessageW(h, BM_GETCHECK, 0, 0) == BST_CHECKED:
                return options[i]
        return default

    def on_ok():
        finish(checked_choice())

    def build_ui(hwnd):
        ex = 0
        parent = hwnd

        def make(cls, text, style, x, y, w, h, cid, font):
            ctrl = _user32.CreateWindowExW(
                ex, cls, text, style, x, y, w, h, parent,
                wintypes.HMENU(cid), hinst, None,
            )
            if ctrl and font:
                _user32.SendMessageW(ctrl, WM_SETFONT, font, 1)
            return ctrl

        make("STATIC", message, WS_CHILD | WS_VISIBLE | SS_LEFT,
             S(20), S(18), S(420), S(24), ID_TITLE, font_title)
        make("STATIC", sub, WS_CHILD | WS_VISIBLE | SS_LEFT,
             S(20), S(46), S(420), S(20), ID_SUB, font_small)

        y = 80
        for i, name in enumerate(options):
            style = WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTORADIOBUTTON
            if i == 0:
                style |= WS_GROUP
            h = make("BUTTON", name, style, S(24), S(y), S(380), S(26),
                     ID_RADIO_BASE + i, font_body)
            state["radios"].append(h)
            if name == default:
                _user32.SendMessageW(h, BM_SETCHECK, BST_CHECKED, 0)
            y += 32

        btn_y = y + 10
        make("STATIC", "选完点「确定」；点「取消」= 这一轮不认证",
             WS_CHILD | WS_VISIBLE | SS_LEFT,
             S(24), S(btn_y), S(420), S(18), ID_HINT, font_small)

        btn_y += 26
        make("BUTTON", "确定", WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_DEFPUSHBUTTON,
             S(236), S(btn_y), S(96), S(30), IDOK, font_body)
        make("BUTTON", "取消", WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
             S(344), S(btn_y), S(96), S(30), IDCANCEL, font_body)

        _user32.SendMessageW(hwnd, DM_SETDEFID, IDOK, 0)
        _user32.SetFocus(_user32.GetDlgItem(hwnd, ID_RADIO_BASE))

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_CREATE:
            state["hwnd"] = hwnd
            build_ui(hwnd)
            return 0
        if msg == WM_COMMAND:
            cid = wparam & 0xFFFF
            if cid == IDOK:
                on_ok()
                return 0
            if cid == IDCANCEL:
                finish(None, cancelled=True)
                return 0
            return 0
        if msg == WM_CLOSE:
            finish(None, cancelled=True)
            return 0
        if msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wndproc_cb = WNDPROC(wndproc)          # 必须一直持有，否则回调被回收

    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.style = 0
    wc.lpfnWndProc = wndproc_cb
    wc.hInstance = hinst
    wc.hCursor = _user32.LoadCursorW(None, ctypes.c_void_p(32512))   # IDC_ARROW
    wc.hbrBackground = wintypes.HBRUSH(COLOR_BTNFACE + 1)
    wc.lpszClassName = class_name
    if not _user32.RegisterClassExW(ctypes.byref(wc)):
        err = ctypes.get_last_error()
        if err not in (0, 1410):           # 1410 = 类已注册，正常
            raise OSError("RegisterClassExW 失败 (error %d)" % err)

    style = WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX
    ex_style = WS_EX_TOPMOST | WS_EX_DLGMODALFRAME | WS_EX_APPWINDOW

    client_w = S(460)
    # 高度按选项数量算：标题区 80 + 每项 32 + 提示 18 + 按钮 30 + 边距
    client_h = S(80 + len(options) * 32 + 10 + 18 + 26 + 30 + 18)

    rect = RECT(0, 0, client_w, client_h)
    _user32.AdjustWindowRectEx(ctypes.byref(rect), style, False, ex_style)
    win_w = rect.right - rect.left
    win_h = rect.bottom - rect.top

    work = RECT()
    _user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)
    x = work.left + ((work.right - work.left) - win_w) // 2
    y = work.top + ((work.bottom - work.top) - win_h) // 2

    hwnd = _user32.CreateWindowExW(
        ex_style, class_name, title, style, x, y, win_w, win_h,
        None, None, hinst, None,
    )
    if not hwnd:
        raise OSError("CreateWindowExW 失败 (error %d)" % ctypes.get_last_error())

    _user32.ShowWindow(hwnd, SW_SHOWNORMAL)
    _user32.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE)
    _user32.SetForegroundWindow(hwnd)
    _user32.BringWindowToTop(hwnd)

    msg = MSG()
    while True:
        got = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if got in (0, -1):
            break
        if _user32.IsDialogMessageW(hwnd, ctypes.byref(msg)):
            continue          # Tab / 方向键 / Esc 由系统按对话框语义处理
        _user32.TranslateMessage(ctypes.byref(msg))
        _user32.DispatchMessageW(ctypes.byref(msg))

    for f in (font_body, font_title, font_small):
        try:
            _gdi32.DeleteObject(f)
        except Exception:                                         # noqa: BLE001
            pass

    return state["result"]
