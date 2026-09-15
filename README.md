# mujoco-zh —— MuJoCo Studio 中文化

> **让 MuJoCo Studio 说中文：官方界面原生中文化（234 条界面文字）。**

---

## 这是什么

让官方 **MuJoCo Studio** 的界面**原生显示中文** —— 不是旁边挂个面板，是
菜单栏、Inspector、各设置面板里的文字**本身**变成中文，形如 `中文 (English)`。

| 层 | 做法 | 条数 |
|---|---|---|
| **Python 侧** | 拦截 `imgui` 模块调用（monkeypatch，零源码改动）| 23 |
| **C++ 侧** | 改 `ux.so` 源码字面量后**重编译** | 211 |

**两级用法，按需要选**：

| | 覆盖范围 | 代价 |
|---|---|---|
| **基础**（装字体 + 跑模块）| Python 侧 23 条 | 零侵入，随时卸载 |
| **完整**（再重编译 `ux.so`）| 再加 C++ 侧 211 条 | 需 clang + libc++，可一键还原 |

> 基础用法**不改官方任何文件**（除了字体，可还原）。
> 完整用法会替换 `ux.so`，但原版已自动备份，`build_ux_zh.sh --restore` 一键还原。

**截图**：见 `assets/panel.png` 与 `assets/studio_cjk_verify.png`。
想**并排看原版 vs 汉化版**：`./scripts/compare_ux.sh <model.xml>`（见下）。

---

## 快速开始

```bash
# 0. 找到「装了 mujoco 的那个 python」——这一步最容易踩坑
#    如果 python3 -c "import mujoco" 报错，说明你默认的 python3 不是它
MUJOCO_PY=/path/to/your/venv/bin/python3

# 1. 装中文字体（必须，否则中文显示为方块）
./scripts/install_font.sh

# 2. 跑起来
PYTHONPATH=$PWD/src $MUJOCO_PY -m mujoco_zh.panel_zh /path/to/your_model.xml
```

以上是**基础用法**（Python 侧 23 条 + 面板）。要连 `ux.so` 里那 211 条
C++ 字面量一起翻，再加两步：

```bash
# 3. 装编译依赖（只需一次）
sudo apt-get install -y clang-15 libc++-15-dev libc++abi-15-dev

# 4. 生成译文 patch 并重编译 ux.so
python3 scripts/gen_cpp_patch.py
./scripts/build_ux_zh.sh            # 原版会自动备份；--restore 可还原
```

**退出**：终端按 `Ctrl+C`（官方文档说明的方式）

**前置**：`mujoco` ≥ 3.11（Studio 从这版开始进 wheel）

> ⚠️ **`install_font.sh` 会自动探测解释器**，但如果你机器上不止一个 mujoco，用 `PYTHON=<路径> ./scripts/install_font.sh` 显式指定。

---

## 对比验证：原版 vs 汉化版并排看

想直观确认汉化效果（或截图做前后对照），一条命令拉起**两个窗口**：

```bash
./scripts/compare_ux.sh /path/to/your_model.xml

# 例
./scripts/compare_ux.sh ~/UniLab/src/unilab/assets/robots/dm10/scene_flat.xml
```

> ⚠️ **挑模型只影响观感，不影响对比结果**，但别用「纯机器人」文件：
> `robots/dm10/dm10.xml` 里**没有地面**（地面在 `scene_flat.xml`），
> 自由基座的机器人会**无限下坠**（实测 5 秒掉到 z=−121），看着像 bug。
> 挑 `<robot>/scene*.xml`，或干脆用不会动的 `stewart/scene.xml`。

左边选**① 原版 MuJoCo Studio**，右边选**② 汉化版 MuJoCo Studio**（窗口标题可分辨）。

### 为什么要两个进程

