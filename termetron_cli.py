"""termetron_cli.py — 隧道管理命令（new / list / show / use / delete）。

设计：**隧道码是身份，URL 是承载**。
  · `termetron new [标签]`      新建一条隧道，输出隧道码 + 密码（只显示这一次）
  · `termetron list`           列出本机所有隧道
  · `termetron show <码>`      查看某条隧道的详情与当前 URL
  · `termetron use <码>`       用指定隧道身份启动 cloudflared（URL 会变，码不变）
  · `termetron delete <码>`    删除一条隧道

⚠️ 密码只在 `new` 时显示一次：之后任何列表/详情接口都不回传密码，
   避免明文密码散落在日志、终端回滚缓冲、截图里。丢了就重新 new 一条。

用法示例：
    python termetron_cli.py new 我的电脑
    python termetron_cli.py list
    python termetron_cli.py use ABC123
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# 复用 quant_terminal 里的 RemoteMgr（同一份持久化格式）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _mgr():
    import importlib.util

    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "qt", os.path.join(here, "quant_terminal.py"))
    qt = importlib.util.module_from_spec(spec)
    # 不执行 main()，只加载模块级定义
    spec.loader.exec_module(qt)
    return qt.RemoteMgr()


def _fmt_ts(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))
    except Exception:  # noqa: BLE001
        return "-"


def cmd_new(args) -> int:
    m = _mgr()
    t = m.create_tunnel(args.label or "")
    print()
    print("  ┌─────────────────────────────────────────────┐")
    print("  │  New tunnel created                         │")
    print("  └─────────────────────────────────────────────┘")
    print()
    print(f"    Tunnel Code : {t['code']}")
    print(f"    Password    : {t['password']}")
    print()
    print("  ⚠️  The password is shown ONCE. Copy it into the app now.")
    print("      It is not stored anywhere you can read it back.")
    print()
    print(f"  Next:  python {os.path.basename(__file__)} use {t['code']}")
    print()
    return 0


def cmd_list(args) -> int:
    m = _mgr()
    tunnels = m.load_tunnels()
    if not tunnels:
        print("  No tunnels yet. Create one with:  termetron new [label]")
        return 0
    items = sorted(tunnels.values(), key=lambda t: t.get("createdAt") or 0,
                   reverse=True)
    active = (m.code or "").upper()
    print()
    print(f"  {'CODE':<8} {'LABEL':<20} {'CREATED':<18} ACTIVE")
    print(f"  {'-'*8} {'-'*20} {'-'*18} {'-'*6}")
    for t in items:
        code = str(t.get("code") or "")
        mark = "*" if code.upper() == active else ""
        print(f"  {code:<8} {(t.get('label') or '-'):<20} "
              f"{_fmt_ts(t.get('createdAt')):<18} {mark}")
    print()
    print("  (* = currently running in this process)")
    print("  Passwords are never listed. Re-create a tunnel if you lost one.")
    print()
    return 0


def cmd_show(args) -> int:
    m = _mgr()
    code = (args.code or "").strip().upper()
    t = m.find_tunnel(code)
    if not t:
        print(f"  No such tunnel: {code}", file=sys.stderr)
        return 1
    print()
    print(f"    Tunnel Code : {t.get('code')}")
    print(f"    Label       : {t.get('label') or '-'}")
    print(f"    Created     : {_fmt_ts(t.get('createdAt'))}")
    print(f"    Password    : (not shown — stored; re-create if lost)")
    print()
    return 0


def cmd_use(args) -> int:
    """用指定隧道身份启动 cloudflared（URL 会变，隧道码不变）。"""
    m = _mgr()
    code = (args.code or "").strip().upper()
    if not m.find_tunnel(code):
        print(f"  No such tunnel: {code}", file=sys.stderr)
        return 1
    port = args.port
    print(f"  Starting tunnel {code} -> http://127.0.0.1:{port} ...")
    r = m.start(port, code)
    if not r.get("ok"):
        print(f"  start failed: {r.get('error')}", file=sys.stderr)
        return 1
    # 等 URL 出现（cloudflared 需要几秒注册）
    for _ in range(40):
        st = m.status_json()
        if st.get("url"):
            print()
            print(f"    Tunnel Code : {st.get('code')}")
            print(f"    URL         : {st['url']}")
            print()
            print("  Paste the URL once in the app. After that the app only")
            print("  needs the code + password — it re-resolves the URL itself.")
            print()
            return 0
        time.sleep(0.5)
    print("  tunnel did not report a URL in time", file=sys.stderr)
    return 1


def cmd_delete(args) -> int:
    m = _mgr()
    code = (args.code or "").strip().upper()
    if not m.delete_tunnel(code):
        print(f"  No such tunnel: {code}", file=sys.stderr)
        return 1
    print(f"  Deleted tunnel {code}.")
    return 0


def _ask_yes_no(question: str) -> bool:
    """问一次 yes/no。

    默认（直接回车）是 **no** —— 这是不可逆的批量删除，
    手一滑回车不该把东西删光。
    """
    try:
        ans = input(f"{question} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans in ("y", "yes")


def cmd_clear(args) -> int:
    m = _mgr()
    tunnels = m.load_tunnels()
    n = len(tunnels)
    if n == 0:
        print("  Nothing to clear — no tunnels stored.")
        return 0

    print()
    print(f"  This will permanently delete {n} tunnel(s):")
    for code, t in sorted(tunnels.items()):
        print(f"    {code:<8} {(t.get('label') or '-'):<20} "
              f"{_fmt_ts(t.get('createdAt'))}")
    print()
    print("  Passwords are not recoverable — once cleared, those codes are")
    print("  gone for good and any phone paired to them must be re-paired.")
    print()

    # --yes 供脚本使用；交互场景下走确认
    if not args.yes and not _ask_yes_no("  Clear all tunnels?"):
        print("  Aborted — nothing was deleted.")
        return 1

    removed = m.clear_tunnels()
    print(f"  Cleared {removed} tunnel(s).")
    print("  Run `termetron new` to create a fresh one.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="termetron",
        description="Manage Termetron tunnels (code = identity, URL = transport)")
    sub = ap.add_subparsers(dest="cmd")

    p_new = sub.add_parser("new", help="create a new tunnel (code + password)")
    p_new.add_argument("label", nargs="?", default="", help="optional label")
    p_new.set_defaults(func=cmd_new)

    p_list = sub.add_parser("list", help="list tunnels")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="show one tunnel")
    p_show.add_argument("code")
    p_show.set_defaults(func=cmd_show)

    p_use = sub.add_parser("use", help="run a tunnel by its code")
    p_use.add_argument("code")
    p_use.add_argument("--port", type=int, default=8899)
    p_use.set_defaults(func=cmd_use)

    p_del = sub.add_parser("delete", help="delete a tunnel")
    p_del.add_argument("code")
    p_del.set_defaults(func=cmd_delete)

    p_clear = sub.add_parser("clear", help="delete ALL tunnels (asks for confirmation)")
    p_clear.add_argument("--yes", "-y", action="store_true",
                         help="skip the confirmation prompt (for scripts)")
    p_clear.set_defaults(func=cmd_clear)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
