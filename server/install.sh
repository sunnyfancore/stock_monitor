#!/usr/bin/env bash
# 盯盘小窗 · 加密中转服务 —— Docker Compose 一键部署
#
# 用法：
#   bash install.sh                交互选择部署模式（再次运行 = 按上次设置更新/重建）
#   bash install.sh standalone     中转服务自己做 TLS，直接对外（默认端口 8443）
#   bash install.sh nginx          同时启动 nginx 容器做 TLS + 反代（默认端口 443）
#   bash install.sh host-nginx     服务器已有 nginx：只启动中转服务，并生成 nginx 配置片段
#   bash install.sh url            查看客户端连接串（含令牌，勿外传）
#   bash install.sh status | logs | down
#
# 无人值守（GitHub Actions 等）可用环境变量：
#   NONINTERACTIVE=1          不提问，全部用默认值 / 环境变量
#   RELAY_PUBLIC_HOST=1.2.3.4 服务器公网 IP（客户端只接受 IP）
#   RELAY_PORT=8443           对外端口
#   RELAY_LOCAL_PORT=18080    host-nginx 模式下中转服务监听的本机端口
#   RELAY_IMAGE=ghcr.io/...   指定镜像则拉取，不在本机构建
#   HIDE_URL=1                不在输出里打印连接串（CI 日志里绝不能出现令牌）
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# 读取上次保存的设置（.env）；命令行环境变量优先
envget() { [ -f .env ] && grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true; }

file_for() {
  case "$1" in
    standalone) echo docker-compose.yml ;;
    nginx)      echo docker-compose.nginx.yml ;;
    host-nginx) echo docker-compose.host-nginx.yml ;;
    *) return 1 ;;
  esac
}
default_port() { [ "$1" = nginx ] && echo 443 || echo 8443; }

interactive() { [ "${NONINTERACTIVE:-0}" != 1 ] && [ -t 0 ]; }
ask() {   # ask 变量名 "提示" 默认值
  local __var="$1" __prompt="$2" __def="$3" __ans=""
  if interactive; then read -rp "$__prompt [$__def]: " __ans || true; fi
  printf -v "$__var" '%s' "${__ans:-$__def}"
}

# ---------------------------------------------------------------- 环境检查
command -v docker >/dev/null 2>&1 || die "未安装 Docker：https://docs.docker.com/engine/install/"
docker info >/dev/null 2>&1 || die "无法访问 Docker（请用 root 运行，或把当前用户加入 docker 组）"
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  die "未找到 docker compose 插件：https://docs.docker.com/compose/install/linux/"
fi

RELAY_MODE="${RELAY_MODE:-$(envget RELAY_MODE)}"
dc() { "${DC[@]}" -f "$(file_for "$RELAY_MODE")" "$@"; }
# 连接串优先从 data/url 读（日志会轮转），读不到再从日志里找
show_url() {
  local u
  u="$(docker exec stock-relay cat /data/url 2>/dev/null || true)"
  [ -n "$u" ] || u="$(docker logs stock-relay 2>&1 | grep -o 'swr://[^[:space:]]*' | tail -n 1 || true)"
  echo "$u"
}

# ---------------------------------------------------------------- 子命令
CMD="${1:-}"
case "$CMD" in
  url|status|logs|down)
    [ -n "$RELAY_MODE" ] || die "还没有部署过，先运行：bash install.sh"
    case "$CMD" in
      url)    u="$(show_url)"; [ -n "$u" ] || die "没找到连接串，确认容器在运行：bash install.sh status"
              echo "$u" ;;
      status) dc ps ;;
      logs)   docker logs --tail 50 stock-relay 2>&1 | sed -E 's#swr://[^[:space:]]+#swr://***（用 bash install.sh url 查看）#' ;;
      down)   dc down; say "已停止（证书和令牌保留在 ./data，重新部署后连接串不变）" ;;
    esac
    exit 0 ;;
  ""|standalone|nginx|host-nginx) ;;
  *) die "未知参数：$CMD（可用：standalone / nginx / host-nginx / url / status / logs / down）" ;;
esac

