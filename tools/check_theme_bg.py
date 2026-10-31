"""检查：页面底色必须一致，且图标/启动页底色必须一致（**两者不是同一个值**）。

为什么需要这个断言：
    用户反馈「移动端状态栏背景的颜色和状态栏下面的颜色不一样，有色差」。
    这类 bug 的共性是——**同一个语义值散落在多个地方**，改了一处漏了多处，
    而且只有在真机上「状态栏 + 页面」紧贴时才看得出来，浏览器里预览不到。

⚠️ 2026-10-30 修正：这里其实是**两组**颜色，之前混成一组，是错的。
    取自 Scholarius 的 res/values/colors.xml：
        app_background_dark   #1E1E1E   ← 页面/窗口底色（--bg）
        ic_launcher_background #1B1B1B  ← 图标底色
    Livolog / Scholarius 的 `--bg` 实测都是 **#1E1E1E**，
    而图标底色仍是 #1B1B1B。两者不同，不能共用 EXPECTED。

A 组（页面底色 = #1E1E1E）：
      1. quant_terminal.py        桌面端页面 --bg
      2. app/src/styles.css       App 页面 --bg
      3. quant_terminal.py        <meta name="theme-color">
      4. app/index.html           <meta name="theme-color">
B 组（图标/启动页底色 = #1B1B1B）：
      5. app/scripts/patch-android.py  STATUS_BAR_COLOR
      6. app/scripts/patch-android.py  SPLASH_BG
      7. app/scripts/patch-android.py  windowSplashScreenBackground
      8. app/capacitor.config.ts       SplashScreen.backgroundColor
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 页面底色（--bg）：两个参考应用实测均为 #1E1E1E
EXPECTED = "#1E1E1E"
# 图标/启动页底色：保持 #1B1B1B（与 ic_launcher_background 一致）
EXPECTED_ICON = "#1B1B1B"

ok = True


def check(label: str, value: str | None, expected: str = EXPECTED) -> None:
    global ok
    good = value is not None and value.upper() == expected.upper()
    print(("  ✅ " if good else "  ❌ ") + f"{label}: {value}  (期望 {expected})")
    ok &= good


qt = (ROOT / "quant_terminal.py").read_text(encoding="utf-8")
m = re.search(r":root\{--bg:(#[0-9A-Fa-f]{6})", qt)
check("quant_terminal.py  --bg", m.group(1) if m else None)

css = (ROOT / "app" / "src" / "styles.css").read_text(encoding="utf-8")
m = re.search(r"--bg:\s*(#[0-9A-Fa-f]{6})", css)
check("app/src/styles.css --bg", m.group(1) if m else None)

patch = (ROOT / "app" / "scripts" / "patch-android.py").read_text(encoding="utf-8")
m = re.search(r'^STATUS_BAR_COLOR = "(#[0-9A-Fa-f]{6})"', patch, re.M)
check("patch STATUS_BAR_COLOR", m.group(1) if m else None, EXPECTED_ICON)
m = re.search(r'^SPLASH_BG = "(#[0-9A-Fa-f]{6})"', patch, re.M)
check("patch SPLASH_BG", m.group(1) if m else None, EXPECTED_ICON)
m = re.search(r"windowSplashScreenBackground\">(#[0-9A-Fa-f]{6})<", patch)
check("patch windowSplashScreenBackground", m.group(1) if m else None, EXPECTED_ICON)

cap = (ROOT / "app" / "capacitor.config.ts").read_text(encoding="utf-8")
m = re.search(r"backgroundColor:\s*'(#[0-9A-Fa-f]{6})'", cap)
check("capacitor SplashScreen.backgroundColor", m.group(1) if m else None, EXPECTED_ICON)

# ⚠️ theme-color 也会决定手机浏览器/状态栏的配色，漏了它同样出现色差
m = re.search(r'<meta name="theme-color" content="(#[0-9A-Fa-f]{6})"', qt)
check("quant_terminal.py  theme-color", m.group(1) if m else None)
m = re.search(r"background_color:'(#[0-9A-Fa-f]{6})', theme_color:'(#[0-9A-Fa-f]{6})'", qt)
check("quant_terminal.py  PWA background/theme", m.group(1) if m else None)
if m:
    check("quant_terminal.py  PWA theme_color", m.group(2))

# 移动端自有入口也要有 theme-color，否则 WebView 顶条与页面底色不一致
index_html = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
m = re.search(r'<meta name="theme-color" content="(#[0-9A-Fa-f]{6})"', index_html)
check("app/index.html    theme-color", m.group(1) if m else None)

print()
if ok:
    print(f"底线一致：全部为 {EXPECTED}")
else:
    print(f"存在不一致：以上必须全部改成 {EXPECTED}，否则真机上会出现色差接缝")
raise SystemExit(0 if ok else 1)
