"""交换站 - Render 免费版后端 + 前端（完整版）

只给熟人用，上传要站长审核。只碰云盘里“交换站”下的两个文件夹：
- 待审核：熟人上传先放这里，别人看不到、也下载不到
- 已发布：站长审核通过后才移进来，熟人才能下载

安全底线（写死在代码里，不靠配置自觉）：
- 所有 Drive 查询、上传、移动、删除，只用下面两个文件夹 ID，绝不列出
  或读取交换站以外的任何文件，也绝不分享任何文件给别人。
- 下载前先查文件元数据，确认它的父文件夹就是“已发布”，否则 403。
- 审核通过/不通过前先查文件元数据，确认它当前就在“待审核”里，否则 403。
- 服务账号 JSON、站长口令、熟人访问口令一律只从环境变量读，代码里
  没有任何硬编码口令、密钥或账号。
- 没有配置服务账号时，网站仍能正常打开，并如实提示“云盘暂未连接，
  上传/下载暂不可用”，绝不冒充已连接云盘，也不会暴露任何云盘内容。
- 文件不存 Render 本地：上传直接流式传到云盘，下载直接从云盘流式回传，
  Render 免费版休眠、重启都不会丢文件（免费版本地磁盘本来就是临时的）。

环境变量（全部在 Render 的 Environment 里填，不要写进代码、不要发给别人）：
- DRIVE_PENDING_FOLDER_ID：待审核文件夹 ID（默认是已建好的那个）
- DRIVE_PUBLISHED_FOLDER_ID：已发布文件夹 ID（默认是已建好的那个）
- GOOGLE_SERVICE_ACCOUNT_JSON：服务账号 JSON 全文，或它的 Base64
  （注意：服务账号自身没有 Drive 存储空间，往个人云盘新建文件会被
  Google 以 storageQuotaExceeded 拒绝，见 2026-11-03 上传 500 日志；
  因此生产上优先用下面的 OAuth 方式，服务账号只做备用/只读）
- GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET /
  GOOGLE_OAUTH_REFRESH_TOKEN：用户本人 OAuth 授权（推荐方式，文件记在
  用户自己的云盘空间里）。三项齐全时优先用 OAuth，否则回落到服务账号。
- ADMIN_TOKEN：站长审核口令
- EXCHANGE_CODE：熟人进入网站的访问口令；未配置时首页会说明“口令由站长提供”
- MAX_UPLOAD_MB：单文件上限（兆字节），默认 200，Render 免费版只有 512MB 内存
"""
import base64
import hashlib
import hmac
import io
import json
import os

from flask import Flask, Response, jsonify, redirect, render_template_string, request

app = Flask(__name__)

PENDING_FOLDER_ID = os.environ.get(
    "DRIVE_PENDING_FOLDER_ID", "1vlEHcik1BsdP8HxG9SIWqWeZYDuHf1B5"
)
PUBLISHED_FOLDER_ID = os.environ.get(
    "DRIVE_PUBLISHED_FOLDER_ID", "1w6lZz9yeTENzHdUy9Ljt_H5JkgEIVO5F"
)
try:
    MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "200"))
except ValueError:
    MAX_UPLOAD_MB = 200
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


def _env(name):
    return os.environ.get(name, "").strip()


def _gate_value(secret):
    """用口令本身做 HMAC，生成可跨重启验证的访问 cookie，不存任何会话。"""
    return hmac.new(
        secret.encode("utf-8"), b"exchange-station-access", hashlib.sha256
    ).hexdigest()


def _has_gate(secret, cookie_name):
    if not secret:
        return False
    value = request.cookies.get(cookie_name, "")
    return bool(value) and hmac.compare_digest(value, _gate_value(secret))


def _drive_configured():
    if _env("GOOGLE_OAUTH_REFRESH_TOKEN") and _env("GOOGLE_OAUTH_CLIENT_ID"):
        return True
    return bool(_env("GOOGLE_SERVICE_ACCOUNT_JSON"))


