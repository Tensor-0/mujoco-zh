"""panel_zh.py —— MuJoCo Studio 中文控制面板（带悬停教学）

## 这是什么

在官方 MuJoCo Studio 查看器上，挂一个**纯 Python** 面板：
- 所有控件**中文**
- 鼠标**悬停**在控件上 → 弹出「这个选项是干什么的」的解释

## 为什么不用官方老 viewer（simulate）

实测确认老 viewer 的路全堵死：
- 它的 UI 框架是自研 `mjUI`（不是 ImGui），**没有 tooltip 概念**
- 字体只有 **128 个 ASCII 字形**，反汇编 `makeFont` 实证，**中文画不出来**
- 二进制 patch 双重死因：字体 + 挂不了交互

而 Studio 用的是 Dear ImGui 1.92.6，**tooltip 是原生能力**。

## 前置（重要）

**中文字体必须替换**，否则中文显示为空白/豆腐块：

```bash
cp /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \\
   "$(python -c 'import mujoco.experimental.studio as s, os; print(os.path.dirname(s.__file__))')/assets/AtkinsonHyperlegibleNext[wght].ttf"
```

原因：Studio 主字体 Atkinson Hyperlegible **不含 CJK 字形**。
而 ImGui 1.92 引入了动态字形光栅化（"不需要再指定 glyph ranges"），所以**换文件即可生效**。

## 用法

```bash
MUJOCO_ZH_FONT_CHECK=1 python -m mujoco_zh.panel_zh <model.xml>
```

键盘：`Ctrl+C` 退出（官方文档说明的方式）
"""

from __future__ import annotations

import os
import sys

from mujoco.experimental.studio import launch_passive, messages, sim as studio_sim
from mujoco.experimental.studio import viewer_protocol
from mujoco.experimental.dear_imgui import dear_imgui as imgui

from . import tooltips as T
from . import translate

# ⚠️ **必须在官方 UI 构建之前安装**。
# 官方 viewer_app / studio_app 也是 `import ... as imgui`，
# 它们拿到的是**同一个模块对象** —— 所以在模块属性上打补丁，它们立刻就生效。
# 时机：只要在 launch_passive 之前即可。
translate.install(imgui)


# ══════════════════════════════════════════════════════════
# 小工具：带 tooltip 的控件包装
# ══════════════════════════════════════════════════════════

def _tooltip(key: str) -> None:
    """当前 item 悬停时显示解释"""
    if imgui.IsItemHovered():
        txt = T.tip_text(key)
        if txt:
            imgui.SetItemTooltip(txt)


def text_tip(label: str, key: str) -> None:
    """中文标签 + 悬停解释"""
    imgui.Text(label)
    _tooltip(key)


def checkbox_tip(label: str, value: bool, key: str) -> bool:
    changed, val = imgui.Checkbox(label, value)
    _tooltip(key)
    return val


def slider_tip(label: str, value: float, lo: float, hi: float, key: str) -> float:
    changed, val = imgui.SliderFloat(label, value, lo, hi)
    _tooltip(key)
    return val


# ══════════════════════════════════════════════════════════
# 面板
# ══════════════════════════════════════════════════════════

