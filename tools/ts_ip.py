"""检测本机的 Tailscale IP。

Termetron 放弃隧道方案，改用 Tailscale 直连：
手机与电脑在同一个 tailnet 里，电脑端监听自己的 Tailscale IP，
手机直接填这个 IP + 端口即可连接（不再需要一次性密码 / 配对码）。

优先级：
  1. `tailscale ip -4`（最准，Tailscale 官方命令）
  2. 环境变量 TERMETRON_TS_IP（CI / 手动指定）
  3. 网卡扫描：100.64.0.0/10 是 Tailscale 的 CGNAT 保留段

⚠️ 不要用 `0.0.0.0` 兜底：那会把服务暴露到局域网/公网所有接口，
   与「只在 tailnet 内可达」的目标相反。
"""
from __future__ import annotations

import os
import subprocess
import sys


def _from_cli() -> str | None:
    """调用 `tailscale ip -4` 取第一个 IPv4。"""
    exe = _find_tailscale()
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "ip", "-4"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if out.returncode != 0:
            return None
        for line in out.stdout.splitlines():
            line = line.strip()
            if line and line.count('.') == 3:
                return line
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def _find_tailscale() -> str | None:
    """找 tailscale 可执行文件（Windows 装在 Program Files）。"""
    import shutil
    found = shutil.which("tailscale") or shutil.which("tailscale.exe")
    if found:
        return found
    candidates = [
        r"C:\Program Files\Tailscale\tailscale.exe",
        r"C:\Program Files (x86)\Tailscale\tailscale.exe",
        "/usr/bin/tailscale",
        "/usr/local/bin/tailscale",
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _from_interfaces() -> str | None:
    """扫描网卡找 100.64.0.0/10 段的地址（Tailscale CGNAT 段）。"""
    import socket
    try:
        hostname = socket.gethostname()
        addrs = socket.getaddrinfo(hostname, None, socket.AF_INET)
    except (OSError, socket.gaierror):
        return None
    for info in addrs:
        ip = info[4][0]
        parts = ip.split('.')
        if len(parts) == 4 and parts[0] == '100':
            try:
                second = int(parts[1])
            except ValueError:
                continue
            # 100.64.0.0/10 -> 第二字节 64..127
            if 64 <= second <= 127:
                return ip
    return None


def detect_tailscale_ip() -> str | None:
    env = os.environ.get("TERMETRON_TS_IP", "").strip()
    if env:
        return env
    return _from_cli() or _from_interfaces()


if __name__ == "__main__":
    ip = detect_tailscale_ip()
    if ip:
        print(ip)
    else:
        print("[termetron] 未检测到 Tailscale IP", file=sys.stderr)
        raise SystemExit(1)
