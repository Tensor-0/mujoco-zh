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


def build_patch(translations: dict[str, str], src_dir: Path,
                tooltips: dict[str, dict] | None = None) -> tuple[str, dict]:
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

    for rel in TARGET_FILES:
        target = src_dir / rel
        if not target.is_file():
            raise SystemExit(
                f"❌ 找不到源码: {target}\n"
                f"   （先跑 scripts/build_ux_zh.sh 拉源码，或用 --src 指定）")

        original = target.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)
        # ⚠️ 语句级上下文 —— 用于 `_needs_no_wrap` 的**跨行字面量**判断。
        #    每次替换后 line 数不变（只改行内内容），所以这份映射一直有效。
        stmts = _statement_spans(lines)
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

    diff, st = build_patch(translations, args.src, tooltips)

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
