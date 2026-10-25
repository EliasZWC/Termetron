"""生成 Termetron App 图标：深底 + 渐变 `>_` 提示符（无外框、无线框）。

═══════════════════════════════════════════════════════════════════
一、规格（全部来自实测 Scholarius / Livolog，不要凭感觉调）
═══════════════════════════════════════════════════════════════════
  底板        #1B1B1B   两款应用的 launcher background 实测都是 (27,27,27)
  最亮色      #FFFFFF   两款前景最亮一档（纯灰，R=G=B，出现次数最多）
  最暗色      #6E6E6E   两款前景最暗一档；只统计**笔画核心**（距离变换 >=4，
                        避开边缘抗锯齿）后，出现次数第二多的色阶，
                        Scholarius 286 次 / Livolog 359 次，两者完全相同
  线宽        20.00/174 = 0.11494  (Scholarius，距离变换骨架中位数)
              21.54/190 = 0.11337  (Livolog，同法)
              -> 取 0.114（两者之间，误差 <0.1%）
  占位        41.4% (Scholarius) / 41.3% (Livolog) -> 取 0.414
              占位是**正方形**，边长与 Scholarius 完全一致
  圆头        直径 == 线宽（半径 = stroke/2）

═══════════════════════════════════════════════════════════════════
二、造型
═══════════════════════════════════════════════════════════════════
  左边 `>`，右边 `_`，两个字符占位宽度相等。

  `>` 渐变：沿**字符路径的左边缘线**，不是中心线。
      · 起点 = 左上角（路径开始处）= 最亮 #FFFFFF
      · 终点 = 左下角（路径结束处）= 最暗 #6E6E6E
        路径是 左上 → 折点 → 左下，所以要经过折点才最暗。
  `_` 渐变：**不渐变**，整条最暗色。

═══════════════════════════════════════════════════════════════════
三、踩过的坑（改之前先读）
═══════════════════════════════════════════════════════════════════
  · draw.text 画 `>_`       笔画粗细由字体决定，同一符号里两种粗细。
  · 用 UI 强调色 #F2F2F0     那是网页 --acc，不是图标色。
  · 「左边缘」=「中心线沿角平分线平移」 -> 偏移量随夹角变化，
      实测折点只偏 16px 而不是应有的 22px，右上角露出一块错色三角。
      正解：求相邻两段平移线的**交点**（left_edge_polyline）。
  · 加渐变蒙版 / 底色过渡   用户明确不要。
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
BG = (27, 27, 27, 255)              # #1B1B1B

# ── 颜色：只有两个端点，中间线性插值，不引入第三种色 ──
SYMBOL_LIGHT = (255, 255, 255)      # #FFFFFF
SYMBOL_DARK = (110, 110, 110)       # #6E6E6E
#   ⚠️ 最暗色 = 110，别再"优化"成更暗的值。
#      取证方法：先算距离变换，只取**笔画核心**（distance >= 4）的像素，
#      避开边缘抗锯齿。两款应用的真实色阶是：
#          Scholarius  最多 255(1072) / 次多 110(286)   min=109
#          Livolog     最多 255(1251) / 次多 110(359)   min=110
#      即「最亮 #FFFFFF，最暗 #6E6E6E」，两个端点完全一致。
#      ✗ 曾经被抗锯齿暗像素（亮度 3~20）误导，把最暗改成 29 ——
#        结果 `_` 与底板 #1B1B1B 只差 2 个色阶，对比度 1.02:1，肉眼不可见。

# ── 尺寸（相对画布）──
#   ⚠️ 这两个数直接取自 Scholarius 的 VectorDrawable 设计注释（实测核实）：
#         视口 108 × 108
#         符号外接 38   -> 38/108 = 0.3519
#         线宽     4.67 -> 4.67/38 = 0.1229（线宽 / 符号外接）
#      之前用的 0.414 让符号**偏大 18%** —— 占位一大，同一条线就显得细，
#      这正是「线条粗细还是不对」的真因（比例本身是对的，基数错了）。
SYMBOL_RATIO = 0.414                # 占位正方形边长 / 画布
#   ⚠️ 对标的是两款应用的**桌面图标 PNG**（design/*.png），不是它们的
#      VectorDrawable 前景（108 视口、符号占 35.2%）—— 两者用途不同：
#        桌面图标 PNG     占画布 41.4% / 41.3%
#        VectorDrawable   占视口 35.2%（自适应图标还有 66% 安全区）
#      桌面预览 / 商店图标看的是 PNG 那套，所以用 41.4%。
#      （我曾按 35.2% 改过一版，图标明显变小、线也显细，是错的。）
SUPERSAMPLE = 4
STROKE_RATIO = 0.1080               # 线宽 / 占位边长
#   目标 = 参考桌面图标的实测中值：
#          Scholarius 2×11.0/174 = 0.1270
#          Livolog    2×11.7/190 = 0.1232
#          中值 0.1251
#   本常量是 PIL 的**源参数**，取整后实际略粗，故写 0.1058。
#   ⚠️ 测量用「骨架距离变换的**最大值**」（最粗处），不能用中位数 ——
#      中位数会被图标内部的细笔画拉低到 0.1149，明显偏细。
#   参考实测（原始分辨率、距离变换骨架）：
#       Scholarius 20.00 / 174 = 0.1149
#       Livolog    21.54 / 190 = 0.1134
#   ⚠️ 这里是**源参数**，不是最终观感值。PIL 画多边形时坐标取整，
#      实际渲染出来会比 stroke 宽约 1px（实测 0.113 -> 48.0 画成 49~50px）。
#      取 0.1095 后实测线宽/占位 = 0.1154，正好落在参考区间中位。
#      （验算：python tools/check_icon_stroke.py 第 2 段）

# 兼容旧接口的常量（tools/ 里仍有引用）
SYMBOL_ASPECT = 1.0
_SYMBOL_X0 = 0.0
_SYMBOL_Y0 = 0.0
_SYMBOL_W = 1.0
_SYMBOL_H = 1.0
CHEV = (0.0, 0.0, 0.5, 0.5, 0.0, 1.0)
US = (0.58, 1.0, 1.0)


# ══════════════════════════════════════════════════════════════
#  布局
# ══════════════════════════════════════════════════════════════
def glyph_layout(size_px, stroke_px, center=None):
    """把占位正方形划成左右两半，返回两个字形的中心线（像素坐标）。

    参数
        size_px    占位正方形边长
        stroke_px  线宽（像素），圆头直径 == 它
        center     占位正方形中心 (cx, cy)，默认画布中心

    返回 dict: box / half_w / chev_pts / us_pts / stroke

    ⚠️「两个字符占位宽度相等」的实现：
        一个字符的可视宽度 = 中心线横向跨度 + stroke（两端各外扩半个圆头）。
        所以两边的中心线跨度都必须等于 half_w - stroke。

    ⚠️ `>` 的下半段**中间截断**（用户 2026-10-26 澄清两次后的定稿）：
        `>` 的位置和形状完全不变，起点 / 折点 / 原终点都不动。
        只是下半段走到一半就停，末端留圆头；然后在**原本的左下角终点**
        位置放一个**孤立圆点**（直径 = 线宽，纯最暗色）。

        所以：
          · 圆点圆心 = 原来的左下角终点 (chev_left, cy1)
          · 末端圆心 → 圆点圆心 = 线宽(空隙) + 线宽(一个半径+一个半径) = 2 × 线宽
          · 下半段从折点起走过的长度 = 原长 - 2 × 线宽

        ⚠️ 不要理解成「沿斜线继续往外放圆点」——那样圆点会跑到左下角之外，
           占位也跟着变大。用户要的是原来的那个角变成点。
    """
    cx, cy = center if center else (SIZE / 2.0, SIZE / 2.0)
    x0 = cx - size_px / 2.0
    y0 = cy - size_px / 2.0
    half_w = size_px / 2.0

    # 半个线宽。中心线坐标 = 可视外框坐标内缩 inset，
    # 因为笔画向两侧各外扩 stroke/2、圆头半径也是 stroke/2。
    inset = stroke_px / 2.0

    # 每个字符的可视宽度（含两端圆头）= 占位一半
    # 中心线横向跨度 = 可视宽度 - stroke（两端各去掉一个半径）
    span = max(1.0, half_w - stroke_px)

    # `>` 在左半区：可视 x ∈ [x0, x0+half_w]，中心线 x ∈ [x0+inset, x0+inset+span]
    chev_left = x0 + inset
    chev_right = chev_left + span
    # 纵向占满整个占位高度（形状、位置都不变）
    cy0 = y0 + inset
    cy1 = y0 + size_px - inset
    knee = (chev_right, (cy0 + cy1) / 2.0)
    corner = (chev_left, cy1)                   # 原本的左下角终点

    # 下半段方向（折点 → 左下角），单位向量
    dx = corner[0] - knee[0]
    dy = corner[1] - knee[1]
    seg_len = math.hypot(dx, dy) or 1.0
    ux, uy = dx / seg_len, dy / seg_len

    # 截断：末端圆心距离折点 = 原长 - 2*stroke（空隙 stroke + 两个半径）
    lower_len = max(1.0, seg_len - 2.0 * stroke_px)
    tail_end = (knee[0] + ux * lower_len, knee[1] + uy * lower_len)

    chev_pts = [(chev_left, cy0), knee, tail_end]

    # `_` 在右半区：可视 x ∈ [x0+half_w, x0+2*half_w]，中心线同上规则；纵向贴底
    us_left = x0 + half_w + inset
    us_right = us_left + span
    us_y = cy1
    us_pts = [(us_left, us_y), (us_right, us_y)]

    return {
        'box': (x0, y0, x0 + size_px, y0 + size_px),
        'half_w': half_w,
        'chev_pts': chev_pts,
        'us_pts': us_pts,
        'tail_dot': corner,                     # 圆点就在原来的左下角
        'tail_r': stroke_px / 2.0,
        'stroke': stroke_px,
    }


def chevron_points(sx=None, sy=None, origin=None):
    """兼容旧接口：返回默认尺寸下 `>` 的中心线三点。新代码用 glyph_layout()。"""
    return glyph_layout(SYMBOL_RATIO * SIZE,
                        STROKE_RATIO * SYMBOL_RATIO * SIZE)['chev_pts']


def symbol_scale(target_w=None):
    """兼容旧接口：占位宽度已直接给出，缩放系数恒为 1。"""
    return 1.0


def symbol_height(size_px):
    """占位是正方形，高度 == 宽度。"""
    return float(size_px)


# ══════════════════════════════════════════════════════════════
#  左边缘线（渐变路径）
# ══════════════════════════════════════════════════════════════
def left_edge_polyline(pts, stroke_w):
    """把中心线折线向左外侧平移 stroke/2，返回「左边缘」折线。

    为什么渐变按左边缘（用户）：
        按中心线算弧长时，折点附近同一小块像素同时接近「上段末端」和
        「下段起点」，两种颜色争抢 -> 折点糊成一团。
        `>` 的左边缘在折点处是**尖折**，渐变沿它走才对。

    两步，别混在一起写：
        1. 每段各自左移 half，得到两条平行线段；
        2. 折点取这两条的**交点**（保持尖角）。