C++ 侧那 200 多条汉化靠**替换 `ux.so` 这一个文件**实现，而 venv 里同一时刻只能装一个版本。
所以脚本造了个 **1.7MB 的影子包**（默认 `/tmp/mujoco-orig`）——整棵包树全是指向真实包的
symlink，**只有 `ux.so` 是实体**（官方备份的拷贝），靠 `PYTHONPATH` 排在 `site-packages`
前面命中它。两个 Studio 进程各加载各的 `.so`，互不干扰。

脚本开跑前会先把**两个实例实际加载的 `.so` 的 md5** 打出来，一眼能核对该不一致。

### ⚠️ 三个必须知道的点

1. **原版实例会设 `MUJOCO_ZH_NO_TRANSLATE=1`。** 不设的话，Python 侧那 200 多条仍会被汉化，
   你看到的就成了「**半汉化 vs 全汉化**」而不是「原版 vs 汉化版」。
2. **两边共用同一套 CJK 字体**（影子包的 `assets` 是 symlink，这是**有意设计**）——
   否则原版实例里的中文全是豆腐块，反而看不出差异。**别把它当 bug「修」掉。**
3. **窗口位置不可控**（Studio 硬编码 `SDL_WINDOWPOS_UNDEFINED`），要用
   `Super + ←` / `Super + →` 手动摆成左右半屏。

### 其他实测结论（省得重踩）

| | |
|---|---|
| **出图慢** | 要 **30~60 秒**；头 50 秒窗口列表里看不到它是正常的，不是启动失败 |
| **不需要隔离 HOME** | Studio 是 `io.IniFilename = nullptr`，**根本不读也不写 `imgui.ini`** |
| **退出必崩** | `Engine::shutdown() called from the wrong thread!` —— **官方 `.so` 也一样**，是 Studio 已知问题，不是汉化引入的 |
| **截图尺寸** | 本机 GUI 是 `DISPLAY=:1`、屏幕 **2560x1440**。写反成 `1440x2560` 时 ffmpeg 会报错**但仍写出坏图**，静默坑 |

---

## ⚠️ 关键前提：字体必须替换

**Studio 主字体 `AtkinsonHyperlegibleNext[wght].ttf` 不含任何 CJK 字形。**

**但 ImGui 1.92 起引入「动态字形光栅化」**——

> 【引用】*"the user doesn't need to provide glyph ranges any more"*

**⇒ 换掉字体文件就自动生效，不需要指定 glyph range、不需要改代码。**

```bash
./scripts/install_font.sh              # 自动备份 + 替换 + 校验
PYTHON=<你的mujoco解释器> ./scripts/install_font.sh   # 显式指定解释器
```

**还原**：

```bash
./scripts/restore_font.sh              # 从备份还原
```

> ⚠️ **备份的前提是备份时还是原版字体。** 脚本会先校验文件大小（原版 ~200 KB / CJK 字体 19 MB+），
> **已经是 CJK 字体就不会拿它去覆盖备份** —— 否则会把 CJK 字体当成「原版」备份下来，
> 之后「还原」等于什么都没做。
>
> ⚠️ **`pip install --upgrade mujoco` 会覆盖字体** —— 升级后重跑 `install_font.sh`。

---

## 中文化是怎么做到的

分**两条路径**，取决于文字在哪：

### ① Python 侧（23 条）—— monkeypatch

官方 Studio 的界面文字全部通过 `imgui.Begin('Inspector')`、`imgui.Text('...')`
这样的**调用**产生。所以**包住这一层就能翻译整个官方 UI，零源码改动**。

```python
from mujoco_zh import translate
translate.install()      # 幂等；必须装在官方 UI 构建之前
```

### ② C++ 侧（211 条）—— 重编译

`ux.so` 里那部分文字 Python 层够不着（原因见下节「覆盖边界」），
只能改了源码重编。**一条命令**：

```bash
python3 scripts/gen_cpp_patch.py    # 从 .po 生成 patch
./scripts/build_ux_zh.sh            # 编译 + 安装（含 ABI 自检）
./scripts/build_ux_zh.sh --restore  # 还原官方
```