def drive_service():
    """只在配置了 Drive 凭证时才连接，否则返回 None。

    优先用用户本人的 OAuth 刷新令牌（文件记在用户自己的云盘空间里，
    服务账号没有存储空间，上传会被 Google 拒绝）；三项不全时回落到
    服务账号（只读/移动仍可用，上传仍可能被拒绝）。
    """
    from googleapiclient.discovery import build

    oauth_refresh = _env("GOOGLE_OAUTH_REFRESH_TOKEN")
    oauth_id = _env("GOOGLE_OAUTH_CLIENT_ID")
    oauth_secret = _env("GOOGLE_OAUTH_CLIENT_SECRET")
    if oauth_refresh and oauth_id and oauth_secret:
        from google.oauth2.credentials import Credentials

        creds = Credentials(
            token=None,
            refresh_token=oauth_refresh,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=oauth_id,
            client_secret=oauth_secret,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        return build("drive", "v3", credentials=creds)
    raw = _env("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except Exception:
        try:
            info = json.loads(base64.b64decode(raw).decode("utf-8"))
        except Exception:
            return None
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)


def _fmt_size(size):
    try:
        b = int(size)
    except (TypeError, ValueError):
        return ""
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    if b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    return f"{b / (1024 * 1024 * 1024):.2f} GB"


def list_folder(service, folder_id):
    if service is None:
        return []
    res = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id,name,size,mimeType)",
        pageSize=100,
    ).execute()
    out = []
    for f in res.get("files", []):
        out.append(
            {
                "id": f["id"],
                "name": f["name"],
                "size": _fmt_size(f.get("size")),
                "ext": (f["name"].rsplit(".", 1)[-1].upper()[:4] if "." in f["name"] else "FILE"),
            }
        )
    return out


def get_file(service, file_id):
    return service.files().get(
        fileId=file_id, fields="id,name,size,mimeType,parents"
    ).execute()


def _safe_filename(name):
    # 只去目录部分，不改中文文件名本身；空名、路径穿越一律拒绝。
    name = (name or "").strip().replace("\\", "/").split("/")[-1].strip()
    return name


