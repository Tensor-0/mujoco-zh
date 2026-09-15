"""tooltips.py —— 控件中文名 + 悬停解释（纯数据层）

⭐ **这个文件是整个项目的核心资产。**
它与 UI 框架完全解耦：换 PyQt / Web / ImGui 都能复用。

## 设计原则

1. **解释写「功能」，不写「原理」**
   - ✅ 「调大 → 关节变慢、更稳」
   - ❌ 「基于粘性阻尼系数的力矩-速度关系」

2. **每条解释都回答三个问题**
   - 这是什么？
   - 调大/调小、打开/关闭会看到什么变化？
   - 什么时候该动它？

3. **键名 = MuJoCo 官方字段名** —— 便于和上游对齐、便于查证
"""

from dataclasses import dataclass, field


@dataclass
class Tip:
    """一个控件的完整说明"""
    zh: str                       # 中文名
    en: str                       # 英文原名（与官方一致）
    what: str                     # 一句话：这是什么
    effects: list[str] = field(default_factory=list)   # 调大/调小会怎样
    when: str = ""                # 什么时候该动它


# ══════════════════════════════════════════════════════════
# 一、可视化开关（对应 MjvOption.flags）
# ══════════════════════════════════════════════════════════

VIS_FLAGS: dict[str, Tip] = {
    "mjVIS_CONVEXHULL": Tip(
        zh="凸包轮廓", en="Convex Hull",
        what="给每个几何体画出它的凸包边界线。",
        effects=[
            "打开 → 能看到碰撞体的真实形状（常比可视模型简单）",
            "关闭 → 只显示外观模型",
        ],
        when="怀疑碰撞体和看到的不一致时打开。",
    ),
    "mjVIS_TEXTURE": Tip(
        zh="纹理贴图", en="Texture",
        what="是否给几何体贴上图。",
        effects=[
            "打开 → 看到材质、图案（更真实）",
            "关闭 → 全是灰白纯色（看得更清楚结构）",
        ],
        when="看不清形状、或嫌画面太花时关掉。",
    ),
    "mjVIS_JOINT": Tip(
        zh="关节轴", en="Joint",
        what="画出每个关节的旋转轴（一根线）。",
        effects=[
            "打开 → 看到关节往哪转、绕什么轴",
            "关闭 → 画面更干净",
        ],
        when="⭐ 新手最该开的开关之一 —— 一眼看懂每个关节的自由度。",
    ),
    "mjVIS_ACTUATOR": Tip(
        zh="执行器", en="Actuator",
        what="画出执行器（电机）作用的方向和大小。",
        effects=[
            "打开 → 看到每个电机在往哪使劲",
            "力越大 → 箭头越长",
        ],
        when="怀疑某个电机在使反劲时打开。",
    ),
    "mjVIS_CONTACTFORCE": Tip(
        zh="接触力", en="Contact Force",
        what="画出脚（或任何部位）和地面的接触力箭头。",
        effects=[
            "打开 → 看到哪里在受力、多大力",
            "没箭头 → 那个地方没接触",
        ],
        when="⭐ 检查「脚到底踩实了没」时必开。",
    ),
    "mjVIS_CONTACTPOINT": Tip(
        zh="接触点", en="Contact Point",
        what="标出所有正在接触的点。",
        effects=[
            "打开 → 出现小方块 = 接触位置",
            "脚该落地却没有 → 说明还没踩上",
        ],
        when="和「接触力」一起用，判断支撑状态。",
    ),
    "mjVIS_TRANSPARENT": Tip(
        zh="半透明", en="Transparent",
        what="让几何体变成半透明。",
        effects=[
            "打开 → 能看到内部的关节和结构",
            "关闭 → 正常实心显示",
        ],
        when="看内部结构、或机器人挡住视线时打开。",
    ),
    "mjVIS_AUTOCONNECT": Tip(
        zh="自动连线", en="Auto Connect",
        what="把相邻刚体的质心连起来。",
        effects=[
            "打开 → 看出运动学的父子关系",
            "关闭 → 没有这些线",
        ],
        when="想理解刚体树结构时打开。",
    ),
    "mjVIS_COM": Tip(
        zh="质心", en="Center of Mass",
        what="标出每个刚体的质心位置。",
        effects=[
            "打开 → 出现质心标记",
            "整机质心 → 常用来判断平衡",
        ],
        when="⭐ 调平衡、加配重时必看。",
    ),
    "mjVIS_INERTIA": Tip(
        zh="惯量主轴", en="Inertia",
        what="画出每个刚体的转动惯量主轴（三个方向的椭球/线）。",
        effects=[
            "打开 → 看到物体「转起来顺」的方向",
            "关闭 → 不显示",
        ],
        when="怀疑模型惯量填错时打开。",
    ),
    "mjVIS_PERTFORCE": Tip(
        zh="扰动力", en="Perturb Force",
        what="显示你手动施加的扰动力箭头。",
        effects=[
            "Ctrl+右键拖 → 施力，这里能看到方向和大小",
        ],
        when="测试抗扰动能力时用。",
    ),
    "mjVIS_PERTOBJ": Tip(
        zh="扰动作用点", en="Perturb Object",
        what="高亮当前被施加扰动力的物体。",
        effects=["打开 → 施力时目标会高亮"],
        when="同时推多个物体、怕搞混时用。",
    ),
    "mjVIS_SELECT": Tip(
        zh="选中高亮", en="Selection",
        what="高亮当前选中的几何体。",
        effects=["双击某个部位 → 它会被高亮，这里控制高亮样式"],
        when="一般保持默认。",
    ),
    "mjVIS_SKIN": Tip(
        zh="柔性皮肤", en="Skin",
        what="显示柔性体（skin）网格。",
        effects=["本项目用不到（无柔性体）"],
        when="——",
    ),
    "mjVIS_FLEX": Tip(
        zh="柔性体", en="Flex",
        what="显示柔性体（可变形物体）。",
        effects=["本项目用不到"],
        when="——",
    ),
}


