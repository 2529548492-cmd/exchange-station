# 交换站

熟人之间传文件的地方。上传先到待审核，站长通过后才出现在已发布里，熟人才能下载。

## 功能
- 熟人访问口令进站，站长口令审核
- 上传最多 3 个文件，总量不超过 500 MB，直传云盘或经本站中转
- 已发布按分类分组（文档 / 图片 / 影音 / 压缩包 / 安装包 / 其他），图片和视频有缩略图
- 在线预览（图片、PDF、文本、音视频），其他类型走下载
- 站长审核、改名、删除已发布、访问日志（时间 / IP / 属地 / 设备 / 动作）
- 两层中转：上海前端 + 境外后端（`RELAY_BACKEND`），大陆用户走前端，后端负责拉云盘

## 部署（以 Render 为例）
1. Fork 本仓库，在 Render 新建 Web Service，指向本仓库。
2. 在 Render 环境变量里填：
   - `DRIVE_PENDING_FOLDER_ID`：待审核文件夹 ID
   - `DRIVE_PUBLISHED_FOLDER_ID`：已发布文件夹 ID
   - `MAX_UPLOAD_MB`：单批总量上限，例如 500
   - `ADMIN_TOKEN`：站长口令
   - `EXCHANGE_CODE`：熟人口令
   - `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` / `GOOGLE_OAUTH_REFRESH_TOKEN`：站长本人的 Google Drive OAuth 授权（推荐）
   - 可选 `RELAY_BACKEND`：两层中转时填后端地址，例如 https://exchange-station.onrender.com
3. 启动命令：`gunicorn app:app --bind 0.0.0.0:$PORT --timeout 300 --workers 1 --worker-class gthread --threads 4`

## 安全说明
- 所有口令、密钥、OAuth 凭证一律只从环境变量读，**不要写进代码、不要提交到仓库**。
- 服务账号 / OAuth 凭证只应共享待审核和已发布两个文件夹，不要共享整个云盘。
- 本仓库不含任何真实口令、密钥或用户文件，公开后别人只能看到代码，拿不到你的云盘文件和口令。

## 许可
MIT，见 LICENSE。
