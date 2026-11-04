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
- SECONDARY_ADMIN_TOKEN：二级管理员口令（独立于站长口令；未配置时二级入口关闭；与站长口令相同时直接失效）
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
import tempfile
import threading
import shutil
import subprocess
import struct
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


# ---------------- 进站公告（站长在站长区“公告管理”里改，不用改代码） ----------------
# 公告存在云盘根目录的 交换站公告.txt 里，JSON 存 {"title": ..., "body": ...}，和留言/
# AI 结论文件同一个做法。读取规则：文件不存在/读不出/JSON 坏了 → 用默认文案弹；
# 正文被清空（空字符串）→ 不弹公告（等于站长把公告关了）。读取不走 30 秒已发布
# 列表缓存，每次进站现读，保存后下一次进站立即生效。
ANNOUNCEMENT_FILE_NAME = "交换站公告.txt"
ANNOUNCEMENT_DEFAULT_TITLE = "交换站公告"
ANNOUNCEMENT_DEFAULT_BODY = "这是一个大家共用的免费云盘，大家可以在上面上传自己喜欢的 OC 和文件，分享给朋友们。上传的文件会先经过审核，通过后才会公开显示，请不要上传违规内容。"


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


def _is_admin():
    token = _env("ADMIN_TOKEN")
    return bool(token) and _has_gate(token, "admin_access")


def _secondary_configured():
    return bool(_env("SECONDARY_ADMIN_TOKEN"))


def _is_secondary():
    """二级管理员：独立口令、独立 cookie；与站长口令相同时直接失效，防止误配提权。"""
    token = _env("SECONDARY_ADMIN_TOKEN")
    if not token:
        return False
    if token == _env("ADMIN_TOKEN"):
        return False
    return _has_gate(token, "secondary_access")


def _is_manager():
    """站长或二级管理员。权限类写操作（账号改密码/禁用）不用它，另走 _is_admin。"""
    return _is_admin() or _is_secondary()


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
    for _k in ("exchange_access", "admin_access", "feedback_user", "secondary_access"):
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
    for _k in ("exchange_access", "admin_access", "feedback_user", "secondary_access"):
        if _k in request.cookies:
            cookies[_k] = request.cookies[_k]
    try:
        r = _rq.get(_relay_backend() + "/api/files", cookies=cookies, timeout=15)
        return r.json()
    except Exception:
        return None


def _proxy_post_to_backend(path):
    """Forward an admin POST form to the backend and return its result."""
    import requests as _rq
    cookies = {}
    for _k in ("exchange_access", "admin_access", "feedback_user", "secondary_access"):
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
    """合并已发布视图（谷歌 + 百度，去重、按优先后端排序），任一后端不通时
    只显示另一方并给出友好提示，绝不报 500。返回 (files, folders, notice)。"""
    now = time.time()
    if _PUB_CACHE['files'] is not None and now - _PUB_CACHE['t'] < 30:
        _dl_attach(_PUB_CACHE['files'], service)
        _ai_attach_published(_PUB_CACHE['files'])
        return _PUB_CACHE['files'], _PUB_CACHE['folders'] or [], _PUB_CACHE.get('notice') or ""
    gfiles, gfolders, google_err = [], [], False
    if _google_fallback_enabled():
        try:
            if service is not None:
                gfiles, gfolders = _list_published_tree(service)
        except Exception:
            gfiles, gfolders, google_err = [], [], True
    bfiles, bfolders, baidu_err = [], [], False
    if _baidu_enabled():
        try:
            bfiles, bfolders = _baidu_published()
        except Exception:
            baidu_err = True
    elif _priority_backend() == "baidu":
        baidu_err = True
    files, folders = _merge_mixed(gfiles, gfolders, bfiles, bfolders)
    notices = []
    if google_err:
        notices.append("谷歌区暂时连不上，只显示百度区")
    if baidu_err:
        if _priority_backend() == "baidu" and not _baidu_enabled():
            notices.append("百度后端未配置，只显示谷歌区")
        elif _baidu_enabled():
            notices.append("百度区暂时连不上，只显示谷歌区")
    notice = "；".join(notices)
    _dl_attach(files, service)
    _ai_attach_published(files)
    _PUB_CACHE['files'] = files
    _PUB_CACHE['folders'] = folders
    _PUB_CACHE['notice'] = notice
    _PUB_CACHE['t'] = now
    return files, folders, notice


def _invalidate_pub_cache():
    _PUB_CACHE['files'] = None
    _PUB_CACHE['folders'] = None
    _PUB_CACHE['t'] = 0


# ---------- 下载量统计 ----------
# 计数键 = 文件 id（谷歌 Drive id，或百度 "bd:<fs_id>"）。内存里实时计，
# 定期写回云盘已发布文件夹里的隐藏文件 _download_counts.json；列表渲染时按
# 精确文件名排除它，它绝不会变成一个格子。写回在后台线程做、节流 60 秒，
# 写回失败绝不阻塞下载；部署前的下载量一律从 0 起计，不编造历史。
_DL_COUNT_FILE = "_download_counts.json"
_DL_COUNTS = {}
_DL_DELTAS = {}
_DL_LOCK = threading.Lock()
_DL_STATE = {"loaded": False, "last_load_try": 0, "last_flush": 0,
             "flushing": False, "file_id": None}


def _dl_drive_counts(service):
    """从云盘隐藏文件读回计数字典。返回 (counts, ok)：没文件也算 ok；
    读不到、读坏、服务不可用时 ok 为 False，不抛异常。"""
    if service is None:
        return {}, False
    try:
        res = service.files().list(
            q="name = '" + _DL_COUNT_FILE + "' and '" + PUBLISHED_FOLDER_ID
            + "' in parents and trashed = false",
            fields="files(id)", pageSize=5).execute()
        fl = res.get("files") or []
        if not fl:
            return {}, True
        _DL_STATE["file_id"] = fl[0]["id"]
        from googleapiclient.http import MediaIoBaseDownload
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=fl[0]["id"]))
        done = False
        while not done:
            _, done = dl.next_chunk()
        data = json.loads(buf.getvalue().decode("utf-8", errors="ignore") or "{}")
        counts = data.get("counts") or {}
        out = {}
        for k, v in counts.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out[str(k)] = int(v)
        return out, True
    except Exception as e:
        print(f"[dlcount] load failed: {e}", flush=True)
        return {}, False


def _dl_ensure_loaded(service):
    now = time.time()
    with _DL_LOCK:
        if _DL_STATE["loaded"] or now - _DL_STATE["last_load_try"] < 30:
            return
        _DL_STATE["last_load_try"] = now
    counts, ok = _dl_drive_counts(service)
    if not ok:
        return
    with _DL_LOCK:
        for k, v in counts.items():
            _DL_COUNTS[k] = max(_DL_COUNTS.get(k, 0), v)
        _DL_STATE["loaded"] = True


def _dl_attach(files, service=None):
    """给文件字典补 dl_count（缺省 0）。已发布格子、/api/files 都走这里；
    中转站的 JSON 本来就带 dl_count，格子模板里有 default 兜底。"""
    try:
        _dl_ensure_loaded(service)
    except Exception:
        pass
    try:
        _dl_baidu_ensure_loaded()
    except Exception:
        pass
    with _DL_LOCK:
        snapshot = dict(_DL_COUNTS)
        snapshot_baidu = dict(_DL_BAIDU_COUNTS)
    for f in files or []:
        if isinstance(f, dict):
            if (f.get("id") or "").startswith("bd:"):
                f["dl_count"] = snapshot_baidu.get(f.get("id"), 0)
            else:
                f["dl_count"] = snapshot.get(f.get("id"), 0)


def _dl_should_count(total):
    """分段下载只计第 1 块（Range 起点为 0），避免一次下载被计成十几次；
    无 Range 的整文件下载计 1 次。预览（/view）和缩略图（/thumb）根本不调它。"""
    try:
        rng = _parse_range(request.headers.get("Range"), total)
    except Exception:
        return True
    return rng is None or rng[0] == 0


def _dl_flush_worker(service, snapshot):
    try:
        counts, _ok = _dl_drive_counts(service)
        for k, v in snapshot.items():
            counts[k] = counts.get(k, 0) + v
        body = json.dumps({"counts": counts}, ensure_ascii=False)
        from googleapiclient.http import MediaIoBaseUpload
        media = MediaIoBaseUpload(io.BytesIO(body.encode("utf-8")),
                                  mimetype="application/json", resumable=False)
        fid = _DL_STATE.get("file_id")
        if fid:
            service.files().update(fileId=fid, media_body=media).execute()
        else:
            created = service.files().create(
                body={"name": _DL_COUNT_FILE, "mimeType": "application/json",
                      "parents": [PUBLISHED_FOLDER_ID]},
                media_body=media, fields="id").execute()
            _DL_STATE["file_id"] = created.get("id")
        with _DL_LOCK:
            for k, v in snapshot.items():
                left = _DL_DELTAS.get(k, 0) - v
                if left > 0:
                    _DL_DELTAS[k] = left
                else:
                    _DL_DELTAS.pop(k, None)
            _DL_STATE["last_flush"] = time.time()
    except Exception as e:
        print(f"[dlcount] flush failed: {e}", flush=True)
    finally:
        with _DL_LOCK:
            _DL_STATE["flushing"] = False


def _dl_maybe_flush(service_hint=None):
    with _DL_LOCK:
        if _DL_STATE["flushing"]:
            return
        pending = sum(_DL_DELTAS.values())
        if pending <= 0:
            return
        now = time.time()
        if now - _DL_STATE["last_flush"] < 60 and pending < 50:
            return
        snapshot = dict(_DL_DELTAS)
        _DL_STATE["flushing"] = True
    service = service_hint
    if service is None:
        try:
            service = drive_service()
        except Exception:
            service = None
    if service is None:
        with _DL_LOCK:
            _DL_STATE["flushing"] = False
        return
    t = threading.Thread(target=_dl_flush_worker, args=(service, snapshot),
                         daemon=True)
    t.start()


def _dl_bump(file_id, service=None):
    """一次已校验通过的下载 +1（内存立即生效，写回后台节流进行）。"""
    if (file_id or "").startswith("bd:"):
        with _DL_LOCK:
            _DL_BAIDU_COUNTS[file_id] = _DL_BAIDU_COUNTS.get(file_id, 0) + 1
            _DL_BAIDU_DELTAS[file_id] = _DL_BAIDU_DELTAS.get(file_id, 0) + 1
        try:
            _dl_baidu_maybe_flush()
        except Exception:
            pass
        return
    with _DL_LOCK:
        _DL_COUNTS[file_id] = _DL_COUNTS.get(file_id, 0) + 1
        _DL_DELTAS[file_id] = _DL_DELTAS.get(file_id, 0) + 1
    try:
        _dl_maybe_flush(service)
    except Exception:
        pass


# ---------- 百度下载量（与谷歌计数文件平行，各后端自带一份） ----------
_DL_BAIDU_COUNTS = {}
_DL_BAIDU_DELTAS = {}
_DL_BAIDU_STATE = {"loaded": False, "last_load_try": 0, "last_flush": 0, "flushing": False}


def _dl_baidu_counts():
    if not _baidu_enabled():
        return {}, False
    try:
        _base, pub, _pend = _baidu_dirs()
        text = _baidu_read_text(pub + _DL_COUNT_FILE)
        if text is None:
            return {}, True
        data = json.loads(text or "{}")
        counts = data.get("counts") or {}
        out = {}
        for k, v in counts.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out[str(k)] = int(v)
        return out, True
    except Exception as e:
        print(f"[dlcount-baidu] load failed: {e}", flush=True)
        return {}, False


def _dl_baidu_ensure_loaded():
    now = time.time()
    with _DL_LOCK:
        if _DL_BAIDU_STATE["loaded"] or now - _DL_BAIDU_STATE["last_load_try"] < 30:
            return
        _DL_BAIDU_STATE["last_load_try"] = now
    counts, ok = _dl_baidu_counts()
    if not ok:
        return
    with _DL_LOCK:
        for k, v in counts.items():
            _DL_BAIDU_COUNTS[k] = max(_DL_BAIDU_COUNTS.get(k, 0), v)
        _DL_BAIDU_STATE["loaded"] = True


def _dl_baidu_flush_worker(snapshot):
    try:
        counts, _ok = _dl_baidu_counts()
        for k, v in snapshot.items():
            counts[k] = counts.get(k, 0) + v
        body = json.dumps({"counts": counts}, ensure_ascii=False)
        _base, pub, _pend = _baidu_dirs()
        if not _baidu_write_text(pub + _DL_COUNT_FILE, body):
            raise RuntimeError("baidu counts write failed")
        with _DL_LOCK:
            for k, v in snapshot.items():
                left = _DL_BAIDU_DELTAS.get(k, 0) - v
                if left > 0:
                    _DL_BAIDU_DELTAS[k] = left
                else:
                    _DL_BAIDU_DELTAS.pop(k, None)
            _DL_BAIDU_STATE["last_flush"] = time.time()
    except Exception as e:
        print(f"[dlcount-baidu] flush failed: {e}", flush=True)
    finally:
        with _DL_LOCK:
            _DL_BAIDU_STATE["flushing"] = False


def _dl_baidu_maybe_flush():
    with _DL_LOCK:
        if _DL_BAIDU_STATE["flushing"]:
            return
        pending = sum(_DL_BAIDU_DELTAS.values())
        if pending <= 0:
            return
        now = time.time()
        if now - _DL_BAIDU_STATE["last_flush"] < 60 and pending < 50:
            return
        snapshot = dict(_DL_BAIDU_DELTAS)
        _DL_BAIDU_STATE["flushing"] = True
    th = threading.Thread(target=_dl_baidu_flush_worker, args=(snapshot,), daemon=True)
    th.start()

# ---------- 百度网盘源（第一期：只读并排，默认关闭） ----------
# 只读：列表 / 预览 / 下载 / 图片缩略图。dlink 现取现用，不缓存、不写日志，
# 绝不 redirect 给浏览器。开关未开或任一步异常时，谷歌区行为完全不变。
_BAIDU_TOKEN = {"token": None}
_BAIDU_TOKEN_LOCK = threading.Lock()


def _baidu_enabled():
    if (_env("BAIDU_ENABLED") or "").lower() not in ("1", "true", "yes", "on"):
        return False
    return bool(_env("BAIDU_CLIENT_ID") and _env("BAIDU_CLIENT_SECRET")
                and _env("BAIDU_REFRESH_TOKEN"))


def _baidu_dirs():
    base = (_env("BAIDU_APP_FOLDER") or "/apps/交换站/").strip()
    if not base.startswith("/"):
        base = "/" + base
    if not base.endswith("/"):
        base += "/"
    pub = base + (_env("BAIDU_PUBLISHED_SUBDIR") or "已发布").strip("/") + "/"
    pend = base + (_env("BAIDU_PENDING_SUBDIR") or "待审核").strip("/") + "/"
    return base, pub, pend


def _priority_backend():
    """优先后端：baidu/google，缺省 baidu；非法值回退 baidu。"""
    v = (_env("PRIORITY_BACKEND") or "baidu").strip().lower()
    return v if v in ("baidu", "google") else "baidu"


def _google_fallback_enabled():
    """谷歌后备总开关，缺省开；关掉时合并视图只显示百度区。"""
    return (_env("GOOGLE_FALLBACK") or "1").strip().lower() not in ("0", "false", "no", "off")


def _use_baidu_meta():
    """元数据文件（意见/留言/公告/AI 结论/日志/账号/私人区）是否走百度。
    百度优先且百度已配置时走百度，否则走谷歌。"""
    return _priority_backend() == "baidu" and _baidu_enabled()


# ---------------- 百度刷新令牌持久化（轮换令牌问题） ----------------
# 百度刷新一次就可能轮换刷新令牌：响应里带回新的 refresh_token，旧的随即
# 作废；只靠环境变量里的副本，下次重启/刷新必然再报 sign 不匹配。
# 所以：刷新成功且响应带新令牌时，立刻把新令牌写回百度应用根目录的令牌
# 文件（只含 refresh_token 与更新时间，不含任何其他密钥）；本进程优先用已
# 知的最新值，环境变量只作初始回退。令牌文件在应用根目录，不在已发布/
# 待审核里，永远不成格子。
# 已知的边界（诚实写明）：令牌文件本身也要 access_token 才能读；若本进程
# 的 access_token 缓存已空、且环境变量里的旧令牌又已作废，那就读不出文件、
# 只能重新授权——这一步没有不经授权的解法。并发刷新被顶掉时，下面的重试
# 逻辑用还有效的 access_token 重读文件、用最新值再刷一次。
_BAIDU_TOKEN_FILE_NAME = "交换站百度令牌.txt"
_BAIDU_REFRESH = {"value": None}


def _baidu_token_file_path():
    base, _pub, _pend = _baidu_dirs()
    return base + _BAIDU_TOKEN_FILE_NAME


def _baidu_current_refresh():
    return _BAIDU_REFRESH["value"] or _env("BAIDU_REFRESH_TOKEN")


def _baidu_refresh_with(value):
    import requests as _rq
    r = _rq.post("https://openapi.baidu.com/oauth/2.0/token", data={
        "grant_type": "refresh_token",
        "refresh_token": value,
        "client_id": _env("BAIDU_CLIENT_ID"),
        "client_secret": _env("BAIDU_CLIENT_SECRET"),
    }, timeout=20)
    j = r.json() or {}
    tok = j.get("access_token") or ""
    if not tok:
        raise RuntimeError("baidu token refresh failed")
    return tok, (j.get("refresh_token") or "").strip()


def _baidu_api_with_token(access_token, path, params):
    import requests as _rq
    url = "https://pan.baidu.com/rest/2.0/xpan/" + path
    p = dict(params or {})
    p["access_token"] = access_token
    r = _rq.get(url, params=p, timeout=20)
    j = r.json() or {}
    if j.get("errno") not in (None, 0):
        raise RuntimeError("baidu api error")
    return j


def _baidu_read_token_file(access_token):
    """用已有 access_token 读令牌文件，返回文件里的 refresh_token 或 None。"""
    try:
        base, _pub, _pend = _baidu_dirs()
        j = _baidu_api_with_token(access_token, "file", {
            "method": "list", "dir": base, "order": "name", "start": 0, "limit": 100})
        target = None
        for e in j.get("list") or []:
            if (e.get("server_filename") or "") == _BAIDU_TOKEN_FILE_NAME and not e.get("isdir"):
                target = e
                break
        if not target:
            return None
        meta = _baidu_api_with_token(access_token, "multimedia", {
            "method": "filemetas", "fsids": json.dumps([int(target["fs_id"])]), "dlink": 1})
        lst = meta.get("list") or []
        if not lst or not lst[0].get("dlink"):
            return None
        import requests as _rq
        r = _rq.get(lst[0]["dlink"], params={"access_token": access_token},
                    headers={"User-Agent": "pan.baidu.com"}, timeout=30)
        data = json.loads(r.content.decode("utf-8", errors="ignore") or "{}")
        v = (data.get("refresh_token") or "").strip()
        return v or None
    except Exception:
        return None


def _baidu_write_token_file(access_token, refresh_token):
    """把新 refresh_token 写回令牌文件；失败只记日志，绝不阻塞本次请求。"""
    try:
        import requests as _rq
        body = json.dumps({"refresh_token": refresh_token,
                           "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")},
                          ensure_ascii=False)
        url = "https://pan.baidu.com/rest/2.0/xpan/file"
        params = {"method": "upload", "path": _baidu_token_file_path(),
                  "ondup": "overwrite", "access_token": access_token}
        r = _rq.post(url, params=params,
                     files={"file": (_BAIDU_TOKEN_FILE_NAME, io.BytesIO(body.encode("utf-8")))},
                     timeout=30)
        j = r.json() or {}
        if j.get("errno") not in (None, 0):
            print(f"[baidu-token] persist failed: errno {j.get('errno')}", flush=True)
    except Exception as e:
        print(f"[baidu-token] persist failed: {e}", flush=True)


def _baidu_refresh_token():
    cur = _baidu_current_refresh()
    # 刷新前预读：进程已有缓存 access_token 时，先看令牌文件里有没有更新的
    # refresh_token（另一台服务器刚轮换过），有就优先用它，避免拿旧令牌白跑一次。
    # 读不到（无缓存 token / token 已过期 / 文件不存在）就照常用当前值刷新。
    try:
        _cached = _BAIDU_TOKEN.get("token")
        if _cached:
            _stored = _baidu_read_token_file(_cached)
            if _stored and _stored != cur:
                cur = _stored
    except Exception:
        pass
    try:
        tok, new_refresh = _baidu_refresh_with(cur)
    except Exception:
        # 并发刷新被顶掉时的恢复：用内存里还有效的 access_token 重读令牌文件，
        # 拿到最新 refresh_token 再重试一次；仍失败才真正报错，绝不静默用过期令牌。
        cached = _BAIDU_TOKEN["token"]
        if cached:
            latest = _baidu_read_token_file(cached)
            if latest and latest != cur:
                tok, new_refresh = _baidu_refresh_with(latest)
                _BAIDU_REFRESH["value"] = latest
                cur = latest
            else:
                raise
        else:
            raise
    if new_refresh and new_refresh != cur:
        _BAIDU_REFRESH["value"] = new_refresh
        _baidu_write_token_file(tok, new_refresh)
    return tok


def _baidu_token():
    with _BAIDU_TOKEN_LOCK:
        if _BAIDU_TOKEN["token"]:
            return _BAIDU_TOKEN["token"]
        tok = _baidu_refresh_token()
        _BAIDU_TOKEN["token"] = tok
        return tok


def _baidu_invalidate_token():
    with _BAIDU_TOKEN_LOCK:
        _BAIDU_TOKEN["token"] = None


def _baidu_api(path, params, retry=True):
    # path: "file" (list) 或 "multimedia" (filemetas)；access_token 只放请求参数，不记日志。
    import requests as _rq
    url = "https://pan.baidu.com/rest/2.0/xpan/" + path
    p = dict(params or {})
    p["access_token"] = _baidu_token()
    r = _rq.get(url, params=p, timeout=20)
    j = r.json() or {}
    err = j.get("errno")
    if err not in (None, 0) and retry and err in (111, 110):
        _baidu_invalidate_token()
        p["access_token"] = _baidu_token()
        r = _rq.get(url, params=p, timeout=20)
        j = r.json() or {}
    if j.get("errno") not in (None, 0):
        raise RuntimeError("baidu api error")
    return j


def _baidu_api_post(path, params, data):
    """百度写接口（filemanager/create/precreate）：POST，令牌过期重试一次。"""
    import requests as _rq
    url = "https://pan.baidu.com/rest/2.0/xpan/" + path
    for attempt in range(2):
        p = dict(params or {})
        p["access_token"] = _baidu_token()
        r = _rq.post(url, params=p, data=data, timeout=30)
        j = r.json() or {}
        err = j.get("errno")
        if err in (111, 110) and attempt == 0:
            _baidu_invalidate_token()
            continue
        if err not in (None, 0):
            raise RuntimeError("baidu api error")
        return j
    raise RuntimeError("baidu api error")


