"""translate.py —— MuJoCo Studio 官方 UI 中文化（v3）

## 设计来源与取舍

| 层 | 用的什么 | 为什么 |
|---|---|---|
| **翻译资产** | ✅ **GNU gettext**（`.po` → `.mo`）| 调研结论：白拿 Poedit/Weblate 译者工作流、`.po` 的 Git 友好 diff |
| **翻译策略** | ✅ **XUnity 式**（缓存 + 排除规则）| 抄 `bbepis/XUnity.AutoTranslator` ⭐3410 的结构 |
| **拦截层** | ⚠️ **手写 `setattr`**（**不是 wrapt**）| 见下 |

### ⚠️ 为什么没用 wrapt（调研曾建议用）

调研建议用 `wrapt` 替代手写 `setattr`（签名保留 + 可逆卸载）。
**但实测发现 wrapt 在这里不适用**：

```
>>> wrapt.wrap_function_wrapper(ig, 'MenuItem', w)
>>> type(ig.MenuItem)     # 仍是 builtin_function_or_method —— 静默失败！
```

**原因**：`imgui` 模块的属性是 pybind11 的 `builtin_function_or_method`，
**不是普通 Python 函数** —— wrapt 的包装机制对它们不生效（且不报错）。

而手写 `setattr` 是可行的（实测）：
```
>>> type(ig.Text)         # builtin_function_or_method
>>> ig.Text = fake        # ✅ 赋值成功
```

**可逆性**由本模块自己保证（`_originals` 字典 + `uninstall()`）。

### ⚠️ 为什么只包「安全方法」（踩过的坑）

第一版把所有方法都包了，结果 ImGui 报：

```
[imgui-error] Calling End() too many times!
[imgui-error] Calling EndMenu() in wrong window!
```

**根因**：ImGui 的 `Begin*` / `TreeNode*` **严格要求 `if X(): ... EndX()` 配对**，
而且 `MenuItem` / `TreeNode` / `TreeNodeEx` / `BeginTabItem` 是**重载函数**
（`Overloaded function`）—— 包一层会破坏调用约定。

**⇒ 只包「纯文本、无返回值、非重载」的方法**（见 `SAFE_METHODS`）。
代价：`MenuItem` 等标签翻不了 —— 但 `BeginMenu` 能翻，菜单栏已经中文化。

## 用法

```python
from mujoco_zh import translate
translate.install()      # 幂等；必须装在官方 UI 构建之前
```
"""

from __future__ import annotations

import gettext as _gettext
from pathlib import Path
from typing import Any, Callable

# ══════════════════════════════════════════════════════════
# 一、翻译资产（gettext）
# ══════════════════════════════════════════════════════════

DOMAIN = "mujoco_zh"
_LOCALE_DIR = Path(__file__).resolve().parents[2] / "locales"

#: fallback 表 —— 没编译 .mo 时用（开箱可用，免编译步骤）
_FALLBACK: dict[str, str] = {
    "File": "文件", "Help": "帮助", "Charts": "图表", "Simulation": "仿真",
    "Quit": "退出", "Info": "信息", "Solver": "求解器", "Stats": "统计",
    "Inspector": "检查器", "Options": "选项", "StatusBar": "状态栏",
    "ToolBar": "工具栏", "Physics Settings": "物理设置",
    "Rendering Settings": "渲染设置", "Visualization": "可视化",
    "Visibility Groups": "可见性分组", "Controls": "控制", "Joints": "关节",
    "Noise": "噪声", "State": "状态", "Watch": "监视", "Reset": "重置",
}

_translation: Any = None


def _load_translation() -> Any:
    global _translation
    if _translation is None:
        try:
            _translation = _gettext.translation(
                DOMAIN, localedir=str(_LOCALE_DIR), languages=["zh_CN"])
        except FileNotFoundError:
            _translation = _gettext.NullTranslations()
    return _translation


def _is_real_translation(t: Any) -> bool:
    """是否装载到了真正的 .mo。

    ⚠️ **不能用 `isinstance(t, NullTranslations)`** ——
    `GNUTranslations` 是 `NullTranslations` 的**子类**，那个判断恒为 True，
    会让 `.mo` 永远被当成"没装载"而退回 fallback 表
    （实测踩过：214 条译文一条没用上，一直在用 22 条 fallback）。
    """
    return not (type(t) is _gettext.NullTranslations)


def gettext(s: str) -> str:
    t = _load_translation()
    if not _is_real_translation(t):
        return _FALLBACK.get(s, s)
    out = t.gettext(s)
    # .mo 里没有的条目，再查 fallback（两处译文互为补充）
    return out if out != s else _FALLBACK.get(s, s)


# ══════════════════════════════════════════════════════════
# 二、翻译策略（XUnity 式）
# ══════════════════════════════════════════════════════════

