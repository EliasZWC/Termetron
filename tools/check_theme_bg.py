"""检查：所有「页面底色 / 状态栏 / 启动页」相关的色值必须完全一致。

为什么需要这个断言：
    用户反馈「移动端状态栏背景的颜色和状态栏下面的颜色不一样，有色差」。
    这类 bug 的共性是——**同一个语义值散落在 5 个地方**，改了一处漏了四处，
    而且只有在真机上「状态栏 + 页面」紧贴时才看得出来，浏览器里预览不到。

    参与比对的 8 处（全部应该是同一个值 #1B1B1B）：
      1. quant_terminal.py        桌面端页面 --bg
      2. app/index.html           App 页面 --bg
      3. app/scripts/patch-android.py  STATUS_BAR_COLOR（模块级常量）
      4. app/scripts/patch-android.py  SPLASH_BG + windowSplashScreenBackground
      5. app/capacitor.config.ts  SplashScreen.backgroundColor
      6. quant_terminal.py        <meta name="theme-color">（手机浏览器地址栏）
      7. quant_terminal.py        PWA manifest 的 background_color / theme_color
      8. app/index.html           <meta name="theme-color">

判据：1~8 全部相同，且等于常量 EXPECTED。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = "#1B1B1B"

ok = True


def check(label: str, value: str | None) -> None:
    global ok
    good = value is not None and value.upper() == EXPECTED.upper()
    print(("  ✅ " if good else "  ❌ ") + f"{label}: {value}")
    ok &= good


qt = (ROOT / "quant_terminal.py").read_text(encoding="utf-8")
m = re.search(r":root\{--bg:(#[0-9A-Fa-f]{6})", qt)
check("quant_terminal.py  --bg", m.group(1) if m else None)

html = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
m = re.search(r"--bg:\s*(#[0-9A-Fa-f]{6})", html)
check("app/index.html    --bg", m.group(1) if m else None)

patch = (ROOT / "app" / "scripts" / "patch-android.py").read_text(encoding="utf-8")
m = re.search(r'^STATUS_BAR_COLOR = "(#[0-9A-Fa-f]{6})"', patch, re.M)
check("patch STATUS_BAR_COLOR", m.group(1) if m else None)
m = re.search(r'^SPLASH_BG = "(#[0-9A-Fa-f]{6})"', patch, re.M)
check("patch SPLASH_BG", m.group(1) if m else None)
m = re.search(r"windowSplashScreenBackground\">(#[0-9A-Fa-f]{6})<", patch)
check("patch windowSplashScreenBackground", m.group(1) if m else None)

cap = (ROOT / "app" / "capacitor.config.ts").read_text(encoding="utf-8")
m = re.search(r"backgroundColor:\s*'(#[0-9A-Fa-f]{6})'", cap)
check("capacitor SplashScreen.backgroundColor", m.group(1) if m else None)

# ⚠️ theme-color 也会决定手机浏览器/状态栏的配色，漏了它同样出现色差
m = re.search(r'<meta name="theme-color" content="(#[0-9A-Fa-f]{6})"', qt)
check("quant_terminal.py  theme-color", m.group(1) if m else None)
m = re.search(r"background_color:'(#[0-9A-Fa-f]{6})', theme_color:'(#[0-9A-Fa-f]{6})'", qt)
check("quant_terminal.py  PWA background/theme", m.group(1) if m else None)
if m:
    check("quant_terminal.py  PWA theme_color", m.group(2))
m = re.search(r'<meta name="theme-color" content="(#[0-9A-Fa-f]{6})"', html)
check("app/index.html    theme-color", m.group(1) if m else None)

print()
if ok:
    print(f"底线一致：全部为 {EXPECTED}")
else:
    print(f"存在不一致：以上必须全部改成 {EXPECTED}，否则真机上会出现色差接缝")
raise SystemExit(0 if ok else 1)