def _baidu_list_dir(dir_path):
    out = []
    start = 0
    while True:
        j = _baidu_api("file", {"method": "list", "dir": dir_path,
                                "order": "name", "start": start, "limit": 100})
        items = j.get("list") or []
        out.extend(items)
        if len(items) < 100:
            break
        start += 100
    return out


def _baidu_map_entry(e, folder_id, folder_name):
    name = e.get("server_filename") or ""
    if name.startswith("_preview_"):
        return None
    if name == _DL_COUNT_FILE or name == _BAIDU_TOKEN_FILE_NAME:
        return None
    low = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return {
        "id": "bd:" + str(e.get("fs_id")),
        "name": name,
        "size": _fmt_size(e.get("size")),
        "size_bytes": int(e.get("size") or 0),
        "ext": (name.rsplit(".", 1)[-1].upper()[:4] if "." in name else "FILE"),
        "kind": _file_kind(name, ""),
        "is_image": low in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"),
        "is_video": low in ("mp4", "mov", "mkv", "webm", "avi"),
        "is_audio": low in ("mp3", "flac", "wav", "ogg", "m4a"),
        "thumb_link": "",
        "folder_id": folder_id,
        "folder_name": folder_name,
        "source": "baidu",
    }


def _baidu_published():
    # 已发布 = 应用已发布目录根 + 一级子文件夹两层；更深层第一期不展开。
    _base, pub, _pend = _baidu_dirs()
    files = []
    folders = []
    root = _baidu_list_dir(pub)
    for e in root:
        if e.get("isdir"):
            continue
        m = _baidu_map_entry(e, pub, "")
        if m:
            files.append(m)
    for e in root:
        if not e.get("isdir"):
            continue
        sub = e.get("path") or (pub + (e.get("server_filename") or ""))
        subname = e.get("server_filename") or ""
        try:
            entries = _baidu_list_dir(sub)
        except Exception:
            entries = []
        sub_files = []
        for se in entries:
            if se.get("isdir"):
                continue
            m = _baidu_map_entry(se, sub, subname)
            if m:
                sub_files.append(m)
        folders.append({"id": sub, "name": subname, "count": len(sub_files),
                        "source": "baidu"})
        files.extend(sub_files)
    return files, folders


def _baidu_find_by_path(path):
    """按完整路径找百度文件条目，找不到返回 None。"""
    path = (path or "").rstrip("/") or "/"
    parent, _, name = path.rpartition("/")
    parent = (parent + "/") if parent else "/"
    if not name:
        return None
    try:
        entries = _baidu_list_dir(parent)
    except Exception:
        return None
    for e in entries:
        if (e.get("server_filename") or "") == name:
            return e
    return None


def _baidu_mkdir(path):
    """建百度目录（已存在算成功）；返回 True/False。"""
    path = (path or "").rstrip("/")
    if not path:
        return False
    try:
        found = _baidu_find_by_path(path)
        if found and found.get("isdir"):
            return True
        _baidu_api_post("file", {"method": "create", "path": path, "isdir": 1}, {})
        return True
    except Exception:
        try:
            found = _baidu_find_by_path(path)
            return bool(found and found.get("isdir"))
        except Exception:
            return False


def _baidu_ensure_dir(path):
    """逐级建目录（path 本身是目录路径）。"""
    path = (path or "").strip("/")
    if not path:
        return False
    cur = ""
    for part in path.split("/"):
        cur = cur + "/" + part if cur else "/" + part
        if not _baidu_mkdir(cur):
            return False
    return True


def _baidu_filemanager(opera, filelist, extra=None):
    data = {"async": 0, "filelist": json.dumps(filelist, ensure_ascii=False)}
    if extra:
        data.update(extra)
    return _baidu_api_post("file", {"method": "filemanager", "opera": opera}, data)


def _baidu_move_path(src_path, dest_dir, newname=None, ondup="newcopy"):
    """百度内移动；ondup=newcopy 避免覆盖已发布同名文件（改名后显示）。"""
    item = {"path": src_path, "dest": dest_dir}
    if newname:
        item["newname"] = newname
    return _baidu_filemanager("move", [item], {"ondup": ondup})


def _baidu_delete_path(path):
    return _baidu_filemanager("delete", [{"path": path}])


def _baidu_rename_path(path, newname):
    return _baidu_filemanager("rename", [{"path": path, "newname": newname}])


_BAIDU_UPLOAD_CHUNK = 4 * 1024 * 1024


def _baidu_upload_bytes(path, data):
    """小文件单次上传（百度 PCS upload 接口，参数待实测确认）。"""
    import requests as _rq
    tok = _baidu_token()
    url = "https://pan.baidu.com/rest/2.0/xpan/file"
    params = {"method": "upload", "path": path, "ondup": "overwrite", "access_token": tok}
    r = _rq.post(url, params=params,
                 files={"file": (path.rsplit("/", 1)[-1], io.BytesIO(data))}, timeout=120)
    j = r.json() or {}
    if j.get("errno") not in (None, 0):
        raise RuntimeError("baidu upload failed")
    return j


def _baidu_upload_stream(path, stream):
    """流式上传：先落临时文件再按大小分流；≤4MB 单次传，更大走
    precreate → superfile2 → create 三段式（4MB 分片；迁移脚本的检查点续传
    不在此函数，见设计文档第 3 节）。返回 fs_id（拿不到时返回 None）。"""
    import hashlib
    import tempfile
    tmp = tempfile.TemporaryFile()
    try:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            tmp.write(chunk)
        size = tmp.tell()
        if size == 0:
            raise RuntimeError("baidu upload empty file")
        if size <= _BAIDU_UPLOAD_CHUNK:
            tmp.seek(0)
            j = _baidu_upload_bytes(path, tmp.read())
            return j.get("fs_id")
        tmp.seek(0)
        block_list = []
        slices = []
        while True:
            data = tmp.read(_BAIDU_UPLOAD_CHUNK)
            if not data:
                break
            block_list.append(hashlib.md5(data).hexdigest())
            slices.append(len(data))
        tok = _baidu_token()
        pre = _baidu_api_post("file", {"method": "precreate", "path": path, "size": size,
                                       "isdir": 0, "autoinit": 1,
                                       "block_list": json.dumps(block_list)}, {})
        if pre.get("return_type") == 1:
            info = pre.get("info") or {}
            return info.get("fs_id")
        uploadid = pre.get("uploadid") or ""
        if not uploadid:
            raise RuntimeError("baidu precreate failed")
        import requests as _rq
        tmp.seek(0)
        for idx in range(len(slices)):
            data = tmp.read(_BAIDU_UPLOAD_CHUNK)
            url = "https://d.pcs.baidu.com/rest/2.0/pcs/superfile2"
            params = {"method": "upload", "path": path, "type": "tmpfile",
                      "uploadid": uploadid, "partseq": idx, "access_token": tok}
            r = _rq.post(url, params=params,
                         files={"file": (path.rsplit("/", 1)[-1], io.BytesIO(data))},
                         timeout=120)
            jj = r.json() or {}
            if jj.get("errno") not in (None, 0):
                raise RuntimeError("baidu superfile upload failed")
        created = _baidu_api_post("file", {"method": "create", "path": path, "size": size,
                                           "isdir": 0, "uploadid": uploadid,
                                           "block_list": json.dumps(block_list)}, {})
        return created.get("fs_id")
    finally:
        try:
            tmp.close()
        except Exception:
            pass


def _baidu_pending():
    """百度待审核列表（根文件，不展开子目录），条目格式与谷歌 list_folder 对齐。"""
    _base, _pub, pend = _baidu_dirs()
    out = []
    for e in _baidu_list_dir(pend):
        if e.get("isdir"):
            continue
        m = _baidu_map_entry(e, pend, "")
        if m:
            out.append(m)
    return out


def _baidu_read_text(path):
    """按路径读百度小文本文件，失败返回 None。"""
    try:
        entry = _baidu_find_by_path(path)
        if not entry or entry.get("isdir"):
            return None
        meta = _baidu_api("multimedia", {"method": "filemetas",
                                         "fsids": json.dumps([int(entry["fs_id"])]), "dlink": 1})
        lst = meta.get("list") or []
        if not lst or not lst[0].get("dlink"):
            return None
        import requests as _rq
        tok = _baidu_token()
        r = _rq.get(lst[0]["dlink"], params={"access_token": tok},
                    headers={"User-Agent": "pan.baidu.com"}, timeout=30)
        return r.content.decode("utf-8", errors="ignore")
    except Exception:
        return None


def _baidu_write_text(path, text):
    """按路径覆写百度小文本文件，成功 True。"""
    try:
        _baidu_upload_bytes(path, (text or "").encode("utf-8"))
        return True
    except Exception as e:
        print(f"[baidu] write text failed: {e}", flush=True)
        return False


def _baidu_move_pending_to_published(fs_id, name):
    """百度待审核→已发布：filemanager move；返回 True/False（不抛异常，由调用方记理由）。"""
    try:
        meta = _baidu_meta(fs_id)
        src = meta.get("path") or ""
        if _baidu_area(src) != "pending":
            return False
        _base, pub, _pend = _baidu_dirs()
        _baidu_move_path(src, pub, name)
        _invalidate_pub_cache()
        return True
    except Exception as e:
        print(f"[baidu] move pending failed: {e}", flush=True)
        return False


def _baidu_private_base():
    base, _pub, _pend = _baidu_dirs()
    return base + "私人区/"


def _baidu_private_dir(username):
    safe = (username or "").replace("/", "").replace("\\", "").strip()
    return _baidu_private_base() + safe + "/"


def _baidu_private_list(username):
    d = _baidu_private_dir(username)
    out = []
    try:
        for e in _baidu_list_dir(d):
            if e.get("isdir"):
                continue
            m = _baidu_map_entry(e, d, username)
            if m:
                out.append(m)
    except Exception:
        pass
    return out


def _baidu_private_users():
    users = []
    try:
        for e in _baidu_list_dir(_baidu_private_base()):
            if not e.get("isdir"):
                continue
            name = e.get("server_filename") or ""
            users.append({"name": name, "files": _baidu_private_list(name)})
    except Exception:
        pass
    return users


def _merge_key(f):
    return (f.get("folder_name") or "", f.get("name") or "", int(f.get("size_bytes") or 0))


def _merge_mixed(gfiles, gfolders, bfiles, bfolders):
    """合并视图：去重键 = 文件夹名 + 文件名 + 大小；百度胜出（设计 2.2 第 3 点，
    误判边界：同名同大小但内容不同时谷歌项会被隐藏，见设计文档 2.2 第 4 点；
    迁移对照表优先去重尚未接入——对照表文件由迁移步骤生成，本次先按文件名判重）。
    顺序按优先后端：优先方在前，另一方在后（去重后）。"""
    bkeys = {_merge_key(f) for f in bfiles}
    if _priority_backend() == "baidu":
        files = list(bfiles) + [f for f in gfiles if _merge_key(f) not in bkeys]
    else:
        files = [f for f in gfiles if _merge_key(f) not in bkeys] + list(bfiles)
    folders = []
    for fo in list(gfolders) + list(bfolders):
        f2 = dict(fo)
        f2["count"] = sum(1 for f in files if (f.get("folder_name") or "") == (fo.get("name") or ""))
        folders.append(f2)
    return files, folders


def _merged_pending(service):
    """合并待审核队列（谷歌 + 百度），每项带 source；失败的一方静默为空，绝不 500。"""
    g = []
    if _google_fallback_enabled():
        try:
            if service is not None:
                g = list_folder(service, PENDING_FOLDER_ID)
                for f in g:
                    f["source"] = "google"
        except Exception:
            g = []
    b = []
    try:
        if _baidu_enabled():
            b = _baidu_pending()
    except Exception:
        b = []
    bkeys = {_merge_key(f) for f in b}
    if _priority_backend() == "baidu":
        return list(b) + [f for f in g if _merge_key(f) not in bkeys]
    return [f for f in g if _merge_key(f) not in bkeys] + list(b)


def _baidu_meta(fs_id):
    j = _baidu_api("multimedia", {"method": "filemetas",
                                  "fsids": json.dumps([int(fs_id)]), "dlink": 1})
    lst = j.get("list") or []
    if not lst:
        raise RuntimeError("baidu file not found")
    return lst[0]


def _baidu_area(path):
    # 返回 published / pending / None（不在两区里，或已发布超过一级子文件夹深度）
    _base, pub, pend = _baidu_dirs()
    if path.startswith(pub):
        rest = path[len(pub):].strip("/")
        if "/" not in rest:
            return "published"
        if rest.count("/") == 1:
            return "published"
        return None
    if path.startswith(pend):
        rest = path[len(pend):].strip("/")
        if "/" not in rest:
            return "pending"
        return None
    return None


def _baidu_gate(meta):
    # 返回 True 表示放行，False 表示应返回 403/重定向（由调用方按区决定文案）。
    area = _baidu_area(meta.get("path") or "")
    if area == "published":
        code = _env("EXCHANGE_CODE")
        return "published" if _has_gate(code, "exchange_access") else "need_gate"
    if area == "pending":
        if _is_manager():
            return "pending"
        return "need_admin"
    return None


def _baidu_stream_response(meta, name, ctype, disposition_kind):
    # 服务器中转字节：dlink 带 UA 取回，Range 由本站按 _parse_range 切片；
    # 上游回 206 时按其起始偏移对齐，回 200 时从 0 跳字节。绝不 redirect dlink。
    import requests as _rq
    total = 0
    try:
        total = int(meta.get("size") or 0)
    except Exception:
        total = 0
    rng = _parse_range(request.headers.get("Range"), total)

    def gen():
        try:
            tok = _baidu_token()
            headers = {"User-Agent": "pan.baidu.com"}
            if rng is not None:
                headers["Range"] = "bytes=%d-%d" % (rng[0], rng[1])
            r = _rq.get(meta["dlink"], params={"access_token": tok},
                        headers=headers, stream=True, timeout=60)
            pos = rng[0] if (rng is not None and r.status_code == 206) else 0
            for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
                if not chunk:
                    continue
                cl = len(chunk)
                if rng is not None:
                    s, e = rng
                    if pos + cl <= s:
                        pos += cl
                        continue
                    if pos > e:
                        break
                    yield chunk[max(0, s - pos):min(cl, e - pos + 1)]
                    pos += cl
                    if pos > e:
                        break
                    continue
                yield chunk
                pos += cl
        except Exception:
            return

    headers = {
        "Content-Disposition": _disposition(disposition_kind, name),
        "Content-Type": ctype,
        "Accept-Ranges": "bytes",
    }
    if rng is not None:
        headers["Content-Length"] = str(rng[1] - rng[0] + 1)
        headers["Content-Range"] = "bytes %d-%d/%d" % (rng[0], rng[1], total)
        return Response(gen(), status=206, headers=headers)
    if total > 0:
        headers["Content-Length"] = str(total)
    return Response(gen(), headers=headers)


_BAIDU_CT = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
    "mp4": "video/mp4", "mov": "video/quicktime", "mkv": "video/x-matroska",
    "webm": "video/webm", "avi": "video/x-msvideo",
    "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
    "flac": "audio/flac", "ogg": "audio/ogg",
    "pdf": "application/pdf", "txt": "text/plain; charset=utf-8",
    "md": "text/plain; charset=utf-8",
}


def _baidu_view(file_id):
    if not _baidu_enabled():
        return "百度源暂未连接", 503
    try:
        meta = _baidu_meta(file_id[3:])
    except Exception:
        return "文件不存在或无权访问", 404
    gate = _baidu_gate(meta)
    if gate == "need_gate":
        return redirect("/?msg=请先输入访问口令")
    if gate == "need_admin":
        return "该文件尚未发布", 403
    if gate is None:
        return "文件不存在或无权访问", 404
    name = meta.get("server_filename") or file_id
    low = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    ctype = _BAIDU_CT.get(low)
    if ctype is None:
        # SVG/HTML 等可执行类型与设计一致：不 inline，改走下载（比谷歌现版更严）。
        return redirect(f"/download/{file_id}")
    return _baidu_stream_response(meta, name, ctype, "inline")


def _baidu_download(file_id):
    if not _baidu_enabled():
        return "百度源暂未连接", 503
    try:
        meta = _baidu_meta(file_id[3:])
    except Exception:
        return "文件不存在或无权访问", 404
    if _baidu_area(meta.get("path") or "") != "published":
        return "该文件尚未发布", 403
    name = meta.get("server_filename") or file_id
    low = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    ctype = _BAIDU_CT.get(low) or "application/octet-stream"
    try:
        _bd_total = int(meta.get("size") or 0)
    except Exception:
        _bd_total = 0
    if _dl_should_count(_bd_total):
        _dl_bump(file_id)
    return _baidu_stream_response(meta, name, ctype, "attachment")


def _baidu_thumb(file_id):
    if not _baidu_enabled():
        return "百度源暂未连接", 503
    try:
        meta = _baidu_meta(file_id[3:])
    except Exception:
        return "文件不存在或无权访问", 404
    gate = _baidu_gate(meta)
    if gate == "need_gate":
        return "请先输入访问口令", 403
    if gate == "need_admin":
        return "该文件尚未发布", 403
    if gate is None:
        return "文件不存在或无权访问", 404
    name = meta.get("server_filename") or file_id
    low = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if low in ("mp4", "mov", "mkv", "webm", "avi"):
        # 百度不保证视频缩略图：第一期按无缩略图处理，不伪造、不跳转直链。
        return "暂无视频缩略图", 404
    if low not in ("jpg", "jpeg", "png", "gif", "webp", "bmp"):
        return "文件不存在或无权访问", 404
    try:
        import requests as _rq
        tok = _baidu_token()
        r = _rq.get(meta["dlink"], params={"access_token": tok},
                    headers={"User-Agent": "pan.baidu.com"}, stream=True, timeout=60)
        data = bytearray()
        for chunk in r.iter_content(chunk_size=65536):
            data.extend(chunk)
            if len(data) > 30 * 1024 * 1024:
                raise RuntimeError("baidu thumb too large")
        data = bytes(data)
        if not data:
            raise RuntimeError("baidu thumb empty")
    except Exception:
        return "文件不存在或无权访问", 404
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im)
        im.thumbnail((192, 192))
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            alpha = im.convert("RGBA").split()[3]
            bg.paste(im.convert("RGBA"), mask=alpha)
            im = bg
        else:
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=45, optimize=True)
        return Response(out.getvalue(), headers={"Content-Type": "image/jpeg",
                                                 "Cache-Control": "public, max-age=86400"})
    except Exception:
        return Response(data, headers={"Content-Type": "image/jpeg",
                                       "Cache-Control": "public, max-age=86400"})


_DRIVE_LOG_BUFFER = []
_DRIVE_LOG_FILE_ID = [None]


def _flush_drive_log():
    """把缓冲的日志行追加到云盘里的 交换站访问日志.txt。
    文件放在云盘根目录（不在待审核/已发布里，熟人看不到），找不到就新建一个。
    失败时静默跳过，绝不影响上传/下载主流程；缓冲的行会留在内存里下次再试。"""
    global _DRIVE_LOG_BUFFER
    if not _DRIVE_LOG_BUFFER:
        return
    if _use_baidu_meta():
        lines = _DRIVE_LOG_BUFFER
        _DRIVE_LOG_BUFFER = []
        try:
            _base, _pub, _pend = _baidu_dirs()
            old = _baidu_read_text(_base + "交换站访问日志.txt") or ""
            if not _baidu_write_text(_base + "交换站访问日志.txt", old + "".join(lines)):
                raise RuntimeError("baidu log write failed")
        except Exception as e:
            print(f"[access] baidu log flush failed: {e}", flush=True)
            _DRIVE_LOG_BUFFER = lines + _DRIVE_LOG_BUFFER
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
    if _use_baidu_meta():
        try:
            _base, _pub, _pend = _baidu_dirs()
            text = _baidu_read_text(_base + ACCOUNTS_FILE_NAME)
            if text is None:
                return []
            out = []
            for line in text.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) >= 3:
                    out.append({"name": parts[0], "password": parts[1], "created": parts[2], "last": parts[3] if len(parts) > 3 else "", "disabled": parts[4] if len(parts) > 4 else "0"})
            return out
        except Exception:
            return []
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
    if _use_baidu_meta():
        try:
            _base, _pub, _pend = _baidu_dirs()
            body = "\n".join(f"{a['name']}\t{a['password']}\t{a['created']}\t{a.get('last','')}\t{a.get('disabled','0')}" for a in accounts)
            return _baidu_write_text(_base + ACCOUNTS_FILE_NAME, body)
        except Exception as e:
            print(f"[accounts] write failed: {e}", flush=True)
            return False
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
        accounts = _read_accounts(service) if (service or _use_baidu_meta()) else []
    except Exception:
        return False
    for a in accounts:
        if a["name"] == username:
            return a.get("disabled") != "1"
    return False


def _private_is_admin():
    # 站长与二级管理员都能管理私人文件；账号密码与权限变更另有硬拦截。
    return _is_manager()


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
    if _use_baidu_meta():
        try:
            _base, _pub, _pend = _baidu_dirs()
            text = _baidu_read_text(_base + FEEDBACK_FILE_NAME)
            if text is None:
                return []
            items = []
            for line in text.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t", 3)
                if len(parts) >= 4:
                    items.append({"id": parts[0], "time": parts[1], "status": parts[2], "text": parts[3], "author": parts[4] if len(parts) > 4 else ""})
            items.reverse()
            return items
        except Exception:
            return []
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
    if _use_baidu_meta():
        try:
            _base, _pub, _pend = _baidu_dirs()
            body = "\n".join(f"{it['id']}\t{it['time']}\t{it['status']}\t{it['text']}" for it in items_oldest_first)
            return _baidu_write_text(_base + FEEDBACK_FILE_NAME, body)
        except Exception as e:
            print(f"[feedback] write failed: {e}", flush=True)
            return False
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



COMMENTS_FILE_NAME = "交换站留言.txt"


def _comment_file_id(service):
    if _env("COMMENTS_DRIVE_FILE_ID"):
        return _env("COMMENTS_DRIVE_FILE_ID")
    try:
        res = service.files().list(q=f"name = '{COMMENTS_FILE_NAME}' and trashed = false", fields="files(id)", pageSize=5).execute()
        fl = res.get("files") or []
        return fl[0]["id"] if fl else None
    except Exception:
        return None