⚠️ **必须用 clang + libc++** —— pybind11 的类型注册表按编译器 ABI 隔离，
用 gcc/libstdc++ 编出来会在运行时报 `incompatible function arguments`。
详见 → [`docs/重编译覆盖C++字符串.md`](docs/重编译覆盖C++字符串.md)

### 一份译文，两条消费者

`locales/zh_CN/LC_MESSAGES/mujoco_zh.po` **同时喂给两边**：

```
.po (214 条) ──┬──→ translate.py         (Python 侧，运行时 gettext)
               └──→ gen_cpp_patch.py → patches/ux-zh.patch → 重编译
```

**⇒ 改词表只需动 `.po` 一处。**

界面样式统一是 **`中文 (English)`** 双语并列，方便你对照官方文档和英文教程。
C++ 侧实际写进字面量的是 `中文 (English)##English` ——
`##` 后缀让 **ImGui 的 widget ID 保持不变**，否则布局 ini 缓存会错乱。

### ⭐ 三条实测教训（都踩过）

**1. `wrapt` 在这里静默失效，只能手写 `setattr`**

```python
>>> wrapt.wrap_function_wrapper(ig, 'MenuItem', wrapper)
>>> type(ig.MenuItem)     # 仍是 builtin_function_or_method —— 没包上！也不报错
```

原因：`imgui` 的属性是 pybind11 的 `builtin_function_or_method`，
**不是普通 Python 函数**，wrapt 的包装机制对它们不生效。手写 `setattr` 可行。

**2. 不能包「所有」方法 —— 会破坏 ImGui 的配对**

第一版把所有方法都包了，ImGui 立刻报：

```
[imgui-error] Calling End() too many times!
[imgui-error] Calling EndMenu() in wrong window!
```

`Begin*` / `TreeNode*` 严格要求 `if X(): ... EndX()` 配对，而
`MenuItem` / `TreeNode` / `TreeNodeEx` / `BeginTabItem` 是**重载函数**。
**⇒ 只包「纯文本、无返回值、非重载」的方法**（见 `translate.py` 的 `SAFE_METHODS`）。

**3. 五条排除规则 —— 这是「不崩」的保障**

非 `str` · 空串 · 含 `%`（格式串）· 含 `##`（ImGui ID 后缀）· 已含中文（幂等）
—— 任一命中原样透传。另外 `TextUnformatted` 这类会被 ImGui 直接当格式串用的也不能包。

> ⚠️ **代价**：默认**关闭**安装，必须显式 `translate.install()`；`uninstall()` 完整还原。
> 这是吸取 **MusicBrainz Picard PR #2421** 的教训 —— 那个项目先 monkeypatch 了 gettext，
> 后来**主动拆掉**（mypy 报错 / pylint 需额外配置 / 破坏下游）。

---

## 覆盖边界（实测，别期待超出这个范围）

| 位置 | 条数 | 状态 |
|---|---|---|
| `viewer_app.py` / `studio_app.py` 的 Python 字面量 | 23 | ✅ monkeypatch 自动翻 |
| `ux.cpython-*.so` 的 C++ 字面量 | **211 / 234** | ✅ 已重编译覆盖 |
| `libmujoco.so` 导出的枚举名（`Fog` `Haze` `Cull Face` `Id Color`…）| — | ❌ 够不着 |
| 状态字段标识符（`QPOS` `QVEL` `CTRL` `PGS` `Newton`…）| 23 | ⛔ **刻意不翻** |

**⛔ 那 23 个标识符不翻是有意的**：它们是 `mjData` 的字段名和求解器专名，
官方文档也用英文，翻了反而对不上。UI 里它们与旁边的说明标签配对出现，只翻标签：

```cpp
{"QPOS", "位置 (Position)##Position"},    // 标识符原样保留
{"CTRL", "控制 (Control)##Control"},
```

**为什么 Python 层翻不了 `ux.so`** —— 这不是 bug，是**设计使然**。四条独立实测证据：

