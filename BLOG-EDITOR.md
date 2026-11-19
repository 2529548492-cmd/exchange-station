# 网页写博客功能（BLOG-EDITOR）

交换站内置的「在浏览器里写博客」功能，写完点保存，博客站会自动构建上线。

---

## 一、整体流程

```
博客站 https://gujianxiaozi.ccwu.cc/posts/
   │ 点击导航「写博客」（跳转）
   ▼
交换站  http://211.154.28.6:31116/blog-editor
   │ 页面：static/write.html（输站长口令 + 标题 + Markdown 正文）
   │ POST /api/blog/save {password, title, content}
   ▼
app.py  api_blog_save()
   │ 1. 校验 password == .env 的 ADMIN_TOKEN
   │ 2. 用 .env 的 GITHUB_TOKEN 调 GitHub Contents API
   │ 3. 在 exchange-station-site 仓库创建 pages/posts/<日期>-<标题>.md
   ▼
GitHub Actions（该仓库 .github/workflows/deploy.yml）
   │ pnpm install → valaxy build → wrangler pages deploy
   ▼
Cloudflare Pages（项目 exchange-station）
   ▼
约 1~2 分钟后，博客站 /posts/ 出现新文章
```

前端页面还会轮询 `GET /api/blog/latest`，把云端构建状态显示给用户。

---

## 二、为什么编辑器放在交换站，而不是博客站

博客站是 HTTPS（Cloudflare），交换站是 HTTP（IP:端口）。

浏览器会拦截 HTTPS 页面向 HTTP 地址发起的 `fetch`（混合内容限制），所以把编辑器页面直接放在交换站（同源 HTTP）最简单可靠；博客站只放一个跳转入口。

> 如果以后交换站也上了 HTTPS，可以把 `static/write.html` 搬到博客站的 `public/` 下，并给接口加 CORS（代码里已带 `_blog_cors`）。

---

## 三、代码位置

| 内容 | 位置 |
|---|---|
| 编辑器页面 | `static/write.html` |
| 页面路由 | `app.py` → `blog_editor_page()`（`GET /blog-editor`）|
| 保存接口 | `app.py` → `api_blog_save()`（`POST /api/blog/save`）|
| 构建状态接口 | `app.py` → `api_blog_latest()`（`GET /api/blog/latest`）|
| CORS | `app.py` → `_blog_cors()`（仅对 `/api/blog/*` 生效）|
| 自动部署 | `exchange-station-site` 仓库的 `.github/workflows/deploy.yml` |

---

## 四、需要配置的环境变量（服务器 `.env`）

```ini
ADMIN_TOKEN=站长口令            # 编辑器密码就是它
GITHUB_TOKEN=ghp_xxx            # GitHub Token（勾选 repo 权限）
GITHUB_REPO=2529548492-cmd/exchange-station-site
BLOG_BRANCH=main
```

> `GITHUB_TOKEN` 建议使用 classic PAT 或 fine-grained token，至少需要目标仓库的 **Contents: Read and write** 权限。

---

## 五、接口说明

### POST /api/blog/save

请求：

```json
{ "password": "站长口令", "title": "文章标题", "content": "Markdown 正文" }
```

成功：

```json
{
  "ok": true,
  "path": "pages/posts/2026-11-19-文章标题.md",
  "commit": "0b53ac2c",
  "msg": "已提交，GitHub 正在自动构建，约 1~2 分钟后上线"
}
```

失败：

| 状态码 | 含义 |
|---|---|
| 403 `密码不对` | `password` 与 `ADMIN_TOKEN` 不一致 |
| 400 `标题和正文都不能为空` | 参数缺失 |
| 500 `服务器未配置 GITHUB_TOKEN` | `.env` 缺少 token |
| 502 `连接 GitHub 失败` / `提交失败` | 网络或 token 失效（401）|

### GET /api/blog/latest

返回最近一次 GitHub Actions 运行状态：

```json
{ "ok": true, "status": "completed", "conclusion": "success", "created_at": "...", "url": "..." }
```

---

## 六、排查

| 现象 | 原因 / 处理 |
|---|---|
| 保存提示「密码不对」 | `.env` 的 `ADMIN_TOKEN` 与输入不一致；改完后需重启交换站 |
| 保存提示「提交失败（401）」 | GitHub Token 失效或被撤销 → 重新生成并更新 `.env`，重启 |
| 保存成功但网站没更新 | 去 GitHub 仓库 Actions 看构建日志（Node 版本 / pnpm workspace / Cloudflare Token）|
| 页面 404 | 交换站 `static/write.html` 缺失，或路由补丁未生效 |
| 手机打不开编辑器 | 用交换站地址直连，需要能访问 `211.154.28.6:31116` |

---

## 七、安全说明

- 编辑器页面本身不泄露任何密钥；密钥只在服务器 `.env` 与后端使用。
- 密码即站长口令，请勿公开。
- 该接口只允许写 `pages/posts/` 下的文章，不会改动仓库其他文件。
