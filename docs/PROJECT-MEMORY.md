# 交换站 · 项目记忆（详细版）

> 更新：2026-11-20 ｜ 用途：跨会话恢复上下文、给后来者接手
> 简版入口：`C:\Users\陈云财\计划\AGENTS.md`

---

## 一、项目全貌

### 1. 交换站（Flask 单体应用）

- **作用**：熟人间传文件；上传→待审核→站长通过→已发布；支持预览、下载、改名、删除、访问日志。
- **位置**：云电脑 `211.154.28.6`，`C:\exchange-station\`，端口 3115（公网 `http://211.154.28.6:31116`）。
- **技术**：`app.py`（约 410KB / 8000+ 行单体 Flask）+ `serve.py`（waitress）。无数据库，状态存本地 JSON + 百度网盘。
- **存储**：本地优先存储（`local_store/`：pending/published/thumbs + index.json），上传后立即本地可见，后台线程再上传百度网盘。
- **APP**：Android WebView 壳（`apkbuild-shell2`），2.5 版已发布 `bd:105608514977654`；支持文件选择、视频全屏、点击穿透修复。

### 2. 博客站（Valaxy 静态站）

- **作用**：站点主页、博客文章、下载页、FAQ、更新日志。
- **仓库**：`exchange-station-site`（私有），`main` 分支。
- **技术**：Valaxy 1.3.4 + valaxy-theme-yun；`pages/posts/*.md` 为文章；`layouts/home.vue` 自定义首页（开场动效 + 推荐卡片）。
- **托管**：Cloudflare Pages 项目 `exchange-station`（**Direct Upload 直传类型，未连 Git**），域名 `gujianxiaozi.ccwu.cc`，备用 `exchange-station.pages.dev`。

---

## 二、发布链路（重点）

### A. 网页写博客（日常）

```
博客站 /posts/
  → 点导航「写博客」
  → 交换站 http://211.154.28.6:31116/blog-editor （static/write.html）
  → 输入站长口令 + 标题 + Markdown 正文
  → POST /api/blog/save
  → 交换站用 .env 的 GITHUB_TOKEN 调 GitHub Contents API
  → 仓库 exchange-station-site 新建 pages/posts/<日期>-<标题>.md
  → GitHub Actions（.github/workflows/deploy.yml，Node 22）
       pnpm install --frozen-lockfile → pnpm build → pnpm exec wrangler pages deploy dist
  → Cloudflare Pages → 域名上线（约 1~2 分钟）
```

前端还会轮询 `GET /api/blog/latest` 显示构建状态。

### B. 本地改代码

```
本机 station-site：pnpm build
  → pnpm exec wrangler pages deploy dist --project-name=exchange-station --branch=main
（凭据在 station-site/.env.deploy，不入库）
```

### 为什么要两套

- 静态站必须「构建 → 上传」；网页写博客只写 Markdown，构建交给 GitHub Actions。
- 换样式/布局/导航必须本地构建（或推到仓库让 CI 构建）。

---

## 三、关键文件与界面位置