#: 排除表（XUnity 的 exclusion list）
EXCLUDE_EXACT: frozenset[str] = frozenset({"##ToolBarTable", "OK", "ID"})

_CJK_LO, _CJK_HI = "一", "鿿"

#: 翻译缓存（XUnity 的 translation cache）—— ImGui 每帧重复调同样 label
#:
#: ⚠️ `tr` 与 `tr_id` 的输出格式不同（后者带 `##ID`），**必须分缓存** ——
#:    共用会让先调用的那个把结果污染给另一个（实测踩过：
#:    `tr_id` 拿到 `tr` 存的「无 ## 」版本，ID 就丢了）。
_cache: dict[str, str] = {}
_cache_id: dict[str, str] = {}


def tr(s: Any) -> Any:
    """翻译一条 UI 字符串 → `中文 (English)`。

    五条排除规则（任一命中即原样返回）—— 这是「不崩」的保障：
      1. 非 str（None / 数字 / 对象）
      2. 空串
      3. 含 `%`   —— 格式串
      4. 含 `##`  —— ImGui ID 后缀
      5. 已含中文 —— 幂等
      另：EXCLUDE_EXACT 里的也跳过

    ⚠️ 这个变体**不带 `##ID`**，只能用于「纯文本」方法（Text/BeginMenu…）。
    凡是**返回 bool 并要和 EndX() 配对**的（TreeNodeEx / CollapsingHeader…），
    必须用 `tr_id()` —— 否则 ImGui 拿译文算 widget ID，
    展开状态会跨会话错乱（见 `tr_id` 的说明）。
    """
    if not isinstance(s, str) or not s or s in EXCLUDE_EXACT:
        return s
    if "%" in s or "##" in s:
        return s
    if any(_CJK_LO <= ch <= _CJK_HI for ch in s):
        return s

    hit = _cache.get(s)
    if hit is not None:
        return hit
    zh = gettext(s)
    result = f"{zh} ({s})" if zh != s else s
    _cache[s] = result
    return result


def tr_id(s: Any) -> Any:
    """翻译成 `中文 (English)##English` —— **保留 ImGui 的 widget ID**。

    ## 为什么必须单独有这个变体

    ImGui 的约定是 `"显示文本##ID"`：没有 `##` 时，它**拿整个字符串算 ID**
    （`GetID(label)`）。`TreeNodeEx` / `CollapsingHeader` 这类函数
    用这个 ID 记住「展开/折叠」状态，而状态会持久化到 `imgui.ini`。

    ⇒ 直接用 `tr()` 的结果（`渲染设置 (Rendering Settings)`）会让 ID 改变，
       **原来展开的面板全变回折叠**，用户手工调好的 docking 布局也一起失效。

    加上 `##English` 后 ID 恒定，界面文字变了但状态不受影响。

    ⚠️ 只能用于「第一个参数是 label、返回 bool、且需与 EndX() 配对」的函数，
    且**包装器必须原样返回它的返回值**（否则会报
    `Calling End() too many times!` / `in wrong window`）。
    """
    if not isinstance(s, str) or not s or s in EXCLUDE_EXACT:
        return s
    if "%" in s or "##" in s:          # 已有 ID 后缀 —— 原样透传，别插第二个
        return s
    if any(_CJK_LO <= ch <= _CJK_HI for ch in s):
        return s

    hit = _cache_id.get(s)
    if hit is not None:
        return hit
    zh = gettext(s)
    result = f"{zh} ({s})##{s}" if zh != s else s
    _cache_id[s] = result
    return result



def clear_cache() -> None:
    _cache.clear()
    _cache_id.clear()


# ══════════════════════════════════════════════════════════
# 三、拦截（手写 setattr + 只包安全方法）
# ══════════════════════════════════════════════════════════

#: ⚠️ 只包这些 —— 全部满足「纯文本 / 无返回值 / 非重载」
#
# 实测排除掉的（包了会报 ImGui 配对错误）：
#   Begin* / TreeNode* / CollapsingHeader / Button / Checkbox / Slider* /
#   RadioButton / Selectable / MenuItem(重载) / TreeNode(重载) / BeginTabItem(重载)
SAFE_METHODS: tuple[str, ...] = (
    "Text", "TextWrapped", "TextDisabled", "SeparatorText", "BulletText",
)

#: 这组返回 bool，但**只要不改返回值**就能安全包（用于翻菜单栏）
#: 实测：`BeginMenu` 不是重载，包它安全
SAFE_BOOL_METHODS: tuple[str, ...] = ("BeginMenu",)

