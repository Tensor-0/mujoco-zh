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
TARGET_FILE = "src/experimental/platform/ux/gui.cc"

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
    """在源码副本上做替换，返回 (unified diff, 统计)。"""
    target = src_dir / TARGET_FILE
    if not target.is_file():
        raise SystemExit(f"❌ 找不到源码: {target}\n"
                         f"   （先跑 scripts/build_ux_zh.sh 拉源码，或用 --src 指定）")

    original = target.read_text(encoding="utf-8")
    text = original
    applied, skipped, missing = [], [], []

    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    for en in audit["translatable"]:
        if en in NO_TRANSLATE:
            skipped.append(en)
            continue
        zh = translations.get(en)
        if not zh:
            missing.append(en)
            continue

        # 格式串校验：占位符必须一致
        if sorted(FMT_RE.findall(en)) != sorted(FMT_RE.findall(zh)):
            raise SystemExit(
                f"❌ 格式符不一致: {en!r} → {zh!r}\n"
                f"   原文占位符 {FMT_RE.findall(en)}，译文 {FMT_RE.findall(zh)}")

        # ⚠️ 只替换**完整字面量** `"<en>"`，绝不碰前缀/子串
        #    （否则 "Body" 会误伤 "Body " 之类的其它串）
        # 双语并列，与 Python 侧 translate.py 的风格一致：中文 (English)
        # ⚠️ 不能用 "中文 (English)" 直接替换 —— 英文原文在括号里，ImGui 的
        #    widget ID 会跟着变。这里改成「中文 (English)」但**保留原串做 ID**：
        #    ImGui 约定 "显示文本##ID"，所以写成 "中文 (English)##English"。
        needle = f'"{c_escape(en)}"'
        replacement = f'"{c_escape(zh)} ({c_escape(en)})##{c_escape(en)}"'
        n = text.count(needle)
        if n == 0:
            continue
        # ⚠️ 带 ## 的串是 ImGui 的 ID 后缀，绝不能改（改了 widget ID 会变、
        #    布局会给 ini 缓存搞乱）。audit 已过滤，这里再兜一层。
        if "##" in en:
            skipped.append(en)
            continue
        text = text.replace(needle, replacement)
        applied.append((en, zh, n))

    if text == original:
        raise SystemExit("⚠️ 没有任何替换生效 —— 检查译文表是否覆盖了 audit 里的串")

    # 用 `diff -u` 产出标准 unified diff（git apply 认这个）
    tmp_new = src_dir / (TARGET_FILE + ".zh")
    tmp_new.write_text(text, encoding="utf-8")
    diff = subprocess.run(
        ["diff", "-u",
         "--label", f"a/{TARGET_FILE}", "--label", f"b/{TARGET_FILE}",
         str(target), str(tmp_new)],
        capture_output=True, text=True).stdout
    tmp_new.unlink()

    return diff, {"applied": applied, "skipped": skipped, "missing": missing}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path,
                    default=Path("/tmp/ux-zh-build/mujoco-src/src"),
                    help="mujoco 源码根（默认 build_ux_zh.sh 的 WORKDIR）")
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

    print(f"\n替换生效: {len(st['applied'])} 条")
    print(f"跳过（标识符/算法名）: {len(st['skipped'])} 条")
    print(f"缺译文:               {len(st['missing'])} 条")

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
