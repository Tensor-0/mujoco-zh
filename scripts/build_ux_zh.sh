#!/usr/bin/env bash
# build_ux_zh.sh —— 重编译 MuJoCo Studio 的 ux.so 使其支持中文
#
# ══════════════════════════════════════════════════════════════════
# 为什么要重编译
# ══════════════════════════════════════════════════════════════════
# ux.so 静态链接了自己的一份 C++ ImGui，界面文字是 C++ 字面量，
# Python 层的 monkeypatch（src/mujoco_zh/translate.py）永远够不着。
# 四条独立证据（GIL 释放 / lea→RDI 反汇编 / imgui.cpp 内部串 / 无模块名）
# 见 README「覆盖边界」一节。
#
# ══════════════════════════════════════════════════════════════════
# ⚠️ 两个必须遵守的硬约束（踩过坑，改错就白编）
# ══════════════════════════════════════════════════════════════════
#
# 1. **必须用 clang + libc++，不能用 gcc + libstdc++**
#
#    pybind11 的类型注册表按编译器 ABI 隔离，key 是
#    __pybind11_internals_v11_<compiler>_<stdlib>_<abi>__
#
#      官方 ux.so : __pybind11_internals_v11_system_libcpp_abi1__
#      gcc 编出来 : __pybind11_internals_v11_system_libstdcpp_gxx_abi_1xxx_use_cxx11_abi_1__
#
#    key 不同 ⇒ 两套注册表 ⇒ sim.so 注册的 StepControl 认不出来，报：
#      TypeError: step_control_gui(): incompatible function arguments
#
#    （官方 .so 的 NEEDED 里没有 libstdc++，那就是用 libc++ 编的指纹。）
#
# 2. **必须排除 imgui_bridge.cc 和 picture_gui.cc**
#
#    前者直接 include <mjrfilament.h>，后者经 hal/renderer.h 间接拖进
#    filament（GB 级的 FetchContent 依赖）。排除后 ux.so 里 filament 命中为 0。
#
# ══════════════════════════════════════════════════════════════════
# 用法
# ══════════════════════════════════════════════════════════════════
#   ./scripts/build_ux_zh.sh              # 编译并安装
#   ./scripts/build_ux_zh.sh --build-only # 只编译不安装
#   ./scripts/build_ux_zh.sh --restore    # 仅还原官方 .so
#
#   环境变量：
#     MUJOCO_VERSION  目标版本（默认从已安装的 mujoco 读）
#     WORKDIR         工作目录（默认 /tmp/ux-zh-build）
#     PYTHON          装了 mujoco 的解释器
#     PATCH_DIR       译文 patch 目录（默认 <repo>/patches）
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
MODE="${1:-install}"

# ── 定位解释器 ──────────────────────────────────────────────
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

MUJOCO_VER="${MUJOCO_VERSION:-$("$PY" -c 'import mujoco;print(mujoco.__version__)')}"
STUDIO="$("$PY" -c 'from mujoco.experimental import studio as s;print(list(s.__path__)[0])')"
SP="$(dirname "$(dirname "$STUDIO")")"                       # site-packages/mujoco
SO_NAME="ux.cpython-$( "$PY" -c 'import sys;print(f"{sys.version_info.major}{sys.version_info.minor}")' )-x86_64-linux-gnu.so"
TARGET="$STUDIO/$SO_NAME"
BACKUP="$REPO/.ux_official_backup/ux.official.so"

# ── 还原模式 ────────────────────────────────────────────────
if [[ "$MODE" == "--restore" ]]; then
  [[ -f "$BACKUP" ]] || { echo "❌ 没有备份：$BACKUP" >&2; exit 1; }
  cp --remove-destination "$BACKUP" "$TARGET"
  echo "✅ 已还原官方 ux.so"
  md5sum "$BACKUP" "$TARGET" | awk '{print "   "$1, $NF}'
  exit 0
fi

# ── 首次运行：备份官方 .so ──────────────────────────────────
mkdir -p "$(dirname "$BACKUP")"
if [[ ! -f "$BACKUP" ]]; then
  cp --remove-destination "$TARGET" "$BACKUP"
  echo "✅ 已备份官方 ux.so → $BACKUP"
fi

echo "解释器:   $PY"
echo "mujoco:   $MUJOCO_VER"
echo "目标 .so: $TARGET"

