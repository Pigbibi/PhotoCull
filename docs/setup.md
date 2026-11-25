# PhotoCull 部署与接入指南

## 1. 小米 14T 手机设置

1. 打开 **OneDrive App**：开启「相机上传」，保留原图自动备份到如 `/Pictures/Camera Roll`（或自定义目录）。
2. 打开 **Google 相册（Google Photos）**：**关闭**「备份」开关。避免未筛选的连拍抢先上传到 Google Photos（官方 API 不支持删除非本应用上传的历史照片）。
3. 手机本地空间不足时，直接用小米相册释放本地空间即可，不需要依赖手机端同步删除工具。

## 2. 部署 Cloudflare Worker + D1 + R2

建议复制 `wrangler.jsonc` 为本地未跟踪的 `wrangler.local.jsonc`（已在 `.gitignore` 忽略），填入实际 `account_id`、`database_id`、`ALLOWED_GITHUB_LOGINS` 和部署后的 `APP_ORIGIN`：

```bash
npm install
npm run build

npx wrangler d1 create photocull
npx wrangler r2 bucket create photocull-previews
npx wrangler d1 migrations apply photocull --remote --config wrangler.local.jsonc

npx wrangler secret put GITHUB_CLIENT_ID --config wrangler.local.jsonc
npx wrangler secret put GITHUB_CLIENT_SECRET --config wrangler.local.jsonc
npx wrangler secret put SESSION_SECRET --config wrangler.local.jsonc
npx wrangler secret put PROCESSOR_TOKEN --config wrangler.local.jsonc

npx wrangler deploy --config wrangler.local.jsonc
```

GitHub OAuth App 回调地址填写：`https://<你的Worker域名>/api/auth/callback`。

## 3. Microsoft Entra 应用（OneDrive）

1. 在 Azure Portal / Microsoft Entra 注册应用，支持个人 Microsoft 账号（`consumers`），在「身份验证 -> 允许公共客户端流」中开启 **是**，并添加桌面/移动平台重定向 URI `https://login.microsoftonline.com/common/oauth2/nativeclient`。
2. 授予 Microsoft Graph 委托权限：`Files.ReadWrite`、`offline_access`。
3. 先将 `MS_CLIENT_ID` 写入 `.env`，再运行设备码登录自动写入 `MS_REFRESH_TOKEN`：

```bash
.venv/bin/python -m processor.cli auth-onedrive --env-file .env --write-env
```

## 4. Google Photos API（仅追加上传）

1. 在 Google Cloud Console 启用 **Photos Library API**。
2. OAuth 同意屏幕与凭据只申请以下 Scope（如处于 Testing 模式，把自己的 Google 账号加入 Test Users）：
   - `https://www.googleapis.com/auth/photoslibrary.appendonly`
3. 创建 **桌面应用（Desktop app）** 类型 OAuth 客户端（允许 `http://127.0.0.1:<port>/callback` 回环），将 `GOOGLE_CLIENT_ID` 与 `GOOGLE_CLIENT_SECRET` 填入 `.env`。
4. 通过内置 CLI 获取并保存 `GOOGLE_REFRESH_TOKEN`（通过 SSH 连接 Mac mini 时建议加 `--manual`，在本地浏览器授权后粘贴跳转 URL）：

```bash
.venv/bin/python -m processor.cli auth-gphotos --env-file .env --manual --write-env
```

## 5. Mac mini (Ubuntu Server) 启用后台服务

参考 [deploy/systemd/README.md](../deploy/systemd/README.md) 安装虚拟环境、填写 `.env` 并启动 `photocull-scan.timer` 与 `photocull-exec.timer`。

可以先手动测试一次：

```bash
.venv/bin/python -m processor.cli scan --env-file .env
# 在手机浏览器打开 Worker 页面确认一两组后：
.venv/bin/python -m processor.cli execute --env-file .env
.venv/bin/python -m processor.cli status --env-file .env
```
