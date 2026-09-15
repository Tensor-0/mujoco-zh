#!/usr/bin/env bash
# compare_ux.sh —— 原版 vs 汉化版 Studio **并排对比**
#
# ══════════════════════════════════════════════════════════════════
# 为什么要两个进程
# ══════════════════════════════════════════════════════════════════
# C++ 侧那 201 条汉化是靠**替换 ux.so 这一个文件**实现的，
# 而 venv 里同一时刻只能装一个版本 ⇒ 要「同时看」就得让两个 Studio
# 进程各自加载不同的 ux.so。
#
# 做法 = **影子包**（默认 /tmp/mujoco-orig，仅 ~1.7MB）：
#
#   mujoco/                      ← 除 experimental 外全部 symlink
#   mujoco/experimental/         ← 除 studio 外全部 symlink
#   mujoco/experimental/studio/  ← 除 ux.so 外全部 symlink
#                                  ux.so = **实体**（官方备份的拷贝）
#
# 靠 PYTHONPATH 排在 site-packages 前面命中它。
#
# ⚠️ **assets 保持 symlink 到 venv 是【有意设计】，不是疏漏。**
#    两边共用替换过的 CJK 字体，原版实例才能正常显示界面里的中文
#    （否则原版中文全是豆腐块，反而看不出差异）。**别把它「修」掉。**
#
# ⚠️⚠️ **原版实例必须设 MUJOCO_ZH_NO_TRANSLATE=1**（见 panel_zh.py:62）。
#    否则 Python 侧那 214 条仍会汉化 —— 看到的是「半汉化 vs 全汉化」，
#    不是「原版 vs 汉化版」，对比就失真了。
#
# ══════════════════════════════════════════════════════════════════
# 实测事实（省得重踩）
# ══════════════════════════════════════════════════════════════════
#   · 本机 GUI 是 **DISPLAY=:1**（不是 :0）
#   · Studio 出图要 **30~60s**；头 50s 窗口列表里看不到它是正常的，
#     不是启动失败 —— 瓶颈是 Filament 初始化，**换小模型不能加速**
#   · 窗口**位置不可控**（Studio 硬编码 SDL_WINDOWPOS_UNDEFINED，
#     SDL2 已移除 SDL_VIDEO_WINDOW_POS，本机无 wmctrl/xdotool）⇒ 手动摆
#   · **不需要隔离 HOME** —— `window.cc:55` 是 `io.IniFilename = nullptr`，
#     Studio 根本不读也不写 imgui.ini，两个实例天然同布局
#   · 退出**必崩**（`Engine::shutdown() called from the wrong thread!`）
#     —— 官方 .so 也一样，是 Studio 已知问题，不是汉化引入的
#
# 用法:  ./scripts/compare_ux.sh <model.xml>
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"