| 内容 | 位置 |
|---|---|
| 写博客页面 | 交换站 `static/write.html`，路由 `GET /blog-editor` |
| 保存接口 | `app.py` → `api_blog_save()`（`POST /api/blog/save`）|
| 构建状态接口 | `app.py` → `api_blog_latest()` |
| CORS（仅 /api/blog/*）| `app.py` → `_blog_cors()` |
| 首页自定义（动效/卡片）| 博客站 `layouts/home.vue` |
| 导航（含写博客入口）| 博客站 `valaxy.config.ts` → `themeConfig.nav` |
| CI 自动部署 | 博客站 `.github/workflows/deploy.yml` |
| 本机一键发布 | 博客站 `publish.cmd` / `publish.ps1` |
| 本地新建文章工具 | 博客站 `tools/new-post.cmd` / `new-post.ps1` |
| 硝子聊天助手 App 页 | 博客站 `pages/apps/xiaozichat.md` → `/apps/xiaozichat/` |
| 硝子聊天助手 APK | 博客站 `public/dl/xiaozichat-1.7.apk` → `https://gujianxiaozi.ccwu.cc/dl/xiaozichat-1.7.apk`（8.2 MB，走 Cloudflare，比网盘稳） |

---

## 四、环境变量与凭据（都不入库）

### 交换站 `.env`（服务器 `C:\exchange-station\.env`）

```ini
ADMIN_TOKEN=<站长口令>        # 站长区登录 + 写博客页面密码
GITHUB_TOKEN=<PAT>            # 需 repo 或 Contents 读写
GITHUB_REPO=2529548492-cmd/exchange-station-site
BLOG_BRANCH=main
BAIDU_CLIENT_ID / BAIDU_CLIENT_SECRET / BAIDU_REFRESH_TOKEN
PORT=3115
MAX_UPLOAD_MB=...
PRIORITY_BACKEND=baidu
BAIDU_ENABLED=...
```

### 本机 `station-site/.env.deploy`

```ini
CLOUDFLARE_API_TOKEN=...
CLOUDFLARE_ACCOUNT_ID=...
CF_PAGES_PROJECT=exchange-station
CF_DOMAIN=gujianxiaozi.ccwu.cc
```

### GitHub Actions Secrets（仓库 Settings）

`CLOUDFLARE_API_TOKEN`、`CLOUDFLARE_ACCOUNT_ID`

- GitHub PAT 建议 fine-grained：目标仓库 **Contents: Read and write**。
- 注意：classic PAT 过期/被撤销后的症状是接口返回 401；网页写博客会提示「提交失败（401）」。

---

## 五、服务器运维

- 系统：Windows Server 2012 R2，2 vCPU / 2GB 内存；磁盘 55.5GB（可用约 40GB）。
- 站点重启：
  ```powershell
  Stop-Process -Name python -Force
  schtasks /run /tn ExchangeStation
  Start-Sleep -Seconds 18
  (Invoke-WebRequest 'http://127.0.0.1:3115/health').StatusCode   # 应为 200
  ```
- 远程命令通道（曾用）：写 ASCII 命令到 `C:\Temp\dsh\cmd.txt`；计划任务 `ExchangeWatcher` 跑 `watch.ps1`；结果读 `C:\Temp\dsh\out.log`，以 `=== END ===` 分块。相同内容会被跳过，需追加唯一随机标记。
  - **2026-11-19 起通道失效**（health 仍 200），需 RDP 登录重启计划任务或 watch.ps1。
- 已关闭的无关进程（省内存）：浏览器、Clash、ServerManager 等；内存占用前几名：svchost/powershell/dwm/python。
- 云电脑带宽仅约 2~3 Mbps（上下行），这是大文件上传/下载慢的根因。

---

## 六、排错手册

| 现象 | 原因 | 处理 |
|---|---|---|
| 网页写博客「密码不对」 | 输入与 `ADMIN_TOKEN` 不一致 | 用站长口令；改 `.env` 后重启 |
| 「提交失败（401）」 | GitHub Token 过期/被撤销 | 重新生成 PAT → 更新 `.env` → 重启 |
| 保存成功但网站没更新 | CI 构建失败 | 看仓库 Actions 日志 |
| CI Deploy 步骤失败（Node 20）| wrangler 要求 Node ≥ 22.12 | 工作流用 `node-version: 22` |
| CI setup-node 失败 `packages field missing` | pnpm-workspace 缺 `packages:` | 加 `packages: ['.']` |
| CI Deploy 报 `ERR_PNPM_ADDING_TO_ROOT` | 用了 wrangler-action | 改 `pnpm exec wrangler pages deploy` |
| 首页没开场动效 | 系统开了「减少动画」，或模板写死 intro-complete | 关减少动画；`layouts/home.vue` 绑定 `yun.bannerAnimationDone` |
| 编辑器页面 404 | `static/write.html` 丢失/路由补丁未生效 | 补文件/补路由后重启 |
| 本机 git push 卡住弹黑框 | git-credential-manager 弹窗 | 用 `-c credential.helper=` + token URL；或走代理 7890 |
| 本机直连 GitHub 失败 | 网络 | 用用户代理 `http://127.0.0.1:7890` |
| 域名打不开（ERR_TIMED_OUT）| 域名有 AAAA(IPv6) 记录、本机无 IPv6 | hosts 固定 IPv4 或关闭 IPv6 优先 |

---

## 七、历史决策与踩坑记录

1. **本地优先存储**：上传先落本地盘（立即可见），后台再传百度；解决「上传完还要等网盘同步」的痛点。
2. **首页性能**：`render_template_string` 每次重编译导致 TTFB 2.4s；加页面渲染缓存 + 模板编译缓存 + 百度查询 TTL 后降到 ~0.07s。
3. **站长页 500**：自定义函数 `_baidu_pending` 与原函数重名导致 `KeyError: 'id'`；改名后修复，并加了未捕获异常写 `err.log`。
4. **写博客编辑器位置**：博客站是 HTTPS，直接 POST 到 HTTP 交换站会被浏览器拦截混合内容，所以页面放交换站同源，博客站只留跳转入口。
5. **AI 自动发布**：保持开启，但要保留 AI 记录面板（按今天/昨天/更早分组）。
6. **上传队列可视化**：XHR/fetch 钩子显示每文件进度、速度、剩余时间。
7. **Cloudflare Pages 项目类型**：直传模式无法用 Deploy Hook，自动构建只能靠 GitHub Actions。

---

## 八、待办 / 可改进

- [ ] 重启云电脑 `ExchangeWatcher` 命令通道（需 RDP 或用户手动）。
- [ ] 博客站仓库是否公开待定。
- [ ] 大文件上传提速：百度分享链接导入 / 升级 IDC 带宽 / 分片并行（云电脑带宽封顶则收效有限）。
- [ ] 可加：文章草稿箱、图片粘贴上传、删除文章的管理界面。

---

## 九、给接手者的阅读顺序

1. 本文件（全局上下文）
2. 博客站仓库 `README.md`（构建/部署/定制点/踩坑）
3. 交换站仓库 `BLOG-EDITOR.md`（写博客接口与安全）
4. 交换站仓库 `README.md`（部署与环境变量总览）

---

## 十、2026-11-20 更新：硝子聊天助手上线

### 已完成
- **APK 瘦身**：28.06 MB → **8.21 MB**（8,613,255 B，SHA256 `088CC8A9C527887A4802F3F00BE25A551EB55BD1FBB12AC6879747A85742831D`）
  - 删 mlkit 中文 OCR（改视觉模型）、material、constraintlayout、旧 mipmap
  - 关键：删库后必须 `:app:clean :app:assembleDebug`，否则 18.61MB 增量打包空洞不回收
- **APK 托管到 Cloudflare Pages**：https://gujianxiaozi.ccwu.cc/dl/xiaozichat-1.7.apk （不再走网盘）
- **详情页**：https://gujianxiaozi.ccwu.cc/apps/xiaozichat/ （`pages/apps/xiaozichat.md`）
- **下载页**：两张卡片（`pages/download.md`，`/tile-xiaozichat.jpg`）
- **首页推荐区**：两张卡片（`layouts/home.vue`，去掉 `x-apps-single`；第二张 `x-app-purple`，HTML 标题/副标题）
  - 注意：第一张卡片「硝子交换站」的标题是**烘焙在 tile-xiangzi.jpg 图片里**的，源码里 `.x-app-body` 为空，不是 bug
- **交换站旧包清理**：pending `lcl:3346f39490c8` + published `bd:1024948613204292`（28.1MB）已删除
- 站长口令仍为 `cb198164`（本次验证可登录）

### 本机构建工具链问题（已知）
- `C:\Users\陈云财\AppData\Local\pnpm` 被标记为不可信挂载点，`pnpm build` 报 `UNKNOWN: unknown error`
- `station-site\node_modules` 里 valaxy/wrangler 的 junction 已失效
- **绕过办法**：改源码 → 推到 `exchange-station-site` 仓库 → GitHub Actions 自动构建部署（已多次验证成功，约 1~2 分钟）
  - 本机推送需走代理 `http://127.0.0.1:7890`（直连会被 reset）
  - 推送命令：`git -c credential.helper= -c http.proxy=... push https://x-access-token:<PAT>@github.com/...`

### CI 注意
- 仓库含 8.2MB APK，提交/推送会稍慢，属正常
- 构建成功判据：Actions 里 Deploy 步骤 success；线上首页两张卡片同时出现
