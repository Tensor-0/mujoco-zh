#!/usr/bin/env python3
"""从 gui.cc 抽取「需要悬停说明」的控件调用点。

    python3 scripts/extract_controls.py                      # 打印统计
    python3 scripts/extract_controls.py --write              # 写 JSON 骨架

覆盖两类（都不走 gui_spec.cc 的 FIELD/QFIELD 那条路）：

  A. 辅助函数 —— 本身没有 tooltip 形参，需要加一个尾随参数
       ImGui_Input / ImGui_InputN / ImGui_Slider / ImGui_SwitchToggle / ImGui_Checkbox
  B. 直调 ImGui —— 只能在调用点后面插一行 SetItemTooltip
       ImGui::ColorEdit3/4 / SliderInt / SliderFloat / InputInt

⚠️ 刻意排除的：
   * ImGui::Checkbox（gui.cc:851）—— 官方已经用老式
     `if (ImGui::IsItemHovered()) ImGui::SetTooltip(...)` + 查表挂上了（state component 勾选框）
   * ImGui_Slider（gui.cc:1329/1386）—— label 是变量 name，逐关节/执行器动态生成，无固定文案
   * ImGui::BeginCombo / ImGui::InputText / ImGui::Button / ImGui::Selectable ——
     前两个的 label 是 "##Speed" 这类**纯 ID**（无可读文本）；后两个是导航控件

⚠️ 不在本脚本范围内的（已由 68 条开关那批覆盖）：
   ImGui_BitToggle / ImGui_ButtonToggle 落在 4 个 CALL_SITES 循环里的调用点。
   但另外两个循环（Act Group %d / GroupGui 的 %s %d）**没覆盖**，本脚本会单独挑出来。

⚠️ 复用 extract_spec_fields 的字符扫描器 —— tooltip 字面量里含分号/逗号，
   `[^;]*?` 这类正则会截断。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from extract_spec_fields import _scan, _split_args   # noqa: E402

REPO = HERE.parent
OUT = REPO / "data/tooltips_controls.json"
MUJOCO_SRC = Path("/tmp/mujoco-src/src")
DEFAULT_CC = MUJOCO_SRC / "src/experimental/platform/ux/gui.cc"
GUI_CC_REL = "src/experimental/platform/ux/gui.cc"

#: 辅助函数 —— 要加尾随形参。值 = 参数个数（含 name）
HELPERS: dict[str, int] = {
    "ImGui_Input": 3,      # name, value, opts
    "ImGui_InputN": 4,     # name, value, num, opts
    "ImGui_Slider": 4,     # name, value, min, max
    "ImGui_SwitchToggle": 2,   # label, boolean  (size 有默认值)
    "ImGui_Checkbox": 2,   # name, value
}

#: 直调 ImGui 控件 —— 在调用点后插 SetItemTooltip
DIRECT: tuple[str, ...] = (
    "ImGui::ColorEdit4", "ImGui::ColorEdit3",
    "ImGui::SliderInt", "ImGui::SliderFloat",
    "ImGui::InputInt",
    "ImGui::Combo",
)

#: ⚠️ 这两个循环里的 toggle **没被 68 条那批覆盖**，单独挑出来
EXTRA_LOOPS: tuple[str, ...] = (
    "Act Group %d",
    "%s %d",          # GroupGui（已在 CONCAT_HELPERS，不能加 ##）
)


def _skip_noise(code: str, i: int) -> int:
    """若 `code[i]` 是字符串/注释的开始，返回跳过后的下标；否则返回 i。"""
    n = len(code)
    if code[i] == '"':
        j = i + 1
        while j < n and code[j] != '"':
            j += 2 if code[j] == "\\" else 1
        return j + 1
    if code[i] == "/" and i + 1 < n and code[i + 1] == "/":
        while i < n and code[i] != "\n":
            i += 1
        return i
    if code[i] == "/" and i + 1 < n and code[i + 1] == "*":
        i += 2
        while i + 1 < n and not (code[i] == "*" and code[i + 1] == "/"):
            i += 1
        return i + 2
    return i


def _fn_bounds(src: str) -> list[tuple[int, int, str]]:
    """所有 `void XxxGui(` 的 (起, 止, 名)。"""
    hits = [(m.start(), m.group(1))
            for m in re.finditer(r"^void (\w+Gui)\(", src, re.M)]
    out = []
    for k, (pos, name) in enumerate(hits):
        end = hits[k + 1][0] if k + 1 < len(hits) else len(src)
        out.append((pos, end, name))
    return out


def extract(cc: Path) -> list[dict]:
    src = cc.read_text(encoding="utf-8")
    bounds = _fn_bounds(src)
    # 每行属于哪个函数
    line_of = [0] * (src.count("\n") + 1)
    for a, b, name in bounds:
        for ln in range(src.count("\n", 0, a),
                        src.count("\n", 0, b)):
            line_of[ln] = name  # 后写的覆盖，但函数不重叠

    calls: list[dict] = []
    names = sorted(set(HELPERS) | set(DIRECT), key=len, reverse=True)
    alt = "|".join(re.escape(n) for n in names)
    pat = re.compile(rf"\b({alt})\s*\(")

    i = 0
    n = len(src)
    while i < n:
        j = _skip_noise(src, i)
        if j != i:
            i = j
            continue
        m = pat.match(src, i)
        if not m:
            i += 1
            continue
        close = _scan(src, m.end() - 1)
        args = _split_args(src[m.end():close])
        fn = m.group(1)
        line = src.count("\n", 0, i) + 1
        panel = line_of[min(line - 1, len(line_of) - 1)]
        # label 一定是第一个实参，且是字面量
        if not args or not args[0].startswith('"'):
            i = close + 1
            continue
        label = args[0][1:-1]
        if fn in HELPERS:
            kind = "helper"
            if len(args) > HELPERS[fn]:
                i = close + 1
                continue          # tooltip 已经有了（重跑时）
        else:
            # 直调：`ImGui::Checkbox` 等第一个参数是 label；
            # 但 ColorEdit3/4 / SliderInt 第一个也是 label。统一。
            kind = "direct"
        calls.append({"panel": panel, "fn": fn, "kind": kind,
                      "label_en": label, "line": line,
                      "args": args})
        i = close + 1

    # 额外两个循环
    for lit in EXTRA_LOOPS:
        for m in re.finditer(re.escape(f'"{lit}"'), src):
            line = src.count("\n", 0, m.start()) + 1
            calls.append({"panel": line_of[min(line - 1, len(line_of) - 1)],
                          "fn": "(loop)", "kind": "loop",
                          "label_en": lit, "line": line, "args": []})
    return sorted(calls, key=lambda c: c["line"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cc", type=Path, default=DEFAULT_CC)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    if not args.cc.is_file():
        raise SystemExit(f"❌ 找不到 {args.cc}\n   先跑 scripts/build_ux_zh.sh 拉源码")

    calls = extract(args.cc)
    from collections import Counter
    by_panel = Counter(c["panel"] for c in calls)
    by_fn = Counter(c["fn"] for c in calls)

    print(f"控件调用点 {len(calls)}")
    print("\n按面板：")
    for p, k in by_panel.most_common():
        print(f"  {p:<22} {k:>3}")
    print("\n按控件：")
    for f, k in by_fn.most_common():
        print(f"  {f:<24} {k:>3}")

    if args.write:
        if args.out.exists():
            raise SystemExit(f"❌ {args.out} 已存在 —— 不覆盖")
        # (label_en, panel) 去重 —— 同一标签在不同面板含义不同
        seen, entries = set(), []
        for c in calls:
            key = (c["label_en"], c["panel"])
            if key in seen:
                continue
            seen.add(key)
            entries.append({"label_en": c["label_en"], "panel": c["panel"],
                            "fn": c["fn"], "kind": c["kind"], "zh": "",
                            "provenance": ""})
        skel = {
            "schema": 1, "mujoco_version": "3.11.0",
            "_doc": [
                "Studio 里「非 Elements 面板」的控件悬停说明。",
                "覆盖三类：",
                "  helper — ImGui_Input / ImGui_InputN / ImGui_Slider /",
                "           ImGui_SwitchToggle / ImGui_Checkbox（本身没有 tooltip 形参，要加尾随参数）",
                "  direct — ImGui::ColorEdit3/4 / SliderInt / SliderFloat / Checkbox / InputInt",
                "           （在调用点之后插一行 SetItemTooltip）",
                "  loop   — Act Group %d / GroupGui 的 %s %d（68 条开关那批没覆盖到）",
                "",
                "⚠️ key 是 (label_en, panel) —— 同一英文标签在不同面板含义不同",
                "   （Constraint 在 Mapping 是缩放系数、在 Colors 是颜色）。",
                "",
                "⚠️ zh 里禁用 `##`（会被 ImGui 当 ID 吃掉）与 `**`（ImGui 按纯文本渲染，会画出星号）。",
                "",
                "字段：",
                "  label_en   控件英文标签（= gui.cc 里的字面量），也是 tooltip 首行的英文部分",
                "  panel      所属面板函数名，用于消歧",
                "  zh         译文。留空 = 还没写，生成器会报错",
                "  provenance 出处，必填",
            ],
            "entries": entries,
        }
        args.out.write_text(json.dumps(skel, ensure_ascii=False, indent=1) + "\n",
                            encoding="utf-8")
        print(f"\n✅ 写入 {args.out}（{len(entries)} 条，已按 (label, panel) 去重）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