usage() {
  cat <<'EOF'
compare_ux.sh —— 原版 vs 汉化版 Studio 并排对比

用法:
  ./scripts/compare_ux.sh <model.xml>

例:
  ./scripts/compare_ux.sh ~/UniLab/src/unilab/assets/robots/dm10/scene_flat.xml
  ./scripts/compare_ux.sh ~/UniLab/src/unilab/assets/robots/stewart/scene.xml

⚠️ **挑模型只影响观感，不影响对比结果** —— 但别用「纯机器人」文件：
   `robots/dm10/dm10.xml` 里**没有地面**（地面在 `scene_flat.xml`），
   自由基座的机器人会**无限下坠**（实测 5 秒掉到 z=−121），看着像 bug。
   挑 `<robot>/scene*.xml`，或干脆用不会动的 `stewart/scene.xml`。

⚠️ 人形模型没有控制器时**会自己倒**（dm10 从 qpos0 起能站住，
   但按 State→Home 切到屈膝关键帧后 5 秒内必倒）—— 这是物理，不是汉化的锅。

环境变量:
  PYTHON              装了 mujoco 的解释器（默认自动探测）
  MUJOCO_ORIG_DIR     影子包位置（默认 /tmp/mujoco-orig）
  MUJOCO_ZH_CMP_W/H   窗口尺寸，单位=逻辑点（默认 1100x1200，见下）
  MUJOCO_ZH_CMP_GAP   两实例启动间隔秒数（默认 8）

⚠️ 窗口尺寸传的是**逻辑点**，实际像素要乘 DPI 缩放（本机 1.13x）——
   设 1100x1200 得到的窗口约 1243x1356 px，正好配合 2560x1440 屏并排。

原理（影子包 / 为什么要跳过翻译层）见脚本头部注释。
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then usage; exit 0; fi
MODEL="${1:-}"
if [[ -z "$MODEL" ]]; then usage >&2; exit 2; fi
[[ -f "$MODEL" ]] || { echo "❌ 找不到模型: $MODEL" >&2; exit 1; }
MODEL="$(readlink -f "$MODEL")"

# ── 定位解释器（与 build_ux_zh.sh 同一套逻辑）───────────────
find_python() {
  local cands=() c seen=""
  [[ -n "${PYTHON:-}" ]] && cands+=("$PYTHON")
  cands+=("python3")
  local g
  for g in "$HOME"/*/.venv/bin/python3 "$HOME"/.venv/bin/python3; do
    [[ -x "$g" ]] && cands+=("$g")
  done
  for c in "${cands[@]}"; do
    [[ "$seen" == *"|$c|"* ]] && continue; seen+="|$c|"
    command -v "$c" >/dev/null 2>&1 || [[ -x "$c" ]] || continue
    if "$c" -c "import mujoco.experimental.studio" >/dev/null 2>&1; then
      printf '%s\n' "$c"; return 0
    fi
  done
  return 1
}
PY="$(find_python)" || { echo "❌ 找不到装了 mujoco 的解释器（用 PYTHON=... 指定）" >&2; exit 1; }

STUDIO="$("$PY" -c 'from mujoco.experimental import studio as s;print(list(s.__path__)[0])')"
SP="$(dirname "$(dirname "$STUDIO")")"                       # site-packages/mujoco
SO_NAME="ux.cpython-$( "$PY" -c 'import sys;print(f"{sys.version_info.major}{sys.version_info.minor}")' )-x86_64-linux-gnu.so"
TARGET="$STUDIO/$SO_NAME"
BACKUP="$REPO/.ux_official_backup/ux.official.so"
ORIG="${MUJOCO_ORIG_DIR:-/tmp/mujoco-orig}"

# ── 前置检查 ────────────────────────────────────────────────
[[ -f "$BACKUP" ]] || {
  echo "❌ 没有官方 ux.so 备份：$BACKUP" >&2
  echo "   先跑一次 ./scripts/build_ux_zh.sh（首次运行会自动备份官方 .so）" >&2
  exit 1; }

if cmp -s "$TARGET" "$BACKUP"; then
  echo "❌ venv 里装的就是**官方版** ux.so —— 两个窗口会一模一样，没有对比意义。" >&2
  echo "   装汉化版：./scripts/build_ux_zh.sh" >&2
  exit 1
fi

# ── 建/更新影子包 ───────────────────────────────────────────
# 保险：绝不 rm -rf 可疑目录（ORIG 可被环境变量覆盖）
case "$ORIG" in
  ""|/|"$HOME"|"$HOME"/) echo "❌ MUJOCO_ORIG_DIR 指向可疑路径，拒绝操作: $ORIG" >&2; exit 1;;
esac

link_except() { # <源目录> <目标目录> <要跳过的条目名...>
  local src="$1" dst="$2"; shift 2
  local e b s skip
  for e in "$src"/* "$src"/.[!.]*; do
    [[ -e "$e" || -L "$e" ]] || continue          # -L 兜住悬空符号链接
    b="$(basename "$e")"; skip=0
    for s in "$@"; do [[ "$b" == "$s" ]] && { skip=1; break; }; done
    (( skip )) && continue
    ln -sfn "$(readlink -f "$e")" "$dst/$b"       # 必须绝对路径，否则跨目录断链
  done
}

# 复用条件：影子包里的 ux.so 与官方备份**逐字节相同**，且结构完整
if cmp -s "$ORIG/mujoco/experimental/studio/$SO_NAME" "$BACKUP" \
   && [[ -e "$ORIG/mujoco/experimental/studio/assets" ]]; then
  echo "影子包已就绪: $ORIG"
else
  echo "建影子包 $ORIG ..."
  rm -rf "$ORIG"
  mkdir -p "$ORIG/mujoco/experimental/studio"
  link_except "$SP"              "$ORIG/mujoco"                     experimental
  link_except "$SP/experimental" "$ORIG/mujoco/experimental"        studio
  link_except "$STUDIO"          "$ORIG/mujoco/experimental/studio" "$SO_NAME"
  cp --remove-destination "$BACKUP" "$ORIG/mujoco/experimental/studio/$SO_NAME"
  echo "  ✅ $(du -sh "$ORIG" | cut -f1)（真包 95MB，其余全是 symlink）"
fi

# ⚠️ 本机真实图形界面是 :1（不是 :0）—— 设错会静默失败
export DISPLAY="${DISPLAY:-:1}"
W="${MUJOCO_ZH_CMP_W:-1100}"
H="${MUJOCO_ZH_CMP_H:-1200}"

echo
echo "═══ 两个实例将加载的 ux.so ═══"
echo "  ① 原版    $(md5sum "$ORIG/mujoco/experimental/studio/$SO_NAME" | cut -c1-16)  官方备份"
echo "  ② 汉化版  $(md5sum "$TARGET" | cut -c1-16)  $TARGET"

# ── 启动 ────────────────────────────────────────────────────
PIDS=()
cleanup() {
  trap - EXIT INT TERM
  if (( ${#PIDS[@]} )); then
    # ⚠️ -9：正常退出路径必崩（wrong thread），SIGTERM 会被卡住的析构吃掉
    for p in "${PIDS[@]}"; do kill -9 "$p" 2>/dev/null || true; done
  fi
  wait 2>/dev/null || true
  echo
  echo "两个实例已退出。"
}
trap cleanup EXIT INT TERM

echo
echo "启动 ① 原版（全英文）..."
PYTHONPATH="$ORIG:$REPO/src" \
MUJOCO_ZH_NO_TRANSLATE=1 \
MUJOCO_ZH_TITLE="① 原版 MuJoCo Studio" \
MUJOCO_ZH_WIDTH="$W" MUJOCO_ZH_HEIGHT="$H" \
  "$PY" -m mujoco_zh.panel_zh "$MODEL" &
PIDS+=($!)

sleep "${MUJOCO_ZH_CMP_GAP:-8}"     # 错开，免得两个 Filament 同时抢 GPU

echo "启动 ② 汉化版（中文）..."
PYTHONPATH="$REPO/src" \
MUJOCO_ZH_TITLE="② 汉化版 MuJoCo Studio" \
MUJOCO_ZH_WIDTH="$W" MUJOCO_ZH_HEIGHT="$H" \
  "$PY" -m mujoco_zh.panel_zh "$MODEL" &
PIDS+=($!)

cat <<'EOF'

══════════════════════════════════════════════════════════════════
 两个窗口正在启动 —— 出图要 30~60 秒，请耐心等
══════════════════════════════════════════════════════════════════

 ⚠️ 头 50 秒在窗口列表里看不到窗口是**正常的**，不是启动失败 ——
    还在初始化 Filament 渲染器。

 ⚠️ 窗口**位置不可控**，需要手动摆（分辨用窗口标题 ① / ②）：
        Super + ←   左半屏          Super + →   右半屏

 截图（⚠️ 尺寸必须 2560x1440，写反了 ffmpeg 会报错但仍写出坏图）：
   DISPLAY=:1 /usr/bin/ffmpeg -loglevel error -f x11grab \
     -video_size 2560x1440 -i :1 -frames:v 1 -y /tmp/cmp.png

 退出：在本终端按 Ctrl+C
   ⚠️ 两个进程都会 core dump（wrong thread），是 Studio 已知问题

══════════════════════════════════════════════════════════════════
EOF

wait
