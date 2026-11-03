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
from collections import deque
from datetime import datetime, timedelta, timezone
import hmac
import io
import json
import os
import time
from urllib.parse import quote

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


def _relay_backend():
    return (_env("RELAY_BACKEND") or "").rstrip("/")


def _is_relay():
    return bool(_relay_backend())


def _proxy_to_backend(path):
    """Stream a backend response back to the browser, forwarding gate cookies."""
    import requests as _rq
    url = _relay_backend() + path
    # Forward only the gate cookies, never any other header
    cookies = {}
    for _k in ("exchange_access", "admin_access", "feedback_user"):
        if _k in request.cookies:
            cookies[_k] = request.cookies[_k]
    try:
        r = _rq.get(url, cookies=cookies, stream=True, timeout=300)
    except Exception as e:
        return f"中转后端暂时连不上：{e}", 502
    headers = {}
    for _h in ("Content-Type", "Content-Disposition", "Content-Length", "Cache-Control"):
        if _h in r.headers:
            headers[_h] = r.headers[_h]
    return Response(r.iter_content(chunk_size=1024 * 1024), status=r.status_code, headers=headers)


def _relay_files():
    """In relay mode, fetch published+pending lists from the backend (Render)."""
    import requests as _rq
    cookies = {}
    for _k in ("exchange_access", "admin_access", "feedback_user"):
        if _k in request.cookies:
            cookies[_k] = request.cookies[_k]
    try:
        r = _rq.get(_relay_backend() + "/api/files", cookies=cookies, timeout=15)
        return r.json()
    except Exception:
        return {"published": [], "pending": [], "folders": []}


def _proxy_post_to_backend(path):
    """Forward an admin POST form to the backend and return its result."""
    import requests as _rq
    cookies = {}
    for _k in ("exchange_access", "admin_access", "feedback_user"):
        if _k in request.cookies:
            cookies[_k] = request.cookies[_k]
    try:
        r = _rq.post(_relay_backend() + path, cookies=cookies, data=request.form.to_dict(), timeout=60, allow_redirects=False)
    except Exception as e:
        return f"中转后端暂时连不上：{e}", 502
    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
        loc = r.headers["Location"]
        # Backend redirects to its own /admin pages; keep the user on the relay host
        if loc.startswith("/"):
            return redirect(loc)
        return redirect("/")
    return (r.text, r.status_code)


def _drive_configured():
    if _env("GOOGLE_OAUTH_REFRESH_TOKEN") and _env("GOOGLE_OAUTH_CLIENT_ID"):
        return True
    return bool(_env("GOOGLE_SERVICE_ACCOUNT_JSON"))



ACCESS_LOG = deque(maxlen=500)
EXPECTED_SIZES = {}  # file_id -> expected bytes, from direct upload completion; only for files uploaded via this version
_PUB_CACHE = {'t': 0, 'files': None, 'folders': None}


def _cached_published(service):
    now = time.time()
    if _PUB_CACHE['files'] is not None and now - _PUB_CACHE['t'] < 30:
        return _PUB_CACHE['files'], _PUB_CACHE['folders'] or []
    files, folders = _list_published_tree(service)
    _PUB_CACHE['files'] = files
    _PUB_CACHE['folders'] = folders
    _PUB_CACHE['t'] = now
    return files, folders


def _invalidate_pub_cache():
    _PUB_CACHE['files'] = None
    _PUB_CACHE['folders'] = None
    _PUB_CACHE['t'] = 0

_DRIVE_LOG_BUFFER = []
_DRIVE_LOG_FILE_ID = [None]


def _flush_drive_log():
    """把缓冲的日志行追加到云盘里的 交换站访问日志.txt。
    文件放在云盘根目录（不在待审核/已发布里，熟人看不到），找不到就新建一个。
    失败时静默跳过，绝不影响上传/下载主流程；缓冲的行会留在内存里下次再试。"""
    global _DRIVE_LOG_BUFFER
    if not _DRIVE_LOG_BUFFER:
        return
    try:
        service = drive_service()
        if service is None:
            return
        lines = _DRIVE_LOG_BUFFER
        _DRIVE_LOG_BUFFER = []
        text = "".join(lines)
        fid = _DRIVE_LOG_FILE_ID[0] or _env("ACCESS_LOG_DRIVE_FILE_ID")
        if not fid:
            res = service.files().list(
                q="name = '交换站访问日志.txt' and trashed = false",
                fields="files(id)", pageSize=5,
            ).execute()
            files = res.get("files") or []
            fid = files[0]["id"] if files else None
        if fid:
            _DRIVE_LOG_FILE_ID[0] = fid
            from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

            buf = io.BytesIO()
            dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=fid))
            done = False
            while not done:
                _, done = dl.next_chunk()
            old = buf.getvalue().decode("utf-8", errors="ignore")
            media = MediaIoBaseUpload(io.BytesIO((old + text).encode("utf-8")), mimetype="text/plain", resumable=True)
            service.files().update(fileId=fid, media_body=media).execute()
        else:
            from googleapiclient.http import MediaIoBaseUpload

            media = MediaIoBaseUpload(io.BytesIO(text.encode("utf-8")), mimetype="text/plain", resumable=True)
            created = service.files().create(body={"name": "交换站访问日志.txt"}, media_body=media, fields="id").execute()
            _DRIVE_LOG_FILE_ID[0] = created.get("id")
    except Exception as e:
        print(f"[access] drive log flush failed: {e}", flush=True)
        _DRIVE_LOG_BUFFER = lines + _DRIVE_LOG_BUFFER



ACCOUNTS_FILE_NAME = "交换站账号.txt"


def _read_accounts(service):
    if service is None:
        return []
    try:
        res = service.files().list(q=f"name = '{ACCOUNTS_FILE_NAME}' and trashed = false", fields="files(id)", pageSize=5).execute()
        fl = res.get("files") or []
        if not fl:
            return []
        from googleapiclient.http import MediaIoBaseDownload
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=fl[0]["id"]))
        done = False
        while not done:
            _, done = dl.next_chunk()
        out = []
        for line in buf.getvalue().decode("utf-8", errors="ignore").strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                out.append({"name": parts[0], "password": parts[1], "created": parts[2], "last": parts[3] if len(parts) > 3 else "", "disabled": parts[4] if len(parts) > 4 else "0"})
        return out
    except Exception:
        return []


def _write_accounts(service, accounts):
    from googleapiclient.http import MediaIoBaseUpload
    body = "\n".join(f"{a['name']}\t{a['password']}\t{a['created']}\t{a.get('last','')}\t{a.get('disabled','0')}" for a in accounts)
    media = MediaIoBaseUpload(io.BytesIO(body.encode("utf-8")), mimetype="text/plain", resumable=False)
    try:
        res = service.files().list(q=f"name = '{ACCOUNTS_FILE_NAME}' and trashed = false", fields="files(id)", pageSize=5).execute()
        fl = res.get("files") or []
        if fl:
            service.files().update(fileId=fl[0]["id"], media_body=media).execute()
        else:
            service.files().create(body={"name": ACCOUNTS_FILE_NAME, "mimeType": "text/plain"}, media_body=media, fields="id").execute()
        return True
    except Exception as e:
        print(f"[accounts] write failed: {e}", flush=True)
        return False


def _account_gate():
    return request.cookies.get("feedback_user", "")


# ---- 私人区：每个注册账号在云盘里有一个以账号名命名的专属文件夹，
# 放在“交换站私人区”根文件夹下；只有本人和站长能看里面的文件，
# 其他人即使拿到文件链接，没有本人登录状态也打不开。
PRIVATE_ROOT_NAME = "交换站私人区"
_PRIVATE_ROOT_ID = [None]


def _drive_q(v):
    return (v or "").replace("\\", "\\\\").replace("'", "\\'")


def _private_root_id(service):
    if _env("PRIVATE_FOLDER_ID"):
        return _env("PRIVATE_FOLDER_ID")
    if _PRIVATE_ROOT_ID[0]:
        return _PRIVATE_ROOT_ID[0]
    if service is None:
        return None
    res = service.files().list(
        q=f"name = '{PRIVATE_ROOT_NAME}' and 'root' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
        fields="files(id)", pageSize=5).execute()
    fl = res.get("files") or []
    if fl:
        _PRIVATE_ROOT_ID[0] = fl[0]["id"]
        return fl[0]["id"]
    created = service.files().create(
        body={"name": PRIVATE_ROOT_NAME, "mimeType": "application/vnd.google-apps.folder"},
        fields="id").execute()
    _PRIVATE_ROOT_ID[0] = created["id"]
    return created["id"]


def _private_user_folder_id(service, username, create=True):
    root = _private_root_id(service)
    if not root or service is None:
        return None
    res = service.files().list(
        q=f"name = '{_drive_q(username)}' and '{root}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
        fields="files(id)", pageSize=5).execute()
    fl = res.get("files") or []
    if fl:
        return fl[0]["id"]
    if not create:
        return None
    created = service.files().create(
        body={"name": username, "mimeType": "application/vnd.google-apps.folder", "parents": [root]},
        fields="id").execute()
    return created["id"]


def _private_owner(service, meta):
    """返回文件所属的私人区账号名；不是私人区文件时返回 None。"""
    try:
        root = _private_root_id(service)
        for pid in (meta.get("parents") or []):
            parent = service.files().get(fileId=pid, fields="id,name,parents").execute()
            if root in (parent.get("parents") or []):
                return parent.get("name")
    except Exception:
        return None
    return None


def _private_account_ok(service, username):
    """账号存在且未被站长禁用时才能用私人区。"""
    if not username:
        return False
    try:
        accounts = _read_accounts(service) if service else []
    except Exception:
        return False
    for a in accounts:
        if a["name"] == username:
            return a.get("disabled") != "1"
    return False


def _private_is_admin():
    token = _env("ADMIN_TOKEN")
    return bool(token) and _has_gate(token, "admin_access")


FEEDBACK_FILE_NAME = "交换站意见.txt"
_FEEDBACK_CACHE = {"t": 0, "items": []}


def _feedback_file_id(service):
    if _env("FEEDBACK_DRIVE_FILE_ID"):
        return _env("FEEDBACK_DRIVE_FILE_ID")
    try:
        res = service.files().list(q=f"name = '{FEEDBACK_FILE_NAME}' and trashed = false", fields="files(id)", pageSize=5).execute()
        fl = res.get("files") or []
        return fl[0]["id"] if fl else None
    except Exception:
        return None


def _read_feedback(service):
    """Return list of {id, time, text, status} newest first. Status: open=未解决, done=已解决."""
    if service is None:
        return []
    try:
        fid = _feedback_file_id(service)
        if not fid:
            return []
        from googleapiclient.http import MediaIoBaseDownload
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=fid))
        done = False
        while not done:
            _, done = dl.next_chunk()
        items = []
        for line in buf.getvalue().decode("utf-8", errors="ignore").strip().split("\\n"):
            if not line.strip():
                continue
            parts = line.split("\\t", 3)
            if len(parts) >= 4:
                items.append({"id": parts[0], "time": parts[1], "status": parts[2], "text": parts[3], "author": parts[4] if len(parts) > 4 else ""})
        items.reverse()
        return items
    except Exception:
        return []


def _write_feedback(service, items_oldest_first):
    """Overwrite the Drive feedback file with items in oldest-first order."""
    from googleapiclient.http import MediaIoBaseUpload
    body = "\\n".join(f"{it['id']}\\t{it['time']}\\t{it['status']}\\t{it['text']}" for it in items_oldest_first)
    media = MediaIoBaseUpload(io.BytesIO(body.encode("utf-8")), mimetype="text/plain", resumable=False)
    try:
        fid = _feedback_file_id(service)
        if fid:
            service.files().update(fileId=fid, media_body=media).execute()
        else:
            meta = {"name": FEEDBACK_FILE_NAME, "mimeType": "text/plain"}
            service.files().create(body=meta, media_body=media, fields="id").execute()
        return True
    except Exception as e:
        print(f"[feedback] write failed: {e}", flush=True)
        return False


def _log(action, detail=""):
    """访问日志：只记时间、IP、动作、文件名，不记任何口令、密钥、完整 UA。
    内存里留近期 500 条供站长区查看，同时每 20 条或每次站长操作后缓冲写进
    云盘根目录的 交换站访问日志.txt，服务休眠/重启也不会丢长期记录。"""
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "?").split(",")[0].strip()
    ua = request.headers.get("User-Agent", "")
    device = "手机" if ("Mobile" in ua or "Android" in ua or "iPhone" in ua) else "电脑"
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    entry = {"time": now.strftime("%Y-%m-%d %H:%M"), "ip": ip, "loc": "", "device": device, "action": action, "detail": detail}
    ACCESS_LOG.appendleft(entry)
    line = f"{entry['time']} {ip} {entry['loc']} {device} {action} {detail}\n"
    print(f"[access] {line.strip()}", flush=True)
    _DRIVE_LOG_BUFFER.append(line)
    if len(_DRIVE_LOG_BUFFER) >= 20 or action in ("审核通过", "审核不通过", "删除已发布", "改名"):
        _flush_drive_log()


