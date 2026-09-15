#!/usr/bin/env bash
# install_font.sh —— 把 Studio 主字体换成 CJK 字体（中文支持的前提）
#
# 为什么需要：
#   Studio 主字体 Atkinson Hyperlegible **不含任何中文字形**。
#   而 ImGui 1.92 起引入动态字形光栅化（"不需要再指定 glyph ranges"），
#   所以「换个字体文件」就能让中文显示出来 —— 无需改任何代码。
#
# 用法：
#   ./scripts/install_font.sh [CJK字体路径]
#
# 还原：
#   cp <studio>/assets_backup/* <studio>/assets/
set -euo pipefail

CJK_FONT="${1:-/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc}"
[[ -f "$CJK_FONT" ]] || { echo "❌ 找不到 CJK 字体: $CJK_FONT"; exit 1; }
echo "CJK 字体: $CJK_FONT ($(stat -c%s "$CJK_FONT" | awk '{printf "%.1f MB", $1/1048576}'))"

STUDIO=$(python3 -c "
from mujoco.experimental import studio as s
print(list(s.__path__)[0])" 2>/dev/null) || {
  echo "❌ 无法定位 mujoco.experimental.studio"
  echo "   试试用装 mujoco 的那个解释器，例如："
  echo "   /home/zhan/UniLab/.venv/bin/python3 $0"
  exit 1; }

ASSETS="$STUDIO/assets"
BACKUP="$STUDIO/assets_backup"
echo "目标目录: $ASSETS"
mkdir -p "$BACKUP"

for f in "AtkinsonHyperlegibleNext[wght].ttf" "AtkinsonHyperlegibleMono-Regular.ttf"; do
  [[ -f "$ASSETS/$f" ]] || { echo "  · 未找到 $f，跳过"; continue; }
  if [[ ! -f "$BACKUP/$f" ]]; then
    cp "$ASSETS/$f" "$BACKUP/$f"
    echo "  ✅ 已备份 $f"
  else
    echo "  · $f 已有备份（未覆盖）"
  fi
  cp "$CJK_FONT" "$ASSETS/$f"
  echo "  ✅ 已替换 $f"
done

echo
echo "完成。"
echo "  验证：python3 -m mujoco_zh.panel_zh <model.xml>"
echo "  还原：cp '$BACKUP'/* '$ASSETS'/"
echo
echo "⚠️ pip install --upgrade mujoco 会覆盖字体，升级后需重新跑本脚本。"
