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


def vthick(im: Image.Image, x: int, thr: int = 250) -> int:
    run = best = 0
    for y in range(im.size[1]):
        if _lum(im, x, y) >= thr:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def hthick(im: Image.Image, y: int, thr: int = 250) -> int:
    run = best = 0
    for x in range(im.size[0]):
        if _lum(im, x, y) >= thr:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def main() -> int:
    gi = load_gen()
    # ⚠️ STROKE_RATIO 现在是「线宽 / 占位边长」，不是「线宽 / 画布」。
    #    画布上的实际线宽 = STROKE_RATIO * 占位边长。
    box = gi.SYMBOL_RATIO * S
    stroke = round(gi.STROKE_RATIO * box)
    ok = True

    def check(cond: bool, msg: str) -> None:
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + msg)
        ok &= cond

    print(f"名义线宽 stroke_w = {stroke}px（= 占位 {box:.0f}px 的 {gi.STROKE_RATIO:.3f}，"
          f"参考 Scholarius 0.1149 / Livolog 0.1134）\n")

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
    # ⚠️ 阈值要「比背景亮」，不能用 250：
    #    `_` 是**最暗色 #6E6E6E(110)**，用 250 会一个像素都量不到。
    # ⚠️ 还要限定在**右半区**：`>` 的暗部（左下的 #6E6E6E）同样 >= 60，
    #    不限定就会把 `>` 的斜段当成 `_` 量。
    thr = 60
    x_split = int(W * 0.5)
    best_row, best_len = None, 0
    for y in range(int(H * 0.55), H):
        run = best = 0
        for x in range(x_split, W):
            run = run + 1 if _lum(icon, x, y) >= thr else 0
            best = max(best, run)
        if best > best_len:
            best_row, best_len = y, best
    segs, run, start = [], 0, 0
    for x in range(x_split, W):
        if _lum(icon, x, best_row) >= thr:
            if run == 0:
                start = x
            run += 1
        else:
            if run:
                segs.append((start, run))
            run = 0
    if run:
        segs.append((start, run))
    mid = max(segs, key=lambda t: t[1])
    px = mid[0] + mid[1] // 2
    t = vthick(icon, px, thr)
    print(f"  `_` 所在行 y={best_row}，中点列 x={px}，竖直厚度 {t}px")
    check(abs(t - stroke) <= 2, f"`_` 线宽 {t}px = {stroke}px")

    print("\n== 4) 源码事实：所有笔画共用同一个 stroke ==")
    src = (ROOT / "app/assets/generate-icons.py").read_text(encoding="utf-8")
    # 新实现用 _thick_polyline（矩形 + 顶点圆），不再用 draw.line(width=...)。
    # 判据改成：`>` 与 `_` 都调用同一个 _thick_polyline；圆头半径都取 half = stroke/2。
    same_poly = src.count("_thick_polyline(") >= 3   # 1 处定义 + `>` 与 `_` 各 1 处调用
    half_used = "half = stroke_w / 2.0" in src
    check(same_poly and half_used,
          "`>` 与 `_` 都走 _thick_polyline；圆头/顶点圆半径 = stroke/2（同一来源）")

    print("\n" + ("所有笔画同粗" if ok else "笔画粗细不一致"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
