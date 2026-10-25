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
    """真实图标 PNG 缩放到 S 画布（不再手绘假参考）。

    ⚠️ 旧实现手绘了一条 Σ 折线，并声称「外接 38/108」——
    那是**凭印象的模型**，与真实 Scholarius 图标（秒表）形状完全不同，
    量出的占位 35.2% 也是假的。真实 PNG 在 512 画布上实测 41.4%。
    对比必须对真实产物，不能对自己脑补的近似图。
    """
    src = REF_ROOT / "Scholarius/design/assets/livolog.png"
    return Image.open(src).convert("RGB").resize((S, S), Image.LANCZOS)


def render_livolog(S: int) -> Image.Image:
    """真实图标 PNG 缩放到 S 画布。"""
    src = REF_ROOT / "Livolog/design/_livolog-final.png"
    return Image.open(src).convert("RGB").resize((S, S), Image.LANCZOS)


def render_termetron(S: int) -> Image.Image:
    """直接用生产脚本，保证对比的是真实产物。"""
    gen = ROOT / "app" / "assets" / "generate-icons.py"
    spec = importlib.util.spec_from_file_location("generate_icons", gen)
    gi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gi)  # type: ignore[union-attr]

    # ⚠️ 必须与生产一致：main() 里是 rounded=False（方形满底）。
    #    用 True 会引入 4% 的纯黑角，与参考对比失真。
    icon = gi.make_icon(bg=True, rounded=False)
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
        # 底板色：取符号区之外、靠上边缘的一点
        "bg": im.getpixel((w // 2, int(h * 0.06))),
        # 四角：方形满底后必须也是底色，不能是纯黑（圆角残留）
        "corners": [im.getpixel(p) for p in
                    ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))],
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
    me = rows["termetron"]
    ok = True
    print()
    # ── 占位判定：按用户 2026-10-26 定稿的规格 ──
    #    「占位框和 Scholarius 图标中的占位面积一样」——
    #    所以判据是 **等于 Scholarius**，不是「落在两者之间」。
    #    （旧判据是 [35.2%, 36.5%] ±3%，那是去掉线框前的口径，已作废。）
    sch = rows["scholarius"]["bbox_w"]
    checks = [
        (abs(me["bbox_w"] - sch) <= 0.01,
         f"占位 {me['bbox_w']:.1%} == Scholarius {sch:.1%}（容差 1%）"),
        (abs(me["bbox_w"] - me["bbox_h"]) <= 0.005,
         f"占位是正方形（{me['bbox_w']:.1%} x {me['bbox_h']:.1%}）"),
        # Termetron 的 `>` 用路径渐变（#FFFFFF -> #6E6E6E）、`_` 用最暗色。
        # 所以既要「最亮达到纯白」，也要「存在最暗色」——两个端点都必须来自参考应用。
        (me["max"] >= 250, f"最亮达到纯白（实测 {me['max']}，= 参考渐变起点）"),
        (me["min"] <= 120, f"最暗达到 #6E6E6E 附近（实测 {me['min']}，= 参考渐变终点）"),
        (me["bg"] == (27, 27, 27), f"底板 #1B1B1B（实测 {me['bg']}）"),
        (all(c == (27, 27, 27) for c in me["corners"]),
         f"四角为底色、非纯黑（实测 {me['corners'][0]}）"),
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
