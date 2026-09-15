#!/usr/bin/env python3
"""extract_sc_font.py —— 从 TTC 里抽出「简体中文」那一个 face，存成单字体文件。

## 为什么需要这一步

Studio 加载字体走的是 `src/experimental/platform/hal/window.cc`：

    ImFontConfig main_cfg;                       // ← 没有设 FontNo
    io.Fonts->AddFontFromMemoryTTF(data, size, 16.f, &main_cfg);

`ImFontConfig::FontNo` 默认 0，ImGui 内部用 stb_truetype 取 **face 0**。

而 `NotoSansCJK-Regular.ttc` 的 face 顺序是：

    0: Noto Sans CJK JP     ← 被取中的是这个
    1: Noto Sans CJK KR
    2: Noto Sans CJK SC     ← 中文用户想要的是这个
    ...

⇒ 直接把 TTC 拷进去，简体中文用户看到的是**日文字形变体**
（「直」「骨」「者」「令」这类字 JP/SC 写法不同）。

**抽成单 face 文件后，face 0 就是 SC，问题消失。**

## 用法

    python3 extract_sc_font.py <输入字体> <输出路径> [偏好语言]

    偏好语言默认 SC（简体中文），可选 TC / JP / KR。
    输入如果不是 TTC（已经是单字体），直接复制。

## 不用 fontTools 的退化路径

没装 fontTools 时，用 `--allow-copy` 直接复制原文件（会有日文字形变体问题）。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

#: 语言偏好 → 字体族名里应当包含的关键字（按优先级）
LANG_PATTERNS: dict[str, tuple[str, ...]] = {
    "SC": ("Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "CJK SC"),
    "TC": ("Noto Sans CJK TC", "Noto Sans TC", "Source Han Sans TC", "CJK TC"),
    "JP": ("Noto Sans CJK JP", "Noto Sans JP", "Source Han Sans JP", "CJK JP"),
    "KR": ("Noto Sans CJK KR", "Noto Sans KR", "Source Han Sans KR", "CJK KR"),
}


def _family(font) -> str:
    name = font["name"]
    return (name.getDebugName(1) or name.getDebugName(4) or "").strip()


def extract(src: Path, dst: Path, lang: str) -> int:
    """把 src 里的目标 face 写到 dst。返回 0 成功。"""
    from fontTools.ttLib import TTCollection, TTFont  # 延迟导入，便于给出友好报错

    with src.open("rb") as fh:
        magic = fh.read(4)

    # 不是 TTC → 已经是单字体，直接复制
    if magic != b"ttcf":
        shutil.copyfile(src, dst)
        print(f"  · {src.name} 不是 TTC（单字体），直接复制")
        return 0

    collection = TTCollection(str(src))
    pats = LANG_PATTERNS.get(lang.upper(), LANG_PATTERNS["SC"])

    print(f"  TTC 共 {len(collection.fonts)} 个 face：")
    chosen_idx, chosen_family = None, ""
    for i, font in enumerate(collection.fonts):
        fam = _family(font)
        is_mono = "Mono" in fam
        mark = ""
        if not is_mono and chosen_idx is None:
            for p in pats:
                if p in fam:
                    chosen_idx, chosen_family = i, fam
                    mark = "  ⭐ 选中"
                    break
        print(f"    [{i}] {fam or '(无名)'}{mark}")

    if chosen_idx is None:
        print(f"  ⚠️  没找到 {lang} 对应的 face，退回 face 0", file=sys.stderr)
        chosen_idx, chosen_family = 0, _family(collection.fonts[0])

    collection.fonts[chosen_idx].save(str(dst))
    print(f"  ✅ 抽出 face[{chosen_idx}] = {chosen_family}  ({dst.stat().st_size / 1e6:.1f} MB)")
    print(f"     face 0 现在是 {chosen_family} —— Studio 不设 FontNo 也取对")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, help="输入字体（TTC 或单字体）")
    ap.add_argument("dst", type=Path, help="输出路径")
    ap.add_argument("lang", nargs="?", default="SC", help="偏好语言 SC/TC/JP/KR（默认 SC）")
    ap.add_argument("--allow-copy", action="store_true",
                    help="没装 fontTools 时，允许直接复制原文件（会有字形变体问题）")
    args = ap.parse_args()

    if not args.src.is_file():
        print(f"❌ 找不到字体文件: {args.src}", file=sys.stderr)
        return 1

    args.dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        return extract(args.src, args.dst, args.lang)
    except ImportError:
        if args.allow_copy:
            shutil.copyfile(args.src, args.dst)
            print(f"  ⚠️  没装 fontTools，已直接复制（{args.lang} 字形变体问题未解决）")
            print("     修复：pip install fonttools")
            return 0
        print("❌ 需要 fontTools 才能抽取 face：pip install fonttools", file=sys.stderr)
        print("   （或加 --allow-copy 直接复制，但会有日文字形变体问题）", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