# ══════════════════════════════════════════════════════════
# 二、渲染选项
# ══════════════════════════════════════════════════════════

REND_FLAGS: dict[str, Tip] = {
    "mjRND_SHADOW": Tip(
        zh="阴影", en="Shadow",
        what="是否渲染阴影。",
        effects=[
            "打开 → 有影子，判断高度更直观",
            "关闭 → 画面更快，但没有空间感",
        ],
        when="觉得看不出脚离地多高时打开。",
    ),
    "mjRND_WIREFRAME": Tip(
        zh="线框", en="Wireframe",
        what="把几何体画成线框。",
        effects=[
            "打开 → 看到网格结构",
            "关闭 → 正常实体",
        ],
        when="看网格密度、或调试渲染问题时用。",
    ),
    "mjRND_HAZE": Tip(
        zh="雾效", en="Haze",
        what="远处加雾，增强纵深感。",
        effects=["打开 → 远景变淡", "关闭 → 全清晰"],
        when="一般保持默认。",
    ),
    "mjRND_SKYBOX": Tip(
        zh="天空盒", en="Skybox",
        what="背景是否绘制天空/渐变。",
        effects=[
            "打开 → 有背景色",
            "关闭 → 黑/白背景（截图更干净）",
        ],
        when="要出图给别人看时，关掉可能更清爽。",
    ),
    "mjRND_FOG": Tip(
        zh="雾", en="Fog",
        what="更明确的雾效开关。",
        effects=["同「雾效」"],
        when="——",
    ),
    "mjRND_REFLECTION": Tip(
        zh="地面反射", en="Reflection",
        what="地面是否反射机器人。",
        effects=[
            "打开 → 有倒影（更好看，更慢）",
            "关闭 → 无倒影",
        ],
        when="要好看的效果时打开；平时关掉省性能。",
    ),
    "mjRND_SEGMENT": Tip(
        zh="分割着色", en="Segmentation",
        what="每个物体用不同纯色渲染（不是真实颜色）。",
        effects=[
            "打开 → 一眼区分不同刚体/几何",
            "关闭 → 正常渲染",
        ],
        when="⭐ 想数清「这到底有几个部件」时打开。",
    ),
    "mjRND_LABEL": Tip(
        zh="标签", en="Label",
        what="在画面上显示文字标签。",
        effects=[
            "打开 → 显示刚体名/关节名等",
            "关闭 → 不显示",
        ],
        when="调试「到底哪个是哪个」时用。",
    ),
}


