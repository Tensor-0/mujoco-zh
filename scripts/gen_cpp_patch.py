#!/usr/bin/env python3
"""gen_cpp_patch.py —— 从 gettext .po 生成 C++ 源码的界面字符串 patch。

## 设计核心：一份译文，两条消费者

`locales/zh_CN/LC_MESSAGES/mujoco_zh.po` 同时喂给：

  ① **Python 侧** —— `src/mujoco_zh/translate.py` 用 gettext 读（已有）
  ② **C++ 侧**   —— 本脚本把它渲染成 `patches/ux-zh.patch`，重编译 `ux.so`

⇒ 改词表只需动 `.po` 一处。

## 用法

    python3 scripts/gen_cpp_patch.py                # 生成 patch
    python3 scripts/gen_cpp_patch.py --check        # 只报告，不写文件
    python3 scripts/gen_cpp_patch.py --list-missing # 列出还没译的条目
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PO = REPO / "locales/zh_CN/LC_MESSAGES/mujoco_zh.po"
AUDIT = REPO / "data/ux_strings_audit.json"
PATCH = REPO / "patches/ux-zh.patch"

#: C++ 源码里要改的目标文件（相对 mujoco 源码根）
#:
#: ⚠️ 为什么是**多个文件**：界面文字不只在 gui.cc ——
#:    * `sim/sim_profiler.cc` —— Profiler 面板的图表标题与 12 条曲线图例
#:      （`CpuTimeGraph` / `DimensionsGraph`，走 `ImPlot::BeginPlot` 和
#:       `GetLegendLabel` / `GetDimensionLabel`）
#:    * `ux/gui_spec.cc`       —— Elements（属性）面板的 `list(...)` 条目
#:    只改 gui.cc 会漏掉这两块（实测过：用户点开 Profiler 发现仍是英文）。
TARGET_FILES: tuple[str, ...] = (
    "src/experimental/platform/ux/gui.cc",
    "src/experimental/platform/ux/gui_spec.cc",
    "src/experimental/platform/sim/sim_profiler.cc",
)

#: ⚠️ 这几个**绝不翻译**：
#:   * 窗口名 —— `DockBuilderDockWindow("Inspector", ...)` 按**名字**匹配窗口，
#:     改名会让 docking 找不到目标、布局错乱
#:   * ImGui 持久 ID —— `ImGui::GetID("Root")`，一改 ID 就变
#:
#: 注意 `Elements` / `Default` / `Text` / `Tuple` 这些**不在**此列 ——
#: 它们出现在 `list("Actuators", mjOBJ_ACTUATOR)` 里，第一个参数是**纯显示标题**
#: （类型由第二个参数给），所以**该翻**。
NEVER_TRANSLATE: frozenset[str] = frozenset({
    "Dockspace", "Inspector", "Editor", "Profiler", "Explorer",
    "Root",
    # 代码拼接的半截串 —— `return "Tracking (" + std::to_string(...) + ")"`，
    # 翻了会让括号不匹配（实测编译错误）
    "Tracking (",
})

#: ⚠️ 这些**不翻译** —— 它们是 mjData 字段名 / 求解器算法名 / 单位缩写。
#: 官方文档也用英文，翻了反而对不上。它们在 UI 里与旁边的说明标签配对出现，
#: 例如 {"QPOS", "Position"} —— 只翻 "Position"。
NO_TRANSLATE: frozenset[str] = frozenset({
    # 状态组件标识符（mjData 字段名）
    "ACT", "CTRL", "EQ_ACTIVE", "QFRC_APPLIED", "XFRC_APPLIED",
    "QPOS", "QVEL", "MOCAP_POS", "MOCAP_QUAT", "WARMSTART", "HISTORY",
    "USERDATA", "TIME",
    # 积分器 / 求解器算法名（专有名词）
    "CG", "PGS", "Newton", "RK4", "Euler", "Dense", "Sparse",
    # 单位 / 缩写
    "CPU", "FPS", "FOV",
})

#: 含格式符的串：译文里必须**原样保留**这些占位符
FMT_RE = re.compile(r"%[-#0-9.]*[dsfegx]")


def parse_po(path: Path) -> dict[str, str]:
    """读 .po，返回 msgid → msgstr（跳过空译文和文件头）。"""
    out: dict[str, str] = {}
    msgid = msgstr = None
    mode = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#") or not line:
            continue
        if line.startswith("msgid "):
            if msgid is not None and msgstr:
                out[msgid] = msgstr
            msgid = _unquote(line[6:])
            msgstr = None
            mode = "id"
        elif line.startswith("msgstr "):
            msgstr = _unquote(line[7:])
            mode = "str"
        elif line.startswith('"'):
            if mode == "id" and msgid is not None:
                msgid += _unquote(line)
            elif mode == "str" and msgstr is not None:
                msgstr += _unquote(line)
    if msgid is not None and msgstr:
        out[msgid] = msgstr
    out.pop("", None)
    return out


def _unquote(s: str) -> str:
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return s.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


def c_escape(s: str) -> str:
    """转义成 C++ 字符串字面量内部的形式。"""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_patch(translations: dict[str, str], src_dir: Path) -> tuple[str, dict]:
    """在源码副本上做替换，返回 (unified diff, 统计)。

    支持**多文件** —— 界面文字散在 gui.cc / gui_spec.cc / sim_profiler.cc，
    只改一个会漏（实测：Profiler 面板整块仍是英文）。
    """
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    todo = [en for en in audit["translatable"] if en not in NO_TRANSLATE]

    diffs: list[str] = []
    applied, skipped, missing = [], [], []
    touched_files: list[str] = []

    for rel in TARGET_FILES:
        target = src_dir / rel
        if not target.is_file():
            raise SystemExit(
                f"❌ 找不到源码: {target}\n"
                f"   （先跑 scripts/build_ux_zh.sh 拉源码，或用 --src 指定）")

        original = target.read_text(encoding="utf-8")
        text = original

        for en in todo:
            zh = translations.get(en)
            if not zh:
                continue
            # 带 ## 的串是 ImGui ID 后缀，绝不能改（改了 widget ID 会变、
            # 布局会被 ini 缓存搞乱）
            if "##" in en:
                continue
            # 格式串校验：占位符必须一致
            if sorted(FMT_RE.findall(en)) != sorted(FMT_RE.findall(zh)):
                raise SystemExit(
                    f"❌ 格式符不一致: {en!r} → {zh!r}\n"
                    f"   原文 {FMT_RE.findall(en)}，译文 {FMT_RE.findall(zh)}")

            # ⚠️ 只替换**完整字面量** `"<en>"`，绝不碰前缀/子串
            #    （否则 "Body" 会误伤 "Body " 之类的其它串）
            #
            # 写作 `中文 (English)##English`：`##` 后是 ImGui 的 ID 部分，
            # 保持英文原串 ⇒ widget ID 恒定，界面文字变了但状态（展开、
            # docking 布局）不受影响。详见 docs/重编译覆盖C++字符串.md
            needle = f'"{c_escape(en)}"'
            replacement = f'"{c_escape(zh)} ({c_escape(en)})##{c_escape(en)}"'
            n = text.count(needle)
            if n == 0:
                continue
            text = text.replace(needle, replacement)
            applied.append((en, zh, rel, n))

        # 汇总是按「条目」算的，某条可能已在别的文件里替换过
        if text != original:
            touched_files.append(rel)
            tmp = target.with_suffix(target.suffix + ".zh")
            tmp.write_text(text, encoding="utf-8")
            d = subprocess.run(
                ["diff", "-u",
                 "--label", f"a/{rel}", "--label", f"b/{rel}",
                 str(target), str(tmp)],
                capture_output=True, text=True).stdout
            tmp.unlink()
            diffs.append(d)

    if not diffs:
        # 区分「译文表有问题」和「源码已经是译文状态」—— 后者是正常情况
        already = sum(1 for rel in TARGET_FILES
                      for en, zh in translations.items()
                      if en in audit["translatable"]
                      and f'"{c_escape(zh)} ({c_escape(en)})##' in
                          (src_dir / rel).read_text(encoding="utf-8"))
        if already:
            raise SystemExit(
                f"⚠️ 源码已处于译文状态（检测到 {already} 处已翻译），无需重复生成。\n"
                f"   要从干净源码重来：删除源码树后重跑 scripts/build_ux_zh.sh\n"
                f"   （patch 本身没问题，可以直接用）")
        raise SystemExit(
            "❌ 没有任何替换生效，且源码里也找不到译文。\n"
            "   可能原因：\n"
            "     1. audit 清单与源码版本不匹配（换过 mujoco 版本？）\n"
            "     2. 译文表没覆盖 audit 里的串 —— 跑 --list-missing 看看")

    # 按文件分别统计，便于核对漏翻
    by_file: dict[str, int] = {}
    for _, _, rel, _ in applied:
        by_file[rel] = by_file.get(rel, 0) + 1
    missing = sorted({en for en in todo if en not in translations})

    return "".join(diffs), {
        "applied": applied, "skipped": skipped, "missing": missing,
        "by_file": by_file, "files": touched_files,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path,
                    default=Path("/tmp/ux-zh-build/mujoco-src"),
                    help="mujoco 仓库根（含 CMakeLists.txt；默认 build_ux_zh.sh 的 WORKDIR）")
    ap.add_argument("--out", type=Path, default=PATCH)
    ap.add_argument("--check", action="store_true", help="只报告，不写文件")
    ap.add_argument("--list-missing", action="store_true", help="列出缺译文的条目")
    args = ap.parse_args()

    if not PO.is_file():
        print(f"❌ 找不到 {PO}", file=sys.stderr)
        return 1
    translations = parse_po(PO)
    print(f"读入 .po: {len(translations)} 条译文")

    diff, st = build_patch(translations, args.src)

    print(f"\n替换生效: {len(st['applied'])} 处，分布：")
    for rel, n in sorted(st["by_file"].items()):
        print(f"    {n:>4}  {rel}")
    print(f"跳过（窗口名/ID/标识符）: {len(st['skipped'])} 条")
    print(f"缺译文:                   {len(st['missing'])} 条")

    if args.list_missing and st["missing"]:
        print("\n═══ 还缺译文的 ═══")
        for s in st["missing"]:
            print(f"  {s}")
        return 0

    if not st["applied"]:
        print("❌ 无改动", file=sys.stderr)
        return 1

    if args.check:
        print("\n（--check：未写入文件）")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(diff, encoding="utf-8")
    print(f"\n✅ 已写出 {args.out}")
    print(f"   {len(diff.splitlines())} 行 diff")
    print("\n应用：./scripts/build_ux_zh.sh   （会自动 apply patches/*.patch）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
