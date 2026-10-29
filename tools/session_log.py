"""会话输出持久化。

为什么需要：
    Termetron 的会话状态原本只在内存里（`Session.lines`）。
    服务端进程一死（崩溃 / 手动重启 / 系统重启），所有会话和输出全丢，
    手机端再连上来就是一片空白。这比 SSH 差 —— SSH 至少会话在 sshd 那边，
    配了 tmux 还能 `attach` 回去。

    用户明确要求「持久性、稳定性」优先，所以这里加一层磁盘缓冲：
    · 每个会话一个 JSONL 文件，**追加写**（不重写整个文件）
    · 启动时读回最后 N 行，会话界面上像"历史记录"一样还在
    · 写失败绝不影响主流程（磁盘满 / 只读挂载 / 权限问题都要能跑）

文件位置：
    $TERMETRON_STATE_DIR  （显式指定）
    ~/.local/state/termetron  （XDG 惯例，Linux/WSL/macOS）
    %LOCALAPPDATA%\\termetron  （Windows）

    ⚠️ 不用仓库目录：那里可能有 git、可能只读、也可能被清空。

格式：JSONL（每行一个 JSON 对象），而不是整块 JSON：
    · 追加写不需要读全文，开销恒定
    · 进程被 kill 时最多丢最后一行，前面的都有效
    · 读回时逐行容错，坏行直接跳过
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

# 单会话保留的最大行数（读回时截断）。600 与 /api/output 的 snapshot 一致。
MAX_LINES = 600
# 落盘节流：最快每 400ms 写一次，避免高频输出把磁盘打满
FLUSH_INTERVAL = 0.4


def state_dir() -> str:
    explicit = os.environ.get("TERMETRON_STATE_DIR", "").strip()
    if explicit:
        path = explicit
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "termetron")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "state")
        path = os.path.join(base, "termetron")
    return os.path.join(path, "sessions")


def _safe_name(name: str) -> str:
    """会话名 -> 安全文件名。只保留字母数字和 -_，其余转 _。"""
    out = []
    for ch in name:
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        else:
            out.append("_")
    safe = "".join(out)[:80]
    return safe or "_"


class SessionLog:
    """单个会话的输出日志：追加写 + 启动时读回。"""

    def __init__(self, name: str, directory: str | None = None):
        self.name = name
        self.dir = directory or state_dir()
        self.path = os.path.join(self.dir, _safe_name(name) + ".jsonl")
        self._lock = threading.Lock()
        self._pending: list[str] = []
        self._last_flush = 0.0
        self._broken = False  # 一旦写失败就不再尝试，避免刷屏
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError:
            self._broken = True

    # ── 写 ──────────────────────────────────────────────────────────────
    def append(self, lines: list[str], *, force: bool = False) -> None:
        if self._broken or not lines:
            return
        now = time.time()
        with self._lock:
            self._pending.extend(lines)
            if not force and (now - self._last_flush) < FLUSH_INTERVAL:
                return
            batch = self._pending
            self._pending = []
            self._last_flush = now
        self._write(batch)

    def _write(self, lines: list[str]) -> None:
        if self._broken or not lines:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                for line in lines:
                    f.write(json.dumps({"t": time.time(), "s": line},
                                       ensure_ascii=False) + "\n")
        except OSError:
            self._broken = True
            print(f"[termetron] warn: 无法写入会话日志 {self.path}",
                  file=sys.stderr)

    def flush(self) -> None:
        """强制把待写内容刷盘（进程退出前调用）。"""
        with self._lock:
            batch = self._pending
            self._pending = []
        self._write(batch)

    # ── 读 ──────────────────────────────────────────────────────────────
    def load(self, limit: int = MAX_LINES) -> list[str]:
        """读回最后 limit 行。文件不存在/损坏时返回空列表，绝不抛异常。"""
        if not os.path.isfile(self.path):
            return []
        lines: list[str] = []
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue  # 进程被 kill 时的半行，跳过
                    text = obj.get("s")
                    if isinstance(text, str):
                        lines.append(text)
        except OSError:
            return []
        return lines[-limit:]

    # ── 维护 ────────────────────────────────────────────────────────────
    def truncate(self) -> None:
        """清空日志（用户执行 clear 时调用）。"""
        with self._lock:
            self._pending = []
        if self._broken:
            return
        try:
            open(self.path, "w", encoding="utf-8").close()
        except OSError:
            pass

    def compact(self, keep: int = MAX_LINES) -> None:
        """把文件压到只剩最后 keep 行（防止无限增长）。"""
        if self._broken or not os.path.isfile(self.path):
            return
        tail = self.load(keep)
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                for line in tail:
                    f.write(json.dumps({"t": time.time(), "s": line},
                                       ensure_ascii=False) + "\n")
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass


def list_session_names() -> list[str]:
    """列出磁盘上有日志的会话名（供启动时恢复）。"""
    d = state_dir()
    if not os.path.isdir(d):
        return []
    names = []
    for fn in os.listdir(d):
        if fn.endswith(".jsonl"):
            names.append(fn[:-6])
    return sorted(names)


def remove_session(name: str) -> None:
    """删除某会话的日志文件（会话被销毁时调用）。"""
    path = os.path.join(state_dir(), _safe_name(name) + ".jsonl")
    try:
        os.remove(path)
    except OSError:
        pass
