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
        size = f.get("size")
        try:
            size_text = f"{int(size) // 1024} KB" if size else ""
        except (TypeError, ValueError):
            size_text = ""
        out.append({"id": f["id"], "name": f["name"], "size": size_text})
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
body{font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
max-width:760px;margin:32px auto;padding:0 16px;color:#222}
h1{font-size:26px} h2{font-size:18px;margin:0 0 12px}
.card{border:1px solid #ddd;border-radius:14px;padding:18px;margin:18px 0}
.note{color:#666;font-size:14px;line-height:1.7}
.warn{background:#fff4e5;border:1px solid #f0c27a;border-radius:10px;padding:12px}
a{color:#0b5fff} input,button{padding:10px;margin:6px 0;font-size:15px}
button{cursor:pointer} ul{line-height:2}
.drop{border:2px dashed #aaa;border-radius:12px;padding:24px;text-align:center;color:#555}
.drop.drag{border-color:#0b5fff;background:#eef4ff}
.filelist{color:#333;font-size:14px;white-space:pre-wrap;word-break:break-all}
"""

PAGE = (
    "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>交换站</title><style>"
    + BASE_CSS
    + "</style></head><body>"
    "<h1>交换站</h1>"
    "<p class=\"note\">只给熟人用。上传先进入待审核，站长通过后才会在已发布里出现，别人才能下载。"
    "Render 免费版 15 分钟没人用会休眠，第一次打开要等约 1 分钟唤醒，这是正常的。</p>"
    "{% if not code_configured %}"
    "<div class=\"card warn\"><strong>本站需要访问口令</strong><br>"
    "访问口令由站长提供。站长请先在 Render 环境变量中设置 EXCHANGE_CODE，"
    "再把口令私下发给熟人，不要写在公开页面上。</div>"
    "{% elif not entered %}"
    "<div class=\"card\"><h2>请输入访问口令</h2>"
    "<form method=\"post\" action=\"/enter\">"
    "<input type=\"password\" name=\"code\" placeholder=\"熟人访问口令\" required> "
    "<button type=\"submit\">进入交换站</button></form>"
    "{% if msg %}<p class=\"note\">{{ msg }}</p>{% endif %}</div>"
    "{% else %}"
    "{% if not drive_configured %}"
    "<div class=\"card warn\"><strong>云盘暂未连接</strong><br>"
    "网站可以打开，但上传和下载暂不可用。请联系站长配置云盘连接后再试。"
    "这里没有列出任何真实云盘文件。</div>"
    "{% endif %}"
    "<div class=\"card\"><h2>已发布文件</h2>"
    "{% if files %}<ul>{% for f in files %}"
    "<li><a href=\"/download/{{ f.id }}\">{{ f.name }}</a>{% if f.size %}（{{ f.size }}）{% endif %}</li>"
    "{% endfor %}</ul>"
    "{% else %}<p>目前还没有已发布的文件。</p>{% endif %}</div>"
    "<div class=\"card\"><h2>上传文件（进入待审核）</h2>"
    "<p class=\"note\">上传后别人看不到，等站长通过后才会出现在已发布列表。"
    "单文件上限 {{ max_mb }} MB。</p>"
    "<form id=\"upform\" method=\"post\" action=\"/upload\" enctype=\"multipart/form-data\">"
    "<div class=\"drop\" id=\"drop\">把文件拖到这里，或点下面选择文件"
    "<br><input type=\"file\" id=\"fileinput\" name=\"file\" multiple required></div>"
    "<div class=\"filelist\" id=\"filelist\"></div>"
    "<button type=\"submit\" {% if not drive_configured %}disabled{% endif %}>上传，等待审核</button></form>"
    "<script>var d=document.getElementById('drop'),i=document.getElementById('fileinput'),"
    "l=document.getElementById('filelist');"
    "function show(){l.textContent=Array.from(i.files).map(function(f){return f.name;}).join('\\n');}"
    "i.addEventListener('change',show);"
    "['dragover','dragenter'].forEach(function(e){d.addEventListener(e,function(ev){ev.preventDefault();d.classList.add('drag');});});"
    "['dragleave','drop'].forEach(function(e){d.addEventListener(e,function(ev){ev.preventDefault();d.classList.remove('drag');});});"
    "d.addEventListener('drop',function(ev){i.files=ev.dataTransfer.files;show();});</script>"
    "{% if msg %}<p class=\"note\">{{ msg }}</p>{% endif %}</div>"
    "{% endif %}"
    "<div class=\"card\"><h2>站长审核</h2>"
    "<p class=\"note\">站长入口与熟人入口分开，站长口令只在审核页使用。</p>"
    "<form method=\"post\" action=\"/admin/login\">"
    "<input type=\"password\" name=\"token\" placeholder=\"站长口令\" required> "
    "<button type=\"submit\">进入审核</button></form></div>"
    "</body></html>"
)

ADMIN_PAGE = (
    "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>审核 - 交换站</title><style>"
    + BASE_CSS
    + "</style></head><body><h1>待审核文件</h1>"
    "{% if not drive_configured %}"
    "<div class=\"card warn\"><strong>云盘暂未连接</strong><br>"
    "这里没有列出任何真实云盘文件。请联系站长配置云盘连接后再审核。</div>"
    "{% elif files %}<div class=\"card\"><ul>{% for f in files %}<li>{{ f.name }}"
    "{% if f.size %}（{{ f.size }}）{% endif %}<br>"
    "<form method=\"post\" action=\"/admin/approve\" style=\"display:inline\">"
    "<input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\"><button>通过</button></form> "
    "<form method=\"post\" action=\"/admin/reject\" style=\"display:inline\">"
    "<input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\">"
    "<button>不通过（移到回收站）</button></form></li>{% endfor %}</ul></div>"
    "{% else %}<p>目前没有待审核的文件。</p>{% endif %}"
    "<p><a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></body></html>"
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
    # 流式回传，不把整个文件读进内存。
    import requests

    creds = service._http.credentials
    token = creds.token
    if not token:
        from google.auth.transport.requests import Request as AuthRequest

        creds.refresh(AuthRequest())
        token = creds.token
    url = (
        f"https://www.googleapis.com/drive/v3/files/{file_id}"
        "?alt=media&supportsAllDrives=false"
    )
    req = requests.get(
        url, headers={"Authorization": f"Bearer {token}"}, stream=True, timeout=120
    )
    if req.status_code != 200:
        return "下载失败，请稍后再试", 502

    def generate():
        try:
            for chunk in req.iter_content(chunk_size=256 * 1024):
                if chunk:
                    yield chunk
        finally:
            req.close()

    return Response(
        generate(),
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
