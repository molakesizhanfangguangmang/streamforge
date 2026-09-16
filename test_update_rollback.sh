#!/bin/sh
# 更新链的回退行为测试：用桩 docker / curl / zstd 驱动 update.sh，断言三件事 ——
#   1. 探针通过：退出 0，且绝不碰旧镜像；
#   2. 探针失败：把旧镜像的标签打回去、再重建一次、退出非零；
#   3. 更新前没有在跑的容器：不假装回退，明确报「无法自动回退」。
# 真实 docker 不参与，所以 CI 里能跑。测的是脚本的控制流与退出码，不是 docker 语义。
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT INT TERM
BIN=$SANDBOX/bin
STATE=$SANDBOX/state
PORT=18181
mkdir -p "$BIN" "$STATE"

OLD_IMAGE='sha256:1111111111111111111111111111111111111111111111111111111111111111'
NEW_IMAGE='sha256:2222222222222222222222222222222222222222222222222222222222222222'
CONTAINER_ID='deadbeefcafe'
COMPOSE_TAG='streamforge:latest'
export OLD_IMAGE NEW_IMAGE CONTAINER_ID COMPOSE_TAG SANDBOX STATE

failures=0
check() {
  if [ "$2" = 1 ]; then printf 'PASS  %s\n' "$1"
  else printf 'FAIL  %s%s\n' "$1" "${3:+  -> $3}"; failures=$((failures + 1)); fi
}

# --- 假 Release 资产：校验和必须对得上假镜像包，否则 update.sh 会在这里就停下 ---
printf 'fake docker archive\n' > "$SANDBOX/payload"
payload_sum=$(sha256sum "$SANDBOX/payload" | cut -d' ' -f1)
printf '%s  streamforge-arm64.tar.zst\n' "$payload_sum" > "$SANDBOX/sha256"
cat > "$SANDBOX/release.json" <<JSON
{"tag_name":"v1.0.0","assets":[
 {"name":"streamforge-arm64.tar.zst","browser_download_url":"http://fake.invalid/streamforge-arm64.tar.zst"},
 {"name":"streamforge-arm64.tar.zst.sha256","browser_download_url":"http://fake.invalid/streamforge-arm64.tar.zst.sha256"}]}
JSON
printf 'services:\n  streamforge:\n    image: streamforge:latest\n' > "$SANDBOX/compose.yaml"

# --- 桩：curl（假 Release、假资产、假探针） ---
cat > "$BIN/curl" <<'SH'
#!/bin/sh
out=''; url=''; want_out=0
for arg in "$@"; do
  if [ "$want_out" = 1 ]; then out=$arg; want_out=0; continue; fi
  case "$arg" in
    -o) want_out=1 ;;
    http://*|https://*) url=$arg ;;
  esac
done
case "$url" in
  *api.github.com*) cp "$SANDBOX/release.json" "$out"; exit 0 ;;
  *.sha256) cp "$SANDBOX/sha256" "$out"; exit 0 ;;
  *.tar.zst) cp "$SANDBOX/payload" "$out"; exit 0 ;;
  http://127.0.0.1:*)
    [ -f "$STATE/failing" ] && exit 7
    case "$url" in
      */api/health) printf '{"ok": true, "version": "1.0.0", "build": {"sha": "abcdef123456", "tracked": true}}\n' ;;
    esac
    exit 0 ;;
esac
exit 1
SH

# --- 桩：docker（compose up 的次数就是「新版起没起来」这个事实） ---
cat > "$BIN/docker" <<'SH'
#!/bin/sh
printf 'docker %s\n' "$*" >> "$STATE/calls"
case "$1" in
  tag) exit 0 ;;
  inspect)
    case "$3" in
      *RepoTags*) printf '%s\n' "$COMPOSE_TAG" ;;
      *) printf '%s\n' "$OLD_IMAGE" ;;
    esac
    exit 0 ;;
  image) printf '%s\n' "$NEW_IMAGE"; exit 0 ;;
  load) cat > /dev/null; exit 0 ;;
  compose)
    shift
    sub=''; skip=0
    for arg in "$@"; do
      if [ "$skip" = 1 ]; then skip=0; continue; fi
      case "$arg" in
        -f) skip=1; continue ;;
        -*) continue ;;
      esac
      sub=$arg; break
    done
    case "$sub" in
      ps)
        case " $* " in *" -q "*) if [ "$SCENARIO" = nocontainer ]; then exit 0; fi ;; esac
        printf '%s\n' "$CONTAINER_ID" ;;
      config) printf '%s\n' "$COMPOSE_TAG" ;;
      up)
        ups=$(cat "$STATE/ups" 2>/dev/null || printf '0')
        ups=$((ups + 1)); printf '%s\n' "$ups" > "$STATE/ups"
        if [ "$SCENARIO" != good ]; then
          if [ "$ups" = 1 ]; then touch "$STATE/failing"; else rm -f "$STATE/failing"; fi
        fi ;;
      logs) printf '[fake] 新容器起不来\n' ;;
    esac
    exit 0 ;;
