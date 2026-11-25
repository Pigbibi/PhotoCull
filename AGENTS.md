# PhotoCull AGENTS.md

## 项目是什么

面向旅拍场景的精选与同步工作台：由 Ubuntu 后台定时扫描 OneDrive、对连拍聚类并调用多模态 AI 评分，通过 Cloudflare 审核台确认后移入 OneDrive 回收站，并将保留的精选上传至 Google Photos。

## 技术栈

- 云端审核台：React 19 + Vite + Cloudflare Worker + D1 + private R2
- 后台处理器：Python 3.11+ + Pillow + SQLite（Ubuntu `systemd` 定时器）

## 目录

- `web/` — 移动端优先的审核前端
- `worker/` — Cloudflare Worker API 与 GitHub OAuth 鉴权
- `migrations/` — D1 数据库迁移脚本
- `processor/` — Ubuntu 扫描、dHash 聚类、AI 选优与同步执行器
- `deploy/systemd/` — Mac mini (`ubuntu-server`) 定时器单元
- `docs/` — 部署与接入指南
- `tests/` — Worker 与 Python 单元测试

## 本地验证

```bash
npm ci
npm test
npm run build
.venv/bin/python -m unittest discover -s tests/python -p 'test_*.py'
```

## 命名与文档

- 文档文件名保持小写、使用 `-` 连接，中文副本如需双语则加 `.zh-CN.md` 后缀。
- 不提交审计、复盘、交接或阶段计划文档。

## 不要做

- 不要在 Google Photos 接入 `photoslibrary.readonly` 或尝试读取/删除历史全库。
- 不要在代码、日志或配置文件中写入真实 OAuth 密钥、Refresh Token 或个人路径。
- 不要绕过 `If-Match` 核对或在 AI 判定异常时默认标记 `trash`。

<!-- content-boundary:begin -->
- 公开材料不出现真实账号、Bucket ID、Database ID 或个人域名前缀，统一使用占位符。
- 所有密钥与 Token 通过 `wrangler secret put` 或服务器 `.env` 注入，不进入 Git。
<!-- content-boundary:end -->

## 提交与 PR

- Commit and PR titles use imperative Conventional Commits (`feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`) within 72 characters.
- Preserve required checks (`Repository hygiene`, `Gitleaks`, `pr-template`, `ai-review`, `risk-gate`, `CI`) and use feature branches (`<type>/<topic>`) for subsequent changes.