# ── 工具链检查 ──────────────────────────────────────────────
CLANG=""
for c in clang++-15 clang++-14 clang++-16 clang++-17 clang++; do
  command -v "$c" >/dev/null 2>&1 && { CLANG="$c"; break; }
done
[[ -n "$CLANG" ]] || {
  echo "❌ 找不到 clang++。必须用 clang + libc++（见脚本头部说明）。" >&2
  echo "   sudo apt-get install -y clang-15 libc++-15-dev libc++abi-15-dev" >&2; exit 1; }
[[ -f /usr/include/c++/v1/vector || -f /usr/lib/llvm-15/include/c++/v1/vector ]] || {
  echo "❌ 找不到 libc++ 头文件（-stdlib=libc++ 需要）" >&2
  echo "   sudo apt-get install -y libc++-15-dev libc++abi-15-dev" >&2; exit 1; }
echo "编译器:   $CLANG"

WORK="${WORKDIR:-/tmp/ux-zh-build}"
# tarball 顶层是 mujoco-<ver>/，`--strip-components=1` 后
# **<mujoco-src> 本身就是仓库根**（含 CMakeLists.txt / src/ / include/ / model/）
#   C++ 源码根 = <mujoco-src>/src   （gui.cc 在 src/experimental/platform/ux/）
#   Python 侧   = <mujoco-src>/python/mujoco/
# ⚠️ 踩过：曾被一个自己污染的目录（里面多套了一层 src/）误导，
#    把这里写成 $WORK/mujoco-src/src 并让编译命令再拼 /src/experimental，
#    结果对干净环境反而路径错误。**路径以干净 tarball 的布局为准。**
REPO_SRC="$WORK/mujoco-src"
SRC="$REPO_SRC/src"
mkdir -p "$WORK"

# ── 拉依赖（幂等，但**目录存在 ≠ 版本正确**）──────────────────
# ⚠️ 2026-09-20 修：旧版只判断 `-d "$dir/.git"` 就 return，导致
#    「目录在、但 commit 不是 pin 的那个」这种状态被静默当成合格。
#    实测代价：手动 clone 拿到默认分支最新版（imgui 420f179 而非
#    913a3c605）⇒ 编译时报 `ImGuiCol_DockingPreview` / `GetWindowDpiScale`
#    不存在，而脚本直到编译阶段才发现，报错指向源码而不是依赖版本。
fetch() { # url sha dir [tag]
  local url="$1" sha="$2" dir="$3" tag="${4:-}"
  local want="${sha:-$tag}"

  if [[ -d "$dir/.git" ]]; then
    local have; have="$(git -C "$dir" rev-parse HEAD 2>/dev/null || echo '')"
    if [[ -z "$want" ]]; then
      echo "  · $dir 已存在（未 pin 版本，按原样使用）"; return 0
    fi
    # tag 可能是轻量 tag，比 sha 前缀；两边都试
    if [[ "$have" == "$want"* ]] || git -C "$dir" describe --tags --exact-match 2>/dev/null | grep -qx "$tag"; then
      echo "  · $dir 已存在且版本正确（${have:0:9}）"; return 0
    fi
    echo "  ⚠️ $dir 版本不对：现在 ${have:0:9}，需要 ${want:0:9} —— 重新拉取"
    rm -rf "$dir"
  fi

  echo "  ↓ $dir"
  if [[ -n "$tag" ]]; then
    git clone -q --depth 1 --branch "$tag" "$url" "$dir" 2>/dev/null || git clone -q "$url" "$dir"
  else
    git clone -q "$url" "$dir" 2>/dev/null || { echo "  ❌ clone 失败: $url" >&2; return 1; }
  fi
  if [[ -n "$sha" ]]; then
    git -C "$dir" fetch -q --depth 1 origin "$sha" 2>/dev/null \
      && git -C "$dir" checkout -q "$sha" 2>/dev/null \
      || { echo "  ❌ 无法切到 $sha" >&2; return 1; }
    local now; now="$(git -C "$dir" rev-parse HEAD)"
    [[ "${now:0:9}" == "${sha:0:9}" ]] || { echo "  ❌ checkout 后 HEAD=${now:0:9}，期望 ${sha:0:9}" >&2; return 1; }
  fi
  return 0
}

