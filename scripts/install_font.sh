#!/usr/bin/env bash
# install_font.sh —— 把 Studio 主字体换成 CJK 字体（中文支持的前提）
#
# 为什么需要：
#   Studio 主字体 Atkinson Hyperlegible **不含任何中文字形**。
#   而 ImGui 1.92 起引入动态字形光栅化（"不需要再指定 glyph ranges"），
#   所以「换个字体文件」就能让中文显示出来 —— 无需改任何代码。
#
# 为什么还要抽 face：
#   window.cc 里 ImFontConfig 没设 FontNo，ImGui 取 **face 0**。
#   而 NotoSansCJK-Regular.ttc 的 face 0 是 **JP**（日文），SC 在 face 2。
#   直接用 TTC ⇒ 简体用户看到日文字形变体。见 extract_sc_font.py。
#
# 用法：
#   ./scripts/install_font.sh [CJK字体路径] [语言]
#   PYTHON=/path/to/mujoco/venv/bin/python3 ./scripts/install_font.sh
#
# 还原：
#   ./scripts/restore_font.sh
set -euo pipefail

CJK_FONT="${1:-/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc}"
LANG_PREF="${2:-SC}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

#: 原版 Atkinson 字体大约 200 KB；CJK 字体 10 MB+
ORIGINAL_MAX_BYTES=$((2 * 1024 * 1024))

[[ -f "$CJK_FONT" ]] || { echo "❌ 找不到 CJK 字体: $CJK_FONT" >&2; exit 1; }

# ══════════════════════════════════════════════════════════
# 一、找「装了 mujoco 的那个 python」
#     ⚠️ 不能硬编码 python3 —— 很多机器上默认 python3 不是跑 mujoco 的那个
# ══════════════════════════════════════════════════════════
find_python() {
  local cands=()
  [[ -n "${PYTHON:-}" ]] && cands+=("$PYTHON")
  cands+=("python3")
  # 常见 venv 位置
  local g
  for g in "$HOME"/*/.venv/bin/python3 "$HOME"/.venv/bin/python3 "$HOME"/*/venv/bin/python3; do
    [[ -x "$g" ]] && cands+=("$g")
  done
  # 去重
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
  echo "❌ 找不到装了 mujoco 的 python 解释器。" >&2
  echo "   用过的方法：\$PYTHON 环境变量 / python3 / \$HOME/*/.venv/bin/python3" >&2
  echo "   请显式指定，例如：" >&2
  echo "     PYTHON=/path/to/venv/bin/python3 $0 $*" >&2
  exit 1
fi
echo "解释器:   $PY"
echo "CJK 字体: $CJK_FONT ($(stat -c%s "$CJK_FONT" | awk '{printf "%.1f MB", $1/1048576}'))"

STUDIO="$("$PY" -c "
from mujoco.experimental import studio as s
print(list(s.__path__)[0])")" || {
  echo "❌ 无法定位 mujoco.experimental.studio" >&2; exit 1; }

ASSETS="$STUDIO/assets"
BACKUP="$STUDIO/assets_backup"
echo "目标目录: $ASSETS"

# ══════════════════════════════════════════════════════════
# 二、抽 SC face（绕开 window.cc 不设 FontNo 的坑）
# ══════════════════════════════════════════════════════════
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FONT_SRC="$WORK/cjk.ttf"
echo
echo "抽取 $LANG_PREF face..."
if ! "$PY" "$HERE/extract_sc_font.py" "$CJK_FONT" "$FONT_SRC" "$LANG_PREF" --allow-copy; then
  echo "⚠️  抽 face 失败，退回直接使用原字体（会有字形变体问题）" >&2
  FONT_SRC="$CJK_FONT"
fi

# ══════════════════════════════════════════════════════════
# 三、备份 —— ⚠️ 备份前必须校验「要备份的确实是原版」
#     否则会把已经换上去的 CJK 字体当成「原版」存下来，
#     之后「还原」等于什么都没做。
# ══════════════════════════════════════════════════════════
mkdir -p "$BACKUP"
echo
echo "备份原版字体 → $BACKUP"

is_original() {
  local f="$1"
  [[ -f "$f" ]] || return 1
  local sz; sz=$(stat -c%s "$f")
  (( sz < ORIGINAL_MAX_BYTES ))
}

for f in "AtkinsonHyperlegibleNext[wght].ttf" "AtkinsonHyperlegibleMono-Regular.ttf"; do
  [[ -f "$ASSETS/$f" ]] || { echo "  · 未找到 $f，跳过"; continue; }

  if [[ -f "$BACKUP/$f" ]]; then
    echo "  · $f 已有备份（不覆盖）"
  elif is_original "$ASSETS/$f"; then
    cp "$ASSETS/$f" "$BACKUP/$f"
    echo "  ✅ 已备份 $f ($(stat -c%s "$BACKUP/$f" | awk '{printf "%.0f KB", $1/1024}'))"
  else
    echo "  ⚠️  $f 看起来已经是 CJK 字体（$(stat -c%s "$ASSETS/$f" | awk '{printf "%.1f MB", $1/1048576}')），"
    echo "      不拿它当「原版」备份 —— 本地已无干净副本。"
    echo "      还原请重装：$PY -m pip install --force-reinstall mujoco"
  fi
done

# ══════════════════════════════════════════════════════════
# 四、替换
#     ⚠️ 必须用 --remove-destination：
#     venv 里的字体常常与 pip/uv 缓存是**硬链接**，普通 cp 会原地写穿，
#     把缓存里那份「原版」也一起改掉（本项目实测踩过，导致无副本可还原）。
# ══════════════════════════════════════════════════════════
echo
echo "替换字体"
for f in "AtkinsonHyperlegibleNext[wght].ttf" "AtkinsonHyperlegibleMono-Regular.ttf"; do
  [[ -f "$ASSETS/$f" ]] || { echo "  · 跳过 $f"; continue; }
  cp --remove-destination "$FONT_SRC" "$ASSETS/$f"
  echo "  ✅ 已替换 $f ($(stat -c%s "$ASSETS/$f" | awk '{printf "%.1f MB", $1/1048576}'))"
  # 校验确实换了 inode（断开了硬链接）
  local_nlink=$(stat -c%h "$ASSETS/$f")
  (( local_nlink == 1 )) || echo "     ⚠️ 该文件仍有 $local_nlink 个硬链接，缓存副本可能已被改动"
done

echo
echo "完成。"
echo "  验证：PYTHONPATH=src $PY -m mujoco_zh.panel_zh <model.xml>"
echo "  还原：$HERE/restore_font.sh"
echo
echo "⚠️ pip install --upgrade mujoco 会覆盖字体，升级后需重新跑本脚本。"
