"""界面资源（HTML / CSS / JS）。

为什么内嵌成字符串而不是放 web/ 目录：
    打包成单文件 exe 时，外部资源要额外处理 sys._MEIPASS 路径，
    而且用户可能会把 exe 单独拷走、目录就丢了。
    直接内嵌进 exe，怎么拷都不会掉界面。
"""

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SUIT 校园网自动认证</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%233d8bfd'/%3E%3Cstop offset='1' stop-color='%237c4dff'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='100' height='100' rx='22' fill='url(%23g)'/%3E%3Ctext x='50' y='73' font-size='70' font-weight='700' font-family='Segoe UI,Arial,sans-serif' fill='%23fff' text-anchor='middle'%3ES%3C/text%3E%3C/svg%3E">
<style>
__CSS__
</style>
</head>
<body>
<div class="wrap">

  <header class="top">
    <div class="brand">
      <div class="logo">S</div>
      <div>
        <h1>SUIT 校园网自动认证</h1>
        <p class="sub">锐捷 ePortal · Windows 版</p>
      </div>
    </div>
    <div class="statusbox">
      <span id="pill" class="pill idle"><i></i><span id="pillText">读取中</span></span>
      <span id="ipText" class="ip">本机 IP —</span>
    </div>
  </header>

  <div class="grid">

    <!-- 账号 -->
    <section class="card">
      <h2>账号设置</h2>
      <label class="field">
        <span>校园网账号</span>
        <input id="username" type="text" placeholder="学号 / 手机号" autocomplete="username">
      </label>
      <label class="field">
        <span>密码</span>
        <input id="password" type="password" placeholder="留空 = 不修改已保存的密码" autocomplete="current-password">
      </label>
      <label class="field">
        <span>运营商</span>
        <select id="service">
          <option>校园网</option>
          <option>电信宽带</option>
          <option>移动宽带</option>
          <option>联通宽带</option>
        </select>
      </label>
      <p class="hint tip">「校园网」= 学校自己的出口，没办运营商宽带就选这个。</p>
      <label class="field">
        <span>认证服务器</span>
        <input id="portal_host" type="text" placeholder="10.10.16.56">
      </label>
      <p class="hint">密码用 Windows 账户加密（DPAPI）保存，只有当前 Windows 用户能解开。</p>
    </section>

    <!-- 自动重连 -->
    <section class="card">
      <h2>自动重连</h2>

      <div class="row">
        <div>
          <div class="rowtitle">启用自动认证</div>
          <div class="rowdesc">掉线自动重连，在线时每 30 秒检查一次</div>
        </div>
        <label class="switch"><input id="enabled" type="checkbox"><span></span></label>
      </div>

      <div class="row">
        <div>
          <div class="rowtitle">开机自动启动</div>
          <div class="rowdesc">登录 Windows 后在后台静默运行，不弹窗</div>
        </div>
        <label class="switch"><input id="autostart" type="checkbox"><span></span></label>
      </div>

      <div class="btns">
        <button id="btnAuth" class="btn primary">立即认证</button>
        <button id="btnForce" class="btn">强制重新认证</button>
      </div>
      <p class="hint">「立即认证」会先查在线，已在线就跳过；「强制」不查，直接跑完整流程。</p>
    </section>
  </div>

  <!-- 高级 -->
  <section class="card">
    <details>
      <summary>高级设置</summary>
      <div class="grid adv">
        <label class="field">
          <span>在线检测间隔（秒）</span>
          <input id="interval" type="number" min="5" max="600">
        </label>
        <label class="field">
          <span>掉线重试间隔（秒）</span>
          <input id="interval_offline" type="number" min="1" max="300">
        </label>
        <label class="field">
          <span>退避上限（秒）</span>
          <input id="interval_offline_max" type="number" min="5" max="600">
        </label>
        <label class="field">
          <span>请求超时（秒）</span>
          <input id="timeout" type="number" min="3" max="60">
        </label>
        <label class="field wide">
          <span>在线检测地址（空格分隔，必须用域名）</span>
          <input id="online_check_urls" type="text">
        </label>
        <label class="field wide">
          <span>探测地址（空格分隔）</span>
          <input id="probe_urls" type="text">
        </label>
        <label class="field wide">
          <span>门户入口 URL（留空 = 每次自动探测，推荐）</span>
          <input id="entry_url" type="text" placeholder="留空即可">
        </label>
      </div>
    </details>
  </section>

  <!-- 日志 -->
  <section class="card">
    <div class="loghead">
      <h2>运行日志</h2>
      <div>
        <button id="btnClear" class="btn tiny">清空</button>
        <button id="btnOpenLog" class="btn tiny">打开日志文件</button>
        <button id="btnQuit" class="btn tiny">退出程序</button>
      </div>
    </div>
    <pre id="log" class="log">加载中...</pre>
  </section>

  <footer class="foot">
    <span id="saveHint"></span>
    <button id="btnSave" class="btn primary">保存设置</button>
  </footer>
