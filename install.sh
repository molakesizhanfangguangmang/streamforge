#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DATA_DIR=${STREAMFORGE_DATA_DIR:-"$APP_DIR/data"}
DOWNLOAD_DIR=${STREAMFORGE_DOWNLOAD_DIR:-"$APP_DIR/downloads"}
PLUGIN_DIR=${STREAMFORGE_PLUGIN_DIR:-"$APP_DIR/plugins"}
COMPOSE_FILE="$APP_DIR/compose.generated.yaml"
NODE_VERSION=${STREAMFORGE_NODE_VERSION:-v24.13.1}

say() { printf '%s\n' "$*"; }
ask() { printf '%s [y/N] ' "$1"; read answer || answer=''; case "$answer" in y|Y|yes|YES) return 0;; *) return 1;; esac; }
find_node() {
  for p in /vol1/@appcenter/nodejs_v24/bin/node /var/apps/nodejs_v24/target/bin/node /usr/local/bin/node /usr/bin/node; do
    if [ -x "$p" ]; then printf '%s\n' "$p"; return 0; fi
  done
  return 1
}
node_source=none
node_host_path=''
node_data_path=''

mkdir -p "$DATA_DIR" "$DOWNLOAD_DIR" "$PLUGIN_DIR"

if host_node=$(find_node 2>/dev/null); then
  host_version=$($host_node --version 2>/dev/null || true)
  say "检测到宿主机 Node.js ${host_version:-版本未知}: $host_node"
  if ask '是否使用宿主机 Node.js？'; then
    node_source=host
    node_host_path=$host_node
  fi
fi

if [ "$node_source" = none ]; then
  if ask '是否安装流铸自带的对应架构 Node.js？'; then
    arch=$(uname -m)
    case "$arch" in
      aarch64|arm64) node_arch=arm64 ;;
      x86_64|amd64) node_arch=x64 ;;
      *) say "不支持自动安装 Node.js 的架构：$arch"; exit 1 ;;
    esac
    tools="$DATA_DIR/tools/node"
    mkdir -p "$DATA_DIR/tools"
    archive="node-${NODE_VERSION}-linux-${node_arch}.tar.xz"
    url="https://nodejs.org/dist/${NODE_VERSION}/${archive}"
    say "下载对应架构 Node.js：$url"
    if command -v curl >/dev/null 2>&1; then curl -fL --retry 3 "$url" -o "$DATA_DIR/tools/$archive"; else wget -O "$DATA_DIR/tools/$archive" "$url"; fi
    rm -rf "$tools.tmp"
    mkdir -p "$tools.tmp"
    tar -xJf "$DATA_DIR/tools/$archive" -C "$tools.tmp" --strip-components=1
    mv "$tools.tmp" "$tools"
    rm -f "$DATA_DIR/tools/$archive"
    node_source=managed
    node_data_path="$tools"
  else
    say '不使用 Node.js。基础下载功能仍可运行，但 YouTube 部分解析可能受限。'
  fi
fi

cat > "$COMPOSE_FILE" <<EOF
services:
  streamforge:
    build: .
    container_name: streamforge
    restart: unless-stopped
    ports:
      - "${STREAMFORGE_PORT:-8081}:8081"
    environment:
      STREAMFORGE_DATA: /data
      STREAMFORGE_DOWNLOADS: /downloads
      STREAMFORGE_NODE: ${node_source}
    volumes:
      - ./data:/data
      - ./downloads:/downloads
      - ./plugins:/plugins
EOF

if [ "$node_source" = host ]; then
  printf '      - %s:/usr/local/bin/node:ro\n' "$node_host_path" >> "$COMPOSE_FILE"
elif [ "$node_source" = managed ]; then
  printf '      - %s:/opt/node:ro\n' "$node_data_path" >> "$COMPOSE_FILE"
fi

say "使用 Compose 配置：$COMPOSE_FILE"
if command -v docker >/dev/null 2>&1; then
  docker compose -f "$COMPOSE_FILE" up -d --build
  say "流铸已启动：http://127.0.0.1:${STREAMFORGE_PORT:-8081}"
else
  say '未找到 Docker，请安装 Docker Compose 后重新运行。'
  exit 1
fi