if [[ ! -d "$SRC" ]]; then
  echo
  echo "拉取 mujoco $MUJOCO_VER 源码..."
  mkdir -p "$WORK/mujoco-src"
  tar="$WORK/mujoco-$MUJOCO_VER.tar.gz"

  # ⚠️ 2026-09-20 修：改用 codeload 直链 + 校验完整性。
  #
  # 旧写法 `github.com/<org>/<repo>/archive/refs/tags/<tag>.tar.gz` 会 302 到
  # codeload，经代理时这条重定向容易断流。实测同一台机器：
  #     github.com/...  → 卡在 8.8 MB / 10.3 MB（三次都断）
  #     codeload 直链   → 10 秒下完 71 MB
  # ⚠️ 而且断掉的包【开头仍是合法 gzip 头 0x1f8b】，`file` 看不出来。
  # 旧代码只判断 `[[ -f "$tar" ]]` ⇒ 会拿坏包继续跑，报错是
  # `tar: 归档文件中异常的 EOF`，指向 tar 而不是下载，极难诊断。
  # ⇒ 判据必须是「文件存在 **且** tar tzf 能读通」。
  ok_tar() { [[ -f "$tar" ]] && tar tzf "$tar" >/dev/null 2>&1; }

  dl() { # url
    echo "  ↓ $1"
    curl -sL --max-time 600 --retry 2 -o "$tar" "$1" || true
    ok_tar || { echo "  ⚠️ 下载不完整（$(stat -c%s "$tar" 2>/dev/null || echo 0) 字节），重试" >&2; return 1; }
  }

  if ! ok_tar; then
    [[ -f "$tar" ]] && { echo "  ⚠️ 已有包损坏，删除重下"; rm -f "$tar"; }
    # tag 名可能是 3.11.0 或 v3.11.0，两个都试
    for base in \
      "https://codeload.github.com/google-deepmind/mujoco/tar.gz/refs/tags" ; do
      dl "$base/$MUJOCO_VER" || dl "$base/v$MUJOCO_VER" || {
        echo "❌ 源码下载失败：$base/$MUJOCO_VER（已试 v 前缀）" >&2; exit 1; }
    done
  fi
  echo "  ✅ 源码包完整（$(stat -c%s "$tar" | awk '{printf "%.1f MB", $1/1e6}')）"
  tar xzf "$tar" -C "$WORK/mujoco-src" --strip-components=1 || {
    echo "❌ 解压失败" >&2; exit 1; }
fi

# ⚠️ imgui 必须是 pin 的 docking 分支 commit，不能用 master
#    （master 上 ImGuiCol_DockingEmptyBg 等枚举会被删，编译不过）
echo "拉取第三方依赖..."
fetch https://github.com/ocornut/imgui.git \
      913a3c60561bb07e8fd410ec7d4a8f6f485defd6 "$WORK/imgui"
fetch https://github.com/epezent/implot.git \
      ec7306ceb99d19ff193eb30dc74fa3598f5e7dc6 "$WORK/implot"
fetch https://github.com/abseil/abseil-cpp.git "" "$WORK/abseil" "20250127.0"

