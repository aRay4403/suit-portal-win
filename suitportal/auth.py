"""锐捷 ePortal 认证流程（移植自原版 suit-portal.sh）。

流程对照（原版注释里的编号保持一致，方便两边对读）：
    [0a] 建立 CAS 会话 —— GET /cas-sso/logout 拿 SESSION cookie
    [0b] 触发网关拦截 —— 探测外网，从注入的 JS 里抠出 eportal 入口 URL
         再请求入口 -> 302 到 portal-main，拿到 sessionId / userIp / userMac / nasIp
    [0c] 按正确顺序重建会话（清 cookie 后重新 logout，顺序错了会 401）
    [1]  GET  /cas-sso/login  -> 解析 execution 与 croypto(AES 密钥)
    [2]  POST /cas-sso/login  -> AES-128-ECB/PKCS7 加密密码，换 CAS ticket
    [3]  POST /eportal/workFlow/getCurrentNode
    [4]  POST /eportal/network/serviceSelection -> 查运营商
    [5]  POST /eportal/network/serviceLogin     -> 选运营商，authResult=success
    [5b] getCurrentNode -> finish
    [5c] userOnline -> online:true
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

from .aes import encrypt_ecb_b64
from .config import Config
from .httpc import Response, Session, local_ip_for

LogFn = Callable[[str], None]

# 门户劫持特征：命中任何一个就说明「被拦到认证页了」，即离线
PORTAL_MARKERS = ("eportal", "cas-sso", "location.href", "portal-main")

# 界面上显示的运营商名 -> 服务端 serviceLogin 真正要的 key。
#
# 【为什么需要这张表】
# 锐捷 ePortal 的 /eportal/network/serviceSelection 返回的是 {"key":"default"} 这种
# 机器名，其中 default 表示「校内自有出口」，也就是大家说的「校园网」。
# 如果直接把界面上的「校园网」三个字当 service 提交，服务端不认，会认证失败。
# 原版（OpenWrt）的注释记录了实机情况：只返回 default 和「电信宽带」两项。
SERVICE_ALIASES = {
    "校园网": "default",
    "校内网": "default",
}


class AuthError(Exception):
    """认证流程中可预期的失败，message 直接给用户看。"""


@dataclass
class AuthResult:
    ok: bool
    message: str = ""
    already_online: bool = False


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------
def urlencode(s: str) -> str:
    """RFC3986 百分号编码，只保留 unreserved 字符。"""
    return quote(s, safe="-_.~")


def b64urlencode(s: str) -> str:
    """base64 风格字段的百分号编码。

    必须做！CAS 表单是 application/x-www-form-urlencoded，服务端会把 '+' 解成空格。
    AES 密文 base64 后约 1/3 概率含 '+'，原样提交就会「用户名或密码错误」——
    原版为此踩了很久的坑，注释里写得很清楚。
    """
    return s.replace("%", "%25").replace("+", "%2B").replace("/", "%2F").replace("=", "%3D")


def extract_p(html: str, elem_id: str) -> str:
    """从 HTML 里取 <p id="xxx">VALUE</p>。"""
    m = re.search(
        r"""<p[^>]*\bid\s*=\s*["']%s["'][^>]*>([^<]*)</p>""" % re.escape(elem_id),
        html,
        re.I,
    )
    return m.group(1).strip() if m else ""


def extract_js_redirect(body: str) -> str:
    """从网关注入的 JS 里抠出跳转 URL。

    这套网关不是标准 302，而是返回 200 + 一段
        <script>top.self.location.href='http://10.10.16.56/eportal/index.jsp?...'</script>
    所以不能只看 Location 响应头。
    """
    for pat in (
        r"""location\.href\s*=\s*['"](http[^'"]*)['"]""",
        r"""location\.replace\(\s*['"](http[^'"]*)['"]""",
        r"""window\.location\s*=\s*['"](http[^'"]*)['"]""",
    ):
        m = re.search(pat, body, re.I)
        if m:
            return m.group(1)
    return ""


def qs_param(url: str, name: str) -> str:
    m = re.search(r"[?&]%s=([^&]*)" % re.escape(name), url)
    return m.group(1) if m else ""


def abs_url(u: str, portal_host: str) -> str:
    if not u:
        return ""
    if u.startswith("http://") or u.startswith("https://"):
        return u
    if u.startswith("/"):
        return "http://" + portal_host + u
    return "http://" + portal_host + "/" + u