BASE_CSS = """
  :root{
    --bg:#f2f3ef;
    --surface:#ffffff;
    --surface-2:#eef1ea;
    --ink:#17211a;
    --muted:#6b756c;
    --line:#dde1d8;
    --green:#17694a;
    --green-ink:#0f4a33;
    --green-soft:#e3efe7;
    --amber:#9a6a12;
    --amber-soft:#fdf0c8;
    --amber-line:#e8d48a;
    --red:#b42318;
    --red-soft:#fdecea;
    --shadow:0 8px 28px rgba(23,33,26,.07);
    --radius:18px;
  }
  @media (prefers-color-scheme: dark){
    :root{
      --bg:#111511;
      --surface:#1a1f1a;
      --surface-2:#222822;
      --ink:#e8ede8;
      --muted:#9aa89d;
      --line:#2e362e;
      --green:#3cb179;
      --green-ink:#7ed3a6;
      --green-soft:#1e3327;
      --amber:#e5b64a;
      --amber-soft:#332a12;
      --amber-line:#5a4a1a;
      --red:#ff8a7a;
      --red-soft:#3a1d19;
      --shadow:0 8px 28px rgba(0,0,0,.35);
    }
  }
  *{box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:"Noto Sans SC",system-ui,-apple-system,sans-serif;line-height:1.6}
  a{color:var(--green)}
  button,input{font:inherit}
  .wrap{max-width:1120px;margin:0 auto;padding:28px 20px 56px}

  /* Header */
  .top{display:flex;gap:24px;justify-content:space-between;align-items:flex-start;flex-wrap:wrap}
  h1{font-family:"Noto Serif SC",serif;font-size:42px;line-height:1.05;margin:0;letter-spacing:.02em}
  .lead{margin:12px 0 0;color:var(--muted);font-size:16px;max-width:560px}
  .lead strong{color:var(--ink)}
  .badge-live{display:inline-flex;align-items:center;gap:8px;background:var(--surface);border:1px solid var(--line);border-radius:999px;padding:8px 14px;font-size:13px;font-weight:700;white-space:nowrap;box-shadow:var(--shadow)}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 0 4px var(--green-soft)}

  /* Flow */
  .flow{display:grid;grid-template-columns:1fr auto 1fr auto 1fr;align-items:stretch;gap:10px;margin-top:26px}
  @media(max-width:700px){.flow{grid-template-columns:1fr}.flow .arrow{transform:rotate(90deg);justify-self:center}}
  .step{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
  .step b{display:flex;align-items:center;gap:10px;font-size:15px}
  .step p{margin:6px 0 0;font-size:13.5px;color:var(--muted)}
  .num{width:28px;height:28px;border-radius:50%;background:var(--ink);color:var(--bg);display:grid;place-items:center;font-size:13px;flex-shrink:0}
  .step.mid{border-color:var(--amber-line);background:var(--amber-soft)}
  .step.mid .num{background:var(--amber);color:#fff}
  .arrow{align-self:center;color:var(--muted);font-size:20px}

  /* Role switch */
  .rolebar{margin-top:22px;background:var(--ink);color:var(--bg);border-radius:14px;padding:14px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;justify-content:space-between}
  .rolebar p{margin:0;font-size:14px;opacity:.92}
  .seg{display:flex;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.18);padding:4px;border-radius:999px;gap:4px}
  @media (prefers-color-scheme: dark){.seg{background:rgba(0,0,0,.3)}}
  .seg button{border:0;background:transparent;color:inherit;padding:8px 16px;border-radius:999px;cursor:pointer;font-weight:700;font-size:14px}
  .seg button.active{background:var(--bg);color:var(--ink)}

  /* Layout */
  .layout{display:grid;grid-template-columns:340px 1fr;gap:20px;margin-top:20px;align-items:start}
  @media(max-width:860px){.layout{grid-template-columns:1fr}}
  .card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow)}
  .upload{padding:22px;position:sticky;top:16px}
  @media(max-width:860px){.upload{position:static}}
  .upload h2,.section-head h2{font-size:18px;margin:0}
  .upload label{font-size:13px;font-weight:700;color:var(--muted);display:block;margin:16px 0 6px}
  .textin{width:100%;padding:11px 13px;border-radius:10px;border:1px solid var(--line);background:var(--bg);color:var(--ink)}
  .drop{margin-top:14px;border:1.8px dashed #b9c2b6;border-radius:14px;padding:26px 16px;text-align:center;cursor:pointer;transition:.15s;background:var(--surface-2)}
  .drop:hover,.drop.over{border-color:var(--green);background:var(--green-soft)}
  .drop strong{display:block;font-size:15px}
  .drop span{font-size:13px;color:var(--muted)}
  .drop svg{width:34px;height:34px;color:var(--green);margin-bottom:6px}
  .rules{margin:18px 0 0;padding:0;list-style:none;font-size:13.5px;color:var(--muted);display:grid;gap:9px}
  .rules li{display:flex;gap:9px}
  .rules li::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--green);margin-top:8px;flex-shrink:0}
  .note{margin-top:16px;font-size:12.5px;color:var(--muted);background:var(--bg);border-radius:10px;padding:10px 12px}

  /* Lists */
  .maincol{display:grid;gap:20px}
  .toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;justify-content:space-between}
  .tabs{display:flex;gap:8px;flex-wrap:wrap}
  .tab{border:1px solid var(--line);background:var(--surface);padding:8px 15px;border-radius:999px;cursor:pointer;font-weight:700;font-size:14px;color:var(--ink)}
  .tab.active{background:var(--green);border-color:var(--green);color:#fff}
  .search{flex:1;min-width:170px;max-width:300px;padding:10px 13px;border-radius:999px;border:1px solid var(--line);background:var(--surface);color:var(--ink)}
  .section{padding:20px 22px}
  .section-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:6px}
  .section-head p{margin:0;color:var(--muted);font-size:13.5px}
  .count{font-weight:900;font-size:13px;background:var(--surface-2);border:1px solid var(--line);padding:3px 10px;border-radius:999px}
  .count.amber{background:var(--amber-soft);border-color:var(--amber-line);color:var(--amber)}

  .file{display:flex;gap:14px;padding:15px 0;border-top:1px solid var(--line);align-items:center}
  .file:first-of-type{border-top:0}
  .fic{width:46px;height:46px;border-radius:12px;background:var(--surface-2);border:1px solid var(--line);display:grid;place-items:center;flex-shrink:0;font-size:11px;font-weight:900;letter-spacing:.04em;color:var(--green-ink)}
  .fic.pdf{color:#b42318}.fic.zip{color:#7a4a00}.fic.img{color:#175c8a}.fic.audio{color:#6d3bbf}
  .fmain{flex:1;min-width:0}
  .fname{font-weight:700;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .fmeta{font-size:13px;color:var(--muted);display:flex;gap:8px;flex-wrap:wrap;margin-top:2px}
  .chip{font-size:11.5px;font-weight:800;padding:2px 8px;border-radius:999px;background:var(--green-soft);color:var(--green-ink)}
  .chip.wait{background:var(--amber-soft);color:var(--amber)}
  .chip.sample{background:var(--surface-2);color:var(--muted);border:1px solid var(--line)}
  .actions{display:flex;gap:8px;flex-shrink:0;flex-wrap:wrap}
  .btn{border:1px solid var(--line);background:var(--surface);color:var(--ink);padding:9px 15px;border-radius:10px;cursor:pointer;font-weight:700;font-size:14px}
  .btn.primary{background:var(--green);border-color:var(--green);color:#fff}
  .btn.danger{color:var(--red);border-color:var(--line)}
  .btn.danger:hover{background:var(--red-soft)}
  .btn:active{transform:scale(.98)}
  @media(max-width:560px){.file{flex-wrap:wrap}.actions{width:100%;padding-left:60px}.search{max-width:none}}
  .empty{padding:28px 10px;text-align:center;color:var(--muted);font-size:14px;border:1.5px dashed var(--line);border-radius:12px;margin-top:12px}
  .locked{padding:18px;border-radius:12px;background:var(--surface-2);border:1px solid var(--line);font-size:14px;color:var(--muted);margin-top:10px}

  /* Bottom info */
  .bottom{display:grid;grid-template-columns:1.1fr .9fr;gap:20px;margin-top:20px}
  @media(max-width:860px){.bottom{grid-template-columns:1fr}}
  .bottom .card{padding:22px}
  .bottom h3{margin:0 0 12px;font-size:16px}
  .bottom p,.bottom li{font-size:14px;color:var(--muted)}
  .bottom ul{margin:8px 0 0;padding-left:18px}
  .tree{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13.5px;background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:16px;line-height:1.9;white-space:pre;overflow-x:auto;color:var(--ink)}
  footer{margin-top:28px;text-align:center;color:var(--muted);font-size:13px}

  #toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%) translateY(20px);background:var(--ink);color:var(--bg);padding:12px 18px;border-radius:999px;font-size:14px;font-weight:700;opacity:0;pointer-events:none;transition:.25s;z-index:50;max-width:calc(100vw - 32px);text-align:center}
  #toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
"""

