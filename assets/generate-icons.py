"""生成 QT App 图标：深色底 + 紫色终端标志（feather terminal icon `>_`）"""
import os
from PIL import Image, ImageDraw

SIZE = 1024
BG = (13, 19, 27, 255)        # #0d131b 面板色
PURPLE = (167, 139, 250, 255) # #a78bfa 与 QT --acc 一致

# 终端符号在 24 viewBox 里的坐标
POLY = [(4, 17), (10, 11), (4, 5)]
LINE = [(12, 19), (20, 19)]
ALL_PTS = [(4, 17), (10, 11), (4, 5), (12, 19), (20, 19)]


def draw_symbol(draw, scale, w):
    """画 `>_` 终端符号（圆头连接）"""
    for a, b in [(POLY[0], POLY[1]), (POLY[1], POLY[2])]:
        draw.line([(a[0] * scale, a[1] * scale), (b[0] * scale, b[1] * scale)],
                  fill=PURPLE, width=w, joint='curve')
    draw.line([(LINE[0][0] * scale, LINE[0][1] * scale),
               (LINE[1][0] * scale, LINE[1][1] * scale)],
              fill=PURPLE, width=w)
    r = w // 2
    for x, y in ALL_PTS:
        draw.ellipse([x * scale - r, y * scale - r, x * scale + r, y * scale + r],
                     fill=PURPLE)


def symbol_scale(target_w):
    """符号在 24 空间宽 16 格，缩放到 target_w 像素"""
    return target_w / 16.0


def centered(img, target_w):
    """返回绘制上下文，符号居中"""
    draw = ImageDraw.Draw(img, 'RGBA')
    scale = symbol_scale(target_w)
    # 符号包围盒（24 空间）：x 4..20, y 5..19
    cx = (4 + 20) / 2 * scale
    cy = (5 + 19) / 2 * scale
    ox = (SIZE - (20 - 4) * scale) / 2 - (4 * scale)
    oy = (SIZE - (19 - 5) * scale) / 2 - (5 * scale)
    # 平移：先画在原始坐标，再整体平移
    return draw, scale, ox, oy


def draw_centered(draw, scale, ox, oy, w):
    orig = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    d2 = ImageDraw.Draw(orig, 'RGBA')
    draw_symbol(d2, scale, w)
    shifted = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    shifted.paste(orig, (int(ox), int(oy)), orig)
    draw.bitmap((0, 0), shifted)


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
    draw_centered(draw, scale, ox, oy, w)
    return img


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
    os.makedirs(out, exist_ok=True)

    # 1. 完整图标（legacy launcher）：圆角深色底 + 符号（缩小留白，避免贴边）
    make_icon(bg=True, rounded=True, target_w=480, w=96, radius=220).convert('RGB').save(
        os.path.join(out, 'icon.png'))

    # 2. icon-only：透明底 + 符号（缩小留白）
    make_icon(bg=False, target_w=600, w=100).convert('RGB').save(
        os.path.join(out, 'icon-only.png'))

    # 3. adaptive foreground：透明底 + 居中安全区符号（缩小留白）
    make_icon(bg=False, target_w=480, w=96).convert('RGB').save(
        os.path.join(out, 'icon-foreground.png'))

    # 4. adaptive background：纯深色
    Image.new('RGB', (SIZE, SIZE), BG).save(os.path.join(out, 'icon-background.png'))

    for f in ['icon.png', 'icon-only.png', 'icon-foreground.png', 'icon-background.png']:
        p = os.path.join(out, f)
        print(f, Image.open(p).size, os.path.getsize(p))


if __name__ == '__main__':
    main()
