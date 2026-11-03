# 交换站 - Render 免费版部署说明

## 这个站是干什么的

只给熟人用的文件交换站，配合 Google 云盘里的“交换站”文件夹：

- 熟人先输入访问口令（EXCHANGE_CODE）才能进入网站
- 熟人上传 -> 云盘“待审核”文件夹，别人看不到、也下载不到
- 站长用站长口令（ADMIN_TOKEN）进 /admin 审核：
  通过 -> 文件从“待审核”移到“已发布”，熟人才能下载；
  不通过 -> 文件移到回收站
- 网站本身不存文件。上传直接流式传到云盘，下载直接从云盘流式回传，
  Render 免费版休眠、重启都不会丢文件（免费版本地磁盘本来就是临时的）

## 云盘文件夹（已经建好，暂时私有，没有分享给任何人）

- 交换站（根）
  - 待审核：`10sy_eATALXEAr1EhW67YVbYbVTeM7lI4`
  - 已发布：`1fsc2jUUVJnqgupdl_-HY0D0nKVt6xMR3`

上面两个 ID 只写在 `render.yaml` 和 `app.py` 的默认环境变量里，
代码里除了这两个文件夹，绝不读取、列出、分享任何其他云盘文件。

## 部署步骤（需要站长自己的账号，见文末“还差哪几步”）

### 第 1 步：把代码传到 GitHub

把本文件夹（`app.py`、`requirements.txt`、`render.yaml`、本文件）传到
你自己的 GitHub 仓库，公开或私有都可以，Render 连 GitHub 部署。
**不要把服务账号 JSON、站长口令、访问口令写进代码或传到仓库里。**
`.gitignore` 已经排除了 `.env` 和 `service-account*.json`。

### 第 2 步：在 Render 上新建免费 Web Service

1. 登录 Render，选 New -> Web Service，连接上面那个 GitHub 仓库。
2. 计划选 Free。Render 会自动读 `render.yaml`：
   构建命令 `pip install -r requirements.txt`，
   启动命令 `gunicorn app:app --bind 0.0.0.0:$PORT`。
3. 部署完成后会得到一个地址，形如 `https://exchange-station.onrender.com`。

### 第 3 步：在 Google Cloud 里新建服务账号（不要用个人账号密码）

1. 打开 Google Cloud Console，新建一个项目（或用已有的空项目）。
2. 在“API 和服务 -> 库”里启用 **Google Drive API**。
3. 在“API 和服务 -> 凭据”里新建 **服务账号**（Service Account），
   记下它的邮箱，形如 `xxx@xxx.iam.gserviceaccount.com`。
4. 给这个服务账号新建一个密钥（Key -> JSON），下载 JSON 文件。
   这个 JSON 就是 `GOOGLE_SERVICE_ACCOUNT_JSON`，只填到 Render 环境变量里，
   不要发给任何人、不要传到 GitHub、不要贴到聊天里。
5. **只分享两个文件夹给这个服务账号，不要分享整个云盘：**
   在 Google 云盘里，分别打开“待审核”和“已发布”两个文件夹，
   点分享，把服务账号邮箱加进去，权限选 **编辑者（Editor）**。
   编辑者权限是必需的：服务账号要在“待审核”里新建文件、
   在审核时把文件从“待审核”移到“已发布”、把不通过的文件移到回收站。
   不要分享“交换站”根文件夹以外的任何文件，也不要分享整个云盘。
   提示：先用一个小文件做一次端到端测试（上传 -> 待审核出现 ->
   审核通过 -> 已发布出现并能下载），确认文件确实出现在你自己的云盘里，
   再把地址发给熟人。

### 第 4 步：在 Render 的 Environment 里填保密值

以下三项一律只填在 Render 的 Environment（环境变量）里：

- `GOOGLE_SERVICE_ACCOUNT_JSON`：服务账号 JSON 的全部内容，
  或它的 Base64 编码（代码两种格式都接受）。
- `ADMIN_TOKEN`：你自己定的站长审核口令。
- `EXCHANGE_CODE`：你自己定的熟人访问口令，私下发给熟人，不要公开。

可选：`MAX_UPLOAD_MB`（单文件上限，默认 200），
`DRIVE_PENDING_FOLDER_ID` / `DRIVE_PUBLISHED_FOLDER_ID`（一般不用改）。

### 第 5 步：告诉熟人怎么用

1. 打开 Render 地址，第一次打开如果服务在休眠，要等约 1 分钟唤醒，
   这是 Render 免费版的正常表现，不是网站坏了。
2. 输入你私下给的访问口令，进入后能看到已发布文件、能下载、能上传。
3. 上传后不会马上出现在已发布里，要等站长审核通过。

## Render 免费版限制（先说清楚）

- 15 分钟没人用就休眠，下次访问约 1 分钟唤醒；休眠时不计入免费额度。
- 一个工作区一个月 750 小时免费额度，休眠时不计时间；一个熟人小站一般不会超。
- 免费版是 0.1 核、512MB 内存，只适合熟人小流量。代码已经做了流式上传下载，
  避免把整个大文件读进内存，但单文件仍建议控制在 200MB 以内，大文件请分批传。
- 免费版没有持久磁盘，所以本站绝不把文件存在 Render 本地，文件只在云盘里。
- 以后想绑定你自己的免费域名，可以在 Render 里加 Custom Domain，
  再去域名那边做 CNAME 指向 Render 地址；域名本身不改变上面这些步骤。

## 本地检查（没有真实账号也能跑）

没有配置服务账号时，网站也能打开，只是会如实提示“云盘暂未连接，
上传/下载暂不可用”，不会列出任何真实云盘文件，也不会冒充已连接：

```bash
python3 -m py_compile app.py
PORT=5001 python3 app.py
# 另开一个终端：
curl -s http://127.0.0.1:5001/health
curl -s http://127.0.0.1:5001/ | head -20
```

## 常见问题

- 打开首页只看到“需要访问口令”：说明 Render 里还没设置 `EXCHANGE_CODE`，
  站长设置好并私下发给熟人即可。
- 上传/下载提示“云盘暂未连接”：说明 Render 里还没设置
  `GOOGLE_SERVICE_ACCOUNT_JSON`，或 JSON 格式不对。
- 上传后在待审核里看不到：先检查服务账号有没有被分享“待审核”文件夹的
  编辑者权限；只分享了根文件夹、或权限是查看者，都会上传失败。
- 下载提示“该文件尚未发布”：这是故意的安全检查，待审核文件不允许
  直接用链接下载，必须先由站长通过。
- 第一次打开很慢：Render 免费版休眠唤醒，约 1 分钟，之后就正常了。

## 还差哪几步才能真正上线（本机没有替你做，也没有替你登录任何账号）

1. 站长提供/登录自己的 Render 账号，并把代码仓库连到 Render。
2. 站长在 Google Cloud 新建服务账号并下载 JSON 密钥。
3. 站长只把“待审核”和“已发布”两个文件夹分享给服务账号邮箱（编辑者）。
4. 站长在 Render 环境变量里填入 `GOOGLE_SERVICE_ACCOUNT_JSON`、
   `ADMIN_TOKEN`、`EXCHANGE_CODE`，然后做一次小文件端到端测试。
5. 测试通过后，再把 Render 地址和访问口令私下发给熟人。
