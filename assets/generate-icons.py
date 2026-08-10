"""生成 QT App 图标：深色底 + 浅紫色终端标志（与桌面 favicon 一致）"""
import os
from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
BG = (11, 15, 20, 255)        # 深色底 #0b0f14
SYMBOL = (167, 139, 250, 255) # 浅紫色符号 #a78bfa（与 QT --acc 一致）

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


def draw_symbol(draw, scale, w):
    """画「终端窗口 + >_」：圆角窗口(屏幕容器) + 等宽粗体字符 >_（字形质感 → 一眼读出命令行提示符）。"""
    # 终端窗口（圆角描边）
    r = max(2, int(scale * 4))
    draw.rounded_rectangle(
        [WIN[0] * scale, WIN[1] * scale, WIN[2] * scale, WIN[3] * scale],
        radius=r, outline=SYMBOL, width=max(2, int(scale * 1.6)))
    # >_ 等宽字符（Windows Terminal 式）
    font = load_font(max(10, int(FONT_SIZE * scale)))
    draw.text((TEXT[0] * scale, TEXT[1] * scale), '>_', font=font,
              fill=SYMBOL, anchor='ls')


def symbol_scale(target_w):
    """符号在 24 空间宽 21 格，缩放到 target_w 像素"""
    return target_w / 21.0


def centered(img, target_w):
    """返回绘制上下文，符号居中"""
    draw = ImageDraw.Draw(img, 'RGBA')
    scale = symbol_scale(target_w)
    # 符号包围盒（24 空间）：x 1.5..22.5, y 2..22
    ox = (SIZE - (22.5 - 1.5) * scale) / 2 - (1.5 * scale)
    oy = (SIZE - (22 - 2) * scale) / 2 - (2 * scale)
    # 平移：先画在原始坐标，再整体平移
    return draw, scale, ox, oy


def draw_centered(img, scale, ox, oy, w):
    """把符号画在透明图层后 alpha 合成到主图（保留符号颜色，勿用 bitmap 掩码）"""
    orig = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    d2 = ImageDraw.Draw(orig, 'RGBA')
    draw_symbol(d2, scale, w)
    shifted = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    shifted.paste(orig, (int(ox), int(oy)), orig)
    img.alpha_composite(shifted)


def make_icon(bg=True, rounded=False, target_w=600, w=110, radius=220):
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
    draw, scale, ox, oy = centered(img, target_w)
    draw_centered(img, scale, ox, oy, w)
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


def make_splash():
    """生成 Android 启动页 splash.png（2732²）：深色底 + 居中终端 logo + TERMETRON 品牌字。"""
    img = Image.new('RGB', (SPLASH_SIZE, SPLASH_SIZE), BG)
    # logo：宽 ~460px（24 空间宽 21 格）
    scale = 460 / 21.0
    lw = lh = int(24 * scale)
    logo = Image.new('RGBA', (lw, lh), (0, 0, 0, 0))
    draw_symbol(ImageDraw.Draw(logo, 'RGBA'), scale, 0)
    # logo 居中略偏上（下方留空间给品牌字）
    cx = (SPLASH_SIZE - lw) // 2
    cy = (SPLASH_SIZE - lh) // 2 - 140
    img.paste(logo, (cx, cy), logo)
    # TERMETRON 品牌文字（紫色，与 logo 一致）
    font = load_text_font(160)
    d = ImageDraw.Draw(img)
    text = 'TERMETRON'
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    tx = (SPLASH_SIZE - tw) / 2 - bbox[0]
    ty = cy + lh + 60 - bbox[1]
    d.text((tx, ty), text, font=font, fill=SYMBOL)
    return img


def main():
    # 脚本本身就在 assets/ 目录，直接输出到本目录（勿再加 'assets' 子目录）
    out = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out, exist_ok=True)

    # 1. 完整图标（legacy launcher）：圆角深色底 + 符号
    make_icon(bg=True, rounded=True, target_w=600, w=96, radius=220).convert('RGB').save(
        os.path.join(out, 'icon.png'))

    # 2. icon-only：透明底 + 符号
    make_icon(bg=False, target_w=720, w=100).convert('RGB').save(
        os.path.join(out, 'icon-only.png'))

    # 3. adaptive foreground：透明底 + 居中安全区符号（安全区 66%，600/1024≈59% 在区内）
    make_icon(bg=False, target_w=600, w=96).convert('RGB').save(
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
