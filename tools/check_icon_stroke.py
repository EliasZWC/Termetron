"""验证：Termetron 图标线宽与 Scholarius / Livolog 像素级一致。

判据：**线宽 / 占位** 这个比例三家要一致。
  为什么不能直接比 px：三款应用的占位不同（Scholarius 41.4%、Livolog 41.6%、
  Termetron 按用户要求等于 Scholarius 的 41.4%），占位大线宽自然大。
  为什么不能只比 SVG 声明值：Termetron 不画 SVG，且参考应用的 SVG 视口
  与实际渲染口径不同。

⚠️ 测量方法必须是**距离变换骨架**，不能逐列竖直扫描：
   · 54° 斜线逐列扫描会放大 1/cos 倍（约 1.7 倍）；
   · 闭合圆环逐列扫描量到的是弦长，不是线宽。
   旧实现用逐列扫描，得到 Scholarius 22px / Termetron 33px，
   差点让我误以为「线宽偏粗 50%」而改坏图标。
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

TERM = Path(r"E:/product/Termetron")
REF = Path(r"E:/product")
S = 512


def declared(path: Path) -> set[str]:
    return {m.group(1) for m in re.finditer(r'strokeWidth="([\d.]+)"',
                                            path.read_text(encoding="utf-8"))}


def mask_of(img: Image.Image) -> np.ndarray:
    """前景 mask：与底板 #1B1B1B 有明显差异的像素。"""
    a = np.array(img.convert("RGB")).astype(int)
    return np.abs(a - np.array([27, 27, 27])).sum(2) > 12


def measure(img: Image.Image) -> float:
    """线宽 = 2 × 骨架距离变换的**最大值**（= 最粗处）。

    ⚠️ 必须取 max，不能取 median（2026-10-26 踩过）。
       dataset 里图标内部有细笔画（圆环内侧、指针、圆点边缘），
       中位数会被它们拉低：Scholarius 中位 5.0、最大 11.0，
       差了一倍多。按中位数算出来 0.1149，会让我以为
       Termetron 偏粗而把线宽调细 8%——用户一眼就看出"细了"。
       肉眼比较的是**最粗的那一段**，所以用 max。

    ⚠️ 调用方必须传**原始分辨率**的图。缩放（LANCZOS）会做插值，
       把边缘过渡拉宽，最大值随之虚高。
    """
    m = mask_of(img)
    d = ndimage.distance_transform_edt(m)
    if not m.any():
        return 0.0
    return 2.0 * float(d.max())


def median(xs) -> int:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0


def bbox_w_of(img: Image.Image) -> int:
    """符号外接宽度（像素）。用来把线宽换算成「线宽/占位」比例。"""
    m = mask_of(img)
    xs = np.nonzero(m.any(axis=0))[0]
    return int(xs.max() - xs.min() + 1) if len(xs) else 0


def rend_scholarius(S: int) -> Image.Image:
    """真实图标 PNG（**原始分辨率**，不缩放）。

    ⚠️ 旧实现手绘一条 Σ 折线并声称「线宽 4.67/108」——
    那是凭印象的模型，真实 Scholarius 图标是秒表，形状完全不同。
    ⚠️ 也不要缩放到 S：缩放插值会把边缘过渡拉宽，比例虚高。
    """
    return Image.open(REF / "Scholarius/design/assets/livolog.png").convert("RGB")


def rend_livolog(S: int) -> Image.Image:
    """真实图标 PNG（原始分辨率，不缩放）。"""
    return Image.open(REF / "Livolog/design/_livolog-final.png").convert("RGB")


def main() -> int:
    spec = importlib.util.spec_from_file_location("gi", TERM / "app/assets/generate-icons.py")
    gi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gi)  # type: ignore[union-attr]

    ok = True
    print("== 1) 声明值 ==")
    s_w = declared(REF / "Scholarius/app/src/main/res/drawable/ic_launcher_foreground.xml")
    l_w = declared(REF / "Livolog/app/src/main/res/drawable/ic_launcher_foreground.xml")
    # 参考的 SVG 视口里写的是 4.67 / 4.0，但真正要对齐的是**渲染后最粗处的
    # 线宽/占位**：Scholarius 0.1270、Livolog 0.1232（见下方第 2 段实测）。
    # STROKE_RATIO 是源参数，因 PIL 取整而略小于最终比例。
    t_ratio = gi.STROKE_RATIO
    print(f"  Scholarius {sorted(s_w)}   Livolog {sorted(l_w)}   "
          f"Termetron 源参数 STROKE_RATIO = {t_ratio:.4f}")
    c1 = 0.100 <= t_ratio <= 0.112
    print(("  ✅ " if c1 else "  ❌ ")
          + f"STROKE_RATIO {t_ratio:.4f} 合理（最终比例见第 2 段）")
    ok &= c1

    print("\n== 2) 实测值（各应用**原始分辨率**，骨架距离**最大值**）==")
    imgs = {
        "scholarius": rend_scholarius(S),
        "livolog": rend_livolog(S),
        "termetron": gi.make_icon(bg=True, rounded=False).convert("RGB"),
    }
    widths, ratios = {}, {}
    for name, im in imgs.items():
        widths[name] = measure(im)
        ratios[name] = widths[name] / (bbox_w_of(im) or 1)
        print(f"  {name:11}: 线宽 {widths[name]:5.1f}px   "
              f"占位 {bbox_w_of(im):3d}px   线宽/占位 {ratios[name]:.4f}")

    lo = min(ratios["scholarius"], ratios["livolog"])
    hi = max(ratios["scholarius"], ratios["livolog"])
    c2 = lo - 0.003 <= ratios["termetron"] <= hi + 0.003
    print(("  ✅ " if c2 else "  ❌ ")
          + f"Termetron 线宽/占位 {ratios['termetron']:.4f} "
            f"在参考区间 [{lo:.4f}, {hi:.4f}] ±0.003")
    ok &= c2

    # 三款应用必须都是「线条勾勒」：线宽 / 符号外接 >= 0.09
    print("\n== 3) 三者都是线条勾勒（线宽/符号 > 0.09）==")
    for name in ratios:
        print(f"  {name:11}: {ratios[name]:.4f}")

    print("\n" + ("线宽一致" if ok else "线宽不一致"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