def _read_comments(service):
    if _use_baidu_meta():
        try:
            _base, _pub, _pend = _baidu_dirs()
            text = _baidu_read_text(_base + COMMENTS_FILE_NAME)
            if text is None:
                return []
            items = []
            for line in text.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t", 4)
                if len(parts) >= 5:
                    items.append({"id": parts[0], "time": parts[1], "file_id": parts[2], "author": parts[3], "text": parts[4]})
            items.reverse()
            return items
        except Exception:
            return []
    """Return all comments newest first: {id, time, file_id, author, text}.
    存储复用意见区思路：云盘根目录一个 txt，tab 分隔行，磁盘上最旧在前。
    根目录文件绝不会出现在已发布文件夹列表里，所以留言文件本身不成格子。"""
    if service is None:
        return []
    try:
        fid = _comment_file_id(service)
        if not fid:
            return []
        from googleapiclient.http import MediaIoBaseDownload
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=fid))
        done = False
        while not done:
            _, done = dl.next_chunk()
        items = []
        for line in buf.getvalue().decode("utf-8", errors="ignore").strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t", 4)
            if len(parts) >= 5:
                items.append({"id": parts[0], "time": parts[1], "file_id": parts[2], "author": parts[3], "text": parts[4]})
        items.reverse()
        return items
    except Exception:
        return []


def _write_comments(service, items_oldest_first):
    """Overwrite the Drive comments file with items in oldest-first order."""
    if _use_baidu_meta():
        try:
            _base, _pub, _pend = _baidu_dirs()
            body = "\n".join(f"{it['id']}\t{it['time']}\t{it['file_id']}\t{it['author']}\t{it['text']}" for it in items_oldest_first)
            return _baidu_write_text(_base + COMMENTS_FILE_NAME, body)
        except Exception as e:
            print(f"[comments] write failed: {e}", flush=True)
            return False
    from googleapiclient.http import MediaIoBaseUpload
    body = "\n".join(f"{it['id']}\t{it['time']}\t{it['file_id']}\t{it['author']}\t{it['text']}" for it in items_oldest_first)
    media = MediaIoBaseUpload(io.BytesIO(body.encode("utf-8")), mimetype="text/plain", resumable=False)
    try:
        fid = _comment_file_id(service)
        if fid:
            service.files().update(fileId=fid, media_body=media).execute()
        else:
            meta = {"name": COMMENTS_FILE_NAME, "mimeType": "text/plain"}
            service.files().create(body=meta, media_body=media, fields="id").execute()
        return True
    except Exception as e:
        print(f"[comments] write failed: {e}", flush=True)
        return False


def _announcement_file_id(service):
    if _env("ANNOUNCEMENT_DRIVE_FILE_ID"):
        return _env("ANNOUNCEMENT_DRIVE_FILE_ID")
    try:
        res = service.files().list(q=f"name = '{ANNOUNCEMENT_FILE_NAME}' and trashed = false", fields="files(id)", pageSize=5).execute()
        fl = res.get("files") or []
        return fl[0]["id"] if fl else None
    except Exception:
        return None


def _read_announcement_raw(service):
    """读云盘公告文件，返回 {"title":..., "body":...}；文件不存在/读不出/JSON 坏了返回 None。"""
    if _use_baidu_meta():
        try:
            _base, _pub, _pend = _baidu_dirs()
            text = _baidu_read_text(_base + ANNOUNCEMENT_FILE_NAME)
            if text is None:
                return None
            data = json.loads(text)
            if not isinstance(data, dict):
                return None
            return {"title": data.get("title") or "", "body": data.get("body") or ""}
        except Exception:
            return None
    if service is None:
        return None
    try:
        fid = _announcement_file_id(service)
        if not fid:
            return None
        from googleapiclient.http import MediaIoBaseDownload
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=fid))
        done = False
        while not done:
            _, done = dl.next_chunk()
        data = json.loads(buf.getvalue().decode("utf-8", errors="ignore"))
        if not isinstance(data, dict):
            return None
        return {"title": data.get("title") or "", "body": data.get("body") or ""}
    except Exception:
        return None


def _announcement_display(service):
    """进站要弹的公告：文件缺失/坏了 → 默认文案；正文为空 → None（不弹，等于关掉）。"""
    raw = _read_announcement_raw(service)
    if raw is None:
        return {"title": ANNOUNCEMENT_DEFAULT_TITLE, "body": ANNOUNCEMENT_DEFAULT_BODY}
    if not raw["body"].strip():
        return None
    return {"title": raw["title"].strip() or ANNOUNCEMENT_DEFAULT_TITLE, "body": raw["body"]}


def _write_announcement(service, title, body):
    """把公告 JSON 写回云盘根目录文件（有则覆盖、无则新建），成功返回 True。"""
    if _use_baidu_meta():
        try:
            _base, _pub, _pend = _baidu_dirs()
            payload = json.dumps({"title": title, "body": body}, ensure_ascii=False)
            return _baidu_write_text(_base + ANNOUNCEMENT_FILE_NAME, payload)
        except Exception as e:
            print(f"[announcement] write failed: {e}", flush=True)
            return False
    from googleapiclient.http import MediaIoBaseUpload
    payload = json.dumps({"title": title, "body": body}, ensure_ascii=False)
    media = MediaIoBaseUpload(io.BytesIO(payload.encode("utf-8")), mimetype="application/json", resumable=False)
    try:
        fid = _announcement_file_id(service)
        if fid:
            service.files().update(fileId=fid, media_body=media).execute()
        else:
            meta = {"name": ANNOUNCEMENT_FILE_NAME, "mimeType": "application/json"}
            service.files().create(body=meta, media_body=media, fields="id").execute()
        return True
    except Exception as e:
        print(f"[announcement] write failed: {e}", flush=True)
        return False


def _comments_for(service, file_id):
    """Comments for one file, oldest first (display order)."""
    return [c for c in reversed(_read_comments(service)) if c["file_id"] == file_id]


