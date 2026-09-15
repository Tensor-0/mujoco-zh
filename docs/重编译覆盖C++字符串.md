# 覆盖 ux.so 里的 C++ 字符串

> `ux.cpython-*.so` 里有 **210 条**界面文字，Python 层的 monkeypatch 够不着。
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

一条命令：

```bash
./scripts/build_ux_zh.sh            # 编译 + 安装（含 ABI 自检）
./scripts/build_ux_zh.sh --restore  # 还原官方
```

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

`data/ux_strings_audit.json` —— **210 条可翻**，分布在 19 个面板：

| 面板 | 条数 | 默认可见 |
|---|---|---|
| `visualization_gui` | 71 | 需展开 Visualization |
| `physics_gui` | 46 | 需展开 Physics Settings |
| `state_gui` | 38 | 需展开 State |
| `label_selection_gui` | 19 | ✅ 常驻 |
| `frame_selection_gui` | 10 | ✅ 常驻 |
| `info_gui` | 9 | 需展开 Info |
| `groups_gui` / `counts_gui` | 7 / 6 | 需展开 |
| `sensor_gui` / `convergence_gui` / `watch_gui` | 各 4 | 需展开 |
| `step_control_gui` | 3 | ✅ 常驻（底部控制条）|
| `controls_gui` / `joints_gui` | 3 / 2 | 需展开 |
| `camera_selection_gui` / `rendering_gui` / `noise_gui` | 各 2 | ✅ 常驻 |
| `theme_select_gui` | 1 | ✅ 常驻 |

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

## 四、验证的坑

| 坑 | 后果 |
|---|---|
| **靠截图验证** | 若目标面板默认折叠，截图上什么都看不到 → 误判「改动无效」。**N 轮返工都是这么来的** |
| **`strings` 假阴性** | 见上 |

**正确做法**：在代码路径上断言 —— 临时在 `gui.cc` 插桩，打印实际传给 ImGui 的字符串，
比截图可靠得多，也便宜得多（全屏截图一张要上百 k token）。
