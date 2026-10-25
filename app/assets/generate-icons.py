"""生成 Termetron App 图标：深底 + 纯白 `>_` 提示符（无外框）。

═══ 对标 Scholarius / Livolog（唯一依据，不要凭感觉调）═══
    · 底板       #1B1B1B —— 两款应用 ic_launcher_background 都是这个值（已核对 XML）
    · 符号色     纯白 #FFFFFF —— 即两款应用渐变**起点**的颜色。
                （2026-10-26 用户要求「不要渐变」，因此符号一律纯白。）
    · 符号宽度   Scholarius 外接 38/108 = 35.2%，Livolog 圆环直径 44/108 = 40.7%。
                取 38% 落在两者之间。
    · 线宽       两款都是 4.67/108（Livolog 指针 4.0）。取 4.67。

═══ 造型 ═══
    只有 `>` 和 `_` 两个字形，都是**描边路径 + 圆头**，线宽统一走 STROKE_RATIO。
    外侧那圈圆角「终端窗口」框已按用户要求去掉（2026-10-26）。

⚠️ 符号**宽度**固定 38%（占位不变），但**高度**比等比例时更矮 ——
   用户要求「占位要矮一些」。用 SYMBOL_ASPECT 控制，< 1 就是压矮。

⚠️ 之前犯过的错：
   · 用 draw.text 画 `>_` —— 笔画粗细由字体决定，与线宽无关，同一符号里两种粗细。
   · 用 UI 强调色 #F2F2F0 当符号色 —— 那是网页 --acc，不是图标色。
"""
import os
from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
BG = (27, 27, 27, 255)        # 底板 #1B1B1B（与 Scholarius / Livolog 一致）

# 符号颜色：纯白（= 两款应用渐变起点色）。用户 2026-10-26 要求不要渐变。
SYMBOL = (255, 255, 255)      # #FFFFFF

# 符号占画布比例（**中心线**外接宽度）。
# ⚠️ 2026-10-26 定稿：0.34 -> 加上线宽外缘后约 37%，
#    与 Scholarius 35.2% / Livolog 36.5% 一致（并排时大小相同）。
#    之前用 0.38 是「保持加框时的占位」，去掉外框后外缘会到 42%，明显偏宽。
SYMBOL_RATIO = 0.34
# 符号高度 / 宽度（等比例 = 1.0）。< 1 就是「占位矮一些」。
# 用户 2026-10-26：先要「矮一些」，再确认「再矮一些」-> 0.72。
SYMBOL_ASPECT = 0.72
STROKE_RATIO = 4.67 / 108.0   # 线宽占画布比例（两款应用同为 4.67/108）

# ── 几何（单位：0..1 的符号局部坐标，再按外接框缩放到目标像素）──
#    去掉外框后，符号 = `>` + `_` 两个字形。
#    竖向上 `>` 占满整个高度，`_` 位于底部；横向上 `>` 在左、`_` 在右。
#
#    ⚠️ 所有值都是「字形中心线」的坐标，不是外缘 ——
#       实际渲染时笔画会向外扩 stroke/2，所以包围盒要留出这部分余量，
#       否则圆头会被裁掉。
CHEV = (0.02, 0.06, 0.34, 0.50, 0.02, 0.94)   # `>`: 起点x,y / 折点x,y / 终点x,y
US = (0.52, 0.94, 0.98)                        # `_`: 起点x, y, 终点x

_font_cache = {}


def load_font(size):
    """加载等宽粗体字体（Consolas 优先，回退常规 Consolas/默认）"""
    if size in _font_cache:
        return _font_cache[size]
    for path in (r'C:\Windows\Fonts\consolab.ttf', r'C:\Windows\Fonts\consola.ttf'):
        if os.path.exists(path):
            f = ImageFont.truetype(path, size)
            _font_cache[size] = f
            return f
    f = ImageFont.load_default()
    _font_cache[size] = f
    return f