| 证据 | 内容 |
|---|---|
| **GIL 被释放** | `ux.cc` 里 **18 个 `*_gui` 绑定全部** `py::gil_scoped_release no_gil;` —— 进 C++ 前先放掉 GIL |
| **反汇编** | `lea -0x80f5f(%rip),%rdi # 1a4aa <Algorithmic Parameters>` —— 字符串地址直接进第一个参数寄存器，这是 C++ 调 `ImGui::Text()` 的产物；走 Python 必须先 `PyUnicode_FromString` |
| **imgui.cpp 内部串** | `Missing EndTable()` / `Calling End() too many times!` 在 `ux.so` 里成片出现 —— 说明 imgui 实现被静态编进去了 |
| **没有模块名** | `ux.so` 里搜不到 `dear_imgui`；要回调 Python 必须 import 它，模块名一定会在 |

**⇒ `ux.so` 静态链接了自己的一份 C++ ImGui**（本机一共躺着**四份**独立 ImGui 拷贝：
`ux` / `dear_imgui` / `native_viewer_cc` / `implot` 各一份，C 层零符号共享，
唯一的桥是 `native_viewer.py` 把同一个 context 指针分别灌给每一份）。
Python 层的 patch 永远看不到它发出的文字。

**要翻这部分，走重编译** —— 已打通，一条命令：

```bash
./scripts/build_ux_zh.sh            # 编译 + 安装（含 ABI 自检）
./scripts/build_ux_zh.sh --restore  # 还原官方
```

⚠️ **必须用 clang + libc++**（pybind11 的类型注册表按编译器 ABI 隔离，
gcc 编出来会在运行时报 `incompatible function arguments`）。
完整说明与 234 条清单见 → [`docs/重编译覆盖C++字符串.md`](docs/重编译覆盖C++字符串.md)

---

## ⭐ 为什么不用官方老 viewer（`mujoco.viewer`）

| 路线 | 结论 | 一句话原因 |
|---|---|---|
| 老 viewer 加 tooltip | ❌ | 它用的不是 ImGui，是自研 `mjUI`；`mjuiItem` 结构体**没有描述字段** |
| 老 viewer 显示中文 | ❌ | 字体只有 **128 个 ASCII 字形**；渲染**逐字节查表无掩码**，汉字节越界 → 画出错字且**静默不报错** |
| 二进制 patch 老 viewer | ❌ | 双重死因：挂不了 tooltip 交互 + 中文根本画不出来 |

**而 Studio 三件套全有**：Dear ImGui 1.92.6（声明式 UI）· 官方已在用 tooltip
（`gui.cc` 里的 `SetItemTooltip`）· 运行时从磁盘加载 TTF（**字体可换**）。

> **完整反汇编证据链**（含 `glGenLists(128)` 的实证）见 → [`docs/为什么用Studio.md`](docs/为什么用Studio.md)

---

## 项目结构

```
mujoco-zh/
├── README.md                      本文
├── docs/
│   ├── 为什么用Studio.md           三条路线的实测证据（含反汇编）
│   ├── tooltip文案规范.md          写解释文案的规则
│   └── 重编译覆盖C++字符串.md      ux.so 里的 C++ 字符串怎么翻 + 怎么验证
├── src/mujoco_zh/
│   ├── __init__.py
│   ├── translate.py               ⭐ 官方 UI 中文化（monkeypatch 层）
│   ├── tooltips.py                悬停文案数据层（52 条，**当前未接入 UI**）
│   └── panel_zh.py                ⭐ 主程序
├── locales/zh_CN/LC_MESSAGES/     gettext 译文（.po / .mo）
├── data/
│   └── ux_strings_audit.json      盘点：ux.so 里 234 条可翻字符串 + 面板归属
├── patches/
│   └── ux-zh.patch                译文 patch（由 gen_cpp_patch.py 从 .po 生成）
├── scripts/
│   ├── install_font.sh            字体替换（含 SC face 抽取 + 校验 + 备份）
│   ├── restore_font.sh            字体还原
│   ├── extract_sc_font.py         从 TTC 抽简体中文 face（绕开 window.cc 不设 FontNo）
│   ├── gen_cpp_patch.py           ⭐ .po → C++ 译文 patch
│   ├── build_ux_zh.sh             ⭐ 重编译 ux.so（clang+libc++，含 ABI 自检 + 还原）
│   ├── compare_ux.sh              ⭐ 原版 vs 汉化版并排对比（影子包 + 双进程）
│   └── compile_mo.sh              .po → .mo
└── assets/                        截图
```

