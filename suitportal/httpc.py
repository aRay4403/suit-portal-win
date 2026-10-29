"""HTTP 传输层。

为什么不用 requests：
    1) 要「零依赖」，同学解压就能跑，不该先 pip install；
    2) 更重要的是**不能自动跟随 302**。原版脚本里最关键的信息
       （sessionId / ticket）全在 Location 响应头里，跟随后就丢了。

原版为什么用 nc 发裸 HTTP：
    OpenWrt 的 uclient-fetch 既没有 cookie 支持，也没有打印响应头的选项，
    而 CAS 认证必须带 SESSION cookie。Windows 上 http.client 直接就能拿到
    Set-Cookie 和 Location，所以这里不需要 nc 那套折腾。
"""

from __future__ import annotations

import http.client
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)

DEFAULT_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


@dataclass
class Response:
    status: int = 0
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""
    url: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return 300 > self.status >= 200

    def header(self, name: str) -> str:
        """取第一个同名响应头（不区分大小写）。"""
        low = name.lower()
        for k, v in self.headers:
            if k.lower() == low:
                return v
        return ""

    def header_all(self, name: str) -> list[str]:
        low = name.lower()
        return [v for k, v in self.headers if k.lower() == low]

    @property
    def location(self) -> str:
        """取**最后一个** Location：一次响应里可能有多个跳转，最后一个才是终点。"""
        locs = self.header_all("Location")
        return locs[-1].strip() if locs else ""

    @property
    def text(self) -> str:
        """尽量按 utf-8 解码；失败就忽略坏字节（页面编码很杂）。"""
        return self.body.decode("utf-8", errors="ignore")


class CookieJar:
    """极简 cookie jar：名字 -> 值。

    只做原版那套逻辑：取 "name=value" 里分号前那段；值为空视为删除；同名覆盖。
    """

    def __init__(self) -> None:
        self._c: dict[str, str] = {}

    def clear(self) -> None:
        self._c.clear()

    def absorb(self, resp: Response) -> None:
        for raw in resp.header_all("Set-Cookie"):
            pair = raw.split(";", 1)[0].strip()
            if not pair or "=" not in pair:
                continue
            name, _, value = pair.partition("=")
            name = name.strip()
            if not name:
                continue
            if value.strip() == "":
                self._c.pop(name, None)
            else:
                self._c[name] = value.strip()

    def header(self) -> str:
        return "; ".join("%s=%s" % (k, v) for k, v in self._c.items())

    def get(self, name: str) -> str:
        return self._c.get(name, "")

    def __bool__(self) -> bool:
        return bool(self._c)


class Session:
    """一次认证会话：绑定 portal 主机 + 自带 cookie jar。"""

    def __init__(self, portal_host: str, timeout: int = 6) -> None:
        host, _, port = portal_host.partition(":")
        self.default_host = host.strip()
        self.default_port = int(port) if port.strip().isdigit() else 80
        self.timeout = timeout
        self.cookies = CookieJar()

    # ------------------------------------------------------------------
    def _split(self, url: str) -> tuple[str, int, str, bool]:
        """把 url（绝对或相对路径）拆成 host/port/path。"""
        if url.startswith("http://") or url.startswith("https://"):
            p = urlsplit(url)
            path = p.path or "/"
            if p.query:
                path += "?" + p.query
            return p.hostname or self.default_host, p.port or 80, path, True
        path = url if url.startswith("/") else "/" + url
        return self.default_host, self.default_port, path, False

    @staticmethod
    def _encode_path(path: str) -> str:
        """URL 里可能带空格（accessTime 参数），直接发会被服务端判 400。"""
        out = []
        for ch in path:
            if ch == " ":
                out.append("%20")
            elif ord(ch) > 127:
                out.append("".join("%%%02X" % b for b in ch.encode("utf-8")))
            else:
                out.append(ch)
        return "".join(out)

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        ctype: str | None = None,
        referer: str = "",
        accept: str = DEFAULT_ACCEPT,
        absorb_cookies: bool = True,
    ) -> Response:
        host, port, path, is_abs = self._split(url)
        path = self._encode_path(path)

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "close",
        }
        if is_abs:
            headers["Host"] = host if port == 80 else "%s:%d" % (host, port)
        if ctype:
            headers["Content-Type"] = ctype
        if referer:
            headers["Referer"] = referer
        if method == "POST":
            headers["Origin"] = "http://" + (
                self.default_host if self.default_port == 80
                else "%s:%d" % (self.default_host, self.default_port)
            )
        ck = self.cookies.header()
        if ck:
            headers["Cookie"] = ck
        if body is not None:
            headers["Content-Length"] = str(len(body))

        resp = Response(url=url)
        conn = None
        try:
            conn = http.client.HTTPConnection(host, port, timeout=self.timeout)
            conn.request(method, path, body=body, headers=headers)
            raw = conn.getresponse()
            resp.status = raw.status
            resp.headers = raw.getheaders()
            resp.body = raw.read()
        except (socket.timeout, TimeoutError):
            resp.error = "连接超时（%ds）" % self.timeout
        except ConnectionRefusedError:
            resp.error = "连接被拒绝"
        except socket.gaierror as exc:
            resp.error = "域名解析失败：%s" % exc
        except Exception as exc:                                  # noqa: BLE001
            resp.error = "%s: %s" % (type(exc).__name__, exc)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:                                 # noqa: BLE001
                    pass

        if absorb_cookies and resp.headers:
            self.cookies.absorb(resp)
        return resp

    def get(self, url: str, **kw) -> Response:
        return self.request("GET", url, **kw)

    def post(
        self,
        url: str,
        body: bytes | str,
        ctype: str = "application/x-www-form-urlencoded",
        **kw,
    ) -> Response:
        if isinstance(body, str):
            body = body.encode("utf-8")
        return self.request("POST", url, body=body, ctype=ctype, **kw)

    def post_json(self, url: str, payload: str, referer: str = "") -> Response:
        return self.post(url, payload, "application/json", referer=referer)


# ----------------------------------------------------------------------
def local_ip_for(host: str, port: int = 80) -> str:
    """取本机用来访问 portal 的那张网卡的 IP。

    对应原版的 wan_ip()：UDP connect 不会真的发包，但内核会选好出口网卡，
    getsockname() 就能拿到该网卡的地址。比遍历网卡猜一个靠谱。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(0.5)
        s.connect((host, port))
        return s.getsockname()[0]
    except Exception:                                             # noqa: BLE001
        return ""
    finally:
        s.close()
