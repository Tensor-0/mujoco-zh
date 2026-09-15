#!/usr/bin/env bash
# restore_font.sh —— 把 Studio 字体还原成原版 Atkinson Hyperlegible
#
# 用法：
#   ./scripts/restore_font.sh
#   PYTHON=/path/to/mujoco/venv/bin/python3 ./scripts/restore_font.sh
#
# ⚠️ 关于「没有备份」的情况：
#   如果 `assets_backup/` 不存在（例如当初是手敲 cp 命令换的字体，
#   或者备份已被 pip 升级清掉），**本地就没有原版字体了**，
#   只能重装 mujoco 拿回来 —— 脚本会提示这条命令。
#
#   踩过的坑：venv 里的字体文件常与 pip/uv 缓存是**硬链接**，
#   用普通 `cp` 覆盖会**原地写穿**，把缓存里那份原版也一起改掉，
#   于是备份和缓存双双失效。install_font.sh 已改用
#   `cp --remove-destination` 断开硬链接来避免这件事。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

find_python() {
  local cands=()
  [[ -n "${PYTHON:-}" ]] && cands+=("$PYTHON")
  cands+=("python3")
  local g
  for g in "$HOME"/*/.venv/bin/python3 "$HOME"/.venv/bin/python3 "$HOME"/*/venv/bin/python3; do
    [[ -x "$g" ]] && cands+=("$g")
  done
  local seen="" c
  for c in "${cands[@]}"; do
    [[ "$seen" == *"|$c|"* ]] && continue
    seen+="|$c|"
    command -v "$c" >/dev/null 2>&1 || [[ -x "$c" ]] || continue
    if "$c" -c "import mujoco.experimental.studio" >/dev/null 2>&1; then
      printf '%s\n' "$c"; return 0
    fi
  done
  return 1
}

if ! PY="$(find_python)"; then
  echo "❌ 找不到装了 mujoco 的 python 解释器，请用 PYTHON=... 指定" >&2
  exit 1
fi

STUDIO="$("$PY" -c "
from mujoco.experimental import studio as s
print(list(s.__path__)[0])")" || {
  echo "❌ 无法定位 mujoco.experimental.studio" >&2; exit 1; }

ASSETS="$STUDIO/assets"
BACKUP="$STUDIO/assets_backup"

echo "解释器:   $PY"
echo "目标目录: $ASSETS"
echo "备份目录: $BACKUP"
echo

if [[ ! -d "$BACKUP" ]] || [[ -z "$(ls -A "$BACKUP" 2>/dev/null)" ]]; then
  echo "❌ 没有可用备份 —— 本地已无原版 Atkinson 字体。"
  echo
  echo "   还原办法：重装 mujoco"
  echo "     $PY -m pip install --force-reinstall mujoco"
  echo
  echo "   ⚠️ 重装后若字体仍是 CJK，说明 pip 缓存里那份也已被覆盖，"
  echo "      加 --no-cache-dir 再试一次。"
  exit 1
fi

n=0
for f in "$BACKUP"/*; do
  [[ -f "$f" ]] || continue
  base="$(basename "$f")"
  cp --remove-destination "$f" "$ASSETS/$base"
  echo "  ✅ 已还原 $base ($(stat -c%s "$ASSETS/$base" | awk '{printf "%.0f KB", $1/1024}'))"
  n=$((n + 1))
done

if (( n == 0 )); then
  echo "❌ 备份目录是空的" >&2
  exit 1
fi

echo
echo "完成，已还原 $n 个文件。"
echo "⚠️ 字体是启动时加载的 —— 重启 Studio 才生效。"