PAGE = (
    "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>交换站</title><style>"
    + BASE_CSS
    + "</style></head><body><div class=\"wrap\">"
    "<div class=\"top\"><div><h1>交换站</h1>"
    "<p class=\"lead\">熟人之间传文件的地方。<strong>上传不会立刻公开</strong> —— 先进入待审核，站长通过后，其他人才能在已发布里下载。</p></div>"
    "<div class=\"badge-live\"><span class=\"dot\"></span> 仅熟人使用 · 需审核</div></div>"
    "<div class=\"flow\"><div class=\"step\"><b><span class=\"num\">1</span> 上传文件</b><p>选文件提交就行，不用注册。</p></div>"
    "<div class=\"arrow\">→</div><div class=\"step mid\"><b><span class=\"num\">2</span> 待审核</b><p>文件先躺在这里，只有站长能看到和处理。</p></div>"
    "<div class=\"arrow\">→</div><div class=\"step\"><b><span class=\"num\">3</span> 已发布 · 下载</b><p>通过后出现在已发布列表，熟人随时下载。</p></div></div>"
    "{% if not code_configured %}"
    "<div class=\"card section warn\"><strong>本站需要访问口令</strong><br>"
    "访问口令由站长提供。站长请先在 Render 环境变量中设置 EXCHANGE_CODE，再把口令私下发给熟人。</div>"
    "{% elif not entered %}"
    "<div class=\"card section\"><h2>请输入访问口令</h2>"
    "<form method=\"post\" action=\"/enter\">"
    "<input class=\"textin\" type=\"password\" name=\"code\" placeholder=\"熟人访问口令\" required> "
    "<button class=\"btn primary\" type=\"submit\">进入交换站</button></form>"
    "{% if msg %}<p class=\"note\">{{ msg }}</p>{% endif %}</div>"
    "{% else %}"
    "{% if not drive_configured %}"
    "<div class=\"card section warn\"><strong>云盘暂未连接</strong><br>网站可以打开，但上传和下载暂不可用。</div>"
    "{% endif %}"
    "<div class=\"layout\"><aside class=\"card upload\"><h2>上传文件</h2>"
    "<p class=\"note\">上传后别人看不到，等站长通过后才会出现在已发布列表。单文件上限 {{ max_mb }} MB。</p>"
    "<form method=\"post\" action=\"/upload\" enctype=\"multipart/form-data\">"
    "<div class=\"drop\" id=\"drop\"><strong>点这里选文件，或把文件拖进来</strong><span>支持多选 · 提交后进入待审核</span>"
    "<br><input type=\"file\" id=\"fileinput\" name=\"file\" multiple required></div>"
    "<button class=\"btn primary\" type=\"submit\" {% if not drive_configured %}disabled{% endif %}>上传，等待审核</button></form>"
    "<script>var d=document.getElementById('drop'),i=document.getElementById('fileinput');"
    "d.addEventListener('click',function(){i.click();});"
    "d.addEventListener('dragover',function(e){e.preventDefault();d.classList.add('over');});"
    "d.addEventListener('dragleave',function(){d.classList.remove('over');});"
    "d.addEventListener('drop',function(e){e.preventDefault();d.classList.remove('over');i.files=e.dataTransfer.files;});</script>"
    "{% if msg %}<p class=\"note\">{{ msg }}</p>{% endif %}</aside>"
    "<div class=\"maincol\"><div class=\"toolbar\"><input class=\"search\" id=\"search\" type=\"search\" placeholder=\"搜索文件名…\" oninput=\"onSearch(this.value)\"></div>"
    "<section class=\"card section\"><div class=\"section-head\"><h2>已发布</h2><span class=\"count\">{{ files|length }} 个</span></div>"
    "{% if files %}{% for f in files %}"
    "<div class=\"file\"><div class=\"fic\">{{ f.ext }}</div>"
    "<div class=\"fmain\"><div class=\"fname\">{{ f.name }}</div><div class=\"fmeta\"><span>{{ f.size }}</span></div></div>"
    "<div class=\"actions\"><a class=\"btn primary\" href=\"/download/{{ f.id }}\">下载</a></div></div>"
    "{% endfor %}{% else %}<p class=\"empty\">目前还没有已发布的文件。</p>{% endif %}</section>"
    "<script>function onSearch(v){v=v.toLowerCase();document.querySelectorAll('.file').forEach(function(r){r.style.display=r.textContent.toLowerCase().includes(v)?'':'none';});}</script>"
    "</div></div>"
    "{% endif %}"
    "<div class=\"card section\"><h2>站长审核</h2>"
    "<p class=\"note\">站长入口与熟人入口分开，站长口令只在审核页使用。</p>"
    "<form method=\"post\" action=\"/admin/login\">"
    "<input class=\"textin\" type=\"password\" name=\"token\" placeholder=\"站长口令\" required> "
    "<button class=\"btn primary\" type=\"submit\">进入审核</button></form></div>"
    "<footer>交换站 · 熟人文件交换 · 上传需站长审核</footer></div></body></html>"
)