# ---------------------------------------------------------------- 选择模式
PREV_MODE="$(envget RELAY_MODE)"
MODE="${CMD:-$RELAY_MODE}"
if [ -z "$MODE" ]; then
  if interactive; then
    echo "请选择部署模式："
    echo "  1) standalone  中转服务自己做 TLS，直接对外（服务器上没有 nginx）"
    echo "  2) nginx       同时启动 nginx 容器做 TLS + 反代（推荐，对外就是普通 https 站点）"
    echo "  3) host-nginx  服务器已经装了 nginx，只启动中转服务并生成 nginx 配置"
    read -rp "输入 1/2/3 [2]: " n || true
    case "${n:-2}" in 1) MODE=standalone ;; 3) MODE=host-nginx ;; *) MODE=nginx ;; esac
  else
    MODE=nginx                                  # 无人值守首次部署默认 nginx（与交互默认一致）
  fi
fi
file_for "$MODE" >/dev/null || die "未知模式：$MODE"
RELAY_MODE="$MODE"

# ---------------------------------------------------------------- 公网 IP / 端口
detect_ip() {
  local ip="" u
  for u in https://api.ipify.org https://ipv4.icanhazip.com https://ifconfig.me; do
    ip="$(curl -4 -fsS --max-time 4 "$u" 2>/dev/null | tr -d '[:space:]' || true)"
    [ -n "$ip" ] && break
  done
  [ -n "$ip" ] || ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  echo "$ip"
}
HOST_IN="${RELAY_PUBLIC_HOST:-$(envget RELAY_PUBLIC_HOST)}"
[ -n "$HOST_IN" ] || HOST_IN="$(detect_ip)"
ask RELAY_PUBLIC_HOST "服务器公网 IP（客户端按 IP 连接，不要填域名）" "$HOST_IN"
if ! [[ "$RELAY_PUBLIC_HOST" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ || "$RELAY_PUBLIC_HOST" == *:* ]]; then
  warn "“$RELAY_PUBLIC_HOST” 不是 IP 地址：客户端只接受 IP（用域名会暴露服务器名），请填公网 IP"
fi

PORT_IN="${RELAY_PORT:-}"
if [ -z "$PORT_IN" ] && [ "$PREV_MODE" = "$MODE" ]; then PORT_IN="$(envget RELAY_PORT)"; fi
ask RELAY_PORT "对外端口" "${PORT_IN:-$(default_port "$MODE")}"
[[ "$RELAY_PORT" =~ ^[0-9]+$ ]] && [ "$RELAY_PORT" -ge 1 ] && [ "$RELAY_PORT" -le 65535 ] \
  || die "端口不合法：$RELAY_PORT"

RELAY_LOCAL_PORT="${RELAY_LOCAL_PORT:-$(envget RELAY_LOCAL_PORT)}"
RELAY_LOCAL_PORT="${RELAY_LOCAL_PORT:-18080}"
RELAY_IMAGE="${RELAY_IMAGE:-$(envget RELAY_IMAGE)}"

cat > .env <<EOF
# 由 install.sh 生成；修改后重新运行 bash install.sh 生效
RELAY_MODE=$RELAY_MODE
RELAY_PUBLIC_HOST=$RELAY_PUBLIC_HOST
RELAY_PORT=$RELAY_PORT
RELAY_LOCAL_PORT=$RELAY_LOCAL_PORT
RELAY_IMAGE=$RELAY_IMAGE
EOF
chmod 600 .env

# ---------------------------------------------------------------- 数据目录（证书 / 令牌）
mkdir -p data
chmod 700 data 2>/dev/null || true                 # 已归 10001 所有时非 root 改不了，不影响
if [ "$(stat -c %u data)" != "10001" ]; then        # 容器内以 uid 10001 运行
  if [ "$(id -u)" = 0 ]; then chown 10001:10001 data
  elif command -v sudo >/dev/null 2>&1; then
    if interactive; then sudo chown 10001:10001 data
    else sudo -n chown 10001:10001 data || die "需要 root 权限修改 ./data 属主（CI 下请用 root 或免密 sudo）"; fi
  else die "需要把 ./data 的属主改为 10001，请用 root 运行"; fi
fi

# ---------------------------------------------------------------- 启动
if [ -n "$PREV_MODE" ] && [ "$PREV_MODE" != "$MODE" ] && file_for "$PREV_MODE" >/dev/null; then
  say "切换模式：$PREV_MODE → $MODE，先停止旧服务"
  "${DC[@]}" -f "$(file_for "$PREV_MODE")" down --remove-orphans || true
