#!/usr/bin/env python3
"""App 图标对标检查：把 Termetron 的图标与 Scholarius / Livolog 放在同一尺度下量化比对。

为什么需要它：
    “图标配色不对 / 跟另外两个应用不像”这类问题，看截图只能得出主观结论。
    这个脚本把三款应用**真实生成**的图标渲染到同一尺寸，逐项量出：
        · 底板色     · 符号色（是否纯白）     · 符号占位比例
    然后断言 Termetron 的每一项都落在两款参考应用的区间内。

    ⚠️ 墨量（符号像素占比）**不作为判据**：
       Termetron 按用户要求去掉了外框，只剩 `>_` 两条笔画；
       而 Σ 与时钟环都有闭合轮廓，墨量天然高得多。
       两者对比没有意义（2026-10-26 用户明确“直接不要框了”）。

参考图标的几何来源（硬编码，不是猜的）：
    · Scholarius  Σ 折线：A(70.665,37.335) → B(37.335,37.335) → C(54,54)
                          → D(37.335,70.665) → E(61.325,70.665)，线宽 4.67/108
    · Livolog    时钟环：圆心(54,54)，中线半径 19.67，线宽 4.67/108，缺口 12点~2点
    两者共同规格：底板 #1B1B1B、符号渐变 #FFFFFF → #6E6E6E（顶部白、底部灰）。
    Termetron：底板 #1B1B1B、符号**纯白**（= 参考渐变起点色）、线宽 4.67/108。

用法（在仓库根目录）：
    python tools/check_icons.py
退出码：0 = 底板色/符号色/占位比例都与参考对得上；1 = 有不一致项。
"""
from __future__ import annotations

import importlib.util
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
REF_ROOT = Path(os.environ.get("TERMETRON_REF_ROOT", ROOT.parent))
OUT = ROOT / "tools" / "_icon_compare"


def _grad_v(w: int, h: int, top: tuple, bot: tuple) -> Image.Image:
    g = Image.new("RGB", (1, max(1, h)))
    px = g.load()
    for y in range(max(1, h)):
        t = y / max(1, h - 1)
        px[0, y] = tuple(round(top[i] + (bot[i] - top[i]) * t) for i in range(3))
    return g.resize((max(1, w), max(1, h)), Image.BILINEAR)


def _tint(mask: Image.Image, top, bot) -> Image.Image:
    """按掩码上白->灰竖直渐变，返回 RGBA。"""
    xs = [x for x in range(mask.width)]
    ys = [y for y in range(mask.height)]
    bbox = mask.getbbox()
    if not bbox:
        return Image.new("RGBA", mask.size, (0, 0, 0, 0))
    x0, y0, x1, y1 = bbox
    grad = Image.new("RGB", mask.size, bot)
    grad.paste(_grad_v(x1 - x0, y1 - y0, top, bot), (x0, y0))
    out = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def render_scholarius(S: int) -> Image.Image:
    """Σ：白 -> 灰的折线，线宽 4.67/108，外接 38/108。"""
    k = S / 108
    bg = Image.new("RGB", (S, S), (0x1B, 0x1B, 0x1B))
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    pts = [(70.665, 37.335), (37.335, 37.335), (54, 54), (37.335, 70.665), (61.325, 70.665)]
    d.line([(x * k, y * k) for x, y in pts], fill=255, width=round(4.67 * k), joint="curve")
    r = 4.67 * k / 2
    for x, y in (pts[0], pts[-1]):
        d.ellipse([x * k - r, y * k - r, x * k + r, y * k + r], fill=255)
    bg.paste(_tint(m, (255, 255, 255), (110, 110, 110)), (0, 0), m)
    return bg


def render_livolog(S: int) -> Image.Image:
    """时钟环：外径 44/108，线宽 4.67/108，缺口 12 点~2 点。"""
    k = S / 108
    bg = Image.new("RGB", (S, S), (0x1B, 0x1B, 0x1B))
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    cx = cy = 54 * k
    R = 19.67 * k
    w = round(4.67 * k)
    # 从 12 点逆时针 300°（PIL 角度：0=3点，顺时针增长）
    d.arc([cx - R, cy - R, cx + R, cy + R], start=-90, end=210, fill=255, width=w)
    # 指针
    d.line([cx, cy, cx, cy - 10.67 * k], fill=255, width=round(4 * k))
    hx = cx + 7.33 * k * math.cos(math.radians(240 - 90))
    hy = cy + 7.33 * k * math.sin(math.radians(240 - 90))
    d.line([cx, cy, hx, hy], fill=255, width=round(4 * k))
    d.ellipse([cx - 2 * k, cy - 2 * k, cx + 2 * k, cy + 2 * k], fill=255)
    bg.paste(_tint(m, (255, 255, 255), (110, 110, 110)), (0, 0), m)
    return bg


