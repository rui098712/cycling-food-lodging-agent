#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_DIR="/opt/cycling-food-lodging-agent"
SERVICE_USER="cyclingagent"

if [[ "${EUID}" -ne 0 ]]; then
    echo "请使用 root 用户运行此脚本。" >&2
    exit 1
fi

if [[ "${PROJECT_DIR}" != "${EXPECTED_DIR}" ]]; then
    echo "项目应位于 ${EXPECTED_DIR}，当前位于 ${PROJECT_DIR}。" >&2
    exit 1
fi

if [[ ! -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
    echo "没有找到项目虚拟环境，请先创建 .venv 并安装 requirements.txt。" >&2
    exit 1
fi

if [[ ! -s "${PROJECT_DIR}/.env" ]]; then
    echo "没有找到有效的 .env，请先配置高德和模型密钥。" >&2
    exit 1
fi

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0700 \
    "${PROJECT_DIR}/.runtime"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${PROJECT_DIR}/.runtime"
chown "${SERVICE_USER}:${SERVICE_USER}" "${PROJECT_DIR}/.env"
chmod 0600 "${PROJECT_DIR}/.env"

install -m 0644 "${PROJECT_DIR}/deploy/cycling-agent.service" \
    /etc/systemd/system/cycling-agent.service
install -m 0644 "${PROJECT_DIR}/deploy/nginx.conf" \
    /etc/nginx/sites-available/cycling-agent
ln -sfn /etc/nginx/sites-available/cycling-agent \
    /etc/nginx/sites-enabled/cycling-agent

# 禁用 Ubuntu 自带的 Nginx 欢迎页，让公网 IP 指向本项目。
if [[ -L /etc/nginx/sites-enabled/default ]]; then
    unlink /etc/nginx/sites-enabled/default
fi

nginx -t
systemctl daemon-reload
systemctl enable --now cycling-agent
systemctl enable nginx
systemctl restart nginx

for attempt in $(seq 1 15); do
    if curl --fail --silent http://127.0.0.1:8000/api/health >/dev/null; then
        break
    fi
    if [[ "${attempt}" -eq 15 ]]; then
        echo "cycling-agent 启动超时，请运行 systemctl status cycling-agent 查看状态。" >&2
        exit 1
    fi
    sleep 1
done

curl --fail --silent --show-error http://127.0.0.1:8000/api/health
echo
curl --fail --silent --show-error http://127.0.0.1/api/health
echo
echo "部署完成：cycling-agent 和 nginx 已启动。"
