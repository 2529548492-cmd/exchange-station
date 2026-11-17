# 自建部署修复记录（2026-11-17 ~ 18）

本文件记录把交换站从 Render 迁移到自建 Windows 云电脑 + 百度网盘后端时，
发现并修复的问题。改动全部在 `app.py`。

## 一、百度网盘后端

| # | 问题 | 现象 | 修复 |
|---|---|---|---|
| 1 | 百度写接口缺少 `User-Agent: pan.baidu.com` | 创建目录、移动、删除全部返回 `errno=2 参数错误` | 给 `_baidu_api` / `_baidu_api_post` 统一加 UA |
| 2 | 分片上传把 `return_type=1` 误判为「秒传完成」 | 超过 4 MB 的文件上传后"成功"但文件不存在（大文件凭空消失） | 只有 `block_list` 为空且拿到 fs_id 时才短路；否则按 `block_list` 逐片上传 |
| 3 | `create` 接口参数放在 query 而非 body | 分片传完后创建文件报 `errno=2 path=""` | 参数改到 POST 表单体 |
| 4 | `_baidu_meta` 读 `server_filename`，filemetas 返回的是 `filename` | 缩略图接口判定"不是图片"→ 404 | 兼容 `filename`，并可从 path 兜底 |
| 5 | 移入已发布时 `newname` 传成了文件 ID | 点「通过」报 `errno=12 / -7` | 用真实文件名，异常时从路径取 |
| 6 | 删除接口 filelist 传对象 | 删除已发布失败 | 改为路径字符串数组 |
| 7 | `/admin/delete` 先检查 Google 连接 | 百度模式下永远返回「云盘暂未连接」 | 把 `bd:` 分支提前，Google 检查移到分支之后 |
| 8 | 冷启动读不到轮换后的 refresh_token | 重启后百度令牌失效，站点上传全挂 | 新令牌同时写本地 `baidu_token.json`，冷启动优先读它 |

## 二、访问控制

| # | 问题 | 修复 |
|---|---|---|
| 9 | 未配置 `EXCHANGE_CODE` 时 `/api/files` 直接返回空 | 改为 `(not code) or _access_granted()` |
| 10 | 首页路由同样被 `if code and entered` 卡住 | 同样改为开放放行，首页才能读到文件列表 |

## 三、前端与缓存

| # | 问题 | 修复 |
|---|---|---|
| 11 | 缩略图 404 被浏览器缓存 14 天 | 失败响应改 `no-store`；缩略图 URL 加 `?v=2` 版本号（含服务端模板与 JS 两处） |
| 12 | `/api/files` 可能被缓存空结果 | 加 `Cache-Control: no-store` |
| 13 | 手机端 AI 说明被挤成竖排、按钮点不到 | 注入移动端 CSS：`file` 网格横跨整行、按钮区整行平分、页面可滚动 |

## 四、新增功能

| # | 功能 | 说明 |
|---|---|---|
| 14 | AI 审核密钥可视化配置 | `/admin/ai` 页面：查看当前 Key（脱敏）、保存新 Key、一键测试连通性；密钥存本地文件并优先于环境变量 |
| 15 | 跨平台数据目录 | 新增 `_DATA_DIR`，默认程序同目录，可用 `EXCHANGE_DATA_DIR` 覆盖，Windows/Linux 通用 |
| 16 | 百度后端「已连接」判定 | `_drive_configured()` 在百度已配置时返回 True，不再误报「云盘暂未连接」 |

## 部署方式（当前生产环境）

```
Windows Server 2012 R2 + waitress（serve.py）
监听 0.0.0.0:3115，经机房 NAT 映射到公网 31116
存储：百度网盘 /apps/交换站/（已发布、待审核）
计划任务：ExchangeStation（开机自启、SYSTEM 权限）
```

## 注意事项

- 百度 `refresh_token` 会轮换：新旧令牌由 `baidu_token.json` 自动维护，**不要手动改环境变量里的旧值**
- 上传大文件走「precreate → superfile2 分片 → create」三段式，任一步失败都会记日志 `[baidu-upload]`
- 缩略图仅对图片生效，视频/安装包返回文字角标
