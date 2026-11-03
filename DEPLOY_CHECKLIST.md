# 交换站部署清单（准备工作，2026-11-03）

目标：只给熟人用，上传先进待审核，Ryan 批准后才能进已发布给熟人下载。

## 结论：代码就绪，可以进入上线步骤
- 代码完整：`app.py`、`README.md`、`render.yaml`、`requirements.txt`、`.gitignore` 都在。
- 环境变量名一致：`app.py` 和 `render.yaml` 都用
  `GOOGLE_SERVICE_ACCOUNT_JSON`、`ADMIN_TOKEN`、`EXCHANGE_CODE`，
  以及 `DRIVE_PENDING_FOLDER_ID`、`DRIVE_PUBLISHED_FOLDER_ID`、`MAX_UPLOAD_MB`。
- 没有硬编码密钥：三个保密值在代码里只从环境变量读，
  `render.yaml` 里它们是 `sync: false`（即只在 Render 后台填，不写进文件）。
  文件夹 ID 是默认值，不是密钥，见下。
- `.gitignore` 已排除 `.env` 和 `service-account*.json`。
- Drive 文件夹（已建好，本次未改动权限、未读写）：
  - 交换站根：`1tYXq8vz8212wB64uaw2N12rbEuJiummn`
  - 待审核：`10sy_eATALXEAr1EhW67YVbYbVTeM7lI4`
  - 已发布：`1fsc2jUUVJnqgupdl_-HY0D0nKVt6xMR3`

## 本次本地测试（未连真实 Drive，未改 Drive）
用临时 venv（`/tmp/exchange-test-venv`，不在工作区）装 Flask 后跑测试客户端，
测试口令均为临时值，测试后即弃用，不在任何文件里留存：
- `py_compile app.py`：通过
- `/health`：`{"ok": true, "drive_configured": false}`，诚实报告未连接
- 未配访问口令时首页：提示“本站需要访问口令”，不列文件
- 配口令后：错口令跳转回首页，对口令设置访问 cookie，能进首页
- 进首页后：显示“云盘暂未连接”，上传按钮禁用，上传请求被导回并提示未连接
- 未登录管理员时 `/admin`：403；对管理员口令后能进审核页，同样显示未连接
- 未连接时下载：503
- 未测到的部分（按任务要求，故意不连真实 Drive）：
  真实上传、下载、审核通过/不通过的 Drive 路径本次没有跑，
  必须在上线后的端到端小文件测试里验证。

## 上线还缺的东西（按顺序，哪一步要叫 Ryan 已标出）
1. **代码传到 GitHub** — 需要 Ryan 在自己设备上提供/登录 GitHub 账号，
   把本文件夹传到他自己的仓库（公开或私有均可），不要把保密值传进仓库。
2. **Render 账号并新建免费 Web Service** — 需要 Ryan 在自己设备上登录 Render，
   选 New → Web Service，连上第 1 步的仓库，计划选 Free，
   `render.yaml` 会自动用 `pip install -r requirements.txt` 和
   `gunicorn app:app --bind 0.0.0.0:$PORT`。不要购买任何付费计划。
3. **Google Cloud 服务账号** — 需要 Ryan 在自己设备上操作：
   新建项目（或用已有空项目），启用 Google Drive API，
   新建服务账号并下载 JSON 密钥。
   该 JSON 只填到 Render 环境变量里，不要发给任何人、不要贴到聊天里、
   不要传到 GitHub。
4. **只共享两个文件夹给服务账号** — 需要 Ryan 在自己设备上的 Drive 里操作：
   只把“待审核”和“已发布”两个文件夹分享给服务账号邮箱，权限选编辑者。
   不要分享交换站根以外的文件，不要分享整个云盘。
5. **Render 环境变量** — 需要 Ryan 在 Render 后台填写（不要在聊天里给值）：
   `GOOGLE_SERVICE_ACCOUNT_JSON`、`ADMIN_TOKEN`、`EXCHANGE_CODE`。
   文件夹 ID 和 `MAX_UPLOAD_MB=200` 已在 `render.yaml` 里，不用改。
6. **端到端小文件测试** — 上线后用一个小文件走一遍：
   熟人口令进站 → 上传 → Drive“待审核”出现 → 站长审核通过 →
   Drive“已发布”出现并能下载 → 待审核文件直接下载应被拒绝。
   测试通过前不要把地址和访问口令发给熟人。
7. **Ryan 自己检查 live 站** — 测试通过后，Ryan 自己打开 Render 地址检查，
   第一次打开若服务休眠，约 1 分钟唤醒属正常（Render 免费版限制：
   15 分钟闲置休眠，0.1 核、512MB 内存，单文件建议 200MB 以内）。

## 本次没有做的事
- 没有登录任何浏览器账号，没有购买任何东西。
- 没有改动 Drive 里任何文件或权限，没有读写真实 Drive。
- 没有在聊天或文件里写任何密码、密钥、令牌；测试口令均为临时值且已弃用。
- Colab 上下文改 24576 的任务由另一路并行进行，本清单不干预它。
