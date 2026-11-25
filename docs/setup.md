# PhotoCull 部署与接入指南

## 1. 小米 14T 手机设置

1. 打开 **OneDrive App**：开启「相机上传」，保留原图（含视频按需）自动备份到如 `/Pictures/Camera Roll`（或自定义目录）。
2. 打开 **Google 相册（Google Photos）**：**关闭**「备份」开关。避免未筛选的连拍抢先上传到 Google Photos（官方 API 不支持删除非本应用上传的历史照片）。
3. 手机本地空间不足时，直接用小米相册释放本地空间即可，不需要依赖手机端同步删除工具。

## 2. 部署 Cloudflare Worker + D1 + R2

```bash
npm install
npm run build

# 创建 D1 与 R2
npx wrangler d1 create photocull
npx wrangler r2 bucket create photocull-previews
```

将返回的 `database_id` 填入 `wrangler.jsonc`，并把 `ALLOWED_GITHUB_LOGINS` 改为你的 GitHub 用户名、`APP_ORIGIN` 改为实际 Worker 域名。

执行数据库迁移并配置密钥：

```bash
npx wrangler d1 migrations apply photocull --remote

npx wrangler secret put GITHUB_CLIENT_ID
npx wrangler secret put GITHUB_CLIENT_SECRET
npx wrangler secret put SESSION_SECRET      # 例如 openssl rand -hex 32
npx wrangler secret put PROCESSOR_TOKEN     # 例如 openssl rand -hex 32

npm run deploy
```

GitHub OAuth App 回调地址填写：`https://<你的Worker域名>/api/auth/callback`。

## 3. Microsoft Entra 应用（OneDrive）

1. 在 Azure Portal / Microsoft Entra 注册应用，支持个人 Microsoft 账号（`consumers`），重定向 URI 添加公共客户端 `http://localhost`（或设备码流）。
2. 授予 Microsoft Graph 委托权限：`Files.ReadWrite`、`offline_access`。
3. 获取 `MS_REFRESH_TOKEN` 后填入 `.env`。

## 4. Google Photos API（仅追加上传）

1. 在 Google Cloud Console 启用 **Photos Library API**。
2. OAuth 同意屏幕与凭据只申请以下 Scope：
   - `https://www.googleapis.com/auth/photoslibrary.appendonly`
3. 使用桌面端 OAuth 客户端获取 `GOOGLE_REFRESH_TOKEN` 后填入 `.env`（如测试阶段处于 Testing 模式，把自己的 Google 账号加入 Test Users）。

## 5. Mac mini (Ubuntu Server) 启用后台服务

参考 [deploy/systemd/README.md](../deploy/systemd/README.md) 安装虚拟环境、填写 `.env` 并启动 `photocull-scan.timer` 与 `photocull-exec.timer`。

可以先手动测试一次：

```bash
.venv/bin/python -m processor.cli scan --env-file .env
# 在手机浏览器打开 Worker 页面确认一两组后：
.venv/bin/python -m processor.cli execute --env-file .env
.venv/bin/python -m processor.cli status --env-file .env
```
