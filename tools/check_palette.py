r"""配色一致性检查：Termetron 用到的颜色必须全部来自 Scholarius / Livolog 的调色板。

设计约束（用户明确要求）：
    配色方案只用 Scholarius 与 Livolog 已有的中性灰阶，不出现那两款应用里没有的颜色。
    ——「朴素简洁却好看」，任何突兀的彩色都是 bug，不是风格。

判据（唯一判据，不靠眼睛看）：
    1. 收集 Scholarius / Livolog 的 styles.css、colors.xml、themes.xml 中所有
       #RRGGBB 与 rgba()/rgb() 的颜色，构成「允许集合」。
    2. 收集 Termetron 各源文件里出现的颜色。
    3. 逐个比对，不在允许集合里的就是「外来颜色」。

⚠️ rgba 只比 rgb 三元组，alpha 可以不同（同一颜色不同透明度是允许的）。

补充人工判据（脚本管不到的部分）：
    · 语义色只准用调色板里已有的 --danger（#C6211A / #E06C5E）与 --success（#15803D / #7EE2A8），
      不要再引入新的红/绿/蓝/紫。
    · 不使用纯黑 #000000 / 纯白 #FFFFFF 作为界面底色（底部色不要贴到纯黑/纯白）。
      例外：二维码、终端 ANSI 色这类「功能性黑白」——它们由终端程序输出，不归本调色板管。

兄弟目录可用环境变量覆盖：
    TERMETRON_REF_ROOT=/path/to/product  python tools/check_palette.py

用法：
    python tools/check_palette.py          # 在仓库根目录执行
退出码：0 = 全部合规；1 = 有外来颜色；2 = 找不到参考应用（无法判定）
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF_ROOT = Path(os.environ.get("TERMETRON_REF_ROOT", ROOT.parent))
REF_APPS = [REF_ROOT / "Scholarius", REF_ROOT / "Livolog"]

# 参考应用里存放配色定义的文件
REF_GLOBS = [
    "app/src/main/assets/www/styles.css",
    "app/src/main/res/values/colors.xml",
    "app/src/main/res/values-night/colors.xml",
    "app/src/main/res/values/themes.xml",
    "app/src/main/res/values-night/themes.xml",
]

# 需要检查的 Termetron 文件（相对仓库根）
TARGET_GLOBS = [
    "quant_terminal.py",
    "app/index.html",
    "app/src/main.js",
    "app/src/updater.js",
    "app/devtest/update-test.html",
    "app/capacitor.config.ts",
    "app/scripts/patch-android.py",
    "app/assets/generate-icons.py",
    "app/android/app/src/main/res/drawable/ic_launcher_background.xml",
]

HEX_RE = re.compile(r"#([0-9a-fA-F]{6})\b")
RGB_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")


def collect(paths: list[Path]) -> dict[str, set[str]]:
    """返回 {文件路径: {颜色}}；颜色统一为大写 #RRGGBB。"""
    found: dict[str, set[str]] = {}
    for p in paths:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        cols = {"#" + m.group(1).upper() for m in HEX_RE.finditer(text)}
        cols |= {
            f"#{int(m.group(1)):02X}{int(m.group(2)):02X}{int(m.group(3)):02X}"
            for m in RGB_RE.finditer(text)
        }
        if cols:
            found[str(p)] = cols
    return found


def main() -> int:
    if not all(a.exists() for a in REF_APPS):
        missing = [str(a) for a in REF_APPS if not a.exists()]
        print(f"[error] 找不到参考应用：{missing}", file=sys.stderr)
        print("        用 TERMETRON_REF_ROOT 指定父目录后重试。", file=sys.stderr)
        return 2

    ref = collect([a / g for a in REF_APPS for g in REF_GLOBS])
    allowed: set[str] = set().union(*ref.values())
    print(f"参考调色板（Scholarius + Livolog）共 {len(allowed)} 个颜色：")
    print("  " + " ".join(sorted(allowed)))

    targets = collect([ROOT / g for g in TARGET_GLOBS])
    print(f"\nTermetron 命中 {len(targets)} 个文件：")

    bad = False
    for path, cols in sorted(targets.items()):
        outside = sorted(c for c in cols if c not in allowed)
        rel = Path(path).relative_to(ROOT)
        if outside:
            bad = True
            print(f"  ❌ {rel}: 外来颜色 {outside}")
        else:
            print(f"  ✅ {rel}: {len(cols)} 色，全部在调色板内")

    print()
    if bad:
        print("结论：存在调色板之外的颜色，必须改成 Scholarius/Livolog 已用的值。")
        return 1
    print("结论：全部颜色都在 Scholarius/Livolog 调色板内。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