class ChinesePanel:
    """中文控制面板。

    挂在 Studio 的 `on_build_gui` 事件上。
    """

    def __init__(self) -> None:
        # 本地状态（将来可直接接到 model.opt / vis 上）
        self._sim_speed = 1.0
        self._show_help = True

    # ── 主入口 ────────────────────────────────────────
    @messages.handler
    def on_build_gui(self, _: messages.BuildGuiEvent) -> None:
        imgui.SetNextWindowSize(imgui.Vec2(380, 560), imgui.Cond.FirstUseEver)

        if imgui.Begin("控制面板（中文）"):
            self._section_help()
            self._section_sim()
            self._section_physics()
            self._section_rendering()
            self._section_camera()
            self._section_groups()
        imgui.End()

    # ── 各分节 ────────────────────────────────────────
    def _section_help(self) -> None:
        if imgui.CollapsingHeader("❓ 怎么用这个面板", imgui.TreeNodeFlags.DefaultOpen):
            imgui.TextWrapped(
                "把鼠标停在任意选项上，会弹出这个选项是干什么的。"
            )
            imgui.Spacing()
            imgui.TextWrapped(
                "常用操作：\n"
                "  · 左键拖动 = 转视角\n"
                "  · 右键拖动 / 滚轮 = 平移 / 缩放\n"
                "  · Ctrl + 右键拖 = 给机器人施加推力\n"
                "  · 双击部位 = 选中并高亮"
            )

    def _section_sim(self) -> None:
        if imgui.CollapsingHeader("▶ 仿真控制", imgui.TreeNodeFlags.DefaultOpen):
            for k in ("pause", "reset", "step", "speed"):
                t = T.get(k)
                if t:
                    text_tip(f"{t.zh} ({t.en})", k)
            imgui.Spacing()
            imgui.TextDisabled("（按钮接到 Studio 原生控制上）")

    def _section_physics(self) -> None:
        if imgui.CollapsingHeader("⚙ 物理参数"):
            imgui.TextWrapped("改这些会影响仿真的精确度和速度。")
            imgui.Spacing()
            for k in ("timestep", "gravity", "iterations", "tolerance",
                      "solver", "integrator", "cone", "friction"):
                t = T.get(k)
                if t:
                    text_tip(f"{t.zh} ({t.en})", k)

    def _section_rendering(self) -> None:
        if imgui.CollapsingHeader("🎨 渲染与显示"):
            imgui.TextWrapped("控制画面上显示什么、不显示什么。")
            imgui.Spacing()
            for k in ("mjVIS_JOINT", "mjVIS_CONTACTFORCE", "mjVIS_CONTACTPOINT",
                      "mjVIS_COM", "mjVIS_TRANSPARENT", "mjVIS_ACTUATOR",
                      "mjRND_SHADOW", "mjRND_SEGMENT", "mjRND_LABEL"):
                t = T.get(k)
                if t:
                    text_tip(f"{t.zh} ({t.en})", k)

    def _section_camera(self) -> None:
        if imgui.CollapsingHeader("📷 相机"):
            for k in ("type", "trackbody", "distance", "azimuth",
                      "elevation", "orthographic"):
                t = T.get(k)
                if t:
                    text_tip(f"{t.zh} ({t.en})", k)

    def _section_groups(self) -> None:
        if imgui.CollapsingHeader("👁 分组可见性"):
            imgui.TextWrapped("按「组号」批量显示/隐藏，用来在复杂场景里筛选。")
            imgui.Spacing()
            for k in ("geomgroup", "sitegroup", "jointgroup", "actuatorgroup"):
                t = T.get(k)
                if t:
                    text_tip(f"{t.zh} ({t.en})", k)


# ══════════════════════════════════════════════════════════
# 字体检查
# ══════════════════════════════════════════════════════════

def check_font() -> bool:
    """检查 Studio 字体是否已替换为 CJK 字体"""
    try:
        from mujoco.experimental import studio as studio_pkg
        base = list(studio_pkg.__path__)[0]
        f = os.path.join(base, "assets", "AtkinsonHyperlegibleNext[wght].ttf")
        size = os.path.getsize(f)
        ok = size > 5_000_000        # CJK 字体动辄 10MB+
        if ok:
            print(f"✅ 字体已替换（{size/1e6:.1f} MB）—— 中文可正常显示")
        else:
            print(f"⚠️  字体未替换（{size/1024:.0f} KB）—— 中文可能显示为空白/方块")
            print("   修复：")
            print("   cp /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \\")
            print(f"      '{f}'")
        return ok
    except Exception as e:
        print(f"⚠️  字体检查失败: {e}")
        return False


# ══════════════════════════════════════════════════════════
# 主程序
# ══════════════════════════════════════════════════════════

def run(model_path: str, width: int = 1400, height: int = 900) -> int:
    import mujoco
    from mujoco.experimental.studio import viewer_app

    print(f"加载模型: {model_path}")
    model = mujoco.MjModel.from_xml_path(model_path)
    data = mujoco.MjData(model)
    print(f"  {model.nbody} 刚体 · {model.njnt} 关节 · {model.nu} 执行器")

    check_font()
    print()
    st = translate.stats()
    print(f"✅ 翻译层已装：{st['translations']} 条译文（来源 {st['source']}）"
          f" / 包装了 {st['wrapped']} 个 imgui 方法")
    print()
    print("启动 Studio（关掉请在终端按 Ctrl+C）...")

    config = viewer_protocol.ViewerConfig(
        title="MuJoCo 中文面板",
        width=width, height=height,
        gfx="",
        viewer_mode=viewer_protocol.ViewerMode.NATIVE,
    )

    # ⚠️ 关键：必须同时挂官方 ViewerApp，否则菜单栏 / Inspector /
    #    Physics / State 面板全都不加载，只剩一个光秃秃的渲染窗口。
    with launch_passive.launch_passive(
        config,
        viewer_handlers=[
            viewer_app.ViewerApp(),   # ← 官方完整 UI（菜单栏、各面板）
            ChinesePanel(),           # ← 我们的中文面板（追加）
        ],
    ) as handle:
        handle.send_to_viewer(messages.ModelEvent(model=model, path=model_path))
        step_control = studio_sim.StepControl()
        try:
            while handle.is_running():
                step_control.advance(model, data)
                model, data, step_control = handle.sync(model, data, step_control)
        except KeyboardInterrupt:
            print("\n退出。")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 1
    return run(argv[0])


if __name__ == "__main__":
    sys.exit(main())