</div>

<script>
__JS__
</script>
</body>
</html>
"""

STYLE_CSS = r"""
:root{
  --bg:#0f1216; --card:#171b21; --card2:#1d2229; --line:#272d36;
  --text:#e8edf3; --dim:#8b96a5; --accent:#3d8bfd; --accent2:#2f6fd0;
  --ok:#25c26e; --warn:#f0a53a; --err:#f2555a;
  --radius:14px;
}
@media (prefers-color-scheme: light){
  :root{
    --bg:#f4f6f9; --card:#ffffff; --card2:#f7f9fc; --line:#e3e8ef;
    --text:#1b2430; --dim:#66717f; --accent:#2f6fd0; --accent2:#255cb0;
    --ok:#12a35c; --warn:#c47f12; --err:#d33a3f;
  }
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--bg); color:var(--text);
  font-family:"Segoe UI","Microsoft YaHei UI","Microsoft YaHei",system-ui,sans-serif;
  font-size:14px; line-height:1.55; padding:26px 20px 40px;
}
.wrap{max-width:940px;margin:0 auto}

/* 顶部 */
.top{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:20px}
.brand{display:flex;align-items:center;gap:12px}
.logo{
  width:42px;height:42px;border-radius:12px;flex:none;
  background:linear-gradient(140deg,var(--accent),#7c4dff);
  color:#fff;font-weight:700;font-size:20px;display:grid;place-items:center;
}
h1{font-size:17px;margin:0;font-weight:650;letter-spacing:.2px}
.sub{margin:1px 0 0;color:var(--dim);font-size:12px}
.statusbox{display:flex;align-items:center;gap:12px}
.ip{color:var(--dim);font-size:12px;font-variant-numeric:tabular-nums}

.pill{
  display:inline-flex;align-items:center;gap:7px;
  padding:6px 13px;border-radius:999px;font-size:13px;font-weight:600;
  background:var(--card2);border:1px solid var(--line);white-space:nowrap;
}
.pill i{width:8px;height:8px;border-radius:50%;background:var(--dim);flex:none}
.pill.online{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 35%,transparent)}
.pill.online i{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 18%,transparent)}
.pill.offline,.pill.error{color:var(--err);border-color:color-mix(in srgb,var(--err) 35%,transparent)}
.pill.offline i,.pill.error i{background:var(--err)}
.pill.authing,.pill.checking{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 35%,transparent)}
.pill.authing i,.pill.checking i{background:var(--accent);animation:blink 1s infinite}
@keyframes blink{50%{opacity:.25}}

/* 卡片 */
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
@media (max-width:760px){.grid{grid-template-columns:1fr}}
.card{
  background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:18px 18px 16px;margin-bottom:16px;
}
.grid .card{margin-bottom:0}
.card h2{font-size:13px;margin:0 0 14px;color:var(--dim);font-weight:650;letter-spacing:.6px;text-transform:uppercase}

/* 表单 */
.field{display:block;margin-bottom:12px}
.field>span{display:block;font-size:12px;color:var(--dim);margin-bottom:5px}
.field.wide{grid-column:1/-1}
input[type=text],input[type=password],input[type=number],select{
  width:100%;padding:9px 11px;border-radius:9px;
  background:var(--card2);border:1px solid var(--line);color:var(--text);
  font-size:13.5px;font-family:inherit;outline:none;transition:border-color .15s,box-shadow .15s;
}
input:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 16%,transparent)}
.hint{font-size:11.5px;color:var(--dim);margin:10px 0 0;line-height:1.5}
.hint.tip{
  margin:-4px 0 14px;padding-left:9px;
  border-left:2px solid color-mix(in srgb,var(--accent) 55%,transparent);
}

