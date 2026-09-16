#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE=${STREAMFORGE_COMPOSE_FILE:-"$APP_DIR/compose.generated.yaml"}
REPOSITORY=${STREAMFORGE_REPOSITORY:-molakesizhanfangguangmang/streamforge}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

say() { printf '%s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { say "缺少命令：$1"; exit 1; }; }
need curl; need docker; need sha256sum; need zstd
[ -f "$COMPOSE_FILE" ] || { say "找不到 Compose 文件：$COMPOSE_FILE"; exit 1; }

meta="$WORK/release.json"
curl -fsSL "https://api.github.com/repos/$REPOSITORY/releases/latest" -o "$meta"
python3 - "$meta" "$WORK" <<'PY'
import json, sys
release=json.load(open(sys.argv[1]))
assets={x['name']: x['browser_download_url'] for x in release.get('assets', [])}
image=next((n for n in assets if n.endswith('.tar.zst')), None)
checksum=(image + '.sha256') if image else None
if not image or checksum not in assets:
    raise SystemExit('Release 缺少 streamforge-arm64.tar.zst 或对应 .sha256 文件')
open(sys.argv[2] + '/urls', 'w').write(assets[image]+'\n'+assets[checksum]+'\n'+release['tag_name']+'\n')
PY
image_url=$(sed -n '1p' "$WORK/urls")
checksum_url=$(sed -n '2p' "$WORK/urls")
tag=$(sed -n '3p' "$WORK/urls")
say "将更新到 $tag。下载并校验 ARM64 Docker archive。"
curl -fL --retry 3 "$image_url" -o "$WORK/streamforge-arm64.tar.zst"
curl -fL --retry 3 "$checksum_url" -o "$WORK/streamforge-arm64.tar.zst.sha256"
(cd "$WORK" && sha256sum -c streamforge-arm64.tar.zst.sha256)
cp "$COMPOSE_FILE" "$COMPOSE_FILE.backup-$(date +%Y%m%d-%H%M%S)"
zstd -dc "$WORK/streamforge-arm64.tar.zst" | docker load
cd "$APP_DIR"
docker compose -f "$COMPOSE_FILE" up -d --force-recreate
sleep 2
curl -fsS http://127.0.0.1:${STREAMFORGE_PORT:-8081}/api/health >/dev/null
say "更新完成：$tag"
