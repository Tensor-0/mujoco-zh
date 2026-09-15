#!/usr/bin/env bash
# compile_mo.sh —— 把 .po 编译成 .mo（gettext 标准流程）
#
# 什么时候需要跑：
#   * 改过 locales/zh_CN/LC_MESSAGES/mujoco_zh.po 之后
#   * 仓库里的 .mo 是过期的
#
# 注意：即使不跑也能用 —— translate.py 内置了 fallback 表。
#       但编译后翻译可以独立更新，不用改代码。
set -euo pipefail

PO="locales/zh_CN/LC_MESSAGES/mujoco_zh.po"
MO="locales/zh_CN/LC_MESSAGES/mujoco_zh.mo"

[[ -f "$PO" ]] || { echo "❌ 找不到 $PO（请在仓库根目录运行）"; exit 1; }

echo "编译: $PO → $MO"

if command -v msgfmt >/dev/null 2>&1; then
    msgfmt "$PO" -o "$MO"
    echo "  ✅ msgfmt 完成"
elif python3 -c "import msgfmt" 2>/dev/null; then
    python3 -c "import msgfmt; msgfmt.make('$PO', '$MO')"
    echo "  ✅ python msgfmt 完成"
else
    echo "  ❌ 找不到 msgfmt（GNU gettext 工具）"
    echo "     Ubuntu: sudo apt install gettext"
    echo "     或:     pip install msgfmt"
    exit 1
fi

n=$(python3 -c "
import gettext
t = gettext.translation('mujoco_zh', localedir='locales', languages=['zh_CN'])
msgids = list(t._catalog.keys())
print(len([m for m in msgids if m]))
")
echo "  📊 已编译 $n 条译文"
echo
echo "验证："
python3 -c "
import gettext
t = gettext.translation('mujoco_zh', localedir='locales', languages=['zh_CN'])
for s in ('File', 'Physics Settings', 'Joints'):
    print(f'  {s!r:22} → {t.gettext(s)!r}')
"