#: ⚠️ 这组返回 bool **且兼作树节点 ID** —— 必须用 `tr_id()` 保留 `##ID`，
#:    否则展开状态跨会话错乱（详见 tr_id 的说明）。
#:
#: `TreeNodeEx` 是重载函数，但官方只用**重载 1**：
#:     TreeNodeEx(label: str, flags=0) -> bool
#: 第一个参数是 str、返回 bool、且**不是** `if X(): ... EndX()` 那种
#: 配对式的（它配对的 `TreePop()` 只在返回 True 的分支里调，由调用方决定）——
#: 包一层只要原样转发返回值就安全。
#:
#: ⚠️ `TreeNode`（不带 Ex）是重载且官方用法不同，**没有**纳入。
SAFE_TREE_METHODS: tuple[str, ...] = ("TreeNodeEx",)

_originals: dict[str, Callable] = {}
_module: Any = None


def _wrap(fn: Callable, translator: Callable[[Any], Any] = tr) -> Callable:
    """包装：翻译第一个位置参数

    ⚠️ **必须原样返回 fn 的返回值** —— ImGui 靠 `if BeginMenu(): ... EndMenu()`
    配对，改动返回值会导致 "EndMenu() in wrong window"。

    `translator` 决定用 `tr`（不带 ID）还是 `tr_id`（保留 `##ID`）。
    """
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if args and isinstance(args[0], str):
            args = (translator(args[0]),) + args[1:]
        return fn(*args, **kwargs)
    wrapper._mujoco_zh_wrapped = True    # type: ignore[attr-defined]
    return wrapper


def install(imgui_module: Any | None = None) -> bool:
    """装上翻译层。**幂等**。

    ⚠️ 必须在官方 UI 构建**之前**调用 ——
    官方 `viewer_app`/`studio_app` 拿到的是**同一个模块对象**，
    在模块属性上打补丁它们立刻生效（已实测确认 `is` 为同一对象）。

    返回 True 表示本次真的装了。
    """
    global _module
    if _originals:
        return False

    if imgui_module is None:
        from mujoco.experimental.dear_imgui import dear_imgui as imgui_module
    _module = imgui_module

    groups = (
        (SAFE_METHODS + SAFE_BOOL_METHODS, tr),
        (SAFE_TREE_METHODS, tr_id),          # 需保留 ##ID
    )
    for names, translator in groups:
        for name in names:
            fn = getattr(imgui_module, name, None)
            if fn is None or not callable(fn):
                continue
            if getattr(fn, "_mujoco_zh_wrapped", False):
                continue                      # 已被包过，不叠加
            _originals[name] = fn
            setattr(imgui_module, name, _wrap(fn, translator))
    return True


def uninstall() -> bool:
    """完整还原"""
    if not _originals:
        return False
    for name, fn in _originals.items():
        setattr(_module, name, fn)
    _originals.clear()
    return True


def is_installed() -> bool:
    return bool(_originals)


def stats() -> dict[str, Any]:
    t = _load_translation()
    return {
        "translations": (len([k for k in getattr(t, "_catalog", {}) if k])
                         if _is_real_translation(t) else len(_FALLBACK)),
        "source": ".mo" if _is_real_translation(t) else "fallback表",
        "wrapped": len(_originals),
        "cached": len(_cache) + len(_cache_id),
    }


# ══════════════════════════════════════════════════════════
# 自检
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=== 配置 ===")
    print(f"  locale 目录: {_LOCALE_DIR}")
    print(f"  .mo 存在:    {(_LOCALE_DIR / 'zh_CN/LC_MESSAGES' / (DOMAIN + '.mo')).exists()}")
    st = stats()
    print(f"  译文来源:    {st['source']}（{st['translations']} 条）")
    if st["source"] != ".mo":
        print("  ⚠️ 没读到 .mo —— 只会有内置 fallback 表那 22 条。"
              "先跑 scripts/compile_mo.sh")
    print()
    print("=== 翻译样例 ===")
    for en in ("File", "Physics Settings", "Inspector", "Joints"):
        print(f"  {en!r:22} → {tr(en)!r}")
    print()
    print("=== 排除规则（应原样返回）===")
    for s in ("", None, "Error: %s", "File##menu", "已经是中文", "##ToolBarTable"):
        r = tr(s)
        print(f"  {str(s)!r:20} → {str(r)!r:20} {'✅' if r == s else '❌'}")
    print()
    print("=== tr_id：给「兼作 ID」的配对函数用（TreeNodeEx…）===")
    print("  必须带 ## 后缀，否则展开状态跨会话错乱")
    for en in ("Rendering Settings", "Visibility Groups"):
        r = tr_id(en)
        ok = "##" in r
        print(f"  {en!r:22} → {r!r}  {'✅' if ok else '❌ 缺 ##ID'}")
    print("  排除规则同样适用：", repr(tr_id("File##menu")), "（已有 ID，不重复插）")
    print()
    print("=== 待包装方法 ===")
    print(f"  纯文本 (tr):     {SAFE_METHODS}")
    print(f"  布尔安全 (tr):   {SAFE_BOOL_METHODS}")
    print(f"  树节点 (tr_id):  {SAFE_TREE_METHODS}")