# ----------------------------------------------------------------------
# 在线检测
# ----------------------------------------------------------------------
def check_online(cfg: Config, log: LogFn, session: Session | None = None) -> bool:
    """探测外网判断是否已认证。

    原版曾用服务端 /eportal/network/userOnline 当判据，后来发现那是错的：
    它依赖入口 URL，而入口里的 wlanuserip 是加密写死的旧 IP，
    WAN 地址一变就误报离线，于是「明明有网却每 5 秒重认证一次」。
    所以这里只探测外网：
      · 命中门户特征        -> 确定离线
      · 请求成功且内容干净   -> 在线
      · 全部探测都失败      -> 按离线处理
    """
    s = session or Session(cfg.portal_host, cfg.timeout)
    for url in cfg.online_check_urls.split():
        resp = s.get(url, accept="*/*")
        blob = resp.text + resp.location
        if any(m in blob for m in PORTAL_MARKERS) or cfg.portal_host in blob:
            return False
        if resp.ok:
            return True
    return False


# ----------------------------------------------------------------------
# 认证引擎
# ----------------------------------------------------------------------
class Authenticator:
    def __init__(self, cfg: Config, log: LogFn) -> None:
        self.cfg = cfg
        self.log = log
        self.base = "http://" + cfg.portal_host
        self.session = Session(cfg.portal_host, cfg.timeout)
        # 流程中间状态
        self.sid = ""
        self.userip = ""
        self.usermac = ""
        self.nasip = ""
        self.pageid = ""
        self.entry_url = ""
        self.chosen_key = cfg.service       # 真正提交给服务端的 key
        self.chosen_label = cfg.service     # 给用户看的名字
        self._fresh_entry = False

    # -- 0a ------------------------------------------------------------
    def _logout_url(self) -> str:
        host = self.cfg.portal_host.replace(":", "%3A")
        return "/cas-sso/logout?service=http%%3A%%2F%%2F%s%%2Fportal%%2Fpc%%2FofflineSuccess" % host

    def session_init(self) -> None:
        self.log("[0/5] 建立 CAS 会话...")
        resp = self.session.get(self._logout_url())
        sess = self.session.cookies.get("SESSION")
        if sess:
            self.log("      SESSION=%s... (已存入 cookie jar)" % sess[:24])
        else:
            # 拿不到不算致命：取登录页时还会再收一次 Set-Cookie
            self.log("      logout 未下发 SESSION（HTTP %s），稍后再试" % (resp.status or resp.error))

    # -- 0b ------------------------------------------------------------
    def probe_entry(self) -> None:
        self.log("[0/5] 触发网关拦截...")
        hit = ""

        # 先探测：只要 AC 还在劫持，拿到的入口一定带当前 IP，天然不会过期
        for probe in self.cfg.probe_urls.split():
            resp = self.session.get(probe, accept="*/*")
            candidate = extract_js_redirect(resp.text)
            if not candidate and resp.location:
                candidate = resp.location
            if candidate and ("eportal" in candidate or self.cfg.portal_host in candidate):
                hit = candidate
                self._fresh_entry = True
                self.log("      探测 %s -> 捕获入口（含当前 IP）" % probe)
                break

        if not hit:
            if self.cfg.entry_url:
                self.log("      探测未劫持，改用配置的固定入口兜底")
                hit = self.cfg.entry_url
                self._fresh_entry = False
            else:
                raise AuthError(
                    "未捕获网关跳转，且没有配置兜底入口 URL。\n"
                    "　　原因通常是：AC 认为本机已认证，不再劫持探测请求。\n"
                    "　　办法：先断开再重连一次校园网（让 AC 重新劫持），然后点「立即认证」。"
                )

        self.entry_url = abs_url(hit, self.cfg.portal_host)
        self.log("      入口: %s..." % self.entry_url[:100])

        resp = self.session.get(self.entry_url)
        loc = resp.location or extract_js_redirect(resp.text)
        if not loc:
            raise AuthError("入口没有产生跳转，拿不到 sessionId（HTTP %s）" % (resp.status or resp.error))
        self.log("      入口跳转: %s..." % loc[:120])

        main_url = abs_url(loc, self.cfg.portal_host)
        self.sid = qs_param(main_url, "sessionId")
        self.userip = qs_param(main_url, "userIp")
        self.usermac = qs_param(main_url, "userMac")
        self.nasip = qs_param(main_url, "nasIp")
        self.pageid = qs_param(main_url, "customPageId")

        if not self.sid:
            raise AuthError("未取得 sessionId")
        self.log("      sessionId=%s" % self.sid)
        self.log("      userIp=%s userMac=%s nasIp=%s" % (self.userip, self.usermac, self.nasip))

        # 入口新鲜度：入口里的 wlanuserip 是加密写死的，本机 IP 变了它就废了
        cur = local_ip_for(self.session.default_host, self.session.default_port)
        if cur and self.userip and cur != self.userip:
            self.log("      ⚠ 入口已过期：会话 userIp=%s，本机 IP=%s" % (self.userip, cur))
            self.log("        该入口是旧 IP 的产物，网关很可能回 ACK_AUTH_REFUSE。")
        elif self._fresh_entry:
            self._save_entry_url(self.entry_url)

    def _save_entry_url(self, url: str) -> None:
        """把探测到的新入口回写配置，下次兜底用的就是对的。"""
        if not url or url == self.cfg.entry_url:
            return
        try:
            from .config import ConfigStore  # 延迟导入，避免循环
            store = ConfigStore.__new__(ConfigStore)  # 不重新读盘
            self.cfg.entry_url = url
            self.cfg.save()
            self.log("      已回写入口 URL（带当前 IP）")
        except Exception as exc:                                  # noqa: BLE001
            self.log("      （入口回写失败，不影响本次认证：%s）" % exc)

    # -- 0c ------------------------------------------------------------
    def cas_reinit(self) -> None:
        """清 cookie 后重新 logout。

        顺序很关键：必须先拿 sessionId，再 logout 建会话，最后取登录页。
        反过来的话，Step 0b 的请求会把 cookie 覆盖掉，POST 时带的就不是
        logout 下发的那个 SESSION —— 实测直接 401。
        """
        self.session.cookies.clear()
        resp = self.session.get(self._logout_url())
        sess = self.session.cookies.get("SESSION")
        if sess:
            self.log("      CAS 会话: %s..." % sess[:12])
        elif not resp.ok:
            self.log("      ⚠ logout 异常: HTTP %s" % (resp.status or resp.error))

    # -- 1 -------------------------------------------------------------
    def get_login_page(self) -> tuple[str, str]:
        self.log("[1/5] 获取登录页...")
        qs = (
            "flowSessionId=%s&customPageId=%s&preview=false&appType=normal"
            "&language=zh-CN&timer=%d" % (self.sid, self.pageid, int(time.time() * 1000))
        )
        if self.nasip:
            qs += "&nasIp=" + self.nasip
        if self.userip:
            qs += "&userIp=" + self.userip
        if self.usermac:
            qs += "&userMac=" + self.usermac
        qs += "&accept-language=zh-CN"

        login_path = "/cas-sso/login?" + qs
        login_url = self.base + login_path
        referer = self.base + "/portal/entry/pc/authenticate;flowParams=undefined;from="

        resp = self.session.get(login_url, referer=referer)
        html = resp.text
        if not html and resp.location:
            resp = self.session.get(abs_url(resp.location, self.cfg.portal_host), referer=referer)
            html = resp.text
        if not html:
            raise AuthError("登录页为空（HTTP %s）" % (resp.status or resp.error))

        execution = extract_p(html, "login-page-flowkey")
        croypto = extract_p(html, "login-croypto")
        if not execution:
            raise AuthError("未解析出 execution（login-page-flowkey），登录页可能不是预期的那个")
        if not croypto:
            raise AuthError("未解析出 croypto（AES 密钥）")

        self.log("      execution=%s... (len %d)" % (execution[:40], len(execution)))
        self.log("      croypto=%s" % croypto)
        self._login_url = login_url
        self._login_referer = referer
        return execution, croypto

    # -- 2 -------------------------------------------------------------
    def submit_login(self, execution: str, croypto: str) -> str:
        self.log("[2/5] 提交登录...")
        self.log("      账号=[%s] (长度 %d)  密码长度 %d"
                 % (self.cfg.username, len(self.cfg.username), len(self.cfg.password)))

        enc_pw = encrypt_ecb_b64(self.cfg.password, croypto)
        # captcha_payload 恒为 AES("{}")：本网络没有第三方验证码，前端初始值就是 {}
        enc_captcha = encrypt_ecb_b64("{}", croypto)

        body = "&".join([
            "username=" + urlencode(self.cfg.username),
            "type=UsernamePassword",
            "_eventId=submit",
            "geolocation=",
            "execution=" + b64urlencode(execution),
            "captcha_code=",
            "croypto=" + b64urlencode(croypto),
            "password=" + b64urlencode(enc_pw),
            "captcha_payload=" + b64urlencode(enc_captcha),
        ])

        resp = self.session.post(
            self._login_url, body, "application/x-www-form-urlencoded",
            referer=self._login_referer,
        )
        loc = resp.location
        self.log("      POST 完成: HTTP %s, body %d 字节" % (resp.status or resp.error, len(resp.body)))

        if not loc or not ("ticket=" in loc or "eportal" in loc or "portal-main" in loc):
            # 失败时把证据打出来，便于一次定位
            self.log("      ── 失败诊断 ──")
            self.log("      响应状态: %s" % (resp.status or resp.error))
            self.log("      Location: %s" % (loc or "<无>"))
            self.log("      响应 body 前 300 字节: %s" % resp.text[:300].replace("\n", " "))
            self.log("      ── 诊断结束 ──")
            if resp.status == 401:
                raise AuthError("账号或密码错误（HTTP 401）")
            raise AuthError("登录失败，未取得 ticket（HTTP %s）" % (resp.status or resp.error))

        ticket = qs_param(loc, "ticket")
        self.log("      登录成功, ticket=%s..." % (ticket[:30] if ticket else "?"))
        return ticket

    # -- 3 -------------------------------------------------------------
    def advance_workflow(self) -> None:
        self.log("[3/5] 推进认证工作流...")
        payload = json.dumps({"sessionId": self.sid, "flowKey": "portal_auth"},
                             separators=(",", ":"), ensure_ascii=False)
        resp = self.session.post_json(
            self.base + "/eportal/workFlow/getCurrentNode", payload,
            referer=self.base + "/portal/portal-main",
        )
        node = re.search(r'"currentNodePath"\s*:\s*"([^"]*)"', resp.text)
        self.log("      当前节点: %s" % (node.group(1) if node else "未知"))

    # -- 4 -------------------------------------------------------------
    @staticmethod
    def _parse_services(text: str) -> list[dict]:
        """把 serviceSelection 的响应解析成 [{"key":..,"name":..}]。

        优先按 JSON 解析（能同时拿到 key 和中文 name）；
        万一返回的不是合法 JSON，退回原来的正则抓 key，保证不因此整条流程崩掉。
        """
        try:
            data = json.loads(text)
        except Exception:                                         # noqa: BLE001
            return [{"key": k, "name": ""}
                    for k in re.findall(r'"key"\s*:\s*"([^"]*)"', text)]

        out: list[dict] = []

        def walk(node) -> None:
            if isinstance(node, dict):
                key = node.get("key")
                if isinstance(key, str):
                    name = node.get("name") or node.get("serviceName") or ""
                    out.append({"key": key, "name": str(name)})
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(data)
        return out

    def _pick_service(self, entries: list[dict], want: str) -> tuple[str, str, str]:
        """挑出要提交的运营商，返回 (服务端 key, 显示名, 回退说明)。

        四级匹配，从最准到最兜底：
          1. 服务端 key 就叫这个名字（有些学校真的返回 "校园网"）
          2. 别名表映射（校园网 -> default）
          3. 服务端返回的中文 name 里含这个名字
          4. 回退：第一个非 default；再不行就用 default
        """
        keys = [e["key"] for e in entries]
        name_of = {e["key"]: e["name"] for e in entries}

        # 1) key 同名
        if want in keys:
            return want, name_of.get(want) or want, ""

        # 2) 别名
        alias = SERVICE_ALIASES.get(want)
        if alias and alias in keys:
            return alias, name_of.get(alias) or want, ""

        # 3) 按中文名模糊匹配（服务端可能用别的 key，但 name 写着校园网）
        for e in entries:
            if want and want in e["name"]:
                return e["key"], e["name"], ""

        # 4) 回退
        others = [k for k in keys if k != "default"]
        if others:
            return others[0], name_of.get(others[0]) or others[0], \
                "服务端未提供「%s」，已自动回退" % want
        if "default" in keys:
            return "default", name_of.get("default") or "校园网", \
                "服务端只提供 default，已回退到校园网出口"

        return want, want, "服务端未返回任何运营商"

    def query_services(self) -> None:
        self.log("[4/5] 查询运营商...")
        payload = json.dumps({"sessionId": self.sid}, separators=(",", ":"))
        resp = self.session.post_json(
            self.base + "/eportal/network/serviceSelection", payload,
            referer=self.base + "/portal/portal-main",
        )

        entries = self._parse_services(resp.text)
        shown = ["%s(%s)" % (e["key"], e["name"]) if e["name"] else e["key"]
                 for e in entries]
        self.log("      可选: %s" % (" ".join(shown) or "<无>"))

        key, label, note = self._pick_service(entries, self.cfg.service)
        self.chosen_key = key
        self.chosen_label = label
        if note:
            self.log("      %s" % note)
        if key != label:
            self.log("      选定: %s（service=%s）" % (label, key))
        else:
            self.log("      选定: %s" % key)

    # -- 5 -------------------------------------------------------------
    def submit_service(self) -> bool | None:
        """返回 True=继续收尾 / False=失败 / None=已确认在线（提前成功）。"""
        self.log("[5/5] 提交运营商完成认证...")
        payload = json.dumps({"sessionId": self.sid, "service": self.chosen_key},
                             separators=(",", ":"), ensure_ascii=False)
        resp = self.session.post_json(
            self.base + "/eportal/network/serviceLogin", payload,
            referer=self.base + "/portal/portal-main",
        )
        # 服务端返回的是带缩进的 JSON（"authResult" : "success"），先去掉空白再匹配
        flat = re.sub(r"\s+", "", resp.text)

        if '"authResult":"success"' in flat:
            self.log("      运营商受理成功 (authResult=success)")
            return True

        msg_m = re.search(r'"authMessage":"([^"]*)"', flat)
        message = msg_m.group(1) if msg_m else "响应异常"
        self.log("认证失败: %s" % message)

        # ACK_AUTH_REFUSE 最常见的原因不是故障，而是这台设备本来就在线
        # （AC 不允许重复认证）。回头探一次外网，通就按成功处理，避免死循环重试。
        if "ACK_AUTH_REFUSE" in message:
            if check_online(self.cfg, self.log):
                self.log("      → 外网可通：设备其实已在线（AC 拒绝重复认证），按成功处理")
                return None
            self.log("      → 外网仍不通；入口 URL 很可能已过期")

        self.log("      原始响应: %s" % resp.text[:200].replace("\n", " "))
        return False

    # -- 5b ------------------------------------------------------------
    def confirm_finish(self) -> None:
        payload = json.dumps({"sessionId": self.sid, "flowKey": "portal_auth"},
                             separators=(",", ":"))
        resp = self.session.post_json(
            self.base + "/eportal/workFlow/getCurrentNode", payload,
            referer=self.base + "/portal/portal-main",
        )
        flat = re.sub(r"\s+", "", resp.text)
        m = re.search(r'"currentNodePath":"([^"]*)"', flat)
        self.log("      工作流节点: %s" % (m.group(1) if m else "未知"))

    # -- 5c ------------------------------------------------------------
    def verify_online(self) -> bool:
        payload = json.dumps({"sessionId": self.sid}, separators=(",", ":"))
        resp = self.session.post_json(
            self.base + "/eportal/network/userOnline", payload,
            referer=self.base + "/portal/portal-main",
        )
        flat = re.sub(r"\s+", "", resp.text)
        if '"online":true' in flat:
            self.log("      服务端确认在线 (userOnline=true)")
            return True
        if '"online":false' in flat:
            self.log("      服务端仍显示离线 (userOnline=false)")
            self.log("      运营商已受理但网关未放行，原始响应: %s" % flat[:200])
            return False
        # 拿不到 userOnline 不代表失败，退回探外网
        if check_online(self.cfg, self.log):
            self.log("      （userOnline 无有效响应，经外网探测确认在线）")
            return True
        self.log("      认证结果未知：userOnline 无有效响应，外网也不通")
        return False

    # ------------------------------------------------------------------
    def run(self) -> AuthResult:
        if not self.cfg.username or not self.cfg.password:
            raise AuthError("还没填校园网账号或密码，请先在「账号设置」里填写并保存。")

        self.session_init()
        self.probe_entry()
        self.cas_reinit()
        execution, croypto = self.get_login_page()
        self.submit_login(execution, croypto)
        self.advance_workflow()
        self.query_services()

        step5 = self.submit_service()
        if step5 is None:
            return AuthResult(True, "已在线（网关拒绝重复认证）", already_online=True)
        if step5 is False:
            return AuthResult(False, "运营商受理失败，详见日志")

        self.confirm_finish()
        if self.verify_online():
            return AuthResult(True, "认证成功（%s）" % self.chosen_label)
        return AuthResult(False, "认证未生效：运营商已受理但网关未放行")


# ----------------------------------------------------------------------
def do_auth(cfg: Config, log: LogFn) -> AuthResult:
    try:
        return Authenticator(cfg, log).run()
    except AuthError as exc:
        log("错误: %s" % exc)
        return AuthResult(False, str(exc))
    except Exception as exc:                                      # noqa: BLE001
        log("错误: 未预期异常 %s: %s" % (type(exc).__name__, exc))
        return AuthResult(False, "%s: %s" % (type(exc).__name__, exc))
