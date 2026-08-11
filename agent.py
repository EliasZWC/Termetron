"""Termetron agent 通道 —— 给 AI agent（如 Copilot）的共享/监控 CLI。

让 agent 直接读取 termetron 会话状态与输出，无需浏览器/共享窗口：
  status                列出所有会话（busy / 脚本 / 命令 / 进度 / 输出尾部）
  watch <session>       读某会话输出尾部（增量监控）
  wait <session>        等待会话空闲（任务跑完，替代 wait_termetron）

用法：
  python lib/termetron/agent.py status
  python lib/termetron/agent.py status --port 8900 --lines 20
  python lib/termetron/agent.py status --auto        # 自动探测运行中的服务器（含 VS Code 扩展随机端口）
  python lib/termetron/agent.py watch shell --lines 50
  python lib/termetron/agent.py wait shell --timeout 900

数据源：termetron 自带 HTTP API —— /api/sessions 返回每会话
{lines[-600:], progress, done, busy, script, cmd, updated}。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _api(base: str, path: str) -> dict:
    req = urllib.request.Request(base + path, headers={"User-Agent": "termetron-agent"})
    with _opener().open(req, timeout=10) as r:
        return json.loads(r.read().decode())


def _detect_ports() -> list[int]:
    """Windows：扫 python 进程命令行里的 quant_terminal.py --port。"""
    if os.name != "nt":
        return []
    ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
          "Where-Object { $_.CommandLine -like '*quant_terminal.py*' } | "
          "ForEach-Object { $_.CommandLine }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=15)
    except Exception:  # noqa: BLE001
        return []
    ports = []
    for line in r.stdout.splitlines():
        m = re.search(r"--port\s+(\d+)", line)
        if m:
            ports.append(int(m.group(1)))
    return sorted(set(ports))


def _resolve_base(args) -> str:
    if args.auto:
        ports = _detect_ports()
        if not ports:
            sys.exit("[error] --auto: no running quant_terminal.py server found")
        # 优先 8900/8899（手动服务器），否则第一个
        port = next((p for p in (8900, 8899) if p in ports), ports[0])
        print(f"[info] --auto detected server port: {port}  (all: {ports})", file=sys.stderr)
        return f"http://127.0.0.1:{port}"
    return f"http://127.0.0.1:{args.port}"


def _fmt_progress(p) -> str:
    if not p:
        return "-"
    try:
        pct = p.get("pct") if isinstance(p, dict) else None
        if pct is None and isinstance(p, dict):
            cur, total = p.get("cur"), p.get("total")
            pct = f"{cur}/{total}"
        return f"{pct}"
    except Exception:  # noqa: BLE001
        return str(p)


def cmd_status(base: str, lines_n: int) -> None:
    s = _api(base, "/api/sessions")
    print(f"=== Termetron agent status @ {base} ===")
    print(f"  sessions: {len(s)}")
    for name, ss in s.items():
        busy = bool(ss.get("busy"))
        scr = ss.get("script")
        scr_name = (scr or {}).get("name") if isinstance(scr, dict) else (scr or "-")
        print(f"\n  [{name}] busy={'YES' if busy else 'no'}  "
              f"script={scr_name or '-'}  cmd={ss.get('cmd') or '-'}  "
              f"updated={ss.get('updated')}")
        prog = ss.get("progress")
        if prog:
            print(f"    progress: {_fmt_progress(prog)}")
        tail = ss.get("lines") or []
        if tail:
            print(f"    tail (last {min(lines_n, len(tail))}/{len(tail)}):")
            for ln in tail[-lines_n:]:
                print("    " + ln)


def cmd_watch(base: str, name: str, lines_n: int) -> None:
    try:
        ss = _api(base, f"/api/sessions/{name}")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"[error] session '{name}': {e}")
    print(f"=== Termetron session '{name}' @ {base} ===")
    print(f"  busy={'YES' if ss.get('busy') else 'no'}  "
          f"cmd={ss.get('cmd') or '-'}  updated={ss.get('updated')}")
    tail = ss.get("lines") or []
    if tail:
        print(f"  tail (last {min(lines_n, len(tail))}/{len(tail)}):")
        for ln in tail[-lines_n:]:
            print("  " + ln)


def cmd_wait(base: str, name: str, timeout: float, idle_for: float = 2.0) -> None:
    t0 = time.time()
    idle_since: float | None = None
    while time.time() - t0 < timeout:
        try:
            ss = _api(base, f"/api/sessions/{name}")
        except Exception:  # noqa: BLE001
            idle_since = None
            time.sleep(1.0)
            continue
        if ss.get("busy"):
            idle_since = None
        else:
            if idle_since is None:
                idle_since = time.time()
            if time.time() - idle_since >= idle_for:
                print(f"[wait] session '{name}' idle — task done ({time.time() - t0:.1f}s)")
                return
        time.sleep(1.0)
    sys.exit(f"[wait] TIMEOUT after {timeout:.0f}s — session '{name}' still busy")


def main() -> None:
    ap = argparse.ArgumentParser(description="Termetron agent 通道：共享/监控会话")
    ap.add_argument("command", choices=["status", "watch", "wait"])
    ap.add_argument("session", nargs="?", default=None, help="watch/wait 的会话名")
    ap.add_argument("--port", type=int, default=8900)
    ap.add_argument("--auto", action="store_true", help="自动探测运行中的服务器端口")
    ap.add_argument("--lines", type=int, default=15, help="输出尾部行数（默认 15）")
    ap.add_argument("--timeout", type=float, default=1800.0, help="wait 超时秒数")
    args = ap.parse_args()

    base = _resolve_base(args)
    if args.command == "status":
        cmd_status(base, args.lines)
    elif args.command == "watch":
        if not args.session:
            sys.exit("[error] watch needs a session name")
        cmd_watch(base, args.session, args.lines)
    else:  # wait
        if not args.session:
            sys.exit("[error] wait needs a session name")
        cmd_wait(base, args.session, args.timeout)


if __name__ == "__main__":
    main()