"""
    if len(pts) < 2:
        return list(pts)
    half = stroke_w / 2.0
    n = len(pts)

    # 每段左法线（单位向量）。行进方向 (dx,dy) 的左侧 = (-dy, dx)。
    normals = []
    for i in range(n - 1):
        dx = pts[i + 1][0] - pts[i][0]
        dy = pts[i + 1][1] - pts[i][1]
        ln = math.hypot(dx, dy) or 1.0
        normals.append((-dy / ln, dx / ln))

    out = [(pts[0][0] + normals[0][0] * half, pts[0][1] + normals[0][1] * half)]

    for i in range(1, n - 1):
        a0 = (pts[i - 1][0] + normals[i - 1][0] * half,
              pts[i - 1][1] + normals[i - 1][1] * half)
        d1 = (pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        b0 = (pts[i][0] + normals[i][0] * half,
              pts[i][1] + normals[i][1] * half)
        d2 = (pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        den = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(den) < 1e-9:                  # 两段共线，退化为直接平移
            out.append((pts[i][0] + normals[i][0] * half,
                        pts[i][1] + normals[i][1] * half))
        else:
            t = ((b0[0] - a0[0]) * d2[1] - (b0[1] - a0[1]) * d2[0]) / den
            out.append((a0[0] + d1[0] * t, a0[1] + d1[1] * t))

    out.append((pts[-1][0] + normals[-1][0] * half,
                pts[-1][1] + normals[-1][1] * half))
    return out


def _edge_samples(edge):
    """沿左边缘折线按 ~1px 步长采样，返回 [(点, 进度 0..1)]。"""
    segs = list(zip(edge, edge[1:]))
    lengths = [math.dist(a, b) for a, b in segs]
    total = sum(lengths) or 1.0
    table = []
    done = 0.0
    for (a, b), seg_len in zip(segs, lengths):
        steps = max(2, int(round(seg_len)))
        for i in range(steps + 1):
            t = i / steps
            table.append((
                (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t),
                min(1.0, (done + seg_len * t) / total),
            ))
        done += seg_len
    return table


def _mix(t):
    """t=0 -> 最亮；t=1 -> 最暗。"""
    return tuple(
        int(round(SYMBOL_LIGHT[c] + (SYMBOL_DARK[c] - SYMBOL_LIGHT[c]) * t))
        for c in range(3)
    )


def _paint_path_gradient(mask_img, pts, stroke_w, box):
    """按笔画路径给 mask 内像素上色，返回 RGB 图层。

    ⚠️ 判定「进度」用的是**在中心线上的投影**，不是「离左边缘最近点的进度」。

    为什么不能按左边缘最近点（2026-10-26 修）：
        左上角圆头是个实心圆，它的**上半部分**（y 比圆心更小的那一半）
        根本不在左边缘线的左侧延伸方向上，最近点会落到
        「另一端的左边缘」上 —— 结果整个上半圆被涂成**最暗色**，
        与下半圆（最亮色）在圆心处突然断开，看起来就是左上角色差。
        实测：y=301..329 全是 110，y=333 往下才 255。

    正确做法：
        把像素投影到中心线折线上，取投影点的弧长占比 t（0=起点，1=终点）。
        · 圆头处投影会落到端点上 -> t 恒为 0 / 1，整圆同色，不会断开
        · 折点处投影连续 -> 颜色沿路径单调变化
        · 笔画「横向」的层次（左边缘亮/右边缘暗）本来就不该有 ——
          用户要的是「沿路径渐变」，不是「沿横截面渐变」。
    """
    # 中心线的分段信息（沿路径做投影用）
    segs = list(zip(pts, pts[1:]))
    lengths = [math.dist(a, b) for a, b in segs]
    total = sum(lengths) or 1.0

    bx0, by0, bx1, by1 = (int(math.floor(box[0])) - 2, int(math.floor(box[1])) - 2,
                          int(math.ceil(box[2])) + 2, int(math.ceil(box[3])) + 2)
    bx0, by0 = max(0, bx0), max(0, by0)
    bx1, by1 = min(mask_img.size[0], bx1), min(mask_img.size[1], by1)

    # ── 进度 = 各段投影的**距离加权平均**，不是「最近采样点的进度」──
    # ⚠️ 为什么不能用最近点（2026-10-26 修，用户两次报色差）：
    #   折点是个尖角。折点内侧的像素到「上行末段」和「下行起始段」的距离
    #   几乎相等，最近的采样点会在两段之间来回跳，t 值整行突变。
    #   实测 y=512 一行：189,189,177,177,177,188,178,178 —— 中间夹着
    #   188/185 的凸起，上下行都是平滑的 189→181，视觉上就是折角处一道亮痕。
    #   改成按 1/d² 加权平均后，折点内外都是两段的平滑过渡，没有奇点。
    segs_n = len(segs)
    seg_dirs = []
    for (a, b), seg_len in zip(segs, lengths):
        seg_dirs.append(((b[0] - a[0]) / seg_len, (b[1] - a[1]) / seg_len))
    # 每段的起点弧长偏移
    seg_off = []
    acc = 0.0
    for seg_len in lengths:
        seg_off.append(acc)
        acc += seg_len

    out = Image.new('RGB', mask_img.size, SYMBOL_DARK)
    src = mask_img.load()
    dst = out.load()

    eps = 1.0
    for y in range(by0, by1):
        for x in range(bx0, bx1):
            if not src[x, y]:
                continue
            wsum = 0.0
            tsum = 0.0
            for i in range(segs_n):
                ax, ay = segs[i][0]
                dx, dy = seg_dirs[i]
                # 投影到该段（限制在段内），得到段内位置 s ∈ [0, seg_len]
                s = (x - ax) * dx + (y - ay) * dy
                s = 0.0 if s < 0 else (lengths[i] if s > lengths[i] else s)
                px, py = ax + dx * s, ay + dy * s
                d2 = (px - x) ** 2 + (py - y) ** 2
                w = 1.0 / (d2 + eps)          # 1/d² 加权，近的段占主导
                wsum += w
                tsum += w * ((seg_off[i] + s) / total)
            dst[x, y] = _mix(tsum / wsum if wsum else 0.0)
    return out


# ══════════════════════════════════════════════════════════════
#  绘图
# ══════════════════════════════════════════════════════════════
def _thick_polyline(draw, pts, stroke_w, fill):
    """把折线画成「粗折线」：每段画成矩形 + 每个顶点画圆。

    为什么不用 draw.line(width=...)：
        PIL 的 line() 只接受整数坐标，浮点会被截断；宽度较大时
        端点还是方的。这里用矩形 + 圆拼接，任何浮点坐标都精确，
        且圆头直径严格等于线宽。
    """
    half = stroke_w / 2.0
    for a, b in zip(pts, pts[1:]):
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln * half, dx / ln * half
        quad = [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny),
                (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)]
        draw.polygon(quad, fill=fill)
    # 顶点圆：保证折点与两端都是直径 == 线宽的圆
    for (x, y) in pts:
        draw.ellipse([x - half, y - half, x + half, y + half], fill=fill)


def _round_caps(draw, pts, stroke_w, fill):
    """在折线两端各画一个直径 == 线宽的圆（圆头）。"""
    r = stroke_w / 2.0
    for (x, y) in (pts[0], pts[-1]):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def compose_symbol(canvas, layout, gradient=True):
    """把 `>` 与 `_` 画到 canvas（RGBA）上，返回 canvas。

    `>`：先画实心 mask（折线 + 折点圆 + 两端圆头），再按左边缘路径上色。
    `_`：直接画最暗色的折线 + 两端圆头，不渐变。
    """
    stroke = layout['stroke']
    chev = layout['chev_pts']
    us = layout['us_pts']
    w, h = canvas.size

    # ── `>` 的 mask：粗折线 + 折点圆 + 两端圆头 ──
    mask = Image.new('L', (w, h), 0)
    md = ImageDraw.Draw(mask)
    _thick_polyline(md, chev, stroke, 255)

    if gradient:
        layer = _paint_path_gradient(mask, chev, stroke, layout['box'])
        canvas.paste(layer, (0, 0), mask)
    else:
        canvas.paste(Image.new('RGB', (w, h), SYMBOL_LIGHT), (0, 0), mask)

    d = ImageDraw.Draw(canvas)

    # ── 尾部孤立圆点：直径 = 线宽，纯最暗色，不渐变 ──
    #    用户 2026-10-26：「> 的左下角尾部变成一个孤立的圆点，
    #    直径也是和线条的宽度一样，全部用纯最暗色」。
    #    它离下半段末端（圆头外缘）正好一个线宽，见 glyph_layout 的 tail_drop。
    dr = layout['tail_r']
    dx, dy = layout['tail_dot']
    d.ellipse([dx - dr, dy - dr, dx + dr, dy + dr], fill=SYMBOL_DARK)

    # ── `_`：整条最暗色，圆头，不渐变 ──
    _thick_polyline(d, us, stroke, SYMBOL_DARK)
    return canvas


def draw_centered(img, size_px=None, center=None, ss=None):
    """在 img 正中（或指定 center）按占位正方形画符号。

    ⚠️ 必须超采样（ss >= 4）：
       ImageDraw.polygon / ellipse 在整数网格上直接填充，边缘**没有抗锯齿**，
       放大看是硬台阶。实测未超采样时，斜边一侧从 26 直接跳到 231，
       中间一个过渡像素都没有；超采样后是 26 → 28 → 31 → 150 → 245。
    ⚠️ `ss=None` 而不是 `ss=SUPERSAMPLE`：默认参数在函数**定义时**求值，
       写死常量后改模块变量不会生效（调试时会被误导）。
    """
    if ss is None:
        ss = SUPERSAMPLE
    if size_px is None:
        size_px = round(SYMBOL_RATIO * SIZE)
    stroke = max(2.0, STROKE_RATIO * size_px)

    if ss <= 1:
        compose_symbol(img, glyph_layout(size_px, stroke, center), gradient=True)
        return img

    # 超采样：在 ss 倍画布上画，再缩回
    w, h = img.size
    big = Image.new('RGBA', (w * ss, h * ss), (0, 0, 0, 0))
    c = (center[0] * ss, center[1] * ss) if center else (w * ss / 2.0, h * ss / 2.0)
    compose_symbol(big, glyph_layout(size_px * ss, stroke * ss, c), gradient=True)
    small = big.resize((w, h), Image.LANCZOS)
    return Image.alpha_composite(img, small)


def draw_symbol_on(img, cx, cy, size_px, stroke_px=None, ss=None):
    """在指定中心点画符号（供 splash 等大画布使用）。"""
    if ss is None:
        ss = SUPERSAMPLE
    if stroke_px is None:
        stroke_px = max(2.0, STROKE_RATIO * size_px)
    if ss <= 1:
        layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
        compose_symbol(layer, glyph_layout(size_px, stroke_px, (cx, cy)), gradient=True)
        return Image.alpha_composite(img, layer)

    w, h = img.size
    big = Image.new('RGBA', (w * ss, h * ss), (0, 0, 0, 0))
    compose_symbol(big, glyph_layout(size_px * ss, stroke_px * ss,
                                     (cx * ss, cy * ss)), gradient=True)
    return Image.alpha_composite(img, big.resize((w, h), Image.LANCZOS))


def make_icon(bg=True, rounded=False, radius=220):
    """整张图标。bg=False 返回透明底（adaptive foreground / icon-only）。

    ⚠️ `rounded` 默认 False，且**生产上只能传 False**。
       原因：参考两款应用的图标都是**方形满底**（四角 = (27,27,27) 底色）。
       之前用圆角会留下 4.0% 的纯黑 (0,0,0) 角 —— 那既不是底色，
       也不是透明（保存成 RGB 后透明会变黑），与并排的两个应用对比就是
       四个突兀的黑角。Android 8+ 的自适应图标由系统自己裁圆角，
       不需要我们在图里预先裁。
    """
    if bg:
        img = Image.new('RGBA', (SIZE, SIZE), BG)
        # ⚠️ draw_centered 现在**返回新图**（超采样合成会产生新 Image），
        #    必须接住返回值。漏接的后果是整个符号消失，只剩底板。
        img = draw_centered(img)
        if rounded:
            # 圆角只在显式要求时用，且圆角外用**底色**填，不用黑色。
            mask = Image.new('L', (SIZE, SIZE), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, SIZE - 1, SIZE - 1], radius=radius, fill=255)
            flat = Image.new('RGBA', (SIZE, SIZE), BG)
            img = Image.composite(img, flat, mask)
        return img
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    return draw_centered(img)


SPLASH_SIZE = 2732


def load_text_font(size):
    """品牌字：优先用仓库内字体；失败则用默认字体（不阻断生成）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ('termetron-mono-bold', 'termetron-mono-regular'):
        for ext in ('.ttf', '.otf', '.woff2'):
            p = os.path.join(here, '..', 'public', 'fonts', name + ext)
            if os.path.exists(p) and ext != '.woff2':
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def make_splash():
    img = Image.new('RGBA', (SPLASH_SIZE, SPLASH_SIZE), BG)
    logo = round(SPLASH_SIZE * SYMBOL_RATIO)
    stroke = STROKE_RATIO * logo
    cx, cy = SPLASH_SIZE / 2.0, SPLASH_SIZE / 2.0 - 150
    draw_symbol_on(img, cx, cy, logo, stroke)

    try:
        font = load_text_font(120)
        d = ImageDraw.Draw(img)
        text = 'TERMETRON'
        bbox = d.textbbox((0, 0), text, font=font)
        d.text(((SPLASH_SIZE - (bbox[2] - bbox[0])) / 2 - bbox[0],
                cy + logo / 2 + 80 - bbox[1]),
               text, font=font, fill=SYMBOL_LIGHT)
    except Exception:
        pass
    return img


