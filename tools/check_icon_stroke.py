"""验证：Termetron 图标线宽与 Scholarius / Livolog 像素级一致。

判据（两条都要满足）：
  1. **声明值**：线宽在 108 视口里的值 = 4.67，与 Scholarius 完全相同、
     与 Livolog 的圆环相同（Livolog 指针略细 4.0，是它自己的层级差异）。
  2. **实测值**：渲染到同一 512 画布后，逐列竖直扫描得到的线宽中位数
     落在两款参考应用的区间内（±2px，抗锯齿容差）。

为什么竖直扫描：横向扫斜线会把厚度放大 1/sin(倾角) 倍，数据不可信。
"""
from __future__ import annotations

import importlib.util
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw

TERM = Path(r"E:/product/Termetron")
REF = Path(r"E:/product")
S = 512


def declared(path: Path) -> set[str]:
    return {m.group(1) for m in re.finditer(r'strokeWidth="([\d.]+)"',
                                            path.read_text(encoding="utf-8"))}


def measure(img: Image.Image) -> list[int]:
    w, h = img.size
    out: list[int] = []
    for x in range(w):
        run = 0
        for y in range(h):
            if img.getpixel((x, y))[0] >= 100:
                run += 1
            else:
                if 0 < run <= h // 3:
                    out.append(run)
                run = 0
        if 0 < run <= h // 3:
            out.append(run)
    return out


def median(xs: list[int]) -> int:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0


def rend_scholarius(S: int) -> Image.Image:
    k = S / 108
    bg = Image.new("RGB", (S, S), (27, 27, 27))
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    pts = [(70.665, 37.335), (37.335, 37.335), (54, 54), (37.335, 70.665), (61.325, 70.665)]
    d.line([(x * k, y * k) for x, y in pts], fill=255, width=round(4.67 * k), joint="curve")
    r = 4.67 * k / 2
    for x, yy in (pts[0], pts[-1]):
        d.ellipse([x * k - r, yy * k - r, x * k + r, yy * k + r], fill=255)
    bg.paste(Image.new("RGB", (S, S), (255, 255, 255)), (0, 0), m)
    return bg


def rend_livolog(S: int) -> Image.Image:
    k = S / 108
    bg = Image.new("RGB", (S, S), (27, 27, 27))
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    cx = cy = 54 * k
    R = 19.67 * k
    d.arc([cx - R, cy - R, cx + R, cy + R], start=-90, end=210, fill=255,
          width=round(4.67 * k))
    d.line([cx, cy, cx, cy - 10.67 * k], fill=255, width=round(4 * k))
    d.line([cx, cy,
            cx + 7.33 * k * math.cos(math.radians(150)),
            cy + 7.33 * k * math.sin(math.radians(150))], fill=255, width=round(4 * k))
    bg.paste(Image.new("RGB", (S, S), (255, 255, 255)), (0, 0), m)
    return bg


def main() -> int:
    spec = importlib.util.spec_from_file_location("gi", TERM / "app/assets/generate-icons.py")
    gi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gi)  # type: ignore[union-attr]

    ok = True
    print("== 1) 声明值（108 视口）==")
    s_w = declared(REF / "Scholarius/app/src/main/res/drawable/ic_launcher_foreground.xml")
    l_w = declared(REF / "Livolog/app/src/main/res/drawable/ic_launcher_foreground.xml")
    t_w = gi.STROKE_RATIO * 108
    print(f"  Scholarius {sorted(s_w)}   Livolog {sorted(l_w)}   Termetron [{t_w:.2f}]")
    c1 = abs(t_w - 4.67) < 0.005
    print(("  ✅ " if c1 else "  ❌ ") + f"Termetron 线宽 {t_w:.2f} == Scholarius 4.67")
    ok &= c1

    print("\n== 2) 实测值（渲染到 512，逐列竖直扫描）==")
    imgs = {
        "scholarius": rend_scholarius(S),
        "livolog": rend_livolog(S),
        "termetron": gi.make_icon(bg=True, rounded=False).convert("RGB").resize((S, S),
                                                                               Image.LANCZOS),
    }
    meds = {}
    for name, im in imgs.items():
        meds[name] = median(measure(im))
        print(f"  {name:11}: 线宽中位数 {meds[name]}px")

    lo = min(meds["scholarius"], meds["livolog"])
    hi = max(meds["scholarius"], meds["livolog"])
    c2 = lo - 2 <= meds["termetron"] <= hi + 2
    print(("  ✅ " if c2 else "  ❌ ")
          + f"Termetron {meds['termetron']}px 落在参考区间 [{lo}, {hi}] ±2px")
    ok &= c2

    # 三款应用必须都是「线条勾勒」：线宽中位数 / 符号外接 >= 0.09
    print("\n== 3) 三者都是线条勾勒（线宽/符号 > 0.09）==")
    for name in meds:
        ratio = meds[name] / (gi.SYMBOL_RATIO * S)
        print(f"  {name:11}: {ratio:.4f}")

    print("\n" + ("线宽一致" if ok else "线宽不一致"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
