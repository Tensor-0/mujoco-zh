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
TOOLTIPS = REPO / "data/tooltips_flags.json"

#: Elements（属性）面板的数据 —— 179 个属性名 + 228 条 tooltip。
#: 由 scripts/extract_spec_fields.py 从 gui_spec.cc 抽出，见该文件的说明。
FIELDS = REPO / "data/tooltips_fields.json"

#: 非 Elements 面板的控件（数值框 / 颜色块 / 滑块 / 开关）—— 76 条。
#: 由 scripts/extract_controls.py 从 gui.cc 抽出。
CONTROLS = REPO / "data/tooltips_controls.json"

#: C++ 源码里要改的目标文件（相对 mujoco 源码根）
#:
#: ⚠️ 为什么是**多个文件**：界面文字不只在 gui.cc ——
#:    * `sim/sim_profiler.cc` —— Profiler 面板的图表标题与 12 条曲线图例
#:      （`CpuTimeGraph` / `DimensionsGraph`，走 `ImPlot::BeginPlot` 和
#:       `GetLegendLabel` / `GetDimensionLabel`）
#:    * `ux/gui_spec.cc`       —— Elements（属性）面板的 `list(...)` 条目
#:      ＋ FIELD/QFIELD 的属性名与悬停说明
#:    * `ux/imgui_widgets.{cc,h}` —— 属性表的控件层，**唯一**一处悬停挂载点修复
#:    只改 gui.cc 会漏掉这几块（实测过：用户点开 Profiler 发现仍是英文）。
TARGET_FILES: tuple[str, ...] = (
    "src/experimental/platform/ux/gui.cc",
    "src/experimental/platform/ux/gui_spec.cc",
    "src/experimental/platform/ux/imgui_widgets.cc",
    "src/experimental/platform/ux/imgui_widgets.h",
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


#: ⚠️⚠️ **哪些串不能加 `##`**——必须按「这个串最终被谁消费」判断。
#:
#: `##` 在 ImGui 里的语义是「从这里开始是 ID，不显示」。它**只在
#: ImGui 直接把 label 当 ID 用时才成立**。一旦这个串被 `snprintf`/
#: 字符串拼接当**数据**用，或整串被当成**显示文本**，`##` 就会把
#: 后面的内容全部吃掉。
#:
#: 实测踩过的三类（2026-09-15，用户截图对比发现"汉化版缺了数字信息"）：
#:
#:   A. **会被拼接/格式化**  `snprintf("%s %d", name, i)`
#:      ⇒ 拼出 `几何体 (Geoms)##Geoms 0`，ImGui 从 `##` 起当 ID
#:      ⇒ 屏幕上只剩 `几何体 (Geoms)`，**编号 0~5 全丢**
#:      （`GroupGui` 一个 lambda 就吃掉 7 组 × 6 = 42 个编号）
#:
#:   B. **整串当显示文本**  `SetItemTooltip("%s", "...")`
#:      ⇒ `##` 后全不显示 ⇒ **译文被截掉一半**（`目标速度 (Desired Speed)` 全没）
#:
#:   C. **本就有 `###` 分隔**  `"%-9s (%4.0f)###%s"`
#:      ⇒ 我们**多加**一个 `##` 反而破坏原有分隔
#:
#: ✅ **可以加**的位置：`ImGui::Begin` / `TreeNodeEx` / `Button` / `Text` 等
#:    直接把 label 传给 ImGui 的调用，以及 `SectionHeader`（内部用
#:    `window->GetID(label)`，本就吃 `##`）。
#:
#: 判别方法：**看这个字面量出现的那一行**。出现在 `snprintf`/`sprintf`/
#: `std::to_string` 拼接/`SetItemTooltip` 附近 ⇒ 不加 `##`；
#: 直接是 `ImGui::Xxx("...")` 的第一个参数 ⇒ 加。
#:
#: ⚠️ **但有一种「行内看不出问题」的陷阱**：字面量被当成**参数**传进一个
#: 「内部会拼接它」的辅助函数。此时该行长得完全正常
#: （`GroupGui("Geoms", ...)`），危险发生在**函数体内**。
#: 这类只能靠**知道函数名**来判 —— 见 `CONCAT_HELPERS`。
NOWRAP_GUARDS: tuple[str, ...] = (
    "snprintf", "sprintf", "to_string", "SetItemTooltip", "###",
)

#: ⚠️ 这些辅助函数**内部会拼接/格式化它的参数**，所以调用点传进去的
#: 字面量**不能带 `##`**（带了会被函数体拼进数据、再被 ImGui 当 ID 吃掉）。
#:
#:   `GroupGui(name, group)` —— 内部
#:       `snprintf(label, "%s %d", name, i)`  ⇒ 拼出 `几何体 (Geoms)##Geoms 0`
#:       ⇒ **每个分组的 0~5 编号全丢**（7 组 × 6 = 42 个）
#:
#: 新增此类函数时**必须**加进来，否则症状是「编号/后缀凭空消失」。
CONCAT_HELPERS: tuple[str, ...] = (
    "GroupGui",
)

#: 一行里出现这些 ⇒ 该行上的字面量**不能**加 `##`
IDX_RE = re.compile(r"%[-#0-9.]*[dsfegx]")


def _needs_no_wrap(line: str, stmt: str = "") -> bool:
    """这一行上的字符串字面量能不能安全地加 `##` 后缀？

    三类危险：
      ① 本行内就在拼接/格式化/当显示文本（`NOWRAP_GUARDS`）
      ② 本行把字面量传给了「内部会拼接它」的辅助函数（`CONCAT_HELPERS`）
         —— 这一行本身看着无害，危险在被调函数体内
      ③ ⚠️ **跨行字面量**（`stmt` 派上用场）—— 见下面的长注释

    ### ⚠️ 为什么需要 `stmt`（第三个参数）—— 踩过的第四个变体

    上游有这样的**跨行**写法（`gui.cc`，原版）：

        ImGui::SetItemTooltip(                        // ← SetItemTooltip 在这一行
            "Disable gravity and passive springs,\\n"  // ← 续行，本行没有 SetItemTooltip
            "add viscosity for easier posing.");       // ← 字面量在这一行 ← 误判！

    **只看 `line` 会漏**：第 3 行里没有 `SetItemTooltip` 字样，
    于是被判为「可以加 `##`」⇒ 生成
    `"增加粘度… (add viscosity for easier posing.)##add viscosity for easier posing."`
    ⇒ **用户悬停时看到尾巴上挂着一坨 `##...`**
    （`TextEx` 明确不剥 `##`：imgui_widgets.cpp:190
      *"we don't hide text after ## in this end-user function"*）。

    ⇒ 所以要把判断提升到**语句级**：`stmt` 是「本行所属的完整语句」
    （由调用方用括号配平切出来），只要语句里出现过 guard 就算危险。

    ⚠️ 这已经是我踩的**第四个** `##` 误用变体了。根本教训：
    **行级启发式在 C++ 上是不可靠的** —— 想彻底避免，就别让文案经过这条路
    （tooltip 那批就是这么设计的：生成器对它只做原样输出）。
    """
    for text in (line, stmt):
        if not text:
            continue
        if any(g in text for g in NOWRAP_GUARDS):
            return True
        if any(re.search(rf'\b{h}\s*\(', text) for h in CONCAT_HELPERS):
            return True
    return False


def _statement_spans(lines: list[str]) -> list[str]:
    """把源码按「语句」切分，返回**与 `lines` 等长**的列表。

    每个元素是该行所属语句的全文（用于 `_needs_no_wrap` 的跨行判断）。
    切分规则很粗但够用：以 `;` 结尾且括号已配平的算一条语句结束。

    ⚠️ 不需要精确解析 C++ —— 只要求「跨行字面量所在的整条语句」能被看见。
    """
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    for ln in lines:
        buf.append(ln)
        # 粗略配平：只数圆括号（字符串内的括号会让计数偏移，但我们的目标是
        # 让「同一条语句」聚合在一起，多聚合一点是安全的，少聚合才危险）
        depth += ln.count("(") - ln.count(")")
        stripped = ln.split("//")[0].rstrip()
        if depth <= 0 and (stripped.endswith(";") or stripped.endswith("{")):
            blk = "".join(buf)
            out.extend([blk] * len(buf))
            buf, depth = [], 0
    if buf:                       # 收尾：未闭合的残余
        blk = "".join(buf)
        out.extend([blk] * len(buf))
    return out[:len(lines)]


# ══════════════════════════════════════════════════════════════════
# tooltip（悬停说明）—— 与 label 替换**共用一条流水线**
# ══════════════════════════════════════════════════════════════════
#
# 数据在 data/tooltips_flags.json，由**另一条路**（不是 .po）维护：
# 这 26 个开关的标签来自 libmujoco.so 的 mjDISABLESTRING/mjENABLESTRING，
# **ux.so 里一个字符串字面量都没有** ⇒ `.po` 那条「替换已存在字面量」的
# 机制物理上做不到，只能新增 C++ 数组 + 调用点。

GUI_CC = "src/experimental/platform/ux/gui.cc"

#: 注入锚点 —— 必须**早于**所有用到这些数组的函数
#: ⚠️ 不能锚在 RenderingGui（1212）之前：kDisableTip 在 PhysicsGui（986）里就要用
INJECT_ANCHOR = "void PhysicsGui(mjModel* model, float min_width) {"

#: 调用点：循环头 → 标签表达式 → tooltip 数组名
#:
#: ⚠️ 标签表达式有两种形态，别搞混：
#:   `mjDISABLESTRING[i]`    —— 一维数组
#:   `mjVISSTRING[i][0]`     —— **二维**（[i][0] 是显示名、[i][1] 是默认值、[i][2] 是快捷键）
CALL_SITES: tuple[tuple[str, str, str], ...] = (
    ("for (int i = 0; i < mjNDISABLE; ++i)", "mjDISABLESTRING[i]", "kDisableTip"),
    ("for (int i = 0; i < mjNENABLE; ++i)",  "mjENABLESTRING[i]",  "kEnableTip"),
    ("for (int i = 0; i < mjNVISFLAG; ++i)", "mjVISSTRING[i][0]",  "kVisTip"),
    ("for (int i = 0; i < mjNRNDFLAG; ++i)", "mjRNDSTRING[i][0]",  "kRndTip"),
)

#: 生成 C++ 数组时用的类型 —— `const char*` 而非 `std::string`：
#: 全是编译期常量，且 SetItemTooltip 收 const char*。
ARRAY_TMPL = """// ══ mujoco-zh 开关悬停说明（由 scripts/gen_cpp_patch.py 生成，勿手改）══
// 数据源：data/tooltips_flags.json（{n} 条）
namespace {{
{tables}
}}  // namespace
// ══ end mujoco-zh ══
"""

TABLE_TMPL = """constexpr const char* {name}[] = {{
{items}
}};
// ⚠️ 这条 static_assert **不能省** —— 数组声明成 [] 由初始化列表定长，
//    MuJoCo 升级后枚举变长而这边没跟上就会**编译失败**（响亮地失败）。
//    若改成显式定长 {name}[{count}]，则会**静默错位**（下标合法但挂错开关）。
static_assert(sizeof({name}) / sizeof({name}[0]) == {count},
              "{name} 与 {enum} 不同步 —— 需更新 data/tooltips_flags.json");
"""


def load_tooltips(path: Path) -> dict[str, dict]:
    """读 tooltips JSON 并**逐条校验**，返回 {表名: {index: tip}}。

    ⚠️ 校验不是可选项 —— 开发期间这份数据连错过 3 次
    （字段名 prov/der、MultiCCD 放错表、key 前缀），**每次都是校验抓出来的**。
    """
    if not path.is_file():
        return {}
    d = json.loads(path.read_text(encoding="utf-8"))

    tables: dict[str, dict[int, str]] = {}
    expect = {k: v["count"] for k, v in d["tables"].items()}
    for e in d["entries"]:
        t, i = e["table"], e["index"]
        if t not in expect:
            raise SystemExit(f"❌ tooltip: 未知表 {t!r}（{e['key']}）")
        if not e.get("tip"):
            continue                       # 故意留空
        if not e.get("provenance"):
            raise SystemExit(f"❌ tooltip: {e['key']} 有 tip 但缺 provenance")
        if "##" in e["tip"]:
            # ⚠️ tooltip 是**显示文本**，`##` 之后的内容会被 ImGui 吃掉
            raise SystemExit(f"❌ tooltip: {e['key']} 的 tip 含 `##` —— 会被 ImGui 当 ID 吃掉")
        if t in tables and i in tables[t]:
            raise SystemExit(f"❌ tooltip: {t}[{i}] 重复（{e['key']}）")
        tables.setdefault(t, {})[i] = e["tip"]

    # index 必须连续 0..n-1 —— 缺一个就会错位
    for t, cnt in expect.items():
        got = sorted(tables.get(t, {}))
        if got != list(range(cnt)):
            raise SystemExit(
                f"❌ tooltip: {t} 的 index 不是连续 0..{cnt-1}\n"
                f"   实际: {got}\n"
                f"   （缺 index 会导致 C++ 数组长度对不上，static_assert 会报错）")
    return tables


def _cpp_str(s: str) -> str:
    """转成 C++ 字符串字面量内容（转义 \\ 和 "，换行写 \\n）。"""
    return (s.replace("\\", "\\\\").replace('"', '\\"')
             .replace("\n", "\\n"))


def _render_tables(tables: dict[str, dict[int, str]]) -> str:
    """渲染成 C++ 数组块。"""
    # 表名 → (官方枚举常量名, 枚举类型名)（static_assert 用）
    enum_of = {
        "kDisableTip": ("mjNDISABLE", "mjtDisableBit"),
        "kEnableTip":  ("mjNENABLE",  "mjtEnableBit"),
        "kVisTip":     ("mjNVISFLAG", "mjtVisFlag"),
        "kRndTip":     ("mjNRNDFLAG", "mjtRndFlag"),
    }
    blocks = []
    for name in ("kDisableTip", "kEnableTip", "kVisTip", "kRndTip"):
        if name not in tables:
            continue
        tips = tables[name]
        cnt, enum = enum_of[name]
        items = "\n".join(f'    /*{i:>2}*/ "{_cpp_str(tips[i])}",' for i in sorted(tips))
        blocks.append(TABLE_TMPL.format(name=name, items=items, count=cnt, enum=enum))
    return ARRAY_TMPL.format(n=sum(len(v) for v in tables.values()),
                             tables="".join(blocks))


def _inject_tooltips(lines: list[str], tables: dict[str, dict[int, str]]
                     ) -> tuple[list[str], list[tuple]]:
    """把 C++ 数组块 + SetItemTooltip 调用插进 gui.cc 的行列表。"""
    src = "".join(lines)

    # ① 数组块 —— 插在 PhysicsGui 之前
    if INJECT_ANCHOR not in src:
        raise SystemExit(
            f"❌ tooltip: 找不到注入锚点\n   {INJECT_ANCHOR}\n"
            f"   ⚠️ 宁可报错也不要静默跳过 —— 静默跳过会编出「看着成功、\n"
            f"   实际一个 tooltip 都没加」的 .so（本项目踩过同类坑）")
    block = _render_tables(tables)
    src = src.replace(INJECT_ANCHOR, block + INJECT_ANCHOR, 1)

    # ② 调用点 —— 紧跟在 toggle 调用之后
    #    ⚠️ tooltip 附着的是「上一个 item」，中间不能插任何 ImGui 调用
    #
    #    ⚠️ 两种 toggle 函数、两种标签形态（都实测过）：
    #      Physics  : ImGui_BitToggle(mjDISABLESTRING[i], ...)     一维
    #      Rendering: ImGui_ButtonToggle(mjVISSTRING[i][0], ...)  二维
    #    ⇒ 正则必须同时吃下 `ImGui_(Bit|Button)Toggle` 和 `[i]` / `[i][0]`
    inserted = []
    for loop, label_expr, table in CALL_SITES:
        if table not in tables:
            continue
        if loop not in src:
            raise SystemExit(f"❌ tooltip: 找不到调用点循环\n   {loop}")
        # 从该循环头开始，抓到最近的 toggle 调用行
        pat = re.compile(
            re.escape(loop) +
            r"(?P<body>(?:[^\n]*\n)*?"
            r"(?P<indent> *)(?:if \(.*?\) )?"
            r"ImGui_(?:Bit|Button)Toggle\([^;]*?\);\n)")
        m = pat.search(src)
        if not m:
            raise SystemExit(f"❌ tooltip: 循环 `{loop}` 后找不到 toggle 调用")
        seg = m.group("body")
        if label_expr not in seg:
            raise SystemExit(
                f"❌ tooltip: 循环 `{loop}` 里的 toggle 调用不含 `{label_expr}`\n"
                f"   实际是: {seg.strip().splitlines()[-1].strip()[:90]}")
        indent = m.group("indent")
        call = (f'{indent}// mujoco-zh: 悬停说明（生成物，勿手改）\n'
                f'{indent}if ({table}[i]) '
                f'ImGui::SetItemTooltip("%s", {table}[i]);\n')
        src = src[:m.end("body")] + call + src[m.end("body"):]
        inserted.append((table, label_expr))

    return src.splitlines(keepends=True), [
        (f"tooltip:{t}", "C++ 悬停说明", GUI_CC, 1) for t, _ in inserted
    ]


# ══════════════════════════════════════════════════════════════════
# Elements（属性）面板 —— 属性名 + 悬停说明 + 悬停挂载点
# ══════════════════════════════════════════════════════════════════
#
# 与上面 68 个开关的 tooltip 是**两套机制**，别搞混：
#   * 开关的 tooltip —— ux.so 里**没有**那些字符串（枚举来自 libmujoco.so）
#     ⇒ 只能**新增** C++ 数组 + 调用点
#   * Elements 的 tooltip —— 字符串**已经在** gui_spec.cc 里（官方自带英文）
#     ⇒ 是**定点替换**，而且**绝不能加 `##`**（tooltip 是显示文本）
#
# 属性名走的是第三条路：`#NAME` 是**裸标识符**（不是字面量），
# 通用替换够不着 ⇒ 改成「宏里套一个查表函数」，296 个调用点一个都不用动。

GUI_SPEC = "src/experimental/platform/ux/gui_spec.cc"
IMGUI_CC = "src/experimental/platform/ux/imgui_widgets.cc"
IMGUI_H = "src/experimental/platform/ux/imgui_widgets.h"
GUI_CC_GUI = "src/experimental/platform/ux/gui.cc"

#: 复用 extract_spec_fields 的字符扫描器（tooltip 字面量含分号/逗号，正则不可靠）
from extract_spec_fields import _scan, _split_args   # noqa: E402

#: 属性名查表函数 + 表的注入锚点（必须早于 ElementSpecGui 里的宏定义）
FIELD_ANCHOR = "void ElementSpecGui(mjsElement* element, SpecEditor* editor) {"

#: 宏体替换：把 `#NAME` 换成查表结果。⚠️ QFIELD 里 **`#ALT` 绝不能碰** ——
#: 它是 `alt.xyaxes` 这类派生标签的前缀（imgui_widgets.cc:307）。
FIELD_MACROS: tuple[tuple[str, str], ...] = (
    ("table(#NAME, elem->NAME, ref->NAME, TIP);",
     "table(SpecFieldLabel(#NAME), elem->NAME, ref->NAME, TIP);"),
    ("table(#NAME, #ALT, elem->NAME, ref->NAME, elem->ALT, ref->ALT, TIP);",
     "table(SpecFieldLabel(#NAME), #ALT, elem->NAME, ref->NAME, elem->ALT, ref->ALT, TIP);"),
)

FIELD_BLOCK_TMPL = """// ══ mujoco-zh 属性名（由 scripts/gen_cpp_patch.py 生成，勿手改）══
// 数据源：data/tooltips_fields.json 的 labels（{n} 条）
// 用法：FIELD/QFIELD 宏把 `#NAME` 包成 `SpecFieldLabel(#NAME)`。
// ⚠️ 返回的是 `中文 (字段名)` 这种**纯显示文本** —— 它进的是 ImGui::Text，
//    而 Text **不剥 `##`**（imgui_widgets.cpp:190），所以这里绝不能有 `##`。
namespace {{
struct ZhFieldLabel {{ const char* field; const char* label; }};
constexpr ZhFieldLabel kZhFieldLabels[] = {{
{items}
}};
// 这条 static_assert 不能省：数组长度与数据文件脱节时要**编译失败**，而不是静默回退英文。
static_assert(sizeof(kZhFieldLabels) / sizeof(kZhFieldLabels[0]) == {n},
              "kZhFieldLabels 与 data/tooltips_fields.json 不同步");

const char* SpecFieldLabel(const char* name) {{
  const std::string_view key(name);
  for (const ZhFieldLabel& e : kZhFieldLabels) {{
    if (key == e.field) return e.label;
  }}
  return name;  // 查不到就原样返回 —— 生成器离线已断言 296 个字段名全部命中
}}
}}  // namespace
// ══ end mujoco-zh ══
"""

HOVER_EMIT = 'ImGui::SetItemTooltip("%s", tooltip);'


def load_field_tooltips(path: Path) -> dict:
    """读 data/tooltips_fields.json 并**逐条校验**。

    ⚠️ 校验不是可选项 —— 这一份数据踩过的坑：
      * 官方原句**凭印象写**（`frame orientation` 那条），机器核验才抓出来
      * 同一个英文原文在不同对象类型下含义不同（`geom type` / `mass`）
        ⇒ 必须支持 `scope`，且 scope 指向的 mjOBJ_* 必须真实存在
    """
    if not path.is_file():
        return {}
    d = json.loads(path.read_text(encoding="utf-8"))

    labels: dict[str, str] = {}
    for l in d.get("labels", []):
        if not l.get("zh"):
            raise SystemExit(f"❌ 属性名 {l['field']!r} 还没写 zh")
        if "##" in l["zh"]:
            raise SystemExit(
                f"❌ 属性名 {l['field']!r} 的 zh 含 `##` —— 左列走 ImGui::Text，"
                f"`##` 会**原样显示**出来（imgui_widgets.cpp:190）")
        if not l.get("provenance"):
            raise SystemExit(f"❌ 属性名 {l['field']!r} 缺 provenance")
        labels[l["field"]] = l["zh"]

    plain: dict[str, str] = {}                 # en → zh（不分对象类型）
    scoped: dict[tuple[str, str], str] = {}    # (en, mjOBJ_X) → zh
    for e in d.get("entries", []):
        if not e.get("zh"):
            raise SystemExit(f"❌ tooltip {e['en']!r} 还没写 zh")
        if "##" in e["zh"]:
            raise SystemExit(
                f"❌ tooltip {e['en']!r} 的 zh 含 `##` —— tooltip 是**显示文本**，"
                f"`##` 会被 ImGui 当 ID 吃掉（本项目 251bdc6 修过一次）")
        if e.get("scope"):
            key = (e["en"], e["scope"])
            if key in scoped:
                raise SystemExit(f"❌ tooltip {key} 重复")
            scoped[key] = e["zh"]
        else:
            if e["en"] in plain:
                raise SystemExit(f"❌ tooltip {e['en']!r} 重复（且都没 scope）")
            plain[e["en"]] = e["zh"]

    types = {c["elemtype"] for c in d["calls"]} if "calls" in d else set()
    for en, sc in scoped:
        if types and sc not in types:
            raise SystemExit(f"❌ tooltip {en!r} 的 scope={sc!r} 不是真实的 mjOBJ_*")

    def resolve(en: str, elemtype: str) -> str | None:
        return scoped.get((en, elemtype)) or plain.get(en)

    return {"labels": labels, "plain": plain, "scoped": scoped,
            "resolve": resolve,
            "n_labels": len(labels), "n_entries": len(plain) + len(scoped)}


def spec_calls(src_dir: Path) -> list[dict]:
    """**实时**从 gui_spec.cc 抽取 296 个 FIELD/QFIELD 调用点。

    ⚠️ 不从 data/tooltips_fields.json 里读 `calls` —— 那份是**派生数据**，
    源码一改就会过期。本项目吃过这个亏：`ux_strings_audit.json` 的
    `by_panel` 就是「只扫过一部分时期的产物」，后来一直没人发现它早就不准了。
    派生的东西，每次现算。
    """
    sys.path.insert(0, str(HERE))
    from extract_spec_fields import extract                    # noqa: PLC0415
    return extract(src_dir / GUI_SPEC)


def _render_field_labels(labels: dict[str, str]) -> str:
    items = "\n".join(
        f'    {{"{_cpp_str(f)}", "{_cpp_str(labels[f])} ({_cpp_str(f)})"}},'
        for f in sorted(labels))
    return FIELD_BLOCK_TMPL.format(n=len(labels), items=items)


def _field_zh(fields: dict, call: dict) -> str:
    """一个 FIELD/QFIELD 调用点最终要显示的 tooltip 全文。"""
    field = call["field"]
    zh = fields["labels"].get(field)
    if zh is None:
        raise SystemExit(
            f"❌ gui_spec.cc:{call['line']} 的字段名 {field!r} 不在 labels 里 —— "
            f"加进 data/tooltips_fields.json（否则左列会静默回退成英文）")
    body = fields["resolve"](call["tip_en"], call["elemtype"])
    if body is None:
        raise SystemExit(
            f"❌ gui_spec.cc:{call['line']} 的 tooltip {call['tip_en']!r} "
            f"（{call['elemtype']}）解析不到译文 —— 在 entries 里补一条"
            + (f"，或加 scope={call['elemtype']!r}" if call["tip_en"] in fields["plain"] else ""))
    # 首行由**调用点的字段名**拼，不从数据文件取 —— 这样同一英文原文
    # 挂在不同字段名上也不会串（`name of geom 1` 曾同时挂在 bodyname1/geomname1）
    return f"{zh} ({field})\n\n{body}"


def _replace_field_tooltips(text: str, fields: dict,
                            calls: list[dict]) -> tuple[str, list]:
    """阶段 ②：按 **tip 字面量的绝对偏移**做定点替换（不加 `##`）。"""
    if not fields:
        return text, []
    todo = []
    for c in calls:
        lit = '"%s"' % text[c["tip_start"] + 1:c["tip_end"] - 1]
        if lit[1:-1] != c["tip_en"]:
            raise SystemExit(
                f"❌ gui_spec.cc:{c['line']} 的字面量偏移对不上："
                f"期望 {c['tip_en']!r}，实际 {lit[1:-1]!r}\n"
                f"   （源码改动过？重跑 scripts/extract_spec_fields.py 更新偏移量）")
        todo.append((c["tip_start"], c["tip_end"],
                     '"%s"' % _cpp_str(_field_zh(fields, c))))
    # 从后往前替换，前面的偏移量才不受影响
    for start, end, new in sorted(todo, reverse=True):
        text = text[:start] + new + text[end:]
    return text, [(c["elemtype"], c["field"], c["tip_en"]) for c in calls]


def _inject_field_labels(text: str, fields: dict) -> tuple[str, int]:
    """阶段 ①：注入属性名查表块 + 改宏定义（**不动任何调用点**）。"""
    if not fields:
        return text, 0
    if FIELD_ANCHOR not in text:
        raise SystemExit(f"❌ 找不到属性名注入锚点\n   {FIELD_ANCHOR}")
    text = text.replace(FIELD_ANCHOR,
                        _render_field_labels(fields["labels"]) + FIELD_ANCHOR, 1)
    n = 0
    for old, new in FIELD_MACROS:
        if old not in text:
            raise SystemExit(f"❌ 找不到宏体\n   {old}")
        text = text.replace(old, new, 1)
        n += 1
    return text, n


def _brace_blocks(text: str) -> list[tuple[int, int]]:
    """返回所有 `{...}` 块的 (开, 闭) 下标。跳过字符串/字符/注释。"""
    stack: list[int] = []
    blocks: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "'":
            i += 1
            while i < n and text[i] != "'":
                i += 2 if text[i] == "\\" else 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 1
        elif c == "{":
            stack.append(i)
        elif c == "}":
            if stack:
                blocks.append((stack.pop(), i))
        i += 1
    return blocks


def _inject_hover_targets(text: str) -> tuple[str, int]:
    """阶段 ③：把悬停说明**也**挂到输入框上。

    ⚠️⚠️ 官方 bug：`Label()` 在 `Input()` **之前**调 `SetItemTooltip`，
    于是 tooltip 挂在 `ImGui::Text` 的标签上而不是输入框上。两个后果：
      ① 鼠标移到数值框上**没有**提示
      ② `ImGui::Text` 的 item ID 恒为 0（imgui_widgets.cpp:187 `ItemAdd(bb, 0)`），
         而 `IsItemHovered` 有 `g.ActiveId != 0 && g.ActiveId != id → return false`
         ⇒ **只要有任何一个输入框在编辑态，所有标签 tooltip 全被抑制**

    修法：在**标签所在的最近一层大括号块**的最后一条语句之后，补一次
    `SetItemTooltip`。一条规则覆盖三种形态：
      * 直线型函数体 → 插在函数体末尾（等价于 `Input()` 之后）
      * `for` 循环体  → 插在循环体内末尾（每个 `[i]` 都有）
      * `if/else` 之后 → 插在函数体末尾（`char[N]` / `vector<int>` 那两处）
    """
    blocks = _brace_blocks(text)
    hits = [m.start() for m in re.finditer(r"\bLabel\(\s*label\s*,", text)]
    if not hits:
        raise SystemExit("❌ imgui_widgets 里找不到 `Label(label, …)` 调用点")
    spots: list[int] = []
    for p in hits:
        encl = [b for b in blocks if b[0] < p < b[1]]
        if not encl:
            raise SystemExit(f"❌ 偏移 {p} 处的 Label 调用不在任何大括号块内")
        _, close = min(encl, key=lambda b: b[1] - b[0])   # 最小 = 最内层
        # 插到 `}` 所在行的行首之前，**缩进跟随块内最后一条语句**
        line_start = text.rfind("\n", 0, close) + 1
        if text[line_start:close].strip():
            raise SystemExit(f"❌ 偏移 {close} 的 `}}` 前面有非空白内容，无法安全插入")
        prev = text[:line_start].rstrip("\n")
        pstart = prev.rfind("\n") + 1
        indent = re.match(r"[ \t]*", text[pstart:]).group(0)
        if not indent:
            indent = re.match(r"[ \t]*", text[text.rfind("\n", 0, pstart) + 1:]).group(0)
        spots.append((line_start, indent))
    for pos, indent in sorted(set(spots), reverse=True):
        text = text[:pos] + f"{indent}{HOVER_EMIT}\n" + text[pos:]
    return text, len(set(spots))


# ══════════════════════════════════════════════════════════════════
# 非 Elements 面板的控件悬停说明
# ══════════════════════════════════════════════════════════════════
#
# 这一片**完全不走** FIELD/QFIELD。它有三种挂载方式，各改各的：
#
#   helper（47 处）ImGui_Input / ImGui_InputN / ImGui_SwitchToggle
#       这些辅助函数**根本没有 tooltip 形参** ⇒ 加一个**尾随默认参数**，
#       调用点只在需要时补第 4 个实参。
#
#       ⚠️⚠️ tooltip **绝不能塞进 ImGuiOpts<T>** ——
#       `gui.cc` 里有 12 处用**位置初始化** `{0, 1, 0.01, 0.1}`，
#       往结构体里插任何字段都会让它们**静默错位**。（同 `static_assert`
#       防的那类错误：不报错，只是挂错东西。）
#
#   direct（27 处）ImGui::ColorEdit3/4 / SliderInt / SliderFloat / InputInt
#       直调 ImGui，只能在**语句之后**插一行 `SetItemTooltip`。
#
#   loop（2 处）  `Act Group %d` / GroupGui 的 `%s %d`
#       68 条开关那批只覆盖了 4 个 CALL_SITES 循环，这两个漏了。
#       ⚠️ 这两个 label 都是 `snprintf` 拼出来的 ⇒ **不能加 `##`**。
#
# ⚠️ 为什么 tooltip 正文里不能出现 `##`：`ImGui::Text` 不剥 `##`
#    （imgui_widgets.cpp:190），会原样画出来。

#: 辅助函数 → 要加的尾随形参个数（即「已有实参个数」）
#:
#: ⚠️⚠️ 这里数的是**不含 tooltip 的形参个数**。踩过：
#:    `ImGui_SwitchToggle` 的第 3 个形参是 `const ImVec2& size`，
#:    我写成 2 就会把 tooltip **填进 size 的位置** ⇒ 编译报
#:    `no known conversion from 'const char[227]' to 'const ImVec2'`。
#:    而 build_ux_zh.sh 当时**没报错**（见下面的注释），于是装了个残缺的 .so。
HELPER_ARITY: dict[str, int] = {
    "ImGui_Input": 3,         # name, value, opts
    "ImGui_InputN": 4,        # name, value, num, opts
    "ImGui_SwitchToggle": 3,  # label, boolean, size ← ⚠️ 别漏了 size
}

#: helper 辅助函数的**定义**所在文件与签名锚点
HELPER_DEFS: tuple[tuple[str, str, str], ...] = (
    # (相对路径, 原签名片段, 新签名片段)
    (IMGUI_H,
     "bool ImGui_InputN(const char* name, T* value, int num, ImGuiOpts<T> opts = {}) {",
     "bool ImGui_InputN(const char* name, T* value, int num, ImGuiOpts<T> opts = {},\n"
     "                  const char* tooltip = nullptr) {"),
    (IMGUI_H,
     "bool ImGui_Input(const char* name, T* value, ImGuiOpts<T> opts = {}) {\n"
     "  return ImGui_InputN(name, value, 1, opts);\n"
     "}",
     "bool ImGui_Input(const char* name, T* value, ImGuiOpts<T> opts = {},\n"
     "                 const char* tooltip = nullptr) {\n"
     "  return ImGui_InputN(name, value, 1, opts, tooltip);\n"
     "}"),
    (IMGUI_H,
     "bool ImGui_SwitchToggle(const char* label, T* boolean,\n"
     "                        const ImVec2& size = ImVec2(0, 0)) {",
     "bool ImGui_SwitchToggle(const char* label, T* boolean,\n"
     "                        const ImVec2& size = ImVec2(0, 0),\n"
     "                        const char* tooltip = nullptr) {"),
)

#: helper 函数体内「插 SetItemTooltip」的位置：紧跟在锚点这一行之后
#:
#: ⚠️⚠️ `ImGui_InputN` 的 `ImGui::InputScalarN` 在 **`if constexpr` 的三个分支里各有一份**
#:    （int / float / double）。插在 double 那个分支后，int 和 float 就**永远拿不到** tooltip。
#:    ⇒ 必须插在 **`if constexpr` 整个语句之后**，也就是 `} else {` 那个 `}` 后面。
HELPER_EMIT_AFTER: tuple[tuple[str, str], ...] = (
    # (相对路径, 锚点子串 —— 必须**唯一**)
    # ⚠️ `static_assert(...)` 在文件里出现 **2 次**（另一次在 :125 的 stoi 辅助函数里）
    #    ⇒ 锚点必须带上前面几行才能唯一。
    (IMGUI_H,
     "    res = ImGui::InputScalarN(name, ImGuiDataType_Double, value, num, pstep,\n"
     "                              pstep_fast, format);\n"
     "  } else {\n"
     "    static_assert(dependent_false<T>::value, \"Unsupported type\");\n"
     "  }"),
    (IMGUI_H, "  const bool changed = ImGui::SliderInt(label, &i, 0, 1, label, flags);"),
)

HELPER_EMIT = ('  if (tooltip) ImGui::SetItemTooltip("%s", tooltip);')

#: 循环型（`Act Group %d` / GroupGui 的 `%s %d`）：label 是 snprintf 拼出来的，
#: 用它去查一张「拼好的完整标签 → 说明」的表。
#: ⚠️ `label` 是这两个循环里现成的 `char[64]` 缓冲，直接用。
HOVER_EMIT_LOOP = ('if (const char* _t = ZhLoopTip(label)) '
                   'ImGui::SetItemTooltip("%s", _t);')

LOOP_TIP_TMPL = """// ══ mujoco-zh 循环型控件悬停说明（由 scripts/gen_cpp_patch.py 生成，勿手改）══
// 数据源：data/tooltips_controls.json 的 loop 类条目（{n} 条）
// 用法：`Act Group %d` / GroupGui 的 `%s %d` 是 snprintf 拼出来的完整标签，
//       这里按**拼好的完整字符串**查表。
// ⚠️ 返回的是纯显示文本，**绝不能含 `##`**（ImGui::Text 不剥 `##`）。
namespace {{
struct ZhLoopTipEntry {{ const char* label; const char* tip; }};
constexpr ZhLoopTipEntry kZhLoopTips[] = {{
{items}
}};

inline const char* ZhLoopTip(const char* label) {{
  const std::string_view key(label);
  for (const ZhLoopTipEntry& e : kZhLoopTips) {{
    if (key == e.label) return e.tip;
  }}
  return nullptr;
}}
}}  // namespace
// ══ end mujoco-zh ══
"""

#: 循环型查表块的注入锚点 —— 必须早于所有用到的函数
LOOP_TIP_ANCHOR = "void PhysicsGui(mjModel* model, float min_width) {"


def load_control_tooltips(path: Path) -> dict:
    """读 data/tooltips_controls.json 并逐条校验。"""
    if not path.is_file():
        return {}
    d = json.loads(path.read_text(encoding="utf-8"))
    table: dict[tuple[str, str], str] = {}
    for e in d.get("entries", []):
        if not e.get("zh"):
            raise SystemExit(
                f"❌ 控件 tooltip [{e.get('panel')}] {e.get('label_en')!r} 还没写 zh")
        if "##" in e["zh"]:
            raise SystemExit(
                f"❌ 控件 tooltip {e['label_en']!r} 的 zh 含 `##` —— tooltip 是显示文本，"
                f"`##` 会被 ImGui 当 ID 吃掉")
        if "**" in e["zh"]:
            raise SystemExit(
                f"❌ 控件 tooltip {e['label_en']!r} 的 zh 含 markdown `**` —— "
                f"ImGui 按**纯文本**渲染，会原样画出星号")
        key = (e["panel"], e["label_en"])
        if key in table:
            raise SystemExit(f"❌ 控件 tooltip {key} 重复")
        table[key] = e["zh"]
    return {"table": table, "n": len(table)}


def _control_calls(src_dir: Path) -> list[dict]:
    """实时从 gui.cc 抽取控件调用点（派生数据不落盘）。"""
    sys.path.insert(0, str(HERE))
    from extract_controls import extract as extract_controls   # noqa: PLC0415
    return extract_controls(src_dir / GUI_CC_GUI)


def _find_stmt_end(text: str, start: int) -> int:
    """从 `start` 往后找该语句的 `;`，返回其后（含换行）的插入位置。

    ⚠️ 必须跳过字符串/注释里的分号，否则 `ImGui::ColorEdit4("a;b", x);`
    会在字符串内部的分号处截断。
    """
    i, n, depth = start, len(text), 0
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == ";" and depth == 0:
            j = text.find("\n", i)
            return (j + 1) if j >= 0 else n
        i += 1
    raise SystemExit(f"❌ 从偏移 {start} 起找不到语句结束的 `;`")


def _inject_helper_params(text: str) -> tuple[str, int]:
    """给辅助函数加尾随 tooltip 形参（改**定义**，不是调用点）。"""
    n = 0
    for _rel, old, new in HELPER_DEFS:
        if old not in text:
            raise SystemExit(f"❌ 找不到辅助函数定义：\n   {old[:100]}")
        text = text.replace(old, new, 1)
        n += 1
    for _rel, anchor in HELPER_EMIT_AFTER:
        if text.count(anchor) != 1:
            raise SystemExit(
                f"❌ 锚点不唯一（出现 {text.count(anchor)} 次）：\n   {anchor[:100]}")
        indent = re.match(r"[ \t]*", anchor.split("\n")[0]).group(0)
        text = text.replace(anchor, anchor + f"\n{indent}{HELPER_EMIT}", 1)
    return text, n


def _inject_control_tooltips(text: str, controls: dict,
                             calls: list[dict]) -> tuple[str, int]:
    """给 gui.cc 里的控件补 tooltip 实参 / 插 SetItemTooltip。

    ⚠️⚠️ **不能用行号定位后再逐个插入** —— 每插一次文本就变长，
    后面所有行号全部失效（踩过：37 个 `ImGui_Input` 里只有一半被改到，
    另一半静默漏掉，因为它们排在前面、行号被后面的插入推偏了）。
    ⇒ 一次性算出**所有**插入点（绝对偏移），再从后往前统一插入。
    """
    if not controls or not calls:
        return text, 0
    table = controls["table"]
    edits: list[tuple[int, str]] = []          # (插入位置, 插入内容)

    for c in calls:
        key = (c["panel"], c["label_en"])
        zh = table.get(key)
        if zh is None:
            raise SystemExit(
                f"❌ gui.cc:{c['line']} 的控件 {c['label_en']!r}（{c['panel']}）"
                f"解析不到译文 —— 在 data/tooltips_controls.json 里补一条")
        lit = '"%s"' % _cpp_str(zh)
        off = _offset_of_line(text, c["line"])
        lpar = text.index("(", off)
        close = _scan(text, lpar)
        # 语句**本身的**缩进（不是加 4）—— 直调型要插在语句后面、与语句对齐
        ind = re.match(r"[ \t]*", text[off:]).group(0)
        # 续行缩进：多行调用时实参对齐用
        ind_arg = ind + "    "

        if c["kind"] == "helper":
            have = len(_split_args(text[lpar + 1:close]))
            want = HELPER_ARITY.get(c["fn"])
            if want is None:
                raise SystemExit(f"❌ 未知辅助函数 {c['fn']!r}")
            # ⚠️⚠️ 必须**数清已有实参个数**再补 —— tooltip 是最后一个形参，
            #    不是「第三个」。踩过：`ImGui_Input(name, &v)` 只有 2 个实参，
            #    直接把 tooltip 接在后面会**填进 opts 的位置**（能编译，静默错位）。
            #    ⚠️ 也踩过一次**公式写错**：写成 `want - 1 - have`，
            #    于是 `have=2, want=3` 算出 fill=0 → 没补 `{}` → 同上错位。
            #    `want` 是「含 opts 在内的形参个数」，所以缺几个就是 `want - have`。
            fill = max(0, want - have)
            add = ", ".join(["{}"] * fill + [lit])
            body_len = close - lpar
            sep = ("\n" + ind_arg) if body_len + len(add) > 72 else (" " if fill else "")
            edits.append((close, f",{sep}{add}"))
        elif c["kind"] == "loop":
            # `Act Group %d` / GroupGui 的 `%s %d` —— 在**循环体内**的 toggle
            # 调用之后插一行，用 `snprintf` 出来的 label 查表。
            # ⚠️ 这两个 label 是拼出来的 ⇒ 绝不能加 `##`。
            # ⚠️ 锚点要用**循环头**的位置：抽取器给的 `line` 指向 `snprintf`
            #    那一行，而 `snprintf` 在循环体**内部** —— 得往前找 `for (`。
            head = text.rfind("for (", 0, off)
            if head < 0:
                raise SystemExit(
                    f"❌ gui.cc:{c['line']} 的控件 {c['label_en']!r} 前面找不到 `for (`")
            body = _loop_body_after(text, head)
            if body is None:
                raise SystemExit(
                    f"❌ gui.cc:{c['line']} 的循环里找不到 toggle 调用（{c['label_en']!r}）")
            edits.append((body, f'{ind}// mujoco-zh: 悬停说明（生成物，勿手改）\n'
                               f'{ind}{HOVER_EMIT_LOOP}\n'))
        elif c["kind"] == "direct":
            end = _find_stmt_end(text, off)
            edits.append((end,
                          f'{ind}// mujoco-zh: 悬停说明（生成物，勿手改）\n'
                          f'{ind}ImGui::SetItemTooltip("%s", {lit});\n'))
        else:
            raise SystemExit(f"❌ 未知 kind {c['kind']!r}")

    for pos, ins in sorted(edits, key=lambda e: -e[0]):
        text = text[:pos] + ins + text[pos:]
    return text, len(edits)


def _expand_loop_labels(label_en: str, panel: str) -> list[str]:
    """把循环标签模板展开成**具体标签**。

    `Act Group %d` → `Act Group 0` … `Act Group 5`（disableactuator 有 6 组）
    `%s %d`        → 7 组 × 6 = 42 个（`Geoms 0` … `Sites 5`）

    ⚠️ 这两个数字**不是猜的**：
      * `Act Group` 的循环头是 `for (int i = 0; i < 6; ++i)`（gui.cc:1089）
      * GroupGui 的调用点传 7 个组名，内层同样 `i < 6`（gui.cc:1258）
    """
    if label_en == "Act Group %d":
        return [f"Act Group {i}" for i in range(6)]
    if label_en == "%s %d":
        groups = ["Geoms", "Sites", "Joints", "Tendons", "Actuators", "Flexes",
                  "Skins"]
        return [f"{g} {i}" for g in groups for i in range(6)]
    return [label_en]


def _render_loop_tips(controls: dict, calls: list[dict]) -> tuple[str, int]:
    """渲染循环型查表块（表项来自 `data/tooltips_controls.json` 的 loop 条目）。"""
    table = controls["table"]
    rows = []
    for c in calls:
        if c["kind"] != "loop":
            continue
        zh = table.get((c["panel"], c["label_en"]))
        if zh is None:
            raise SystemExit(f"❌ 循环型控件 {c['label_en']!r} 解析不到译文")
        # tooltip 首行用展开后的**具体标签**
        for lab in _expand_loop_labels(c["label_en"], c["panel"]):
            body = zh.split("\n", 2)[-1] if zh.count("\n") >= 2 else zh
            rows.append((lab, f"{lab}\n\n{body}"))
    if not rows:
        return "", 0
    items = "\n".join(
        f'    {{"{_cpp_str(lab)}", "{_cpp_str(tip)}"}},'
        for lab, tip in rows)
    return LOOP_TIP_TMPL.format(n=len(rows), items=items), len(rows)


def _inject_loop_tips(text: str, controls: dict | None, calls: list[dict]
                      ) -> tuple[str, int]:
    """把循环型查表块插进 gui.cc（必须早于 PhysicsGui）。"""
    if not controls:
        return text, 0
    block, n = _render_loop_tips(controls, calls)
    if not block:
        return text, 0
    if LOOP_TIP_ANCHOR not in text:
        raise SystemExit(f"❌ 找不到循环型查表块的注入锚点\n   {LOOP_TIP_ANCHOR}")
    return text.replace(LOOP_TIP_ANCHOR, block + LOOP_TIP_ANCHOR, 1), n


def _loop_body_after(text: str, anchor: int) -> int | None:
    """从**循环头**位置往后，找该 `for` 的循环体，返回「体内最后一条 toggle 之后」的插入点。

    ⚠️ `anchor` 必须是**循环头**（`for (int i = 0; i < 6; ++i)` 里的 `%d` 或 `%s`），
    不能是 `snprintf` 的右括号 —— 那后面第一个 `{` 会是别的块（踩过：
    `BeginTable` 的 `if` 块抢先匹配，于是判定「找不到 toggle」）。

    实现：从 `anchor` 往后**跳过第一个 `(...)`**（循环条件），再找紧跟的 `{`。
    """
    p = text.find("(", anchor)
    if p < 0:
        return None
    depth, i, n = 0, p, len(text)
    while i < n:                       # 跳过 `(...)`：循环条件
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    brace = text.find("{", i)
    if brace < 0:
        return None
    depth, i, end = 0, brace, -1
    while i < n:                       # 找循环体的配对 `}`
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
        i += 1
    if end < 0:
        return None
    last = None
    for m in re.finditer(r"ImGui_(?:Bit|Button)Toggle\([^;]*?\);", text[brace:end]):
        last = m
    if last is None:
        return None
    j = text.find("\n", brace + last.end())
    return (j + 1) if j >= 0 else None


def _offset_of_line(text: str, line: int) -> int:
    """第 `line` 行（1 起）的行首偏移。"""
    pos, cur = 0, 1
    while cur < line:
        nxt = text.find("\n", pos)
        if nxt < 0:
            raise SystemExit(f"❌ 找不到第 {line} 行")
        pos, cur = nxt + 1, cur + 1
    return pos


def build_patch(translations: dict[str, str], src_dir: Path,
                tooltips: dict[str, dict] | None = None,
                fields: dict | None = None,
                controls: dict | None = None) -> tuple[str, dict]:
    """在源码副本上做替换，返回 (unified diff, 统计)。

    支持**多文件** —— 界面文字散在 gui.cc / gui_spec.cc / sim_profiler.cc，
    只改一个会漏（实测：Profiler 面板整块仍是英文）。
    """
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    todo = [en for en in audit["translatable"] if en not in NO_TRANSLATE]

    diffs: list[str] = []
    applied, skipped, missing = [], [], []
    touched_files: list[str] = []
    nowrapped: list[tuple[str, str, str]] = []   # 未加 ## 的，供报告

    # 296 个调用点只抽一次（两个阶段都要用）
    spec = spec_calls(src_dir) if fields else []
    # 控件调用点同理
    ctl = _control_calls(src_dir) if controls else []

    for rel in TARGET_FILES:
        target = src_dir / rel
        if not target.is_file():
            raise SystemExit(
                f"❌ 找不到源码: {target}\n"
                f"   （先跑 scripts/build_ux_zh.sh 拉源码，或用 --src 指定）")

        original = target.read_text(encoding="utf-8")
        text = original

        # ── ① Elements 的 tooltip：按偏移定点替换 ────────────────────
        # ⚠️ **必须早于通用 label 替换**：通用那轮的 needle 是 `"<英文>"`
        #    精确匹配，这里先把字面量换掉，通用那轮自然就够不着了。
        #    （反过来的话，工具提示会被当成 label 加上 `##` ⇒ 悬停时看到一坨 `##…`）
        if rel == GUI_SPEC and fields:
            text, spec_tips = _replace_field_tooltips(text, fields, spec)
            applied.extend((f"tooltip:{t}", "属性悬停说明", rel, 1)
                           for t, _, _ in spec_tips)

        # 定点替换不改行数，所以在这里切行/算语句跨度都安全
        # ⚠️ 语句级上下文 —— 用于 `_needs_no_wrap` 的**跨行字面量**判断。
        #    每次替换后 line 数不变（只改行内内容），所以这份映射一直有效。
        lines = text.splitlines(keepends=True)
        stmts = _statement_spans(lines)

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
            needle = f'"{c_escape(en)}"'
            if needle not in text:
                continue

            # ⚠️⚠️ 逐行判断该不该加 `##`（见 NOWRAP_GUARDS 的长注释）
            #
            #    `中文 (English)##English` 里的 `##` 保持 widget ID 稳定，
            #    但**只在 ImGui 直接消费 label 时成立**。被拼进数据或整串
            #    当显示文本时，`##` 会把后面的内容吃掉。
            plain = f'"{c_escape(zh)} ({c_escape(en)})"'
            wrapped = f'{plain[:-1]}##{c_escape(en)}"'

            out_lines, hits = [], 0
            for idx, ln in enumerate(lines):
                if needle in ln:
                    hits += ln.count(needle)
                    # ⚠️ 传 idx 对应的**语句全文**，不是只传本行 ——
                    #    跨行字面量的续行上没有 SetItemTooltip（见函数注释）
                    if _needs_no_wrap(ln, stmts[idx]):
                        out_lines.append(ln.replace(needle, plain))
                        nowrapped.append((en, zh, rel))
                    else:
                        out_lines.append(ln.replace(needle, wrapped))
                else:
                    out_lines.append(ln)
            if not hits:
                continue
            lines = out_lines
            text = "".join(out_lines)
            applied.append((en, zh, rel, hits))

        # ── ② Elements 的属性名：注入查表块 + 改宏定义 ──────────────
        # 放在通用替换**之后**：它只改 `#define` 两行 + 插一段块，
        # 与通用替换的 `"字面量"` 精确匹配不冲突，顺序其实无所谓；
        # 但放后面能让「通用替换生效了几条」的统计保持原样。
        if rel == GUI_SPEC and fields:
            text, n_macro = _inject_field_labels(text, fields)
            applied.extend((f"fieldlabel:{f}", "属性名", rel, 1)
                           for f in fields["labels"])
            lines = text.splitlines(keepends=True)

        # ── ③ 悬停挂载点：让 tooltip 也挂在输入框上（官方 bug 修复）──
        if rel in (IMGUI_CC, IMGUI_H):
            text, n_hover = _inject_hover_targets(text)
            applied.extend((f"hovertarget:{rel}:{i}", "悬停挂载点", rel, 1)
                           for i in range(n_hover))
            lines = text.splitlines(keepends=True)

        # ── ④ 控件悬停说明：辅助函数加形参 + 调用点补实参/插调用 ─────
        # ⚠️ 必须**晚于**通用 label 替换：通用那轮按 `"<英文>"` 精确匹配
        #    加 `##`，而控件 label（如 `"Fog start"`）大多不在 audit 里，
        #    不受影响；但顺序上仍放在最后，避免插入的行打乱行号。
        if rel == IMGUI_H and controls:
            text, n_hdef = _inject_helper_params(text)
            applied.extend((f"helperdef:{i}", "辅助函数加形参", rel, 1)
                           for i in range(n_hdef))
            lines = text.splitlines(keepends=True)
        if rel == GUI_CC_GUI and controls:
            # ⚠️⚠️ 顺序不能反：`_inject_control_tooltips` 按**绝对偏移**定位调用点，
            #    而 `_inject_loop_tips` 会在文件头部插 48 行查表项，
            #    一插就把后面所有偏移推后（踩过：报「循环里找不到 toggle 调用」，
            #    实际是行号已经指到别处了）。
            #    ⇒ 先做按偏移的，再做按锚点的。
            text, n_ctl = _inject_control_tooltips(text, controls, ctl)
            text, n_loop = _inject_loop_tips(text, controls, ctl)
            applied.extend((f"ctltip:{c['panel']}:{c['label_en']}", "控件悬停说明", rel, 1)
                           for c in ctl)
            lines = text.splitlines(keepends=True)

        # ── tooltip 注入（只对 gui.cc）────────────────────────────
        # ⚠️ 必须和 label 替换**生成进同一个 patch**：现有 patch 的 hunk
        #    上下文已经包含那两个 for 循环，另出一个 patch 会交叉，而
        #    build_ux_zh.sh 按 glob 顺序应用 —— 顺序不可依赖。
        if rel == GUI_CC and tooltips:
            lines, tips_applied = _inject_tooltips(lines, tooltips)
            text = "".join(lines)
            applied.extend(tips_applied)
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
        # ⚠️ 不能只找带 ## 的 —— 有些串现在**故意不加** ##（被拼接/当显示文本）
        already = 0
        for rel in TARGET_FILES:
            body = (src_dir / rel).read_text(encoding="utf-8")
            for en, zh in translations.items():
                if en not in audit["translatable"]:
                    continue
                if f'"{c_escape(zh)} ({c_escape(en)})' in body:
                    already += 1
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
        "nowrapped": nowrapped,
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

    # tooltip 数据（独立于 .po —— 见 TOOLTIPS 附近的说明）
    try:
        tooltips = load_tooltips(TOOLTIPS)
        if tooltips:
            tot = sum(len(v) for v in tooltips.values())
            print(f"读入 tooltip: {tot} 条 -> "
                  + ", ".join(f"{k}({len(v)})" for k, v in tooltips.items()))
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 1

    # Elements（属性）面板 —— 属性名 + 悬停说明
    try:
        fields = load_field_tooltips(FIELDS)
        if fields:
            print(f"读入 Elements: 属性名 {fields['n_labels']} 条 / "
                  f"悬停说明 {fields['n_entries']} 条")
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 1

    # 非 Elements 面板的控件
    try:
        controls = load_control_tooltips(CONTROLS)
        if controls:
            print(f"读入控件悬停说明: {controls['n']} 条")
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 1

    diff, st = build_patch(translations, args.src, tooltips, fields, controls)

    print(f"\n替换生效: {len(st['applied'])} 处，分布：")
    for rel, n in sorted(st["by_file"].items()):
        print(f"    {n:>4}  {rel}")
    print(f"跳过（窗口名/ID/标识符）: {len(st['skipped'])} 条")
    print(f"缺译文:                   {len(st['missing'])} 条")

    # ⚠️ 不加 ## 的那批要显式报告 —— 它们是**故意**的，不是漏了
    nw = st.get("nowrapped", [])
    if nw:
        print(f"\n⚠️ 以下 {len(nw)} 处**故意不加 `##`**（会被拼接/当显示文本，"
              f"加了会吃掉后面的内容）：")
        for en, zh, rel in nw:
            print(f"    [{rel.split('/')[-1]}] {en}")
        print("    ⇒ 这些位置译文形如 `中文 (English)`，无 ID 后缀，"
              "属**预期行为**，别当 bug 修。")

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
