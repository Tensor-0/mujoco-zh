# 为什么用 Studio 而不是官方老 viewer

> **本文是三条路线的实测证据。**
> 结论：**老 viewer 的两条路（加 tooltip / 显示中文）都堵死，二进制 patch 双重死因。**

---

## 一、老 viewer 加不了 tooltip

### 它用的不是 ImGui

```bash
$ nm -D libmujoco.so.3.11.0 | grep mjui_
  mjui_add  mjui_event  mjui_render  mjui_update
```

**自研的 `mjUI`**（C 写的立即模式 UI），实现在 `src/ui/ui_main.c`。
**ImGui 那套 tooltip API 在这里根本不存在。**

### `mjuiItem` 结构体里没有描述字段

读随包发的头文件 `include/mujoco/mjui.h`：

```c
typedef struct mjuiItem_ {
  int type;
  char name[mjMAXUINAME];   // ← 只有 40 字节的显示名
  int state;
  void *pdata;
  int sectionid; int itemid; int userid;
  union { mjuiItemSingle_ single; mjuiItemMulti_ multi;
          mjuiItemSlider_ slider; mjuiItemEdit_ edit; };
  mjrRect rect; int skip;
} mjuiItem;                 // ← 没有任何 description / help / tooltip 字段
```

全仓 grep `help|tip|tooltip|hover|desc` **只命中一行**：

```c
int mousehelp;   // help button down: print shortcuts
```

**⇒ 它唯一的"帮助"是按住右键显示快捷键，不是解释。**

---

## 二、老 viewer 显示不了中文

### 字体只有 128 个字形

反汇编 `makeFont @0x4826f0`：

```asm
48283c: movl $0x80, 0xec00(%rbx)    ← 128 个字形
482846: mov  $0x80, %edi
48284b: call *...glGenLists          ← 生成 128 个 OpenGL display list
```

共 18 个字体 blob（`normal/big × 50/100/150/200/250/300`）。

### 渲染是逐字节查表，**没有掩码**

```asm
488f7e: movzbl (%rbx,%rdx,1), %r8d     ← 原始字节，未 & 0x7f
488fa0: add 0xee24(%r9,%r8,4), %esi
```

**UTF-8 汉字首字节 `0xE4=228` 越界** → `glCallList(base+228)`
落到**相邻字体的 display list** 上 → 画出别的字号图集里的错字。

### 实测渲染结果（逐像素差分）

| 输入 | 像素数 | bbox 高 |
|---|---|---|
| ASCII `abcdef` | 339 | 14 px |
| 中文 `中文测试` | **1173** | **19 px** ← 画出来了，但是垃圾 |

**⇒ 不是空白，是错字。而且 `mjr_getError()` 返回 0，静默画错。**

**`mjr_changeFont()` 只能在 6 档字号间切换，不能换字形集。**

---

## 三、二进制 patch：能改，但没用

技术可行性其实是**成立的**：

| 项 | 实测值 |
|---|---|
| UI 标签 | **254 条**，340 字节定长槽 |
| 可用空间 | 最小 3 / **中位 39** / 最大 303 字节 |
| 重定位 | **0 条**（编译器按值内联拷贝：`movups` SSE 整块搬）|

**⇒ 中位 39 字节 ≈ 13 个汉字，长度不是瓶颈。**

**但双重死因**：

1. **加不了 tooltip** —— `mjuiItem` 没描述字段，代码没 hover 文本机制
2. **中文画不出来** —— 128 字形（见上）

**⇒ 改了也白改。**

---

## 四、Studio 为什么行

官方自己的对比表（`doc/skills/studio/SKILL.md`）：

| Feature | Simulate（legacy） | **Studio** |
|---|---|---|
| GUI Framework | Custom fixed MuJoCo UI (`mjui`) | **Dear ImGui** |
| Extensibility | **Hardcoded UI panels and keybinds** | **Plugin Architecture**（C++ 和 Python）|

### 三件套全有

| 需要什么 | Studio 有吗 | 证据 |
|---|---|---|
| **声明式 UI** | ✅ Dear ImGui 1.92.6 | 二进制 strings |
| **tooltip 机制** | ✅ **官方已在用** | `src/experimental/studio/ux/gui.cc:621,669,1062,1088` 有 `SetItemTooltip` |
| **可替换 CJK 字体** | ✅ 运行时从磁盘加载 TTF | `fonts.h` 的 `kMainFontFile` + `assets/*.ttf` |

### Python 钩子实测跑通

```python
@messages.handler
def on_build_gui(self, _: messages.BuildGuiEvent) -> None:
    if imgui.Begin("中文面板"):
        imgui.Text("时间步长")
        if imgui.IsItemHovered():
            imgui.SetItemTooltip("仿真步长：越小越精确，但越慢")
    imgui.End()

with launch_passive.launch_passive(config, viewer_handlers=[Panel()]) as h:
    h.send_to_viewer(messages.ModelEvent(model=model))
    ...
```

**实测：跑了 1056868 帧，中文 + tooltip 全部正常。**

---

## 五、前人怎么做的（跨领域参考）

| 软件 | i18n 方案 | tooltip 教学 |
|---|---|---|
| **Blender** | GNU gettext + 专用宏 `TIP_()` | ⭐ **标杆**：tooltip 内容来自数据模型（RNA 描述），不硬编码 |
| FreeCAD | Crowdin + `Std_WhatsThis` | ⚠️ "这是什么"藏太深，issue 挂一年多没解决 |
| ParaView | Qt `.ts/.qm` | 普通 tooltip |
| OpenSCAD / Fritzing | gettext / Qt | 无教学功能 |
| **Gazebo / PyBullet / Isaac** | **全都没做** | — |

**三条规律**：

1. **「教学文案」必须是数据，不能是代码** —— Blender 做透了这条
2. **i18n 只有两条活路**：GNU gettext 或 Qt `.ts/.qm`（都要官方翻译平台 + 上游化仓库）
3. **CJK 是字体问题，不是翻译问题** —— 凡是字体机制不给换的项目（老 `simulate`），汉化就没做成过

---

## 结论

> **前人做成的（Blender）都是在「声明式 UI + 数据驱动的解释文案 + 可替换 CJK 字体」三件套齐全时才做成的。**
> **老 `simulate` 三件全缺，Studio 三件全有。**