esac
exit 0
SH

# --- 桩：zstd ---
cat > "$BIN/zstd" <<'SH'
#!/bin/sh
cat "${2:-/dev/null}"
SH
chmod +x "$BIN/curl" "$BIN/docker" "$BIN/zstd"

run_case() {
  SCENARIO=$1
  rm -f "$STATE/calls" "$STATE/ups" "$STATE/failing"
  SCENARIO=$SCENARIO PATH="$BIN:$PATH" STREAMFORGE_COMPOSE_FILE="$SANDBOX/compose.yaml" \
    STREAMFORGE_PROBE_TRIES=2 STREAMFORGE_PROBE_INTERVAL=0 STREAMFORGE_PORT=$PORT \
    sh "$ROOT/update.sh" > "$SANDBOX/out" 2>&1 && RC=0 || RC=$?
  CALLS=$(cat "$STATE/calls" 2>/dev/null || true)
  OUT=$(cat "$SANDBOX/out")
  REBUILDS=$(printf '%s\n' "$CALLS" | grep -c ' up -d ' || true)
  RETAGS=$(printf '%s\n' "$CALLS" | grep -c '^docker tag ' || true)
  rm -f "$SANDBOX"/compose.yaml.backup-* 2>/dev/null || true
}

# --- 1. 探针通过 ---
run_case good
check '探针通过时退出码为 0' "$([ "$RC" = 0 ] && printf 1 || printf 0)" "rc=$RC"
check '打印更新完成与版本' "$(printf '%s' "$OUT" | grep -q '更新完成：v1.0.0' && printf 1 || printf 0)" "$OUT"
check '打印出在跑的构建戳' "$(printf '%s' "$OUT" | grep -q '当前运行 v1.0.0 构建 abcdef123456' && printf 1 || printf 0)" "$OUT"
check '顺利时不重建第二次' "$([ "$REBUILDS" = 1 ] && printf 1 || printf 0)" "重建 $REBUILDS 次"
check '顺利时不碰旧镜像标签' "$([ "$RETAGS" = 0 ] && printf 1 || printf 0)" "打标签 $RETAGS 次"

# --- 2. 探针失败 → 回退 ---
run_case bad
check '探针失败时退出非零' "$([ "$RC" != 0 ] && printf 1 || printf 0)" "rc=$RC"
check '报告更新失败' "$(printf '%s' "$OUT" | grep -q '更新失败：v1.0.0' && printf 1 || printf 0)" "$OUT"
check '把旧镜像标签打回去' "$(printf '%s' "$CALLS" | grep -q "^docker tag $OLD_IMAGE $COMPOSE_TAG\$" && printf 1 || printf 0)" "$CALLS"
check '回退后重建一次（共两次）' "$([ "$REBUILDS" = 2 ] && printf 1 || printf 0)" "重建 $REBUILDS 次"
check '报告已回退且服务恢复' "$(printf '%s' "$OUT" | grep -q '已回退到旧版本，服务已恢复' && printf 1 || printf 0)" "$OUT"
check '交出坏版本镜像 ID 备用' "$(printf '%s' "$OUT" | grep -q "坏版本镜像仍在本地：$NEW_IMAGE" && printf 1 || printf 0)" "$OUT"

# --- 3. 更新前没有在跑的容器 ---
run_case nocontainer
check '没有旧容器时退出非零' "$([ "$RC" != 0 ] && printf 1 || printf 0)" "rc=$RC"
check '明确说明无法自动回退' "$(printf '%s' "$OUT" | grep -q '无法自动回退' && printf 1 || printf 0)" "$OUT"
check '没有旧容器时不假装打标签' "$([ "$RETAGS" = 0 ] && printf 1 || printf 0)" "打标签 $RETAGS 次"
check '没有旧容器时不重建第二次' "$([ "$REBUILDS" = 1 ] && printf 1 || printf 0)" "重建 $REBUILDS 次"

printf '\n'
if [ "$failures" = 0 ]; then printf '全部通过\n'
else printf '失败 %d 项\n' "$failures"; fi
exit "$([ "$failures" = 0 ] && printf 0 || printf 1)"