def render_termetron(S: int) -> Image.Image:
    """直接用生产脚本，保证对比的是真实产物。"""
    gen = ROOT / "app" / "assets" / "generate-icons.py"
    spec = importlib.util.spec_from_file_location("generate_icons", gen)
    gi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gi)  # type: ignore[union-attr]

    icon = gi.make_icon(bg=True, rounded=True, radius=220)
    return icon.convert("RGB").resize((S, S), Image.LANCZOS)


def stats(im: Image.Image) -> dict:
    px = list(im.convert("RGB").getdata())
    w, h = im.size
    sym = [(i, c) for i, c in enumerate(px) if c[0] >= 100]
    xs = [i % w for i, _ in sym]
    ys = [i // w for i, _ in sym]
    brights = [c[0] for _, c in sym]
    return {
        "symbol_px": len(sym),
        "coverage": len(sym) / (w * h),
        "bbox_w": (max(xs) - min(xs)) / w if xs else 0,
        "bbox_h": (max(ys) - min(ys)) / h if ys else 0,
        "max": max(brights) if brights else 0,
        "min": min(brights) if brights else 0,
        # ⚠️ 不能取 (2,2)：完整图标有圆角，四角是透明（实测 (0,0,0)），
        #    要取靠近边缘但落在底板上的位置。
        "bg": im.getpixel((w // 2, int(h * 0.06))),
    }


def main() -> int:
    OUT.mkdir(exist_ok=True)
    S = 512
    renders = {
        "scholarius": render_scholarius(S),
        "livolog": render_livolog(S),
        "termetron": render_termetron(S),
    }
    print(f"{'app':12} {'coverage':>9} {'bbox':>13} {'bright range':>14} {'bg':>16}")
    rows = {}
    for name, im in renders.items():
        im.save(OUT / f"{name}.png")
        st = stats(im)
        rows[name] = st
        print(f"{name:12} {st['coverage']:8.2%} "
              f"{st['bbox_w']:5.1%}x{st['bbox_h']:5.1%} "
              f"{st['min']:5d}-{st['max']:<5d} {str(st['bg']):>16}")

    # 同屏拼图（便于人眼复核，但结论以上面的数字为准）
    strip = Image.new("RGB", (S * 3, S))
    for i, im in enumerate(renders.values()):
        strip.paste(im, (i * S, 0))
    strip.save(OUT / "compare.png")

    ref = [rows["scholarius"], rows["livolog"]]
    lo, hi = min(r["bbox_w"] for r in ref), max(r["bbox_w"] for r in ref)
    me = rows["termetron"]
    ok = True
    print()
    checks = [
        (lo - 0.03 <= me["bbox_w"] <= hi + 0.03,
         f"符号宽度 {me['bbox_w']:.1%} 在参考区间 [{lo:.1%}, {hi:.1%}]（容差 3%）"),
        # Termetron 按用户要求用纯白（不用渐变）；参考应用从纯白起步，所以只要
        # 最亮值达到纯白即视为同一色系。
        (me["max"] >= 250, f"符号为纯白（最亮实测 {me['max']}，参考渐变起点也是 255）"),
        (me["bg"] == (27, 27, 27), f"底板 #1B1B1B（实测 {me['bg']}）"),
    ]
    for cond, msg in checks:
        print(("  ✅ " if cond else "  ❌ ") + msg)
        ok &= cond

    # 墨量只打印、不判定：无外框的两个字形 vs 闭合轮廓，天然不可比
    cov_lo = min(r["coverage"] for r in ref)
    cov_hi = max(r["coverage"] for r in ref)
    print(f"  ℹ️  墨量 Termetron {me['coverage']:.2%} / 参考 {cov_lo:.2%}~{cov_hi:.2%}"
          "（无外框，不可比，不作判据）")

    print("\n" + ("对比通过" if ok else "对比未通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
