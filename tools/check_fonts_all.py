"""全量字体一致性检查 —— 应用内所有文字必须用更纱黑体。

用户要求（2026-10-26）：「全部文字都该是同一样式的，除了特殊的我说的。」

为什么需要这个脚本（而不是靠 grep 抽查）：
    只改 `app/index.html` 里的 CSS 是不够的。`main.js` / `updater.js`
    会把一整套弹窗 CSS **以字符串形式注入** `<style>`，那些声明只存在于
    JS 里，用 grep 搜 HTML 根本看不到。它们也只在 APK 里才渲染，
    浏览器打开 index.html 预览时完全不会出现 —— 「肉眼验证过」不可靠。

检查内容：
    1. `font-family: ...` 是否都以 'Termetron Mono' 打头（或 inherit）
    2. `font:` 简写里是否混入了非更纱黑体的族名
    3. 裸字面量 system-ui / -apple-system / Segoe UI / ui-sans-serif

用法：
    python tools/check_fonts_all.py          # 静态扫描
    python tools/check_fonts_all.py --runtime  # 额外提示如何做运行时验证

退出码：0 = 全部合规；1 = 有违规。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FILES = [
    'quant_terminal.py',
    'app/index.html',
    'app/src/main.js',
    'app/src/updater.js',
    'app/vite.config.ts',
]

GOOD = 'Termetron Mono'

# 这些族名都不属于更纱黑体
BAD_FAMILIES = [
    'system-ui', 'sans-serif', '-apple-system', 'Segoe', 'Arial',
    'Helvetica', 'Roboto,', 'ui-sans', 'PingFang', 'Microsoft YaHei',
]

# 兜底链里允许出现的族名（排在 'Termetron Mono' **之后**才合法）
ALLOWED_FALLBACKS = ['Roboto Mono', 'Droid Sans Mono', 'monospace', 'inherit']


def scan_file(rel: str) -> list[str]:
    p = ROOT / rel
    if not p.exists():
        return []

    text = p.read_text(encoding='utf-8')
    lines = text.split('\n')
    problems: list[str] = []

    def line_of(pos: int) -> int:
        return text.count('\n', 0, pos) + 1

    # 1) font-family 声明
    for m in re.finditer(r'font-family\s*:\s*([^;}"\n]+)', text):
        val = m.group(1).strip()
        if GOOD in val or val == 'inherit':
            continue
        problems.append(f'{rel}:{line_of(m.start())}  font-family: {val[:90]}')

    # 2) font: 简写（排除 font-size / font-weight / font-family 等长属性）
    for m in re.finditer(r'(?<![-a-z])font\s*:\s*([^;}"\n]+)', text):
        val = m.group(1).strip()
        if GOOD in val or val == 'inherit':
            continue
        low = val.lower()
        if any(b.lower().rstrip(',') in low for b in BAD_FAMILIES):
            problems.append(f'{rel}:{line_of(m.start())}  font 简写: {val[:90]}')

    # 3) 裸字面量（可能被拼进 JS 字符串，前面两条抓不到）
    for bad in ['system-ui', '-apple-system', 'Segoe UI', 'ui-sans-serif']:
        for m in re.finditer(re.escape(bad), text):
            ctx = lines[line_of(m.start()) - 1].strip()
            if 'font-family' in ctx or re.search(r'(?<![-a-z])font\s*:', ctx):
                continue  # 已在 1/2 里报过
            problems.append(f'{rel}:{line_of(m.start())}  裸字面量 "{bad}": {ctx[:90]}')

    return problems


def main() -> int:
    all_problems: list[str] = []

    print('=== 静态扫描：字体声明 ===')
    for rel in FILES:
        p = ROOT / rel
        if not p.exists():
            print(f'  {rel:24s} 跳过（不存在）')
            continue
        probs = scan_file(rel)
        if probs:
            print(f'  {rel:24s} ❌ {len(probs)} 处违规')
            all_problems.extend(probs)
        else:
            print(f'  {rel:24s} OK')

    if all_problems:
        print()
        print('=== 违规明细 ===')
        for prob in all_problems:
            print('  ❌', prob)
        print()
        print(f'❌ 共 {len(all_problems)} 处违规 —— 应用内所有文字必须用 {GOOD}')

    if '--runtime' in sys.argv:
        print()
        print('=== 运行时验证建议 ===')
        print('  静态扫描抓不到「实际渲染用了什么字体」。要在桌面端验证：')
        print('    python termetron.py --port 8999')
        print('    然后在浏览器里执行：')
        print("      [...document.querySelectorAll('body *')]")
        print("        .filter(el => el.textContent.trim() && !el.children.length)")
        print("        .map(el => getComputedStyle(el).fontFamily.split(',')[0])")
        print('    期望：全部为 "Termetron Mono"，零例外。')
        print()
        print('  App 端（main.js / updater.js 的弹窗）只在 APK 里渲染，')
        print('  请用 Build 后的 app/www 起服务并触发一次升级/提示弹窗再查。')

    return 1 if all_problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
