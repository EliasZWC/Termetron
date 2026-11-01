"""termetron_cli.py - 隧道管理命令（new / list / show / use / delete）。

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
import urllib.error
import urllib.request

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


def _read_password() -> str | None:
    """让用户自己设密码，普通可见输入（不回显遮蔽）。

    ⚠️ 这里**刻意不用 getpass**：用户在设密码的当下应该看得见自己敲了什么，
       否则打错一个字符就只会在配对时失败，而且完全不知道错在哪。
       密码最终会落盘到 tunnels.json，回显遮蔽也挡不住同一个终端里的旁人；
       真正的保护是"设完之后不再回显"，而不是输入时遮起来。
    """
    print()
    print("  Set a password for this tunnel.")
    print("  You will type it again in the app to pair your phone.")
    print("  Leave blank to generate a random one.")
    print()
    try:
        pw1 = input("  Password: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    pw1 = pw1.strip()
    if not pw1:
        return ""          # 空字符串 = 让服务端自动生成

    if len(pw1) < 6:
        print("  Too short - use at least 6 characters.", file=sys.stderr)
        return None

    try:
        pw2 = input("  Repeat:   ")
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if pw1 != pw2.strip():
        print("  Passwords do not match.", file=sys.stderr)
        return None
    return pw1


def cmd_new(args) -> int:
    m = _mgr()

    # --password 供脚本使用；没给就交互式问，问完还要二次确认
    # ⚠️ args.password 为 None = 没给（走交互）；空字符串 = 明确要自动生成。
    #    这两种情况在 argparse 里都是 ""，所以用 None 默认值区分。
    pw = args.password
    if pw is None:
        pw = _read_password()
        if pw is None:
            print("  Aborted - no tunnel was created.", file=sys.stderr)
            return 1
    user_set = bool(pw)

    t = m.create_tunnel(args.label or "", password=pw or None)

    print()
    print("  NEW TUNNEL CREATED")
    print("  ---------------------------------------------------")
    print(f"    Tunnel Code : {t['code']}")
    if user_set:
        print("    Password    : (the one you just set)")
    else:
        print(f"    Password    : {t['password']}")
    print("  ---------------------------------------------------")
    print()
    if user_set:
        print("  Enter this code and your password in the app.")
    else:
        print("  This random password is shown ONCE. Copy it into the app now.")
        print("  It is not stored anywhere you can read it back.")
    print()
    print(f"  Next:  termetron use {t['code']}")
    print()
    return 0


def _live_status(port: int = 8899) -> dict | None:
    """向**运行中的服务端**要实时隧道状态。

    ⚠️ 不能靠本地新建一个 RemoteMgr 来判断"哪条在跑"：
       那个实例的 .code 永远是 None（它没有启动过隧道），
       所以 ACTIVE 列会一直全空 —— 之前就是这样，用户看不出隧道有没有运行。
       真实状态只存在于跑着隧道的那个进程里，只能通过 API 问它。

    返回 None 表示服务端没在运行（此时所有隧道都不可能在跑）。
    """
    base = _service_base(port)
    if not base:
        return None
    try:
        with urllib.request.urlopen(base + "/api/remote/status", timeout=5) as r:
            return json.loads(r.read() or b"{}")
    except Exception:  # noqa: BLE001
        return None


def cmd_list(args) -> int:
    m = _mgr()
    tunnels = m.load_tunnels()
    if not tunnels:
        print("  No tunnels yet. Create one with:  termetron new [label]")
        return 0

    st = _live_status(getattr(args, "port", 8899))
    running = bool(st and st.get("status") == "on")
    live_code = (st.get("code") or "").upper() if running else ""
    url = st.get("url") if running else None

    items = sorted(tunnels.values(), key=lambda t: t.get("createdAt") or 0,
                   reverse=True)
    print()
    print(f"  {'CODE':<8} {'LABEL':<18} {'CREATED':<18} {'STATE':<10} URL")
    print(f"  {'-'*8} {'-'*18} {'-'*18} {'-'*10} {'-'*40}")
    for t in items:
        code = str(t.get("code") or "")
        if live_code and code.upper() == live_code:
            state = "RUNNING"
            cell = url or "(starting)"
        else:
            state = "idle"
            cell = "-"
        print(f"  {code:<8} {(t.get('label') or '-'):<18} "
              f"{_fmt_ts(t.get('createdAt')):<18} {state:<10} {cell}")

    print()
    if st is None:
        print("  server: NOT RUNNING - no tunnel can be active.")
        print("  start it with:  termetron --remote")
    elif running:
        print(f"  server: running, tunnel {live_code} is UP")
        print()
        # 独立一行，方便直接复制粘贴进 App 的 Address 字段。
        # 正常情况下 App 会自动跟随这个地址，只有它彻底失效时才需要手填。
        print(f"  address for the app:  {url}")
    elif st.get("status") == "starting":
        print("  server: running, tunnel is STARTING (no URL yet)")
    elif st.get("status") == "error":
        print(f"  server: running, tunnel ERROR: {st.get('error')}")
    else:
        print("  server: running, but no tunnel is open")
        print("  open one with:  termetron remote")
    print("  Passwords are never listed.")
    print()
    return 0


def cmd_show(args) -> int:
    m = _mgr()
    code = (args.code or "").strip().upper()
    t = m.find_tunnel(code)
    if not t:
        print(f"  No such tunnel: {code}", file=sys.stderr)
        return 1
    st = _live_status(getattr(args, "port", 8899))
    running = bool(st and st.get("status") == "on"
                   and (st.get("code") or "").upper() == code)
    print()
    print(f"    Tunnel Code : {t.get('code')}")
    print(f"    Label       : {t.get('label') or '-'}")
    print(f"    Created     : {_fmt_ts(t.get('createdAt'))}")
    print(f"    Password    : (not shown - stored; re-create if lost)")
    if running:
        print(f"    State       : RUNNING")
        print(f"    URL         : {st.get('url') or '(starting)'}")
    elif st is None:
        print(f"    State       : not running (server is down)")
    elif st.get("status") == "starting":
        print(f"    State       : starting")
    else:
        print(f"    State       : not running")
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
            print("  needs the code + password - it re-resolves the URL itself.")
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

    默认（直接回车）是 **no** -- 这是不可逆的批量删除，
    手一滑回车不该把东西删光。
    """
    try:
        ans = input(f"{question} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans in ("y", "yes")


def cmd_remote(args) -> int:
    """让**运行中的服务端**开启隧道，并打印 URL。

    为什么必须在服务端开而不是本地开 cloudflared：
      cloudflared 是启动它的进程的子进程。在这里（CLI 进程）直接 start，
      命令一退出隧道就断（`termetron use` 就是这个行为）。
      隧道要长期存活，必须挂到服务端进程名下 —— 所以这里走 HTTP API。
    """
    base = _service_base(args.port)
    if not base:
        print("  Termetron server is not running.", file=sys.stderr)
        print("  Start it first:  termetron", file=sys.stderr)
        return 1

    # --status：只看状态，不动隧道
    if args.status:
        try:
            with urllib.request.urlopen(base + "/api/remote/status", timeout=5) as r:
                st = json.loads(r.read() or b"{}")
        except Exception as e:  # noqa: BLE001
            print(f"  status failed: {e}", file=sys.stderr)
            return 1
        print()
        print(f"    Status : {st.get('status')}")
        print(f"    Code   : {st.get('code') or '-'}")
        print(f"    URL    : {st.get('url') or '-'}")
        if st.get("error"):
            print(f"    Error  : {st['error']}")
        print()
        return 0

    # --off：关闭隧道
    if args.off:
        try:
            req = urllib.request.Request(base + "/api/remote/stop", data=b"{}",
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                json.loads(r.read() or b"{}")
        except Exception as e:  # noqa: BLE001
            print(f"  stop failed: {e}", file=sys.stderr)
            return 1
        print("  Tunnel closed.")
        return 0

    body = {}
    code = (args.code or "").strip().upper()
    if code:
        body["code"] = code
    payload = json.dumps(body).encode() if body else b"{}"

    try:
        req = urllib.request.Request(
            base + "/api/remote/start", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        print(f"  start failed: HTTP {e.code}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"  start failed: {e}", file=sys.stderr)
        return 1

    if not res.get("ok"):
        print(f"  start failed: {res.get('error') or 'unknown'}", file=sys.stderr)
        return 1

    # 等 URL 就绪（cloudflared 注册通常 3-8 秒）
    url = res.get("url")
    used = res.get("code")
    for _ in range(60):
        if url:
            break
        time.sleep(1)
        try:
            with urllib.request.urlopen(base + "/api/remote/status", timeout=5) as r:
                st = json.loads(r.read() or b"{}")
            url = st.get("url")
            used = st.get("code") or used
        except Exception:  # noqa: BLE001
            pass

    print()
    if url:
        # 二维码：手机扫这一下就把「隧道码 + 地址」填好，不用手打网址。
        # ⚠️ 密码不在码里 —— 它显示在屏幕上可能被旁人看到/截图，
        #    密码必须由用户在手机上自己输入。
        try:
            import termqr
            art = termqr.render(termqr.payload(used or "", url),
                                invert=getattr(args, "light", False))
        except Exception:  # noqa: BLE001
            art = None

        if art:
            print("  Scan this with the Termetron app (fills in code + address):")
            print()
            for line in art.splitlines():
                print("  " + line)
            print()
            print("  It carries the code and address only, NOT the password.")
            print("  Type your password on the phone.")
        else:
            # 没装 qrcode 库时退回可复制的文本，不因为缺库就让命令失败
            print("  (qrcode module not installed - showing text instead)")
            print("  Install it with:  python3 -m pip install qrcode")
            print()
        print(f"    Tunnel Code : {used}")
        print(f"    URL         : {url}")
        print()
        print("  The app only needs the code + password; it re-resolves")
        print("  the URL itself, so a changing URL is harmless.")
    else:
        print(f"  Tunnel {used} is starting but has no URL yet.")
        print("  Check again with:  termetron remote --status")
    print()
    return 0


def _service_base(port: int) -> str | None:
    """找到运行中的服务端地址；找不到返回 None。"""
    for host in ("127.0.0.1", "localhost"):
        b = f"http://{host}:{port}"
        try:
            with urllib.request.urlopen(b + "/api/discover", timeout=2) as r:
                d = json.loads(r.read() or b"{}")
            if d.get("app") == "termetron":
                return b
        except Exception:  # noqa: BLE001
            continue
    return None


def cmd_clear(args) -> int:
    m = _mgr()
    tunnels = m.load_tunnels()
    n = len(tunnels)
    if n == 0:
        print("  Nothing to clear - no tunnels stored.")
        return 0

    print()
    print(f"  This will permanently delete {n} tunnel(s):")
    for code, t in sorted(tunnels.items()):
        print(f"    {code:<8} {(t.get('label') or '-'):<20} "
              f"{_fmt_ts(t.get('createdAt'))}")
    print()
    print("  Passwords are not recoverable - once cleared, those codes are")
    print("  gone for good and any phone paired to them must be re-paired.")
    print()

    # --yes 供脚本使用；交互场景下走确认
    if not args.yes and not _ask_yes_no("  Clear all tunnels?"):
        print("  Aborted - nothing was deleted.")
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
    p_new.add_argument("--password", "-p", default=None,
                       help="set the password non-interactively (for scripts); "
                            "omit to be prompted, pass '' to auto-generate")
    p_new.set_defaults(func=cmd_new)

    p_list = sub.add_parser("list", help="list tunnels and which one is running")
    p_list.add_argument("--port", type=int, default=8899,
                        help="server port to query for live status (default 8899)")
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

    p_rem = sub.add_parser(
        "remote",
        help="open the tunnel through the running server "
             "(defaults to the first stored tunnel)")
    p_rem.add_argument("code", nargs="?", default="",
                       help="tunnel code; omit to use the first one")
    p_rem.add_argument("--port", type=int, default=8899,
                       help="server port (default 8899)")
    p_rem.add_argument("--status", action="store_true",
                       help="show tunnel status instead of starting it")
    p_rem.add_argument("--off", action="store_true",
                       help="close the tunnel")
    p_rem.add_argument("--light", action="store_true",
                       help="invert the QR colors for a light-background terminal")
    p_rem.set_defaults(func=cmd_remote)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
