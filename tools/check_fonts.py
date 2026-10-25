"""检查：构建后字体引用路径与 Capacitor 本地服务器的解析是否一致。

背景：
    vite.config.ts 里 base = './'，但 CSS 中的 @font-face 用的是**绝对路径**
    url('/fonts/termetron-mono-*.woff2')。

    这两个不冲突，但必须确认清楚，否则 Android 上会出现"字体在 Windows 浏览器里
    好好的、装到手机里变成系统字体"这种只在真机上暴露的问题：

      · 文件位置：app/public/fonts/  ->  vite 原样复制到  www/fonts/
      · App 内 WebView 的 origin 是 https://localhost（capacitor.config.ts 的 androidScheme）
        所以 /fonts/xxx -> https://localhost/fonts/xxx -> www/fonts/xxx  ✅
      · 桌面端由 quant_terminal.py 直接提供 /fonts/xxx 路由  ✅
      · 用 file:// 直接打开 index.html 时会失败（没有 host），这只是本地预览限制，
        不影响 App 与桌面端。

本脚本断言：
    1. app/public/fonts 下两个 woff2 存在，且与 Scholarius 的字体逐字节相同；
    2. index.html 的 @font-face 引用的文件名与实际文件一致；
    3. capacitor.config.ts 的 androidScheme 产生 https origin（绝对路径可解析）；
    4. quant_terminal.py 有 /fonts/ 路由且做了白名单。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "app" / "public" / "fonts"
HTML = ROOT / "app" / "index.html"
CAP = ROOT / "app" / "capacitor.config.ts"
QT = ROOT / "quant_terminal.py"
REF = Path("E:/product/Scholarius/app/src/main/assets/www/fonts")

ok = True


def check(cond: bool, msg: str) -> None:
    global ok
    print(("  ✅ " if cond else "  ❌ ") + msg)
    ok &= cond


print("== 1) 字体文件存在且与 Scholarius 逐字节相同 ==")
for local, ref in [("termetron-mono-regular.woff2", "scholarius-mono-regular.woff2"),
                   ("termetron-mono-bold.woff2", "scholarius-mono-bold.woff2")]:
    lp, rp = FONTS / local, REF / ref
    check(lp.is_file(), f"{local} 存在")
    if lp.is_file() and rp.is_file():
        lh = hashlib.sha256(lp.read_bytes()).hexdigest()
        rh = hashlib.sha256(rp.read_bytes()).hexdigest()
        check(lh == rh, f"{local} 与 Scholarius {ref} 内容一致（{lh[:12]}）")

print("\n== 2) index.html 的 @font-face 引用 ==")
html = HTML.read_text(encoding="utf-8")
urls = re.findall(r"url\('(/fonts/[^']+)'\)", html)
print(f"  引用：{urls}")
for u in urls:
    fname = u.rsplit("/", 1)[-1]
    check((FONTS / fname).is_file(), f"{u} 对应的文件存在")
check(len(urls) == 2, "两个 @font-face 都用绝对路径 /fonts/")

print("\n== 3) Capacitor origin ==")
cap = CAP.read_text(encoding="utf-8")
check("androidScheme: 'https'" in cap,
      "androidScheme = https -> 页面 origin 是 https://localhost，绝对路径可解析")

print("\n== 4) 桌面端 /fonts/ 路由 ==")
qt = QT.read_text(encoding="utf-8")
check('path.startswith("/fonts/")' in qt, "do_GET 里有 /fonts/ 分支")
check("termetron-mono-regular.woff2" in qt and "termetron-mono-bold.woff2" in qt,
      "字体文件名白名单存在（防路径穿越）")

print("\n" + ("字体链路完整" if ok else "字体链路有问题"))
raise SystemExit(0 if ok else 1)
