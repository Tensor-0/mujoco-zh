# mujoco-zh —— MuJoCo 中文控制面板（带悬停教学）

> **给 MuJoCo Studio 加中文控制面板：所有控件中文，鼠标悬停弹出「这个选项是干什么的」。**

---

## 这是什么

在官方 **MuJoCo Studio** 上挂一个纯 Python 面板：

| 功能 | 说明 |
|---|---|
| **中文面板** | 控件标签全中文（中英对照）|
| ⭐ **悬停教学** | 鼠标停在任意选项上 → 弹出「调大/调小会怎样」|
| **零侵入** | 不改官方二进制、不重编译、不 fork |

**截图**：见 `assets/panel.png`

---

## 快速开始

```bash
# 1. 装中文字体（必须，否则中文显示为方块）
./scripts/install_font.sh

# 2. 跑起来（用装 mujoco 的那个 python）
export PYTHONPATH=$PWD/src
python3 -m mujoco_zh.panel_zh /path/to/your_model.xml
```

**退出**：终端按 `Ctrl+C`（官方文档说明的方式）

**前置**：`mujoco` 需 ≥ 3.11（Studio 从这版开始在 wheel 里）

---

## ⭐ 为什么不用官方老 viewer（`mujoco.viewer`）

> **这一节是本次调研最重要的结论。三条路我们都实测验证过，全部堵死。**

### 实测证据

| 路线 | 结论 | 证据 |
|---|---|---|
| **老 viewer 加 tooltip** | ❌ **框架层不支持** | 它用的**不是 ImGui**，是自研 `mjUI`。`mjuiItem` 结构体**没有任何描述字段**，全仓只有 `int mousehelp`（按右键显示快捷键）|
| **老 viewer 显示中文** | ❌ **字体只有 128 个 ASCII 字形** | 反汇编 `makeFont`：`movl $0x80, ...; glGenLists(128)`。渲染用 `glCallLists` **逐字节查表**，UTF-8 汉字节越界 → 画到相邻字体图集上，**画出乱码且不报错** |
| **二进制 patch** | ❌ **双重死因** | ① 只能换字符串、**挂不了 tooltip 交互** ② 中文根本画不出来（同上）。长度反而不是问题（40 字节槽，中位余量 39 字节 ≈ 13 汉字）|

### 而 Studio 三件套全有

官方自己的对比表（`doc/skills/studio/SKILL.md`）：

| | 老 `simulate` | **Studio** |
|---|---|---|
| GUI 框架 | 自制 `mjUI` | **Dear ImGui** |
| 可扩展性 | **硬编码面板和快捷键** | **C++ 和 Python 双插件架构** |

**⇒ 这就是选 Studio 的理由。**

---

## ⚠️ 关键前提：字体必须替换

**Studio 主字体 `AtkinsonHyperlegibleNext[wght].ttf` 不含任何 CJK 字形。**

**但 ImGui 1.92 起引入「动态字形光栅化」**——

> 【引用】*"the user doesn't need to provide glyph ranges any more"*

**⇒ 换掉字体文件就自动生效，不需要指定 glyph range、不需要改代码。**

```bash
# 就是这一条
cp /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
   "$(python3 -c 'from mujoco.experimental import studio as s; print(list(s.__path__)[0])')/assets/AtkinsonHyperlegibleNext[wght].ttf"
```

`scripts/install_font.sh` 会自动做这件事（含备份）。

> ⚠️ **`pip install --upgrade mujoco` 会覆盖字体** —— 升级后重跑脚本。

---

## 项目结构

```
mujoco-zh/
├── README.md                      本文
├── docs/
│   ├── 为什么用Studio.md           三条路线的实测证据（含反汇编）
│   └── tooltip文案规范.md          写解释文案的规则
├── src/mujoco_zh/
│   ├── __init__.py
│   ├── tooltips.py                ⭐ 文案数据层（52 条，与 UI 框架解耦）
│   └── panel_zh.py                ⭐ 主程序
├── scripts/
│   └── install_font.sh            字体替换（含备份）
└── assets/
    └── panel.png                  截图
```

---

## ⭐ 核心设计：文案与前端解耦

`tooltips.py` 是**纯数据**，换任何前端（ImGui / PyQt / Web）都能复用：

```python
@dataclass
class Tip:
    zh: str                        # 中文名
    en: str                        # 英文原名
    what: str                      # 一句话：这是什么
    effects: list[str]             # 调大/调小会怎样
    when: str                      # 什么时候该动它
```

**每条解释都回答三个问题**：

1. **这是什么？**
2. **调大/调小、打开/关闭会看到什么变化？**
3. **什么时候该动它？**

**示例**：

```
仿真步长  (Timestep)
────────────────────────────
每次物理计算往前推进多少秒。

调整效果：
  · 调小 → 更精确、更稳定，但一步算得慢
  · 调大 → 快，但可能「穿透」「抖动」「爆炸」
  · 经验值：机器人仿真常用 0.001~0.005 秒

💡 ⭐ 看到关节抽搐、物体穿透时，先调小它试试。
```

**当前覆盖 52 条**：可视化开关 15 · 渲染 8 · 物理参数 12 · 仿真控制 4 · 分组 6 · 相机 7

---

## 已知问题

| 现象 | 说明 |
|---|---|
| **退出时 core dump** | `Engine::shutdown() called from the wrong thread!` —— Studio passive 模式 teardown 的已知竞态，**运行期无影响** |
| `Xlib: NV-GLX missing` | 无害警告（软件渲染路径）|
| `dear_imgui` 是命名空间包 | 必须 `from mujoco.experimental.dear_imgui import dear_imgui`（多一层）|
| 枚举是类属性 | 用 `imgui.Cond.FirstUseEver`，不是 `imgui.ImGuiCond_FirstUseEver` |
| `SetNextWindowSize` 要 `Vec2` | `imgui.SetNextWindowSize(imgui.Vec2(380, 560), ...)` |

---

## 调研背景：这个方向是空白

三个独立 agent 检索确认：

| 检索词 | 结果 |
|---|---|
| `mujoco i18n` / `mujoco 汉化` / `mujoco viewer alternative` | **0 个项目** |
| Gazebo 全系 / PyBullet / Isaac Lab 的翻译文件 | **0 个** |

> **⇒ 机器人仿真领域，三大家（Gazebo / Bullet / Isaac）都没做 i18n。**
> **这个项目没有现成轮子可抄。**

**最接近的参考**：`LeonIdris/alien-chinese`（ImGui 模拟器的汉化版）——
但它是**纯翻译**，没有悬停教学。**这正是本项目的差异化。**

---

## 相关资源

| 资源 | 链接 |
|---|---|
| MuJoCo 官方仓库 | https://github.com/google-deepmind/mujoco |
| Studio 源码 | `src/experimental/studio/`（同仓库）|
| ImGui | https://github.com/ocornut/imgui |
| Noto Sans CJK | https://fonts.google.com/noto/specimen/Noto+Sans+SC |

---

## 许可

本仓库代码 MIT。引用的字体（Noto Sans CJK）为 SIL OFL。