/* 开关行 */
.row{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:11px 0;border-bottom:1px solid var(--line)}
.row:last-of-type{border-bottom:none}
.rowtitle{font-size:13.5px;font-weight:600}
.rowdesc{font-size:11.5px;color:var(--dim);margin-top:2px}
.switch{position:relative;width:42px;height:24px;flex:none;cursor:pointer}
.switch input{opacity:0;width:0;height:0;position:absolute}
.switch span{position:absolute;inset:0;background:var(--line);border-radius:999px;transition:background .18s}
.switch span::after{
  content:"";position:absolute;width:18px;height:18px;border-radius:50%;
  background:#fff;top:3px;left:3px;transition:transform .18s;box-shadow:0 1px 3px rgba(0,0,0,.35);
}
.switch input:checked+span{background:var(--accent)}
.switch input:checked+span::after{transform:translateX(18px)}

/* 按钮 */
.btns{display:flex;gap:9px;margin-top:16px}
.btn{
  flex:1;padding:9px 14px;border-radius:9px;cursor:pointer;
  background:var(--card2);border:1px solid var(--line);color:var(--text);
  font-size:13px;font-family:inherit;font-weight:600;transition:.15s;
}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn:disabled{opacity:.45;cursor:not-allowed;border-color:var(--line);color:var(--text)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.primary:hover{background:var(--accent2);border-color:var(--accent2);color:#fff}
.btn.tiny{flex:none;padding:5px 11px;font-size:12px;font-weight:500}

/* 高级 */
details summary{
  cursor:pointer;font-size:13px;font-weight:650;color:var(--dim);
  letter-spacing:.6px;text-transform:uppercase;list-style:none;
}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"▸ ";display:inline-block;transition:transform .15s}
details[open] summary::before{transform:rotate(90deg)}
.adv{grid-template-columns:repeat(4,1fr);margin-top:16px;gap:12px}
@media (max-width:760px){.adv{grid-template-columns:1fr 1fr}}

/* 日志 */
.loghead{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.loghead h2{margin:0}
.loghead>div{display:flex;gap:8px}
.log{
  margin:0;padding:13px;border-radius:10px;background:var(--card2);
  border:1px solid var(--line);max-height:330px;overflow:auto;
  font-family:Consolas,"Cascadia Mono",ui-monospace,monospace;
  font-size:12px;line-height:1.65;color:var(--dim);white-space:pre-wrap;word-break:break-all;
}
.log .t{color:color-mix(in srgb,var(--dim) 70%,transparent);margin-right:7px}
.log .m-ok{color:var(--ok)}
.log .m-err{color:var(--err)}
.log .m-warn{color:var(--warn)}

/* 底部 */
.foot{display:flex;align-items:center;justify-content:flex-end;gap:14px;margin-top:4px}
.foot .btn{flex:none;min-width:120px}
#saveHint{font-size:12.5px;color:var(--ok);opacity:0;transition:opacity .3s}
#saveHint.show{opacity:1}
#saveHint.err{color:var(--err)}
"""

APP_JS = r"""
const $ = id => document.getElementById(id);
const FIELDS = ["username","service","portal_host","interval","interval_offline",
                "interval_offline_max","timeout","online_check_urls","probe_urls","entry_url"];

let passwordTouched = false;
let lastLogKey = "";

function fmtTime(ts){
  if(!ts) return "";
  const d = new Date(ts*1000);
  return d.toTimeString().slice(0,8);
}

function esc(s){ return s; }

async function api(path, opts){
  const r = await fetch(path, opts);
  if(!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

// ---------- 加载配置 ----------
async function loadConfig(){
  const c = await api("/api/config");
  FIELDS.forEach(k => { if($(k) != null && c[k] != null) $(k).value = c[k]; });
  $("enabled").checked = !!c.enabled;
  $("password").placeholder = c.has_password
      ? "已保存（留空则不修改）" : "请输入校园网密码";
  const auto = await api("/api/autostart");
  $("autostart").checked = !!auto.enabled;
}

// ---------- 轮询状态 ----------
const STATE_TEXT = {
  online:"在线", offline:"离线", authing:"认证中", checking:"检测中",
  disabled:"已关闭", error:"异常", idle:"已停止"
};

async function poll(){
  try{
    const s = await api("/api/state");
    let cls = s.state || "idle";
    // 还没填账号时后端报的是 error，但对用户来说这是「没配置」，不是出故障
    if(cls === "error" && /未配置/.test(s.message || "")) cls = "idle";
    $("pill").className = "pill " + cls;
    let txt = STATE_TEXT[s.state] || s.state || "—";
    if(cls === "idle" && /未配置/.test(s.message || "")) txt = "未配置";
    if(s.state === "offline" && s.fail_count) txt = "离线 · 失败" + s.fail_count + "次";
    $("pillText").textContent = txt;
    $("ipText").textContent = s.local_ip ? "本机 IP " + s.local_ip : "本机 IP —";
    $("btnAuth").disabled = !!s.manual_busy;
    $("btnForce").disabled = !!s.manual_busy;
    renderLogs(s.logs || []);
  }catch(e){
    $("pill").className = "pill error";
    $("pillText").textContent = "未连接";
  }
}

function renderLogs(lines){
  const key = lines.length + "|" + (lines.length ? lines[lines.length-1].t : 0);
  if(key === lastLogKey) return;
  lastLogKey = key;
  const el = $("log");
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  el.innerHTML = "";
  lines.forEach(l => {
    const div = document.createElement("div");
    const t = document.createElement("span");
    t.className = "t"; t.textContent = fmtTime(l.t);
    const m = document.createElement("span");
    m.textContent = l.m;
    if(/成功|已在线|在线/.test(l.m)) m.className = "m-ok";
    else if(/错误|失败|异常/.test(l.m)) m.className = "m-err";
    else if(/⚠|警告|过期/.test(l.m)) m.className = "m-warn";
    div.appendChild(t); div.appendChild(m);
    el.appendChild(div);
  });
  if(atBottom) el.scrollTop = el.scrollHeight;
}

// ---------- 保存 ----------
function hint(msg, isErr){
  const h = $("saveHint");
  h.textContent = msg;
  h.className = isErr ? "show err" : "show";
  setTimeout(() => h.classList.remove("show"), 2600);
}

async function save(){
  const body = {};
  FIELDS.forEach(k => {
    const el = $(k);
    if(!el) return;
    body[k] = (el.type === "number") ? Number(el.value) : el.value;
  });
  if(passwordTouched && $("password").value) body.password = $("password").value;
  body.enabled = $("enabled").checked;
  try{
    await api("/api/config", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify(body)
    });
    if(passwordTouched) { $("password").value = ""; $("password").placeholder = "已保存（留空则不修改）"; }
    passwordTouched = false;
    hint("已保存");
  }catch(e){ hint("保存失败: " + e.message, true); }
}

// ---------- 动作 ----------
async function authNow(force){
  $("btnAuth").disabled = true; $("btnForce").disabled = true;
  try{
    const r = await api("/api/auth", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({force})
    });
    hint(r.ok ? ("✔ " + r.message) : ("✘ " + r.message), !r.ok);
  }catch(e){ hint("请求失败: " + e.message, true); }
  poll();
}

// ---------- 绑定 ----------
$("password").addEventListener("input", () => { passwordTouched = true; });
$("enabled").addEventListener("change", async e => {
  await api("/api/config", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({enabled: e.target.checked})
  });
  hint(e.target.checked ? "自动认证已开启" : "自动认证已关闭");
});
$("autostart").addEventListener("change", async e => {
  try{
    const r = await api("/api/autostart", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({enabled: e.target.checked})
    });
    if(!r.ok){ e.target.checked = !e.target.checked; hint(r.message || "设置失败", true); }
    else hint(e.target.checked ? "已加入开机启动" : "已取消开机启动");
  }catch(err){ e.target.checked = !e.target.checked; hint("设置失败", true); }
});
$("btnAuth").addEventListener("click", () => authNow(false));
$("btnForce").addEventListener("click", () => authNow(true));
$("btnSave").addEventListener("click", save);
$("btnClear").addEventListener("click", async () => {
  await api("/api/logs/clear", {method:"POST"});
  lastLogKey = ""; poll();
});
$("btnOpenLog").addEventListener("click", () => { fetch("/api/openlog", {method:"POST"}); });
$("btnQuit").addEventListener("click", async () => {
  if(!confirm("确定要退出吗？\n\n退出后不再自动认证，下次开机登录时会自动重新启动。")) return;
  try{ await fetch("/api/quit", {method:"POST"}); }catch(e){}
  document.body.innerHTML =
    '<div style="display:grid;place-items:center;height:70vh;color:#8b96a5;font-family:system-ui">'
    + '<div style="text-align:center"><div style="font-size:15px;margin-bottom:8px">已退出</div>'
    + '<div style="font-size:12.5px">可以直接关闭这个页面了</div></div></div>';
});

loadConfig().catch(e => hint("加载配置失败: " + e.message, true));
poll();
setInterval(poll, 1500);
"""


def render_index() -> str:
    return INDEX_HTML.replace("__CSS__", STYLE_CSS).replace("__JS__", APP_JS)
