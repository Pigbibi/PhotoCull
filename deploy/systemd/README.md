# Mac mini (Ubuntu Server) systemd 部署

假设项目安装在 `/opt/photocull`，运行用户为 `photocull`。

```bash
sudo useradd --system --create-home --home-dir /opt/photocull photocull || true
sudo chown -R photocull:photocull /opt/photocull

cd /opt/photocull
sudo -u photocull python3 -m venv .venv
sudo -u photocull .venv/bin/pip install -r processor/requirements.txt
sudo -u photocull cp processor/.env.example .env
# 编辑 /opt/photocull/.env 填入微软、Google Photos、AI 与 Worker 凭据
sudo chmod 600 /opt/photocull/.env

sudo cp deploy/systemd/photocull-*.service /etc/systemd/system/
sudo cp deploy/systemd/photocull-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now photocull-scan.timer photocull-exec.timer
```

常用查看命令：

```bash
systemctl list-timers 'photocull-*'
journalctl -u photocull-scan.service -n 50 --no-pager
journalctl -u photocull-exec.service -n 50 --no-pager
```