def _IP_LOC_CACHE():
    return getattr(_ip_loc, "_cache", None)

def _ip_loc(ip):
    """访问者 IP 属地：用 ip-api.com 查一次并缓存（仅站长日志页用，不影响公开页面速度）。
    查不到、超时或 API 失败时返回空，不编造省市。内网地址直接标内网。"""
    try:
        if not ip or ip == "?":
            return ""
        parts = ip.split(".")
        if len(parts) == 4:
            a, b = int(parts[0]), int(parts[1])
            if a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168) or a == 127:
                return "内网"
        cache = getattr(_ip_loc, "_cache", None)
        if cache is None:
            cache = {}
            _ip_loc._cache = cache
        if ip in cache:
            return cache[ip]
        loc = ""
        try:
            import requests as _rq
            r = _rq.get(f"http://ip-api.com/json/{ip}?fields=status,country,regionName,city", timeout=2)
            d = r.json()
            if d.get("status") == "success":
                city = d.get("city") or ""
                region = d.get("regionName") or ""
                country = d.get("country") or ""
                if country == "China":
                    loc = (region + city) if region else "中国"
                elif country:
                    loc = country
        except Exception:
            loc = ""
        cache[ip] = loc
        return loc
    except Exception:
        return ""




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


def _disposition(kind, name):
    """Content-Disposition: filename* 必须做百分号编码，否则中文文件名会让 gunicorn 报 Invalid HTTP Header，Render 对外就是 502。"""
    safe_ascii = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in name) or "file"
    return f"{kind}; filename=\"{safe_ascii}\"; filename*=UTF-8''{quote(name, safe='')}"


CATS = ["文档", "图片", "影音", "压缩包", "安装包", "其他"]


def _file_kind(name, mime):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "svg") or mime.startswith("image/"):
        return "图片"
    if ext in ("mp4", "mov", "mkv", "avi", "webm", "mp3", "wav", "m4a", "flac", "ogg") or mime.startswith(("video/", "audio/")):
        return "影音"
    if ext in ("pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md", "epub"):
        return "文档"
    if ext in ("zip", "rar", "7z", "tar", "gz"):
        return "压缩包"
    if ext in ("apk", "ipa", "exe", "msi", "dmg", "pkg", "deb", "rpm", "appimage"):
        return "安装包"
    return "其他"


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
        fields="files(id,name,size,mimeType,thumbnailLink)",
        pageSize=100,
    ).execute()
    out = []
    for f in res.get("files", []):
        out.append(
            {
                "id": f["id"],
                "name": f["name"],
                "size": _fmt_size(f.get("size")),
                "size_bytes": int(f.get("size") or 0),
                "ext": (f["name"].rsplit(".", 1)[-1].upper()[:4] if "." in f["name"] else "FILE"),
                "kind": _file_kind(f["name"], f.get("mimeType") or ""),
                "is_image": (f.get("mimeType") or "").startswith("image/"),
                "is_video": (f.get("mimeType") or "").startswith("video/"),
                "is_audio": (f.get("mimeType") or "").startswith("audio/"),
                "thumb_link": f.get("thumbnailLink") or "",
            }
        )
    return out