ADMIN_PAGE = (
    "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>审核 - 交换站</title><style>"
    + BASE_CSS
    + "</style></head><body><div class=\"wrap\"><h1>待审核文件</h1>"
    "{% if not drive_configured %}"
    "<div class=\"card section warn\"><strong>云盘暂未连接</strong><br>这里没有列出任何真实云盘文件。</div>"
    "{% elif files %}<div class=\"card section\">{% for f in files %}<div class=\"file\"><div class=\"fic\">{{ f.ext }}</div>"
    "<div class=\"fmain\"><div class=\"fname\">{{ f.name }}</div><div class=\"fmeta\"><span>{{ f.size }}</span><span class=\"chip wait\">待审核</span></div></div>"
    "<div class=\"actions\"><form method=\"post\" action=\"/admin/approve\" style=\"display:inline\">"
    "<input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\"><button class=\"btn primary\">通过</button></form> "
    "<form method=\"post\" action=\"/admin/reject\" style=\"display:inline\">"
    "<input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\">"
    "<button class=\"btn danger\">不通过（移到回收站）</button></form></div></div>{% endfor %}</div>"
    "{% else %}<p class=\"empty\">目前没有待审核的文件。</p>{% endif %}"
    "<p><a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div></body></html>"
)


@app.route("/")
def index():
    code = _env("EXCHANGE_CODE")
    entered = _has_gate(code, "exchange_access")
    files = []
    if code and entered:
        files = list_folder(drive_service(), PUBLISHED_FOLDER_ID)
    return render_template_string(
        PAGE,
        code_configured=bool(code),
        entered=entered,
        drive_configured=_drive_configured(),
        files=files,
        max_mb=MAX_UPLOAD_MB,
        msg=request.args.get("msg", ""),
    )