def draw_symbol(mask_draw, sx, sy, stroke_w, origin=None):
    """在 L 掩码上画提示符 `>_`（**无外框**）。掩码 255 = 符号，0 = 透明。

    只画形状、不画颜色 —— 上色由 draw_centered() 统一做（当前是纯白）。

    sx, sy   ：x / y 方向各自的缩放。**必须分开**，
               「占位矮一些」= sy < sx，不是把整张图垂直压缩（那会把圆头压扁、
               线宽在水平和竖直方向变得不一样粗）。
    origin   ：局部坐标 (0,0) 在掩码上的位置（启动页在小画布上绘制时用）。
               调用方算出 x0 = pad - _SYMBOL_X0*sx，让真实外接框左上角落在 pad 处。

    ⚠️ 2026-10-26：去掉外围的圆角「终端窗口」框（用户要求「直接不要框了」）。
       现在只剩 `>_` 两个字形。它们各自是**描边路径**、圆头，
       线宽统一走 stroke_w（4.67/108），与 Scholarius 的 Σ、Livolog 的时钟同一种笔触。

    ⚠️ `>_` 必须用路径描边，不能用 draw.text（原来犯过的错）：
       字形笔画粗细由字体决定，与线宽无关 → 同一个符号里两种粗细。
    """
    ox, oy = origin if origin else (0.0, 0.0)

    def cx(u: float) -> float:      # 局部 x -> 掩码坐标
        return ox + u * sx

    def cy(v: float) -> float:      # 局部 y -> 掩码坐标
        return oy + v * sy

    def dot(px: float, py: float) -> None:
        mask_draw.ellipse([px - stroke_w / 2, py - stroke_w / 2,
                           px + stroke_w / 2, py + stroke_w / 2], fill=255)

    # ① `>`：两段折线，圆头、圆角折点
    chevron = [(cx(CHEV[0]), cy(CHEV[1])),
               (cx(CHEV[2]), cy(CHEV[3])),
               (cx(CHEV[4]), cy(CHEV[5]))]
    mask_draw.line(chevron, fill=255, width=stroke_w, joint="curve")
    dot(*chevron[0])
    dot(*chevron[-1])

    # ② `_`：水平线，两端圆头
    mask_draw.line([(cx(US[0]), cy(US[1])), (cx(US[2]), cy(US[1]))],
                   fill=255, width=stroke_w)
    dot(cx(US[0]), cy(US[1]))
    dot(cx(US[2]), cy(US[1]))


# 符号（`>_` 中心线）在局部坐标里的真实外接框
_SYMBOL_X0 = min(CHEV[0], CHEV[2], CHEV[4], US[0], US[2])
_SYMBOL_X1 = max(CHEV[0], CHEV[2], CHEV[4], US[0], US[2])
_SYMBOL_Y0 = min(CHEV[1], CHEV[3], CHEV[5], US[1])
_SYMBOL_Y1 = max(CHEV[1], CHEV[3], CHEV[5], US[1])
_SYMBOL_W = _SYMBOL_X1 - _SYMBOL_X0
_SYMBOL_H = _SYMBOL_Y1 - _SYMBOL_Y0


def symbol_scale(target_w):
    """把符号的**真实外接宽度**缩放到 target_w 像素。

    ⚠️ 必须按真实外接（_SYMBOL_W）归一化，不能按局部坐标的 [0,1] ——
       几何只占 0.06..0.98，用 0..1 会把符号画小一大截。
       判据：SYMBOL_RATIO × SIZE 应该正好等于符号外接宽度，
       这样「去掉外框后占位宽度不变」才成立。
    """
    return target_w / _SYMBOL_W


def symbol_height(size_px):
    """符号外接高度（像素）= 宽度 × 几何纵横比 × SYMBOL_ASPECT。

    SYMBOL_ASPECT < 1 => 比等比例更矮（用户要求「占位要矮一些」）。
    """
    return size_px * (_SYMBOL_H / _SYMBOL_W) * SYMBOL_ASPECT


def draw_centered(img, size_px):
    """把纯白的 `>_` 居中画到主图。

    size_px = 符号中心线外接**宽度**，= SYMBOL_RATIO × SIZE。
    高度 = symbol_height(size_px)（受 SYMBOL_ASPECT 控制，比等比例更矮）。

    ⚠️ 2026-10-26：不用渐变，符号一律纯白 #FFFFFF。
       用户明确说「你别加渐变蒙版了」。纯白也是 Scholarius / Livolog
       渐变**起点**的颜色，并排看仍属同一色系。

    ⚠️ 中心线之外要留 stroke/2 给描边外缘（圆头向外鼓出），否则会被裁平。
    """
    stroke_w = max(2, round(STROKE_RATIO * SIZE))
    sx = symbol_scale(size_px)
    pad = int(stroke_w / 2 + 2)

    # 中心线外接尺寸（像素）
    cw = size_px
    ch = symbol_height(size_px)
    # y 方向单独缩放：让几何高度正好等于 ch（否则只有小画布变了、图形没变）
    sy = ch / _SYMBOL_H

    # 小画布：外接 + 两侧 pad
    mw, mh = round(cw + 2 * pad), round(ch + 2 * pad)
    tmp = Image.new('L', (mw, mh), 0)
    draw_symbol(ImageDraw.Draw(tmp), sx, sy, stroke_w,
                origin=(pad - _SYMBOL_X0 * sx, pad - _SYMBOL_Y0 * sy))

    # 居中贴到整张画布，然后用纯白上色
    mask = Image.new('L', (SIZE, SIZE), 0)
    ox = round((SIZE - mw) / 2)
    oy = round((SIZE - mh) / 2)
    mask.paste(tmp, (ox, oy))
    img.paste(Image.new('RGB', (SIZE, SIZE), SYMBOL), (0, 0), mask)


