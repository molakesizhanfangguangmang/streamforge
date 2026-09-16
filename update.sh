#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE=${STREAMFORGE_COMPOSE_FILE:-"$APP_DIR/compose.generated.yaml"}
REPOSITORY=${STREAMFORGE_REPOSITORY:-molakesizhanfangguangmang/streamforge}
PORT=${STREAMFORGE_PORT:-8081}
PROBE_TRIES=${STREAMFORGE_PROBE_TRIES:-12}
PROBE_INTERVAL=${STREAMFORGE_PROBE_INTERVAL:-3}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

say() { printf '%s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { say "缺少命令：$1"; exit 1; }; }
need curl; need docker; need sha256sum; need zstd; need python3
[ -f "$COMPOSE_FILE" ] || { say "找不到 Compose 文件：$COMPOSE_FILE"; exit 1; }

health_json() { curl --noproxy '*' -fsS "http://127.0.0.1:$PORT/api/health" 2>/dev/null; }
# 只看 /api/health 会漏掉「服务活着但静态页面没了」那类坏镜像，首页也一起要。
# --noproxy 是硬约束：Release 下载可以走代理，本机探针绝不能被代理接走。
probe() {
  curl --noproxy '*' -fsS -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null || return 1
  health_json | grep -q '"ok": *true' || return 1
  return 0
}
container_id() { docker compose -f "$COMPOSE_FILE" ps -q 2>/dev/null | head -n1 || true; }
image_tags() { docker inspect -f '{{join .RepoTags " "}}' "$1" 2>/dev/null || true; }
live_version() {
  health_json | python3 -c 'import json,sys
d=json.load(sys.stdin); b=d.get("build") or {}
print("v%s 构建 %s" % (d.get("version") or "未知", b.get("sha") or "无构建戳（本地构建或热部署）"))' 2>/dev/null || printf '未知'
}

# --- 更新前先留住回退要用的东西：现在跑的是哪个镜像、它挂着哪些标签 ---
old_container=$(container_id)
old_image=''
if [ -n "$old_container" ]; then
  old_image=$(docker inspect -f '{{.Image}}' "$old_container" 2>/dev/null || true)
  say "当前镜像：$old_image $(image_tags "$old_image")"
fi
old_tags=$(image_tags "$old_image")
compose_tag=$(docker compose -f "$COMPOSE_FILE" config --images 2>/dev/null | head -n1 || true)

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
new_image=''
if [ -n "$compose_tag" ]; then
  new_image=$(docker image inspect -f '{{.Id}}' "$compose_tag" 2>/dev/null || true)
fi
cd "$APP_DIR"
docker compose -f "$COMPOSE_FILE" up -d --force-recreate

# --- 探针：新容器要自己站起来才算更新成功 ---
tries=0
while [ "$tries" -lt "$PROBE_TRIES" ]; do
  sleep "$PROBE_INTERVAL"
  if probe; then
    say "更新完成：$tag"
    say "当前运行 $(live_version)"
    exit 0
  fi
  tries=$((tries + 1))
done

# --- 探针没过：先留下现场，再把旧镜像放回去 ---
say "更新失败：$tag 在 $((PROBE_TRIES * PROBE_INTERVAL)) 秒内没有通过健康检查"
say "--- 容器状态"
docker compose -f "$COMPOSE_FILE" ps 2>&1 | tail -5 || true
say "--- 新容器日志（最后 30 行）"
docker compose -f "$COMPOSE_FILE" logs --tail 30 2>&1 | tail -30 || true
if [ -z "$old_image" ]; then
  say "更新前没有记录到正在运行的旧镜像，无法自动回退。请手工处理：$COMPOSE_FILE"
  exit 1
fi
say "--- 回退到 $old_image"
restore_tags=$old_tags
if [ -z "$restore_tags" ]; then
  restore_tags=$compose_tag
fi
for one_tag in $restore_tags; do docker tag "$old_image" "$one_tag"; done
docker compose -f "$COMPOSE_FILE" up -d --force-recreate
tries=0
while [ "$tries" -lt "$PROBE_TRIES" ]; do
  sleep "$PROBE_INTERVAL"
  if probe; then
    say "已回退到旧版本，服务已恢复（$(live_version)）"
    if [ -n "$new_image" ]; then
      say "坏版本镜像仍在本地：$new_image"
    fi
    exit 1
  fi
  tries=$((tries + 1))
done
say "回退后仍不健康，需要人工处理：$COMPOSE_FILE"
exit 1