def _published_subfolders(service):
    if service is None:
        return []
    try:
        res = service.files().list(
            q=f"'{PUBLISHED_FOLDER_ID}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            fields="files(id,name)", pageSize=100).execute()
    except Exception:
        return []
    return [{"id": f["id"], "name": f["name"]} for f in (res.get("files") or [])]


def _published_tree_ids(service):
    ids = {PUBLISHED_FOLDER_ID}
    for fo in _published_subfolders(service):
        ids.add(fo["id"])
    return ids


def _is_published_parent(service, parents):
    parents = parents or []
    if PUBLISHED_FOLDER_ID in parents:
        return True
    if service is None:
        return False
    return bool(set(parents) & _published_tree_ids(service))


def _list_published_tree(service):
    if service is None:
        return [], []
    folders = _published_subfolders(service)
    files = []
    for f in list_folder(service, PUBLISHED_FOLDER_ID):
        f["folder_id"] = PUBLISHED_FOLDER_ID
        f["folder_name"] = ""
        files.append(f)
    for fo in folders:
        for f in list_folder(service, fo["id"]):
            f["folder_id"] = fo["id"]
            f["folder_name"] = fo["name"]
            files.append(f)
    for fo in folders:
        fo["count"] = sum(1 for f in files if f.get("folder_id") == fo["id"])
    return files, folders


def get_file(service, file_id):
    return service.files().get(
        fileId=file_id, fields="id,name,size,mimeType,parents,thumbnailLink"
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
  /* 固定浅色主题：手机和电脑显示一致，不跟随系统深色，避免对比度变差 */
  *{box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:"Noto Sans SC",system-ui,-apple-system,sans-serif;line-height:1.6}
  a{color:var(--green)}
  button,input{font:inherit}
  .wrap{max-width:900px;margin:0 auto;padding:28px 20px 56px}
  @media(min-width:861px){.wrap{padding:28px 24px 56px}h1{font-size:38px}.topnav a{padding:13px 8px;font-size:16px}}

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
  .filelist{margin-top:12px;width:100%}
  .logrow{padding:12px 0;border-top:1px solid var(--line);width:100%}
  .logrow:first-of-type{border-top:0}
  .logrow .fname{white-space:normal;overflow:visible;text-overflow:clip;word-break:break-all;overflow-wrap:anywhere}
  .logrow .fmeta{word-break:break-all;overflow-wrap:anywhere}
  .qrow{display:flex;gap:8px;align-items:center;padding:10px 0;border-top:1px solid var(--line);width:100%}
  .qnum{flex:0 0 auto;font-weight:900}
  .qname{flex:1 1 auto;min-width:0;word-break:break-all;overflow-wrap:anywhere;text-align:left}
  .qbtn{flex:0 0 auto;width:auto;min-height:36px;padding:8px 12px;border-radius:10px;border:1px solid var(--line);background:var(--surface);color:var(--ink);font-size:14px;cursor:pointer}
  .renameform{display:flex;gap:6px;margin-top:8px}.renametext{flex:1;min-width:0;padding:8px 10px;border-radius:10px;border:1px solid var(--line);background:var(--surface);color:var(--ink);font-size:14px}
  .topnav{position:sticky;top:0;z-index:20;background:var(--bg);padding:10px 0;display:flex;gap:8px;flex-wrap:wrap;border-bottom:1px solid var(--line);margin-bottom:16px}.topnav a{flex:1;text-align:center;text-decoration:none;border:1px solid var(--line);background:var(--surface);color:var(--ink);padding:11px 6px;border-radius:12px;font-weight:800;font-size:15px}.topnav a.active{background:var(--green);border-color:var(--green);color:#fff}.tabpanel{display:none}.tabpanel.active{display:block}
  .thumb{width:46px;height:46px;object-fit:cover;border-radius:12px;border:1px solid var(--line);flex-shrink:0;background:var(--surface-2)}
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
  @media(max-width:560px){.search{max-width:none}}
  .catgroup{margin-top:14px}.cathead{margin:14px 0 2px;font-size:16px;display:flex;gap:10px;align-items:center}.cathead .count{margin-left:auto}
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
 body{overflow-x:hidden}.file{min-width:0}.fname{word-break:break-all}
  /* Mobile-first polish: phone is the main way friends will use this */
  @media(max-width:640px){
    .wrap{padding:18px 12px 40px}
    h1{font-size:32px}
    .lead{font-size:15px}
    .badge-live{white-space:normal}
    .flow{gap:8px;margin-top:18px}
    .step{padding:14px 16px}
    .layout{gap:14px;margin-top:14px}
    .upload{padding:18px 16px}
    .section{padding:16px}
    .bottom{margin-top:14px}
    .bottom .card{padding:18px 16px}
    .search{max-width:none;width:100%}
    .toolbar{flex-direction:column;align-items:stretch}
    .textin{font-size:16px;padding:13px}
    .drop{padding:28px 14px}
    .file{display:grid;grid-template-columns:46px 1fr;gap:10px 12px;align-items:start}
    .file .fic,.file .thumb{grid-row:1}
    .file .fmain{min-width:0}
    .fname{white-space:normal;overflow:visible;text-overflow:clip;font-size:15.5px}
    .fmeta{word-break:break-all}
    .actions{grid-column:1 / -1;width:100%;padding-left:0;display:flex}
    .actions .btn,.actions form{flex:1}
    .actions .btn{width:100%;text-align:center;justify-content:center;min-height:44px;display:inline-flex;align-items:center}
    .actions form .btn{width:100%}
    form{display:flex;flex-wrap:wrap;gap:10px}
    form .textin{flex:1 1 100%}
    form .btn{flex:1 1 auto;min-height:44px}
    .upload form{flex-direction:column}
    .qrow{flex-direction:row !important;flex-wrap:nowrap !important}
    .qbtn{flex:0 0 auto !important;width:auto !important;min-height:36px !important}
    .upload form .btn{width:100%}
    .upload .drop{width:100%}
  }
 """

PAGE = (
    "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>交换站</title><style>"
    + BASE_CSS
    + "</style><style>" + '\n.shellhead{position:sticky;top:0;z-index:30;background:var(--bg);height:56px;display:flex;align-items:center;gap:12px;padding:6px 0;border-bottom:1px solid var(--line)}\n.jiabtn{width:44px;height:44px;border-radius:12px;background:var(--green);color:#fff;font-weight:900;font-size:20px;border:0;cursor:pointer;flex-shrink:0}\n.headtitle{flex:1;min-width:0;display:flex;align-items:baseline;gap:8px;white-space:nowrap;overflow:hidden}\n.headtitle h1{font-size:24px}\n#curtab{color:var(--muted);font-size:13px;font-weight:700}\n.drawer-backdrop{position:fixed;top:56px;bottom:0;left:0;right:0;background:rgba(0,0,0,.35);z-index:15}\n.drawer{position:fixed;top:56px;bottom:0;left:0;width:260px;max-width:84vw;background:var(--surface);border-right:1px solid var(--line);z-index:20;display:flex;flex-direction:column;padding:10px 14px 12px;box-sizing:border-box;overflow:hidden}\n.drawer-scroll{flex:1;min-height:0;overflow-y:auto}\n.drawer-label{font-size:11px;font-weight:800;color:var(--muted);letter-spacing:.06em;padding:6px 8px}\n.drawer-navbtn{display:block;width:100%;text-align:left;border:0;border-radius:10px;padding:9px 12px;font-weight:800;font-size:14px;cursor:pointer;margin-bottom:3px;background:transparent;color:var(--ink)}\n.drawer-navbtn.active{background:var(--green);color:#fff}\n.drawer-quota{flex-shrink:0;padding-top:10px;border-top:1px solid var(--line);margin-top:8px}\n.quota-bar{background:var(--surface-2);border-radius:999px;overflow:hidden;height:8px;margin-top:8px}\n.quota-fill{height:100%;background:var(--green)}\n.pub-toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}\n.folderbar{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}\n.album-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px}\n.tile{position:relative;aspect-ratio:1/1;border-radius:14px;overflow:hidden;cursor:pointer;background:var(--surface-2);border:1px solid var(--line)}\n.tileimg{width:100%;height:100%;object-fit:cover;display:block}\n.tilefic{width:100%;height:100%;display:grid;place-items:center;font-weight:900;color:var(--green-ink);font-size:20px}\n.tileinfo{position:absolute;left:0;right:0;bottom:0;padding:24px 8px 7px;background:linear-gradient(transparent,rgba(0,0,0,.65));color:#fff}\n.tile-name{font-size:12px;font-weight:800;word-break:break-all;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}\n.tile-meta{font-size:11px;opacity:.85}\n.tile-check{position:absolute;top:6px;right:6px;width:22px;height:22px;border-radius:50%;border:2px solid #fff;background:rgba(0,0,0,.35);display:none;place-items:center;color:#fff;font-size:14px;z-index:2}\n.album-grid.selecting .tile-check{display:grid}\n.tile.selected .tile-check{background:var(--green)}\n.tile.selected .tile-check::after{content:"\\2713"}\n#previewOverlay{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:40;display:none;align-items:center;justify-content:center;padding:16px;box-sizing:border-box}\n#previewCard{background:var(--surface);border-radius:18px;padding:16px;width:100%;max-width:480px;max-height:90vh;overflow:auto}\n' + "</style></head><body><div class=\"wrap\">"
    '<header class="shellhead"><button id="drawerBtn" class="jiabtn" type="button">交</button><div class="headtitle"><h1>交换站</h1><span id="curtab">/ 已发布区</span></div><div class="badge-live"><span class="dot"></span> 仅熟人使用 · 需审核</div></header><div id="drawerBackdrop" class="drawer-backdrop" style="display:none"></div><aside id="drawer" class="drawer" style="display:none"><div class="drawer-scroll"><div class="drawer-label">导航</div><nav id="drawernav"><button class="drawer-navbtn" data-tab="principle">原理区</button><button class="drawer-navbtn" data-tab="publish">发布区</button><button class="drawer-navbtn active" data-tab="published">已发布区</button><button class="drawer-navbtn" data-tab="feedback">意见区</button><button class="drawer-navbtn" data-tab="private">私人区</button><button class="drawer-navbtn" data-tab="admin">站长区</button></nav><div class="drawer-label">分类</div><div id="drawerCats">{% for c in cats %}{% if c.count %}<button class="drawer-navbtn" data-cat="{{ c.name }}">{{ c.name }}（{{ c.count }}）</button>{% endif %}{% endfor %}</div></div><div class="drawer-quota"><div style="font-weight:800;font-size:13px">私人空间</div>{% if fb_user %}<div class="quota-bar"><div class="quota-fill" style="width:{{ private_used_pct }}%"></div></div><div style="font-size:12px;color:var(--muted);margin-top:6px">{{ private_used_text }} / 20 GB · 还剩 {{ private_remaining_text }}</div>{% else %}<div style="font-size:12px;color:var(--muted);margin-top:6px">登录私人区账号后，这里显示你的用量（每人上限 20 GB）。</div>{% endif %}</div></aside>'
    "<div id=\"progwrap\" style=\"display:none;margin:0 0 14px;padding:12px 14px;border:1px solid var(--line);border-radius:14px;background:var(--surface)\"><div style=\"font-weight:800;margin-bottom:8px\">上传进度</div><div style=\"background:var(--surface-2);border-radius:999px;overflow:hidden;height:14px\"><div id=\"progbar\" style=\"height:100%;width:0%;background:var(--green);transition:width .3s\"></div></div><p class=\"note\" id=\"progtext\" style=\"margin-top:8px\">正在上传… 0%</p></div>"
    "<div class=\"tabpanel\" id=\"tab-principle\">"
    "<p class=\"lead\">熟人之间传文件的地方。<strong>上传不会立刻公开</strong> —— 先进入待审核，站长通过后，其他人才能在已发布里下载。</p>"
    "<div class=\"flow\"><div class=\"step\"><b><span class=\"num\">1</span> 上传文件</b><p>选文件提交就行，不用注册。</p></div>"
    "<div class=\"arrow\">→</div><div class=\"step mid\"><b><span class=\"num\">2</span> 待审核</b><p>文件先躺在这里，只有站长能看到和处理。</p></div>"
    "<div class=\"arrow\">→</div><div class=\"step\"><b><span class=\"num\">3</span> 已发布 · 下载</b><p>通过后出现在已发布列表，熟人随时下载。</p></div></div>"
    "<div class=\"card section\"><h2>这个站是怎么回事</h2>"
    "<p>上传 → 站长审核 → 已发布下载。</p>"
    "</div></div>"
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
    "<div class=\"tabpanel\" id=\"tab-publish\"><div class=\"card upload\"><h2>上传文件</h2>"
    "<p class=\"note\">最多 30 个 · 总共 2 GB · 先审核后公开</p>"
    "<form method=\"post\" action=\"/upload\" enctype=\"multipart/form-data\">"
    "<div class=\"drop\" id=\"drop\"><strong>点这里选文件，或拖进来</strong><span>最多 30 个 · 总共 2 GB</span>"
    "<br><input type=\"file\" id=\"fileinput\" name=\"file\" multiple required></div>"
    "<div class=\"filelist\" id=\"filelist\"></div>"
    "<button class=\"btn primary\" id=\"uploadbtn\" type=\"submit\" {% if not drive_configured %}disabled{% endif %}>上传</button>"
    "<p class=\"note\" id=\"uploadstatus\" style=\"margin-top:10px\"></p>"
    "</form>"
    "<script>var pf=document.querySelector('#tab-publish form');"
    "var __lastFileMeta=null;var __up={done:0,total:0,start:0,lastT:0,lastDone:0};function __fmtSp(bps){return bps>1048576?(bps/1048576).toFixed(1)+' MB/s':(bps/1024).toFixed(0)+' KB/s';}function __showProg(curFile,idx,totalFiles){var bar=document.getElementById('progbar');var tx=document.getElementById('progtext');if(!bar||!tx)return;var pct=__up.total?Math.round(__up.done/__up.total*100):0;bar.style.width=pct+'%';var el=(Date.now()-__up.start)/1000;var bps=el>0.5?__up.done/el:0;var eta=bps>0?'，还剩约'+Math.round((__up.total-__up.done)/bps)+'秒':'';var stalled=(Date.now()-__up.lastT>30000&&__up.done<__up.total)?'（进度 30 秒没动，网可能卡住了，别刷新，等它自己续传）':'';tx.textContent='上传中 '+idx+'/'+totalFiles+'：'+curFile+' '+pct+'%（'+(__up.done/1048576).toFixed(1)+' / '+(__up.total/1048576).toFixed(1)+' MB，'+__fmtSp(bps)+eta+'）'+stalled;}function putChunk(session,file,start,end,total,base){return new Promise(function(res,rej){var x=new XMLHttpRequest();x.open('PUT',session,true);x.setRequestHeader('Content-Range','bytes '+start+'-'+(end-1)+'/'+total);x.upload.onprogress=function(ev){if(ev.lengthComputable){__up.done=base+ev.loaded;__up.lastT=Date.now();__showProg(file.name,__up.idx,__up.n);}};x.onload=function(){if(x.status===308){var rg=x.getResponseHeader('Range');res(rg?parseInt(rg.split('-')[1])+1:start);}else if(x.status>=200&&x.status<300){try{__lastFileMeta=JSON.parse(x.responseText);}catch(e){}res(total);}else{rej(new Error('直传失败 '+x.status));}};x.onerror=function(){rej(new Error('直传网络失败'));};var __to=setTimeout(function(){try{x.abort();}catch(e){}rej(new Error('直传 45 秒无进度，已改中转'));},45000);var __oldOn=function(){clearTimeout(__to);};x.onload=function(){__oldOn();if(x.status===308){var rg=x.getResponseHeader('Range');res(rg?parseInt(rg.split('-')[1])+1:start);}else if(x.status>=200&&x.status<300){try{__lastFileMeta=JSON.parse(x.responseText);}catch(e){}res(total);}else{rej(new Error('直传失败 '+x.status));}};x.send(file.slice(start,end));});}"
    "async function directOne(file,idx,totalFiles){var tx0=document.getElementById('progtext');if(tx0){tx0.textContent='正在向云盘申请直传通道: '+file.name;}var ac=new AbortController();var to=setTimeout(function(){if(tx0){tx0.textContent='直传通道申请超过15秒没回音，可能是服务器刚睡醒，正在重试…';}},15000);var rs=await fetch('/direct_session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:file.name,size:file.size}),signal:ac.signal});clearTimeout(to);var sj=await rs.json();if(!sj.ok){throw new Error(sj.msg||'直传会话失败');}var session=sj.session;var CH=8*1024*1024;var off=0;var fileBase=__up.done;__up.idx=idx;__up.n=totalFiles;__up.lastT=Date.now();while(off<file.size){var nxt=Math.min(off+CH,file.size);off=await putChunk(session,file,off,nxt,file.size,fileBase+off);__up.done=fileBase+off;__up.lastT=Date.now();__showProg(file.name,idx,totalFiles);}await fetch('/direct_done',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:file.name,file_id:__lastFileMeta?__lastFileMeta.id:'',size:file.size})});}"
    "window.__uploading=false;window.addEventListener('beforeunload',function(e){if(window.__uploading){e.preventDefault();e.returnValue='正在上传，离开本页会中断上传';}});document.querySelectorAll('#topnav a').forEach(function(a){a.addEventListener('click',function(e){if(window.__uploading&&a.dataset.tab==='admin'){if(!confirm('正在上传，点站长区进入审核页会中断上传。确定要中断吗？')){e.preventDefault();e.stopImmediatePropagation();}}});});pf.addEventListener('submit',async function(e){e.preventDefault();window.__uploading=true;var us=document.getElementById('uploadstatus');if(us){us.textContent='已点上传，正在向云盘申请直传通道…（如果这行一直不动，说明页面是旧版，请刷新一次再传）';}var b=document.getElementById('uploadbtn');var w=document.getElementById('progwrap');var tx=document.getElementById('progtext');if(!queue.length){if(b){b.disabled=false;}window.__uploading=false;return;}if(b){b.disabled=true;}if(w){w.style.display='block';}__up.done=0;__up.total=queue.reduce(function(a,f){return a+f.size;},0);__up.start=Date.now();__up.lastT=Date.now();try { {% if is_relay %}throw new Error('__relay__');{% else %}for(var k=0;k<queue.length;k++){await directOne(queue[k],k+1,queue.length);}{% endif %}window.location.href='/?msg='+encodeURIComponent('已上传 '+queue.length+' 个文件到待审核，站长通过后才会出现在已发布列表')+'#publish';}catch(err){if(tx){tx.textContent=(err.message==='__relay__'?'正在经本站中转上传（手机→上海→海外→云盘），进度跟着国内这一段走…':'直传失败，正在改用本站中转上传: '+err.message);}try{var fd2=new FormData();queue.forEach(function(f){fd2.append('file',f);});var x2=new XMLHttpRequest();x2.open('POST','/upload',true);x2.setRequestHeader('X-Requested-With','XMLHttpRequest');var bar2=document.getElementById('progbar');x2.upload.onprogress=function(ev){if(ev.lengthComputable){var pct=Math.round(ev.loaded/ev.total*100);if(bar2){bar2.style.width=pct+'%';}if(tx){tx.textContent='中转上传: '+pct+'% ('+(ev.loaded/1048576).toFixed(1)+' / '+(ev.total/1048576).toFixed(1)+' MB)';}}};x2.onload=function(){try{var r=JSON.parse(x2.responseText);if(r.ok){window.location.href='/?msg='+encodeURIComponent(r.msg)+'#publish';}else{alert(r.msg);if(b){b.disabled=false;}}}catch(e2){window.location.reload();}};x2.onerror=function(){if(tx){tx.textContent='上传失败，请检查网络后重试';}if(b){b.disabled=false;}window.__uploading=false;};x2.send(fd2);}catch(e3){if(tx){tx.textContent='上传失败：'+err.message+'，请检查网络后重试';}if(b){b.disabled=false;}}}});</script>"
    ""
    "<script>var d=document.getElementById('drop'),i=document.getElementById('fileinput');"
    "d.addEventListener('click',function(e){if(e.target!==i){i.click();}});"
    "i.addEventListener('click',function(e){e.stopPropagation();});"
    "d.addEventListener('dragover',function(e){e.preventDefault();d.classList.add('over');});"
    "d.addEventListener('dragleave',function(){d.classList.remove('over');});"
    "d.addEventListener('drop',function(e){e.preventDefault();d.classList.remove('over');addQ(e.dataTransfer.files);});"
    "var queue=[];function fmtSize(z){return z<1048576?(z/1024).toFixed(1)+' KB':(z/1048576).toFixed(1)+' MB';}"
    "function syncInput(){var dt=new DataTransfer();queue.forEach(function(f){dt.items.add(f);});i.files=dt.files;}"
    "function show(){var l=document.getElementById('filelist');var tot=queue.reduce(function(a,f){return a+f.size;},0);var mb=(tot/1048576).toFixed(1);var over=queue.length>30||tot>{{ max_mb }}*1048576;"
    "l.innerHTML=queue.map(function(f,idx){return '<div class=\"qrow\"><b class=\"qnum\">'+(idx+1)+'</b><span class=\"qname\">'+f.name+'（'+fmtSize(f.size)+'）</span><button type=\"button\" class=\"qbtn\" onclick=\"moveQ('+idx+',-1)\">↑</button><button type=\"button\" class=\"qbtn\" onclick=\"moveQ('+idx+',1)\">↓</button><button type=\"button\" class=\"qbtn\" onclick=\"delQ('+idx+')\">×</button></div>';}).join('')+(queue.length?'<div style=\"padding:8px 0;font-weight:700\">'+(dropped?'（已忽略多余的 '+dropped+' 个，一次最多 30 个）':'')+'共 '+queue.length+' 个文件，总计 '+mb+' MB'+(queue.length>30?'，一次最多 30 个，请删到 30 个以内':'')+(tot>{{ max_mb }}*1048576?'，已超过 {{ max_mb }} MB 上限，请减少文件或分批上传':'')+'</div>':'');"
    "var b=document.getElementById('uploadbtn');if(b){b.disabled=over||!queue.length;}}"
    "function moveQ(idx,d){var j=idx+d;if(j<0||j>=queue.length){return;}var t=queue[idx];queue[idx]=queue[j];queue[j]=t;syncInput();show();}"
    "function delQ(idx){queue.splice(idx,1);syncInput();show();}"
    "var dropped=0;function addQ(fs){var before=queue.length;Array.from(fs).forEach(function(f){if(queue.length<30){queue.push(f);}else{dropped++;}});syncInput();show();}"
    "i.addEventListener('change',function(){addQ(i.files);});</script>"
    "{% if msg %}<p class=\"note\">{{ msg }}</p>{% endif %}</div></div>"
    '<div class="tabpanel active" id="tab-published"><div class="pub-toolbar"><input class="search" id="search" type="search" placeholder="搜索文件名…" oninput="onSearch(this.value)"><button class="btn" id="openDrawerBtn" type="button">导航</button><button class="btn" id="selBtn" type="button">选择</button></div><section class="card section"><div class="section-head"><h2>已发布</h2><span class="count">{{ files|length }} 个</span></div><div class="folderbar" id="folderbar"><button class="tab active" data-folder="all" onclick="showFolder(\'all\',this)">全部（{{ files|length }}）</button>{% for fo in folders %}<button class="tab" data-folder="{{ fo.id }}" onclick="showFolder(\'{{ fo.id }}\',this)">{{ fo.name }}（{{ fo.count }}）</button>{% endfor %}<button class="tab" id="newFolderBtn" type="button">+ 新建文件夹</button></div><form id="newFolderForm" method="post" action="/folder/create" style="display:none;gap:8px;margin-top:10px"><input class="textin" name="name" placeholder="新文件夹名字，比如：旅行" required maxlength="50" style="flex:1"><button class="btn primary" type="submit">创建</button><button class="btn" type="button" id="newFolderCancel">取消</button></form><div class="tabs" id="cattabs"><button class="tab active" onclick="showCat(\'全部\',this)">全部（{{ files|length }}）</button>{% for c in cats %}{% if c.count %}<button class="tab" onclick="showCat(\'{{ c.name }}\',this)">{{ c.name }}（{{ c.count }}）</button>{% endif %}{% endfor %}</div><div class="album-grid" id="albumGrid">{% for f in files %}<div class="tile" data-cat="{{ f.kind }}" data-folder="{{ f.folder_id }}" data-name="{{ f.name|lower }}" data-id="{{ f.id }}" data-dl="/download/{{ f.id }}" data-view="/view/{{ f.id }}" data-isimg="{{ \'1\' if f.is_image else \'0\' }}" data-isvid="{{ \'1\' if f.is_video else \'0\' }}" data-isaud="{{ \'1\' if f.is_audio else \'0\' }}"><span class="tile-check"></span>{% if f.is_image or f.is_video %}<img class="tileimg" src="/thumb/{{ f.id }}" alt="" loading="lazy" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'grid\';"><div class="tilefic" style="display:none">{{ f.ext }}</div>{% else %}<div class="tilefic">{{ f.ext }}</div>{% endif %}<div class="tileinfo"><div class="tile-name">{{ f.name }}</div><div class="tile-meta">{{ f.kind }} · {{ f.size }}{% if f.folder_name %} · {{ f.folder_name }}{% endif %}</div></div></div>{% endfor %}</div>{% if not files %}<p class="empty">目前还没有已发布的文件。</p>{% endif %}<form id="moveForm" method="post" action="/folder/move" style="display:none;margin-top:12px;gap:8px;flex-wrap:wrap;align-items:center"><span id="selCount" style="font-weight:800"></span><span>移到</span><select name="dest_folder_id" class="textin" style="width:auto"><option value="{{ published_id }}">已发布根目录</option>{% for fo in folders %}<option value="{{ fo.id }}">{{ fo.name }}</option>{% endfor %}</select><input type="hidden" name="file_ids" id="moveFileIds"><button class="btn primary" type="submit">移动</button><button class="btn" type="button" id="dlSelBtn">选择下载</button></form><p class="note">点图片默认就是预览；点右上角“选择”，再点图片打勾，就可以批量下载或移动到文件夹。</p></section><div id="previewOverlay"><div id="previewCard"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><b id="previewTitle" style="word-break:break-all"></b><button class="btn" id="previewClose" type="button">关闭</button></div><div id="previewBody" style="margin-top:12px"></div></div></div><script>var curCat=\'全部\',curFolder=\'all\',curQ=\'\',selectMode=false,selected=new Set();function applyFilter(){document.querySelectorAll(\'#albumGrid .tile\').forEach(function(t){var okCat=curCat===\'全部\'||t.dataset.cat===curCat;var okFolder=curFolder===\'all\'||t.dataset.folder===curFolder;var okQ=t.dataset.name.includes(curQ);t.style.display=(okCat&&okFolder&&okQ)?\'\':\'none\';});}function showCat(c,el){curCat=c;document.querySelectorAll(\'#cattabs .tab\').forEach(function(t){t.classList.remove(\'active\');});el.classList.add(\'active\');applyFilter();}function showFolder(fid,el){curFolder=fid;document.querySelectorAll(\'#folderbar .tab\').forEach(function(t){t.classList.remove(\'active\');});el.classList.add(\'active\');applyFilter();}function onSearch(v){curQ=v.toLowerCase();applyFilter();}function updateSel(){var mf=document.getElementById(\'moveForm\');var sc=document.getElementById(\'selCount\');var ids=document.getElementById(\'moveFileIds\');if(sc)sc.textContent=\'已选 \'+selected.size+\' 个\';if(ids)ids.value=Array.from(selected).join(\',\');if(mf)mf.style.display=selected.size?\'flex\':\'none\';}function openPreview(t){var ov=document.getElementById(\'previewOverlay\');var ti=document.getElementById(\'previewTitle\');var bd=document.getElementById(\'previewBody\');if(!ov||!ti||!bd)return;ti.textContent=t.querySelector(\'.tile-name\').textContent;var view=t.dataset.view,dl=t.dataset.dl;if(t.dataset.isimg===\'1\'){bd.innerHTML=\'<img src="\'+view+\'" style="width:100%;border-radius:12px">\';}else if(t.dataset.isvid===\'1\'){bd.innerHTML=\'<video controls src="\'+view+\'" style="width:100%;border-radius:12px"></video>\';}else if(t.dataset.isaud===\'1\'){bd.innerHTML=\'<audio controls src="\'+view+\'" style="width:100%"></audio>\';}else if(t.dataset.cat===\'文档\'){bd.innerHTML=\'<iframe src="\'+view+\'" style="width:100%;height:420px;border:0;border-radius:12px"></iframe>\';}else{bd.innerHTML=\'<p class="note">这个类型不能在线预览，请下载后打开。</p><a class="btn primary" href="\'+dl+\'">下载</a>\';}ov.style.display=\'flex\';}function closePreview(){var ov=document.getElementById(\'previewOverlay\');if(ov)ov.style.display=\'none\';}function dlTile(t){var url=t.dataset.dl;var name=t.querySelector(\'.tile-name\').textContent;var x=new XMLHttpRequest();x.open(\'GET\',url,true);x.responseType=\'blob\';x.onload=function(){if(x.status===200){var a=document.createElement(\'a\');a.href=URL.createObjectURL(x.response);a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(function(){URL.revokeObjectURL(a.href);},1000);}else{alert(\'下载失败，请重试\');}};x.onerror=function(){alert(\'下载失败，请检查网络后重试\');};x.send();}document.getElementById(\'albumGrid\').addEventListener(\'click\',function(e){var t=e.target.closest(\'.tile\');if(!t)return;if(selectMode){var id=t.dataset.id;if(selected.has(id)){selected.delete(id);t.classList.remove(\'selected\');}else{selected.add(id);t.classList.add(\'selected\');}updateSel();}else{openPreview(t);}});document.getElementById(\'previewClose\').addEventListener(\'click\',closePreview);document.getElementById(\'previewOverlay\').addEventListener(\'click\',function(e){if(e.target.id===\'previewOverlay\')closePreview();});document.getElementById(\'selBtn\').addEventListener(\'click\',function(){selectMode=!selectMode;var g=document.getElementById(\'albumGrid\');if(selectMode){g.classList.add(\'selecting\');this.textContent=\'取消\';}else{selected.clear();document.querySelectorAll(\'#albumGrid .tile\').forEach(function(t){t.classList.remove(\'selected\');});g.classList.remove(\'selecting\');this.textContent=\'选择\';}updateSel();});document.getElementById(\'dlSelBtn\').addEventListener(\'click\',function(){var tiles=[];document.querySelectorAll(\'#albumGrid .tile\').forEach(function(t){if(selected.has(t.dataset.id))tiles.push(t);});if(!tiles.length)return;var i=0;function next(){if(i>=tiles.length){alert(\'已开始下载 \'+tiles.length+\' 个文件（浏览器可能逐个询问是否允许多个下载）。\');selected.clear();document.querySelectorAll(\'#albumGrid .tile\').forEach(function(t){t.classList.remove(\'selected\');});document.getElementById(\'albumGrid\').classList.remove(\'selecting\');selectMode=false;document.getElementById(\'selBtn\').textContent=\'选择\';updateSel();return;}dlTile(tiles[i]);i++;setTimeout(next,600);}next();});document.getElementById(\'newFolderBtn\').addEventListener(\'click\',function(){var f=document.getElementById(\'newFolderForm\');f.style.display=\'flex\';});document.getElementById(\'newFolderCancel\').addEventListener(\'click\',function(){document.getElementById(\'newFolderForm\').style.display=\'none\';});document.getElementById(\'moveForm\').addEventListener(\'submit\',function(e){if(!selected.size){e.preventDefault();alert(\'请先勾选文件\');}});</script></div>'
    "{% endif %}"
    "<script>function dlFile(btn){var row=btn.closest('.file');var wrap=row.querySelector('.dlprog');var bar=row.querySelector('.dlbar');var tx=row.querySelector('.dltext');if(!wrap)return;wrap.style.display='block';btn.disabled=true;var url=btn.getAttribute('data-dl');var name=btn.getAttribute('data-name');var x=new XMLHttpRequest();x.open('GET',url,true);x.responseType='blob';x.onprogress=function(ev){if(ev.lengthComputable){var pct=Math.round(ev.loaded/ev.total*100);bar.style.width=pct+'%';tx.textContent='下载中… '+pct+'%（'+(ev.loaded/1048576).toFixed(1)+' / '+(ev.total/1048576).toFixed(1)+' MB）';}else{tx.textContent='下载中… '+(ev.loaded/1048576).toFixed(1)+' MB';}};x.onload=function(){if(x.status===200){bar.style.width='100%';tx.textContent='下载完成';var a=document.createElement('a');a.href=URL.createObjectURL(x.response);a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(function(){URL.revokeObjectURL(a.href);},1000);}else{tx.textContent='下载失败，请重试';}btn.disabled=false;};x.onerror=function(){tx.textContent='下载失败，请检查网络后重试';btn.disabled=false;};x.send();}</script>"
        '<div class="tabpanel" id="tab-private"><div class="card section"><h2>私人区</h2>'
    '{% if fb_user %}<p class="note">当前账号：{{ fb_user }} · <a href="/logout_feedback">退出</a></p>'
    '<p class="note">私人空间：每人上限 20 GB，你目前已用 {{ private_used_text }}。</p>'
    '<form method="post" action="/private/upload" enctype="multipart/form-data">'
    '<div class="drop"><strong>点这里选文件上传到私人区</strong><span>一次最多 30 个 · 总共 2 GB · 不用站长审核，只有你和站长能看见</span><br><input type="file" name="file" multiple required></div>'
    '<button class="btn primary" style="margin-top:10px">上传到私人区</button></form>'
    '{% if private_files %}{% for f in private_files %}<div class="file"><div class="fic">{{ f.ext }}</div><div class="fmain"><div class="fname">{{ f.name }}</div><div class="fmeta"><span>{{ f.kind }}</span><span>·</span><span>{{ f.size }}</span></div></div>'
    '<div class="actions"><a class="btn" href="/view/{{ f.id }}" target="_blank">打开预览</a><a class="btn primary" href="/download/{{ f.id }}">下载</a>'
    '<form method="post" action="/private/delete" style="display:inline" onsubmit="return confirm(&quot;确定删除这个私人文件吗？&quot;)"><input type="hidden" name="file_id" value="{{ f.id }}"><button class="btn danger">删除</button></form></div></div>'
    '{% endfor %}{% else %}<p class="empty">你的私人区目前还没有文件。</p>{% endif %}'
    '{% else %}<div class="card section"><h3>私人区要先注册 / 登录</h3><p class="note">用意见区同一个账号就行：先注册一个账号名和密码，登录后这个区就只显示你自己的文件。账号密码存在云端，站长能看见和管理，请不要用重要密码注册。</p>'
    '<form method="post" action="/register"><input class="textin" name="name" placeholder="账号名" required> <input class="textin" type="password" name="password" placeholder="密码" required> <button class="btn primary">注册</button></form>'
    '<form method="post" action="/login_feedback" style="margin-top:8px"><input class="textin" name="name" placeholder="账号名" required> <input class="textin" type="password" name="password" placeholder="密码" required> <button class="btn">登录</button></form></div>{% endif %}'
    '</div></div>'
"<div class=\"tabpanel\" id=\"tab-feedback\"><div class=\"card section\"><h2>意见区</h2>"
    "<p class=\"note\">大家都能看见，站长处理后改成已解决。</p>"
    "{% if fb_user %}<p class=\"note\">当前账号：{{ fb_user }} · <a href=\"/logout_feedback\">退出</a></p><form method=\"post\" action=\"/feedback\"><textarea class=\"textin\" name=\"text\" rows=\"3\" placeholder=\"写下你的意见…\" required style=\"width:100%\"></textarea><br><button class=\"btn primary\" style=\"margin-top:8px\">提交意见</button></form>"
    "{% else %}<div class=\"card section\"><h3>提意见要先注册 / 登录</h3><p class=\"note\">意见大家都能看见，但提意见要先有账号，这样才知道是谁提的。注册的账号密码存在云端，站长能看见和管理，请不要用重要密码注册。</p>"
    "<form method=\"post\" action=\"/register\"><input class=\"textin\" name=\"name\" placeholder=\"账号名\" required> <input class=\"textin\" type=\"password\" name=\"password\" placeholder=\"密码\" required> <button class=\"btn primary\">注册</button></form>"
    "<form method=\"post\" action=\"/login_feedback\" style=\"margin-top:8px\"><input class=\"textin\" name=\"name\" placeholder=\"账号名\" required> <input class=\"textin\" type=\"password\" name=\"password\" placeholder=\"密码\" required> <button class=\"btn\">登录</button></form></div>{% endif %}"
    "{% if feedback %}{% for fb in feedback %}<div class=\"logrow\"><div class=\"fname\">{{ fb.text }}</div><div class=\"fmeta\"><span>{{ fb.author }}</span><span>·</span><span>{{ fb.time }}</span><span>·</span>{% if fb.status == 'done' %}<span class=\"chip\">已解决</span>{% else %}<span class=\"chip wait\">未解决</span>{% endif %}</div></div>{% endfor %}{% else %}<p class=\"empty\">目前还没有意见。</p>{% endif %}</div></div>"
    "<div class=\"tabpanel\" id=\"tab-admin\"><div class=\"card section\"><h2>站长审核</h2>"
    "<p class=\"note\">站长入口与熟人入口分开，站长口令只在审核页使用。</p>"
    "<form method=\"post\" action=\"/admin/login\">"
    "<input class=\"textin\" type=\"password\" name=\"token\" placeholder=\"站长口令\" required> "
    "<button class=\"btn primary\" type=\"submit\">进入审核</button></form></div>"
    "</div><script>var TAB_NAMES={'principle':'原理区','publish':'发布区','published':'已发布区','feedback':'意见区','private':'私人区','admin':'站长区'};function openDrawer(){document.getElementById('drawer').style.display='flex';document.getElementById('drawerBackdrop').style.display='block';}function closeDrawer(){document.getElementById('drawer').style.display='none';document.getElementById('drawerBackdrop').style.display='none';}function showTab(name){if(window.__uploading&&name==='admin'&&!confirm('正在上传，点站长区进入审核页会中断上传。确定要中断吗？')){return;}document.querySelectorAll('.tabpanel').forEach(function(el){el.classList.toggle('active',el.id==='tab-'+name);});document.querySelectorAll('#drawernav .drawer-navbtn').forEach(function(b){b.classList.toggle('active',b.dataset.tab===name);});var ct=document.getElementById('curtab');if(ct)ct.textContent='/ '+(TAB_NAMES[name]||name);closeDrawer();}document.getElementById('drawerBtn').addEventListener('click',function(){var d=document.getElementById('drawer');if(d.style.display==='flex'){closeDrawer();}else{openDrawer();}});document.getElementById('drawerBackdrop').addEventListener('click',closeDrawer);document.querySelectorAll('#drawernav .drawer-navbtn').forEach(function(b){b.addEventListener('click',function(e){e.preventDefault();location.hash=b.dataset.tab;showTab(b.dataset.tab);});});document.querySelectorAll('#drawerCats .drawer-navbtn').forEach(function(b){b.addEventListener('click',function(){location.hash='published';showTab('published');curCat=b.dataset.cat;document.querySelectorAll('#cattabs .tab').forEach(function(t){t.classList.toggle('active',t.textContent.indexOf(b.dataset.cat)===0);});applyFilter();});});var _odb=document.getElementById('openDrawerBtn');if(_odb)_odb.addEventListener('click',openDrawer);var h=(location.hash||'#published').replace('#','');if(['principle','publish','published','feedback','private','admin'].indexOf(h)<0){h='published';}showTab(h);</script>"
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
    "{% elif files %}<div class=\"card section\">{% for f in files %}<div class=\"file\">{% if f.is_image or f.is_video %}<img class=\"thumb\" src=\"/thumb/{{ f.id }}\" loading=\"lazy\" onerror=\"this.style.display='none'\">{% else %}<div class=\"fic\">{{ f.ext }}</div>{% endif %}"
    "<div class=\"fmain\"><div class=\"fname\">{{ f.name }}</div><div class=\"fmeta\"><span>{{ f.size }}</span><span class=\"chip wait\">待审核</span></div></div>"
    "<div class=\"actions\"><a class=\"btn\" href=\"/view/{{ f.id }}\" target=\"_blank\">打开预览</a>"
    "<form class=\"renameform\" method=\"post\" action=\"/rename\" style=\"display:inline-flex\"><input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\">"
    "<input type=\"hidden\" name=\"back\" value=\"/admin\"><input class=\"renametext\" type=\"text\" name=\"new_name\" placeholder=\"改个名字…\" required>"
    "<button class=\"btn\">改名</button></form><form method=\"post\" action=\"/admin/approve\" style=\"display:inline\">"
    "<input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\"><button class=\"btn primary\">通过</button></form> "
    "<form method=\"post\" action=\"/admin/reject\" style=\"display:inline\">"
    "<input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\">"
    "<button class=\"btn danger\">不通过（移到回收站）</button></form></div></div>{% endfor %}</div>"
    "{% else %}<p class=\"empty\">目前没有待审核的文件。</p>{% endif %}"
    "<p><a href=\"/admin/accounts\">账号管理</a> · <a href=\"/admin/private\">私人区管理</a> · <a href=\"/admin/feedback\">意见管理</a> · <a href=\"/admin/logs\">访问日志</a> · <a href=\"/admin/published\">已发布管理（删除已发布文件）</a> · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div></body></html>"
)
ADMIN_PAGE = (
    "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>审核 - 交换站</title><style>"
    + BASE_CSS
    + "</style></head><body><div class=\"wrap\"><h1>待审核文件</h1>"
    "{% if not drive_configured %}"
    "<div class=\"card section warn\"><strong>云盘暂未连接</strong><br>这里没有列出任何真实云盘文件。</div>"
    "{% elif files %}<div class=\"card section\">{% for f in files %}<div class=\"file\">{% if f.is_image or f.is_video %}<img class=\"thumb\" src=\"/thumb/{{ f.id }}\" loading=\"lazy\" onerror=\"this.style.display='none'\">{% else %}<div class=\"fic\">{{ f.ext }}</div>{% endif %}"
    "<div class=\"fmain\"><div class=\"fname\">{{ f.name }}</div><div class=\"fmeta\"><span>{{ f.size }}</span><span class=\"chip wait\">待审核</span></div></div>"
    "<div class=\"actions\"><a class=\"btn\" href=\"/view/{{ f.id }}\" target=\"_blank\">打开预览</a>"
    "<form class=\"renameform\" method=\"post\" action=\"/rename\" style=\"display:inline-flex\"><input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\">"
    "<input type=\"hidden\" name=\"back\" value=\"/admin\"><input class=\"renametext\" type=\"text\" name=\"new_name\" placeholder=\"改个名字…\" required>"
    "<button class=\"btn\">改名</button></form><form method=\"post\" action=\"/admin/approve\" style=\"display:inline\">"
    "<input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\"><button class=\"btn primary\">通过</button></form> "
    "<form method=\"post\" action=\"/admin/reject\" style=\"display:inline\">"
    "<input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\">"
    "<button class=\"btn danger\">不通过（移到回收站）</button></form></div></div>{% endfor %}</div>"
    "{% else %}<p class=\"empty\">目前没有待审核的文件。</p>{% endif %}"
    "<p><a href=\"/admin/accounts\">账号管理</a> · <a href=\"/admin/private\">私人区管理</a> · <a href=\"/admin/feedback\">意见管理</a> · <a href=\"/admin/logs\">访问日志</a> · <a href=\"/admin/published\">已发布管理（删除已发布文件）</a> · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div></body></html>"
)

ADMIN_PUBLISHED_PAGE = (
    "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>已发布管理 - 交换站</title><style>"
    + BASE_CSS
    + "</style></head><body><div class=\"wrap\"><h1>已发布管理</h1>"
    "<p class=\"note\">这里可以把已发布的文件移到回收站，熟人就再也下载不到了。回收站里的文件不在列表里，需要去云盘回收站看。</p>"
    "{% if files %}<div class=\"card section\">{% for f in files %}<div class=\"file\">{% if f.is_image or f.is_video %}<img class=\"thumb\" src=\"/thumb/{{ f.id }}\" loading=\"lazy\" onerror=\"this.style.display='none'\">{% else %}<div class=\"fic\">{{ f.ext }}</div>{% endif %}"
    "<div class=\"fmain\"><div class=\"fname\">{{ f.name }}</div><div class=\"fmeta\"><span>{{ f.kind }}</span><span>·</span><span>{{ f.size }}</span></div></div>"
    "<div class=\"actions\"><a class=\"btn\" href=\"/view/{{ f.id }}\" target=\"_blank\">打开预览</a>"
    "<form class=\"renameform\" method=\"post\" action=\"/rename\" style=\"display:inline-flex\"><input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\">"
    "<input type=\"hidden\" name=\"back\" value=\"/admin/published\"><input class=\"renametext\" type=\"text\" name=\"new_name\" placeholder=\"改个名字…\" required>"
    "<button class=\"btn\">改名</button></form>"
    "<form method=\"post\" action=\"/admin/delete\" style=\"display:inline\" onsubmit=\"return confirm('确定把这个文件移到回收站吗？');\">"
    "<input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\">"
    "<button class=\"btn danger\">删除（移到回收站）</button></form></div></div>{% endfor %}</div>"
    "{% else %}<p class=\"empty\">目前没有已发布的文件。</p>{% endif %}"
    "<p><a href=\"/admin\">回待审核</a> · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div></body></html>"
)


@app.route("/api/private")
def api_private():
    code = _env("EXCHANGE_CODE")
    out = {"files": []}
    if code and _has_gate(code, "exchange_access"):
        fb_user = _account_gate()
        service = drive_service()
        if fb_user and service and _private_account_ok(service, fb_user):
            folder = _private_user_folder_id(service, fb_user, create=False)
            if folder:
                out["files"] = list_folder(service, folder)
    return jsonify(out)


@app.route("/api/private_all")
def api_private_all():
    token = _env("ADMIN_TOKEN")
    out = {"users": []}
    if token and _has_gate(token, "admin_access"):
        service = drive_service()
        root = _private_root_id(service) if service else None
        if service and root:
            try:
                res = service.files().list(
                    q=f"'{root}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                    fields="files(id,name)", pageSize=100).execute()
                for folder in res.get("files") or []:
                    out["users"].append({"name": folder["name"], "files": list_folder(service, folder["id"])})
            except Exception:
                out["users"] = []
    return jsonify(out)


@app.route("/api/files")
def api_files():
    code = _env("EXCHANGE_CODE")
    out = {"published": [], "pending": [], "folders": []}
    if code and _has_gate(code, "exchange_access"):
        try:
            _pf, _fo = _list_published_tree(drive_service())
            out["published"] = _pf
            out["folders"] = _fo
        except Exception:
            out["published"] = []
            out["folders"] = []
    token = _env("ADMIN_TOKEN")
    if token and _has_gate(token, "admin_access"):
        try:
            out["pending"] = list_folder(drive_service(), PENDING_FOLDER_ID)
        except Exception:
            out["pending"] = []
    return jsonify(out)


@app.route("/")
def index():
    code = _env("EXCHANGE_CODE")
    entered = _has_gate(code, "exchange_access")
    files = []
    folders = []
    if code and entered:
        if _is_relay():
            try:
                import requests as _rq
                _cookies = {}
                if "exchange_access" in request.cookies:
                    _cookies["exchange_access"] = request.cookies["exchange_access"]
                _r = _rq.get(_relay_backend() + "/api/files", cookies=_cookies, timeout=15)
                _rj = _r.json()
                files = _rj.get("published", [])
                folders = _rj.get("folders", [])
            except Exception:
                files = []
                folders = []
        else:
            files, folders = _cached_published(drive_service())
    cats = [{"name": c, "count": sum(1 for f in files if f["kind"] == c)} for c in CATS]
    private_files = []
    private_used = 0
    fb_user = _account_gate()
    if entered and fb_user:
        if _is_relay():
            try:
                import requests as _rq
                _cookies = {}
                for _k in ("exchange_access", "feedback_user"):
                    if _k in request.cookies:
                        _cookies[_k] = request.cookies[_k]
                _r = _rq.get(_relay_backend() + "/api/private", cookies=_cookies, timeout=15)
                private_files = _r.json().get("files", [])
            except Exception:
                private_files = []
        else:
            service = drive_service()
            if service and _private_account_ok(service, fb_user):
                folder = _private_user_folder_id(service, fb_user, create=False)
                if folder:
                    private_files = list_folder(service, folder)
    private_used = sum(f.get("size_bytes", 0) for f in private_files)
    return render_template_string(
        PAGE,
        code_configured=bool(code),
        entered=entered,
        drive_configured=True if _is_relay() else _drive_configured(),
        cats=cats,
        files=files,
        folders=folders,
        published_id=PUBLISHED_FOLDER_ID,
        private_used_pct=int(min(100, private_used / (20 * 1024 * 1024 * 1024) * 100)) if private_used else 0,
        private_remaining_text=_fmt_size(max(0, 20 * 1024 * 1024 * 1024 - private_used)),
        feedback=_read_feedback(drive_service()) if entered else [],
        fb_user=fb_user,
        private_files=private_files,
        private_used=private_used,
        private_used_text=_fmt_size(private_used),
        max_mb=MAX_UPLOAD_MB,
        is_relay=_is_relay(),
        msg=request.args.get("msg", ""),
    )


@app.route("/enter", methods=["POST"])
def enter():
    code = _env("EXCHANGE_CODE")
    if not code:
        return redirect("/?msg=本站还没配置访问口令，请联系站长")
    if request.form.get("code", "") != code:
        _log("熟人进站失败", "口令不对")
        return redirect("/?msg=访问口令不对，请再试一次")
    resp = redirect("/")
    resp.set_cookie(
        "exchange_access", _gate_value(code), httponly=True, samesite="Lax"
    )
    _log("熟人进站", "")
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
    if _is_relay():
        try:
            import requests as _rq
            _cookies = {}
            for _k in ("exchange_access", "admin_access"):
                if _k in request.cookies:
                    _cookies[_k] = request.cookies[_k]
            _files = [("file", (u.filename, u.stream, u.mimetype)) for u in request.files.getlist("file") if u and u.filename]
            _r = _rq.post(_relay_backend() + "/upload", cookies=_cookies, files=_files, headers={"X-Requested-With": "XMLHttpRequest"}, timeout=300)
            try:
                _j = _r.json()
                return jsonify(_j), _r.status_code
            except Exception:
                return _r.text, _r.status_code
        except Exception as e:
            return jsonify({"ok": False, "msg": f"中转上传失败：{e}"}), 502
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
            chunksize=1024 * 1024,
        )
        service.files().create(
            body={"name": name, "parents": [PENDING_FOLDER_ID]},
            media_body=media,
            fields="id",
        ).execute()
        count += 1
    if count == 0:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "msg": "没有可上传的文件"})
        return redirect("/?msg=没有可上传的文件")
    _log("上传", f"{count} 个文件")
    msg = f"已上传 {count} 个文件到待审核，站长通过后才会出现在已发布列表"
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "msg": msg})
    return redirect(f"/?msg={msg}")


@app.route("/direct_session", methods=["POST"])
def direct_session():
    """直传云盘第一步（服务器端，不暴露任何密钥给浏览器）：
    熟人进站后，服务器用自己的 OAuth 凭证向谷歌申请一个一次性直传会话，
    只对应这一个文件、只进待审核文件夹；浏览器拿到的是这个会话地址，
    不是账号密钥，文件直传云盘不再经过 Render 中转，速度只看上传者网速。
    会话地址本身有写权限，所以绝不记进日志、不写进页面源码，只当次返回。"""
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access"):
        return jsonify({"ok": False, "msg": "请先输入访问口令"}), 403
    if _is_relay():
        try:
            import requests as _rq
            _cookies = {}
            if "exchange_access" in request.cookies:
                _cookies["exchange_access"] = request.cookies["exchange_access"]
            _r = _rq.post(_relay_backend() + "/direct_session", cookies=_cookies, json=request.get_json(silent=True) or {}, timeout=30)
            return jsonify(_r.json()), _r.status_code
        except Exception as e:
            return jsonify({"ok": False, "msg": f"中转直传会话失败：{e}"}), 502
    data = request.get_json(silent=True) or {}
    name = _safe_filename(data.get("name", ""))
    try:
        size = int(data.get("size") or 0)
    except Exception:
        size = 0
    if not name or size <= 0 or size > MAX_UPLOAD_MB * 1024 * 1024:
        return jsonify({"ok": False, "msg": "文件名或大小不对（单批总量不超过 500 MB）"}), 400
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        import requests as _rq

        oauth_refresh = _env("GOOGLE_OAUTH_REFRESH_TOKEN")
        oauth_id = _env("GOOGLE_OAUTH_CLIENT_ID")
        oauth_secret = _env("GOOGLE_OAUTH_CLIENT_SECRET")
        if not (oauth_refresh and oauth_id and oauth_secret):
            return jsonify({"ok": False, "msg": "云盘直传暂未开通（需要站长本人的 OAuth 授权）"}), 503
        creds = Credentials(
            token=None, refresh_token=oauth_refresh, token_uri="https://oauth2.googleapis.com/token",
            client_id=oauth_id, client_secret=oauth_secret,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        creds.refresh(Request())
        meta = {"name": name, "parents": [PENDING_FOLDER_ID]}
        r = _rq.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable",
            headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json; charset=UTF-8"},
            json=meta, timeout=30,
        )
        if r.status_code != 200 or not r.headers.get("Location"):
            return jsonify({"ok": False, "msg": "云盘直传会话创建失败，请重试"}), 502
        return jsonify({"ok": True, "session": r.headers["Location"]})
    except Exception as e:
        print(f"[direct] session failed: {e}", flush=True)
        return jsonify({"ok": False, "msg": "云盘直传会话创建失败，请重试"}), 502


@app.route("/direct_done", methods=["POST"])
def direct_done():
    """直传完成登记：浏览器传完后告诉服务器一声，服务器记一条上传日志；
    实际文件已经在待审核里，站长在站长区通过或删除，不通过就点不通过进回收站。"""
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access"):
        return jsonify({"ok": False}), 403
    data = request.get_json(silent=True) or {}
    name = _safe_filename(data.get("name", "")) or "文件"
    try:
        fid = data.get("file_id", "")
        sz = int(data.get("size") or 0)
        if fid and sz > 0:
            EXPECTED_SIZES[fid] = sz
    except Exception:
        pass
    _log("上传（直传）", name)
    return jsonify({"ok": True, "msg": "已上传到待审核，站长通过后才会出现在已发布列表"})


@app.route("/view/<file_id>")
def view(file_id):
    """在线预览：已发布文件熟人可看，待审核文件只有站长可看。
    图片、PDF、文本、视频、音频会在浏览器里直接打开，其他类型浏览器会提示下载。"""
    if _is_relay():
        return _proxy_to_backend(f"/view/{file_id}")
    service = drive_service()
    if service is None:
        return "云盘暂未连接", 503
    try:
        meta = get_file(service, file_id)
    except Exception:
        return "文件不存在或无权访问", 404
    parents = meta.get("parents") or []
    is_published = _is_published_parent(service, parents)
    is_pending = PENDING_FOLDER_ID in parents
    if is_published:
        code = _env("EXCHANGE_CODE")
        if not _has_gate(code, "exchange_access"):
            return redirect("/?msg=请先输入访问口令")
    elif is_pending:
        token = _env("ADMIN_TOKEN")
        if not token or not _has_gate(token, "admin_access"):
            return "该文件尚未发布", 403
    else:
        owner = _private_owner(service, meta)
        if owner is None or (owner != _account_gate() and not _private_is_admin()):
            return "文件不存在或无权访问", 404
    from googleapiclient.http import MediaIoBaseDownload

    ctype = meta.get("mimeType") or "application/octet-stream"
    # 安全：HTML/SVG 等可在同源执行的网页内容绝不能 inline 预览，否则上传者写的脚本
    # 会在站长浏览器里以本站身份调用审核接口。只对图片、PDF、纯文本、音视频 inline，
    # 其他一律走下载。
    _safe_inline = ctype.startswith(("image/", "video/", "audio/")) or ctype in (
        "application/pdf", "text/plain", "text/markdown",
    ) or ctype.startswith("text/plain")
    if not _safe_inline:
        return redirect(f"/download/{file_id}")
    # 文本预览必须带 charset=utf-8，否则浏览器默认按 Latin-1 解码，中文会乱码。
    if ctype.startswith("text/") and "charset" not in ctype:
        ctype = ctype + "; charset=utf-8"

    def generate_view():
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, service.files().get_media(fileId=file_id))
        downloader._chunksize = 1024 * 1024
        done = False
        while not done:
            buf.seek(0)
            buf.truncate(0)
            _, done = downloader.next_chunk()
            chunk = buf.getvalue()
            if chunk:
                yield chunk

    _log("打开预览", meta.get("name", file_id))
    return Response(
        generate_view(),
        headers={
            "Content-Disposition": _disposition("inline", meta["name"]),
            "Content-Type": ctype,
        },
    )


@app.route("/download/<file_id>")
def download(file_id):
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access"):
        return redirect("/?msg=请先输入访问口令")
    if _is_relay():
        return _proxy_to_backend(f"/download/{file_id}")
    service = drive_service()
    if service is None:
        return "云盘暂未连接，下载暂不可用", 503
    try:
        meta = get_file(service, file_id)
    except Exception:
        return "文件不存在或无权访问", 404
    # 安全底线：只有在已发布树（根目录或其一级子文件夹）里的文件才能下载，待审核文件一律拒绝。
    # 私人区文件例外：本人或站长可以下载，其他人不行。
    if not _is_published_parent(service, meta.get("parents") or []):
        owner = _private_owner(service, meta)
        if owner is None or (owner != _account_gate() and not _private_is_admin()):
            return "该文件尚未发布", 403
    # 用 Drive 官方的 get_media 流式回传，令牌由 googleapiclient 自动刷新，
    # 不再手动拼 Bearer 令牌，避免过期令牌导致下载到空内容。
    from googleapiclient.http import MediaIoBaseDownload

    def generate_dl():
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, service.files().get_media(fileId=file_id))
        downloader._chunksize = 1024 * 1024
        done = False
        while not done:
            buf.seek(0)
            buf.truncate(0)
            _, done = downloader.next_chunk()
            chunk = buf.getvalue()
            if chunk:
                yield chunk

    _log("下载", meta.get("name", file_id))
    dl_headers = {
        "Content-Disposition": _disposition("attachment", meta["name"]),
        "Content-Type": meta.get("mimeType") or "application/octet-stream",
    }
    # Drive 元数据里有真实大小时必须带上 Content-Length，否则手机浏览器不知道总大小，会显示 -1.0 B
    try:
        _sz = int(meta.get("size") or 0)
        if _sz > 0:
            dl_headers["Content-Length"] = str(_sz)
    except Exception:
        pass
    return Response(generate_dl(), headers=dl_headers)



@app.route("/private/upload", methods=["POST"])
def private_upload():
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access"):
        return redirect("/?msg=请先输入访问口令")
    fb_user = _account_gate()
    if not fb_user:
        return redirect("/?msg=私人区要先注册登录#private")
    if _is_relay():
        try:
            import requests as _rq
            _cookies = {}
            for _k in ("exchange_access", "feedback_user"):
                if _k in request.cookies:
                    _cookies[_k] = request.cookies[_k]
            _files = [("file", (u.filename, u.stream, u.mimetype)) for u in request.files.getlist("file") if u and u.filename]
            _r = _rq.post(_relay_backend() + "/private/upload", cookies=_cookies, files=_files, timeout=300, allow_redirects=False)
            if _r.status_code in (301, 302, 303) and _r.headers.get("Location"):
                loc = _r.headers["Location"]
                return redirect(loc if loc.startswith("/") else "/#private")
            return redirect("/#private")
        except Exception as e:
            return redirect(f"/?msg=中转私人上传失败：{e}#private")
    service = drive_service()
    if service is None:
        return redirect("/?msg=云盘暂未连接，私人上传暂不可用#private")
    if not _private_account_ok(service, fb_user):
        return redirect("/?msg=这个账号已被禁用或不存在#private")
    folder = _private_user_folder_id(service, fb_user)
    if not folder:
        return redirect("/?msg=私人文件夹创建失败，请重试#private")
    # 每人私人空间上限 20 GB：已有 + 这次要传的，超了就整批拒绝，不传一半。
    PRIVATE_QUOTA = 20 * 1024 * 1024 * 1024
    used = sum(f.get("size_bytes", 0) for f in list_folder(service, folder))
    _uploads = [u for u in request.files.getlist("file") if u and u.filename]
    _new_total = 0
    for _u in _uploads:
        try:
            _u.stream.seek(0, 2)
            _new_total += _u.stream.tell()
            _u.stream.seek(0)
        except Exception:
            pass
    if used + _new_total > PRIVATE_QUOTA:
        return redirect("/?msg=私人空间已满：每人上限 20 GB，这次传完会超，请先删掉一些私人文件再传#private")
    from googleapiclient.http import MediaIoBaseUpload
    count = 0
    for up in _uploads:
        if not up or not up.filename:
            continue
        name = _safe_filename(up.filename)
        if not name:
            continue
        try:
            up.stream.seek(0)
        except Exception:
            pass
        media = MediaIoBaseUpload(up.stream, mimetype=up.mimetype or "application/octet-stream", resumable=True, chunksize=1024 * 1024)
        service.files().create(body={"name": name, "parents": [folder]}, media_body=media, fields="id").execute()
        count += 1
    if count == 0:
        return redirect("/?msg=没有可上传的文件#private")
    _log("私人上传", f"{fb_user} {count} 个文件")
    return redirect(f"/?msg=已上传 {count} 个文件到私人区，只有你和站长能看见#private")


@app.route("/private/delete", methods=["POST"])
def private_delete():
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access") and not _private_is_admin():
        return redirect("/?msg=请先输入访问口令")
    fb_user = _account_gate()
    if _is_relay():
        return _proxy_post_to_backend("/private/delete")
    service = drive_service()
    if service is None:
        return redirect("/?msg=云盘暂未连接#private")
    file_id = request.form.get("file_id", "")
    if not file_id:
        return redirect("/#private")
    try:
        meta = get_file(service, file_id)
    except Exception:
        return redirect("/?msg=文件不存在或无权访问#private")
    owner = _private_owner(service, meta)
    if owner is None or (owner != fb_user and not _private_is_admin()):
        return redirect("/?msg=这不是你的私人文件#private")
    service.files().update(fileId=file_id, body={"trashed": True}).execute()
    _log("私人删除", meta.get("name", file_id))
    return redirect("/#private")


@app.route("/admin/private")
def admin_private():
    token = _env("ADMIN_TOKEN")
    if not token or not _has_gate(token, "admin_access"):
        return "请先在首页输入站长口令", 403
    if _is_relay():
        try:
            import requests as _rq
            _cookies = {}
            if "admin_access" in request.cookies:
                _cookies["admin_access"] = request.cookies["admin_access"]
            _r = _rq.get(_relay_backend() + "/api/private_all", cookies=_cookies, timeout=15)
            users = _r.json().get("users", [])
        except Exception:
            users = []
    else:
        service = drive_service()
        users = []
        root = _private_root_id(service) if service else None
        if service and root:
            try:
                res = service.files().list(
                    q=f"'{root}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                    fields="files(id,name)", pageSize=100).execute()
                for folder in res.get("files") or []:
                    users.append({"name": folder["name"], "files": list_folder(service, folder["id"])})
            except Exception:
                users = []
    rows = ""
    for u in users:
        _used = sum(f.get("size_bytes", 0) for f in u["files"])
        rows += f"<div class=\"card section\"><h2>{u['name']} 的私人文件</h2><p class=\"note\">已用 {_fmt_size(_used)} / 20 GB</p>"
        if u["files"]:
            for f in u["files"]:
                rows += (f"<div class=\"file\"><div class=\"fic\">{f['ext']}</div>"
                         f"<div class=\"fmain\"><div class=\"fname\">{f['name']}</div><div class=\"fmeta\"><span>{f['size']}</span></div></div>"
                         f"<div class=\"actions\"><a class=\"btn\" href=\"/view/{f['id']}\" target=\"_blank\">打开预览</a>"
                         f"<a class=\"btn primary\" href=\"/download/{f['id']}\">下载</a>"
                         f"<form method=\"post\" action=\"/private/delete\" style=\"display:inline\" onsubmit=\"return confirm('确定删除这个私人文件吗？')\"><input type=\"hidden\" name=\"file_id\" value=\"{f['id']}\"><button class=\"btn danger\">删除</button></form></div></div>")
        else:
            rows += "<p class=\"empty\">这个账号的私人区是空的。</p>"
        rows += "</div>"
    if not rows:
        rows = "<p class=\"empty\">目前还没有任何私人文件。</p>"
    return ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>私人区管理 - 交换站</title><style>" + BASE_CSS + "</style></head>"
            "<body><div class=\"wrap\"><h1>私人区管理（站长可见全部）</h1>"
            "<p class=\"note\">站长能看见所有人的私人文件，普通用户只能看见自己的。</p>"
            + rows +
            "<p><a href=\"/admin\">回待审核</a> · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div></body></html>")


@app.route("/feedback", methods=["POST"])
def feedback_submit():
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access"):
        return redirect("/?msg=请先输入访问口令")
    fb_user = _account_gate()
    if not fb_user:
        return redirect("/?msg=提意见要先注册登录#feedback")
    text = (request.form.get("text") or "").strip().replace("\n", " ").replace("\t", " ")
    if not text:
        return redirect("/#feedback")
    if len(text) > 500:
        text = text[:500]
    service = drive_service()
    if service is None:
        return redirect("/?msg=云盘暂未连接，意见暂无法保存#feedback")
    items = _read_feedback(service)  # newest first
    import time as _t, random as _r
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    fid = now.strftime("%Y%m%d%H%M%S") + str(_r.randint(100, 999))
    items.insert(0, {"id": fid, "time": now.strftime("%Y-%m-%d %H:%M"), "status": "open", "text": text, "author": fb_user})
    # _write_feedback expects oldest first
    if _write_feedback(service, list(reversed(items))):
        _log("提意见", text[:20])
        return redirect("/?msg=意见已提交，大家都能在意见区看见#feedback")
    return redirect("/?msg=意见保存失败，请重试#feedback")


@app.route("/feedback/resolve", methods=["POST"])
def feedback_resolve():
    token = _env("ADMIN_TOKEN")
    if not token or not _has_gate(token, "admin_access"):
        return "只有站长能改状态", 403
    if _is_relay():
        return _proxy_post_to_backend("/feedback/resolve")
    fid = request.form.get("file_id") or request.form.get("id") or ""
    service = drive_service()
    if service is None:
        return "云盘暂未连接", 503
    items = _read_feedback(service)
    for it in items:
        if it["id"] == fid:
            it["status"] = "done" if it["status"] != "done" else "open"
    _write_feedback(service, list(reversed(items)))
    _log("改意见状态", fid)
    return redirect("/admin")



@app.route("/register", methods=["POST"])
def register():
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access"):
        return redirect("/?msg=请先输入访问口令")
    name = (request.form.get("name") or "").strip()
    pw = request.form.get("password") or ""
    if not name or not pw or len(name) > 20 or len(pw) > 50:
        return redirect("/?msg=账号名和密码不能为空#feedback")
    service = drive_service()
    if service is None:
        return redirect("/?msg=云盘暂未连接，注册暂不可用#feedback")
    accounts = _read_accounts(service)
    if any(a["name"] == name for a in accounts):
        return redirect("/?msg=这个账号名已经被注册了，换一个#feedback")
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    accounts.append({"name": name, "password": pw, "created": now.strftime("%Y-%m-%d %H:%M"), "last": "", "disabled": "0"})
    if _write_accounts(service, accounts):
        resp = redirect("/?msg=注册成功，已自动登录，可以提意见了#feedback")
        resp.set_cookie("feedback_user", name, httponly=True, samesite="Lax")
        return resp
    return redirect("/?msg=注册失败，请重试#feedback")


@app.route("/login_feedback", methods=["POST"])
def login_feedback():
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access"):
        return redirect("/?msg=请先输入访问口令")
    name = (request.form.get("name") or "").strip()
    pw = request.form.get("password") or ""
    service = drive_service()
    accounts = _read_accounts(service) if service else []
    for a in accounts:
        if a["name"] == name and a["password"] == pw:
            if a.get("disabled") == "1":
                return redirect("/?msg=这个账号已被站长禁用#feedback")
            a["last"] = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
            _write_accounts(service, accounts)
            resp = redirect("/?msg=登录成功#feedback")
            resp.set_cookie("feedback_user", name, httponly=True, samesite="Lax")
            return resp
    return redirect("/?msg=账号或密码不对#feedback")


@app.route("/logout_feedback")
def logout_feedback():
    resp = redirect("/#feedback")
    resp.delete_cookie("feedback_user")
    return resp


@app.route("/admin/accounts")
def admin_accounts():
    token = _env("ADMIN_TOKEN")
    if not token or not _has_gate(token, "admin_access"):
        return "请先在首页输入站长口令", 403
    service = drive_service()
    accounts = _read_accounts(service) if service else []
    rows = "".join(
        f"<div class=\"logrow\"><div class=\"fname\">{a['name']}</div><div class=\"fmeta\"><span>密码：{a['password']}</span><span>·</span><span>注册：{a['created']}</span><span>·</span><span>最后登录：{a.get('last','')}</span><span>·</span><span>{'已禁用' if a.get('disabled')=='1' else '正常'}</span></div>"
        f"<form method=\"post\" action=\"/admin/accounts/change\" style=\"margin-top:6px\"><input type=\"hidden\" name=\"name\" value=\"{a['name']}\">"
        f"<input class=\"textin\" type=\"text\" name=\"new_password\" placeholder=\"新密码\" required> <button class=\"btn\">改密码</button> "
        f"<button class=\"btn\" formaction=\"/admin/accounts/toggle\">{'解封' if a.get('disabled')=='1' else '禁用'}</button></form></div>"
        for a in accounts
    ) or "<p class=\"empty\">目前还没有注册账号。</p>"
    return render_template_string(
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>账号管理 - 交换站</title><style>" + BASE_CSS + "</style></head><body><div class=\"wrap\">"
        "<h1>账号管理</h1><p class=\"note\">站长能看见账号和密码，也能改密码、禁用 / 解封。请提醒用户不要用重要密码注册。</p>"
        "<div class=\"card section\">" + rows + "</div>"
        "<p><a href=\"/admin\">回待审核</a> · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div></body></html>"
    )


@app.route("/admin/accounts/change", methods=["POST"])
def admin_accounts_change():
    token = _env("ADMIN_TOKEN")
    if not token or not _has_gate(token, "admin_access"):
        return "只有站长能改", 403
    if _is_relay():
        return _proxy_post_to_backend("/admin/accounts/change")
    name = request.form.get("name") or ""
    new_pw = request.form.get("new_password") or ""
    service = drive_service()
    accounts = _read_accounts(service) if service else []
    for a in accounts:
        if a["name"] == name and new_pw:
            a["password"] = new_pw
    _write_accounts(service, accounts)
    return redirect("/admin/accounts")


@app.route("/admin/accounts/toggle", methods=["POST"])
def admin_accounts_toggle():
    token = _env("ADMIN_TOKEN")
    if not token or not _has_gate(token, "admin_access"):
        return "只有站长能改", 403
    if _is_relay():
        return _proxy_post_to_backend("/admin/accounts/toggle")
    name = request.form.get("name") or ""
    service = drive_service()
    accounts = _read_accounts(service) if service else []
    for a in accounts:
        if a["name"] == name:
            a["disabled"] = "0" if a.get("disabled") == "1" else "1"
    _write_accounts(service, accounts)
    return redirect("/admin/accounts")




@app.route("/admin/login", methods=["POST"])
def admin_login():
    token = _env("ADMIN_TOKEN")
    if not token:
        return "站长口令还没配置，请联系站长在 Render 环境变量中设置 ADMIN_TOKEN", 503
    if request.form.get("token", "") != token:
        _log("站长进审核失败", "口令不对")
        return "口令不对", 403
    resp = redirect("/admin")
    _log("站长进审核", "")
    resp.set_cookie("admin_access", _gate_value(token), httponly=True, samesite="Lax")
    return resp


@app.route("/admin")
def admin():
    token = _env("ADMIN_TOKEN")
    if not token:
        return "站长口令还没配置，请联系站长在 Render 环境变量中设置 ADMIN_TOKEN", 503
    if not _has_gate(token, "admin_access"):
        return "请先在首页输入站长口令", 403
    if _is_relay():
        files = _relay_files().get("pending", [])
    else:
        files = list_folder(drive_service(), PENDING_FOLDER_ID)
    return render_template_string(
        ADMIN_PAGE, files=files, drive_configured=True
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
    if _is_relay():
        return _proxy_post_to_backend("/admin/approve")
    service = drive_service()
    if service is None:
        return "云盘暂未连接", 503
    file_id = request.form.get("file_id", "")
    if not file_id or _admin_file_or_403(service, file_id) is None:
        return "该文件不在待审核里，无法通过", 403
    _info = get_file(service, file_id) or {}
    _name = _info.get("name", file_id)
    _expected = EXPECTED_SIZES.get(file_id)
    if _expected:
        try:
            _actual = int(_info.get("size") or 0)
        except Exception:
            _actual = 0
        if _actual != _expected:
            return f"该文件还没传完，不能通过：云盘里只有 {_fmt_size(_actual)}，上传时应为 {_fmt_size(_expected)}，请等上传完成后再通过", 409
    service.files().update(
        fileId=file_id,
        addParents=PUBLISHED_FOLDER_ID,
        removeParents=PENDING_FOLDER_ID,
    ).execute()
    _log("审核通过", _name)
    _invalidate_pub_cache()
    return redirect("/admin")


@app.route("/admin/reject", methods=["POST"])
def reject():
    token = _env("ADMIN_TOKEN")
    if not token or not _has_gate(token, "admin_access"):
        return "请先在首页输入站长口令", 403
    if _is_relay():
        return _proxy_post_to_backend("/admin/reject")
    service = drive_service()
    if service is None:
        return "云盘暂未连接", 503
    file_id = request.form.get("file_id", "")
    if not file_id or _admin_file_or_403(service, file_id) is None:
        return "该文件不在待审核里，无法处理", 403
    _name = (get_file(service, file_id) or {}).get("name", file_id)
    service.files().update(fileId=file_id, body={"trashed": True}).execute()
    _log("审核不通过", _name)
    return redirect("/admin")


@app.route("/admin/published")
def admin_published():
    token = _env("ADMIN_TOKEN")
    if not token or not _has_gate(token, "admin_access"):
        return "请先在首页输入站长口令", 403
    if _is_relay():
        files = _relay_files().get("published", [])
    else:
        files, _fo = _list_published_tree(drive_service())
    return render_template_string(
        ADMIN_PUBLISHED_PAGE, files=files, drive_configured=True
    )


@app.route("/admin/delete", methods=["POST"])
def admin_delete():
    token = _env("ADMIN_TOKEN")
    if not token or not _has_gate(token, "admin_access"):
        return "请先在首页输入站长口令", 403
    if _is_relay():
        return _proxy_post_to_backend("/admin/delete")
    service = drive_service()
    if service is None:
        return "云盘暂未连接", 503
    file_id = request.form.get("file_id", "")
    if not file_id:
        return "没有文件", 403
    try:
        meta = get_file(service, file_id)
    except Exception:
        return "文件不存在或无权访问", 404
    if not _is_published_parent(service, meta.get("parents") or []):
        return "该文件不在已发布里，无法删除", 403
    _name = (get_file(service, file_id) or {}).get("name", file_id)
    service.files().update(fileId=file_id, body={"trashed": True}).execute()
    _invalidate_pub_cache()
    _log("删除已发布", _name)
    return redirect("/admin/published")



@app.route("/admin/feedback")
def admin_feedback():
    token = _env("ADMIN_TOKEN")
    if not token or not _has_gate(token, "admin_access"):
        return "请先在首页输入站长口令", 403
    service = drive_service()
    items = _read_feedback(service) if service else []
    rows = "".join(
        f"<div class=\"logrow\"><div class=\"fname\">{it['text']}</div><div class=\"fmeta\"><span>{it['time']}</span><span>·</span><span>{'已解决' if it['status']=='done' else '未解决'}</span></div>"
        f"<form method=\"post\" action=\"/feedback/resolve\" style=\"margin-top:6px\"><input type=\"hidden\" name=\"id\" value=\"{it['id']}\">"
        f"<button class=\"btn\">{'改回未解决' if it['status']=='done' else '标为已解决'}</button></form></div>"
        for it in items
    ) or "<p class=\"empty\">目前还没有意见。</p>"
    return render_template_string(
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>意见管理 - 交换站</title><style>" + BASE_CSS + "</style></head><body><div class=\"wrap\">"
        "<h1>意见管理</h1><p class=\"note\">只有站长能改状态，改完后意见区里大家都能看见已解决 / 未解决。</p>"
        "<div class=\"card section\">" + rows + "</div>"
        "<p><a href=\"/admin\">回待审核</a> · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div></body></html>"
    )


@app.route("/admin/logs")
def admin_logs():
    token = _env("ADMIN_TOKEN")
    if not token or not _has_gate(token, "admin_access"):
        return "请先在首页输入站长口令", 403
    # 打开日志页时先把缓冲写进云盘，再从云盘读回最近的记录，和内存里的合并去重。
    _flush_drive_log()
    entries = list(ACCESS_LOG)
    try:
        service = drive_service()
        if service is not None:
            fid = _DRIVE_LOG_FILE_ID[0] or _env("ACCESS_LOG_DRIVE_FILE_ID")
            if not fid:
                res = service.files().list(q="name = '交换站访问日志.txt' and trashed = false", fields="files(id)", pageSize=5).execute()
                fl = res.get("files") or []
                fid = fl[0]["id"] if fl else None
            if fid:
                _DRIVE_LOG_FILE_ID[0] = fid
                from googleapiclient.http import MediaIoBaseDownload

                buf = io.BytesIO()
                dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=fid))
                done = False
                while not done:
                    _, done = dl.next_chunk()
                for line in buf.getvalue().decode("utf-8", errors="ignore").strip().split("\n")[-200:]:
                    parts = line.split(" ", 5)
                    if len(parts) >= 6:
                        entries.append({"time": parts[0] + " " + parts[1], "ip": parts[2], "loc": parts[3], "device": parts[4], "action": parts[5], "detail": ""})
                    elif len(parts) >= 5:
                        entries.append({"time": parts[0] + " " + parts[1], "ip": parts[2], "loc": _ip_loc(parts[2]), "device": parts[3], "action": parts[4], "detail": ""})
    except Exception:
        pass
    seen = set()
    uniq = []
    for e in entries:
        k = (e["time"], e["ip"], e["action"], e["detail"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    for e in uniq:
        if not e.get('loc'):
            e['loc'] = _ip_loc(e['ip']) or '属地未知'
    rows = "".join(
        f"<div class=\"logrow\"><div class=\"fname\">{e['time']} · {e['action']} · {e['detail']}</div><div class=\"fmeta\"><span>{e['ip']}</span><span>·</span><span>{e.get('loc','')}</span><span>·</span><span>{e['device']}</span></div></div>"
        for e in uniq[:200]
    ) or "<p class=\"empty\">暂无访问记录。</p>"
    return render_template_string(
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>访问日志 - 交换站</title><style>" + BASE_CSS + "</style></head><body><div class=\"wrap\">"
        "<h1>访问日志</h1><p class=\"note\">只记时间、IP、设备类型、动作和文件名，不记口令。长期记录存在云盘根目录的 交换站访问日志.txt 里（不在待审核/已发布里，熟人看不到），服务休眠或重启也不会丢。</p>"
        "<div class=\"card section\">" + rows + "</div>"
        "<p><a href=\"/admin\">回待审核</a> · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div></body></html>"
    )


@app.route("/thumb/<file_id>")
def thumb(file_id):
    """图片缩略图：只给已发布（熟人）或待审核（站长）看，权限和预览一致。
    用 Pillow 缩到 160 像素宽再返回，没有 Pillow 时原图返回，浏览器按 CSS 缩放。"""
    if _is_relay():
        return _proxy_to_backend(f"/thumb/{file_id}")
    service = drive_service()
    if service is None:
        return "云盘暂未连接", 503
    try:
        meta = get_file(service, file_id)
    except Exception:
        return "文件不存在或无权访问", 404
    parents = meta.get("parents") or []
    is_published = _is_published_parent(service, parents)
    is_pending = PENDING_FOLDER_ID in parents
    if is_published:
        code = _env("EXCHANGE_CODE")
        if not _has_gate(code, "exchange_access"):
            return "请先输入访问口令", 403
    elif is_pending:
        token = _env("ADMIN_TOKEN")
        if not token or not _has_gate(token, "admin_access"):
            return "该文件尚未发布", 403
    else:
        owner = _private_owner(service, meta)
        if owner is None or (owner != _account_gate() and not _private_is_admin()):
            return "文件不存在或无权访问", 404
    if (meta.get("mimeType") or "").startswith("video/"):
        # MP4 缩略图不用 ffmpeg：云盘本身会给视频生成缩略图，直接跳到云盘缩略图链接。
        # 该链接是云盘生成的临时链接，没有它时回退到类型图标。
        tl = meta.get("thumbnailLink")
        if tl:
            return redirect(tl)
        return "暂无视频缩略图", 404
    if not (meta.get("mimeType") or "").startswith("image/"):
        return "不是图片", 404
    from googleapiclient.http import MediaIoBaseDownload

    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, service.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = downloader.next_chunk()
    data = buf.getvalue()
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(data))
        im.thumbnail((320, 320))
        out = io.BytesIO()
        im.convert("RGB").save(out, format="JPEG", quality=80)
        return Response(out.getvalue(), headers={"Content-Type": "image/jpeg", "Cache-Control": "public, max-age=86400"})
    except Exception:
        return Response(data, headers={"Content-Type": meta.get("mimeType") or "image/jpeg", "Cache-Control": "public, max-age=86400"})


@app.route("/rename", methods=["POST"])
def rename():
    """改名：只有站长能改名（已发布和待审核都行），熟人不能改已发布区的文件。
    原因：本站没有每人账号，分不出谁是原上传者，放开给熟人等于谁都能改别人的名字。
    只改名字不改内容，后缀不写时自动保留原后缀；改名会记进访问日志。"""
    if _is_relay():
        # Rename form posts here in relay mode too; backend checks admin gate itself
        return _proxy_post_to_backend("/rename")
    service = drive_service()
    if service is None:
        return "云盘暂未连接", 503
    file_id = request.form.get("file_id", "")
    new_name = (request.form.get("new_name", "") or "").strip()
    if not file_id or not new_name:
        return "请填写新名字", 400
    new_name = _safe_filename(new_name)
    if not new_name:
        return "名字无效", 400
    try:
        meta = get_file(service, file_id)
    except Exception:
        return "文件不存在或无权访问", 404
    parents = meta.get("parents") or []
    is_published = _is_published_parent(service, parents)
    is_pending = PENDING_FOLDER_ID in parents
    token = _env("ADMIN_TOKEN")
    is_admin = bool(token) and _has_gate(token, "admin_access")
    if not is_admin:
        return "只有站长能改名，请先在站长区输入站长口令", 403
    if not is_published and not is_pending:
        return "文件不存在或无权访问", 404
    old_name = meta.get("name", "")
    if "." not in new_name and "." in old_name:
        new_name = new_name + "." + old_name.rsplit(".", 1)[-1]
    service.files().update(fileId=file_id, body={"name": new_name}).execute()
    _invalidate_pub_cache()
    _log("改名", f"{old_name} -> {new_name}")
    return redirect(request.form.get("back", "/") or "/")


@app.route("/folder/create", methods=["POST"])
def folder_create():
    if _is_relay():
        return _proxy_post_to_backend("/folder/create")
    code = _env("EXCHANGE_CODE")
    if not code or not _has_gate(code, "exchange_access"):
        return redirect("/?msg=请先输入访问口令")
    service = drive_service()
    if service is None:
        return redirect("/?msg=云盘暂未连接，无法新建文件夹#published")
    name = _safe_filename(request.form.get("name", ""))
    if not name or name == "全部" or len(name) > 50:
        return redirect("/?msg=文件夹名字无效#published")
    for fo in _published_subfolders(service):
        if fo["name"].lower() == name.lower():
            return redirect("/?msg=已有同名文件夹#published")
    service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [PUBLISHED_FOLDER_ID]},
        fields="id").execute()
    _invalidate_pub_cache()
    _log("新建文件夹", name)
    return redirect(f"/?msg=已新建文件夹 {name}#published")


@app.route("/folder/move", methods=["POST"])
def folder_move():
    if _is_relay():
        return _proxy_post_to_backend("/folder/move")
    code = _env("EXCHANGE_CODE")
    if not code or not _has_gate(code, "exchange_access"):
        return redirect("/?msg=请先输入访问口令")
    service = drive_service()
    if service is None:
        return redirect("/?msg=云盘暂未连接，无法移动#published")
    dest_id = (request.form.get("dest_folder_id") or "").strip()
    raw = request.form.get("file_ids", "") or ""
    file_ids = [x.strip() for x in raw.split(",") if x.strip()] or request.form.getlist("file_id")
    if not dest_id or not file_ids:
        return redirect("/?msg=请选择文件和目标文件夹#published")
    if dest_id == PUBLISHED_FOLDER_ID:
        dest_name = "已发布根目录"
    else:
        try:
            dm = get_file(service, dest_id)
        except Exception:
            return redirect("/?msg=目标文件夹不存在#published")
        if dm.get("mimeType") != "application/vnd.google-apps.folder" or PUBLISHED_FOLDER_ID not in (dm.get("parents") or []):
            return redirect("/?msg=目标文件夹不在已发布里#published")
        dest_name = dm.get("name", dest_id)
    moved = 0
    for fid in file_ids:
        try:
            meta = get_file(service, fid)
        except Exception:
            continue
        parents = meta.get("parents") or []
        if not _is_published_parent(service, parents):
            continue
        old = parents[0] if parents else PUBLISHED_FOLDER_ID
        if old == dest_id:
            continue
        service.files().update(fileId=fid, addParents=dest_id, removeParents=old).execute()
        moved += 1
    _invalidate_pub_cache()
    _log("移动到文件夹", f"{moved} 个文件 -> {dest_name}")
    return redirect(f"/?msg=已移动 {moved} 个文件到 {dest_name}#published")


@app.route("/health")
def health():
    return jsonify({"ok": True, "drive_configured": True if _is_relay() else _drive_configured()})


@app.errorhandler(413)
def too_large(_error):
    return redirect(f"/?msg=一次上传总量超过了 {MAX_UPLOAD_MB} MB，请减少文件或分批上传")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