---

## 📦 预留资产：`tooltips.py`（悬停教学文案，**当前 UI 未接入**）

> ⚠️ **这一层现在不影响运行** —— 主程序只做中文化，不再挂任何附加面板。
> 文件保留是因为文案本身有复用价值，将来想接悬停教学可以直接用。

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

**当前攒了 52 条**：可视化开关 15 · 渲染 8 · 物理参数 12 · 仿真控制 4 · 分组 6 · 相机 7

---

## 已知问题

| 现象 | 说明 |
|---|---|
| **退出时 core dump** | `Engine::shutdown() called from the wrong thread!` —— Studio passive 模式 teardown 的已知竞态，**运行期无影响** |
| `Xlib: NV-GLX missing` | 无害警告（软件渲染路径）|
| 本机 GUI 要用 `DISPLAY=:1` | 不是 `:0`。设错了**静默失败**，容易被误判成「播放器/窗口打不开」 |
| `dear_imgui` 是命名空间包 | 必须 `from mujoco.experimental.dear_imgui import dear_imgui`（多一层）|
| 枚举是类属性 | 用 `imgui.Cond.FirstUseEver`，**不是** `imgui.ImGuiCond_FirstUseEver` |
| `SetNextWindowSize` 要 `Vec2` | `imgui.SetNextWindowSize(imgui.Vec2(380, 560), ...)` |
| `launch_passive` 不接 model/data | 用 `handle.send_to_viewer(messages.ModelEvent(model=model))` |
| `@messages.handler` 要类型标注 | `def on_build_gui(self, _: messages.BuildGuiEvent) -> None:` |
| `studio.__file__` 是 `None` | 用 `list(studio.__path__)[0]` |
| 重编的 `ux.so` 比官方大 | 官方是 `-O2` + `--gc-sections` + strip 过的（1.4 MB），我们是 `-O1` 未 strip（5.4 MB）。功能等价，只是体积大 |

---

## 调研背景：这个方向是空白

| 检索词 | 结果 |
|---|---|
| `mujoco i18n` / `mujoco 汉化` / `mujoco viewer alternative` | **0 个项目** |
| PyPI 全量穷举（24 个含 imgui 的包）| **零个 i18n 包** |
| 三大包描述 grep（imgui-bundle / pyimgui / dearpygui）| 全 **NONE** |
| Gazebo 全系 / PyBullet / Isaac Lab 的翻译文件 | **0 个** |

**ImGui 官方立场**（ocornut 原话）：

> *"it's not really something that dear imgui will do for you. It's really not in the DNA of dear imgui to go toward that direction."*

**最接近的参考**：`LeonIdris/alien-chinese`（ImGui 模拟器汉化）——
同样是纯翻译路线。本项目额外做了**重编译 `.so` 覆盖 C++ 字面量**
（那一半 Python 层够不着）与**双语 `中文 (English)` 对照显示**。

---

## 相关资源

| 资源 | 链接 |
|---|---|
| MuJoCo 官方仓库 | https://github.com/google-deepmind/mujoco |
| Studio 源码 | 同仓库 `src/experimental/platform/` + `python/mujoco/experimental/studio/` |
| ImGui | https://github.com/ocornut/imgui |
| Noto Sans CJK | https://fonts.google.com/noto/specimen/Noto+Sans+SC |

---

## 许可

本仓库代码 MIT。引用的字体（Noto Sans CJK）为 SIL OFL。