# ── 打译文 patch ────────────────────────────────────────────
# ⚠️ 用 `patch` 而不是 `git apply` ——
#    源码是 tarball 解压的，**不是 git 仓库**，`git -C ... apply` 会直接
#    报 "不是 git 仓库" 而失败。踩过：这个错误被 2>/dev/null 吞掉，脚本
#    一路"成功"编出个**原文版**的 .so，看不出任何异常。
PATCH_DIR="${PATCH_DIR:-$REPO/patches}"
if [[ -d "$PATCH_DIR" ]] && compgen -G "$PATCH_DIR/*.patch" >/dev/null; then
  echo "应用译文 patch..."
  n_ok=0
  # ⚠️ 2026-09-20 修：幂等判断必须【自己判、自己跳】，不能靠 patch 的 --batch。
  #
  # 两个错误示范（都踩过）：
  #   ① 不加 --batch：patch 检测到已应用会【交互提问】
  #      「Reversed (or previously applied) patch detected! Assume -R? [n]」。
  #      `--silent` 管不住它，`2>/dev/null` 只吞显示、吞不掉它在读 stdin；
  #      而 stdin 被 `< "$p"` 重定向成 patch 文件 ⇒ 它拿 patch 文本当答案，
  #      行为不确定 + 往终端喷乱码。
  #   ② 加 `--batch`：它的语义是「**假设 -R 并执行**」—— 真的把 patch
  #      **反向撤销**掉！而脚本把退出码 0 当成"成功应用"，静默产出一个
  #      【不含中文】的 .so。实测：双语句从 459 条变 0 条，源码中文行 469→0。
  #
  # ⇒ 正确做法：用 `-R --dry-run` 自己判断"是否已应用"，成立就【跳过】，
  #    不执行任何 patch 命令。`--dry-run` 不改文件，安全。
  for p in "$PATCH_DIR"/*.patch; do
    if patch -d "$REPO_SRC" -p1 -R --dry-run --silent < "$p" >/dev/null 2>&1; then
      echo "  · $(basename "$p") 已应用（跳过）"
      n_ok=$((n_ok + 1))
    elif patch -d "$REPO_SRC" -p1 --dry-run --silent < "$p" >/dev/null 2>&1; then
      patch -d "$REPO_SRC" -p1 --silent --batch < "$p" >/dev/null 2>&1
      echo "  ✅ $(basename "$p")"
      n_ok=$((n_ok + 1))
    else
      echo "❌ $(basename "$p") 打不上 —— 源码版本与 patch 不匹配" >&2
      echo "   重新生成：python3 scripts/gen_cpp_patch.py" >&2
      exit 1
    fi
  done
  echo "  （已应用 $n_ok 个 patch）"
else
  echo "⚠️  没找到 patch（$PATCH_DIR/*.patch）—— 将编译**原文版**"
  echo "    要出中文版请先跑：python3 scripts/gen_cpp_patch.py"
fi

# ⚠️ 从干净源码重打之前，先确认没有残留的上次 patch
#    （patch 是可重入的：已应用的会被识别并跳过，但仍要防手工改脏）

# ── 编译 ────────────────────────────────────────────────────
BUILD="$WORK/build"
mkdir -p "$BUILD"; cd "$BUILD"
PYINC="$("$PY" -c 'import sysconfig;print(sysconfig.get_paths()["include"])')"
PBINC="$("$PY" -c 'import pybind11;print(pybind11.get_include())')"

# ⚠️ 2026-09-20 加：pybind11 版本必须预先校验，不能等编译完看 ABI key 才发现。
#
# 官方 ux.so 的 key 是 __pybind11_internals_v11_system_libcpp_abi1__，
# 而 PYBIND11_INTERNALS_VERSION 随 pybind11 版本跳变（实测三个版本全不同）：
#     pybind11 3.1.0  → 12   ❌ key 变 v12，装上报 incompatible function arguments
#     pybind11 2.13.6 →  5   ❌ key 变 v5_clang_libcpp_cxxabi1002（连编译器段都变了）
#     pybind11 3.0.x  → 11   ✅ 与官方一致
# 整个编译要几分钟，编完才发现版本不对纯属浪费；这里提前拦。
PB_INTERNALS_H="$PBINC/pybind11/detail/internals.h"
[[ -f "$PB_INTERNALS_H" ]] || { echo "❌ 找不到 $PB_INTERNALS_H（pybind11 安装不完整）" >&2; exit 1; }
PB_VER="$(grep -oE '# *define PYBIND11_INTERNALS_VERSION +[0-9]+' "$PB_INTERNALS_H" \
          | grep -oE '[0-9]+$' | head -1)"
PB_PKG="$("$PY" -c 'import pybind11;print(pybind11.__version__)')"
# 官方 key 的版本段（从备份里读；没备份就跳过检查）
PB_EXPECT=""
if [[ -f "$BACKUP" ]]; then
  PB_EXPECT="$(strings -a "$BACKUP" | grep -oE '__pybind11_internals_v[0-9]+_' | grep -oE '[0-9]+' | head -1 || true)"
fi
echo "pybind11: $PB_PKG (INTERNALS_VERSION=v${PB_VER:-?}${PB_EXPECT:+, 官方要求 v$PB_EXPECT})"
if [[ -n "$PB_EXPECT" && -n "$PB_VER" && "$PB_VER" != "$PB_EXPECT" ]]; then
  echo "❌ pybind11 ABI 版本不匹配：当前 v$PB_VER，需要 v$PB_EXPECT" >&2
  echo "   ⇒ 装对应的 pybind11，例如（官方 ux.so 是 v11）：" >&2
  echo "        uv pip install 'pybind11==3.0.2'" >&2
  echo "   （v11 = pybind11 3.0.x ；v12 = 3.1.0 ；v5 = 2.13.6）" >&2
  exit 1
fi
WEBP_INC=""
[[ -f /home/zhan/anaconda3/include/webp/encode.h ]] && WEBP_INC="-I/home/zhan/anaconda3/include"

INC="-I$SP/include -I$REPO_SRC -I$REPO_SRC/include -I$SRC -I$REPO_SRC/python/mujoco \
     -I$WORK/imgui -I$WORK/imgui/backends -I$WORK/imgui/misc/cpp -I$WORK/implot \
     -I$WORK/abseil $WEBP_INC -I$PYINC -I$PBINC"
FLAGS="-std=c++20 -stdlib=libc++ -fPIC -O1 -w -fvisibility=hidden"

echo
echo "编译（$(nproc) 核并行）..."
# ⚠️ 对象文件名必须加前缀：MuJoCo 的 imgui_widgets.cc 与 imgui 库的
#    imgui_widgets.cpp 会同名，后者覆盖前者 → 链接缺符号 ImGui_DataPtrTable::DataPtr
FAILED=0
compile() { # ⚠️ 失败必须让父脚本知道 —— 子 shell 里的 exit 只退出子 shell
  local src="$1" out="$2"
  if ! $CLANG $FLAGS -c "$src" -o "$out" $INC 2>"$out.err"; then
    echo "  ❌ 编译失败: $src" >&2
    head -5 "$out.err" >&2
    return 1
  fi
}

for f in gui gui_spec imgui_widgets interaction spec_editor plugin; do
  ( compile "$SRC/experimental/platform/ux/$f.cc" "mj_$f.o" && echo "  ✅ ux/$f" ) &
done
for f in step_control sim_profiler model_holder sim_history; do
  ( compile "$SRC/experimental/platform/sim/$f.cc" "mj_sim_$f.o" && echo "  ✅ sim/$f" ) &
done
( compile "$SRC/experimental/platform/sys_utils.cc" mj_sys_utils.o && echo "  ✅ sys_utils" ) &
( compile "$SRC/experimental/platform/helpers.cc"  mj_helpers.o   && echo "  ✅ helpers" ) &
( compile "$REPO_SRC/python/mujoco/experimental/studio/ux.cc" mj_ux_pybind.o && echo "  ✅ ux.cc" ) &
# ⚠️⚠️ `FAILED=1` **不能写在子 shell 里** —— 那只是子 shell 的变量，
#    父脚本的 FAILED 永远是 0，于是「编译失败」会被当成成功一路装下去。
#    （这个错误本文件 212 行的注释就警告过，但代码一直没落实；
#      实测代价：gui.cc 编译失败，装了个缺一整个源文件的 .so，脚本还报 ✅）
#    ⇒ 正确做法是用 **`wait` 的退出码**汇总。
rc=0
for pid in $(jobs -p); do wait "$pid" || rc=1; done
FAILED=$rc
# ⚠️ 必须在这里就检查 —— 否则编译失败会拿旧的 .o 凑出一个 .so，
#    装上去看着「成功」实际是残缺的（实测：gui.cc 报错但脚本报 ✅）
(( FAILED == 0 )) || { echo "❌ MuJoCo 源文件编译失败，中止" >&2; exit 1; }

for f in imgui imgui_draw imgui_tables imgui_widgets; do
  ( compile "$WORK/imgui/$f.cpp" "imgui_$f.o" ) &
done
( compile "$WORK/imgui/misc/cpp/imgui_stdlib.cpp" imgui_misc_imgui_stdlib.o ) &
( compile "$WORK/implot/implot.cpp" implot_implot.o ) &
( compile "$WORK/implot/implot_items.cpp" implot_implot_items.o ) &
wait
(( FAILED == 0 )) || { echo "❌ 第三方库编译失败，中止" >&2; exit 1; }
echo "  ✅ imgui / implot"

# ⚠️ 链接前清掉上次的产物，避免旧 .o 混进来
rm -f ux_zh.so

# ── 链接 ────────────────────────────────────────────────────
echo
echo "链接..."
WEBP_LIB=""
for w in /usr/lib/x86_64-linux-gnu/libwebp.so.7 /usr/lib/x86_64-linux-gnu/libwebp.so; do
  [[ -f "$w" ]] && { WEBP_LIB="$w"; break; }
done
[[ -n "$WEBP_LIB" ]] || { echo "❌ 找不到 libwebp（helpers.cc 需要）" >&2
  echo "   sudo apt-get install -y libwebp-dev" >&2; exit 1; }

# ⚠️ libc++ 必须**静态**链接，与官方一致
#
#   官方 .so 的 NEEDED 里没有 libc++/libc++abi/libunwind，
#   而它内部有 libcxxabi 的源码路径串、且未定义 C++ 符号为 0
#   ⇒ libc++ 与 libc++abi 都被静态链进去了。
#
#   动态链的后果：编出来的 .so 依赖 libc++.so.1 / libc++abi.so.1 /
#   libunwind.so.1，**拷到没装 libc++ 的机器上直接跑不起来**。
#
#   直接加 `-Wl,-Bstatic -lc++` 是**无效的** —— clang 驱动会在命令末尾
#   把 -lc++ 重新以动态方式加回来。正确做法是 `-nodefaultlibs`
#   掐掉默认库列表，再显式把静态库和底层 libc 全部列上。
LIBCXX_DIR="/usr/lib/llvm-15/lib"
for d in /usr/lib/llvm-15/lib /usr/lib/llvm-14/lib /usr/lib/llvm-16/lib; do
  [[ -f "$d/libc++.a" ]] && { LIBCXX_DIR="$d"; break; }
done
[[ -f "$LIBCXX_DIR/libc++.a" ]] || {
  echo "❌ 找不到 libc++.a（静态链接需要）:$LIBCXX_DIR" >&2
  echo "   sudo apt-get install -y libc++-15-dev libc++abi-15-dev" >&2; exit 1; }

$CLANG -shared -stdlib=libc++ -nodefaultlibs -o ux_zh.so *.o \
  "$SP/libmujoco.so.$MUJOCO_VER" "$WEBP_LIB" \
  -Wl,--start-group \
    "$LIBCXX_DIR/libc++.a" "$LIBCXX_DIR/libc++abi.a" "$LIBCXX_DIR/libunwind.a" \
  -Wl,--end-group \
  -lgcc_s -lgcc -lc \
  -Wl,-rpath,'$ORIGIN' -Wl,-z,now -Wl,-z,relro 2>&1 | head -20
[[ -f ux_zh.so ]] || { echo "❌ 链接失败" >&2; exit 1; }
echo "  ✅ ux_zh.so ($(stat -c%s ux_zh.so | awk '{printf "%.1f MB", $1/1e6}'))"

# 确认 libc++ 确实静态链进去了（否则产物不可移植）
if readelf -d ux_zh.so | grep -qE "libc\+\+|libunwind"; then
  echo "❌ 仍动态依赖 libc++/libunwind —— 产物无法拷到其它机器" >&2
  readelf -d ux_zh.so | grep NEEDED | sed 's/^/     /' >&2
  exit 1
fi
echo "  ✅ libc++ 已静态链接（无 libc++/libunwind 运行时依赖）"

# ── 自检：ABI key 必须与官方一致 ────────────────────────────
echo
echo "自检..."
KEY_NEW="$(strings -a ux_zh.so | grep -oE '__pybind11_internals_v[0-9]+_[a-z]+_[a-z0-9_]+__' | sort -u | head -1)"
KEY_OFF="$(strings -a "$BACKUP" | grep -oE '__pybind11_internals_v[0-9]+_[a-z]+_[a-z0-9_]+__' | sort -u | head -1)"
echo "  pybind11 key  官方: $KEY_OFF"
echo "               新版: $KEY_NEW"
if [[ "$KEY_NEW" != "$KEY_OFF" ]]; then
  echo "  ❌ ABI key 不一致！装上去会报 incompatible function arguments" >&2
  echo "     检查是否真的用了 clang + libc++（-stdlib=libc++）" >&2
  exit 1
fi
echo "  ✅ ABI key 一致"
echo "  filament 残留: $(strings -a ux_zh.so | grep -cF filament)（应为 0）"

# ── 安装 ────────────────────────────────────────────────────
if [[ "$MODE" == "--build-only" ]]; then
  echo
  echo "产物：$BUILD/ux_zh.so（未安装）"
  exit 0
fi

echo
echo "安装..."
cp --remove-destination ux_zh.so "$TARGET"
echo "  ✅ 已安装到 $TARGET"
echo
echo "验证："
echo "  $PY -c 'import mujoco.experimental.studio.ux as m; print(len([n for n in dir(m) if n.endswith(\"_gui\")]))'"
echo "  （应输出 18）"
echo
echo "还原：$HERE/build_ux_zh.sh --restore"