# ══════════════════════════════════════════════════════════
# 三、物理参数（对应 mjOption）—— ⭐ 新手最需要解释的一类
# ══════════════════════════════════════════════════════════

PHYSICS: dict[str, Tip] = {
    "timestep": Tip(
        zh="仿真步长", en="Timestep",
        what="每次物理计算往前推进多少秒。",
        effects=[
            "调小 → 更精确、更稳定，但一步算得慢",
            "调大 → 快，但可能「穿透」「抖动」「爆炸」",
            "经验值：机器人仿真常用 0.001~0.005 秒",
        ],
        when="⭐ 看到关节抽搐、物体穿透时，先调小它试试。",
    ),
    "gravity": Tip(
        zh="重力", en="Gravity",
        what="三个方向的重力加速度（世界坐标系）。",
        effects=[
            "默认 (0, 0, -9.81) = 地球重力向下",
            "设为 (0, 0, 0) → 失重，机器人会飘",
            "改 x/y 分量 → 相当于地面倾斜",
        ],
        when="做太空场景、或想先排除重力影响调别的东西时用。",
    ),
    "iterations": Tip(
        zh="求解器迭代次数", en="Iterations",
        what="每次步进时，求解接触力算几轮。",
        effects=[
            "调大 → 接触更准（脚不打滑、不穿透），但更慢",
            "调小 → 快，但接触会「软」、可能抖动",
            "经验值：10~100",
        ],
        when="脚底接触不稳、机器人「陷进地面」时调大。",
    ),
    "tolerance": Tip(
        zh="求解器容差", en="Tolerance",
        what="迭代到什么程度算「算够了」。",
        effects=[
            "调小 → 要求更严，可能提前停（也可能更慢）",
            "调大 → 更早停，快但粗",
        ],
        when="一般不动它，先调「迭代次数」。",
    ),
    "solver": Tip(
        zh="求解器类型", en="Solver",
        what="用哪种算法算接触力和约束。",
        effects=[
            "PGS → 默认，通用、稳",
            "CG / Newton → 某些场景更快",
        ],
        when="⭐ 一般不用动。除非遇到特定数值问题。",
    ),
    "integrator": Tip(
        zh="积分器", en="Integrator",
        what="用什么方法把力变成运动。",
        effects=[
            "Euler → 默认，快",
            "RK4 → 更准，但慢 2~4 倍",
            "implicit → 适合弹簧/柔性",
        ],
        when="仿真「发散」「爆炸」时，试试 RK4 或 implicit。",
    ),
    "cone": Tip(
        zh="摩擦锥模型", en="Cone",
        what="怎么近似摩擦力的边界形状。",
        effects=[
            "pyramidal → 默认，快，但各方向摩擦不均",
            "elliptic → 更准（更接近真实摩擦），但慢",
        ],
        when="⭐ 训练机器人走路时，elliptic 能让「打滑」更真实。",
    ),
    "impratio": Tip(
        zh="阻抗比", en="Imp Ratio",
        what="控制「挤压力」和「切向力」的相对权重。",
        effects=[
            "调大 → 物体更难被挤穿（更硬）",
            "调小 → 更容易穿透",
        ],
        when="物体互相穿透时调大。",
    ),
    "friction": Tip(
        zh="默认摩擦系数", en="Friction",
        what="没单独设置时，所有几何体用的摩擦系数。",
        effects=[
            "调大 → 更「粘」，脚不容易滑",
            "调小 → 更「滑」，像踩冰",
        ],
        when="⭐ 想测「打滑」行为时，调小它。",
    ),
    "density": Tip(
        zh="默认密度", en="Density",
        what="没单独设置质量时，按体积 × 密度算质量。",
        effects=[
            "调大 → 所有物体变重",
            "调小 → 变轻",
        ],
        when="一般不动（模型里有明确质量）。",
    ),
    "viscosity": Tip(
        zh="粘性", en="Viscosity",
        what="空气/流体的粘滞阻力。",
        effects=[
            "调大 → 运动有明显阻力（像在糖浆里）",
            "0 → 无阻力",
        ],
        when="本项目一般保持 0。",
    ),
    "wind": Tip(
        zh="风", en="Wind",
        what="全局恒定风力。",
        effects=[
            "非零 → 机器人被持续吹",
        ],
        when="测试抗风扰动用。",
    ),
}