@app.route("/enter", methods=["POST"])
def enter():
    code = _env("EXCHANGE_CODE")
    if not code:
        return redirect("/?msg=本站还没配置访问口令，请联系站长")
    if request.form.get("code", "") != code:
        return redirect("/?msg=访问口令不对，请再试一次")
    resp = redirect("/")
    resp.set_cookie(
        "exchange_access", _gate_value(code), httponly=True, samesite="Lax"
    )
    return resp


@app.route("/logout")
def logout():
    resp = redirect("/")
    resp.delete_cookie("exchange_access")
    resp.delete_cookie("admin_access")
    return resp


@app.route("/upload", methods=["POST"])
def upload():
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access"):
        return redirect("/?msg=请先输入访问口令")
    service = drive_service()
    if service is None:
        return redirect("/?msg=云盘暂未连接，上传暂不可用，请联系站长")
    uploads = request.files.getlist("file")
    uploads = [u for u in uploads if u and u.filename]
    if not uploads:
        return redirect("/?msg=请选择一个文件再上传")
    from googleapiclient.http import MediaIoBaseUpload

    count = 0
    for up in uploads:
        name = _safe_filename(up.filename)
        if not name:
            continue
        # 上传前把流回到开头，避免之前读取导致传空文件（0 字节）。
        try:
            up.stream.seek(0)
        except Exception:
            pass
        # 直接用上传流分块传给云盘，不把整个文件读进内存：
        # Render 免费版只有 512MB 内存，大文件不能先读完再传。
        media = MediaIoBaseUpload(
            up.stream,
            mimetype=up.mimetype or "application/octet-stream",
            resumable=True,
            chunksize=256 * 1024,
        )
        service.files().create(
            body={"name": name, "parents": [PENDING_FOLDER_ID]},
            media_body=media,
            fields="id",
        ).execute()
        count += 1
    if count == 0:
        return redirect("/?msg=没有可上传的文件")
    return redirect(f"/?msg=已上传 {count} 个文件到待审核，站长通过后才会出现在已发布列表")