def make_icon(bg=True, rounded=False, radius=220):
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    if bg:
        if rounded:
            mask = Image.new('L', (SIZE, SIZE), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1],
                                                   radius=radius, fill=255)
            bgimg = Image.new('RGBA', (SIZE, SIZE), BG)
            img.paste(bgimg, (0, 0), mask)
        else:
            img.paste(Image.new('RGBA', (SIZE, SIZE), BG), (0, 0))
    draw_centered(img, round(SYMBOL_RATIO * SIZE))
    return img


SPLASH_SIZE = 2732  # Android 启动页推荐尺寸


def load_text_font(size):
    """加载品牌文字字体（Segoe UI Semibold，回退 Arial/默认）"""
    for path in (r'C:\Windows\Fonts\seguisb.ttf',
                 r'C:\Windows\Fonts\arialbd.ttf',
                 r'C:\Windows\Fonts\arial.ttf'):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_symbol_on(img, cx, cy, size_px, stroke_px):
    """在 img 上以 (cx, cy) 为中心画**纯白**的 `>_`（用于启动页）。

    size_px = 符号中心线外接**宽度**。高度同样受 SYMBOL_ASPECT 压矮，
    与图标保持同一比例。与 make_icon() 一样不用渐变。
    """
    cw = size_px
    ch = symbol_height(cw)
    sx = symbol_scale(cw)
    sy = ch / _SYMBOL_H
    pad = int(stroke_px / 2 + 2)

    # 小画布：外接 + 两侧 pad
    mw, mh = round(cw + 2 * pad), round(ch + 2 * pad)
    tmp = Image.new('L', (mw, mh), 0)
    draw_symbol(ImageDraw.Draw(tmp), sx, sy, stroke_px,
                origin=(pad - _SYMBOL_X0 * sx, pad - _SYMBOL_Y0 * sy))

    # 小画布左上角 = 中心对齐到 (cx, cy)
    x0 = round(cx - cw / 2) - pad
    y0 = round(cy - ch / 2) - pad
    mask = Image.new('L', img.size, 0)
    mask.paste(tmp, (x0, y0))
    img.paste(Image.new('RGB', img.size, SYMBOL), (0, 0), mask)


def make_splash():
    """生成 Android 启动页 splash.png（2732²）：深色底 + 居中 `>_` + TERMETRON 品牌字。"""
    img = Image.new('RGB', (SPLASH_SIZE, SPLASH_SIZE), BG)
    # logo 宽度按图标同一比例（38%），保持启动页与图标观感一致
    logo_w = round(SPLASH_SIZE * SYMBOL_RATIO)
    logo_h = logo_w * _SYMBOL_H / _SYMBOL_W
    stroke_px = round(STROKE_RATIO * SPLASH_SIZE)
    cx = SPLASH_SIZE / 2
    cy = (SPLASH_SIZE - logo_h) / 2 - 100
    draw_symbol_on(img, cx, cy, logo_w, stroke_px)

    # TERMETRON 品牌文字（纯白，与图标渐变起点一致）
    font = load_text_font(160)
    d = ImageDraw.Draw(img)
    text = 'TERMETRON'
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    tx = (SPLASH_SIZE - tw) / 2 - bbox[0]
    ty = cy + logo_h / 2 + 90 - bbox[1]
    d.text((tx, ty), text, font=font, fill=SYMBOL)
    return img


def main():
    # 脚本本身就在 assets/ 目录，直接输出到本目录（勿再加 'assets' 子目录）
    out = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out, exist_ok=True)

    # 1. 完整图标（legacy launcher）：圆角深色底 + 符号
    make_icon(bg=True, rounded=True, radius=220).convert('RGB').save(
        os.path.join(out, 'icon.png'))

    # 2. icon-only：透明底 + 符号
    make_icon(bg=False).convert('RGB').save(
        os.path.join(out, 'icon-only.png'))

    # 3. adaptive foreground：透明底 + 居中安全区符号
    #    38% 落在 Android 自适应图标 66% 安全区之内（自适应层还会按 inset 缩放）
    make_icon(bg=False).convert('RGB').save(
        os.path.join(out, 'icon-foreground.png'))

    # 4. adaptive background：纯深色
    Image.new('RGB', (SIZE, SIZE), BG).save(os.path.join(out, 'icon-background.png'))

    # 5. 启动页 splash.png（Android：deep bg + 居中 logo + 品牌字）
    make_splash().save(os.path.join(out, 'splash.png'))

    for f in ['icon.png', 'icon-only.png', 'icon-foreground.png', 'icon-background.png', 'splash.png']:
        p = os.path.join(out, f)
        print(f, Image.open(p).size, os.path.getsize(p))


if __name__ == '__main__':
    main()