def _published_file_exists(service, file_id):
    """True if file_id is in the published tree (Google or Baidu).
    文件被删除后它就不在这里，留言随之失效/隐藏，不报错。"""
    try:
        files, _folders = _cached_published(service)
        return any(f.get("id") == file_id for f in files)
    except Exception:
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
    if len(_DRIVE_LOG_BUFFER) >= 20 or any(action.startswith(a) for a in ("审核通过", "审核不通过", "删除已发布", "改名", "AI 自动发布")):
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
        if (f.get("mimeType") or "") == "application/vnd.google-apps.folder":
            continue
        if (f.get("name") or "") == _DL_COUNT_FILE:
            continue
        if folder_id == PUBLISHED_FOLDER_ID and (f.get("name") or "").startswith("_preview_"):
            continue
        out.append(
            {
                "id": f["id"],
                "name": f["name"],
                "size": _fmt_size(f.get("size")),
                "size_bytes": int(f.get("size") or 0),
                "ext": (f["name"].rsplit(".", 1)[-1].upper()[:4] if "." in f["name"] else "FILE"),
                "kind": _file_kind(f["name"], f.get("mimeType") or ""),
                "is_image": (f.get("mimeType") or "").startswith("image/") or (f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else "") in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"),
                "is_video": (f.get("mimeType") or "").startswith("video/") or (f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else "") in ("mp4", "mov", "mkv", "webm", "avi"),
                "is_audio": (f.get("mimeType") or "").startswith("audio/") or (f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else "") in ("mp3", "flac", "wav", "ogg", "m4a"),
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
  /* 默认浅色主题，可手动切换深色；选择存在浏览器本地，不跟随系统深色，避免对比度变差 */
  [data-theme="dark"]{
    --bg:#121714;
    --surface:#1c231e;
    --surface-2:#232e27;
    --ink:#e8ede9;
    --muted:#9aa89e;
    --line:#2e3a32;
    --green:#2f9d74;
    --green-ink:#7ed6ae;
    --green-soft:#1e332a;
    --amber:#e0b84f;
    --amber-soft:#3a2f12;
    --amber-line:#5a4a1e;
    --red:#ff7a6e;
    --red-soft:#3d1f1c;
    --shadow:0 8px 28px rgba(0,0,0,.35);
  }
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
  .chip.bad{background:var(--red-soft);color:var(--red)}
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
  /* 站长区 PJAX 淡入淡出 + 公告弹窗动画（只用 transition，无外部库） */
  #admin-main{transition:opacity .22s ease}
  #admin-main.pjax-out{opacity:0}
  #admin-main.pjax-in{animation:pjaxFadeIn .25s ease}
  @keyframes pjaxFadeIn{from{opacity:0}to{opacity:1}}
  .ann-overlay{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:50;display:flex;align-items:center;justify-content:center;padding:20px;box-sizing:border-box;opacity:0;transition:opacity .25s ease}
  .ann-overlay.ann-show{opacity:1}
  .ann-card{background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:18px;padding:20px;width:100%;max-width:420px;max-height:85vh;overflow:auto;box-shadow:var(--shadow);opacity:0;transform:scale(.96);transition:opacity .25s ease,transform .25s ease}
  .ann-overlay.ann-show .ann-card{opacity:1;transform:scale(1)}
  .ann-card h2{margin:0 0 10px;font-size:18px;word-break:break-all}
  .ann-card p{white-space:pre-wrap;word-break:break-all;line-height:1.7;margin:0 0 16px}
  @media (prefers-reduced-motion:reduce){ #admin-main{transition:none} #admin-main.pjax-in{animation:none} .ann-overlay,.ann-card{transition:none} }
 """

# 站长区 PJAX：拦截站长区内部链接（/admin 与 /admin/*，退出与回首页除外），
# fetch 取页、只换 #admin-main 内容并推 history；前进/后退同样换内容不刷新。
# 表单提交一律走整页跳转；取页失败、目标无容器时回退整页跳转。
# 换内容带淡入淡出（先淡出约 160ms 再换内容淡入 250ms，不闪白屏）；
# prefers-reduced-motion 时直接换内容、无动画。
ADMIN_PJAX_SCRIPT = """<script>(function(){var main=document.getElementById('admin-main');if(!main)return;var reduced=!!(window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches);function isAdminLink(a){if(!a||!a.href||a.target==='_blank')return false;var u;try{u=new URL(a.href,location.href);}catch(e){return false;}if(u.origin!==location.origin)return false;if(u.pathname==='/logout'||u.pathname==='/')return false;return u.pathname==='/admin'||u.pathname.indexOf('/admin/')===0;}function load(url,push){var t0=Date.now();function swap(html){var doc=new DOMParser().parseFromString(html,'text/html');var nm=doc.getElementById('admin-main');if(!nm){location.href=url;return;}function doSwap(){main.innerHTML=nm.innerHTML;if(doc.title)document.title=doc.title;window.scrollTo(0,0);main.classList.remove('pjax-out');if(!reduced){main.classList.add('pjax-in');setTimeout(function(){main.classList.remove('pjax-in');},300);}}if(reduced){doSwap();return;}main.classList.add('pjax-out');setTimeout(doSwap,Math.max(0,160-(Date.now()-t0)));}fetch(url,{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}}).then(function(r){if(!r.ok)throw new Error('bad');return r.text();}).then(function(html){if(push){try{history.pushState(null,'',url);}catch(e){}}swap(html);}).catch(function(){location.href=url;});}document.addEventListener('click',function(e){if(e.metaKey||e.ctrlKey||e.shiftKey||e.altKey||e.button!==0)return;var a=e.target.closest?e.target.closest('a'):null;if(!a||!isAdminLink(a))return;e.preventDefault();load(a.href,true);});document.addEventListener('submit',function(e){
var f=e.target;if(!f||!f.action)return;var u;try{u=new URL(f.action,location.href);}catch(err){return;}
if(u.origin!==location.origin)return;
if(u.pathname!=='/admin/approve'&&u.pathname!=='/admin/reject')return;
e.preventDefault();
var row=f.closest?f.closest('.file'):null;
var btn=f.querySelector('button');if(btn)btn.disabled=true;
function tipBox(){var t=document.getElementById('ajax-tip');if(!t){t=document.createElement('p');t.id='ajax-tip';t.className='note';var m=document.getElementById('admin-main');if(m)m.insertBefore(t,m.firstChild);}return t;}
fetch(f.action,{method:'POST',credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'},body:new FormData(f)})
.then(function(r){if(!r.ok)return r.text().then(function(t){var err=new Error((t||'').replace(/<[^>]*>/g,'').slice(0,120)||('操作失败 '+r.status));err.isHttp=true;throw err;});return r.text();})
.then(function(){
tipBox().textContent=u.pathname==='/admin/approve'?'已通过，已移到已发布':'已不通过，已移到回收站';
if(!row)return;
var m=document.getElementById('admin-main');
function after(){if(row&&row.parentNode)row.remove();if(m&&!m.querySelector('.file')){var p=document.createElement('p');p.className='empty';p.textContent='目前没有待审核的文件。';m.appendChild(p);}}
if(reduced){after();return;}
row.style.transition='opacity .25s ease';row.style.opacity='0';setTimeout(after,280);
})
.catch(function(err){
if(err&&err.name==='TypeError'){f.submit();return;}
if(btn)btn.disabled=false;
tipBox().textContent='操作失败：'+(err&&err.message?err.message:'未知原因')+'，该行已保留，可重试。';
});
});
window.addEventListener('popstate',function(){load(location.href,false);});})();</script>"""

PAGE = (
    "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>交换站</title><style>"
    + BASE_CSS
    + "</style><script>try{var _t=localStorage.getItem('theme');if(_t==='dark'){document.documentElement.setAttribute('data-theme','dark');}}catch(e){}</script><style>" + '\n.shellhead{position:sticky;top:0;z-index:30;background:var(--bg);height:56px;display:flex;align-items:center;gap:12px;padding:6px 0;border-bottom:1px solid var(--line)}\n.jiabtn{width:44px;height:44px;border-radius:12px;background:var(--green);color:#fff;font-weight:900;font-size:20px;border:0;cursor:pointer;flex-shrink:0}\n.headtitle{flex:1;min-width:0;display:flex;align-items:baseline;gap:8px;white-space:nowrap;overflow:hidden}\n.headtitle h1{font-size:24px}\n#curtab{color:var(--muted);font-size:13px;font-weight:700}\n.drawer-backdrop{position:fixed;top:56px;bottom:0;left:0;right:0;background:rgba(0,0,0,.35);z-index:15;opacity:0;transition:opacity .32s ease}.drawer-backdrop.open{opacity:1}\n.drawer{position:fixed;top:56px;bottom:0;left:0;width:260px;max-width:84vw;background:var(--surface);border-right:1px solid var(--line);z-index:20;display:flex;flex-direction:column;padding:10px 14px 12px;box-sizing:border-box;overflow:hidden;transform:translateX(-105%);transition:transform .32s cubic-bezier(.2,.8,.2,1);will-change:transform}.drawer.open{transform:translateX(0)}\n.drawer-scroll{flex:1;min-height:0;overflow-y:auto}\n.drawer-label{font-size:11px;font-weight:800;color:var(--muted);letter-spacing:.06em;padding:6px 8px}\n.drawer-navbtn{display:block;width:100%;text-align:left;border:0;border-radius:10px;padding:9px 12px;font-weight:800;font-size:14px;cursor:pointer;margin-bottom:3px;background:transparent;color:var(--ink)}\n.drawer-navbtn.active{background:var(--green);color:#fff}\n.drawer-quota{flex-shrink:0;padding-top:10px;border-top:1px solid var(--line);margin-top:8px}\n.quota-bar{background:var(--surface-2);border-radius:999px;overflow:hidden;height:8px;margin-top:8px}\n.quota-fill{height:100%;background:var(--green)}\n.pub-toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}\n.folderbar{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}\n.album-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px}\n.tile{position:relative;aspect-ratio:1/1;border-radius:14px;overflow:hidden;cursor:pointer;background:var(--surface-2);border:1px solid var(--line)}\n.tileimg{width:100%;height:100%;object-fit:cover;display:block}\n.tilefic{width:100%;height:100%;display:grid;place-items:center;font-weight:900;color:var(--green-ink);font-size:20px}\n.tilefic{font-size:44px}\n.tile-doc{background:#fdf0c8;color:#7a5b00}\n.tile-music{background:#dcefe6;color:#0f4a33}\n.tile-pkg{background:#e4e0f5;color:#3d348b}\n.tile-video{background:#dbeafe;color:#1e40af}\n.tile-zip{background:#ffe4d6;color:#7c2d12}\n.tile-other{background:#e7ebee;color:#334155}\n.tile-img{background:#e7ebee;color:#334155}\n[data-theme="dark"] .tile-doc{background:#4a3d12;color:#f5d97e}\n[data-theme="dark"] .tile-music{background:#1e332a;color:#7ed6ae}\n[data-theme="dark"] .tile-pkg{background:#2e2a4d;color:#c4b5fd}\n[data-theme="dark"] .tile-video{background:#1e3a5f;color:#93c5fd}\n[data-theme="dark"] .tile-zip{background:#4a2a1a;color:#fdba74}\n[data-theme="dark"] .tile-other,[data-theme="dark"] .tile-img{background:#232e27;color:#cbd5ce}\n.tileinfo{position:absolute;left:0;right:0;bottom:0;padding:24px 46px 7px 8px;background:linear-gradient(transparent,rgba(0,0,0,.65));color:#fff}\n.tile-name{font-size:12px;font-weight:800;word-break:break-all;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}\n.tile-meta{font-size:11px;opacity:.85}\n.tile-check{position:absolute;top:6px;right:6px;width:22px;height:22px;border-radius:50%;border:2px solid #fff;background:rgba(0,0,0,.35);display:none;place-items:center;color:#fff;font-size:14px;z-index:2}\n.tile-dl{position:absolute;right:6px;bottom:6px;z-index:3;pointer-events:none;font-size:10px;font-weight:800;color:#fff;background:rgba(0,0,0,.55);border-radius:999px;padding:2px 7px;white-space:nowrap}\n.album-grid.selecting .tile-check{display:grid}\n.tile.selected .tile-check{background:var(--green)}\n.tile.selected .tile-check::after{content:"\\2713"}\n#previewOverlay{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:40;display:none;align-items:center;justify-content:center;padding:16px;box-sizing:border-box}\n#previewCard{background:var(--surface);border-radius:18px;padding:16px;width:100%;max-width:480px;max-height:90vh;overflow:auto}\n' + "</style></head><body><div class=\"wrap\">"
    '<header class="shellhead"><button id="drawerBtn" class="jiabtn" type="button">交</button><div class="headtitle"><h1>交换站</h1><span id="curtab">/ 已发布区</span></div><div class="badge-live"><span class="dot"></span> 仅熟人使用 · 需审核</div></header><div id="drawerBackdrop" class="drawer-backdrop" style="display:none"></div><aside id="drawer" class="drawer" style="display:none"><div class="drawer-scroll"><div class="drawer-label">导航</div><nav id="drawernav"><button class="drawer-navbtn" data-tab="principle">原理区</button><button class="drawer-navbtn" data-tab="publish">发布区</button><button class="drawer-navbtn active" data-tab="published">已发布区</button><button class="drawer-navbtn" data-tab="feedback">意见区</button><button class="drawer-navbtn" data-tab="private">私人区</button><button class="drawer-navbtn" data-tab="admin">站长区</button></nav><div class="drawer-label">分类</div><div id="drawerCats">{% for c in cats %}{% if c.count %}<button class="drawer-navbtn" data-cat="{{ c.name }}">{{ c.name }}（{{ c.count }}）</button>{% endif %}{% endfor %}</div><div class="drawer-label">外观</div><button id="themeBtn" class="drawer-navbtn" type="button">🌙 深色模式</button></div><div class="drawer-quota"><div style="font-weight:800;font-size:13px">私人空间</div>{% if fb_user %}<div class="quota-bar"><div class="quota-fill" style="width:{{ private_used_pct }}%"></div></div><div style="font-size:12px;color:var(--muted);margin-top:6px">{{ private_used_text }} / 20 GB · 还剩 {{ private_remaining_text }}</div>{% else %}<div style="font-size:12px;color:var(--muted);margin-top:6px">登录私人区账号后，这里显示你的用量（每人上限 20 GB）。</div>{% endif %}</div></aside>'
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
    "window.__uploading=false;window.addEventListener('beforeunload',function(e){if(window.__uploading){e.preventDefault();e.returnValue='正在上传，离开本页会中断上传';}});document.querySelectorAll('#topnav a').forEach(function(a){a.addEventListener('click',function(e){if(window.__uploading&&a.dataset.tab==='admin'){if(!confirm('正在上传，点站长区进入审核页会中断上传。确定要中断吗？')){e.preventDefault();e.stopImmediatePropagation();}}});});pf.addEventListener('submit',async function(e){e.preventDefault();window.__uploading=true;var us=document.getElementById('uploadstatus');if(us){us.textContent='已点上传，正在向云盘申请直传通道…（如果这行一直不动，说明页面是旧版，请刷新一次再传）';}var b=document.getElementById('uploadbtn');var w=document.getElementById('progwrap');var tx=document.getElementById('progtext');if(!queue.length){if(b){b.disabled=false;}window.__uploading=false;return;}if(b){b.disabled=true;}if(w){w.style.display='block';}__up.done=0;__up.total=queue.reduce(function(a,f){return a+f.size;},0);__up.start=Date.now();__up.lastT=Date.now();try { {% if is_relay %}throw new Error('__relay__');{% else %}for(var k=0;k<queue.length;k++){await directOne(queue[k],k+1,queue.length);}{% endif %}window.__uploading=false;var __n=queue.length;queue.length=0;try{syncInput();show();}catch(e){}if(w){w.style.display='none';}if(tx){tx.textContent='上传完成';}if(us){us.textContent='已上传 '+__n+' 个文件到待审核，站长通过后才会出现在已发布列表';}}catch(err){if(tx){tx.textContent=(err.message==='__relay__'?'正在经本站中转上传（手机→上海→海外→云盘），进度跟着国内这一段走…':'直传失败，正在改用本站中转上传: '+err.message);}try{var fd2=new FormData();queue.forEach(function(f){fd2.append('file',f);});var x2=new XMLHttpRequest();x2.open('POST','/upload',true);x2.setRequestHeader('X-Requested-With','XMLHttpRequest');var bar2=document.getElementById('progbar');x2.upload.onprogress=function(ev){if(ev.lengthComputable){var pct=Math.round(ev.loaded/ev.total*100);if(bar2){bar2.style.width=pct+'%';}if(tx){tx.textContent='中转上传: '+pct+'% ('+(ev.loaded/1048576).toFixed(1)+' / '+(ev.total/1048576).toFixed(1)+' MB)';}}};x2.onload=function(){try{var r=JSON.parse(x2.responseText);if(r.ok){window.__uploading=false;var __n2=queue.length;queue.length=0;try{syncInput();show();}catch(e){}if(w){w.style.display='none';}if(tx){tx.textContent='上传完成';}if(us){us.textContent=r.msg||'已上传到待审核，站长通过后才会出现在已发布列表';}}else{window.__uploading=false;if(tx){tx.textContent='上传失败：'+(r.msg||'未知原因');}if(us){us.textContent='上传失败：'+(r.msg||'未知原因');}if(b){b.disabled=false;}}}catch(e2){window.__uploading=false;if(tx){tx.textContent='上传失败：服务器返回异常，请重试';}if(us){us.textContent='上传失败：服务器返回异常，请重试';}if(b){b.disabled=false;}}};x2.onerror=function(){if(tx){tx.textContent='上传失败，请检查网络后重试';}if(us){us.textContent='上传失败，请检查网络后重试';}if(b){b.disabled=false;}window.__uploading=false;};x2.send(fd2);}catch(e3){window.__uploading=false;if(tx){tx.textContent='上传失败：'+err.message+'，请检查网络后重试';}if(us){us.textContent='上传失败：'+err.message+'，请检查网络后重试';}if(b){b.disabled=false;}}}});</script>"
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
    '<div class="tabpanel active" id="tab-published"><div class="pub-toolbar"><input class="search" id="search" type="search" placeholder="搜索文件名…" oninput="onSearch(this.value)"><button class="btn" id="openDrawerBtn" type="button">导航</button><button class="btn" id="selBtn" type="button">选择</button></div><section class="card section"><div class="section-head"><h2>已发布</h2><span class="count">{{ files|length }} 个</span></div>{% if msg %}<p class="note" style="margin-top:10px">{{ msg }}</p>{% endif %}{% if backend_notice %}<p class=\"note\" style=\"margin-top:10px\">{{ backend_notice }}</p>{% endif %}<div class="folderbar" id="folderbar"><button class="tab active" data-folder="all" onclick="showFolder(\'all\',this)">全部（{{ files|length }}）</button>{% for fo in folders %}<button class="tab" data-folder="{{ fo.id }}" onclick="showFolder(\'{{ fo.id }}\',this)">{{ fo.name }}（{{ fo.count }}）</button>{% endfor %}<button class="tab" id="newFolderBtn" type="button">+ 新建文件夹</button></div><form id="newFolderForm" method="post" action="/folder/create" style="display:none;gap:8px;margin-top:10px"><input class="textin" name="name" placeholder="新文件夹名字，比如：旅行" required maxlength="50" style="flex:1"><button class="btn primary" type="submit">创建</button><button class="btn" type="button" id="newFolderCancel">取消</button></form><div class="tabs" id="cattabs"><button class="tab active" onclick="showCat(\'全部\',this)">全部（{{ files|length }}）</button>{% for c in cats %}{% if c.count %}<button class="tab" onclick="showCat(\'{{ c.name }}\',this)">{{ c.name }}（{{ c.count }}）</button>{% endif %}{% endfor %}</div><form id="moveForm" method="post" action="/folder/move" style="display:none;margin-top:12px;margin-bottom:4px;gap:8px;flex-wrap:wrap;align-items:center"><span id="selCount" style="font-weight:800"></span><input type="hidden" name="dest_folder_id" id="moveDest"><input type="hidden" name="file_ids" id="moveFileIds"><button class="btn primary" type="button" id="moveBtn">移动</button><button class="btn" type="button" id="dlSelBtn">选择下载</button></form><div class="album-grid" id="albumGrid">{% for f in files %}<div class="tile" data-cat="{{ f.kind }}" data-folder="{{ f.folder_id }}" data-name="{{ f.name|lower }}" data-id="{{ f.id }}" data-dl="/download/{{ f.id }}" data-view="/view/{{ f.id }}" data-isimg="{{ \'1\' if f.is_image else \'0\' }}" data-isvid="{{ \'1\' if f.is_video else \'0\' }}" data-isaud="{{ \'1\' if f.is_audio else \'0\' }}" data-size="{{ f.size_bytes }}"><span class="tile-check"></span>{% if f.is_image or f.is_video %}<img class="tileimg" src="/thumb/{{ f.id }}" data-fb="/thumb/{{ f.id }}" alt="" loading="lazy" onerror="_thumbErr(this)">{% if f.is_video %}<div class="tilefic tile-video" style="display:none">影</div>{% else %}<div class="tilefic tile-img" style="display:none">图</div>{% endif %}{% elif f.is_audio %}<div class="tilefic tile-music">音</div>{% elif f.kind == "文档" %}<div class="tilefic tile-doc">文</div>{% elif f.kind == "压缩包" %}<div class="tilefic tile-zip">压</div>{% elif f.kind == "安装包" %}<div class="tilefic tile-pkg">包</div>{% elif f.kind == "影音" %}<div class="tilefic tile-video">影</div>{% else %}<div class="tilefic tile-other">他</div>{% endif %}<div class="tileinfo"><div class="tile-name" style="display:none">{{ f.name }}</div><div class="tile-meta">{{ f.kind }} · {{ f.size }}{% if f.source == "baidu" %} · 百度{% endif %}{% if f.ai_note %} · {{ f.ai_note }}{% endif %}</div></div><span class="tile-dl">↓ {{ f.dl_count|default(0) }}</span></div>{% endfor %}</div><p class="empty" id="folderEmpty" style="display:none">这个文件夹里还没有文件。</p>{% if not files %}<p class="empty">目前还没有已发布的文件。</p>{% endif %}<p class="note">点图片默认就是预览；点右上角“选择”，再点图片打勾，就可以批量下载或移动到文件夹。</p></section><div id="previewOverlay"><div id="previewCard"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><b id="previewTitle" style="word-break:break-all"></b><button class="btn" id="previewClose" type="button">关闭</button></div><div id="previewBody" style="margin-top:12px"></div></div></div><div id="moveOverlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:40;align-items:center;justify-content:center;padding:16px;box-sizing:border-box"><div style="background:var(--surface);border-radius:18px;padding:16px;width:100%;max-width:420px;max-height:90vh;overflow:auto"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><b>移到哪里？</b><button class="btn" type="button" id="moveCancel">取消</button></div><p class="note" style="margin-top:8px">点一个文件夹，选中的文件就会移过去。</p><button class="btn" type="button" data-dest="{{ published_id }}" style="display:block;width:100%;text-align:left;margin-bottom:8px">已发布根目录</button>{% for fo in folders %}<button class="btn" type="button" data-dest="{{ fo.id }}" style="display:block;width:100%;text-align:left;margin-bottom:8px">{{ fo.name }}（{{ fo.count }}）</button>{% endfor %}</div></div><script>var curCat=\'全部\',curFolder=\'all\',curQ=\'\',selectMode=false,selected=new Set();function applyFilter(){var vis=0;document.querySelectorAll(\'#albumGrid .tile\').forEach(function(t){var okCat=curCat===\'全部\'||t.dataset.cat===curCat;var okFolder=curFolder===\'all\'||t.dataset.folder===curFolder;var okQ=t.dataset.name.includes(curQ);var show=(okCat&&okFolder&&okQ);var _nm=t.querySelector(\'.tile-name\');if(_nm)_nm.style.display=(show&&curQ)?\'\':\'none\';t.style.display=show?\'\':\'none\';if(show)vis++;});var fe=document.getElementById(\'folderEmpty\');if(fe)fe.style.display=(curFolder!==\'all\'&&vis===0)?\'block\':\'none\';}function showCat(c,el){curCat=c;document.querySelectorAll(\'#cattabs .tab\').forEach(function(t){t.classList.remove(\'active\');});el.classList.add(\'active\');applyFilter();}function showFolder(fid,el){curFolder=fid;document.querySelectorAll(\'#folderbar .tab\').forEach(function(t){t.classList.remove(\'active\');});el.classList.add(\'active\');applyFilter();}function onSearch(v){curQ=v.toLowerCase();applyFilter();}function updateSel(){var mf=document.getElementById(\'moveForm\');var sc=document.getElementById(\'selCount\');var ids=document.getElementById(\'moveFileIds\');if(sc)sc.textContent=\'已选 \'+selected.size+\' 个 · 选择模式中，点图片是打勾，点右上角“取消选择”退出后才能查看\';if(ids)ids.value=Array.from(selected).join(\',\');if(mf)mf.style.display=selected.size?\'flex\':\'none\';}function _thumbErr(im){if(!im.dataset.fbDone&&im.dataset.fb){im.dataset.fbDone=\'1\';im.src=im.dataset.fb;return;}im.style.display=\'none\';var nx=im.nextElementSibling;if(nx)nx.style.display=\'grid\';}function openPreview(t){var ov=document.getElementById(\'previewOverlay\');var ti=document.getElementById(\'previewTitle\');var bd=document.getElementById(\'previewBody\');if(!ov||!ti||!bd)return;ti.textContent=t.querySelector(\'.tile-name\').textContent;var view=t.dataset.view,dl=t.dataset.dl;if(t.dataset.isimg===\'1\'){var _th=t.querySelector(\'.tileimg\');var _tu=_th?_th.getAttribute(\'src\'):\'\';bd.innerHTML=\'<img id="pvThumb" src="\'+_tu+\'" style="width:100%;border-radius:12px"><img id="pvOrig" src="\'+view+\'" style="width:100%;border-radius:12px;display:none"><p class="note" id="previewErr" style="display:none">图片预览加载失败，可能是原图太大或网络中断，点下面下载后本地查看。</p><a class="btn primary segdl" data-dl="\'+dl+\'" data-size="\'+(t.dataset.size||\'0\')+\'" href="\'+dl+\'">下载原图</a>\';var _im=bd.querySelector(\'img\');if(_im){_im.onerror=function(){_im.style.display=\'none\';var e=document.getElementById(\'previewErr\');if(e)e.style.display=\'block\';};}var _og=bd.querySelector(\'#pvOrig\');var _tb=bd.querySelector(\'#pvThumb\');if(_og){var _ogLoaded=false;var _ogTimer=null;var _ogRetry=null;function _ogShowRetry(){if(_ogRetry){_ogRetry.style.display=\'\';return;}_ogRetry=document.createElement(\'div\');_ogRetry.id=\'pvOrigRetry\';var _rb=document.createElement(\'button\');_rb.className=\'btn primary\';_rb.type=\'button\';_rb.textContent=\'原图加载较慢或加载失败，点我重试原图\';_rb.onclick=function(){_ogLoaded=false;if(_ogRetry)_ogRetry.style.display=\'none\';var _pe=document.getElementById(\'previewErr\');if(_tb&&(!_pe||_pe.style.display!==\'block\'))_tb.style.display=\'\';_og.style.display=\'none\';_ogStartTimer();_og.src=view+(view.indexOf(\'?\')>=0?\'&\':\'?\')+\'retry=\'+(new Date().getTime());};var _bd2=document.getElementById(\'previewBody\');if(_bd2)_bd2.appendChild(_ogRetry);_ogRetry.appendChild(_rb);}function _ogStartTimer(){if(_ogTimer)clearTimeout(_ogTimer);_ogTimer=setTimeout(function(){if(!_ogLoaded)_ogShowRetry();},15000);}_og.onload=function(){_ogLoaded=true;if(_ogTimer)clearTimeout(_ogTimer);if(_ogRetry)_ogRetry.style.display=\'none\';if(_tb)_tb.style.display=\'none\';_og.style.display=\'block\';};_og.onerror=function(){if(_ogTimer)clearTimeout(_ogTimer);_ogShowRetry();};_ogStartTimer();}}else if(t.dataset.isvid===\'1\'){bd.innerHTML=\'<video id="pvVideo" controls preload="metadata" src="\'+view+\'" style="width:100%;border-radius:12px"></video><div style="display:none;gap:8px;margin:10px 0"><button class="btn" id="qLowBtn" type="button">流畅</button><button class="btn" id="qHighBtn" type="button">高清</button></div><div id="qNote" class="note"></div><p class="note">大视频在线播需要先缓冲一会儿；如果一直转圈，点下面下载后本地播放更快。</p><a class="btn primary segdl" data-dl="\'+dl+\'" data-size="\'+(t.dataset.size||\'0\')+\'" href="\'+dl+\'">下载视频</a>\';var _vid=bd.querySelector(\'#pvVideo\');var _ql=bd.querySelector(\'#qLowBtn\');var _qh=bd.querySelector(\'#qHighBtn\');var _qn=bd.querySelector(\'#qNote\');var _qrow=_ql?_ql.parentElement:null;function _setQ(mode,play){if(!_vid)return;if(mode===\'low\'){_vid.src=\'/preview/\'+t.dataset.id;}else{_vid.src=view;}_vid.load();if(play){try{_vid.play();}catch(e){}}if(_ql){_ql.style.background=mode===\'low\'?\'var(--green)\':\'\';_ql.style.color=mode===\'low\'?\'#fff\':\'\';}if(_qh){_qh.style.background=mode===\'high\'?\'var(--green)\':\'\';_qh.style.color=mode===\'high\'?\'#fff\':\'\';}try{localStorage.setItem(\'video_quality\',mode);}catch(e){}}if(_ql)_ql.addEventListener(\'click\',function(){_setQ(\'low\',true);});if(_qh)_qh.addEventListener(\'click\',function(){_setQ(\'high\',true);});if(_vid)_vid.addEventListener(\'error\',function(){if(_vid.src.indexOf(\'/preview/\')>=0){_vid.src=view;_vid.load();}});fetch(\'/preview_status/\'+t.dataset.id).then(function(r){return r.json();}).then(function(j){if(j.has_preview){if(_qrow)_qrow.style.display=\'flex\';var saved=null;try{saved=localStorage.getItem(\'video_quality\');}catch(e){}_setQ(saved===\'high\'?\'high\':\'low\',false);if(_qn)_qn.textContent=\'流畅版是低清预览，起播快；点高清切回原片，加载慢自己受着。\';}else{if(_qrow)_qrow.style.display=\'none\';if(_qn)_qn.textContent=\'低清版还没生成，先看原片。\';}}).catch(function(){if(_qrow)_qrow.style.display=\'none\';});}else if(t.dataset.isaud===\'1\'){bd.innerHTML=\'<audio controls src="\'+view+\'" style="width:100%"></audio>\';}else if(t.dataset.cat===\'文档\'){bd.innerHTML=\'<iframe src="\'+view+\'" style="width:100%;height:420px;border:0;border-radius:12px"></iframe><p style="margin-top:10px"><a class="btn primary segdl" data-dl="\'+dl+\'" data-size="\'+(t.dataset.size||\'0\')+\'" href="\'+dl+\'">下载</a></p>\';}else{bd.innerHTML=\'<p class="note">这个类型不能在线预览，请下载后打开。</p><a class="btn primary segdl" data-dl="\'+dl+\'" data-size="\'+(t.dataset.size||\'0\')+\'" href="\'+dl+\'">下载</a>\';}ov.style.display=\'flex\';_loadComments(t.dataset.id);}function _escHtml(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");} function _loadComments(fid){var card=document.getElementById(\'previewCard\');if(!card)return;var cs=document.getElementById(\'commentSection\');if(!cs){cs=document.createElement(\'div\');cs.id=\'commentSection\';card.appendChild(cs);}cs.innerHTML=\'<div style="margin-top:14px;border-top:1px solid var(--line);padding-top:10px"><b>留言</b><div id="commentList" style="margin-top:8px">加载留言中…</div><input id="commentNick" placeholder="昵称（不填就是匿名）" maxlength="50" style="width:100%;margin-top:8px;box-sizing:border-box"><textarea id="commentText" rows="2" placeholder="写下你的留言…" maxlength="500" style="width:100%;margin-top:6px;box-sizing:border-box"></textarea><button class="btn primary" id="commentSend" type="button" style="margin-top:6px">发送留言</button><div id="commentMsg" class="note"></div></div>\';var list=cs.querySelector(\'#commentList\');function render(items){if(!items||!items.length){list.innerHTML=\'<p class="note">还没有留言。</p>\';return;}list.innerHTML=items.map(function(c){return \'<div style="margin-bottom:8px"><div style="font-weight:800;font-size:13px">\'+_escHtml(c.author||\'匿名\')+\' <span style="font-weight:400;color:var(--muted);font-size:11px">\'+_escHtml(c.time||\'\')+\'</span></div><div style="font-size:13px;word-break:break-all">\'+_escHtml(c.text||\'\')+\'</div></div>\';}).join(\'\');}function reload(){fetch(\'/comments/\'+encodeURIComponent(fid)).then(function(r){return r.json();}).then(function(j){render(j.comments);}).catch(function(){list.innerHTML=\'<p class="note">留言加载失败，稍后再试。</p>\';});}reload();var btn=cs.querySelector(\'#commentSend\');if(btn){btn.onclick=function(){var nick=cs.querySelector(\'#commentNick\').value;var txt=cs.querySelector(\'#commentText\').value;var msg=cs.querySelector(\'#commentMsg\');if(!txt.trim()){msg.textContent=\'留言不能为空\';return;}msg.textContent=\'发送中…\';var fd=new FormData();fd.append(\'author\',nick);fd.append(\'text\',txt);fetch(\'/comments/\'+encodeURIComponent(fid),{method:\'POST\',body:fd}).then(function(r){return r.json();}).then(function(j){if(j.ok){cs.querySelector(\'#commentText\').value=\'\';msg.textContent=\'已发送\';reload();}else{msg.textContent=j.error||\'发送失败\';}}).catch(function(){msg.textContent=\'发送失败，请重试\';});};}} function closePreview(){var ov=document.getElementById(\'previewOverlay\');var bd=document.getElementById(\'previewBody\');if(bd){bd.querySelectorAll(\'video,audio\').forEach(function(m){m.pause();m.removeAttribute(\'src\');m.load();});bd.innerHTML=\'\';}if(ov)ov.style.display=\'none\';}function dlTile(t){var url=t.dataset.dl;var name=t.querySelector(\'.tile-name\').textContent;var _st=document.createElement(\'div\');_st.className=\'note dlstatus\';_st.textContent=\'下载准备中…\';t.appendChild(_st);_dlFeed(url,name,_st);}document.getElementById(\'albumGrid\').addEventListener(\'click\',function(e){var t=e.target.closest(\'.tile\');if(!t)return;if(e.target.closest&&e.target.closest(\'.dlstatus\'))return;if(selectMode){var id=t.dataset.id;if(selected.has(id)){selected.delete(id);t.classList.remove(\'selected\');}else{selected.add(id);t.classList.add(\'selected\');}updateSel();}else{openPreview(t);}});document.getElementById(\'previewClose\').addEventListener(\'click\',closePreview);document.getElementById(\'previewOverlay\').addEventListener(\'click\',function(e){if(e.target.id===\'previewOverlay\')closePreview();});document.getElementById(\'selBtn\').addEventListener(\'click\',function(){selectMode=!selectMode;var g=document.getElementById(\'albumGrid\');if(selectMode){g.classList.add(\'selecting\');this.textContent=\'取消选择\';}else{selected.clear();document.querySelectorAll(\'#albumGrid .tile\').forEach(function(t){t.classList.remove(\'selected\');});g.classList.remove(\'selecting\');this.textContent=\'选择\';}updateSel();});document.getElementById(\'dlSelBtn\').addEventListener(\'click\',function(){var tiles=[];document.querySelectorAll(\'#albumGrid .tile\').forEach(function(t){if(selected.has(t.dataset.id))tiles.push(t);});if(!tiles.length)return;var i=0;function next(){if(i>=tiles.length){alert(\'已开始下载 \'+tiles.length+\' 个文件（浏览器可能逐个询问是否允许多个下载）。\');selected.clear();document.querySelectorAll(\'#albumGrid .tile\').forEach(function(t){t.classList.remove(\'selected\');});document.getElementById(\'albumGrid\').classList.remove(\'selecting\');selectMode=false;document.getElementById(\'selBtn\').textContent=\'选择\';updateSel();return;}dlTile(tiles[i]);i++;setTimeout(next,600);}next();});document.getElementById(\'newFolderBtn\').addEventListener(\'click\',function(){var f=document.getElementById(\'newFolderForm\');f.style.display=\'flex\';});document.getElementById(\'newFolderCancel\').addEventListener(\'click\',function(){document.getElementById(\'newFolderForm\').style.display=\'none\';});document.getElementById(\'moveForm\').addEventListener(\'submit\',function(e){if(!selected.size){e.preventDefault();alert(\'请先勾选文件\');}});document.getElementById(\'moveBtn\').addEventListener(\'click\',function(){if(!selected.size){alert(\'请先勾选文件\');return;}var ov=document.getElementById(\'moveOverlay\');if(ov)ov.style.display=\'flex\';});document.getElementById(\'moveCancel\').addEventListener(\'click\',function(){var ov=document.getElementById(\'moveOverlay\');if(ov)ov.style.display=\'none\';});document.getElementById(\'moveOverlay\').addEventListener(\'click\',function(e){if(e.target.id===\'moveOverlay\'){e.target.style.display=\'none\';}});document.querySelectorAll(\'#moveOverlay [data-dest]\').forEach(function(b){b.addEventListener(\'click\',function(){var d=document.getElementById(\'moveDest\');if(d)d.value=b.dataset.dest;document.getElementById(\'moveForm\').submit();});});function _segDl(url,name,size,progEl,doneCb){var CH=8*1024*1024,PAR=3,TRY=3,total=Math.ceil(size/CH),parts=new Array(total),done=0,failed=false,next=0,inflight=0;function show(){if(progEl)progEl.textContent=\'下载中 \'+Math.round(done/total*100)+\'%（\'+(done*CH>size?size:done*CH)+\' / \'+size+\' 字节）\';}function fetchC(idx,att,cb){var st=idx*CH,en=Math.min(size-1,st+CH-1);var x=new XMLHttpRequest();x.open(\'GET\',url,true);x.setRequestHeader(\'Range\',\'bytes=\'+st+\'-\'+en);x.responseType=\'blob\';x.onload=function(){if(x.status===206||x.status===200){cb(null,x.response);}else if(att<TRY){fetchC(idx,att+1,cb);}else{cb(new Error(\'chunk failed\'));}};x.onerror=function(){if(att<TRY){fetchC(idx,att+1,cb);}else{cb(new Error(\'chunk network error\'));}};x.send();}function launch(){while(!failed&&inflight<PAR&&next<total){(function(idx){inflight++;fetchC(idx,0,function(err,blob){inflight--;if(failed)return;if(err){failed=true;if(doneCb)doneCb(err);return;}parts[idx]=blob;done++;show();if(done===total){var a=document.createElement(\'a\');a.href=URL.createObjectURL(new Blob(parts));a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(function(){URL.revokeObjectURL(a.href);},1000);if(doneCb)doneCb(null);}else{launch();}});})(next);next++;}}show();launch();}function _dlFeed(url,name,el){el.textContent=\'下载准备中…\';var x=new XMLHttpRequest();x.open(\'GET\',url,true);x.responseType=\'blob\';x.onprogress=function(e){if(e.lengthComputable&&e.total){el.textContent=\'下载中 \'+Math.round(e.loaded/e.total*100)+\'%\';}else{el.textContent=\'下载中 \'+e.loaded+\' 字节\';}};function _fail(){el.innerHTML=\'\';var b=document.createElement(\'button\');b.className=\'btn\';b.type=\'button\';b.textContent=\'下载失败，点我重试\';b.onclick=function(){_dlFeed(url,name,el);};el.appendChild(b);}x.onload=function(){if(x.status===200){el.textContent=\'下载完成\';var a=document.createElement(\'a\');a.href=URL.createObjectURL(x.response);a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(function(){URL.revokeObjectURL(a.href);},1000);setTimeout(function(){if(el.parentNode)el.parentNode.removeChild(el);},2000);}else{_fail();}};x.onerror=function(){_fail();};x.send();}document.getElementById(\'previewBody\').addEventListener(\'click\',function(e){var a=e.target.closest?e.target.closest(\'.segdl\'):null;if(!a)return;var sz=parseInt(a.dataset.size||\'0\',10);if(sz>=20*1024*1024&&sz<=500*1024*1024){e.preventDefault();var prog=document.getElementById(\'segProg\');if(!prog){prog=document.createElement(\'div\');prog.id=\'segProg\';prog.className=\'note\';var _pb=document.getElementById(\'previewBody\');if(_pb)_pb.appendChild(prog);}_segDl(a.dataset.dl,a.dataset.name||\'download\',sz,prog,function(err){if(err&&prog)prog.textContent=\'下载失败，请重试\';else if(prog)prog.textContent=\'下载完成\';});return;}e.preventDefault();var _st2=document.createElement(\'div\');_st2.className=\'note dlstatus\';var _pb2=document.getElementById(\'previewBody\');if(_pb2)_pb2.appendChild(_st2);var _nm=document.getElementById(\'previewTitle\')?document.getElementById(\'previewTitle\').textContent:\'download\';if(!sz||sz>500*1024*1024){_st2.textContent=\'下载准备中…已开始下载，请留意浏览器下载栏\';var _tmp=document.createElement(\'a\');_tmp.href=a.dataset.dl;_tmp.download=_nm;document.body.appendChild(_tmp);_tmp.click();_tmp.remove();return;}_dlFeed(a.dataset.dl,_nm,_st2);});var _pre={};function _preOrig(t){if(!t||t.dataset.isimg!==\'1\')return;var u=t.dataset.view;if(!u||_pre[u])return;_pre[u]=1;var im=new Image();im.src=u;}document.addEventListener(\'touchstart\',function(e){var t=e.target.closest?e.target.closest(\'.tile\'):null;if(t)_preOrig(t);},{passive:true});document.addEventListener(\'mouseover\',function(e){var t=e.target.closest?e.target.closest(\'.tile\'):null;if(t)_preOrig(t);});function _preVisible(){if(navigator.connection&&navigator.connection.saveData)return;var n=0;document.querySelectorAll(\'#albumGrid .tile\').forEach(function(t){if(n>=6)return;if(t.style.display===\'none\')return;var r=t.getBoundingClientRect();if(r.bottom<0||r.top>window.innerHeight)return;var b=Object.keys(_pre).length;_preOrig(t);if(Object.keys(_pre).length>b)n++;});}if(\'requestIdleCallback\' in window){requestIdleCallback(_preVisible,{timeout:2000});}else{setTimeout(_preVisible,1500);}</script></div>'
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
    '{% else %}<div class="card section">'
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
    "<input class=\"textin\" type=\"password\" name=\"token\" placeholder=\"站长区口令\" required> "
    "<button class=\"btn primary\" type=\"submit\">进入站长区</button></form></div>"
    "</div><script>var TAB_NAMES={'principle':'原理区','publish':'发布区','published':'已发布区','feedback':'意见区','private':'私人区','admin':'站长区'};function openDrawer(){var d=document.getElementById('drawer');var b=document.getElementById('drawerBackdrop');if(!d||!b)return;d.style.display='flex';b.style.display='block';requestAnimationFrame(function(){requestAnimationFrame(function(){d.classList.add('open');b.classList.add('open');});});}function closeDrawer(){var d=document.getElementById('drawer');var b=document.getElementById('drawerBackdrop');if(!d||!b||d.style.display==='none')return;d.classList.remove('open');b.classList.remove('open');setTimeout(function(){if(!d.classList.contains('open')){d.style.display='none';b.style.display='none';}},340);}function showTab(name){if(window.__uploading&&name==='admin'&&!confirm('正在上传，点站长区进入审核页会中断上传。确定要中断吗？')){return;}document.querySelectorAll('.tabpanel').forEach(function(el){el.classList.toggle('active',el.id==='tab-'+name);});document.querySelectorAll('#drawernav .drawer-navbtn').forEach(function(b){b.classList.toggle('active',b.dataset.tab===name);});var ct=document.getElementById('curtab');if(ct)ct.textContent='/ '+(TAB_NAMES[name]||name);closeDrawer();}document.getElementById('drawerBtn').addEventListener('click',function(){var d=document.getElementById('drawer');if(d.classList.contains('open')){closeDrawer();}else{openDrawer();}});document.getElementById('drawerBackdrop').addEventListener('click',closeDrawer);document.querySelectorAll('#drawernav .drawer-navbtn').forEach(function(b){b.addEventListener('click',function(e){e.preventDefault();location.hash=b.dataset.tab;showTab(b.dataset.tab);});});document.querySelectorAll('#drawerCats .drawer-navbtn').forEach(function(b){b.addEventListener('click',function(){location.hash='published';showTab('published');curCat=b.dataset.cat;document.querySelectorAll('#cattabs .tab').forEach(function(t){t.classList.toggle('active',t.textContent.indexOf(b.dataset.cat)===0);});applyFilter();});});var _odb=document.getElementById('openDrawerBtn');if(_odb)_odb.addEventListener('click',openDrawer);var h=(location.hash||'#published').replace('#','');if(['principle','publish','published','feedback','private','admin'].indexOf(h)<0){h='published';}showTab(h);</script>"
    "{% if entered and announcement %}<div class=\"ann-overlay\" id=\"annOverlay\"><div class=\"ann-card\" role=\"dialog\" aria-label=\"公告\"><div style=\"display:flex;justify-content:space-between;align-items:flex-start;gap:10px\"><h2>{{ announcement.title|e }}</h2><button class=\"btn\" id=\"annClose\" type=\"button\" aria-label=\"关闭公告\">×</button></div><p>{{ announcement.body_html|safe }}</p><div style=\"text-align:right\"><button class=\"btn primary\" id=\"annOk\" type=\"button\">我知道了</button></div></div></div><script>(function(){var ov=document.getElementById('annOverlay');if(!ov)return;try{if(sessionStorage.getItem('exchange_ann_closed')){ov.remove();return;}}catch(e){}var reduced=!!(window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches);function closeAnn(){try{sessionStorage.setItem('exchange_ann_closed','1');}catch(e){}if(reduced){ov.remove();return;}ov.classList.remove('ann-show');var done=false;function hide(){if(done)return;done=true;ov.remove();}ov.addEventListener('transitionend',hide,{once:true});setTimeout(hide,300);}var c=document.getElementById('annClose');if(c)c.addEventListener('click',closeAnn);var o=document.getElementById('annOk');if(o)o.addEventListener('click',closeAnn);ov.addEventListener('click',function(e){if(e.target===ov)closeAnn();});requestAnimationFrame(function(){requestAnimationFrame(function(){ov.classList.add('ann-show');});});})();</script>{% endif %}"
    "<script>(function(){function applyTheme(t){var b=document.getElementById('themeBtn');if(t==='dark'){document.documentElement.setAttribute('data-theme','dark');if(b)b.textContent='\u2600\ufe0f \u767d\u5929\u6a21\u5f0f';}else{document.documentElement.removeAttribute('data-theme');if(b)b.textContent='🌙 \u6df1\u8272\u6a21\u5f0f';}try{localStorage.setItem('theme',t);}catch(e){}}var _b=document.getElementById('themeBtn');if(_b){_b.addEventListener('click',function(){var cur=document.documentElement.getAttribute('data-theme');applyTheme(cur==='dark'?'light':'dark');});}try{var saved=localStorage.getItem('theme');if(saved==='dark'){applyTheme('dark');}else if(saved==='light'){applyTheme('light');}}catch(e){}})();</script></div></body></html>"
)

ADMIN_PAGE = (
    "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>审核 - 交换站</title><style>"
    + BASE_CSS
    + "</style></head><body><div class=\"wrap\" id=\"admin-main\"><h1>待审核文件</h1>{% if ai_configured %}<div class=\"card section\"><span class=\"note\">上传后自动审核：AI 通过的文件会自动发布；可疑、无法判断或调用失败的留在待审核等站长人工看。</span></div>{% else %}<div class=\"card section warn\">AI 审核未配置（需配置 DEEPSEEK_API_KEY 后可用），原有审核流程不受影响。</div>{% endif %}"
    "{% if not drive_configured %}"
    "<div class=\"card section warn\"><strong>云盘暂未连接</strong><br>这里没有列出任何真实云盘文件。</div>"
    "{% elif files %}<div class=\"card section\">{% for f in files %}<div class=\"file\">{% if f.is_image or f.is_video %}<img class=\"thumb\" src=\"/thumb/{{ f.id }}\" loading=\"lazy\" onerror=\"this.style.display='none'\">{% else %}<div class=\"fic\">{{ f.ext }}</div>{% endif %}"
    "<div class=\"fmain\"><div class=\"fname\">{{ f.name }}</div><div class=\"fmeta\"><span>{{ f.size }}</span><span class=\"chip wait\">待审核</span>{% if f.source == \"baidu\" %}<span class=\"chip\">百度</span>{% elif f.source == \"google\" %}<span class=\"chip\">谷歌</span>{% endif %}</div></div><div class=\"airow\">{% if f.ai %}<span class=\"chip {% if f.ai.verdict == '可疑' %}bad{% elif f.ai.verdict == '通过' %}{% elif f.ai.verdict == 'failed' %}wait{% else %}sample{% endif %}\">AI：{% if f.ai.verdict == 'failed' %}未能审核{% else %}{{ f.ai.verdict }}{% endif %}</span><span class=\"note\">{{ f.ai.reason }}{% if f.ai.confidence is not none %}（置信度 {{ f.ai.confidence }}）{% endif %}{% if f.ai.note %} · {{ f.ai.note }}{% endif %}</span>{% if f.ai.verdict == 'failed' %}<form method=\"post\" action=\"/admin/ai_review\" style=\"display:inline\"><input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\"><button class=\"btn\">重试初审</button></form>{% endif %}{% elif ai_configured %}<span class=\"note\">尚未 AI 初审</span><form method=\"post\" action=\"/admin/ai_review\" style=\"display:inline\"><input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\"><button class=\"btn\">AI 初审</button></form>{% endif %}</div>"
    "<div class=\"actions\"><a class=\"btn\" href=\"/view/{{ f.id }}\" target=\"_blank\">打开预览</a>"
    "<form class=\"renameform\" method=\"post\" action=\"/rename\" style=\"display:inline-flex\"><input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\">"
    "<input type=\"hidden\" name=\"back\" value=\"/admin\"><input class=\"renametext\" type=\"text\" name=\"new_name\" placeholder=\"改个名字…\" required>"
    "<button class=\"btn\">改名</button></form><form method=\"post\" action=\"/admin/approve\" style=\"display:inline\">"
    "<input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\"><button class=\"btn primary\">通过</button></form> "
    "<form method=\"post\" action=\"/admin/reject\" style=\"display:inline\">"
    "<input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\">"
    "<button class=\"btn danger\">不通过（移到回收站）</button></form></div></div>{% endfor %}</div>"
    "{% else %}<p class=\"empty\">目前没有待审核的文件。</p>{% endif %}"
    "<p><a href=\"/admin/accounts\">账号管理</a> · <a href=\"/admin/private\">私人区管理</a> · <a href=\"/admin/feedback\">意见管理</a> · <a href=\"/admin/logs\">访问日志</a> · <a href=\"/admin/comments\">留言管理</a> · <a href=\"/admin/published\">已发布管理（删除已发布文件）</a>{% if not is_secondary %} · <a href=\"/admin/announcement\">公告管理</a>{% endif %} · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div>" + ADMIN_PJAX_SCRIPT + "</body></html>"
)
ADMIN_PAGE = (
    "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>审核 - 交换站</title><style>"
    + BASE_CSS
    + "</style></head><body><div class=\"wrap\" id=\"admin-main\"><h1>待审核文件</h1>{% if ai_configured %}<div class=\"card section\"><span class=\"note\">上传后自动审核：AI 通过的文件会自动发布；可疑、无法判断或调用失败的留在待审核等站长人工看。</span></div>{% else %}<div class=\"card section warn\">AI 审核未配置（需配置 DEEPSEEK_API_KEY 后可用），原有审核流程不受影响。</div>{% endif %}"
    "{% if not drive_configured %}"
    "<div class=\"card section warn\"><strong>云盘暂未连接</strong><br>这里没有列出任何真实云盘文件。</div>"
    "{% elif files %}<div class=\"card section\">{% for f in files %}<div class=\"file\">{% if f.is_image or f.is_video %}<img class=\"thumb\" src=\"/thumb/{{ f.id }}\" loading=\"lazy\" onerror=\"this.style.display='none'\">{% else %}<div class=\"fic\">{{ f.ext }}</div>{% endif %}"
    "<div class=\"fmain\"><div class=\"fname\">{{ f.name }}</div><div class=\"fmeta\"><span>{{ f.size }}</span><span class=\"chip wait\">待审核</span>{% if f.source == \"baidu\" %}<span class=\"chip\">百度</span>{% elif f.source == \"google\" %}<span class=\"chip\">谷歌</span>{% endif %}</div></div><div class=\"airow\">{% if f.ai %}<span class=\"chip {% if f.ai.verdict == '可疑' %}bad{% elif f.ai.verdict == '通过' %}{% elif f.ai.verdict == 'failed' %}wait{% else %}sample{% endif %}\">AI：{% if f.ai.verdict == 'failed' %}未能审核{% else %}{{ f.ai.verdict }}{% endif %}</span><span class=\"note\">{{ f.ai.reason }}{% if f.ai.confidence is not none %}（置信度 {{ f.ai.confidence }}）{% endif %}{% if f.ai.note %} · {{ f.ai.note }}{% endif %}</span>{% if f.ai.verdict == 'failed' %}<form method=\"post\" action=\"/admin/ai_review\" style=\"display:inline\"><input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\"><button class=\"btn\">重试初审</button></form>{% endif %}{% elif ai_configured %}<span class=\"note\">尚未 AI 初审</span><form method=\"post\" action=\"/admin/ai_review\" style=\"display:inline\"><input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\"><button class=\"btn\">AI 初审</button></form>{% endif %}</div>"
    "<div class=\"actions\"><a class=\"btn\" href=\"/view/{{ f.id }}\" target=\"_blank\">打开预览</a>"
    "<form class=\"renameform\" method=\"post\" action=\"/rename\" style=\"display:inline-flex\"><input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\">"
    "<input type=\"hidden\" name=\"back\" value=\"/admin\"><input class=\"renametext\" type=\"text\" name=\"new_name\" placeholder=\"改个名字…\" required>"
    "<button class=\"btn\">改名</button></form><form method=\"post\" action=\"/admin/approve\" style=\"display:inline\">"
    "<input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\"><button class=\"btn primary\">通过</button></form> "
    "<form method=\"post\" action=\"/admin/reject\" style=\"display:inline\">"
    "<input type=\"hidden\" name=\"file_id\" value=\"{{ f.id }}\">"
    "<button class=\"btn danger\">不通过（移到回收站）</button></form></div></div>{% endfor %}</div>"
    "{% else %}<p class=\"empty\">目前没有待审核的文件。</p>{% endif %}"
    "<p><a href=\"/admin/accounts\">账号管理</a> · <a href=\"/admin/private\">私人区管理</a> · <a href=\"/admin/feedback\">意见管理</a> · <a href=\"/admin/logs\">访问日志</a> · <a href=\"/admin/comments\">留言管理</a> · <a href=\"/admin/published\">已发布管理（删除已发布文件）</a>{% if not is_secondary %} · <a href=\"/admin/announcement\">公告管理</a>{% endif %} · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div>" + ADMIN_PJAX_SCRIPT + "</body></html>"
)

ADMIN_PUBLISHED_PAGE = (
    "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>已发布管理 - 交换站</title><style>"
    + BASE_CSS
    + "</style></head><body><div class=\"wrap\" id=\"admin-main\"><h1>已发布管理</h1>"
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
    "<p><a href=\"/admin\">回待审核</a>{% if not is_secondary %} · <a href=\"/admin/announcement\">公告管理</a>{% endif %} · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div>" + ADMIN_PJAX_SCRIPT + "</body></html>"
)


@app.route("/api/private")
def api_private():
    code = _env("EXCHANGE_CODE")
    out = {"files": []}
    if code and _has_gate(code, "exchange_access"):
        fb_user = _account_gate()
        if fb_user and _use_baidu_meta() and _private_account_ok(None, fb_user):
            out["files"] = _baidu_private_list(fb_user)
        else:
            service = drive_service()
            if fb_user and service and _private_account_ok(service, fb_user):
                folder = _private_user_folder_id(service, fb_user, create=False)
                if folder:
                    out["files"] = list_folder(service, folder)
    return jsonify(out)


@app.route("/api/private_all")
def api_private_all():
    out = {"users": []}
    if _is_manager():
        if _use_baidu_meta():
            out["users"] = _baidu_private_users()
            return jsonify(out)
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
    out = {"published": [], "pending": [], "folders": [], "notice": ""}
    if code and _has_gate(code, "exchange_access"):
        try:
            service = drive_service()
        except Exception:
            service = None
        gfiles, gfolders, google_err = [], [], False
        if _google_fallback_enabled():
            try:
                if service is not None:
                    gfiles, gfolders = _list_published_tree(service)
            except Exception:
                gfiles, gfolders, google_err = [], [], True
        bfiles, bfolders, baidu_err = [], [], False
        if _baidu_enabled():
            try:
                bfiles, bfolders = _baidu_published()
            except Exception:
                baidu_err = True
        elif _priority_backend() == "baidu":
            baidu_err = True
        files, folders = _merge_mixed(gfiles, gfolders, bfiles, bfolders)
        out["published"] = files
        out["folders"] = folders
        _dl_attach(files, service)
        _ai_attach_published(files)
        notices = []
        if google_err:
            notices.append("谷歌区暂时连不上，只显示百度区")
        if baidu_err:
            if _priority_backend() == "baidu" and not _baidu_enabled():
                notices.append("百度后端未配置，只显示谷歌区")
            elif _baidu_enabled():
                notices.append("百度区暂时连不上，只显示谷歌区")
        out["notice"] = "；".join(notices)
    if _is_manager():
        try:
            try:
                service = drive_service()
            except Exception:
                service = None
            pending = _merged_pending(service)
            for f in pending:
                f["ai"] = _ai_get_result(f["id"])
            out["pending"] = pending
        except Exception:
            out["pending"] = []
    return jsonify(out)


@app.route("/api/announcement")
def api_announcement():
    """进站公告 JSON（供中继模式的上海站读取 Render 后端公告；本站直连时不走这里）。"""
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access"):
        return jsonify({"ok": False}), 403
    return jsonify({"ok": True, "announcement": _announcement_display(drive_service())})


@app.route("/")
def index():
    code = _env("EXCHANGE_CODE")
    entered = _has_gate(code, "exchange_access")
    files = []
    folders = []
    backend_notice = ""
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
                backend_notice = _rj.get("notice", "") or ""
            except Exception:
                # 中继不通时降级：百度直连，只显示百度区，绝不 500（设计 2.3 第 3 点）
                files, folders = [], []
                if _baidu_enabled():
                    try:
                        bfiles, bfolders = _baidu_published()
                        files, folders = _merge_mixed([], [], bfiles, bfolders)
                        backend_notice = "谷歌区（中继）暂时连不上，只显示百度区"
                    except Exception:
                        backend_notice = "谷歌区（中继）暂时连不上，百度区也未能加载"
                else:
                    backend_notice = "谷歌区（中继）暂时连不上"
        else:
            files, folders, backend_notice = _cached_published(drive_service())
    cats = [{"name": c, "count": sum(1 for f in files if f["kind"] == c)} for c in CATS]
    private_files = []
    private_used = 0
    fb_user = _account_gate()
    if entered and fb_user:
        if _is_relay():
            try:
                import requests as _rq
                _cookies = {}
                for _k in ("exchange_access", "feedback_user", "secondary_access"):
                    if _k in request.cookies:
                        _cookies[_k] = request.cookies[_k]
                _r = _rq.get(_relay_backend() + "/api/private", cookies=_cookies, timeout=15)
                private_files = _r.json().get("files", [])
            except Exception:
                private_files = []
        else:
            if _use_baidu_meta():
                if _private_account_ok(None, fb_user):
                    private_files = _baidu_private_list(fb_user)
            else:
                service = drive_service()
                if service and _private_account_ok(service, fb_user):
                    folder = _private_user_folder_id(service, fb_user, create=False)
                    if folder:
                        private_files = list_folder(service, folder)
    private_used = sum(f.get("size_bytes", 0) for f in private_files)
    ann = None
    if entered:
        if _is_relay():
            try:
                import requests as _rq
                _cookies = {}
                if "exchange_access" in request.cookies:
                    _cookies["exchange_access"] = request.cookies["exchange_access"]
                _r = _rq.get(_relay_backend() + "/api/announcement", cookies=_cookies, timeout=15)
                ann = _r.json().get("announcement")
            except Exception:
                ann = {"title": ANNOUNCEMENT_DEFAULT_TITLE, "body": ANNOUNCEMENT_DEFAULT_BODY}
        else:
            ann = _announcement_display(drive_service())
        if ann:
            import html as _html
            ann = dict(ann)
            ann["body_html"] = _html.escape(ann["body"]).replace("\n", "<br>")
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
        secondary_configured=_secondary_configured(),
        msg=request.args.get("msg", ""),
        announcement=ann,
        backend_notice=backend_notice,
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
    resp.delete_cookie("secondary_access")
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
            for _k in ("exchange_access", "admin_access", "secondary_access"):
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
    if _priority_backend() == "baidu":
        if not _baidu_enabled():
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"ok": False, "msg": "百度暂未连接，上传暂不可用，请联系站长"}), 503
            return redirect("/?msg=百度暂未连接，上传暂不可用，请联系站长")
        _base, _pub, _pend = _baidu_dirs()
        uploads = request.files.getlist("file")
        uploads = [u for u in uploads if u and u.filename]
        if not uploads:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"ok": False, "msg": "没有可上传的文件"})
            return redirect("/?msg=没有可上传的文件")
        count = 0
        for up in uploads:
            name = _safe_filename(up.filename)
            if not name:
                continue
            try:
                up.stream.seek(0)
            except Exception:
                pass
            # 大小守卫：记下上传时应有的字节数，审核/AI 自动发布时核对云盘实际大小，
            # 不一致（没传完）一律不通过——百度分支也不能绕过这道关。
            _up_size = 0
            try:
                up.stream.seek(0, 2)
                _up_size = up.stream.tell()
                up.stream.seek(0)
            except Exception:
                _up_size = 0
            try:
                fs_id = _baidu_upload_stream(_pend + name, up.stream)
                if not fs_id:
                    entry = _baidu_find_by_path(_pend + name)
                    fs_id = (entry or {}).get("fs_id")
            except Exception as e:
                print(f"[baidu-upload] failed: {e}", flush=True)
                continue
            count += 1
            if fs_id:
                if _up_size:
                    EXPECTED_SIZES["bd:" + str(fs_id)] = _up_size
                _auto_ai_trigger("bd:" + str(fs_id))
        if count == 0:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"ok": False, "msg": "百度上传失败，请重试"})
            return redirect("/?msg=百度上传失败，请重试")
        _log("上传", f"{count} 个文件")
        msg = f"已上传 {count} 个文件到待审核，站长通过后才会出现在已发布列表"
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": True, "msg": msg})
        return redirect(f"/?msg={msg}")
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
        _created = service.files().create(
            body={"name": name, "parents": [PENDING_FOLDER_ID]},
            media_body=media,
            fields="id",
        ).execute() or {}
        count += 1
        if _created.get("id"):
            _auto_ai_trigger(_created["id"])
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
    if _priority_backend() == "baidu":
        # 百度优先模式下直传暂缓（设计第 1 节第 4 项）：前端收到失败后自动改走本站中转上传。
        return jsonify({"ok": False, "msg": "百度优先模式下请用中转上传"}), 503
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
    if fid:
        if _is_relay():
            _auto_ai_trigger_relay(fid)
        else:
            _auto_ai_trigger(fid)
    _log("上传（直传）", name)
    return jsonify({"ok": True, "msg": "已上传到待审核，站长通过后才会出现在已发布列表"})


def _parse_range(range_header, total):
    """Parse a single Range header like bytes=0-1023 / bytes=1024- / bytes=-500.
    Returns (start, end) inclusive, or None when absent/invalid/unsatisfiable."""
    if not range_header or total <= 0:
        return None
    try:
        h = (range_header or "").strip()
        if not h.startswith("bytes="):
            return None
        spec = h[len("bytes="):].strip().split(",")[0].strip()
        if "-" not in spec:
            return None
        a, b = spec.split("-", 1)
        if a == "" and b != "":
            n = int(b)
            if n <= 0:
                return None
            return (max(0, total - n), total - 1)
        start = int(a) if a else 0
        end = int(b) if b else total - 1
        if start < 0 or end < start:
            return None
        end = min(end, total - 1)
        if start >= total:
            return None
        return (start, end)
    except Exception:
        return None


@app.route("/view/<file_id>")
def view(file_id):
    """在线预览：已发布文件熟人可看，待审核文件只有站长可看。
    图片、PDF、文本、视频、音频会在浏览器里直接打开，其他类型浏览器会提示下载。"""
    if _is_relay():
        return _proxy_to_backend(f"/view/{file_id}")
    if file_id.startswith("bd:"):
        return _baidu_view(file_id)
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
        if not _is_manager():
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

    _total = 0
    try:
        _total = int(meta.get("size") or 0)
    except Exception:
        _total = 0
    _rng = _parse_range(request.headers.get("Range"), _total)

    def generate_view():
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, service.files().get_media(fileId=file_id))
        downloader._chunksize = 4 * 1024 * 1024
        done = False
        pos = 0
        while not done:
            buf.seek(0)
            buf.truncate(0)
            _, done = downloader.next_chunk()
            chunk = buf.getvalue()
            if not chunk:
                continue
            chunk_len = len(chunk)
            if _rng is not None:
                s, e = _rng
                if pos + chunk_len <= s:
                    pos += chunk_len
                    continue
                if pos > e:
                    break
                yield chunk[max(0, s - pos):min(chunk_len, e - pos + 1)]
                pos += chunk_len
                if pos > e:
                    break
                continue
            yield chunk
            pos += chunk_len

    _log("打开预览", meta.get("name", file_id))
    _view_headers = {
        "Content-Disposition": _disposition("inline", meta["name"]),
        "Content-Type": ctype,
        "Accept-Ranges": "bytes",
    }
    if _rng is not None:
        _view_headers["Content-Length"] = str(_rng[1] - _rng[0] + 1)
        _view_headers["Content-Range"] = f"bytes {_rng[0]}-{_rng[1]}/{_total}"
        return Response(generate_view(), status=206, headers=_view_headers)
    if _total > 0:
        _view_headers["Content-Length"] = str(_total)
    return Response(generate_view(), headers=_view_headers)


@app.route("/download/<file_id>")
def download(file_id):
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access"):
        return redirect("/?msg=请先输入访问口令")
    if _is_relay():
        return _proxy_to_backend(f"/download/{file_id}")
    if file_id.startswith("bd:"):
        return _baidu_download(file_id)
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

    _dl_total = 0
    try:
        _dl_total = int(meta.get("size") or 0)
    except Exception:
        _dl_total = 0
    _dl_rng = _parse_range(request.headers.get("Range"), _dl_total)

    def generate_dl():
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, service.files().get_media(fileId=file_id))
        downloader._chunksize = 4 * 1024 * 1024
        done = False
        pos = 0
        while not done:
            buf.seek(0)
            buf.truncate(0)
            _, done = downloader.next_chunk()
            chunk = buf.getvalue()
            if not chunk:
                continue
            chunk_len = len(chunk)
            if _dl_rng is not None:
                s, e = _dl_rng
                if pos + chunk_len <= s:
                    pos += chunk_len
                    continue
                if pos > e:
                    break
                yield chunk[max(0, s - pos):min(chunk_len, e - pos + 1)]
                pos += chunk_len
                if pos > e:
                    break
                continue
            yield chunk
            pos += chunk_len

    _log("下载", meta.get("name", file_id))
    if _dl_should_count(_dl_total):
        _dl_bump(file_id, service)
    dl_headers = {
        "Content-Disposition": _disposition("attachment", meta["name"]),
        "Content-Type": meta.get("mimeType") or "application/octet-stream",
        "Accept-Ranges": "bytes",
    }
    # Drive 元数据里有真实大小时必须带上 Content-Length，否则手机浏览器不知道总大小，会显示 -1.0 B
    try:
        _sz = int(meta.get("size") or 0)
        if _sz > 0:
            dl_headers["Content-Length"] = str(_sz)
    except Exception:
        pass
    if _dl_rng is not None:
        dl_headers["Content-Length"] = str(_dl_rng[1] - _dl_rng[0] + 1)
        dl_headers["Content-Range"] = f"bytes {_dl_rng[0]}-{_dl_rng[1]}/{_dl_total}"
        return Response(generate_dl(), status=206, headers=dl_headers)
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
            for _k in ("exchange_access", "feedback_user", "secondary_access"):
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
    if _use_baidu_meta():
        if not _private_account_ok(None, fb_user):
            return redirect("/?msg=这个账号已被禁用或不存在#private")
        _bdir = _baidu_private_dir(fb_user)
        _baidu_ensure_dir(_bdir.rstrip("/"))
        PRIVATE_QUOTA = 20 * 1024 * 1024 * 1024
        used = sum(f.get("size_bytes", 0) for f in _baidu_private_list(fb_user))
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
            try:
                _baidu_upload_stream(_bdir + name, up.stream)
                count += 1
            except Exception:
                continue
        if count == 0:
            return redirect("/?msg=没有可上传的文件#private")
        _log("私人上传", f"{fb_user} {count} 个文件")
        return redirect(f"/?msg=已上传 {count} 个文件到私人区，只有你和站长能看见#private")
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
    if (file_id or "").startswith("bd:"):
        if not _baidu_enabled():
            return redirect("/#private")
        try:
            meta = _baidu_meta(file_id[3:])
        except Exception:
            return redirect("/?msg=文件不存在或无权访问#private")
        path = meta.get("path") or ""
        owner = None
        _pb = _baidu_private_base()
        if path.startswith(_pb):
            rest = path[len(_pb):].strip("/")
            owner = rest.split("/")[0] if rest else None
        if owner is None or (owner != fb_user and not _private_is_admin()):
            return redirect("/?msg=这不是你的私人文件#private")
        try:
            _baidu_delete_path(path)
        except Exception:
            return redirect("/?msg=删除失败，请重试#private")
        _log("私人删除", meta.get("server_filename", file_id))
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
    if not _is_manager():
        return "请先在首页输入站长口令", 403
    if _is_relay():
        try:
            import requests as _rq
            _cookies = {}
            if "admin_access" in request.cookies:
                _cookies["admin_access"] = request.cookies["admin_access"]
            if "secondary_access" in request.cookies:
                _cookies["secondary_access"] = request.cookies["secondary_access"]
            _r = _rq.get(_relay_backend() + "/api/private_all", cookies=_cookies, timeout=15)
            users = _r.json().get("users", [])
        except Exception:
            users = []
    elif _use_baidu_meta():
        users = _baidu_private_users()
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
            "<body><div class=\"wrap\" id=\"admin-main\"><h1>私人区管理（站长可见全部）</h1>"
            "<p class=\"note\">站长能看见所有人的私人文件，普通用户只能看见自己的。</p>"
            + rows +
            "<p><a href=\"/admin\">回待审核</a> · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div>" + ADMIN_PJAX_SCRIPT + "</body></html>")


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
    if not _is_manager():
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
    if service is None and not _use_baidu_meta():
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



@app.route("/comments/<file_id>")
def comments_list(file_id):
    """留言列表：过了熟人门才能看；文件已删除时返回空列表，不报错。"""
    if _is_relay():
        return _proxy_to_backend(f"/comments/{file_id}")
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access"):
        return {"error": "请先输入访问口令"}, 403
    service = drive_service()
    if service is None:
        return {"error": "云盘暂未连接"}, 503
    if not _published_file_exists(service, file_id):
        return {"comments": []}
    return {"comments": _comments_for(service, file_id)}


@app.route("/comments/<file_id>", methods=["POST"])
def comments_post(file_id):
    """发留言：过了熟人门就能发，不用私人区账号；昵称不填就是匿名；正文限 500 字。"""
    if _is_relay():
        return _proxy_post_to_backend(f"/comments/{file_id}")
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access"):
        return {"error": "请先输入访问口令"}, 403
    service = drive_service()
    if service is None:
        return {"error": "云盘暂未连接"}, 503
    if not _published_file_exists(service, file_id):
        return {"error": "文件不存在或已删除"}, 404
    text = (request.form.get("text") or "").strip()
    if not text:
        return {"error": "留言不能为空"}, 400
    if len(text) > 500:
        text = text[:500]
    text = text.replace("\n", " ").replace("\t", " ")
    author = (request.form.get("author") or "").strip().replace("\n", " ").replace("\t", " ")
    if not author:
        author = "匿名"
    if len(author) > 50:
        author = author[:50]
    items = _read_comments(service)
    import time as _t, random as _r
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    cid = now.strftime("%Y%m%d%H%M%S") + str(_r.randint(100, 999))
    item = {"id": cid, "time": now.strftime("%Y-%m-%d %H:%M"), "file_id": file_id, "author": author, "text": text}
    items.insert(0, item)
    if _write_comments(service, list(reversed(items))):
        return {"ok": True, "comment": item}
    return {"error": "留言保存失败，请重试"}, 500


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
    if not _is_manager():
        return "请先在首页输入站长口令", 403
    service = drive_service()
    accounts = _read_accounts(service) if service else []
    _sec = _is_secondary()
    rows = ""
    for a in accounts:
        _pw = "••••••" if _sec else a["password"]
        _row = (
            f"<div class=\"logrow\"><div class=\"fname\">{a['name']}</div><div class=\"fmeta\"><span>密码：{_pw}</span><span>·</span><span>注册：{a['created']}</span><span>·</span><span>最后登录：{a.get('last','')}</span><span>·</span><span>{'已禁用' if a.get('disabled')=='1' else '正常'}</span></div>"
        )
        if not _sec:
            _row += (
                f"<form method=\"post\" action=\"/admin/accounts/change\" style=\"margin-top:6px\"><input type=\"hidden\" name=\"name\" value=\"{a['name']}\">"
                f"<input class=\"textin\" type=\"text\" name=\"new_password\" placeholder=\"新密码\" required> <button class=\"btn\">改密码</button> "
                f"<button class=\"btn\" formaction=\"/admin/accounts/toggle\">{'解封' if a.get('disabled')=='1' else '禁用'}</button></form>"
            )
        _row += "</div>"
        rows += _row
    if not rows:
        rows = "<p class=\"empty\">目前还没有注册账号。</p>"
    _note_html = '<p class="note">当前账号只能看见账号名和状态，看不见密码，也不能改密码、禁用 / 解封。</p>' if _sec else '<p class="note">站长能看见账号和密码，也能改密码、禁用 / 解封。请提醒用户不要用重要密码注册。</p>'
    return render_template_string(
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>账号管理 - 交换站</title><style>" + BASE_CSS + "</style></head><body><div class=\"wrap\" id=\"admin-main\">"
        "<h1>账号管理</h1>" + _note_html +
        "<div class=\"card section\">" + rows + "</div>"
        "<p><a href=\"/admin\">回待审核</a>" + (" · <a href=\"/admin/announcement\">公告管理</a>" if not _sec else "") + " · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div>" + ADMIN_PJAX_SCRIPT + "</body></html>"
    )


@app.route("/admin/accounts/change", methods=["POST"])
def admin_accounts_change():
    if not _is_admin():
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
    if not _is_admin():
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
    """站长区统一口令入口：输站长口令进完整权限，输二级口令进受限权限（后端限制不变）。"""
    admin_token = _env("ADMIN_TOKEN")
    sec_token = _env("SECONDARY_ADMIN_TOKEN")
    given = request.form.get("token", "")
    if admin_token and given == admin_token:
        resp = redirect("/admin")
        _log("站长进审核", "")
        resp.set_cookie("admin_access", _gate_value(admin_token), httponly=True, samesite="Lax")
        return resp
    if sec_token and sec_token != admin_token and given == sec_token:
        resp = redirect("/admin")
        _log("受限口令进审核", "")
        resp.set_cookie("secondary_access", _gate_value(sec_token), httponly=True, samesite="Lax")
        return resp
    if not admin_token:
        return "站长口令还没配置，请联系站长在 Render 环境变量中设置 ADMIN_TOKEN", 503
    _log("站长进审核失败", "口令不对")
    return "口令不对", 403


@app.route("/admin/secondary_login", methods=["POST"])
def secondary_login():
    token = _env("SECONDARY_ADMIN_TOKEN")
    if not token or token == _env("ADMIN_TOKEN"):
        return "受限入口未配置", 503
    if request.form.get("token", "") != token:
        _log("受限口令进审核失败", "口令不对")
        return "口令不对", 403
    resp = redirect("/admin")
    _log("受限口令登录", "")
    resp.set_cookie("secondary_access", _gate_value(token), httponly=True, samesite="Lax")
    return resp


@app.route("/admin")
def admin():
    if not _is_manager():
        if not _env("ADMIN_TOKEN") and not _secondary_configured():
            return "站长口令还没配置，请联系站长在 Render 环境变量中设置 ADMIN_TOKEN", 503
        return "请先在首页输入站长口令", 403
    if _is_relay():
        _rf = _relay_files()
        if _rf is None:
            files = _merged_pending(None)
            for f in files:
                f["ai"] = _ai_get_result(f["id"])
        else:
            files = _rf.get("pending", [])
    else:
        files = _merged_pending(drive_service())
        for f in files:
            f["ai"] = _ai_get_result(f["id"])
    return render_template_string(
        ADMIN_PAGE, files=files, drive_configured=True, ai_configured=_ai_configured(), is_secondary=_is_secondary()
    )


def _admin_file_or_403(service, file_id):
    meta = get_file(service, file_id)
    if PENDING_FOLDER_ID not in (meta.get("parents") or []):
        return None
    return meta

# ---------------- AI 自动审核（DeepSeek） ----------------
# 密钥只从环境变量读，绝不写进代码、日志或页面。规则：上传后后台自动初审；
# AI 通过 + 大小守卫过 → 自动发布；可疑/无法判断/调用失败（额度用完、超时、
# 断线、未配置）→ 留待审核，失败时理由记为“AI 未能审核”，绝不因调不通就
# 自动发布、也绝不自动驳回；大小守卫不过或移动失败 → 留待审核、理由写明。
# 图片走 DeepSeek 识图（deepseek-flash）真看图，结论标注“已看图初审”；
# 视频/音频仍只按文件名初审，通过自动发布时标注保留。
# 一键 AI 初审按钮已于 2026-11-04 删除：上传自动审核，单条重试按钮保留给老文件/额度恢复后补审。
_AI_TEXT_EXTS = {"txt", "md", "markdown", "csv", "json", "srt", "vtt", "ass", "lrc", "log"}
_AI_TEXT_LIMIT = 6000
_AI_ZIP_MAX_BYTES = 20 * 1024 * 1024
_AI_ZIP_NAME_LIMIT = 200
_AI_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
_AI_IMAGE_MAX_BYTES = 20 * 1024 * 1024
_AI_IMAGE_MAX_SIDE = 1024
_AI_RESULTS = {}
_AI_LOCK = threading.Lock()
_AI_ALL_RUNNING = set()
_AI_AUTO_RUNNING = set()
_AI_AUTO_LOCK = threading.Lock()
_AI_PUBLISHED = {}  # file_id -> 自动发布时的 AI 结果快照（供已发布格子显示标注）

# ---- AI 结论共享文件（两站同步） ----
# 结论不再只放各服务器内存：写进云盘根目录的共享文件，两台服务器读同一份。
# 根目录文件不会出现在已发布/待审核文件夹列表里，所以成不了格子。
# 格式：每行一个 JSON {file_id, time, verdict, reason, confidence, note}。
# 读取带 30 秒短缓存（与已发布列表缓存同级）；写结论时立即刷新本地缓存，
# 另一台服务器最迟在缓存过期后看到新结论。云盘不可用时只读内存，不报错。
# 谷歌 Drive id 与百度 bd: id 本来就是不同字符串，按键匹配自然互不串。
_AI_FILE_NAME = "交换站AI审核.txt"
_AI_SHARED_CACHE = {"at": 0.0, "data": None}
_AI_SHARED_LOCK = threading.Lock()


def _ai_file_id(service):
    if _env("AI_RESULTS_DRIVE_FILE_ID"):
        return _env("AI_RESULTS_DRIVE_FILE_ID")
    try:
        res = service.files().list(
            q=f"name = '{_AI_FILE_NAME}' and trashed = false",
            fields="files(id)", pageSize=5).execute()
        fl = res.get("files") or []
        return fl[0]["id"] if fl else None
    except Exception:
        return None


def _ai_read_shared(service):
    """返回 {file_id: result}；无文件返回 {}；读不到/读坏/无服务返回 None。"""
    if _use_baidu_meta():
        try:
            _base, _pub, _pend = _baidu_dirs()
            text = _baidu_read_text(_base + _AI_FILE_NAME)
            if text is None:
                return {}
            out = {}
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                key = row.get("file_id")
                if key:
                    out[str(key)] = {"verdict": row.get("verdict"), "reason": row.get("reason") or "", "confidence": row.get("confidence"), "note": row.get("note") or "", "time": row.get("time") or ""}
            return out
        except Exception as e:
            print(f"[ai-shared] read failed: {e}", flush=True)
            return None
    if service is None:
        return None
    try:
        fid = _ai_file_id(service)
        if not fid:
            return {}
        from googleapiclient.http import MediaIoBaseDownload
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=fid))
        done = False
        while not done:
            _, done = dl.next_chunk()
        out = {}
        for line in buf.getvalue().decode("utf-8", errors="ignore").split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            key = row.get("file_id")
            if key:
                out[str(key)] = {
                    "verdict": row.get("verdict"),
                    "reason": row.get("reason") or "",
                    "confidence": row.get("confidence"),
                    "note": row.get("note") or "",
                    "time": row.get("time") or "",
                }
        return out
    except Exception as e:
        print(f"[ai-shared] read failed: {e}", flush=True)
        return None


def _ai_get_shared(service):
    """带 30 秒缓存的共享结论；缓存过期重读，读失败返回旧缓存（可能为 None）。"""
    now = time.time()
    with _AI_SHARED_LOCK:
        if _AI_SHARED_CACHE["data"] is not None and now - _AI_SHARED_CACHE["at"] < 30:
            return dict(_AI_SHARED_CACHE["data"])
    data = _ai_read_shared(service)
    if data is not None:
        with _AI_SHARED_LOCK:
            _AI_SHARED_CACHE["data"] = dict(data)
            _AI_SHARED_CACHE["at"] = now
        return dict(data)
    with _AI_SHARED_LOCK:
        old = _AI_SHARED_CACHE["data"]
    return dict(old) if old is not None else None


def _ai_shared_snapshot(service=None):
    """缓存新鲜时直接用缓存（不建 Drive 连接）；否则读共享文件，读失败用旧缓存。"""
    with _AI_SHARED_LOCK:
        fresh = (_AI_SHARED_CACHE["data"] is not None
                 and time.time() - _AI_SHARED_CACHE["at"] < 30)
        if fresh:
            return dict(_AI_SHARED_CACHE["data"])
        old = dict(_AI_SHARED_CACHE["data"]) if _AI_SHARED_CACHE["data"] is not None else None
    try:
        svc = service if service is not None else _ai_shared_service()
        if svc is None and not _use_baidu_meta():
            return old
        data = _ai_get_shared(svc)
        return data if data is not None else old
    except Exception:
        return old


def _ai_shared_service():
    """共享读写用的 Drive 服务；中转站无凭证时返回 None（调用方回落内存）。"""
    try:
        return drive_service()
    except Exception:
        return None


def _ai_write_shared(service, data):
    """整文件覆写共享结论；成功 True，失败 False，绝不抛异常。"""
    if _use_baidu_meta():
        try:
            _base, _pub, _pend = _baidu_dirs()
            body = "\n".join(json.dumps({"file_id": k, "time": v.get("time") or "", "verdict": v.get("verdict"), "reason": v.get("reason") or "", "confidence": v.get("confidence"), "note": v.get("note") or ""}, ensure_ascii=False) for k, v in data.items())
            return _baidu_write_text(_base + _AI_FILE_NAME, body)
        except Exception as e:
            print(f"[ai-shared] write failed: {e}", flush=True)
            return False
    if service is None:
        return False
    from googleapiclient.http import MediaIoBaseUpload
    body = "\n".join(json.dumps({"file_id": k, "time": v.get("time") or "",
                                  "verdict": v.get("verdict"), "reason": v.get("reason") or "",
                                  "confidence": v.get("confidence"), "note": v.get("note") or ""},
                                 ensure_ascii=False)
                     for k, v in data.items())
    media = MediaIoBaseUpload(io.BytesIO(body.encode("utf-8")), mimetype="text/plain", resumable=False)
    try:
        fid = _ai_file_id(service)
        if fid:
            service.files().update(fileId=fid, media_body=media).execute()
        else:
            meta = {"name": _AI_FILE_NAME, "mimeType": "text/plain"}
            service.files().create(body=meta, media_body=media, fields="id").execute()
        return True
    except Exception as e:
        print(f"[ai-shared] write failed: {e}", flush=True)
        return False



def _ai_attach_published(files, service=None):
    """给已发布文件补 ai_note：优先读共享文件（两站同一结论），
    共享读不到时回落本机自动发布快照。仅存过标注的文件才有 ai_note。"""
    with _AI_LOCK:
        snap = dict(_AI_PUBLISHED)
    try:
        shared = _ai_shared_snapshot(service)
    except Exception:
        shared = None
    for f in files or []:
        if isinstance(f, dict):
            note = None
            if shared:
                r = shared.get(f.get("id"))
                if r and r.get("note"):
                    note = r["note"]
            if note is None:
                r = snap.get(f.get("id"))
                if r and r.get("note"):
                    note = r["note"]
            if note:
                f["ai_note"] = note


def _auto_log(action, detail=""):
    """后台线程用的日志：不碰 request（后台线程没有请求上下文），
    直接写内存日志 + 云盘缓冲，AI 自动发布立即刷进云盘。"""
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    entry = {"time": now.strftime("%Y-%m-%d %H:%M"), "ip": "?", "loc": "", "device": "自动", "action": action, "detail": detail}
    ACCESS_LOG.appendleft(entry)
    line = f"{entry['time']} ?  自动 {action} {detail}\n"
    print(f"[access] {line.strip()}", flush=True)
    _DRIVE_LOG_BUFFER.append(line)
    try:
        _flush_drive_log()
    except Exception:
        pass


def _move_pending_to_published(service, file_id, name):
    """待审核→已发布：移动 + 清已发布缓存 + MP4 预览/faststart 后台任务。
    站长手动通过和 AI 自动发布共用，大小守卫由调用方先做。"""
    service.files().update(
        fileId=file_id,
        addParents=PUBLISHED_FOLDER_ID,
        removeParents=PENDING_FOLDER_ID,
    ).execute()
    _invalidate_pub_cache()
    try:
        if name.lower().endswith(".mp4"):
            threading.Thread(target=_preview_job, args=(file_id,), daemon=True).start()
    except Exception:
        pass
    try:
        if name.lower().endswith(".mp4"):
            threading.Thread(target=_faststart_job, args=(file_id,), daemon=True).start()
    except Exception:
        pass


def _auto_ai_process(file_id):
    """上传后自动审核并分流（只在后端跑）：
    通过 + 大小守卫过 → 自动发布；可疑/无法判断/调用失败 → 留待审核；
    大小守卫不过或移动失败 → 留待审核、理由写明。任何异常都不丢文件。"""
    with _AI_AUTO_LOCK:
        if file_id in _AI_AUTO_RUNNING:
            return
        _AI_AUTO_RUNNING.add(file_id)
    try:
        existing = _ai_get_result(file_id)
        if existing and existing.get("verdict") in ("通过", "可疑", "无法判断"):
            res = existing
        else:
            res = _ai_review_one(file_id)
        verdict = (res or {}).get("verdict")
        if verdict != "通过":
            # 失败/未配置统一记为“AI 未能审核”，绝不自动发布、绝不自动驳回
            if verdict in ("failed", "unconfigured"):
                _ai_set_result(file_id, "failed", "AI 未能审核")
            return
        if (file_id or "").startswith("bd:"):
            meta = _ai_pending_meta(file_id)
            if meta is None:
                return
            name = meta.get("name") or file_id
            expected = EXPECTED_SIZES.get(file_id)
            if expected:
                try:
                    actual = int(meta.get("size") or 0)
                except Exception:
                    actual = 0
                if actual != expected:
                    _ai_set_result(file_id, "通过", res.get("reason") or "内容初审通过",
                                   res.get("confidence"),
                                   f"大小未对齐暂未自动发布：云盘里只有 {_fmt_size(actual)}，上传时应为 {_fmt_size(expected)}，请等上传完成后再人工通过")
                    return
            with _AI_LOCK:
                _AI_PUBLISHED[file_id] = dict(_AI_RESULTS.get(file_id) or {})
            if not _baidu_move_pending_to_published(file_id[3:], name):
                _ai_set_result(file_id, "通过", res.get("reason") or "内容初审通过",
                               res.get("confidence"), "自动发布失败（移动失败），请人工通过")
                return
            _auto_log("AI 自动发布", name)
            return
        service = drive_service()
        if service is None:
            _ai_set_result(file_id, "failed", "AI 未能审核")
            return
        try:
            meta = _admin_file_or_403(service, file_id)
        except Exception:
            meta = None
        if meta is None:
            return
        name = meta.get("name") or file_id
        expected = EXPECTED_SIZES.get(file_id)
        if expected:
            try:
                actual = int(meta.get("size") or 0)
            except Exception:
                actual = 0
            if actual != expected:
                _ai_set_result(file_id, "通过", res.get("reason") or "内容初审通过",
                               res.get("confidence"),
                               f"大小未对齐暂未自动发布：云盘里只有 {_fmt_size(actual)}，上传时应为 {_fmt_size(expected)}，请等上传完成后再人工通过")
                return
        with _AI_LOCK:
            # 注意：这里不能调 _ai_get_result（它自己也要拿 _AI_LOCK，非重入锁会死锁）
            _AI_PUBLISHED[file_id] = dict(_AI_RESULTS.get(file_id) or {})
        try:
            _move_pending_to_published(service, file_id, name)
        except Exception:
            _ai_set_result(file_id, "通过", res.get("reason") or "内容初审通过",
                           res.get("confidence"), "自动发布失败（移动失败），请人工通过")
            return
        _auto_log("AI 自动发布", name)
    except Exception as e:
        print(f"[auto-ai] failed for {file_id}: {e}", flush=True)
    finally:
        with _AI_AUTO_LOCK:
            _AI_AUTO_RUNNING.discard(file_id)


def _auto_ai_trigger(file_id):
    if not file_id:
        return
    threading.Thread(target=_auto_ai_process, args=(file_id,), daemon=True).start()


def _auto_ai_trigger_relay(file_id):
    """中转站直传完成时，后台把触发请求发给后端（带熟人门 cookie）；
    后端无响应也不影响本次上传返回。"""
    if not file_id:
        return
    try:
        cookies = {}
        if "exchange_access" in request.cookies:
            cookies["exchange_access"] = request.cookies["exchange_access"]
        backend = _relay_backend()
    except Exception:
        return

    def _post():
        try:
            import requests as _rq
            _rq.post(backend + "/auto_ai_trigger", cookies=cookies, data={"file_id": file_id}, timeout=30)
        except Exception:
            pass

    threading.Thread(target=_post, daemon=True).start()


def _ai_configured():
    return bool(_env("DEEPSEEK_API_KEY"))


def _ai_now_str():
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")


def _ai_get_result(file_id, service=None):
    """先读共享文件（30 秒缓存），读不到/无此文件时回落本机内存；
    云盘不可用时只读内存，绝不报错。"""
    try:
        shared = _ai_shared_snapshot(service)
    except Exception:
        shared = None
    if shared:
        r = shared.get(file_id)
        if r:
            return dict(r)
    with _AI_LOCK:
        r = _AI_RESULTS.get(file_id)
        return dict(r) if r else None


def _ai_set_result(file_id, verdict, reason, confidence=None, note=""):
    """写本机内存 + 立即刷新共享缓存 + 尽力写回共享文件（先重读再合并单条）；
    写回失败只打印日志，绝不阻塞审核，结论仍在内存可见。"""
    row = {"verdict": verdict, "reason": reason, "confidence": confidence,
           "note": note, "time": _ai_now_str()}
    with _AI_LOCK:
        _AI_RESULTS[file_id] = dict(row)
    with _AI_SHARED_LOCK:
        if _AI_SHARED_CACHE["data"] is not None:
            _AI_SHARED_CACHE["data"][file_id] = dict(row)
            _AI_SHARED_CACHE["at"] = time.time()
    try:
        svc = _ai_shared_service()
        if svc is not None or _use_baidu_meta():
            base = _ai_read_shared(svc)
            if base is None:
                with _AI_SHARED_LOCK:
                    base = dict(_AI_SHARED_CACHE["data"]) if _AI_SHARED_CACHE["data"] is not None else {}
            base[file_id] = dict(row)
            if _ai_write_shared(svc, base):
                with _AI_SHARED_LOCK:
                    _AI_SHARED_CACHE["data"] = dict(base)
                    _AI_SHARED_CACHE["at"] = time.time()
    except Exception as e:
        print(f"[ai-shared] set write failed: {e}", flush=True)


def _ai_fetch_head(service, file_id, limit):
    """只取文件前 limit 字节（小分块、到量即停），不把整个大文件拉下来。"""
    from googleapiclient.http import MediaIoBaseDownload
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=file_id), chunksize=256 * 1024)
    done = False
    while not done:
        _, done = dl.next_chunk()
        if buf.tell() >= limit:
            break
    return buf.getvalue()[:limit]


def _ai_fetch_baidu_head(fs_id, limit):
    """经 dlink 取百度文件前 limit 字节（流式、到量即停）；失败抛异常。"""
    import requests as _rq
    meta = _baidu_meta(fs_id)
    dlink = meta.get("dlink") or ""
    if not dlink:
        raise RuntimeError("baidu dlink missing")
    tok = _baidu_token()
    r = _rq.get(dlink, params={"access_token": tok},
                headers={"User-Agent": "pan.baidu.com"}, stream=True, timeout=60)
    data = bytearray()
    for chunk in r.iter_content(chunk_size=65536):
        if not chunk:
            continue
        data.extend(chunk)
        if len(data) > limit:
            break
    return bytes(data)


def _ai_image_data_url(service, meta):
    """取图并本地压成 JPEG data URL。返回 (data_url, error)，
    error: None / "too_large"（超上限，不识图）/ "fetch_failed" / "decode_failed"。"""
    try:
        size = int(meta.get("size") or 0)
    except Exception:
        size = 0
    if size > _AI_IMAGE_MAX_BYTES:
        return None, "too_large"
    try:
        if meta.get("source") == "baidu":
            raw = _ai_fetch_baidu_head(meta["id"][3:], _AI_IMAGE_MAX_BYTES + 1)
        else:
            raw = _ai_fetch_head(service, meta["id"], _AI_IMAGE_MAX_BYTES + 1)
    except Exception:
        return None, "fetch_failed"
    if not raw or len(raw) > _AI_IMAGE_MAX_BYTES:
        return None, "too_large" if raw else "fetch_failed"
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "PA"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[3])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        im.thumbnail((_AI_IMAGE_MAX_SIDE, _AI_IMAGE_MAX_SIDE), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), None
    except Exception:
        return None, "decode_failed"


def _ai_call_deepseek_vision(data_url, filename, size):
    """调 DeepSeek 识图（OpenAI 兼容 /chat/completions，模型 deepseek-flash）；
    60 秒超时、失败重试一次；任何失败抛异常，调用方一律记“AI 未能审核”。"""
    import requests as _rq
    base = (_env("DEEPSEEK_API_BASE") or "https://api.deepseek.com").rstrip("/")
    model = _env("DEEPSEEK_VISION_MODEL") or "deepseek-flash"
    key = _env("DEEPSEEK_API_KEY") or ""
    system = ("你是文件分享站的初审员。只看图片画面内容，按站内通用口径判断"
              "是否可能违规：色情、暴力血腥、极端主义、毒品、赌博诈骗、恶意软件、"
              "明显侵权盗版。只输出严格 JSON，形如 {\"verdict\": \"通过|可疑|无法判断\", "
              "\"reason\": \"一句话理由\", \"confidence\": 0.0到1.0}，不要输出其他文字。")
    user_text = ("请审查这张图片的画面内容。文件名：" + filename
                 + "，大小：" + _fmt_size(size)
                 + "。只输出严格 JSON，不要输出其他文字。")
    for _ in range(2):
        try:
            r = _rq.post(base + "/chat/completions",
                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                         json={"model": model,
                               "messages": [{"role": "system", "content": system},
                                            {"role": "user", "content": [
                                                {"type": "text", "text": user_text},
                                                {"type": "image_url", "image_url": {"url": data_url}}]}],
                               "temperature": 0.2},
                         timeout=60)
            r.raise_for_status()
            content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                content = content[start:end + 1]
            data = json.loads(content)
            verdict = data.get("verdict") or "无法判断"
            if verdict not in ("通过", "可疑", "无法判断"):
                verdict = "无法判断"
            return {"verdict": verdict,
                    "reason": str(data.get("reason") or "未给出理由")[:200],
                    "confidence": data.get("confidence")}
        except Exception:
            continue
    raise RuntimeError("deepseek vision failed")


def _ai_collect_evidence(service, meta):
    """返回送审证据。文本抽前部；压缩包只在 <=20MB 时整包列清单；
    图片走识图（本地压图后发 data URL）；视频/音频/超大压缩包只按文件名初审，
    并明确标注。meta["source"] 为 "baidu" 时走 dlink 取字节，否则走谷歌下载。"""
    name = meta.get("name") or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    base = {"name": name, "size": meta.get("size"), "ext": ext}
    if ext in _AI_TEXT_EXTS:
        try:
            if meta.get("source") == "baidu":
                raw = _ai_fetch_baidu_head(meta["id"][3:], _AI_TEXT_LIMIT + 1024)
            else:
                raw = _ai_fetch_head(service, meta["id"], _AI_TEXT_LIMIT + 1024)
            text = raw.decode("utf-8", errors="ignore")[:_AI_TEXT_LIMIT]
            return dict(base, kind="text", excerpt=text)
        except Exception:
            return dict(base, kind="text", excerpt="", note="正文读取失败，仅按文件名初审")
    if ext == "zip":
        try:
            size = int(meta.get("size") or 0)
        except Exception:
            size = 0
        if size > _AI_ZIP_MAX_BYTES:
            return dict(base, kind="name_only", note="压缩包过大未列清单，仅文件名初审，未看内容")
        try:
            import zipfile
            if meta.get("source") == "baidu":
                raw = _ai_fetch_baidu_head(meta["id"][3:], _AI_ZIP_MAX_BYTES)
            else:
                raw = _ai_fetch_head(service, meta["id"], _AI_ZIP_MAX_BYTES)
            zf = zipfile.ZipFile(io.BytesIO(raw))
            names = [i.filename for i in zf.infolist()[:_AI_ZIP_NAME_LIMIT]]
            return dict(base, kind="zip", entries=names)
        except Exception:
            return dict(base, kind="zip", entries=[], note="压缩包清单读取失败，仅按文件名初审")
    if ext in _AI_IMAGE_EXTS:
        data_url, err = _ai_image_data_url(service, meta)
        if err == "too_large":
            return dict(base, kind="image_too_large", note="图片过大未能识图")
        if err is not None:
            return dict(base, kind="image_failed", note="图片读取失败，未看图初审")
        return dict(base, kind="image", data_url=data_url, note="已看图初审")
    return dict(base, kind="name_only", note="仅文件名初审，未看内容")


def _ai_call_deepseek(prompt):
    """调 DeepSeek chat 接口；30 秒超时、失败重试一次；密钥和文件正文绝不写日志。"""
    import requests as _rq
    base = (_env("DEEPSEEK_API_BASE") or "https://api.deepseek.com").rstrip("/")
    model = _env("DEEPSEEK_MODEL") or "deepseek-chat"
    key = _env("DEEPSEEK_API_KEY") or ""
    system = ("你是文件分享站的初审员。只根据提供的文件名、类型和摘录判断内容是否"
              "可能违规：色情、暴力血腥、极端主义、毒品、赌博诈骗、恶意软件、明显侵权"
              "盗版。只输出严格 JSON，形如 {\"verdict\": \"通过|可疑|无法判断\", "
              "\"reason\": \"一句话理由\", \"confidence\": 0.0到1.0}，不要输出其他文字。")
    last = None
    for _ in range(2):
        try:
            r = _rq.post(base + "/chat/completions",
                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                         json={"model": model,
                               "messages": [{"role": "system", "content": system},
                                            {"role": "user", "content": prompt}],
                               "temperature": 0.2},
                         timeout=30)
            r.raise_for_status()
            content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                content = content[start:end + 1]
            data = json.loads(content)
            verdict = data.get("verdict") or "无法判断"
            if verdict not in ("通过", "可疑", "无法判断"):
                verdict = "无法判断"
            return {"verdict": verdict,
                    "reason": str(data.get("reason") or "未给出理由")[:200],
                    "confidence": data.get("confidence")}
        except Exception as e:
            last = e
    raise RuntimeError("deepseek failed")


def _ai_pending_meta(file_id):
    """取待审核文件元数据（谷歌/百度两源归一化），不在待审核里返回 None。
    谷歌走 Drive 待审核文件夹；百度走应用待审核目录（_baidu_area 判定）。"""
    if file_id.startswith("bd:"):
        if not _baidu_enabled():
            return None
        try:
            raw = _baidu_meta(file_id[3:])
        except Exception:
            return None
        if _baidu_area(raw.get("path") or "") != "pending":
            return None
        return {"id": file_id, "name": raw.get("server_filename") or file_id,
                "size": raw.get("size"), "source": "baidu"}
    service = drive_service()
    if service is None:
        return None
    try:
        meta = _admin_file_or_403(service, file_id)
    except Exception:
        return None
    if meta is None:
        return None
    meta = dict(meta)
    meta["source"] = "google"
    return meta


def _ai_review_one(file_id):
    """对一个待审核文件做初审并存结果；图片走识图真看图，视频/音频仍只按
    文件名审，文字文档仍抽正文审；任何调用失败只记“AI 未能审核”，绝不因
    调不通就自动发布、也绝不自动驳回，更不许悄悄降级回文件名“通过”。"""
    if not _ai_configured():
        return {"verdict": "unconfigured", "reason": "AI 审核未配置"}
    meta = _ai_pending_meta(file_id)
    if meta is None:
        _ai_set_result(file_id, "failed", "该文件不在待审核里")
        return _ai_get_result(file_id)
    service = None if meta.get("source") == "baidu" else drive_service()
    ev = _ai_collect_evidence(service, meta)
    if ev["kind"] == "image_too_large":
        # 超上限：不下载、不识图、不自动发布，留待审核等站长人工看
        _ai_set_result(file_id, "无法判断", "图片过大未能识图", None, "图片过大未能识图")
        return _ai_get_result(file_id)
    if ev["kind"] == "image_failed":
        _ai_set_result(file_id, "failed", "AI 未能审核")
        return _ai_get_result(file_id)
    if ev["kind"] == "image":
        try:
            res = _ai_call_deepseek_vision(ev["data_url"], ev["name"], ev.get("size"))
        except Exception:
            _ai_set_result(file_id, "failed", "AI 未能审核")
            return _ai_get_result(file_id)
        _ai_set_result(file_id, res["verdict"], res["reason"], res.get("confidence"), "已看图初审")
        return _ai_get_result(file_id)
    parts = ["文件名：" + ev["name"],
             "大小：" + _fmt_size(ev.get("size")),
             "类型：" + (ev.get("ext") or "未知")]
    if ev["kind"] == "text" and ev.get("excerpt"):
        parts.append("正文摘录（前部）：\n" + ev["excerpt"])
    elif ev["kind"] == "zip" and ev.get("entries"):
        parts.append("压缩包内文件清单：\n" + "\n".join(ev["entries"]))
    try:
        res = _ai_call_deepseek("\n".join(parts))
    except Exception:
        _ai_set_result(file_id, "failed", "AI 未能审核")
        return _ai_get_result(file_id)
    note = ev.get("note") or ""
    if ev["kind"] == "name_only":
        note = ev.get("note") or "仅文件名初审，未看内容"
    _ai_set_result(file_id, res["verdict"], res["reason"], res.get("confidence"), note)
    return _ai_get_result(file_id)


@app.route("/admin/announcement")
def admin_announcement():
    if not _is_admin():
        return "只有站长能改公告", 403
    if _is_relay():
        return _proxy_to_backend("/admin/announcement")
    import html as _html
    service = drive_service()
    raw = _read_announcement_raw(service) if service else None
    title = (raw.get("title") if raw else "") or ANNOUNCEMENT_DEFAULT_TITLE
    body = raw.get("body") if raw else ANNOUNCEMENT_DEFAULT_BODY
    msg = request.args.get("msg", "")
    return render_template_string(
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>公告管理 - 交换站</title><style>" + BASE_CSS + "</style></head><body><div class=\"wrap\" id=\"admin-main\">"
        "<h1>公告管理</h1><p class=\"note\">这里改进站公告，保存后立刻生效，不用改代码。正文清空后保存，公告就不再弹出（等于关掉公告）。只有站长能改这里。</p>"
        + (f"<p class=\"note\">{_html.escape(msg)}</p>" if msg else "") +
        "<div class=\"card section\"><form method=\"post\" action=\"/admin/announcement\">"
        f"<input class=\"textin\" type=\"text\" name=\"title\" maxlength=\"100\" value=\"{_html.escape(title, quote=True)}\" placeholder=\"公告标题\" required>"
        f"<textarea class=\"textin\" name=\"body\" rows=\"6\" maxlength=\"2000\" placeholder=\"公告正文，空着就是关掉公告\" style=\"width:100%\">{_html.escape(body)}</textarea>"
        "<button class=\"btn primary\">保存公告</button></form></div>"
        "<p><a href=\"/admin\">回待审核</a> · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div>" + ADMIN_PJAX_SCRIPT + "</body></html>"
    )


@app.route("/admin/announcement", methods=["POST"])
def admin_announcement_save():
    if not _is_admin():
        return "只有站长能改公告", 403
    if _is_relay():
        return _proxy_post_to_backend("/admin/announcement")
    service = drive_service()
    if service is None:
        return redirect("/admin/announcement?msg=云盘暂未连接，保存失败")
    title = (request.form.get("title") or "").strip() or ANNOUNCEMENT_DEFAULT_TITLE
    body = request.form.get("body") or ""
    ok = _write_announcement(service, title, body)
    _log("改公告", "已保存" if ok else "保存失败")
    return redirect("/admin/announcement?msg=" + ("已保存，立即生效" if ok else "保存失败，请重试"))


@app.route("/admin/ai_review", methods=["POST"])
def admin_ai_review():
    if not _is_manager():
        return "请先在首页输入站长口令", 403
    if _is_relay():
        return _proxy_post_to_backend("/admin/ai_review")
    _ai_review_one(request.form.get("file_id", ""))
    return redirect("/admin")


@app.route("/admin/ai_review_all", methods=["POST"])
def admin_ai_review_all():
    if not _is_manager():
        return "请先在首页输入站长口令", 403
    if _is_relay():
        return _proxy_post_to_backend("/admin/ai_review_all")
    if "all" in _AI_ALL_RUNNING:
        return redirect("/admin")

    def _run():
        try:
            service = drive_service()
            files = list_folder(service, PENDING_FOLDER_ID) if service else []
            for f in files:
                try:
                    _ai_review_one(f["id"])
                except Exception:
                    pass
        finally:
            _AI_ALL_RUNNING.discard("all")

    _AI_ALL_RUNNING.add("all")
    import threading as _th
    _th.Thread(target=_run, daemon=True).start()
    return redirect("/admin")




@app.route("/auto_ai_trigger", methods=["POST"])
def auto_ai_trigger():
    """中转站直传完成后来触发后端自动审核；需过熟人门，绝不自动发布失败项。"""
    if _is_relay():
        return _proxy_post_to_backend("/auto_ai_trigger")
    code = _env("EXCHANGE_CODE")
    if not _has_gate(code, "exchange_access"):
        return jsonify({"ok": False}), 403
    fid = (request.get_json(silent=True) or {}).get("file_id") or request.form.get("file_id", "")
    _auto_ai_trigger(fid)
    return jsonify({"ok": True})


def _preview_name(file_id):
    return "_preview_" + file_id + ".mp4"


def _find_preview(service, file_id):
    try:
        res = service.files().list(
            q="'" + PUBLISHED_FOLDER_ID + "' in parents and name = '" + _preview_name(file_id) + "' and trashed = false",
            fields="files(id,name)", pageSize=10).execute()
        files = res.get("files") or []
        return files[0]["id"] if files else None
    except Exception:
        return None


_PREVIEW_RUNNING = set()


def _preview_job(file_id):
    if file_id in _PREVIEW_RUNNING:
        return
    _PREVIEW_RUNNING.add(file_id)
    try:
        _preview_job_inner(file_id)
    finally:
        _PREVIEW_RUNNING.discard(file_id)


def _preview_job_inner(file_id):
    """Background low-res preview: 480p 800k + faststart, best-effort only.
    Any failure leaves the original untouched; playback falls back to original."""
    try:
        if shutil.which("ffmpeg") is None:
            print("preview skipped, no ffmpeg: " + file_id)
            return
        service = drive_service()
        if service is None:
            return
        meta = get_file(service, file_id) or {}
        if PUBLISHED_FOLDER_ID not in (meta.get("parents") or []):
            if not _is_published_parent(service, meta.get("parents") or []):
                return
        old = _find_preview(service, file_id)
        if old:
            try:
                service.files().update(fileId=old, body={"trashed": True}).execute()
            except Exception:
                pass
        tmpdir = tempfile.mkdtemp(prefix="preview-")
        try:
            orig = os.path.join(tmpdir, "orig.mp4")
            out = os.path.join(tmpdir, "preview.mp4")
            from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
            with open(orig, "wb") as fh:
                dl = MediaIoBaseDownload(fh, service.files().get_media(fileId=file_id))
                done = False
                while not done:
                    _, done = dl.next_chunk()
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", orig, "-vf", "scale=854:480",
                 "-b:v", "800k", "-b:a", "96k", "-preset", "veryfast",
                 "-movflags", "+faststart", "-c:v", "libx264", "-c:a", "aac", out],
                capture_output=True, timeout=1800)
            if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
                print("preview transcode failed: " + file_id)
                return
            media = MediaFileUpload(out, mimetype="video/mp4", resumable=True)
            service.files().create(
                body={"name": _preview_name(file_id), "parents": [PUBLISHED_FOLDER_ID]},
                media_body=media).execute()
            print("preview done " + file_id)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception as e:
        print("preview failed " + file_id + ": " + str(e))



@app.route("/preview/<file_id>")
def preview(file_id):
    """Low-res preview stream: resolves original -> preview file, then streams it
    through the normal view logic (gate + Range). 404 if no preview exists yet."""
    if _is_relay():
        return _proxy_to_backend("/preview/" + file_id)
    service = drive_service()
    if service is None:
        return "云盘暂未连接", 503
    meta = get_file(service, file_id) or {}
    if PUBLISHED_FOLDER_ID not in (meta.get("parents") or []):
        # Also allow published subfolders via _is_published_parent
        if not _is_published_parent(service, meta.get("parents") or []):
            return "文件不存在或无权访问", 404
    pid = _find_preview(service, file_id)
    if not pid:
        return "低清预览还没生成", 404
    return view(pid)


@app.route("/preview_status/<file_id>")
def preview_status(file_id):
    """Lightweight JSON: does a low-res preview exist for this published video?"""
    if _is_relay():
        return _proxy_to_backend("/preview_status/" + file_id)
    service = drive_service()
    if service is None:
        return jsonify({"has_preview": False})
    pid = _find_preview(service, file_id)
    if not pid:
        try:
            meta = get_file(service, file_id) or {}
            _pub = PUBLISHED_FOLDER_ID in (meta.get("parents") or []) or _is_published_parent(service, meta.get("parents") or [])
            if _pub and (meta.get("name") or "").lower().endswith(".mp4"):
                threading.Thread(target=_preview_job, args=(file_id,), daemon=True).start()
        except Exception:
            pass
    return jsonify({"has_preview": bool(pid)})


def _read_boxes(f, start, end):
    boxes = []
    f.seek(start)
    while f.tell() + 8 <= end:
        pos = f.tell()
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        size, typ = struct.unpack(">I4s", hdr)
        typ = typ.decode("latin1")
        hdr_len = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            hdr_len = 16
        elif size == 0:
            size = end - pos
        boxes.append((typ, pos, size, hdr_len))
        if size <= 0:
            break
        f.seek(pos + size)
    return boxes

def _patch_moov(moov_bytes, delta):
    # Walk moov recursively, patch stco (32-bit) and co64 (64-bit) entries.
    # moov_bytes includes its own 8/16-byte header.
    def walk(buf, base, end):
        i = base
        while i + 8 <= end:
            size, typ = struct.unpack(">I4s", buf[i:i+8])
            typ = typ.decode("latin1")
            hdr_len = 8
            if size == 1:
                size = struct.unpack(">Q", buf[i+8:i+16])[0]
                hdr_len = 16
            elif size == 0:
                size = end - i
            if typ == "stco":
                # version/flags 4 bytes, then entry_count 4 bytes, then entries
                cnt_off = i + hdr_len + 4
                cnt = struct.unpack(">I", buf[cnt_off:cnt_off+4])[0]
                for k in range(cnt):
                    off = cnt_off + 4 + k * 4
                    v = struct.unpack(">I", buf[off:off+4])[0]
                    struct.pack_into(">I", buf, off, (v + delta) & 0xFFFFFFFF)
            elif typ == "co64":
                cnt_off = i + hdr_len + 4
                cnt = struct.unpack(">I", buf[cnt_off:cnt_off+4])[0]
                for k in range(cnt):
                    off = cnt_off + 4 + k * 8
                    v = struct.unpack(">Q", buf[off:off+8])[0]
                    struct.pack_into(">Q", buf, off, v + delta)
            elif typ in ("moov", "trak", "mdia", "minf", "stbl", "edts", "dinf", "udta", "meta"):
                # meta has 4 extra bytes after header before children
                child_base = i + hdr_len + (4 if typ == "meta" else 0)
                walk(buf, child_base, i + size)
            if size <= 0:
                break
            i += size
    buf = bytearray(moov_bytes)
    # moov header itself
    size, typ = struct.unpack(">I4s", buf[0:8])
    hdr_len = 16 if size == 1 else 8
    walk(buf, hdr_len, len(buf))
    return bytes(buf)

def faststart(in_path, out_path):
    with open(in_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        total = f.tell()
        boxes = _read_boxes(f, 0, total)
    types = [b[0] for b in boxes]
    if "moov" not in types or "mdat" not in types:
        return False, "no moov/mdat"
    moov_idx = types.index("moov")
    mdat_idx = types.index("mdat")
    if moov_idx < mdat_idx:
        return False, "already faststart"
    # Read moov
    _, moov_pos, moov_size, _ = boxes[moov_idx]
    with open(in_path, "rb") as f:
        f.seek(moov_pos)
        moov_bytes = f.read(moov_size)
    patched = _patch_moov(moov_bytes, moov_size)
    # New order: ftyp first (if present), then moov, then others in original order
    order = []
    for idx, b in enumerate(boxes):
        if idx == moov_idx:
            continue
        order.append(idx)
    # Put ftyp first
    ftyp_idx = types.index("ftyp") if "ftyp" in types else None
    new_order = []
    if ftyp_idx is not None:
        new_order.append(ftyp_idx)
        order.remove(ftyp_idx)
    # Write
    with open(in_path, "rb") as fin, open(out_path, "wb") as fout:
        # ftyp
        if ftyp_idx is not None:
            _, pos, size, _ = boxes[ftyp_idx]
            fin.seek(pos)
            remaining = size
            while remaining > 0:
                chunk = fin.read(min(1024*1024, remaining))
                if not chunk:
                    break
                fout.write(chunk)
                remaining -= len(chunk)
        fout.write(patched)
        for idx in order:
            _, pos, size, _ = boxes[idx]
            fin.seek(pos)
            remaining = size
            while remaining > 0:
                chunk = fin.read(min(1024*1024, remaining))
                if not chunk:
                    break
                fout.write(chunk)
                remaining -= len(chunk)
    return True, "moved"

def _faststart_job(file_id):
    """Background faststart after approval: download, move moov, verify, re-upload.
    Best-effort only: any failure leaves the original file untouched."""
    try:
        service = drive_service()
        if service is None:
            return
        meta = get_file(service, file_id) or {}
        parents = meta.get("parents") or []
        if PUBLISHED_FOLDER_ID not in parents:
            return
        tmpdir = tempfile.mkdtemp(prefix="faststart-")
        try:
            orig = os.path.join(tmpdir, "orig.mp4")
            out = os.path.join(tmpdir, "fast.mp4")
            from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
            with open(orig, "wb") as fh:
                dl = MediaIoBaseDownload(fh, service.files().get_media(fileId=file_id))
                done = False
                while not done:
                    _, done = dl.next_chunk()
            moved, _reason = faststart(orig, out)
            if not moved:
                return
            if os.path.getsize(out) != os.path.getsize(orig):
                return
            media = MediaFileUpload(out, mimetype="video/mp4", resumable=True)
            service.files().update(fileId=file_id, media_body=media).execute()
            print("faststart done " + file_id)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception as e:
        print("faststart failed " + file_id + ": " + str(e))


@app.route("/admin/approve", methods=["POST"])
def approve():
    if not _is_manager():
        return "请先在首页输入站长口令", 403
    if _is_relay():
        return _proxy_post_to_backend("/admin/approve")
    file_id = request.form.get("file_id", "")
    if (file_id or "").startswith("bd:"):
        if not _baidu_enabled():
            return "百度源暂未连接", 503
        meta = _ai_pending_meta(file_id)
        if meta is None:
            return "该文件不在待审核里，无法通过", 403
        _name = meta.get("name", file_id)
        _expected = EXPECTED_SIZES.get(file_id)
        if _expected:
            try:
                _actual = int(meta.get("size") or 0)
            except Exception:
                _actual = 0
            if _actual != _expected:
                return f"该文件还没传完，不能通过：云盘里只有 {_fmt_size(_actual)}，上传时应为 {_fmt_size(_expected)}，请等上传完成后再通过", 409
        if not _baidu_move_pending_to_published(file_id[3:], _name):
            return "移动到已发布失败，请重试", 503
        _log("审核通过", _name)
        return redirect("/admin")
    service = drive_service()
    if service is None:
        return "云盘暂未连接", 503
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
    _move_pending_to_published(service, file_id, _name)
    _log("审核通过", _name)
    return redirect("/admin")


@app.route("/admin/reject", methods=["POST"])
def reject():
    if not _is_manager():
        return "请先在首页输入站长口令", 403
    if _is_relay():
        return _proxy_post_to_backend("/admin/reject")
    file_id = request.form.get("file_id", "")
    if (file_id or "").startswith("bd:"):
        if not _baidu_enabled():
            return "百度源暂未连接", 503
        meta = _ai_pending_meta(file_id)
        if meta is None:
            return "该文件不在待审核里，无法处理", 403
        try:
            _raw = _baidu_meta(file_id[3:])
            _baidu_delete_path(_raw.get("path") or "")
        except Exception:
            return "删除失败，请重试", 503
        _log("审核不通过", meta.get("name", file_id))
        return redirect("/admin")
    service = drive_service()
    if service is None:
        return "云盘暂未连接", 503
    if not file_id or _admin_file_or_403(service, file_id) is None:
        return "该文件不在待审核里，无法处理", 403
    _name = (get_file(service, file_id) or {}).get("name", file_id)
    service.files().update(fileId=file_id, body={"trashed": True}).execute()
    _log("审核不通过", _name)
    return redirect("/admin")


@app.route("/admin/published")
def admin_published():
    if not _is_manager():
        return "请先在首页输入站长口令", 403
    if _is_relay():
        _rf = _relay_files()
        if _rf is None:
            try:
                _bf, _bfo = _baidu_published()
                files, _folders = _merge_mixed([], [], _bf, _bfo)
            except Exception:
                files = []
        else:
            files = _rf.get("published", [])
    else:
        files, _fo, _notice = _cached_published(drive_service())
    return render_template_string(
        ADMIN_PUBLISHED_PAGE, files=files, drive_configured=True, is_secondary=_is_secondary()
    )


@app.route("/admin/delete", methods=["POST"])
def admin_delete():
    if not _is_manager():
        return "请先在首页输入站长口令", 403
    if _is_relay():
        return _proxy_post_to_backend("/admin/delete")
    service = drive_service()
    if service is None:
        return "云盘暂未连接", 503
    file_id = request.form.get("file_id", "")
    if not file_id:
        return "没有文件", 403
    if (file_id or "").startswith("bd:"):
        if not _baidu_enabled():
            return "百度源暂未连接", 503
        try:
            _meta = _baidu_meta(file_id[3:])
        except Exception:
            return "文件不存在或无权访问", 404
        if _baidu_area(_meta.get("path") or "") != "published":
            return "该文件不在已发布里，无法删除", 403
        try:
            _baidu_delete_path(_meta.get("path") or "")
        except Exception:
            return "删除失败，请重试", 503
        _invalidate_pub_cache()
        _log("删除已发布", _meta.get("server_filename", file_id))
        return redirect("/admin/published")
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




@app.route("/admin/comments")
def admin_comments():
    if not _is_manager():
        return "请先在首页输入站长口令", 403
    import html as _html
    service = drive_service()
    items = _read_comments(service) if service else []
    names = {}
    try:
        files, _folders = _cached_published(service) if service else ([], [])
        names = {f.get("id"): f.get("name") for f in files}
    except Exception:
        names = {}
    rows = ""
    for it in items:
        fname = names.get(it["file_id"], "（文件已删除）")
        rows += (
            f"<div class=\"logrow\"><div class=\"fname\">{_html.escape(it['text'])}</div>"
            f"<div class=\"fmeta\"><span>{_html.escape(it['author'])}</span><span>·</span>"
            f"<span>{_html.escape(fname)}</span><span>·</span><span>{it['time']}</span></div>"
            f"<form method=\"post\" action=\"/admin/comments/delete\" style=\"margin-top:6px\">"
            f"<input type=\"hidden\" name=\"id\" value=\"{it['id']}\">"
            f"<button class=\"btn danger\">删除这条留言</button></form></div>"
        )
    if not rows:
        rows = "<p class=\"empty\">目前还没有留言。</p>"
    return render_template_string(
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>留言管理 - 交换站</title><style>" + BASE_CSS + "</style></head><body><div class=\"wrap\" id=\"admin-main\">"
        "<h1>留言管理</h1><p class=\"note\">留言由过了熟人门的访客所发，昵称不填就是匿名；站长删了就不再显示。文件已删除的留言会标出来，删了也不报错。</p>"
        "<div class=\"card section\">" + rows + "</div>"
        "<p><a href=\"/admin\">回待审核</a> · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div>" + ADMIN_PJAX_SCRIPT + "</body></html>"
    )


@app.route("/admin/comments/delete", methods=["POST"])
def admin_comments_delete():
    if not _is_manager():
        return "请先在首页输入站长口令", 403
    if _is_relay():
        return _proxy_post_to_backend("/admin/comments/delete")
    service = drive_service()
    if service is None:
        return redirect("/admin/comments")
    cid = request.form.get("id") or ""
    items = _read_comments(service)
    items = [it for it in items if it["id"] != cid]
    _write_comments(service, list(reversed(items)))
    return redirect("/admin/comments")


@app.route("/admin/feedback")
def admin_feedback():
    if not _is_manager():
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
        "<title>意见管理 - 交换站</title><style>" + BASE_CSS + "</style></head><body><div class=\"wrap\" id=\"admin-main\">"
        "<h1>意见管理</h1><p class=\"note\">站长区里能改状态，改完后意见区里大家都能看见已解决 / 未解决。</p>"
        "<div class=\"card section\">" + rows + "</div>"
        "<p><a href=\"/admin\">回待审核</a> · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div>" + ADMIN_PJAX_SCRIPT + "</body></html>"
    )


@app.route("/admin/logs")
def admin_logs():
    if not _is_manager():
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
        "<title>访问日志 - 交换站</title><style>" + BASE_CSS + "</style></head><body><div class=\"wrap\" id=\"admin-main\">"
        "<h1>访问日志</h1><p class=\"note\">只记时间、IP、设备类型、动作和文件名，不记口令。长期记录存在云盘根目录的 交换站访问日志.txt 里（不在待审核/已发布里，熟人看不到），服务休眠或重启也不会丢。</p>"
        "<div class=\"card section\">" + rows + "</div>"
        "<p><a href=\"/admin\">回待审核</a> · <a href=\"/\">回首页</a> · <a href=\"/logout\">退出</a></p></div>" + ADMIN_PJAX_SCRIPT + "</body></html>"
    )


@app.route("/thumb/<file_id>")
def thumb(file_id):
    """图片缩略图：只给已发布（熟人）或待审核（站长）看，权限和预览一致。
    狠压版（2026-11-03）：手机相册格子约 120 像素宽，缩略图只留 192 像素长边、
    JPEG 质量 45，目标是手机上能认出图片是什么就行，不追求清晰；点开预览时
    仍走 /view 看原图。没有 Pillow 时原图返回，浏览器按 CSS 缩放。"""
    if _is_relay():
        return _proxy_to_backend(f"/thumb/{file_id}")
    if file_id.startswith("bd:"):
        return _baidu_thumb(file_id)
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
        if not _is_manager():
            return "该文件尚未发布", 403
    else:
        owner = _private_owner(service, meta)
        if owner is None or (owner != _account_gate() and not _private_is_admin()):
            return "文件不存在或无权访问", 404
    if (meta.get("mimeType") or "").startswith("video/"):
        # MP4 缩略图不用 ffmpeg：云盘本身会给视频生成缩略图。
        # 2026-11-04 改：不再 redirect 到云盘缩略图链接（国内不开加速器打不开，
        # 上海服务器自己也连不上谷歌），改由本站服务器把缩略图取回来、以字节
        # 直接发给浏览器，这样站内 /thumb 在国内也能出图，上海站以后也能缓存它。
        # tl 只来自云盘元数据，不是用户输入；只接受谷歌缩略图域名，防 SSRF。
        tl = meta.get("thumbnailLink") or ""
        if tl.startswith("https://") and ".googleusercontent.com" in tl:
            try:
                import requests as _rq
                _r = _rq.get(tl, timeout=20, stream=True)
                if _r.status_code == 200:
                    _data = b"".join(_r.iter_content(chunk_size=65536))
                    if _data and len(_data) < 5 * 1024 * 1024:
                        _ct = _r.headers.get("Content-Type") or "image/jpeg"
                        return Response(_data, headers={"Content-Type": _ct, "Cache-Control": "public, max-age=86400"})
            except Exception:
                pass
        return "暂无视频缩略图", 404
    from googleapiclient.http import MediaIoBaseDownload

    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, service.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = downloader.next_chunk()
    data = buf.getvalue()
    try:
        from PIL import Image, ImageOps

        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im)
        im.thumbnail((192, 192))
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            alpha = im.convert("RGBA").split()[3]
            bg.paste(im.convert("RGBA"), mask=alpha)
            im = bg
        else:
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=45, optimize=True)
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
    if (file_id or "").startswith("bd:"):
        if not _is_manager():
            return "只有站长区能改名，请先在站长区登录", 403
        if not _baidu_enabled():
            return "百度源暂未连接", 503
        try:
            _meta = _baidu_meta(file_id[3:])
        except Exception:
            return "文件不存在或无权访问", 404
        _area = _baidu_area(_meta.get("path") or "")
        if _area not in ("published", "pending"):
            return "文件不存在或无权访问", 404
        old_name = _meta.get("server_filename", "")
        if "." not in new_name and "." in old_name:
            new_name = new_name + "." + old_name.rsplit(".", 1)[-1]
        try:
            _baidu_rename_path(_meta.get("path") or "", new_name)
        except Exception:
            return "改名失败，请重试", 503
        _invalidate_pub_cache()
        _log("改名", f"{old_name} -> {new_name}")
        return redirect(request.form.get("back", "/") or "/")
    try:
        meta = get_file(service, file_id)
    except Exception:
        return "文件不存在或无权访问", 404
    parents = meta.get("parents") or []
    is_published = _is_published_parent(service, parents)
    is_pending = PENDING_FOLDER_ID in parents
    is_admin = _is_manager()
    if not is_admin:
        return "只有站长区能改名，请先在站长区登录", 403
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
    if _priority_backend() == "baidu":
        if not _baidu_enabled():
            return redirect("/?msg=百度暂未连接，无法新建文件夹#published")
        _base, _pub, _pend = _baidu_dirs()
        try:
            _existing = [e.get("server_filename") for e in _baidu_list_dir(_pub) if e.get("isdir")]
        except Exception:
            _existing = []
        if name.lower() in [x.lower() for x in _existing]:
            return redirect("/?msg=已有同名文件夹#published")
        if not _baidu_mkdir(_pub + name):
            return redirect("/?msg=新建文件夹失败，请重试#published")
        _invalidate_pub_cache()
        _log("新建文件夹", name)
        return redirect(f"/?msg=已新建文件夹 {name}#published")
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
        if (fid or "").startswith("bd:"):
            if not _baidu_enabled():
                continue
            try:
                _meta = _baidu_meta(fid[3:])
            except Exception:
                continue
            if _baidu_area(_meta.get("path") or "") != "published":
                continue
            _base, _pub, _pend = _baidu_dirs()
            if dest_id == PUBLISHED_FOLDER_ID:
                _dest_dir = _pub
            elif (dest_id or "").startswith(_pub):
                _dest_dir = dest_id if dest_id.endswith("/") else dest_id + "/"
            else:
                continue  # 不允许跨后端移动（设计第 1 节第 10 项）
            _src_dir = (_meta.get("path") or "").rsplit("/", 1)[0] + "/"
            if _src_dir == _dest_dir:
                continue
            try:
                _baidu_move_path(_meta.get("path") or "", _dest_dir, _meta.get("server_filename") or "")
                moved += 1
            except Exception:
                continue
            continue
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
