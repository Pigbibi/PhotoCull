# PhotoCull · 旅拍精选台

脱离手机的旅行连拍 AI 筛选与双云同步工作台。

- **收集端（小米 14T）**：手机只开启 **OneDrive 自动备份原图**，关闭 Google Photos 自动备份；本地想删随时删，互不影响。
- **后台处理（Mac mini · Ubuntu Server）**：定时扫描 OneDrive，按拍摄时间窗口 + 64 位 dHash 对连拍聚类，拼图交由多模态 AI 对比选优，并将去 EXIF 的缩略图推送到云端。
- **云端审核（Cloudflare Worker + D1 + R2）**：手机浏览器打开即可快速确认「保留 / 淘汰」，无需公网 IP 或内网穿透。
- **执行同步**：Ubuntu 后台拉取确认结果——淘汰照片带 `If-Match` 移入 OneDrive 回收站（或暂存目录），保留精选通过 `photoslibrary.appendonly` 幂等上传至 Google Photos 并按旅程建相册。

## 架构简图

```text
小米 14T (只开 OneDrive 自动备份)
          │
          ▼
       OneDrive (原片主库)
          │  ▲
          │  │ 确认后移入回收站 (If-Match) / 读取原图
          ▼  │
   Mac mini (Ubuntu Server · systemd)
     ├─ 1. EXIF 时间 + dHash 聚类
     ├─ 2. 编号拼图 → Gemini / OpenAI 选优
     ├─ 3. 去 EXIF 缩略图 ──▶ Cloudflare R2 + D1 审核台 ◀── 手机浏览器确认
     └─ 4. 精选原图 ──▶ Google Photos (photoslibrary.appendonly + 旅程相册)
```

## 快速开始

1. 云端 Worker 部署与 D1/R2 创建见 [部署指南](docs/setup.md)。
2. 本地开发与测试：

```bash
npm install
npm test
npm run build

python3 -m venv .venv
.venv/bin/pip install -r processor/requirements.txt
.venv/bin/python -m unittest discover -s tests/python -p 'test_*.py'
```

3. Ubuntu Server 定时任务配置见 [systemd 部署说明](deploy/systemd/README.md)。

## 安全与边界

- **Google Photos 权限最小化**：仅使用 `https://www.googleapis.com/auth/photoslibrary.appendonly` 上传精选与建相册，不读写 Google Photos 历史全库，也不执行云端删除。
- **防误删兜底**：AI 解析失败或缺失字段时全部默认 `keep`；OneDrive 回收站删除严格携带 `If-Match`，禁止自动重试；Google Photos 上传记录写入本地 SQLite 实现严格幂等。
- **隐私隔离**：上传到 R2 供审核的缩略图会彻底剥离 EXIF 与 GPS 坐标，确认完成后可自动清理 R2 临时预览。