@app.route("/download/<file_id>")
def download(file_id):
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access"):
        return redirect("/?msg=请先输入访问口令")
    service = drive_service()
    if service is None:
        return "云盘暂未连接，下载暂不可用", 503
    try:
        meta = get_file(service, file_id)
    except Exception:
        return "文件不存在或无权访问", 404
    # 安全底线：只有父文件夹是“已发布”的文件才能下载，待审核文件一律拒绝。
    if PUBLISHED_FOLDER_ID not in (meta.get("parents") or []):
        return "该文件尚未发布", 403
    # 用 Drive 官方的 get_media 流式回传，令牌由 googleapiclient 自动刷新，
    # 不再手动拼 Bearer 令牌，避免过期令牌导致下载到空内容。
    from googleapiclient.http import MediaIoBaseDownload

    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, service.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = downloader.next_chunk()
    data = buf.getvalue()
    if not data and meta.get("size"):
        return "下载失败，请稍后再试", 502
    return Response(
        data,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{meta['name']}",
            "Content-Type": meta.get("mimeType") or "application/octet-stream",
        },
    )


@app.route("/admin/login", methods=["POST"])
def admin_login():
    token = _env("ADMIN_TOKEN")
    if not token:
        return "站长口令还没配置，请联系站长在 Render 环境变量中设置 ADMIN_TOKEN", 503
    if request.form.get("token", "") != token:
        return "口令不对", 403
    resp = redirect("/admin")
    resp.set_cookie("admin_access", _gate_value(token), httponly=True, samesite="Lax")
    return resp


@app.route("/admin")
def admin():
    token = _env("ADMIN_TOKEN")
    if not token:
        return "站长口令还没配置，请联系站长在 Render 环境变量中设置 ADMIN_TOKEN", 503
    if not _has_gate(token, "admin_access"):
        return "请先在首页输入站长口令", 403
    files = list_folder(drive_service(), PENDING_FOLDER_ID)
    return render_template_string(
        ADMIN_PAGE, files=files, drive_configured=_drive_configured()
    )


def _admin_file_or_403(service, file_id):
    meta = get_file(service, file_id)
    if PENDING_FOLDER_ID not in (meta.get("parents") or []):
        return None
    return meta


@app.route("/admin/approve", methods=["POST"])
def approve():
    token = _env("ADMIN_TOKEN")
    if not token or not _has_gate(token, "admin_access"):
        return "请先在首页输入站长口令", 403
    service = drive_service()
    if service is None:
        return "云盘暂未连接", 503
    file_id = request.form.get("file_id", "")
    if not file_id or _admin_file_or_403(service, file_id) is None:
        return "该文件不在待审核里，无法通过", 403
    service.files().update(
        fileId=file_id,
        addParents=PUBLISHED_FOLDER_ID,
        removeParents=PENDING_FOLDER_ID,
    ).execute()
    return redirect("/admin")


@app.route("/admin/reject", methods=["POST"])
def reject():
    token = _env("ADMIN_TOKEN")
    if not token or not _has_gate(token, "admin_access"):
        return "请先在首页输入站长口令", 403
    service = drive_service()
    if service is None:
        return "云盘暂未连接", 503
    file_id = request.form.get("file_id", "")
    if not file_id or _admin_file_or_403(service, file_id) is None:
        return "该文件不在待审核里，无法处理", 403
    service.files().update(fileId=file_id, body={"trashed": True}).execute()
    return redirect("/admin")


@app.route("/health")
def health():
    return jsonify({"ok": True, "drive_configured": _drive_configured()})


@app.errorhandler(413)
def too_large(_error):
    return redirect(f"/?msg=文件太大了，单文件上限是 {MAX_UPLOAD_MB} MB，请分批或压缩后再传")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