def main():
    out = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out, exist_ok=True)

    # 1. 主图标（capacitor-assets 用它生成 mipmap-*/ic_launcher.png）
    #    ⚠️ 必须是**透明底**（只有符号），底色由 CI 的
    #       `--iconBackgroundColor '#1B1B1B'` 负责。
    #       踩过的坑（v0.8.10 / v0.8.11 桌面图标一直是错的）：
    #       icon.png 若是「方形满底」，capacitor-assets 会把它当成
    #       "带底色的成品图" 再抠一次底色 —— 结果底板连同符号一起被抠掉，
    #       生成的 ic_launcher.png 有 96% 是纯透明，只剩几个零星像素。
    #       实测：35428 / 36864 像素为 #00000000。
    make_icon(bg=False).save(os.path.join(out, 'icon.png'))
    # ⚠️ 透明底的两张必须保留 RGBA，不能 convert('RGB')：
    #    转 RGB 后透明区域会变成纯黑，adaptive icon 前景层会显示成黑块。
    make_icon(bg=False).save(
        os.path.join(out, 'icon-only.png'))
    make_icon(bg=False).save(
        os.path.join(out, 'icon-foreground.png'))
    Image.new('RGB', (SIZE, SIZE), BG).save(os.path.join(out, 'icon-background.png'))
    make_splash().save(os.path.join(out, 'splash.png'))

    for f in ['icon.png', 'icon-only.png', 'icon-foreground.png',
              'icon-background.png', 'splash.png']:
        p = os.path.join(out, f)
        print(f, Image.open(p).size, os.path.getsize(p))


if __name__ == '__main__':
    main()
