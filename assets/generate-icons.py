"""生成 QT App 图标：深色底 + 浅紫色终端标志（与桌面 favicon 一致）"""
import os
from PIL import Image, ImageDraw

SIZE = 1024
BG = (11, 15, 20, 255)        # 深色底 #0b0f14
SYMBOL = (167, 139, 250, 255) # 浅紫色符号 #a78bfa（与 QT --acc 一致）

# 终端符号在 24 viewBox 里的坐标（几何化 `>_` + 度量刻度 metron）
POLY = [(6, 4), (18, 11.5), (6, 18)]                # 三角形 >（顶点朝右）
TICKS = [((7, 8), (11.5, 8)), ((7, 15), (11, 15))]  # 内部度量刻度（标尺感）
RECT = (8, 19.5, 22, 22.5)                          # 矩形 _（实心基线）


def draw_symbol(draw, scale, w):
    """画几何化 `>_`：三角形(>) 游标 + 内部度量刻度(metron) + 实心矩形(_) 基线。"""
    # 三角形描边（> 的三角化，顶点朝右）
    for a, b in [(POLY[0], POLY[1]), (POLY[1], POLY[2])]:
        draw.line([(a[0] * scale, a[1] * scale), (b[0] * scale, b[1] * scale)],
                  fill=SYMBOL, width=w, joint='curve')
    # 内部度量刻度（短线，标尺感）
    tw = max(1, w // 2)
    for (x1, y1), (x2, y2) in TICKS:
        draw.line([(x1 * scale, y1 * scale), (x2 * scale, y2 * scale)],
                  fill=SYMBOL, width=tw)
    # 实心矩形（_ 基线，圆角）
    r = max(2, int(w * 0.75))
    draw.rounded_rectangle(
        [RECT[0] * scale, RECT[1] * scale, RECT[2] * scale, RECT[3] * scale],
        radius=r, fill=SYMBOL)


def symbol_scale(target_w):
    """符号在 24 空间宽 16 格，缩放到 target_w 像素"""
    return target_w / 16.0


def centered(img, target_w):
    """返回绘制上下文，符号居中"""
    draw = ImageDraw.Draw(img, 'RGBA')
    scale = symbol_scale(target_w)
    # 符号包围盒（24 空间）：x 6..22, y 4..22.5
    ox = (SIZE - (22 - 6) * scale) / 2 - (6 * scale)
    oy = (SIZE - (22.5 - 4) * scale) / 2 - (4 * scale)
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


def main():
    # 脚本本身就在 assets/ 目录，直接输出到本目录（勿再加 'assets' 子目录）
    out = os.path.dirname(os.path.abspath(__file__))
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
