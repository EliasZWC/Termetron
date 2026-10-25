"""判定「所有笔画同粗」的正确方法（替换掉早先会误报的版本）。

早先版本误报 51px vs 44px，原因：
    用「横向宽度 × sin(倾角)」反推线宽，隐含假设「横向截面是理想斜切矩形」。
    但 PIL 的 line(width=, joint="curve") 端点是**圆头**，`>` 两段夹角只有 ~72°，
    靠近端点/折点时横向弦长远大于线宽 → 该公式在那里不成立，不能当判据。

现在的判据（4 条，都能独立验证）：
    1. PIL 对同一 width，水平线与垂直线的厚度相同   -> 方向不影响粗细
    2. 54° 斜线中段的竖直厚度 = 线宽 / cos(θ)        -> 斜线仍是同一线宽
    3. 实际图标里 `_`（水平笔画）的竖直厚度 = 线宽   -> 成品实测
    4. 源码里所有笔画都引用同一个 stroke_w           -> 代码事实
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
S = 1024


def load_gen():
    spec = importlib.util.spec_from_file_location(
        "gi", ROOT / "app/assets/generate-icons.py")
    gi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gi)  # type: ignore[union-attr]
    return gi


def _lum(im: Image.Image, x: int, y: int) -> int:
    """取亮度。L 模式返回 int，RGB 返回 tuple —— 两种都要能处理。"""
    v = im.getpixel((x, y))
    return v if isinstance(v, int) else v[0]


def vthick(im: Image.Image, x: int) -> int:
    run = best = 0
    for y in range(im.size[1]):
        if _lum(im, x, y) >= 250:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def hthick(im: Image.Image, y: int) -> int:
    run = best = 0
    for x in range(im.size[0]):
        if _lum(im, x, y) >= 250:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def main() -> int:
    gi = load_gen()
    stroke = round(gi.STROKE_RATIO * S)
    ok = True

    def check(cond: bool, msg: str) -> None:
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + msg)
        ok &= cond

    print(f"名义线宽 stroke_w = {stroke}px（= 4.67/108，与 Scholarius 相同）\n")

    print("== 1) PIL 对同一 width：水平线 vs 垂直线 ==")
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    d.line([(100, 300), (S - 100, 300)], fill=255, width=stroke)
    d.line([(300, 100), (300, S - 100)], fill=255, width=stroke)
    h_flat = vthick(m, S // 2)   # 水平线 -> 竖直厚度
    v_flat = hthick(m, S // 2)   # 垂直线 -> 水平厚度
    print(f"  水平线竖直厚度 {h_flat}px，垂直线水平厚度 {v_flat}px")
    check(abs(h_flat - stroke) <= 1 and abs(v_flat - stroke) <= 1,
          f"两个方向都是 {stroke}px（方向不影响粗细）")

    print("\n== 2) 54° 斜线中段 ==")
    m2 = Image.new("L", (S, S), 0)
    ang = math.radians(54)
    x0, y0 = 200, 200
    ln = 600
    x1, y1 = x0 + ln * math.cos(ang), y0 + ln * math.sin(ang)
    ImageDraw.Draw(m2).line([(x0, y0), (x1, y1)], fill=255, width=stroke)
    mid_x = int((x0 + x1) / 2)
    vseg = vthick(m2, mid_x)
    expected = stroke / math.cos(ang)
    print(f"  中段 x={mid_x} 竖直厚度 {vseg}px，理论 线宽/cos(54°) = {expected:.1f}px")
    check(abs(vseg - expected) <= 3,
          f"斜线竖直厚度 {vseg}px ≈ {expected:.1f}px（仍为同一线宽）")

    print("\n== 3) 实际图标：`_` 的线宽 ==")
    icon = gi.make_icon(bg=True, rounded=False).convert("RGB")
    W, H = icon.size
    best_row, best_len = None, 0
    for y in range(int(H * 0.55), H):
        t = hthick(icon, y)
        if t > best_len:
            best_row, best_len = y, t
    segs, run, start = [], 0, 0
    for x in range(W):
        if _lum(icon, x, best_row) >= 250:
            if run == 0:
                start = x
            run += 1
        else:
            if run:
                segs.append((start, run))
            run = 0
    mid = max(segs, key=lambda t: t[1])
    px = mid[0] + mid[1] // 2
    t = vthick(icon, px)
    print(f"  `_` 所在行 y={best_row}，中点列 x={px}，竖直厚度 {t}px")
    check(abs(t - stroke) <= 2, f"`_` 线宽 {t}px = {stroke}px")

    print("\n== 4) 源码事实：所有笔画共用 stroke_w ==")
    src = (ROOT / "app/assets/generate-icons.py").read_text(encoding="utf-8")
    check("width=stroke_w" in src and "stroke_w / 2" in src,
          "折线/下划线用 width=stroke_w，圆头半径用 stroke_w/2（同一来源）")

    print("\n" + ("所有笔画同粗" if ok else "笔画粗细不一致"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
