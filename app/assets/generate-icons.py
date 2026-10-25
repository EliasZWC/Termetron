"""生成 Termetron App 图标：深底 + 白→灰渐变的终端标志。

═══ 配色对标 Scholarius / Livolog（唯一依据，不要凭感觉调）═══
    · 底板       #1B1B1B —— 两款应用 ic_launcher_background 都是这个值（已核对 XML）
    · 符号色     白 → 灰 渐变，**不是纯白**。
                Scholarius 的 Σ：纯白 → 浅灰 → 中灰 → 深灰 → 最深灰（五个节点逐段渐变）
                Livolog   的钟环：纯白 / 中灰 / 最深灰（sweep）
                共同点：起点纯白 #FFFFFF，末端最深的灰 #6E6E6E。
    · 符号尺度   Scholarius 外接 38/108 = 35.2%，Livolog 圆环直径 44/108 = 40.7%。
                取 38% 落在两者之间（之前是 58.6%，比两者都大得多 —— 这就是"不像一家"的原因）。
    · 线宽       两款都是 4.67/108（Livolog 指针 4.0）。取 4.67。

⚠️ 之前犯过的错：符号用纯白 #F2F2F0 且占 58.6%。
   #F2F2F0 是网页 --acc（UI 强调色），不该拿来画图标符号 ——
   两款应用的图标符号末端都明显变灰，纯白会显得比它们"更亮、更硬"。
"""
import os
from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
BG = (27, 27, 27, 255)        # 底板 #1B1B1B（与 Scholarius / Livolog 一致）

# 符号渐变：白 -> 灰，端点与两款应用完全相同（#FFFFFF -> #6E6E6E）
SYMBOL_TOP = (255, 255, 255)  # #FFFFFF
SYMBOL_BOT = (110, 110, 110)  # #6E6E6E

# 符号占画布比例：Scholarius 35.2% / Livolog 40.7% -> 取中间 38%
SYMBOL_RATIO = 0.38
STROKE_RATIO = 4.67 / 108.0   # 线宽占画布比例（两款应用同为 4.67/108）

# 终端符号在 24 viewBox 里的坐标（「终端窗口 + >_ 字形」：圆角窗口 + 等宽粗体字符 >_，Windows Terminal 式）
WIN = (1.5, 2, 22.5, 22)      # 终端窗口（圆角描边）
TEXT = (3.5, 13)              # >_ 文本基线位置（24 空间，左上角 = 真实终端提示符位置）
FONT_SIZE = 11.5              # >_ 字号（24 空间，粗体等宽）

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


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_symbol(mask_draw, scale, stroke_w, origin=None):
    """在 L 掩码上画「终端窗口 + >_」。掩码 255 = 符号，0 = 透明。

    只画形状、不画颜色 —— 颜色由 draw_centered() 按 y 统一铺一条竖直渐变，
    贯穿整个符号，与 Scholarius 的弧长渐变同一观感。

    origin=(x0, y0)：把符号的包围盒左上角对到掩码的这个位置，
    在小于整张画布的掩码上绘制时用（启动页）。
    """
    ox, oy = origin if origin else (0.0, 0.0)
    r = max(2, int(scale * 4))
    mask_draw.rounded_rectangle(
        [ox + (WIN[0] - WIN[0]) * scale, oy + (WIN[1] - WIN[1]) * scale,
         ox + (WIN[2] - WIN[0]) * scale, oy + (WIN[3] - WIN[1]) * scale],
        radius=r, outline=255, width=stroke_w)
    font = load_font(max(10, int(FONT_SIZE * scale)))
    mask_draw.text((ox + (TEXT[0] - WIN[0]) * scale, oy + (TEXT[1] - WIN[1]) * scale),
                   '>_', font=font, fill=255, anchor='ls')


def symbol_scale(target_w):
    """符号在 24 空间宽 21 格，缩放到 target_w 像素"""
    return target_w / 21.0


def make_vgradient(w, h):
    """竖直渐变：顶部纯白 #FFFFFF -> 底部最深灰 #6E6E6E。"""
    grad = Image.new('RGB', (1, max(1, h)))
    px = grad.load()
    for y in range(max(1, h)):
        px[0, y] = _lerp(SYMBOL_TOP, SYMBOL_BOT, y / max(1, h - 1))
    return grad.resize((max(1, w), max(1, h)), Image.BILINEAR)


