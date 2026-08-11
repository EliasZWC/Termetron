"""termetron_exec.py — 把命令投递到 Termetron 会话（后台程序映射到浏览器终端）。

让 agent / 终端发起的任何命令都进入 termetron 终端执行，输出实时显示在浏览器
Termetron 中（含底部 tqdm 进度条）。会话不存在时自动创建。

用法：
    python lib/termetron/termetron_exec.py <session> <command...>        # 投递，立即返回
    python lib/termetron/termetron_exec.py --watch <session> <command...>  # 投递并本地回显 20s
    python lib/termetron/termetron_exec.py --watch 60 <session> <command...>  # 回显 60s
    python lib/termetron/termetron_exec.py --port 8900 <session> <command...>

说明：
    - 命令由 termetron 会话内的 cmd.exe 执行（与用户在浏览器手输一致）；
    - --watch 时本地终端同步回显 termetron 会话的新输出行
      （tqdm 进度条在浏览器底部显示，不进本地回显）；
    - 长任务建议不 watch（浏览器看），短任务可用 --watch 拿结果。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request


def _opener():
    """直连 opener：访问本地 Termetron 不走系统代理（避免本地请求被代理污染）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(base: str, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _opener().open(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(base: str, path: str) -> dict:
    with _opener().open(f"{base}{path}", timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description="投递命令到 Termetron 会话")
    ap.add_argument("--port", type=int, default=8899, help="qt 端口（默认 8899）")
    ap.add_argument("--watch", nargs="?", const=20, type=int, default=None,
                    help="投递后本地回显 N 秒（默认 20；省略则立即返回）")
    ap.add_argument("session", help="qt 会话名（不存在自动创建）")
    ap.add_argument("command", nargs=argparse.REMAINDER, help="要执行的命令")
    args = ap.parse_args()

    base = f"http://127.0.0.1:{args.port}"
    cmd = " ".join(args.command).strip()
    if not cmd:
        raise SystemExit("[qt] empty command")

    try:
        sessions = _get(base, "/api/sessions")
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"[qt] cannot reach Termetron at {base} ({e})")

    if args.session not in sessions:
        try:
            r = _post(base, "/api/sessions", {"name": args.session, "cmd": None})
            if not r.get("ok"):
                raise SystemExit(f"[qt] create session failed: {r}")
            print(f"[qt] created session '{args.session}'")
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"[qt] create session failed: {e}")

    try:
        r = _post(base, f"/api/sessions/{args.session}/input", {"text": cmd})
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"[qt] send failed: {e}")
    if not r.get("ok"):
        raise SystemExit(f"[qt] send failed: {r}")
    print(f"[qt] sent to session '{args.session}': {cmd}")

    if args.watch is None:
        print("[qt] done — watch it in the browser terminal")
        return

    # watch 模式：本地回显 qt 会话新增输出行
    t_end = time.time() + args.watch
    seen = 0
    while time.time() < t_end:
        try:
            out = _get(base, f"/api/output/{args.session}")
            lines = out.get("lines", [])
            if len(lines) > seen:
                for ln in lines[seen:]:
                    print(ln)
                seen = len(lines)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    print(f"[qt] watched {args.watch}s — session still running, check the browser terminal")


if __name__ == "__main__":
    main()