# ══════════════════════════════════════════════════════════
# 四、仿真控制
# ══════════════════════════════════════════════════════════

SIM_CONTROL: dict[str, Tip] = {
    "pause": Tip(
        zh="暂停", en="Pause",
        what="冻结物理计算。",
        effects=[
            "暂停 → 画面静止，但还能转视角、改参数",
            "继续 → 仿真接着跑",
        ],
        when="⭐ 想「定格」看清某个姿势时用。",
    ),
    "reset": Tip(
        zh="重置", en="Reset",
        what="把机器人恢复到初始状态（时间归零、关节回位）。",
        effects=[
            "点击 → 回到 home 姿势，速度清零",
            "⚠️ 不会改变你的参数设置",
        ],
        when="每次实验开始前点一下，保证起点一致。",
    ),
    "step": Tip(
        zh="单步", en="Step",
        what="只往前算一步（一个 timestep）。",
        effects=[
            "点一次 → 前进 0.001 秒（按 timestep 设定）",
            "可以疯狂点 → 慢动作观察",
        ],
        when="⭐ 想看「这一步到底发生了什么」时用。",
    ),
    "speed": Tip(
        zh="实时倍速", en="Speed",
        what="仿真跑多快（相对于真实时间）。",
        effects=[
            "1.0 → 实时",
            "0.5 → 半速（慢动作）",
            "2.0 → 两倍速",
        ],
        when="慢动作看细节，或快进看长期行为。",
    ),
}


# ══════════════════════════════════════════════════════════
# 五、显示分组（Group）
# ══════════════════════════════════════════════════════════

GROUPS: dict[str, Tip] = {
    "geomgroup": Tip(
        zh="几何体分组可见性", en="Geom Groups",
        what="按「组号」批量显示/隐藏几何体（共 6 组，0~5）。",
        effects=[
            "勾选 → 显示该组的几何体",
            "取消 → 隐藏",
            "⭐ 模型作者可以把「装饰」「碰撞体」分到不同组",
        ],
        when="⭐ 只想看碰撞体、或嫌装饰太多时用。",
    ),
    "sitegroup": Tip(
        zh="标记点分组可见性", en="Site Groups",
        what="按组显示/隐藏 site（标记点，如 IMU 位置、足端）。",
        effects=[
            "勾选 → 显示对应组的 site",
            "⭐ site 常用来标记「关键位置」",
        ],
        when="想找 IMU 装在哪、足端在哪时打开。",
    ),
    "jointgroup": Tip(
        zh="关节分组可见性", en="Joint Groups",
        what="按组显示/隐藏关节轴。",
        effects=["配合上面的「关节轴」开关一起用"],
        when="关节太多看花眼时，分组显示。",
    ),
    "actuatorgroup": Tip(
        zh="执行器分组可见性", en="Actuator Groups",
        what="按组显示/隐藏执行器。",
        effects=["配合「执行器」开关一起用"],
        when="机器上有多种电机时，分组看。",
    ),
    "tendongroup": Tip(
        zh="肌腱分组可见性", en="Tendon Groups",
        what="按组显示/隐藏 tendon（肌腱/传动）。",
        effects=["本项目用不到（无法驱动）"],
        when="——",
    ),
    "flexgroup": Tip(
        zh="柔性体分组可见性", en="Flex Groups",
        what="按组显示/隐藏柔性体。",
        effects=["本项目用不到"],
        when="——",
    ),
}


