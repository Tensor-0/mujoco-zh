# 覆盖 ux.so 里的 C++ 字符串

> `ux.cpython-*.so` 里有 **234 条**界面文字，Python 层的 monkeypatch 够不着。
> 本文说明**为什么够不着**、**怎么翻**，以及**哪些翻不了**。

---

## 一、为什么 Python 层够不着

`ux.so` **静态链接了自己的一份 C++ ImGui** —— 和 Python 那份 `dear_imgui` 是**两套东西**。
四条独立实测证据：

| # | 证据 | 说明 |
|---|---|---|
| 1 | **18 个 `*_gui` 绑定全部 `py::gil_scoped_release no_gil;`** | 进 C++ 前先把 GIL 放掉；要回调 Python 必须重新 acquire，而这些函数里没有。只有 `set_imgui_context` / `set_implot_context` 不放 GIL（初始化握手，符合预期）|
| 2 | **反汇编**：`lea -0x80f5f(%rip),%rdi # 1a4aa <Algorithmic Parameters>` | 字符串地址**直接进第一个参数寄存器**（SysV 的 `rdi`）—— 这是 C++ 调 `ImGui::Text()` 的编译产物。走 Python 必须先 `PyUnicode_FromString` 变 PyObject，形态完全不同 |
| 3 | **imgui.cpp 函数体内报错串成片出现** | `Missing EndTable()` / `Calling End() too many times!` / `[docking]`×18 —— 这些只在 imgui.cpp 的**实现**里，头文件产生不了 |
| 4 | **搜不到模块名 `dear_imgui`** | 要回调 Python 必须 `PyImport_ImportModule`，模块名一定会在二进制里 |

**⇒ 这是设计使然，不是 bug，也不是 patch 写错了。**

**本机一共有四份独立的 ImGui 拷贝**：`ux` / `dear_imgui` / `native_viewer_cc` / `implot`
各静态链一份，C 层**零符号共享**。唯一的桥梁是 `native_viewer.py:86-91`：

```python
ctx = self._viewer.GetImGuiContext()
imgui.SetCurrentContext(ctx)      # 灌进 dear_imgui.so 那份
ux.set_imgui_context(ctx)         # 灌进 ux.so 那份   ← 两次调用，因为本就是两份
```

---

## 二、怎么翻：重编译

**两步**（译文改了只需重跑第一步）：

```bash
python3 scripts/gen_cpp_patch.py    # ① 从 .po 生成译文 patch
./scripts/build_ux_zh.sh            # ② 编译 + 安装（含 ABI 自检）
./scripts/build_ux_zh.sh --restore  # 还原官方
```

### 译文怎么进 C++ 源码

`gen_cpp_patch.py` 读 `locales/zh_CN/LC_MESSAGES/mujoco_zh.po`
（**和 Python 侧 `translate.py` 用的是同一份**，改词表只动一处），
在 `gui.cc` 上做**完整字面量替换**：

```cpp
-  SectionHeader("Algorithmic Parameters", ...)
+  SectionHeader("算法参数 (Algorithmic Parameters)##Algorithmic Parameters", ...)
```

**为什么要 `##English` 后缀**：ImGui 约定 `"显示文本##ID"`，
不写 `##` 时它拿整个字符串算 widget ID。
译文一变 ID 就变 → docking 布局的 `.ini` 缓存全部错位。
加上 `##` 后 ID 恒定，布局不受影响。

**只替换完整字面量** `"<en>"`，不做子串替换 ——
否则 `"Body"` 会误伤 `"Body "`（尾随空格那个是另一个串）。
带 `##` 的串直接跳过（那是 ID 后缀，改了会出问题）。

**格式串校验**：`%d` 之类的占位符数量必须两边一致，否则直接报错退出
（`"joint %d"` → 译文里丢了 `%d` 会让 `snprintf` 读到垃圾）。

### ⚠️ 23 个标识符刻意不翻

`QPOS` `QVEL` `CTRL` `ACT` `WARMSTART` `EQ_ACTIVE` `PGS` `CG` `Newton`
`RK4` `Dense` `Sparse` `CPU` `FPS` `FOV` …

它们是 **`mjData` 字段名 / 求解器专名 / 单位缩写**，官方文档也用英文。
UI 里它们与旁边的说明标签**配对出现**，脚本只翻标签那一半：

```cpp
{"QPOS", "位置 (Position)##Position"},   // ✅
{"QPOS", "位置 (Position)"},             // ❌ 标识符就丢了
```

清单在 `gen_cpp_patch.py` 的 `NO_TRANSLATE`。

### ⚠️ 硬门槛：必须 clang + libc++

**pybind11 的类型注册表按编译器 ABI 隔离**，key 形如
`__pybind11_internals_v11_<compiler>_<stdlib>_<abi>__`：

| 构建方式 | internals key |
|---|---|
| **官方 `ux.so`** | `..._v11_system_libcpp_abi1__` |
| gcc + libstdc++（❌ 会失败）| `..._v11_system_libstdcpp_gxx_abi_1xxx_use_cxx11_abi_1__` |
| clang + libc++（✅）| `..._v11_system_libcpp_abi1__` |

