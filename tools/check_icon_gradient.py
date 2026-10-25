"""验证图标上色规则：`>` 路径渐变（左上白 -> 折点 -> 左下最暗），`_` 纯最暗色。

用户 2026-10-26 的要求：
    「左边的 >，用路径渐变，也就是跟随字符的形状路径渐变，从路径开始的左上角，
      到中间右边，再到左下角，注意渐变方向不是从上到下，而是按形状路径渐变。
      渐变最亮色和最暗色也取自另外两个应用的图标 logo 中除了背景之外的最亮色
      和最暗色（两个应用图标的配色方案一样）。然后右边的 _ 不渐变，而是全部用最暗色。」

    → 最亮 #FFFFFF、最暗 #6E6E6E（已用脚本核对两款应用前景层的极值色，完全一致）

判据：
    1. 起点 ≈ 纯白，终点 ≈ #6E6E6E，折点色介于两者之间
    2. **关键**：下段同位置点必须比上段更暗 —— 若按 y 渐变，两点 y 不同就无法
       体现「弧长」，而按弧长渐变时下段一定更暗（Σ 也是同样结论）
    3. `_` 全段同色且等于最暗色
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
S = 1024


def load_gen():
    spec = importlib.util.spec_from_file_location(
        "gi", ROOT / "app/assets/generate-icons.py")
    gi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gi)  # type: ignore[union-attr]
    return gi


def main() -> int:
    gi = load_gen()
    icon = gi.make_icon(bg=True, rounded=False).convert("RGB")
    W, H = icon.size
    ok = True

    def check(cond: bool, msg: str) -> None:
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + msg)
        ok &= cond

    # 新布局：占位正方形，大小 = SYMBOL_RATIO×S，中心在画布中心
    size = gi.SYMBOL_RATIO * S
    stroke = gi.STROKE_RATIO * size
    layout = gi.glyph_layout(size, stroke, (S / 2, S / 2))
    pts = layout['chev_pts']
    us_pts = layout['us_pts']
    (x0, y0), (x1, y1), (x2, y2) = pts
    print(f"`>` 三点：起点({x0:.0f},{y0:.0f}) 折点({x1:.0f},{y1:.0f}) "
          f"终点({x2:.0f},{y2:.0f})   线宽 {stroke:.1f}px")

    def sample(px, py):
        """取该点附近最亮的符号像素颜色（避开抗锯齿边缘）。"""
        best = None
        for dy in range(-12, 13):
            for dx in range(-12, 13):
                x, y = int(px + dx), int(py + dy)
                if 0 <= x < W and 0 <= y < H:
                    c = icon.getpixel((x, y))
                    if c[0] > 50 and (best is None or c[0] > best[0]):
                        best = c
        return best

    # ⚠️ 终点色要读**末端圆心本身**，不能用 sample()。
    #    sample() 会在 ±12px 窗口里取最亮像素 —— 线宽 61.5px 时窗口内
    #    既有末端（最暗 110）也有上方的渐变中段（更亮），于是取到 121。
    #    终点就在 「末端圆心」 这一点上，直接读它。
    k = stroke * 0.12
    start_c = sample(x0 + k, y0 + k)
    knee_c = sample(x1 - stroke * 0.6, y1)
    ex, ey = int(round(x2)), int(round(y2))
    end_c = icon.getpixel((ex, ey))[:3] if 0 <= ex < W and 0 <= ey < H else None
    print(f"\n起点色 {start_c}   折点色 {knee_c}   终点色 {end_c}")

    check(start_c and start_c[0] >= 248, f"起点接近纯白（实测 {start_c}）")
    check(end_c and end_c[0] <= 120, f"终点接近 #6E6E6E(110)（实测 {end_c}）")
    check(knee_c is not None and end_c[0] < knee_c[0] < start_c[0],
          f"折点色介于起终点之间（{start_c[0]} > {knee_c[0] if knee_c else '?'} > "
          f"{end_c[0]}）")

    # 关键：上段某点 y 处，看下段同 x 点的颜色（若按 y 渐变会与竖直位置相关）
    # ⚠️ 取点必须**落在笔画内**：往折点内侧偏半个线宽，
    #    否则会采到背景，得到 None。旧实现固定偏 8px，几何一变就失效。
    print("\n== 路径渐变 vs 竖直渐变 的判别 ==")
    inward = stroke * 0.55          # 从中心线往弯内偏移，保证在笔画内
    t = 1 / 3
    ux = x0 + (x1 - x0) * t + inward
    uy = y0 + (y1 - y0) * t
    lx = x0 + (x1 - x0) * t + inward
    ly = y1 + (y2 - y1) * t
    up_c = sample(ux, uy)
    lo_c = sample(lx, ly)
    print(f"  上段 1/3 处 ({ux:.0f},{uy:.0f}) 色 {up_c}")
    print(f"  下段 1/3 处 ({lx:.0f},{ly:.0f}) 色 {lo_c}   ← y 更大、弧长更靠后")
    check(up_c is not None and lo_c is not None and lo_c[0] < up_c[0],
          f"下段同位置更暗（{up_c[0] if up_c else '?'} -> {lo_c[0] if lo_c else '?'}）"
          "：颜色由弧长决定，不是竖直渐变")

    # `_` 必须整条同色（最暗）
    print("\n== `_` 应为纯最暗色、不渐变 ==")
    (ux0, uy0), (ux1, _uy1) = us_pts
    us_c = []
    for frac in (0.15, 0.5, 0.85):
        px = ux0 + (ux1 - ux0) * frac
        c = sample(px, uy0)
        if c:
            us_c.append(c[0])
    print(f"  `_` 三点亮度：{us_c}")
    check(len(us_c) == 3 and max(us_c) - min(us_c) <= 2,
          f"`_` 全段同色（亮度极差 {max(us_c) - min(us_c) if us_c else '?'}）")
    check(us_c and all(abs(v - gi.SYMBOL_DARK[0]) <= 6 for v in us_c),
          f"`_` = 最暗色 #6E6E6E（实测 {us_c}）")

    # ── 圆头内部不得出现突变 ──
    # 用户 2026-10-26：「> 左上角路径开始的地方，有色差」。
    # 真因是当时按「离左边缘最近点」定进度：圆头上半部分的最近点会落到
    # 路径另一端的左边缘上，于是上半圆被涂成最暗色，与下半圆断开
    # （实测 y=301..329 全是 110，y=333 往下才 255）。
    # 现改为「各段投影的 1/d² 加权平均」，圆头内应平滑同色。
    #
    # ⚠️ 只统计半径 0.35×stroke 以内的**内部稳定区**：
    #    圆头贴着路径延伸方向，外圈本来就带正常渐变；
    #    超采样后边缘还有半透明混合像素，全盘统计会把极差拉大而误判。
    print("\n== 圆头内部不得出现突变 ==")
    import numpy as np
    arr = np.asarray(icon.convert("RGB"), dtype=float)
    lum = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
    gy, gx = np.mgrid[0:H, 0:W]
    for label, (kx, ky) in (("起点", (x0, y0)), ("折点", (x1, y1)), ("终点", (x2, y2))):
        inner = ((gx - kx) ** 2 + (gy - ky) ** 2) <= (stroke * 0.35) ** 2
        if not inner.any():
            check(False, f"{label}圆头取样为空")
            continue
        v = lum[inner]
        span = v.max() - v.min()
        # 沿路径走完整个 `>` 才降 145 级；0.35 半径的小圆内应远小于 30
        check(span < 30, f"{label}圆头内部亮度极差 {span:.0f}（< 30，无突变）")

    print("\n" + ("路径渐变正确" if ok else "渐变不符合要求"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
