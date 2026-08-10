"""生成 QT App 图标：深色底 + 浅紫色终端标志（与桌面 favicon 一致）"""
import os
from PIL import Image, ImageDraw

SIZE = 1024
BG = (11, 15, 20, 255)        # 深色底 #0b0f14
SYMBOL = (167, 139, 250, 255) # 浅紫色符号 #a78bfa（与 QT --acc 一致）

# 终端符号在 24 viewBox 里的坐标（「游标尺子」：几何化 `>_` + 尺子刻度）
POLY = [(6, 3), (19, 11), (6, 18)]                  # 实心三角形 >（顶点朝右 = 游标）
BASE = (6, 19.5, 22, 21.5)                          # 基准线 _（实心横条）
TICKS = [((9, 19.5), (9, 23)), ((12, 19.5), (12, 21.8)),
         ((15, 19.5), (15, 23)), ((18, 19.5), (18, 21.8)),
         ((21, 19.5), (21, 23))]                    # 尺子刻度（等距竖线，长短交替）


def draw_symbol(draw, scale, w):
    """画「游标尺子」：实心三角形(> 游标) + 基准线(_) + 从基准线伸出的等距竖刻度(尺子)。"""
    # 实心三角形（> 顶点朝右 = 游标/读数指针）
    draw.polygon([(x * scale, y * scale) for x, y in POLY], fill=SYMBOL)
    # 基准线 _（实心横条）
    r = max(2, int(scale * 1.4))
    draw.rounded_rectangle(
        [BASE[0] * scale, BASE[1] * scale, BASE[2] * scale, BASE[3] * scale],
        radius=r, fill=SYMBOL)
    # 尺子刻度（等距竖线，从基准线向下伸出，长短交替 = 刻度一眼可辨）
    tw = max(2, int(scale * 1.5))
    for (x1, y1), (x2, y2) in TICKS:
        draw.line([(x1 * scale, y1 * scale), (x2 * scale, y2 * scale)],
                  fill=SYMBOL, width=tw)


def symbol_scale(target_w):
    """符号在 24 空间宽 16 格，缩放到 target_w 像素"""
    return target_w / 16.0


def centered(img, target_w):
    """返回绘制上下文，符号居中"""
    draw = ImageDraw.Draw(img, 'RGBA')
    scale = symbol_scale(target_w)
    # 符号包围盒（24 空间）：x 6..22, y 3..23
    ox = (SIZE - (22 - 6) * scale) / 2 - (6 * scale)
    oy = (SIZE - (23 - 3) * scale) / 2 - (3 * scale)
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