# ══════════════════════════════════════════════════════════
# 六、相机
# ══════════════════════════════════════════════════════════

CAMERA: dict[str, Tip] = {
    "type": Tip(
        zh="相机模式", en="Camera",
        what="视角怎么跟着机器人动。",
        effects=[
            "Free（自由）→ 完全手动，机器人跑了要自己追",
            "Tracking（跟随）→ 镜头跟着某个刚体（⭐ 常用，选 torso）",
            "Fixed（固定）→ 锁在某个预设机位",
        ],
        when="⭐ 机器人走动时用 Tracking 跟住它；静止时 Free 随便看。",
    ),
    "trackbody": Tip(
        zh="跟随目标", en="Tracking",
        what="Tracking 模式下，镜头跟着哪个刚体。",
        effects=[
            "选 torso/base_link → 跟着躯干",
            "选 foot → 跟着脚（看步态很方便）",
        ],
        when="⭐ 观察步态时，跟脚比跟躯干更能看清落地。",
    ),
    "distance": Tip(
        zh="距离", en="Distance",
        what="相机离目标多远。",
        effects=["调大 → 拉远看全身", "调小 → 拉近看细节"],
        when="——（也可以直接滚轮）",
    ),
    "azimuth": Tip(
        zh="方位角", en="Azimuth",
        what="相机绕竖直轴转的角度（左右环绕）。",
        effects=["改这个 → 从正面/侧面/背面看"],
        when="⭐ 看走路时，从侧面看最能看清步态。",
    ),
    "elevation": Tip(
        zh="仰角", en="Elevation",
        what="相机的高低角度。",
        effects=[
            "0 → 平视",
            "正值 → 从上往下俯视",
            "负值 → 从下往上仰视",
        ],
        when="俯视看落点，平视看姿态。",
    ),
    "orthographic": Tip(
        zh="正交投影", en="Orthographic",
        what="关掉透视效果（平行投影）。",
        effects=[
            "打开 → 没有「近大远小」，尺寸更可量",
            "关闭 → 正常透视",
        ],
        when="⭐ 想比较两个姿势的几何差异时打开。",
    ),
    "lookat": Tip(
        zh="注视点", en="Lookat",
        what="相机盯着世界坐标里的哪个点。",
        effects=[
            "默认 (0,0,0.4) → 机器人腰部附近",
            "走路时机器人跑远了 → 图像会偏",
        ],
        when="机器人跑出画面时，回到 Free 模式，或用 Tracking。",
    ),
}


# ══════════════════════════════════════════════════════════
# 统一查询接口
# ══════════════════════════════════════════════════════════

ALL_TABLES = {
    "vis_flags": VIS_FLAGS,
    "rend_flags": REND_FLAGS,
    "physics": PHYSICS,
    "sim_control": SIM_CONTROL,
    "groups": GROUPS,
    "camera": CAMERA,
}


def get(key: str) -> Tip | None:
    """按 key 查（跨所有表）"""
    for tbl in ALL_TABLES.values():
        if key in tbl:
            return tbl[key]
    return None


def tip_text(key: str) -> str:
    """生成 tooltip 的完整文本（ImGui 用的换行格式）"""
    t = get(key)
    if not t:
        return ""
    lines = [f"{t.zh}  ({t.en})", "─" * 28, t.what, ""]
    if t.effects:
        lines.append("调整效果：")
        lines += [f"  · {e}" for e in t.effects]
    if t.when and t.when != "——":
        lines += ["", f"💡 {t.when}"]
    return "\n".join(lines)


def stats() -> dict:
    return {name: len(tbl) for name, tbl in ALL_TABLES.items()}


if __name__ == "__main__":
    import sys
    s = stats()
    print(f"共 {sum(s.values())} 条文案")
    for k, v in s.items():
        print(f"  {k:14} {v:3} 条")
    print()
    if len(sys.argv) > 1:
        print(tip_text(sys.argv[1]))
