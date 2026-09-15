#!/usr/bin/env python3
"""从 gui_spec.cc 抽取 `FIELD` / `QFIELD` 调用点，生成 data/tooltips_fields.json 骨架。

    python3 scripts/extract_spec_fields.py            # 打印统计
    python3 scripts/extract_spec_fields.py --write    # 写骨架（已存在则不覆盖）

⚠️ 为什么不用正则一把梭：tooltip 字面量里**含分号和逗号**，例如

    FIELD(elastic2d, "2D passive forces; 0: none, 1: bending, 2: stretching, 3: both");

`[^;]*?` 会在字符串内部的分号处截断。所以这里手写一个**知道字符串/注释边界**的
字符扫描器。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SRC = REPO / "data/tooltips_fields.json"
MUJOCO_SRC = Path("/tmp/mujoco-src/src")
DEFAULT_CC = MUJOCO_SRC / "src/experimental/platform/ux/gui_spec.cc"
MUJOCO_VER = "3.11.0"


def _scan(code: str, start: int) -> int:
    """`code[start]` 是 `(`；返回配对 `)` 的下标。跳过字符串/字符/注释。"""
    depth = 0
    i = start
    n = len(code)
    while i < n:
        c = code[i]
        if c == '"':                       # 字符串字面量（含转义）
            i += 1
            while i < n and code[i] != '"':
                i += 2 if code[i] == "\\" else 1
        elif c == "'":
            i += 1
            while i < n and code[i] != "'":
                i += 2 if code[i] == "\\" else 1
        elif c == "/" and i + 1 < n and code[i + 1] == "/":
            while i < n and code[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and code[i + 1] == "*":
            i += 2
            while i + 1 < n and not (code[i] == "*" and code[i + 1] == "/"):
                i += 1
            i += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("括号不配对")


def _split_args(argstr: str) -> list[str]:
    """按**顶层**逗号切分实参（跳过字符串、以及 `{}`/`()`/`[]` 里的逗号）。

    ⚠️⚠️ **必须认大括号**。踩过：`ImGui_Input("Timestep", &opt.timestep, {0, 1, 0.01, 0.1})`
    的 `{0, 1, 0.01, 0.1}` 会被切成 4 段，于是实参数被判成 6 个 ——
    调用方 `if len(args) > ARITY` 会当成「已经有 tooltip 了」**静默跳过这 12 条**。
    不报错，只是文案凭空少一半。这正是本项目反复踩的「静默失败」类。
    """
    out, buf, i, n = [], [], 0, len(argstr)
    depth = 0          # 大括号 / 小括号 / 方括号的合计嵌套深度
    while i < n:
        c = argstr[i]
        if c in "\"'":
            q = c
            j = i + 1
            while j < n and argstr[j] != q:
                j += 2 if argstr[j] == "\\" else 1
            buf.append(argstr[i:j + 1])
            i = j + 1
            continue
        if c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
        elif c == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return out


def extract(cc: Path) -> list[dict]:
    code = cc.read_text(encoding="utf-8")
    begin = code.index("void ElementSpecGui(")
    end = code.index("void ElementModelGui(")
    body = code[begin:end]

    calls: list[dict] = []
    cur_type: str | None = None
    pending: str | None = None
    depth = 0
    type_depth = -1

    i, n = 0, len(body)
    while i < n:
        c = body[i]

        if c == '"':                                     # 跳过字符串
            j = i + 1
            while j < n and body[j] != '"':
                j += 2 if body[j] == "\\" else 1
            i = j + 1
            continue
        if c == "/" and i + 1 < n and body[i + 1] == "/":  # 跳过行注释
            while i < n and body[i] != "\n":
                i += 1
            continue
        # ⚠️ 跳过预处理指令 —— `#define FIELD(NAME, TIP) table(#NAME, ..., TIP);`
        #    本身长得就像一个 FIELD 调用，不排除会把宏定义当成调用点抽出来。
        if c == "#" and not body[body.rfind("\n", 0, i) + 1:i].strip():
            while i < n and body[i] != "\n":
                i += 1
            continue

        if c == "{":
            depth += 1
            if pending:
                cur_type, type_depth = pending, depth
                pending = None
            i += 1
            continue
        if c == "}":
            if cur_type and depth == type_depth:
                cur_type, type_depth = None, -1
            depth -= 1
            i += 1
            continue

        m = re.match(r"case\s+(mjOBJ_\w+)\s*:", body[i:])
        if m:
            pending = m.group(1)
            i += m.end()
            continue

        m = re.match(r"\b(Q?FIELD)\s*\(", body[i:])
        if m:
            close = _scan(body, i + m.end() - 1)
            args = _split_args(body[i + m.end():close])
            macro = m.group(1)
            want = 3 if macro == "QFIELD" else 2
            if len(args) != want:
                raise SystemExit(f"❌ {macro} 实参数 {len(args)} ≠ {want}：{args}")
            tip = args[-1]
            if not (tip.startswith('"') and tip.endswith('"')):
                raise SystemExit(f"❌ {macro} 的 tip 不是纯字面量：{tip}")
            if not cur_type:
                raise SystemExit(f"❌ {macro}({args[0]}) 不在任何 case 分支内")
            line = body.count("\n", 0, i) + code.count("\n", 0, begin) + 1
            # tip 字面量在**整份文件**里的偏移量 —— 供 gen_cpp_patch.py 做定点替换。
            # tip 是最后一个实参，用 rfind 从本调用起点往右找最稳。
            tip_off = begin + body.rfind(tip, i + m.end(), close)
            calls.append({
                "line": line,
                "elemtype": cur_type,
                "field": args[0],
                "alt": args[1] if macro == "QFIELD" else None,
                "tip_en": tip[1:-1],
                "tip_start": tip_off,
                "tip_end": tip_off + len(tip),
            })
            i = close + 1
            continue

        i += 1

    return calls


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cc", type=Path, default=DEFAULT_CC)
    ap.add_argument("--write", action="store_true", help="写 data/tooltips_fields.json")
    ap.add_argument("--out", type=Path, default=SRC)
    args = ap.parse_args()

    if not args.cc.is_file():
        raise SystemExit(f"❌ 找不到源码 {args.cc}\n   先跑 scripts/build_ux_zh.sh 拉源码")

    calls = extract(args.cc)
    types = sorted({c["elemtype"] for c in calls})
    tips = sorted({c["tip_en"] for c in calls})
    fields = sorted({c["field"] for c in calls})

    print(f"调用点        {len(calls)}")
    print(f"对象类型      {len(types)}")
    print(f"不重复字段名  {len(fields)}")
    print(f"不重复 tooltip {len(tips)}")
    print()
    print("每类型条数：")
    for t in types:
        print(f"  {t:<18} {sum(1 for c in calls if c['elemtype'] == t):>3}")

    if args.write:
        if args.out.exists():
            raise SystemExit(f"❌ {args.out} 已存在 —— 不覆盖（先手动备份/删除）")
        skeleton = {
            "schema": 1,
            "mujoco_version": MUJOCO_VER,
            "_doc": [
                "Studio「Elements / Inspector（属性）」面板的悬停说明与属性名。",
                "由 scripts/extract_spec_fields.py 从 gui_spec.cc 抽出，"
                "scripts/gen_cpp_patch.py 消费。",
                "",
                "⚠️ 属性名走 ImGui::Text 显示（imgui_widgets.cc:210），",
                "   而 Text **不剥 `##`**（imgui_widgets.cpp:190）⇒ labels 里绝不能出现 `##`。",
                "   同理 entries 的 zh 也不能含 `##`（tooltip 是显示文本）。",
                "",
                "字段：",
                "  en         官方英文原文（= gui_spec.cc 里的 TIP 字面量），entries 的 key",
                "  zh         中文译文。留空 = 还没写，生成器会报错",
                "  scope      可选。同一 en 在不同对象类型下要不同译文时用，值为 mjOBJ_XXX",
                "  note       可选。官方原文有瑕疵/复读时的说明（会写进 provenance）",
                "  provenance 出处。每条必填",
            ],
            "labels": [{"field": f, "zh": ""} for f in fields],
            "entries": [{"en": t, "zh": ""} for t in tips],
            "calls": calls,
        }
        args.out.write_text(
            json.dumps(skeleton, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        print(f"\n✅ 写入 {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