key 不同 ⇒ 两套注册表 ⇒ `sim.so` 注册的 `StepControl` 认不出来：

```
TypeError: step_control_gui(): incompatible function arguments.
```

**不用编译就能判断的指纹**：官方 `.so` 的 `NEEDED` 里**没有 `libstdc++`** —— 那就是 libc++ 编的：

```bash
readelf -d ux.cpython-*.so | grep NEEDED
```

依赖：`sudo apt-get install -y clang-15 libc++-15-dev libc++abi-15-dev`

### libc++ 必须**静态**链接

官方 `.so` 的 `NEEDED` 里没有 `libc++`/`libc++abi`/`libunwind`，
且未定义 C++ 符号为 0 ⇒ 它把 libc++ 静态链进去了。
动态链的后果是**产物不可移植**：拷到没装 libc++ 的机器上直接跑不起来。

⚠️ **直接加 `-Wl,-Bstatic -lc++` 无效** —— clang 驱动会在命令末尾
把 `-lc++` 重新以动态方式加回来。正确做法是 `-nodefaultlibs` 掐掉默认库列表，
再显式列出静态库和底层 libc：

```bash
clang++-15 -shared -stdlib=libc++ -nodefaultlibs -o ux_zh.so *.o \
  "$SP/libmujoco.so.$VER" "$WEBP_LIB" \
  -Wl,--start-group \
    /usr/lib/llvm-15/lib/libc++.a \
    /usr/lib/llvm-15/lib/libc++abi.a \
    /usr/lib/llvm-15/lib/libunwind.a \
  -Wl,--end-group \
  -lgcc_s -lgcc -lc -Wl,-rpath,'$ORIGIN' -Wl,-z,now -Wl,-z,relro
```

脚本里有自检：若产物仍 `NEEDED` 里带 `libc++`/`libunwind` 会直接报错退出。

> 体积差异正常：官方 1.4 MB（`-O2` + `--gc-sections` + strip），
> 我们 5.4 MB（`-O1` 未 strip）。功能等价。

### 最小构建：13 个 .cc，完全绕开 filament

| 来源 | 文件 |
|---|---|
| `src/experimental/platform/ux/` | `gui.cc` `gui_spec.cc` `imgui_widgets.cc` `interaction.cc` `spec_editor.cc` `plugin.cc` |
| `src/experimental/platform/sim/` | `step_control.cc` `sim_profiler.cc` `model_holder.cc` `sim_history.cc` |
| `src/experimental/platform/` | `sys_utils.cc` `helpers.cc` |
| `python/mujoco/experimental/studio/` | `ux.cc`（pybind 入口）|

**必须排除 2 个**（会拖进 GB 级的 filament）：

- `ux/imgui_bridge.cc` —— 直接 include `<mjrfilament.h>`
- `ux/picture_gui.cc` —— 经 `hal/renderer.h` 间接

排除后 `ux.so` 里 `filament` 命中为 **0**（官方版也是 0）⇒ **不需要 filament、不需要 CMake**，
裸 `clang++` 即可，编译**秒级**。

### 几个坑

| 坑 | 说明 |
|---|---|
| **对象文件名要加前缀** | MuJoCo 的 `imgui_widgets.cc` 与 imgui 库的 `imgui_widgets.cpp` **同名**，后者覆盖前者 → 链接缺 `ImGui_DataPtrTable::DataPtr` |
| imgui 必须用 **pin 的 docking 分支 commit** | 拿 master 会因 `ImGuiCol_DockingEmptyBg` 被删而编译不过 |
| `helpers.cc` 要 webp | 头在 `/home/zhan/anaconda3/include`，库 `/usr/lib/x86_64-linux-gnu/libwebp.so.7` |
| 需要 **C++20** | 用了 `std::bit_cast` |
| abseil **只要头文件** | `structs.h` 需要 `absl/types/span.h`，但 `ux.so` 里 abseil 命中为 0（不链接）|
| `structs.h` 是**源文件** | 在 `python/mujoco/` 里，不是 codegen 产物 |

---

## 三、覆盖边界

`data/ux_strings_audit.json` 里有**两套口径，别混**：

- **`translatable`** —— 平铺字符串清单，**234 条可翻**（211 条已翻 ＋ 23 条刻意保留），跨 3 个源文件
- **`by_panel`** —— 按面板的分布，**只覆盖 `gui.cc`**，共 228 条候选（下面这张表）

按文件看实际翻了什么（`patches/ux-zh.patch`）：

| 源文件 | 不同字符串 | 替换处 |
|---|---|---|
| `ux/gui.cc` | 187 | 212 |
| `ux/gui_spec.cc` | 27 | 27 |
| `sim/sim_profiler.cc` | 12 | 12 |
| **去重合计** | **211** | **251** |

> ⚠️ **187 是 `gui.cc` 单文件的条数，不是总数。**（这个数曾在 README 里被当成总数用，已修正。）
> 251 处 ≠ 211 条，是因为有 15 条字符串在多个文件里各出现一次。