def draw_centered(img, size_px):
    """把白->灰渐变的符号画到主图（先画掩码，再按符号自身高度取色阶）。"""
    scale = symbol_scale(size_px)
    stroke_w = max(2, round(STROKE_RATIO * SIZE))

    # 符号包围盒（24 空间）：x 1.5..22.5, y 2..22
    box_w = (WIN[2] - WIN[0]) * scale
    box_h = (WIN[3] - WIN[1]) * scale
    ox = (SIZE - box_w) / 2
    oy = (SIZE - box_h) / 2

    # 1) 掩码：符号先画在原点，再整体平移到包围盒左上角
    tmp = Image.new('L', (SIZE, SIZE), 0)
    draw_symbol(ImageDraw.Draw(tmp), scale, stroke_w)
    mask = Image.new('L', (SIZE, SIZE), 0)
    mask.paste(tmp, (round(ox), round(oy)))

    # 2) ⚠️ 渐变必须按**符号包围盒**的高度取色阶，不能按整张画布。
    #    铺满画布时符号只落在渐变中段（实测最亮只到 209 左右），
    #    顶部根本到不了纯白 —— Scholarius / Livolog 的符号都是"起点纯白"。
    #
    #    ⚠️ 起点/终点要对齐**描边中心线**，不是描边外缘：
    #       描边是 4.67 宽、圆头，若色阶从外缘算起，
    #       最顶上露出来的只是圆头边缘的半圈（实测最亮约 246，还差一档）。
    #       把渐变区间收进半个线宽，顶端那条中心线才真正是纯白。
    half = stroke_w / 2
    gx0 = round(ox) + round(half)
    gy0 = round(oy) + round(half)
    gw = max(1, round(box_w) - 2 * round(half))
    gh = max(1, round(box_h) - 2 * round(half))

    # 画布尺寸的 RGB 渐变层。渐变区间 = [gy0, gy0+gh)：
    #   · 区间上方的圆头上半部      -> 纯白（继续用起点色）
    #   · 区间内                    -> 白 -> 灰 线性
    #   · 区间下方                  -> 末端灰
    # 掩码为 0 处不会参与合成，填什么都不影响成品。
    grad = Image.new('RGB', (SIZE, SIZE), SYMBOL_TOP)
    band = make_vgradient(gw + 2, gh)          # 左右各留 1px，避免边缘取样越界
    grad.paste(band, (gx0 - 1, gy0))
    if gy0 + gh < SIZE:
        grad.paste(Image.new('RGB', (SIZE, SIZE - (gy0 + gh)), SYMBOL_BOT),
                   (0, gy0 + gh))

    # 3) 用掩码把渐变裁成符号形状
    img.paste(grad, (0, 0), mask)


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
    """在 img 上以 (cx, cy) 为中心画一个带白->灰渐变的符号（用于启动页）。

    渐变按符号包围盒的高度取色阶，与 make_icon() 的观感一致。
    """
    box_w = size_px
    box_h = size_px * (WIN[3] - WIN[1]) / (WIN[2] - WIN[0])
    x0, y0 = round(cx - box_w / 2), round(cy - box_h / 2)

    scale = box_w / (WIN[2] - WIN[0])
    tmp = Image.new('L', (round(box_w) + 8, round(box_h) + 8), 0)
    draw_symbol(ImageDraw.Draw(tmp), scale, stroke_px, origin=(WIN[0], WIN[1]))
    mask = Image.new('L', img.size, 0)
    mask.paste(tmp, (x0, y0))

    pad = round(stroke_px / 2) + 2
    gx0, gy0 = x0 - pad, y0 - pad
    gw, gh = round(box_w) + 2 * pad, round(box_h) + 2 * pad
    grad = Image.new('RGB', img.size, SYMBOL_BOT)
    grad.paste(make_vgradient(gw, gh), (gx0, gy0))
    img.paste(grad, (0, 0), mask)


def make_splash():
    """生成 Android 启动页 splash.png（2732²）：深色底 + 居中终端 logo + TERMETRON 品牌字。"""
    img = Image.new('RGB', (SPLASH_SIZE, SPLASH_SIZE), BG)
    # logo 宽度按图标同一比例（38%），保持启动页与图标观感一致
    logo_w = round(SPLASH_SIZE * SYMBOL_RATIO)
    logo_h = logo_w * (WIN[3] - WIN[1]) / (WIN[2] - WIN[0])
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
    d.text((tx, ty), text, font=font, fill=SYMBOL_TOP)
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