fi
# 清理旧版用 docker run 部署、不归 compose 管理的同名容器
for c in stock-relay stock-relay-nginx; do
  if docker inspect "$c" >/dev/null 2>&1; then
    lbl="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$c" 2>/dev/null || true)"
    if [ -z "$lbl" ] || [ "$lbl" = "<no value>" ]; then     # 没有 compose 标签 = 旧版 docker run 部署的
      say "移除旧版容器 $c"
      docker rm -f "$c" >/dev/null
    fi
  fi
done

say "部署模式：$MODE（$(file_for "$MODE")），对外端口：$RELAY_PORT"
# --force-recreate：nginx 只在启动时解析一次中转服务地址，中转容器重建后必须一起重建 nginx；
# 也保证删掉 data/token、证书后重新运行能立刻生成新的
up_fail() {
  docker logs --tail 30 stock-relay 2>&1 | sed -E 's#swr://[^[:space:]]+#swr://***#' || true
  die "启动失败（常见原因：端口 $RELAY_PORT 已被占用，可换端口重新运行或改用 host-nginx 模式）"
}
if [ -n "$RELAY_IMAGE" ]; then
  dc pull
  dc up -d --no-build --force-recreate --remove-orphans || up_fail
else
  dc up -d --build --force-recreate --remove-orphans || up_fail
fi

say "等待服务就绪…"
ok=""
for _ in $(seq 1 40); do
  st="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' stock-relay 2>/dev/null || true)"
  if [ "$st" = healthy ]; then ok=1; break; fi
  sleep 2
done
[ -n "$ok" ] || { docker logs --tail 30 stock-relay 2>&1 | sed -E 's#swr://[^[:space:]]+#swr://***#'; die "服务没有正常启动，见上方日志"; }
if [ "$MODE" = nginx ]; then
  sleep 2
  if [ "$(docker inspect -f '{{.State.Running}}' stock-relay-nginx 2>/dev/null || true)" != true ]; then
    docker logs --tail 30 stock-relay-nginx 2>&1 || true
    die "nginx 容器没有启动（常见原因：端口 $RELAY_PORT 已被占用；可改端口重新运行，或改用 host-nginx 模式）"
  fi
fi

# ---------------------------------------------------------------- host-nginx：生成配置片段
if [ "$MODE" = host-nginx ]; then
  sed -e "s#__DATA_DIR__#$(pwd)/data#g" -e "s#__LOCAL_PORT__#$RELAY_LOCAL_PORT#g" \
      -e "s#__PORT__#$RELAY_PORT#g" nginx/host-nginx.conf.example > nginx/host-nginx.conf
  say "已生成 nginx 配置：$(pwd)/nginx/host-nginx.conf"
  if interactive && [ "$(id -u)" = 0 ] && [ -d /etc/nginx/conf.d ] && command -v nginx >/dev/null 2>&1; then
    read -rp "自动安装到 /etc/nginx/conf.d/stock-relay.conf 并重载 nginx？[y/N]: " yn || true
    if [[ "${yn:-N}" =~ ^[Yy]$ ]]; then
      cp nginx/host-nginx.conf /etc/nginx/conf.d/stock-relay.conf
      if nginx -t; then
        systemctl reload nginx 2>/dev/null || nginx -s reload
        say "nginx 已重载"
      else
        rm -f /etc/nginx/conf.d/stock-relay.conf
        warn "nginx -t 检查未通过，已撤回。常见原因：该端口上已有别的 default_server，或端口被占用"
      fi
    fi
  else
    echo "    把它放进 nginx 配置目录（如 /etc/nginx/conf.d/），然后：nginx -t && systemctl reload nginx"
  fi
fi

# ---------------------------------------------------------------- 完成
echo
say "部署完成"
echo "    1) 在云服务器安全组 / 防火墙放行 TCP $RELAY_PORT"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  echo "       本机启用了 ufw：ufw allow $RELAY_PORT/tcp"
fi
if [ "${HIDE_URL:-0}" = 1 ]; then
  echo "    2) 在服务器上执行 bash $(pwd)/install.sh url 获取客户端连接串"
else
  echo "    2) 客户端连接串（右键任务栏条 → 加密中转 → 设置连接串，粘贴回车；含令牌，勿外传）："
  echo
  echo "       $(show_url)"
  echo
fi