**按面板的分布**（`by_panel`，**仅 `gui.cc`**，18 个面板 / 228 条候选）：

| 面板 | 条数 | 默认可见 |
|---|---|---|
| `visualization_gui` | 71 | 需展开 Visualization |
| `physics_gui` | 46 | 需展开 Physics Settings |
| `state_gui` | 36 | 需展开 State |
| `label_selection_gui` | 18 | ✅ 常驻 |
| `frame_selection_gui` | 9 | ✅ 常驻 |
| `info_gui` | 9 | 需展开 Info |
| `groups_gui` | 7 | 需展开 |
| `counts_gui` | 6 | 需展开 |
| `sensor_gui` | 4 | 需展开 |
| `convergence_gui` | 4 | 需展开 |
| `controls_gui` | 3 | 需展开 |
| `step_control_gui` | 3 | ✅ 常驻（底部控制条）|
| `watch_gui` | 3 | 需展开 |
| `camera_selection_gui` | 2 | ✅ 常驻 |
| `joints_gui` | 2 | 需展开 |
| `noise_gui` | 2 | ✅ 常驻 |
| `rendering_gui` | 2 | ✅ 常驻 |
| `theme_select_gui` | 1 | ✅ 常驻 |

> ⚠️ **这张表只覆盖 `gui.cc`，加起来是 228 不是 234。** `gui_spec.cc`（Elements 面板）
> 与 `sim_profiler.cc`（Profiler 面板）的文字**不在 `by_panel` 里** ——
> 那份扫描是**早期只扫 `*_gui` 函数体**时做的，Profiler 的 12 条在
> 图例辅助函数里、没有 `*_gui` 函数体，所以 `by_panel["ProfilerGui"]` 是空的
> （**但它确实已经翻了**）。
>
> **核对总数请用 `translatable`，不要用这张表。**

### ❌ 真正翻不了的

**渲染标志名**：`Fog` / `Haze` / `Cull Face` / `Id Color` / `Segment` …

它们**不在 `ux.so` 里**，是 **`libmujoco.so` 导出的枚举名**，`ux.so` 运行时取回来显示：

```bash
strings -a libmujoco.so.3.11.0 | grep -x "Cull Face"    # 有
strings -a ux.cpython-*.so | grep -x "Cull Face"        # 没有
```

改 `ux.so` 对它们无效 —— 要覆盖只能连 `libmujoco` 一起处理（不建议，那是 C 库，影响面大）。

判断某个串能不能翻的正确方法：

```bash
grep -ac -- "<串>" ux.cpython-*.so      # ≥1 才说明在 ux.so 里
```

> ⚠️ **别用 `strings | grep`**：`strings` 默认只输出 ASCII，中文串（以 `0xFF` 开头）会被跳过，
> 返回 0 会让人误判「译文没编进去」。

---

## 四、怎么验证译文真的生效

**别靠截图。** 两条理由：

1. **目标面板可能默认折叠** → 截图上什么都看不到 → 误判「改动无效」
   （本项目为此返工了好几轮）
2. **Python 侧探针也看不到** → `ux.*_gui` 不走 Python，探针只能看到
   菜单栏那 5 条（`File` / `Help` / `Charts` …），会得出「翻译失败」的错误结论

### ✅ 正确做法：在 `SectionHeader` 里插桩

`gui.cc` 里所有面板标题都走 `SectionHeader()`，在那儿打印收到的 label：

```cpp
// gui.cc，SectionHeader 函数体开头
if (std::getenv("UX_ZH_TRACE")) std::fprintf(stderr, "[TRACE] %s\n", label);
```

重编译后带上环境变量跑：

```bash
UX_ZH_TRACE=1 python3 -m mujoco_zh.panel_zh <model.xml> 2>&1 | grep TRACE
```

实测输出（**这就是「成功」的样子**）：

```
[TRACE] SectionHeader: 算法参数 (Algorithmic Parameters)##Algorithmic Parameters
[TRACE] SectionHeader: 物理参数 (Physical Parameters)##Physical Parameters
[TRACE] SectionHeader: 接触覆盖 (Contact Override)##Contact Override
[TRACE] SectionHeader: 执行器分组 (Actuator Groups)##Actuator Groups
[TRACE] SectionHeader: 开关 (Flags)##Flags
```

⚠️ 验证完记得把插桩去掉再重编一次（或从源码 tarball 重新解压）。

### 二进制的快速自查（更省事）

```bash
UX=<site-packages>/mujoco/experimental/studio/ux.cpython-*.so
grep -ac -- "算法参数 (Algorithmic Parameters)" "$UX"    # 应为 1
grep -ac -- "UX_ZH_TRACE" "$UX"                          # 应为 0（无残留插桩）
```

⚠️ **必须用 `grep -ac`，别用 `strings | grep`**：
`strings` 默认只输出 ASCII，中文串（以 `0xFF` 开头）会被**整个跳过**，
返回 0 会让你误判「译文没编进去」。

