"""panel_zh.py —— 启动 MuJoCo Studio（界面中文化）

## 这是什么

在官方 MuJoCo Studio 查看器上装一层**翻译**：
官方 UI 的每一处文字都走 `imgui.Begin('Inspector')` / `imgui.BeginMenu('File')`
这样的调用，**包住这一层就能翻译整个官方 UI，零源码改动**。
另外 `ux.so` 里的 C++ 字面量由重编译覆盖（见 scripts/build_ux_zh.sh）。

## 为什么不用官方老 viewer（simulate）

实测确认老 viewer 的路全堵死：
- 它的 UI 框架是自研 `mjUI`（不是 ImGui），**没有 tooltip 概念**
- 字体只有 **128 个 ASCII 字形**，反汇编 `makeFont` 实证，**中文画不出来**
- 二进制 patch 双重死因：字体 + 挂不了交互

而 Studio 用的是 Dear ImGui 1.92.6，**动态字形 + 可替换字体**。

## 前置（重要）

**中文字体必须替换**，否则中文显示为空白/豆腐块：

```bash
./scripts/install_font.sh
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

from . import translate

# ⚠️ **必须在官方 UI 构建之前安装**。
# 官方 viewer_app / studio_app 也是 `import ... as imgui`，
# 它们拿到的是**同一个模块对象** —— 所以在模块属性上打补丁，它们立刻就生效。
# 时机：只要在 launch_passive 之前即可。
#
# `MUJOCO_ZH_NO_TRANSLATE=1` 时跳过安装 —— 供「原版 vs 汉化版」并排对比的
# **对照组**使用。没有这个开关的话，即使该实例加载的是原版 ux.so，
# Python 侧那 200+ 条仍会被汉化，看到的就成了「半汉化 vs 全汉化」而非原版。
# 见 scripts/compare_ux.sh
#
# ⚠️ 保持模块级执行 —— 挪进 run() 会晚于官方 UI 构建的 import 时机。
if not os.environ.get("MUJOCO_ZH_NO_TRANSLATE"):
    translate.install(imgui)
else:
    print("ℹ️  MUJOCO_ZH_NO_TRANSLATE=1 —— 跳过 Python 侧翻译层（对比模式）")


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

    # 标题可用环境变量覆盖 —— 「原版 vs 汉化版」并排对比时，两个窗口
    # 默认同名，任务栏里分不清哪个是哪个（见 scripts/compare_ux.sh）
    config = viewer_protocol.ViewerConfig(
        title=os.environ.get("MUJOCO_ZH_TITLE", "MuJoCo 中文面板"),
        width=width, height=height,
        gfx="",
        viewer_mode=viewer_protocol.ViewerMode.NATIVE,
    )

    # ⚠️ 关键：必须挂官方 ViewerApp，否则菜单栏 / Inspector /
    #    Physics / State 面板全都不加载，只剩一个光秃秃的渲染窗口。
    with launch_passive.launch_passive(
        config,
        viewer_handlers=[
            viewer_app.ViewerApp(),   # ← 官方完整 UI（菜单栏、各面板）
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
    # 窗口尺寸：⚠️ 传进去的是**逻辑点**，实际像素要乘 DPI 缩放（本机 1.13x），
    # 所以设 1100 得到的窗口约 1243 px 宽。见 scripts/compare_ux.sh
    return run(argv[0],
               width=int(os.environ.get("MUJOCO_ZH_WIDTH", 1400)),
               height=int(os.environ.get("MUJOCO_ZH_HEIGHT", 900)))


if __name__ == "__main__":
    sys.exit(main())
