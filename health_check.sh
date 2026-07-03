#!/bin/bash
# health_check.sh - dashboard + 反代健康检查
# ponytail: 1 文件 1 个 function, 0 依赖. cron 每 5 分钟跑.
set -e
DASH_PORT=30800
DOMAIN="${HEALTH_DOMAIN:-ceshi.csx0574.top:25417}"

fail() { echo "[FAIL] $1"; exit 1; }
ok()   { echo "[OK]   $1"; }

# 1) 本机 dashboard
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:${DASH_PORT}/" 2>/dev/null || echo "000")
[ "$code" = "200" ] && ok "dashboard 127.0.0.1:${DASH_PORT} = 200" || fail "dashboard 127.0.0.1:${DASH_PORT} = ${code}"

# 2) 内网 IP dashboard
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://192.168.2.115:${DASH_PORT}/" 2>/dev/null || echo "000")
[ "$code" = "200" ] && ok "dashboard 192.168.2.115:${DASH_PORT} = 200" || fail "dashboard 192.168.2.115:${DASH_PORT} = ${code}"

# 3) 反代 (只测 TCP, SSL handshake 在浏览器中验证)
timeout 3 bash -c "</dev/tcp/$(echo $DOMAIN | cut -d: -f1)/$(echo $DOMAIN | cut -d: -f2)" 2>/dev/null \
    && ok "lucky ${DOMAIN} TCP 通" \
    || fail "lucky ${DOMAIN} TCP 不通"

# 4) dashboard 进程
pgrep -f "dashboard.py --port ${DASH_PORT}" > /dev/null \
    && ok "dashboard 进程在" \
    || fail "dashboard 进程不在 — 需要重启"

echo "--- 全部健康 ---"
