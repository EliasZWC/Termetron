"""termqr.py - 在终端里画二维码（纯文本），供 `termetron qr` 使用。

## 为什么终端里要画二维码

手机端要配对就必须先知道"隧道码 + 当前 URL"，而 URL 是随机生成的
一长串域名，手动敲不现实。二维码让手机**扫一下就填好**，用户完全不
用接触网址。

## 二维码里放什么

**只放隧道码和地址，不放密码。**

二维码是显示在屏幕上的，可能被旁人看到、被截图。密码必须由用户
自己在手机上输入 —— 那是唯一不经过任何屏幕的信息。

格式（单行，便于扫码后一次解析）：

    termetron://pair?code=ABC123&url=https%3A%2F%2Fxxx.trycloudflare.com

用自定义 scheme 而不是 https，是因为它明确表示"这是给 App 的配对
指令"，不会被浏览器当成普通链接打开。

## 为什么用 ▀ 半块字符

一个字符格 = 2 个垂直像素。用 ▀（上黑下白）/▄（上白下黑）/█（全黑）
/空格（全白）四种组合，可以在一行文字里表示两行像素，二维码的高度
直接减半 —— 否则 25×25 的码在终端里会占到 25 行以上，超出屏幕。

⚠️ 前提是终端字体行高恰为字宽的 2 倍。绝大多数等宽终端满足；
不满足时码会略微拉长，但多数扫码器仍能识别（二维码有纠错）。
"""

from __future__ import annotations

import urllib.parse

# 二维码前缀：App 据此识别这是配对指令
SCHEME = "termetron://pair"


def payload(code: str, url: str) -> str:
    """生成配对串（不含密码）。"""
    q = urllib.parse.urlencode({"code": (code or "").upper(), "url": url or ""})
    return f"{SCHEME}?{q}"


def parse_payload(text: str) -> dict | None:
    """从扫描结果解析出 {code, url}；不是配对串则返回 None。"""
    if not text or not text.startswith("termetron://pair"):
        return None
    try:
        qs = urllib.parse.urlparse(text).query
        d = urllib.parse.parse_qs(qs)
        code = (d.get("code") or [""])[0].strip().upper()
        url = (d.get("url") or [""])[0].strip()
        if not code or not url.startswith("http"):
            return None
        return {"code": code, "url": url}
    except Exception:  # noqa: BLE001
        return None


def _load_qrcode():
    """导入 qrcode 库；没装就返回 None（调用方退回文本显示）。

    依赖由用户自行安装：python3 -m pip install qrcode
    不在这里自动 pip install —— 擅自改用户的 Python 环境是不礼貌的。
    """
    try:
        import qrcode  # type: ignore
        return qrcode
    except ImportError:
        return None


def render(text: str, *, invert: bool = False) -> str | None:
    """把 text 画成终端二维码。缺库时返回 None。

    invert：深色底终端用默认值（黑块=亮），浅色底终端用 invert=True
            （把黑白对调），否则"亮块"在白底上根本看不见。
    """
    qrcode = _load_qrcode()
    if qrcode is None:
        return None

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=2,
    )
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()   # list[list[bool]]

    # 补成偶数行，方便两行像素合并成一个字符
    if len(matrix) % 2:
        w = len(matrix[0])
        matrix.append([False] * w)

    BLK, WHT = "\u2588", " "        # █ 和 空格
    UP, LOW = "\u2580", "\u2584"    # ▀ 和 ▄

    lines = []
    for y in range(0, len(matrix), 2):
        top = matrix[y]
        bot = matrix[y + 1]
        row = []
        for x in range(len(top)):
            t = top[x]
            b = bot[x]
            if invert:
                t, b = not t, not b
            if t and b:
                row.append(BLK)
            elif t and not b:
                row.append(UP)
            elif not t and b:
                row.append(LOW)
            else:
                row.append(WHT)
        lines.append("".join(row))
    return "\n".join(lines)
