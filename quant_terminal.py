"""Termetron: interactive single-page web terminal for quant sessions.

- Every session is a persistent interactive shell (cmd.exe on Windows).
- New session: name only (optional initial command). Once created, type
  commands in the "$" input box at the bottom of the output area — they run
  in that session's shell, just like a real terminal.
- Session tabs on top (click to switch); [+] add button in the tab bar.
- CSS progress bars are parsed out of tqdm output and pinned at the bottom.
- Delete lives in the [ ... ] menu at the bottom right (Termetron-styled modal).

Run:
    python quant_terminal.py            # default port 8899
Open http://127.0.0.1:8899 in a browser.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_TQDM_RE = re.compile(r"^(.*?)[:]?\s*(\d+)%\|.*?\|\s*(\d+)/(\d+)")

# ANSI 转义序列（光标移动/清行等）：剥离，避免污染 lines 与 tqdm 解析
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)
# 匹配 Windows cmd 提示符（如 E:\code\quant>）：行出现即 shell 空闲
_PROMPT_RE = re.compile(r"^[A-Za-z]:\\.*> *$")
# 命令回显行 `E:\code\quant>cmd`：提取命令，改写成 termetron 提示符风格 `$ cmd`
_CMD_ECHO_RE = re.compile(r"^[A-Za-z]:\\.*>\s*(.*)$")

# 量化脚本编号注册表（(编号, 短名, 文件名)）
SCRIPT_REGISTRY = [
    ("S01", "run_demo", "run_demo.py"),
    ("S02", "run_paper", "run_paper.py"),
    ("S03", "follow_market", "follow_market.py"),
    ("S04", "scan_frequency", "scan_frequency.py"),
    ("S05", "scan_impact", "scan_impact.py"),
    ("S06", "expand_pool", "expand_pool.py"),
    ("S07", "expand_financials", "expand_financials.py"),
    ("S08", "run_miner", "run_miner.py"),
]


def _script_from_cmd(cmd: str | None) -> dict | None:
    """识别命令正在运行的量化脚本。

    返回 {"id": "S01", "name": "run_demo"}；未注册编号但识别到 .py
    程序时返回 {"id": None, "name": "<程序名>"}（用程序名兜底）；
    非 python 命令返回 None（交互 shell）。
    """
    if not cmd:
        return None
    low = cmd.lower().replace("\\", "/")
    m = re.findall(r"([\w./-]+\.py)", low)
    if not m:
        return None
    prog = m[-1].split("/")[-1]  # 程序 basename，如 run_demo.py
    for sid, name, fname in SCRIPT_REGISTRY:
        if fname in low:
            return {"id": sid, "name": name}
    return {"id": None, "name": prog}


def _parse_tqdm(s: str) -> dict | None:
    m = _TQDM_RE.search(s)
    if not m:
        return None
    return {
        "desc": (m.group(1) or "").strip() or "progress",
        "pct": min(100.0, float(m.group(2))),
        "done": int(m.group(3)),
        "total": int(m.group(4)),
    }


def _smart_decode(buf: bytes) -> tuple[str, bytes]:
    """Decode a byte chunk, preferring UTF-8 (Python programs emit UTF-8).

    If a chunk is not valid UTF-8 it is likely cmd.exe's own output in the
    system ANSI codepage (cp936/GBK on zh-CN Windows) — decode that instead
    so Chinese text from a real shell renders correctly.  An incomplete
    multibyte tail is kept for the next chunk so a character split across
    two reads does not garble.
    """
    if not buf:
        return "", b""
    try:
        return buf.decode("utf-8"), b""
    except UnicodeDecodeError as e:
        # Error near the tail: possibly a split multibyte char — keep tail.
        if e.start >= len(buf) - 4:
            head = buf[: e.start]
            try:
                return head.decode("utf-8"), buf[e.start:]
            except UnicodeDecodeError:
                pass
        # Not valid UTF-8: decode as the ANSI codepage (GBK on zh-CN).
        try:
            return buf.decode("cp936", errors="replace"), b""
        except LookupError:
            return buf.decode("utf-8", errors="replace"), b""


def _shell_cmd() -> list[str]:
    if os.name == "nt":
        return ["cmd.exe"]
    return ["/bin/sh"]


class Session:
    def __init__(self, name: str, cmd: str | None = None):
        self.name = name
        self.cmd = cmd or "(interactive shell)"
        self.lines: list[str] = []
        self.pending: str | None = None
        self.prog: dict | None = None
        self.done = False
        self.busy = False  # 有前台命令在跑（发送命令到下次出现提示符之间）
        self._startup = True  # 丢弃 cmd 启动 banner（新建会话应干净）
        self._startup_t0 = time.time()
        self._tqdm_t0: float | None = None  # 本进程 tqdm 起始时间（用于 ETA 估算）
        self.updated = time.time()
        self.script: dict | None = _script_from_cmd(cmd)
        self._spawn()
        if cmd:
            self.send(cmd)
        # 会话开启输出：终端风格欢迎横幅（纯文本 `***` 分隔，醒目且不依赖 CSS 样式）
        self.lines.append("*" * 44)
        self.lines.append(
            f"[termetron] session '{self.name}' ready — type /help for commands or "
            f"run a shell command ({time.strftime('%Y-%m-%d %H:%M:%S')})"
        )
        self.lines.append("*" * 44)

    def _spawn(self) -> None:
        """(Re)start the interactive shell process and its reader thread."""
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        self.proc = subprocess.Popen(
            _shell_cmd(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0, env=env,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def send(self, text: str) -> bool:
        if self.proc.poll() is not None or self.proc.stdin is None:
            return False
        self.cmd = text  # 最近提交的命令（agent 通道读取）
        # cmd.exe on zh-CN Windows reads stdin in the ANSI codepage (GBK);
        # encode commands accordingly so Chinese text typed in the box works.
        enc = "cp936" if os.name == "nt" else "utf-8"
        try:
            self.proc.stdin.write((text + "\r\n").encode(enc, "replace"))
            self.proc.stdin.flush()
            self.busy = True  # 命令开始运行，直到下一个 shell 提示符
            return True
        except Exception:  # noqa: BLE001
            return False

    def _feed(self, text: str) -> None:
        """Consume decoded text, splitting on \r (tqdm update) / \n (line)."""
        for ch in text:
            if ch == "\r":
                if self.pending and _parse_tqdm(self.pending):
                    self._set_prog(_parse_tqdm(self.pending))
                self.pending = ""
            elif ch == "\n":
                hit = _parse_tqdm(self.pending) if self.pending else None
                # 注意：\n 分支不设置 prog —— tqdm 实时更新用 \r（不换行），
                # \n 出现的"像 tqdm"的行常是命令回显文本（如 `46/48`），
                # 若在此 set_prog 会让已完成的进度条在空闲时"复活"闪现。
                # 仅当当前有活跃进度（prog 非 None 且未完成）时，\n 才可用于更新/收尾。
                if hit:
                    if self.prog is not None and hit.get("done", 0) >= hit.get("total", 0):
                        self._set_prog(hit)  # 完成行：清空 prog（隐藏进度条）
                    self.pending = None
                    continue
                else:
                    line = self.pending or ""
                    # 丢弃启动后短暂窗口内的初始输出（cmd banner / 初始提示符），保证新会话干净；
                    # 不依赖提示符判定（命令运行期间无提示符），用时间窗口避免一直丢弃
                    if self._startup:
                        if time.time() - self._startup_t0 >= 0.4:
                            self._startup = False
                        else:
                            self.pending = None
                            continue
                    if _PROMPT_RE.match(line):
                        self.busy = False  # 回到 shell 提示符 = 空闲
                        self.pending = None
                        continue  # 孤立设备提示符（E:\...>）不显示，termetron 用自己的提示符
                    m = _CMD_ECHO_RE.match(line)
                    if m:
                        # 命令回显 `E:\code\quant>cmd` → termetron 风格 `$ cmd`（历史里统一显示 termetron 提示符）
                        cmd = m.group(1).strip()
                        if not cmd:
                            self.pending = None
                            continue
                        line = f"{self.name} $ {cmd}"
                        self.busy = True  # 回显出现 = 有命令在跑
                    self.lines.append(line)
                self.pending = None
            else:
                self.pending = (self.pending or "") + ch
                self._maybe_clear_busy()

    def _maybe_clear_busy(self) -> None:
        """提示符出现后延迟判定空闲（命令回显 `X:\...>` 会先经过提示符形态）。

        若 0.4s 后 pending 仍是同一段提示符（没有命令字符追加），才认为空闲；
        否则说明是 `提示符+命令` 回显行，保持 busy。
        """
        if not _PROMPT_RE.match(self.pending or ""):
            return
        snap = self.pending

        def _check() -> None:
            if self.pending == snap and _PROMPT_RE.match(self.pending or ""):
                self.busy = False

        threading.Timer(0.4, _check).start()

    def _set_prog(self, hit: dict) -> None:
        """记录 tqdm 进度；首次非零进度时记起始时间（前端据此估算 ETA）。
        完成（done>=total）后清除，前端进度条随之隐藏。"""
        if hit.get("pct", 0) > 0 and self._tqdm_t0 is None:
            self._tqdm_t0 = time.time()
        if hit.get("done", 0) >= hit.get("total", 0):
            self._tqdm_t0 = None  # 完成：清除起始，下一进程重新计时
            self.prog = None      # 完成：清空进度，前端隐藏进度条
        else:
            self.prog = hit

    def _read(self) -> None:
        proc = self.proc
        assert proc.stdout is not None
        buf = b""
        while True:
            raw = proc.stdout.read(8192)
            if not raw:
                break
            buf += raw
            text, buf = _smart_decode(buf)
            self._feed(_strip_ansi(text).replace("\r\n", "\n"))
            self.updated = time.time()
        if buf:
            text, _ = _smart_decode(buf)
            self._feed(_strip_ansi(text).replace("\r\n", "\n"))
        proc.wait()
        if self.pending:
            hit = _parse_tqdm(self.pending)
            if hit:
                self._set_prog(hit)
            else:
                self.lines.append(self.pending)
        if self.proc is proc:  # 只有当前 shell 结束才标记 done
            self.done = True
            self.busy = False
        self.updated = time.time()

    def snapshot(self) -> dict:
        out = {
            "lines": self.lines[-600:],
            "progress": self.prog,
            "done": self.done,
            "busy": self.busy,
            "script": self.script,
            "cmd": self.cmd,
            "updated": time.strftime("%H:%M:%S", time.localtime(self.updated)),
        }
        if self.prog is not None and self._tqdm_t0 is not None:
            out["tqdm_t0"] = self._tqdm_t0
        return out

    def stop(self, rebuild: bool = True) -> None:
        """中断当前运行的进程（杀整个进程树），随后按需重建交互 shell。

        rebuild=True：会话保持可用（stop 后仍可继续输入）；
        rebuild=False：仅终止（用于删除会话 / 程序退出）。
        """
        pid = self.proc.pid
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=15,
                )
            else:
                self.proc.kill()
        except Exception:  # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self.busy = False
        if rebuild:
            self._spawn()

    def clear(self) -> None:
        """Clear the captured output (used for cls/clear on the web terminal)."""
        self.lines.clear()
        self.pending = None
        self.prog = None
        self.updated = time.time()


SESSIONS: dict[str, Session] = {}

def _load_about() -> dict:
    """从 lib/termetron/README.md 解析版本号（About 单一来源，随迭代递增）。"""
    info = {
        "version": "v0.0.1",
        "summary": "TERMETRON - geometric metron terminal<br>web terminal + mobile client",
        "readme": "lib/termetron/README.md",
    }
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "README.md"),
                  encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"(?m)^\*\*Version:\*\*\s*(\S+)", text)
        if m:
            info["version"] = m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return info


_QT_ABOUT = _load_about()


_INDEX = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Termetron</title>
<style>
 :root{--bg:#0a0e14;--panel:#0d131b;--bar:#171e2b;--border:#1b2733;--txt:#c9d1d9;--dim:#5a6a7a;--acc:#a78bfa}
 *{box-sizing:border-box;font-family:Consolas,'Cascadia Code','JetBrains Mono','Fira Code','Roboto Mono','Noto Sans Mono','Droid Sans Mono',Menlo,Monaco,'Courier New',monospace;-webkit-tap-highlight-color:transparent!important;-webkit-touch-callout:none}
 *:focus{outline:none}
 button:focus{box-shadow:0 0 0 2px rgba(167,139,250,.28)}
 body{margin:0;background:var(--bg);color:var(--txt);font-size:13px;display:flex;flex-direction:column;height:100vh;overflow:hidden}
 ::-webkit-scrollbar{width:10px;height:10px}
 ::-webkit-scrollbar-track{background:transparent}
 ::-webkit-scrollbar-thumb{background:#1b2733;border:2px solid var(--bg);border-radius:6px}
 ::-webkit-scrollbar-thumb:hover{background:rgba(167,139,250,.5)}
 ::-webkit-scrollbar-corner{background:transparent}
 header{display:flex;align-items:center;gap:12px;background:var(--bar);border-bottom:none;padding:12px 18px;position:relative}
 .cur{display:inline-flex;align-items:center;color:var(--acc);line-height:1}
 .cur svg{width:20px;height:20px}
 .ttl{font-family:'Segoe UI',system-ui,sans-serif;font-size:15px;font-weight:800;color:var(--acc);letter-spacing:3px}
 .client-tag{font-size:11px;color:var(--dim);letter-spacing:1px;align-self:center;margin-left:2px;padding-top:4px}
 #curtitle{font-family:Consolas,'Courier New',monospace;font-weight:700;letter-spacing:0}
 .tabs{display:flex;align-items:flex-end;gap:10px;background:var(--bar);padding:8px 14px 0;border-bottom:none;overflow-x:auto}
 .tab{font-size:13px;color:var(--txt);font-weight:600;padding:6px 16px 8px;border-radius:8px 8px 0 0;display:inline-flex;align-items:center;justify-content:center;min-width:100px;flex-shrink:0;cursor:pointer;border:none;background:rgba(255,255,255,.10);white-space:nowrap;transition:all .12s ease}
 .tab:hover{background:rgba(167,139,250,.16);color:var(--txt)}
 .tab.act{background:var(--bg);color:var(--acc);font-weight:700}
 .btn{font-size:14px;width:26px;height:26px;line-height:1;border-radius:6px;border:1px solid var(--border);background:var(--panel);color:var(--acc);cursor:pointer;flex:none;transition:all .12s ease}
 .btn.add{width:22px;height:22px;padding:0;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;background:var(--bar);border:none;color:var(--acc);font-size:15px;font-weight:600;line-height:1;align-self:center}
 .btn.add:hover{background:var(--acc);color:var(--panel)}
 .btn.stop{width:30px;height:30px;padding:0;border-radius:50%;display:none;align-items:center;justify-content:center;color:var(--acc);border-color:var(--border)}
 .btn.stop svg{width:16px;height:16px;display:block}
 .btn.stop:hover{background:var(--acc);color:var(--panel);border-color:var(--acc)}
 .btn.set{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;flex:none;padding:0;border:none;background:none;color:var(--acc);cursor:pointer;line-height:1;-webkit-tap-highlight-color:transparent;transition:opacity .15s ease}
 .btn.set:hover{opacity:.7}
 .btn.set svg{width:22px;height:22px}
 .btn.hm{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;flex:none;padding:0;border:none;background:none;color:var(--acc);cursor:pointer;line-height:1;-webkit-tap-highlight-color:transparent;transition:opacity .15s ease}
 .btn.hm:hover{opacity:.7}
 .btn.hm svg{width:22px;height:22px}
 .hbtns{margin-left:auto;display:inline-flex;align-items:center;gap:2px} /* 右侧按钮组：菜单/连接/会话等水平罗列，整体靠右 */
 .menu-opt.del{color:#ff7b72}
 .wrap{flex:1;min-height:0;display:flex;flex-direction:column;padding:12px 20px 0;overflow-y:auto}
 body.tunnel .wrap{visibility:hidden} /* 远程进入检测期间隐藏会话内容，检测通过才展示 */
 .inrow{flex:none;display:flex;align-items:flex-start;gap:8px;padding:10px 0 16px}
 .d{color:var(--acc);font-weight:700;font-size:14px;line-height:1.4}
 .inrow .d{align-self:flex-start;padding-top:5px}
 .inrow textarea{flex:1;background:none;border:none;color:var(--txt);font-size:13px;outline:none;padding:5px 2px;caret-color:var(--acc);resize:none;overflow-y:auto;min-height:24px;white-space:pre-wrap;word-break:break-all;line-height:1.4;font-family:inherit}
 .inrow input::placeholder{color:#3d4652}
 .running{color:var(--acc);font-size:14px;font-weight:700;letter-spacing:2px;line-height:1.4;align-self:flex-start;padding-top:5px;display:none}
 .ol{white-space:pre-wrap}
 .tblwrap{overflow-x:auto;margin:8px 0;border:1px solid var(--border);border-radius:6px}
 .tblwrap table{border-collapse:collapse;font-size:12px;width:100%}
 .tblwrap th,.tblwrap td{border:1px solid var(--border);padding:4px 12px;text-align:right;white-space:nowrap}
 .tblwrap th{color:var(--acc);background:rgba(167,139,250,.08);font-weight:600;text-align:center}
 .tblwrap td:first-child,.tblwrap th:first-child{text-align:left}
 .tblwrap tr:last-child td{border-bottom:none}
 .tblwrap tr:hover td{background:rgba(167,139,250,.06)}
 pre{flex:1 1 auto;min-height:0;margin:0;overflow-y:auto;line-height:1.55;white-space:pre-wrap;word-break:break-all;margin-right:-14px;padding-right:14px}
 .empty{display:none;flex:1;align-items:center;justify-content:center;color:var(--dim);font-size:20px;letter-spacing:2px}
 .empty.show{display:flex}
 .fbar{flex:none;background:var(--bar);border-top:1px solid var(--border);padding:10px 18px;position:relative}
 .prow{display:flex;align-items:center;gap:12px;font-size:12px;margin-bottom:6px}
 .ppct{color:var(--acc);font-weight:700;font-size:16px;min-width:46px}
 .pdesc{color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .pnum{color:var(--dim);font-size:11px;margin-left:auto}
 .pbar{height:16px;background:#10161f;border:1px solid var(--border);overflow:hidden}
 .pfill{height:100%;background:var(--acc);transition:width .4s;width:0%}
 .pmeta{display:flex;align-items:center;gap:8px;font-size:10px;color:var(--dim);margin-top:7px;letter-spacing:.5px}
 .pmeta .k{color:var(--acc)}
 .menu{position:relative;margin-left:auto}
 .menu-btn{display:inline-flex;align-items:center;justify-content:center;background:none;border:none;color:var(--dim);cursor:pointer;padding:3px 6px;transition:all .12s ease;border-radius:6px}
 .menu-btn svg{display:block}
 .menu-btn:hover{background:var(--acc);color:var(--panel)}
 .menu-pop{position:absolute;bottom:26px;right:0;background:var(--panel);border:1px solid var(--border);border-radius:6px;min-width:170px;padding:4px;display:none;z-index:20;box-shadow:0 6px 24px rgba(0,0,0,.5)}
 .menu-pop.open{display:block}
 .menu-item{display:block;width:100%;text-align:left;background:none;border:none;color:var(--txt);padding:7px 10px;font-size:12px;border-radius:4px;cursor:pointer;white-space:nowrap;transition:all .12s ease}
 .menu-item:hover{background:var(--acc);color:var(--panel)}
 .menu-item.del{color:var(--acc)}
 .menu-item.del:hover{background:var(--acc);color:var(--panel)}
 .overlay{position:fixed;inset:0;background:rgba(5,8,12,.72);display:none;align-items:center;justify-content:center;z-index:70}
 #rmask{background:#0a0e14} /* 认证遮罩不透明：挡住背景会话目录，防止输入密码时泄露信息 */
 .modal{background:var(--panel);border:1px solid var(--acc);border-radius:8px;width:min(440px,92vw);padding:18px;box-shadow:0 12px 44px rgba(0,0,0,.6)}
 .mtitle{font-size:13px;font-weight:700;color:var(--acc);letter-spacing:.5px;margin-bottom:12px}
 .field{margin-bottom:10px}
 .field label{display:block;font-size:11px;color:var(--dim);margin-bottom:4px;letter-spacing:.5px}
 .field input{width:100%;background:#10161f;border:1px solid var(--border);border-radius:4px;color:var(--txt);padding:7px 9px;font-size:12px;outline:none}
 .field input:focus{border-color:var(--acc)}
 .mmsg{font-size:13px;color:var(--txt);margin:4px 0}
 .srow{display:flex;align-items:center;gap:10px;padding:10px 4px;border-bottom:1px solid var(--border)}
 .srow.cur{color:var(--acc)}
 .srv-p{font-weight:700;color:var(--acc);min-width:64px}
 .srv-m{flex:1;font-size:11px;color:var(--dim)}
 .srv-acts{display:inline-flex;gap:6px}
 .sbtn{padding:3px 10px;border-radius:5px;border:1px solid var(--border);background:var(--panel);color:var(--acc);cursor:pointer;font-size:11px}
 .sbtn:hover{background:rgba(167,139,250,.14)}
 .sbtn.del{color:#ff7b72;border-color:rgba(255,123,114,.4)}
 .sbtn.del:hover{background:rgba(255,123,114,.14)}
 .sbtn.full{width:100%;margin-top:10px;padding:8px;font-weight:700}
 .mmsg b{color:var(--acc)}
 .mactions{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}
 .mbtn{padding:6px 16px;border-radius:5px;border:1px solid var(--border);background:var(--panel);color:var(--dim);cursor:pointer;font-size:12px;transition:all .12s ease}
 .mbtn:hover{background:var(--acc);color:var(--panel);border-color:var(--acc)}
 .mbtn.ok{color:var(--acc);border-color:var(--acc);font-weight:600}
 .mbtn.ok:hover{background:var(--acc);color:var(--panel)}
 .merr{color:var(--acc);font-size:12px;margin:8px 0 0;font-weight:600}
.btn.back{display:inline-flex;align-items:center;justify-content:center;width:38px;height:38px;flex:none;padding:0;border:none;background:none;color:var(--acc);cursor:pointer;line-height:1;-webkit-tap-highlight-color:transparent;transition:opacity .15s}
.btn.back:hover{opacity:.7}
.btn.back svg{width:26px;height:26px}
/* 移动端（窄屏）：会话目录页布局，电脑端保持标签栏 */
body.mobile .tabs{display:none}body.mobile #sesslist{flex:1;overflow-y:auto;padding:12px 14px;gap:10px;flex-direction:column}body.mobile .sess-item{text-align:left;background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:12px 14px;color:var(--txt);cursor:pointer;font-size:13px;display:flex;flex-direction:column;gap:6px}body.mobile .sess-item:active{background:rgba(167,139,250,.12)}body.mobile .sess-item .si-line{display:flex;align-items:center;gap:8px;justify-content:space-between}body.mobile .sess-item .si-name{font-weight:700;color:var(--acc);letter-spacing:.5px}body.mobile .sess-item .si-busy{color:var(--acc);font-size:10px;font-weight:700;letter-spacing:1px}body.mobile .sess-item .si-idle{color:var(--dim);font-size:10px}body.mobile .sess-item .si-cmd{color:var(--dim);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}body.mobile .sess-item.new{justify-content:center;align-items:center;border-style:dashed;color:var(--acc);font-weight:600;letter-spacing:1px}body.mobile .sess-empty{color:var(--dim);text-align:center;padding:40px 0;letter-spacing:2px;font-size:12px}body.mobile .fbar .prow{flex-wrap:wrap}body.mobile .fbar .pmeta{flex-wrap:wrap}body.mobile #connbtn{display:none}body.mobile header{background:var(--bar);border-bottom:1px solid var(--border)}body.mobile .fbar #pmeta-ses{flex-direction:column;align-items:stretch;gap:2px}body.mobile .fbar .pm-item{display:inline-flex;gap:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}body.mobile .fbar #pmeta-ses .menu{position:absolute;right:12px;bottom:6px}
:root{--fs:13px}
#out{font-size:var(--fs)}
.inrow textarea{font-size:var(--fs)}
.modal{max-height:82vh;overflow-y:auto}
/* 设置面板 */
.set-sec{margin:0 0 14px;text-align:left}
.set-h{font-size:11px;font-weight:700;letter-spacing:1px;color:var(--dim);margin:0 0 8px}
.set-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.set-mini{padding:6px 12px;border:1px solid var(--border);border-radius:6px;background:var(--panel);color:var(--acc);font-size:12px;font-weight:600;cursor:pointer}
.set-mini.del{color:#ff7b72;font-size:11px}
.set-val{color:var(--txt);font-size:13px;min-width:44px;text-align:center;font-weight:600}
.swatch{width:30px;height:30px;border-radius:50%;border:2px solid transparent;cursor:pointer}
.swatch.on{border-color:var(--txt)}
.set-sess{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px}
.ss-name{font-size:12px;color:var(--txt);font-weight:600}
.set-empty{color:var(--dim);font-size:12px}
.set-about{color:var(--dim);font-size:12px;line-height:1.7}
.menu-opt{display:flex;align-items:center;justify-content:space-between;width:100%;padding:14px 16px;margin-bottom:8px;background:var(--panel);border:1px solid var(--border);border-radius:8px;color:var(--txt);font-size:14px;font-weight:600;cursor:pointer;text-align:left;-webkit-tap-highlight-color:transparent}
.menu-opt:hover{background:rgba(167,139,250,.1)}
.menu-opt .mo-arr{color:var(--acc);font-size:16px}
/* 设置下拉框（图标下方） */
#dropdown,#hdropdown{position:absolute;top:calc(100% + 6px);right:12px;background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:6px;min-width:210px;box-shadow:0 12px 36px rgba(0,0,0,.55);z-index:50}
.dd-item{display:flex;align-items:center;justify-content:space-between;width:100%;padding:12px 14px;border-radius:6px;border:none;background:none;color:var(--txt);font-size:13px;font-weight:600;cursor:pointer;text-align:left;-webkit-tap-highlight-color:transparent}
.dd-item:hover{background:rgba(167,139,250,.12)}
#hd-del{color:#ff7b72}
.dd-item .mo-arr{color:var(--acc)}
/* 全屏设置页 */
.fullpage{position:fixed;inset:0;background:var(--bg);z-index:60;display:flex;flex-direction:column}
.fp-header{display:flex;align-items:center;gap:8px;background:var(--bar);padding:12px 16px;flex:none}
.fp-back{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;padding:0;border:none;background:none;color:var(--acc);cursor:pointer;line-height:1;-webkit-tap-highlight-color:transparent}
.fp-back svg{width:24px;height:24px}
.fp-title{font-family:Consolas,'Courier New',monospace;font-size:15px;font-weight:700;letter-spacing:0;color:var(--acc)}
.fp-body{flex:1;overflow-y:auto;padding:18px}
</style><meta name="theme-color" content="#0b0f14">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
</head><body>
<header><span class="cur"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="1.5" y="2" width="21" height="20" rx="4" stroke-width="1.6"></rect><path d="M9 8 L14 12 L9 16" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path><path d="M14.5 15.5 H19" stroke-width="2" stroke-linecap="round"></path></svg></span><button class="btn back" id="backbtn" style="display:none" title="all sessions"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg></button><span class="ttl" id="brand">TERMETRON</span><span class="client-tag" id="clienttag"></span><span class="ttl" id="curtitle" style="display:none"></span><span class="hbtns"><button class="btn hm" id="hmbtn" style="display:none" title="session actions"><svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.7"></circle><circle cx="12" cy="12" r="1.7"></circle><circle cx="19" cy="12" r="1.7"></circle></svg></button><button class="btn hm" id="connbtn" title="connect (termetron remote on)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg></button><button class="btn set" id="setbtn" title="menu"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"></line><line x1="3" y1="12" x2="21" y2="12"></line><line x1="3" y1="18" x2="21" y2="18"></line></svg></button></span>
<div id="dropdown" style="display:none">
  <button class="dd-item" id="dd-sess">MANAGE SESSIONS</button>
  <button class="dd-item" id="dd-more">MORE SETTINGS</button>
  <button class="dd-item" id="dd-browser">OPEN IN BROWSER</button>
  <button class="dd-item" id="dd-switch">SWITCH SERVER</button>
</div>
<div id="hdropdown" style="display:none">
  <button class="dd-item" id="hd-ren">RENAME SESSION</button>
  <button class="dd-item" id="hd-del">DELETE SESSION</button>
</div>
</header>
<nav class="tabs" id="tabs"></nav>
<div id="sesslist" style="display:none"></div>
<div class="wrap">
<pre id="out"></pre>
<div class="empty" id="empty">NO SESSION</div>
<div class="inrow" id="inrow"><span class="d" id="prompt">$</span><textarea id="in" spellcheck="false" autocomplete="off" placeholder="type a command ..." rows="1"></textarea><span class="running" id="running">running ...</span><button class="btn stop" id="stopbtn" title="stop (Ctrl+C)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" fill="currentColor" stroke="none"></rect></svg></button></div>
</div>
<footer class="fbar">
 <div class="prow"><span class="ppct" id="ppct">--</span><span class="pdesc" id="pdesc">standby</span><span class="pnum" id="pnum"></span></div>
 <div class="pbar"><div class="pfill" id="pfill"></div></div>
 <div class="pmeta" id="pmeta-srv"><span class="k">[server]</span> <span id="srvmeta">--</span></div>
 <div class="pmeta" id="pmeta-ses">
   <span class="pm-item"><span class="k">[status]</span> <span id="meta">waiting</span></span>
   <span class="pm-item"><span class="k">[script]</span> <span id="scriptmeta">--</span></span>
   <span class="menu" id="menu">
     <button class="menu-btn" id="menubtn" title="session actions"><svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg></button>
     <div class="menu-pop" id="menupop">
       <button class="menu-item" id="menuren">rename session</button>
       <button class="menu-item del" id="menudel">delete session</button>
     </div>
   </span>
 </div>
</footer>
<div class="overlay" id="overlay">
 <div class="modal">
   <div class="mtitle" id="mtitle"></div>
   <div class="mbody" id="mbody"></div>
   <div class="mactions">
     <button class="mbtn cancel" id="mcancel">CANCEL</button>
     <button class="mbtn ok" id="mok">OK</button>
   </div>
 </div>
</div>
<div class="overlay" id="rmask" style="display:none">
 <div class="modal" style="width:min(360px,92vw)">
   <div class="mtitle">CONNECT TO TERMETRON</div>
   <div class="mbody">
     <p class="mmsg">Enter the one-time password shown by <b>termetron remote on</b> on the computer.</p>
     <div class="field"><label>one-time password</label><input id="rtok" spellcheck="false" autocomplete="off" placeholder="\u2022\u2022\u2022\u2022\u2022\u2022"></div>
     <div id="rpair" style="display:none">
       <p class="mmsg">Pairing code:</p>
       <div id="rkey" style="font-size:26px;letter-spacing:5px;font-weight:700;color:var(--acc)"></div>
       <p class="mmsg" style="font-size:11px">Enter this 4-digit code on the computer to complete pairing.</p>
       <p class="mmsg" id="rwait" style="opacity:.55">waiting for approval ...</p>
     </div>
     <p class="merr" id="rerr" style="display:none"></p>
   </div>
   <div class="mactions">
     <button class="mbtn cancel" id="rback">Back</button>
     <button class="mbtn ok" id="rconn">Connect</button>
   </div>
 </div>
</div>
<div class="overlay" id="cmask" style="display:none;background:#0a0e14">
 <div class="modal" style="width:min(360px,92vw)">
   <div class="mtitle" id="ctitle">TUNNEL CLOSED</div>
   <div class="mbody" id="cbody">
     <p class="mmsg">The tunnel is closed or no longer available.</p>
     <p class="mmsg" style="font-size:11px;opacity:.6">The tunnel on the computer is not running. Run <b>termetron remote on</b> on the computer, then reconnect from the login page.</p>
   </div>
   <div class="mactions"><button class="mbtn ok" id="cnew" style="display:none">Restart Tunnel</button><button class="mbtn ok" id="cback">Back</button></div>
 </div>
</div>
<div id="fullpage" class="fullpage" style="display:none">
  <div class="fp-header">
    <button class="fp-back" id="fpback" title="back"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg></button>
    <span class="fp-title" id="fptitle"></span>
  </div>
  <div class="fp-body" id="fpbody"></div>
</div>
<script>
const QT_ABOUT = __QT_ABOUT__;  // 服务器注入：来自 lib/termetron/README.md 的版本/简介
let current = null;
let sessionsData = {};
// 移动端（窄屏 <768px）：会话目录页 view='home' <-> 会话视窗 view='session'；电脑端用标签栏
// 设备类型判断：不看屏幕宽度，看指针能力（有鼠标=桌面，触屏/无指针=移动）
const mqMobile = window.matchMedia('(hover: none) and (pointer: coarse), (hover: none) and (pointer: none)');
// 调试/预览：URL ?m=1 强制移动端，?d=1 强制桌面端
const _forceM = new URLSearchParams(location.search).get('m');
const _forceD = new URLSearchParams(location.search).get('d');
let mobile = _forceM ? true : _forceD ? false : mqMobile.matches;
document.body.classList.toggle('mobile', mobile);  // body.mobile 类驱动移动端 CSS（支持 ?m=1 预览）
// header 客户端标签：桌面端 Desktop Client / 移动端 Mobile Client（与 App 连接页 sub 一致风格，区分设备）
const clientTag = document.getElementById('clienttag');
function setClientTag(){ if (!clientTag) return; if (!mobile) { clientTag.textContent = 'Desktop Client'; return; } clientTag.textContent = view === 'session' ? '' : 'Mobile Client'; }
let view = 'home';
setClientTag();  // 必须在 view 声明后调用（否则 TDZ：Cannot access 'view' before initialization）
// 设置持久化（字体大小 / 主题色）
try{ const f = localStorage.getItem('qt_fs'); if (f) document.documentElement.style.setProperty('--fs', f + 'px'); }catch(e){}
try{ const t = localStorage.getItem('qt_theme'); if (t) document.documentElement.style.setProperty('--acc', t); }catch(e){}
async function api(path, opts) { const r = await fetch(path, opts); return r.json(); }
function openModal(title, body, okText, onOk) {
  document.getElementById('mtitle').textContent = title;
  document.getElementById('mbody').innerHTML = body;
  const ok = document.getElementById('mok');
  ok.textContent = okText;
  ok.onclick = async () => { if (await onOk()) closeModal(); };
  document.getElementById('mcancel').onclick = closeModal;
  document.getElementById('overlay').style.display = 'flex';
  // 键盘优先（终端无鼠标必须可用）：有输入框时聚焦输入框（Enter 确认），
  // 无输入框时聚焦确认按钮（Enter/Space 触发）；Esc 一律关闭（或触发 DENY）。
  const first = document.getElementById('mbody').querySelector('input');
  if (first) {
    first.focus();
    first.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); document.getElementById('mok').click(); } };
  } else {
    ok.focus();
  }
  document.onkeydown = (e) => { if (e.key === 'Escape') { e.preventDefault(); document.getElementById('mcancel').click(); } };
}
function modalError(msg) {
  let e = document.getElementById('merr');
  if (!e) { e = document.createElement('p'); e.id = 'merr'; e.className = 'merr'; document.getElementById('mbody').appendChild(e); }
  e.textContent = msg;
}
function closeModal() {
  document.getElementById('overlay').style.display = 'none';
  document.onkeydown = null;
}
function closeMenu() { document.getElementById('menupop').classList.remove('open'); }

let lastTabs = '';
// 空闲自动聚焦跟踪：首次加载 / busy→空闲 时自动 focus 输入框（终端无鼠标可用）
let prevBusy = false, initBusy = false;
// 会话 busy 状态跟踪（用于"进程完成"通知）：会话名 -> busy
let _prevBusyMap = {};
function notifyDone(name) {
  // 移动端 + 不在前台时发系统通知（Capacitor WebView 用 document.title 提示，浏览器用 Notification API）
  const title = 'termetron: ' + name + ' done';
  const body = (sessionsData[name] && sessionsData[name].script
    ? (sessionsData[name].script.name || name) : name) + ' finished';
  try {
    if (document.hidden && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      new Notification(title, { body, tag: 'term-done-' + name });
    } else if (document.hidden) {
      document.title = title;   // 降级：改标题提示（WebView/无权限）
    }
  } catch (e) { /* 忽略通知异常 */ }
}
function trackBusy(s) {
  for (const n of Object.keys(s)) {
    const b = !!s[n].busy;
    if (_prevBusyMap[n] === true && b === false) notifyDone(n);  // busy→idle 转换
    _prevBusyMap[n] = b;
  }
  for (const n of Object.keys(_prevBusyMap)) {
    if (!(n in s)) delete _prevBusyMap[n];  // 会话已删，清理
  }
}

async function refreshSessions() {
  let s;
  try { s = await api('/api/sessions'); }
  catch (e) { document.getElementById('stopbtn').style.display = 'none'; return; }
  sessionsData = s;
  trackBusy(s);
  const names = Object.keys(s);
  if (mobile) { renderSessList(names); }
  if (!current && names.length) current = names[0];
  if (current && !names.includes(current)) current = names[0] || null;
  const key = names.join('\u0001') + '|' + (current || '');
  if (key !== lastTabs) {
    lastTabs = key;
    const tabs = document.getElementById('tabs');
    tabs.innerHTML = '';
    names.forEach(n => {
      const a = document.createElement('button');
      a.className = 'tab' + (n === current ? ' act' : '');
      a.textContent = n;
      a.title = s[n].cmd;
      a.onclick = () => { current = n; refreshAll(); document.getElementById('in').focus(); };
      tabs.appendChild(a);
    });
    const add = document.createElement('button'); add.className='btn add'; add.textContent='+'; add.title='new session (Ctrl+K)';
    add.onclick = newSessionModal;
    tabs.appendChild(add);
  }
  const meta = document.getElementById('meta');
  meta.textContent = names.length + ' session(s)' + (current ? ' · "' + current + '"' : '');
  document.getElementById('curtitle').textContent = current || '';  // 移动端会话视窗 header 会话名
  // 正在跑的脚本信息：有编号显示 S0x 短名，否则用程序名兜底
  const sm = document.getElementById('scriptmeta');
  if (current && s[current].script) {
    const sc = s[current].script;
    sm.textContent = (sc.id ? sc.id + ' ' : '') + sc.name;
  } else {
    sm.textContent = '--';
  }
  const menubtn = document.getElementById('menubtn');
  menubtn.disabled = !current;
  menubtn.style.opacity = current ? 1 : 0.35;
  // 提示符 = 当前会话名；无会话时只显示空状态图标
  const empty = document.getElementById('empty');
  const inrow = document.getElementById('inrow');
  const prompt = document.getElementById('prompt');
  if (current) {
    empty.classList.remove('show');
    inrow.style.display = 'flex';
    prompt.textContent = current + ' $';
    const inp = document.getElementById('in');
    const stopbtn = document.getElementById('stopbtn');
    const running = document.getElementById('running');
    const busy = current && sessionsData[current] && sessionsData[current].busy;
    // busy：隐藏命令行提示（只显示 running + stop），禁止输入新命令
    prompt.style.display = busy ? 'none' : '';
    inp.style.display = busy ? 'none' : '';
    inp.disabled = !!busy;
    running.style.display = busy ? 'inline' : 'none';
    stopbtn.style.display = busy ? 'inline-flex' : 'none';
    // 空闲时默认输入状态（无需点击激活）；仅首次加载或 busy→空闲 时自动聚焦
    if (busy) { prevBusy = true; }
    else if (prevBusy || !initBusy) { inp.focus(); prevBusy = false; }
    initBusy = true;
  } else {
    empty.classList.add('show');
    inrow.style.display = 'none';
    const inp = document.getElementById('in');
    const stopbtn = document.getElementById('stopbtn');
    const running = document.getElementById('running');
    inp.disabled = true;
    inp.style.display = '';
    prompt.style.display = '';
    running.style.display = 'none';
    stopbtn.style.display = 'none';
  }
}
let _progHideTimer = null;
let _progEverShown = false;
let _progVisible = false;  // 当前进度条是否可见（由 setProgress 维护；applyView 会话页据此保持，防闪现）
function setProgress(p, t0, busy) {
  const prow = document.querySelector('.prow'), pbar = document.querySelector('.pbar');
  const d = document.getElementById('pdesc'), c = document.getElementById('ppct'),
        n = document.getElementById('pnum'), f = document.getElementById('pfill');
  const hasP = p && p.pct !== undefined;
  if (hasP) _progEverShown = true;
  // 会话空闲（busy=false）→ 彻底隐藏（清状态，供下个进程重新判断）
  if (!busy && !hasP) {
    _progEverShown = false;
    _progVisible = false;
    if (_progHideTimer) { clearTimeout(_progHideTimer); _progHideTimer = null; }
    if (prow) prow.style.display = 'none';
    if (pbar) pbar.style.display = 'none';
    return;
  }
  // busy 但此刻无进度：若有历史进度则保留显示（运行中不因短暂 None 频闪）；
  // 无历史进度（还没见过 tqdm）则隐藏
  if (!hasP) {
    if (!_progEverShown) {
      _progVisible = false;
      if (prow) prow.style.display = 'none';
      if (pbar) pbar.style.display = 'none';
    }
    return;
  }
  // 有进度：立即显示并取消待定隐藏
  _progVisible = true;
  if (_progHideTimer) { clearTimeout(_progHideTimer); _progHideTimer = null; }
  if (prow) prow.style.display = '';
  if (pbar) pbar.style.display = '';
  d.textContent = p.desc; c.textContent = Math.round(p.pct) + '%';
  // 时间估计：已用时间 / 进度 → 剩余时间；t0 来自服务端（snapshot 的 tqdm_t0）
  let eta = '';
  if (t0 && p.total > 0 && p.done > 0) {
    const elapsed = (Date.now() / 1000) - t0;
    const rate = elapsed / p.done;                 // 秒/单位
    const remain = Math.max(0, rate * (p.total - p.done));
    eta = ' · ' + fmtDur(remain);
  }
  n.textContent = p.done + '/' + p.total + eta; f.style.width = p.pct + '%';
}
function fmtDur(s) {
  if (!(s > 0) || !isFinite(s)) return '';
  if (s < 60) return Math.round(s) + 's';
  if (s < 3600) { const m = Math.floor(s / 60); return m + 'm' + Math.round(s % 60) + 's'; }
  const h = Math.floor(s / 3600); return h + 'h' + Math.round((s % 3600) / 60) + 'm';
}
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
// 检测连续 pipe 表格（| a | b |）与连续 tab 表格（a\tb\tc）并渲染为 HTML 表格。
// tab 表格启发式：连续行 + tab 数量一致且 >=2（至少 3 列），避免把缩进/树状/日志误判为表格。
function renderOutput(lines) {
  const tabGrp = new Array(lines.length).fill(false);
  for (let i = 0; i < lines.length - 1; i++) {
    if (tabGrp[i]) continue;
    const t = (lines[i].trim().match(/\t/g) || []).length;
    if (t < 2) continue;
    let j = i + 1;
    while (j < lines.length) {
      const tj = (lines[j].trim().match(/\t/g) || []).length;
      if (tj !== t) break;
      j++;
    }
    if (j - i >= 2) { for (let k = i; k < j; k++) tabGrp[k] = true; }
    i = j - 1;
  }
  let html = '', inT = false, head = false;
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const line = raw.trim();
    const isPipe = line.startsWith('|') && line.endsWith('|') && line.indexOf('|', 1) > 0;
    if (isPipe || tabGrp[i]) {
      const cells = isPipe
        ? line.slice(1, -1).split('|').map(c => c.trim())
        : line.split('\t').map(c => c.trim());
      if (isPipe && cells.every(c => /^:?-{2,}:?$/.test(c))) continue; // markdown 分隔行 |---|
      if (!inT) { html += '<div class="tblwrap"><table>'; inT = true; head = true; }
      const tag = head ? 'th' : 'td'; head = false;
      html += '<tr>' + cells.map(c => '<' + tag + '>' + esc(c) + '</' + tag + '>').join('') + '</tr>';
    } else {
      if (inT) { html += '</table></div>'; inT = false; }
      // termetron 提示符行：`name $ cmd`（如 `fin $ python ...`）或旧格式 `$ cmd`，
      // `name $` / `$` 用主题色（.d），命令保持文本色
      const pm = raw.match(/^(\$) (.*)$/) || raw.match(/^(.+? \$) (.*)$/);
      if (pm) {
        const nm = pm[1].replace(/\s+\$$/, '').trim();
        const valid = pm[1] === '$' || (sessionsData && nm in sessionsData);
        if (valid) html += '<div class="ol"><span class="d">' + esc(pm[1]) + '</span> ' + esc(pm[2]) + '</div>';
        else html += '<div class="ol">' + esc(raw) + '</div>';
      } else {
        html += '<div class="ol">' + esc(raw) + '</div>';
      }
    }
  }
  if (inT) html += '</table></div>';
  return html;
}
// 滚动：默认自动跟随最新输出（提示符贴底）；用户手动上滚浏览时暂停自动跟随，避免被弹回底部
let autoScroll = true;
const _out = document.getElementById('out');
_out.addEventListener('scroll', () => {
  autoScroll = _out.scrollHeight - _out.scrollTop - _out.clientHeight < 40;
});
async function refreshOutput() {
  if (mobile && view !== 'session') return;  // 移动端目录页不刷输出
  if (!current) { document.getElementById('out').innerHTML = ''; setProgress(null); return; }
  const o = await api('/api/output/' + current);
  if (!o || !o.lines) return;
  const pre = document.getElementById('out');
  // 内容未变化时跳过重建：避免每轮 innerHTML 重建打断用户选中/复制（选区会丢失）
  const key = o.lines.join('\\n');
  if (pre.__lastKey === key) { setProgress(o.progress, o.tqdm_t0, o.busy); return; }
  pre.__lastKey = key;
  const prevTop = pre.scrollTop, prevH = pre.scrollHeight;
  pre.innerHTML = renderOutput(o.lines);
  if (autoScroll) {
    pre.scrollTop = pre.scrollHeight;              // 跟随最新：提示符贴底
  } else {
    pre.scrollTop = prevTop + (pre.scrollHeight - prevH);  // 保持用户浏览位置
  }
  setProgress(o.progress, o.tqdm_t0, o.busy);
}
// 命令输入框：回车发送到当前会话（像真实终端）；cls/clear 清屏
//（cmd 的 cls 只清控制台缓冲区，管道里无清屏信号，故在此拦截真正清空输出）
// 快捷键：Ctrl+C 中断进程 / Ctrl+L 清屏 / Ctrl+K 新会话 / Ctrl+1..9 切换会话
function busyFlag() {
  return !!(current && sessionsData[current] && sessionsData[current].busy);
}
async function stopNow() {
  if (current) await api('/api/sessions/' + current + '/stop', {method:'POST'});
}
function newSessionModal() {
  openModal('NEW SESSION',
    '<div class="field"><label>session name</label><input id="nname" spellcheck="false" placeholder="e.g. demo"></div>',
    'Create', async () => {
      const name = document.getElementById('nname').value.trim();
      if (!name) { modalError('session name required'); return false; }
      const r = await api('/api/sessions', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name, cmd: null})});
      if (r.error) { modalError(r.error); return false; }
        hist[name] = []; histIdx[name] = 0;  // 新会话无历史（防同名重建残留）
        current = name; await refreshAll();  // 等会话列表刷新完成，管理页才能显示新卡片
        // 管理会话页内新建：留在管理页并刷新列表（不退出该页面）；否则进入新会话
        const fp = document.getElementById('fullpage');
        if (fp && fp.style.display === 'flex') { openSessionManager(); } else { document.getElementById('in').focus(); }
        return true;
    });
}
const inp = document.getElementById('in');
// textarea 自动增高：无上限，随输入内容无限扩展（输入多行时页面/区域滚动查看全部）
function resizeInp() {
  inp.style.height = 'auto';
  inp.style.height = inp.scrollHeight + 'px';
}
inp.addEventListener('input', resizeInp);
// 每会话命令历史（↑ 上一条 / ↓ 下一条，终端原生行为）
let hist = {}, histIdx = {};
function saveHist(name, text) {
  const h = (hist[name] = hist[name] || []);
  if (h[h.length - 1] !== text) h.push(text);
  if (h.length > 200) h.shift();
  histIdx[name] = h.length;
}
// termetron 命令（无鼠标操作）：termetron new / use / del / rename / ls / clear / stop / help
function showMsg(msg) { openModal('TERMETRON', '<p class="mmsg">' + msg + '</p>', 'OK', () => true); }
const HELP_HTML = '<p class="mmsg" style="line-height:1.8;white-space:pre-wrap">' +
  '<b>termetron new &lt;name&gt;</b>          create a session<br>' +
  '<b>termetron use &lt;name&gt;</b>          switch to a session<br>' +
  '<b>termetron del [name]</b>          delete a session, default current<br>' +
  '<b>termetron rename &lt;old&gt; &lt;new&gt;</b>  rename a session<br>' +
  '<b>termetron ls</b>                  list sessions<br>' +
  '<b>termetron clear</b>               clear current output (alias: cls)<br>' +
  '<b>termetron stop</b>                interrupt current process<br>' +
  '<b>termetron help</b>                show this help</p>';
async function handleTmt(text) {
  const parts = text.trim().split(/\s+/);
  const c = (parts[0] === 'termetron' && parts[1]) ? parts[1].toLowerCase() : null;
  const a = parts.slice(2);
  const focus = () => document.getElementById('in').focus();
  if (!c) { showMsg('usage: termetron <command>  (try termetron help)'); focus(); return true; }
  switch (c) {
    case 'new': {
      const name = a[0];
      if (!name) { showMsg('usage: termetron new <name>'); focus(); return true; }
      const r = await api('/api/sessions', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name, cmd: null})});
      if (r.error) showMsg(r.error); else { current = name; hist[name] = []; histIdx[name] = 0; }
      refreshAll(); focus(); return true;
    }
    case 'use': {
      const name = a[0];
      if (name && sessionsData && name in sessionsData) { current = name; refreshAll(); }
      else showMsg('no such session: ' + name);
      focus(); return true;
    }
    case 'del': {
      const name = a[0] || current;
      const r = await api('/api/sessions/' + name, {method:'DELETE'});
      if (r.error) showMsg(r.error);
      else { delete hist[name]; delete histIdx[name]; }
      refreshAll(); focus(); return true;
    }
    case 'rename': {
      const oldn = a[0], newn = a[1];
      if (!oldn || !newn) { showMsg('usage: termetron rename <old> <new>'); focus(); return true; }
      const r = await api('/api/sessions/' + oldn + '/rename', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name: newn})});
      if (r.error) showMsg(r.error);
      else {
        if (hist[oldn]) { hist[newn] = hist[oldn]; histIdx[newn] = histIdx[oldn]; delete hist[oldn]; delete histIdx[oldn]; }
        if (current === oldn) current = newn;
      }
      refreshAll(); focus(); return true;
    }
    case 'ls': {
      const lines = Object.keys(sessionsData || {}).map(n => (sessionsData[n].busy ? '[run ] ' : '[idle] ') + n);
      openModal('SESSIONS', '<p class="mmsg" style="line-height:1.8;white-space:pre-wrap">' + (lines.join('\\n') || '(none)') + '</p>', 'OK', () => true);
      return true;
    }
    case 'clear': {
      if (current) await api('/api/sessions/' + current + '/clear', {method:'POST'});
      return true;
    }
    case 'stop': {
      if (current) await api('/api/sessions/' + current + '/stop', {method:'POST'});
      return true;
    }
    case 'help': {
      openModal('COMMANDS', HELP_HTML, 'OK', () => true);
      return true;
    }
    case 'remote': {
      const sub = (a[0] || '').toLowerCase();
      if (sub === 'on') {
        const r = await api('/api/remote/start', {method:'POST'});
        if (r.error) showMsg('remote error: ' + r.error);
        else showRemotePanel();
        focus(); return true;
      }
      if (sub === 'off') {
        const r = await api('/api/remote/stop', {method:'POST'});
        showMsg(r.error ? ('error: ' + r.error) : 'remote tunnel closed (all sessions/pairings invalidated)');
        focus(); return true;
      }
      if (sub === 'status') {
        const r = await api('/api/remote/status');
        showMsg('REMOTE STATUS\\nstatus: ' + r.status + '\\nurl: ' + (r.url || '-') + '\\ntoken: ' + (r.token || '-') + '\\npending: ' + JSON.stringify(r.pending) + '\\nallowed: ' + r.allowed);
        focus(); return true;
      }
      showMsg('usage: termetron remote on|off|status'); focus(); return true;
    }
    case 'allow': {
      const key = (a[0] || '').trim();
      if (!key) { showMsg('usage: termetron allow <key>'); focus(); return true; }
      const r = await api('/api/remote/allow', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({key})});
      if (r.error) showMsg('allow error: ' + r.error); else showMsg('device allowed: ' + key);
      focus(); return true;
    }
    default:
      showMsg('unknown termetron command: ' + c + '  (try termetron help)');
      focus();
      return true;
  }
}
inp.onkeydown = async (e) => {
  if (!current) return;
  // Ctrl+1..9：切换会话（空闲时可用）
  if ((e.ctrlKey || e.metaKey) && /^[1-9]$/.test(e.key)) {
    const names = Object.keys(sessionsData);
    const n = parseInt(e.key, 10) - 1;
    if (names[n]) { current = names[n]; refreshAll(); document.getElementById('in').focus(); e.preventDefault(); }
    return;
  }
  // 命令历史：↑ 上一条 / ↓ 下一条
  if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
    const h = hist[current] || [];
    if (!h.length) return;
    e.preventDefault();
    const idx = histIdx[current] ?? h.length;
    histIdx[current] = e.key === 'ArrowUp'
      ? Math.max(0, idx - 1)
      : Math.min(h.length, idx + 1);
    inp.value = histIdx[current] < h.length ? h[histIdx[current]] : '';
    resizeInp();
    return;
  }
  // Shift+Enter 换行；Enter 提交（textarea 需 preventDefault 阻止默认换行）
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    const text = e.target.value;
    e.target.value = '';
    inp.style.height = '';   // 提交后重置为一行（下次输入默认仍是一行）
    if (!text.trim()) return;
    saveHist(current, text);
    const trimmed = text.trim();
    // termetron 命令：键盘完成会话管理（无鼠标可用），不发给 cmd；其余全部投终端
    if ((trimmed === 'termetron' || trimmed.startsWith('termetron ')) && await handleTmt(trimmed)) return;
    const t = trimmed.toLowerCase();
    if (t === 'cls' || t === 'clear') {
      await api('/api/sessions/' + current + '/clear', {method:'POST'});
      return;
    }
    await api('/api/sessions/' + current + '/input', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text})});
  }
};
// 全局快捷键（busy 时输入框 disabled 也能响应）：
//   Ctrl+C 中断当前进程 / Ctrl+L 清屏 / Ctrl+K 新建会话
window.addEventListener('keydown', (e) => {
  if (!(e.ctrlKey || e.metaKey) || !current) return;
  const k = e.key.toLowerCase();
  if (k === 'c') {
    if (busyFlag()) { e.preventDefault(); stopNow(); }  // 空闲时保留原生复制
  } else if (k === 'l') {
    e.preventDefault();
    api('/api/sessions/' + current + '/clear', {method:'POST'});
  } else if (k === 'k') {
    e.preventDefault(); newSessionModal();
  }
});
document.getElementById('stopbtn').onclick = stopNow;
document.getElementById('backbtn').onclick = () => { view = 'home'; refreshAll(); };
document.getElementById('setbtn').onclick = (e) => { e.stopPropagation(); toggleDropdown(); };
document.getElementById('dd-sess').onclick = () => { closeDropdown(); openSessionManager(); };
document.getElementById('dd-more').onclick = () => { closeDropdown(); openMoreSettings(); };
document.getElementById('fpback').onclick = closeFullPage;
document.getElementById('menubtn').onclick = (e) => {
  e.stopPropagation();
  document.getElementById('menupop').classList.toggle('open');
};
function renameSessionModal() {
  if (!current) return;
  openModal('RENAME SESSION',
    '<div class="field"><label>new name</label><input id="nname" spellcheck="false" placeholder="' + current + '"></div>',
    'Rename', async () => {
      const name = document.getElementById('nname').value.trim();
      if (!name) { modalError('name required'); return false; }
      if (name === current) { closeModal(); return true; }
      const r = await api('/api/sessions/' + current + '/rename', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name})});
      if (r.error) { modalError(r.error); return false; }
      current = name; refreshAll(); return true;
    });
}
function deleteSessionModal() {
  if (!current) return;
  openModal('DELETE SESSION',
    '<p class="mmsg">delete session <b>' + current + '</b>?</p>',
    'Delete', async () => {
      const r = await api('/api/sessions/' + current, {method:'DELETE'});
      if (r.error) { modalError(r.error); return false; }
      delete hist[current]; delete histIdx[current];  // 删除会话同时清历史
      current = null; view = 'home'; refreshAll(); return true;   // 删除当前会话后回到会话目录页
    });
}
document.getElementById('menuren').onclick = () => { closeMenu(); renameSessionModal(); };
document.getElementById('menudel').onclick = () => { closeMenu(); deleteSessionModal(); };
// 手机端会话视窗 header 的三点会话操作下拉框（同 ☰ 一致）
function toggleHDropdown() { const d = document.getElementById('hdropdown'); d.style.display = d.style.display !== 'block' ? 'block' : 'none'; }
function closeHDropdown() { document.getElementById('hdropdown').style.display = 'none'; }
document.getElementById('hmbtn').onclick = (e) => { e.stopPropagation(); toggleHDropdown(); };
document.getElementById('hd-ren').onclick = () => { closeHDropdown(); renameSessionModal(); };
document.getElementById('hd-del').onclick = () => { closeHDropdown(); deleteSessionModal(); };
document.addEventListener('click', (e) => {
  if (!e.target.closest('.menu')) closeMenu();
  if (!e.target.closest('#dropdown') && !e.target.closest('#setbtn')) closeDropdown();
  if (!e.target.closest('#hdropdown') && !e.target.closest('#hmbtn')) closeHDropdown();
});
async function refreshAll() {
  setClientTag();
  const srv = document.getElementById('srvmeta');
  if (srv) srv.textContent = location.host;  // 当前连接的服务器（localhost:port / 隧道域）
  await refreshSessions();
  await refreshOutput();
}
// ---- 移动端：会话目录页 <-> 会话视窗（电脑端保持标签栏不变）----
function applyView() {
  const home = mobile && view === 'home';
  document.getElementById('sesslist').style.display = mobile ? (home ? 'flex' : 'none') : 'none';
  document.querySelector('.wrap').style.display = home ? 'none' : '';
  document.querySelector('.fbar').style.display = home ? '' : '';
  document.getElementById('backbtn').style.display = mobile && !home ? 'inline-flex' : 'none';
  document.getElementById('tabs').style.display = mobile ? 'none' : '';
  // 移动端会话视窗：header 用背景色与会话区区分；只留返回 + 会话名（不需要 logo/brand）
  document.getElementById('curtitle').style.display = mobile && !home ? '' : 'none';
  document.getElementById('brand').style.display = mobile ? (home ? '' : 'none') : '';
  document.querySelector('.cur').style.display = mobile ? (home ? '' : 'none') : '';
  // ☰ 菜单：桌面端主界面 + 手机端目录页显示（会话跳转入口）；手机端会话视窗不显示
  document.getElementById('setbtn').style.display = mobile && !home ? 'none' : 'inline-flex';
  // 手机端会话视窗：会话操作三点按钮移到 header 右侧；footer 的三点菜单隐藏（只留进度条）
  document.getElementById('hmbtn').style.display = mobile && !home ? 'inline-flex' : 'none';
  document.getElementById('menu').style.display = mobile && !home ? 'none' : '';
  // 移动端底边栏分工：目录页只显示 Server；会话页显示进度条 + status/script（分行）
  if (mobile) {
    document.getElementById('pmeta-srv').style.display = home ? '' : 'none';
    document.getElementById('pmeta-ses').style.display = home ? 'none' : '';
    // 进度条显隐交给 setProgress（_progVisible）；会话页空闲时保持隐藏，不因轮询闪现
    document.querySelector('.prow').style.display = home ? 'none' : (_progVisible ? '' : 'none');
    document.querySelector('.pbar').style.display = home ? 'none' : (_progVisible ? '' : 'none');
  } else {
    document.getElementById('pmeta-srv').style.display = '';
    document.getElementById('pmeta-ses').style.display = '';
    document.querySelector('.prow').style.display = _progVisible ? '' : 'none';
    document.querySelector('.pbar').style.display = _progVisible ? '' : 'none';
  }
}
function renderSessList(names) {
  const host = document.getElementById('sesslist');
  host.innerHTML = '';
  const add = document.createElement('button'); add.className = 'sess-item new'; add.textContent = '+  NEW SESSION';
  add.onclick = newSessionModal;
  host.appendChild(add);
  if (!names.length) {
    const e = document.createElement('div'); e.className = 'sess-empty'; e.textContent = 'NO SESSION';
    host.appendChild(e); applyView(); return;
  }
  names.forEach(n => {
    const s = sessionsData[n];
    const it = document.createElement('button');
    it.className = 'sess-item' + (n === current ? ' act' : '');
    it.innerHTML = '<span class="si-line"><span class="si-name">' + esc(n) + '</span>' +
      (s.busy ? '<span class="si-busy">running</span>' : '<span class="si-idle">idle</span>') + '</span>' +
      '<span class="si-cmd">' + esc(s.cmd || '') + '</span>';
    it.onclick = () => openSession(n);
    host.appendChild(it);
  });
  applyView();
}
function openSession(name) {
  current = name; view = 'session';
  refreshAll(); document.getElementById('in').focus();
}
mqMobile.addEventListener('change', e => {
  mobile = e.matches;
  document.body.classList.toggle('mobile', mobile);
  setClientTag();
  view = mobile ? 'home' : 'session';
  refreshAll();
});
// ---- 设置面板（电脑端/手机端通用）：批量会话管理 + 字体大小 + 主题色 + About ----
function getFs(){ return parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--fs')) || 13; }
function setFs(v){ document.documentElement.style.setProperty('--fs', v + 'px'); try{ localStorage.setItem('qt_fs', String(v)); }catch(e){} }
function currentTheme(){ try{ return localStorage.getItem('qt_theme') || '#a78bfa'; }catch(e){ return '#a78bfa'; } }
function toggleDropdown() {
  const d = document.getElementById('dropdown');
  d.style.display = d.style.display !== 'block' ? 'block' : 'none';
}
function closeDropdown() { document.getElementById('dropdown').style.display = 'none'; }
function openFullPage(title, html) {
  document.getElementById('fptitle').textContent = title;
  document.getElementById('fpbody').innerHTML = html;
  document.getElementById('fullpage').style.display = 'flex';
}
function closeFullPage() { document.getElementById('fullpage').style.display = 'none'; }
function openSessionManager() {
  const names = Object.keys(sessionsData || {});
  const rows = names.length
    ? names.map(n => '<div class="set-sess"><span class="ss-name">' + esc(n) + '</span><button class="set-mini del" data-del="' + esc(n) + '">delete</button></div>').join('')
    : '<div class="set-empty">no sessions</div>';
  openFullPage('MANAGE SESSIONS',
    '<div id="set-sesslist">' + rows + '</div>' +
    '<button class="menu-opt" id="sm-new" style="border-style:dashed;justify-content:center">+ NEW SESSION</button>');
  document.querySelectorAll('#set-sesslist [data-del]').forEach(bn => {
    bn.onclick = async () => {
      const name = bn.dataset.del;
      await api('/api/sessions/' + name, {method:'DELETE'});
      delete hist[name]; delete histIdx[name];
      if (current === name) current = null;
      refreshAll(); openSessionManager();
    };
  });
  document.getElementById('sm-new').onclick = newSessionModal;  // overlay z-index 已提到 70，弹窗显示在管理页之上，无需关 fullpage
}
function openMoreSettings() {
  const themes = ['#a78bfa', '#00e0a0', '#58a6ff', '#f0883e', '#ff7b72'];
  openFullPage('MORE SETTINGS',
    '<div class="set-sec"><div class="set-h">FONT SIZE</div><div class="set-row"><button class="set-mini" id="fs-dec">A-</button><span class="set-val" id="fs-val"></span><button class="set-mini" id="fs-inc">A+</button></div></div>' +
    '<div class="set-sec"><div class="set-h">THEME</div><div class="set-row" id="set-themes">' +
      themes.map(c => '<button class="swatch' + (currentTheme() === c ? ' on' : '') + '" data-c="' + c + '" style="background:' + c + '"></button>').join('') +
    '</div></div>' +
    '<div class="set-sec"><div class="set-h">ABOUT</div><div class="set-about">' + QT_ABOUT.summary + '<br>' + QT_ABOUT.version + '<br>docs: ' + esc(QT_ABOUT.readme) + '</div></div>');
  const fsv = document.getElementById('fs-val');
  const upd = () => { fsv.textContent = getFs() + 'px'; };
  upd();
  document.getElementById('fs-inc').onclick = () => { setFs(Math.min(22, getFs() + 1)); upd(); };
  document.getElementById('fs-dec').onclick = () => { setFs(Math.max(9, getFs() - 1)); upd(); };
  document.querySelectorAll('#set-themes .swatch').forEach(sw => {
    sw.onclick = () => {
      document.documentElement.style.setProperty('--acc', sw.dataset.c);
      try{ localStorage.setItem('qt_theme', sw.dataset.c); }catch(e){}
      document.querySelectorAll('#set-themes .swatch').forEach(x => x.classList.toggle('on', x === sw));
    };
  });
}
// ---- 远程面板（电脑端）：二维码 + URL/token + 配对提示 ----
function drawQR(canvas, mat) {
  const n = mat.length, s = 8, size = n * s;
  canvas.width = size; canvas.height = size;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, size, size);
  ctx.fillStyle = '#000';
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (mat[y][x]) ctx.fillRect(x * s, y * s, s, s);
}
async function showRemotePanel() {
  openModal('REMOTE', '<div id="rpanel" style="min-height:60px;text-align:center">starting tunnel ...</div>', 'OK', () => true);
  const tick = async () => {
    const el = document.getElementById('rpanel');
    if (!el) return;
    const st = await api('/api/remote/status');
    if (st.status === 'error') { el.textContent = 'remote error: ' + (st.error || '?'); return; }
    if (st.status === 'off') { el.textContent = 'remote tunnel closed'; return; }
    if (st.status !== 'on' || !st.url) { setTimeout(tick, 1500); return; }
    const q = await api('/api/remote/qrcode');
    if (!q.ok) {
      el.innerHTML = '<p class="mmsg">' + (st.url || '') + '</p><p class="mmsg">one-time password: <b>' + (st.token || '') + '</b></p><p class="mmsg" style="font-size:11px;opacity:.6">(install qrcode: pip install qrcode)</p>';
      return;
    }
    el.innerHTML = '<canvas id="qrcv" style="background:#fff;padding:8px;border-radius:6px"></canvas>' +
      '<p class="mmsg" style="font-size:11px;word-break:break-all;opacity:.7">' + q.url + '</p>' +
      '<p class="mmsg">one-time password: <b>' + q.token + '</b></p>' +
      '<p class="mmsg" style="font-size:11px;opacity:.6">Scan the QR (URL only) in the Termetron App, type the one-time password shown above; the phone shows a 4-digit code - enter it on this computer to pair.</p>';
    drawQR(document.getElementById('qrcv'), q.matrix);
  };
  tick();
}
// 远程认证（手机端）：检测未认证 -> 遮罩输一次性密码 -> 配对 -> 放行后 reload
// ---- 隧道守护 + 返回连接页（App 内导航回 Capacitor 本地服务器，allowNavigation 需含 localhost）----
function goBackToLogin() {
  document.onkeydown = null;
  document.getElementById('cmask').style.display = 'none';
  const cap = window.Capacitor;
  const isApp = !!(cap && cap.isNativePlatform && cap.isNativePlatform());
  // App 隧道页可能无 Capacitor bridge（bridge 通常只注入本地服务器页面）——
  // 移动设备一律尝试导航回本地连接页（allowNavigation 含 localhost，WebView 内放行）
  if (isApp || /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent)) {
    // 带 ?t=back 返回：连接页据此跳过 splash，避免“重启应用”的观感
    window.location.assign('https://localhost/?t=back');
    return;
  }
  // 桌面浏览器：无连接登录页概念，关闭遮罩留在原处
}
function setCmaskChecking() {
  document.getElementById('ctitle').textContent = 'CHECKING TUNNEL';
  document.getElementById('cbody').innerHTML = '<p class="mmsg">Checking tunnel status ...</p>';
  document.getElementById('cback').style.display = 'none';
}
function showTunnelClosed() {
  const mask = document.getElementById('cmask');
  mask.style.display = 'flex';
  document.getElementById('ctitle').textContent = 'TUNNEL CLOSED';
  // 电脑端（本机）可重开新隧道换新 URL；移动端/远程只能回登录重新连接（不自动连新 tunnel）
  const localDesktop = !mobile && window.__termetronLocal;
  document.getElementById('cbody').innerHTML =
    '<p class="mmsg">The tunnel is closed or no longer available.</p>' +
    '<p class="mmsg" style="font-size:11px;opacity:.6">' +
    (localDesktop
      ? 'Restart to get a fresh tunnel URL & one-time password, or turn the tunnel off.'
      : 'The tunnel on the computer is not running. Run <b>termetron remote on</b> on the computer, then reconnect from the login page.') +
    '</p>';
  document.getElementById('cback').style.display = '';
  document.getElementById('cback').onclick = goBackToLogin;
  const cnew = document.getElementById('cnew');
  cnew.style.display = localDesktop ? '' : 'none';
  cnew.onclick = async () => {
    cnew.disabled = true;
    document.getElementById('cbody').innerHTML =
      '<p class="mmsg">Restarting tunnel, please wait ...</p>';
    try {
      const r = await api('/api/remote/restart', {method:'POST'});
      if (r && r.url) {
        mask.style.display = 'none';
        document.onkeydown = null;
        if (window.__extReq) { window.__extReq('openExternal', { url: r.url }); }
        else { window.open(r.url, '_blank'); }
      } else {
        document.getElementById('cbody').innerHTML =
          '<p class="mmsg">Restart failed: ' + (r && r.error ? r.error : 'unknown') + '</p>';
      }
    } catch (e) {
      document.getElementById('cbody').innerHTML = '<p class="mmsg">Restart failed: ' + e + '</p>';
    }
    cnew.disabled = false;
  };
  document.onkeydown = (e) => { if (e.key === 'Escape') { mask.style.display = 'none'; document.onkeydown = null; } };
}
// 进入门控：远程（隧道）访问先显示检测遮罩（CHECKING TUNNEL），检测通过才展示会话/认证——
// 避免“先露会话目录再盖遮罩”的闪烁；本地桌面访问不遮罩。
// 刚 remote on 时隧道是 starting/URL 未就绪，须重试等待；仅明确 off/error 才显示关闭。
async function initGate() {
  const remote = !window.__termetronLocal && !['127.0.0.1', 'localhost'].includes(location.hostname);
  const cm = document.getElementById('cmask');
  if (remote) {
    document.body.classList.add('tunnel');   // 隐藏会话内容区，等待检测
    cm.style.display = 'flex';
    setCmaskChecking();
  }
  if (!remote) return;                       // 本地桌面：隧道状态无关，正常使用
  for (let i = 0; i < 10; i++) {
    let st = null;
    try { st = await api('/api/remote/status'); } catch (e) { st = null; }
    if (st && st.status === 'on') {
      document.body.classList.remove('tunnel');
      cm.style.display = 'none';
      if (st.auth_required) initAuth();
      return;
    }
    if (st && (st.status === 'off' || st.status === 'error')) break;  // 明确关闭/错误
    await new Promise(r => setTimeout(r, 1500));                      // starting/请求失败：等待重试
  }
  showTunnelClosed();
}
// 页面级检测：进入/会话中隧道不可用（关闭/失效/请求失败）→ 先探测，不直接弹密码框，也不泄露会话内容
async function tunnelGuard() {
  const mask = document.getElementById('cmask');
  const check = async () => {
    if (mask.style.display === 'flex') return;
    if (document.getElementById('rmask').style.display === 'flex') return; // 认证遮罩自行处理
    // 仅远程（隧道）访问需要隧道检测；本地桌面直接访问（127.0.0.1/localhost/__termetronLocal）不受影响
    if (window.__termetronLocal || ['127.0.0.1', 'localhost'].includes(location.hostname)) return;
    let st = null;
    try { st = await api('/api/remote/status'); } catch (e) {}
    if (st && st.status === 'starting') return;  // 隧道启动中：不误报关闭
    if (!st || st.status === 'off' || st.status === 'error') showTunnelClosed();
  };
  check();
  setInterval(check, 3000);
}

// ---- 设备返回键（原生触发，隧道页无 Capacitor bridge 时）：按页面层级返回 ----
// 更多设置/管理会话(fullpage) → 会话目录页；会话视窗 → 目录页；目录页(主页) → 连接登录页
window.__handleBack = () => {
  const fp = document.getElementById('fullpage');
  if (fp && fp.style.display === 'flex') { closeFullPage(); view = 'home'; refreshAll(); return; }
  if (mobile && view === 'session') { view = 'home'; refreshAll(); return; }
  if (mobile && view === 'home') { goBackToLogin(); return; }
};

async function initAuth() {
  const mask = document.getElementById('rmask');
  mask.style.display = 'flex';
  // 键盘优先（终端无鼠标）：Esc 关闭认证遮罩；Enter 已在 rtok 输入框绑定为 connect
  document.onkeydown = (e) => { if (e.key === 'Escape') { mask.style.display = 'none'; document.onkeydown = null; } };
  const tok = document.getElementById('rtok');
  // 安全：一次性密码必须手动输入（二维码只含 URL 不含密码，密码面对面从桌面屏幕读取）
  const err = document.getElementById('rerr');
  // Back：给不出密码可随时返回——App 端回连接登录页，桌面端/浏览器关闭遮罩
  document.getElementById('rback').onclick = () => {
    mask.style.display = 'none';
    goBackToLogin();
  };
  // 认证/配对等待期间监控隧道状态：电脑端 remote off 时明确提示（不再无响应卡住）
  const stPoll = setInterval(async () => {
    let s = null;
    try { s = await api('/api/remote/status'); } catch (e) {}
    if (!s || s.status !== 'on') {
      clearInterval(stPoll);
      err.style.display = 'block';
      err.textContent = 'Tunnel is closed on the computer. Go Back or run "termetron remote on" again.';
    }
  }, 3000);
  const conn = () => {
    const saved = localStorage.getItem('qt_dk') || '';
    const key = /^\d{4}$/.test(saved) ? saved : String(Math.floor(1000 + Math.random() * 9000));
    localStorage.setItem('qt_dk', key);
    api('/api/remote/auth', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({token: tok.value.trim(), device_key: key})})
      .then(r => {
        if (r.error) { err.style.display = 'block'; err.textContent = 'auth failed: ' + r.error; return; }
        err.style.display = 'none';
        document.getElementById('rpair').style.display = 'block';
        document.getElementById('rkey').textContent = key;
        const poll = setInterval(async () => {
          const p = await api('/api/remote/pairstatus?key=' + key);
          if (p.allowed) { clearInterval(poll); location.reload(); }
        }, 2000);
      });
  };
  document.getElementById('rconn').onclick = conn;
  tok.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); conn(); } });
  tok.focus();
}
// PWA：图标 + manifest（添加到主屏幕 / 套壳基础）
function initPWA() {
  const icon = 'data:image/svg+xml;utf8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#0b0f14"/><rect x="4" y="5.3" width="56" height="53.3" rx="10.7" fill="none" stroke="#a78bfa" style="stroke-width:4.3"/><text x="9.3" y="34.7" font-family="monospace" font-size="31" font-weight="700" fill="#a78bfa">>_</text></svg>');
  const fav = document.createElement('link'); fav.rel = 'icon'; fav.href = icon; document.head.appendChild(fav);
  const man = {name:'Termetron', short_name:'TMT', start_url:'.', display:'standalone',
    background_color:'#0b0f14', theme_color:'#0b0f14',
    icons:[{src:icon, sizes:'any', type:'image/svg+xml'}]};
  const blob = new Blob([JSON.stringify(man)], {type:'application/manifest+json'});
  const l = document.createElement('link'); l.rel = 'manifest'; l.href = URL.createObjectURL(blob);
  document.head.appendChild(l);
}
setInterval(refreshAll, 1000);
refreshAll();
initGate();
tunnelGuard();
initPWA();

// 连接按钮：点击 = 运行 `termetron remote on`（打开隧道 + 显示二维码/密码面板）
document.getElementById('connbtn').onclick = async () => {
  const r = await api('/api/remote/start', {method:'POST'});
  if (r.error) showMsg('remote error: ' + r.error); else showRemotePanel();
};

// 扩展内嵌（iframe 壳）专属菜单：打开系统浏览器 / 切换本地服务器；普通浏览器/App 隐藏
function openInBrowser() {
  const url = location.href;
  if (window.parent !== window) {
    try {
      window.parent.postMessage({ kind: 'termetron:openExternal', url }, '*');
      return;
    } catch (e) { /* fall through */ }
  }
  window.open(url, '_blank');
}
// ---- 扩展请求桥（面板内 iframe ↔ 扩展）：服务器管理 ----------------
let __extSeq = 0;
const __extPending = {};
window.__extReq = (cmd, payload) => new Promise((resolve) => {
  if (window.parent === window) { resolve({ error: 'not in extension' }); return; }
  const id = ++__extSeq;
  __extPending[id] = resolve;
  window.parent.postMessage({ kind: 'termetron:req', id, cmd, payload }, '*');
  setTimeout(() => { if (__extPending[id]) { delete __extPending[id]; resolve({ error: 'timeout' }); } }, 10000);
});
window.addEventListener('message', (e) => {
  const d = e.data;
  if (d && d.__reqResp && __extPending[d.id]) {
    const r = __extPending[d.id]; delete __extPending[d.id]; r(d.data);
  }
});

// 面板内服务器管理：列出 / 选择 / 创建 / 删除（经扩展，不弹 VS Code 外部选择器）
async function manageServers() {
  const res = await window.__extReq('listServers');
  const servers = res && Array.isArray(res) ? res : [];
  const cur = Number(location.port) || 0;
  const rows = servers.map((s) => {
    const isCur = s.port === cur;
    return '<div class="srow' + (isCur ? ' cur' : '') + '">' +
      '<span class="srv-p">:' + s.port + '</span>' +
      '<span class="srv-m">' + s.sessions.length + ' sess' + (s.own ? ' · ext' : ' · external') + (isCur ? ' · CURRENT' : '') + '</span>' +
      '<span class="srv-acts">' +
      (isCur ? '' : '<button class="sbtn" data-act="sel" data-p="' + s.port + '">select</button>') +
      (s.own ? '<button class="sbtn del" data-act="del" data-p="' + s.port + '">delete</button>' : '') +
      '</span></div>';
  }).join('') || '<p class="mmsg" style="opacity:.6">no servers found</p>';
  openModal('MANAGE SERVERS',
    rows + '<button class="sbtn full" id="srv-new">+ start new server</button>' +
    '<p class="merr" id="srv-err" style="display:none"></p>',
    'CLOSE', () => true);
  const err = document.getElementById('srv-err');
  const fail = (m) => { err.style.display = 'block'; err.textContent = m; };
  document.querySelectorAll('#mbody [data-act]').forEach((b) => {
    b.onclick = async () => {
      const p = Number(b.dataset.p);
      const r = await window.__extReq(b.dataset.act === 'sel' ? 'connectServer' : 'stopServer', { port: p });
      if (r && r.error) { fail(r.error); return; }
      if (b.dataset.act === 'del') { closeModal(); manageServers(); }  // 删除后刷新
      // select 成功后面板重建（前端重载），无需再处理
    };
  });
  const nb = document.getElementById('srv-new');
  if (nb) nb.onclick = async () => {
    const r = await window.__extReq('startServer');
    if (r && r.error) { fail(r.error); return; }
    if (r && r.port) { closeModal(); await window.__extReq('connectServer', { port: r.port }); }
  };
}
(function () {
  // 扩展内嵌（iframe 壳）或显式 ?ext=1（调试/预览）才显示扩展专属菜单项
  const inExt = window.parent !== window || new URLSearchParams(location.search).get('ext') === '1';
  for (const id of ['dd-browser', 'dd-switch']) {
    const el = document.getElementById(id);
    if (el) el.style.display = inExt ? '' : 'none';
  }
  document.getElementById('dd-browser').onclick = () => { closeDropdown(); openInBrowser(); };
  document.getElementById('dd-switch').onclick = () => { closeDropdown(); manageServers(); };
})();

// ---- 配对审批（桌面端）：手机请求配对 -> 弹窗让电脑输入手机屏幕上的 4 位配对码 ----
let pairPromptShown = false;
async function checkPending() {
  let st;
  try { st = await api('/api/remote/status'); } catch (e) { return; }
  if (!st || st.status !== 'on') return;
  // 仅本机桌面弹审批（手机/远程访问不弹）；webview 内嵌（__termetronLocal）等同本机
  if (!window.__termetronLocal && !['127.0.0.1', 'localhost'].includes(location.hostname)) return;
  if (!st.pending || st.pending.length === 0) { pairPromptShown = false; return; }
  if (pairPromptShown) return;
  pairPromptShown = true;
  showPairRequest();
}
function showPairRequest() {
  openModal('PAIRING REQUEST',
    '<p class="mmsg">A phone wants to pair with this terminal.</p>' +
    '<p class="mmsg">Enter the 4-digit pairing code shown on the phone screen.</p>' +
    '<div class="field"><label>pairing code</label>' +
    '<input id="rpairin" spellcheck="false" autocomplete="off" inputmode="numeric" pattern="[0-9]*" maxlength="4" placeholder="\u2022 \u2022 \u2022 \u2022"></div>' +
    '<p class="merr" id="rpairerr" style="display:none"></p>',
    'PAIR', async () => {
      const input = document.getElementById('rpairin');
      const err = document.getElementById('rpairerr');
      const code = (input ? input.value : '').trim();
      if (!/^\d{4}$/.test(code)) {
        err.style.display = 'block'; err.textContent = 'Enter the 4-digit code shown on the phone.';
        return false;
      }
      const r = await api('/api/remote/allow', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({key: code})});
      if (r.error) {
        err.style.display = 'block'; err.textContent = r.error;
        return false;
      }
      return true;
    });
  document.getElementById('mcancel').textContent = 'CLOSE';
}
setInterval(checkPending, 1500);

// ---- 手机 App 返回键（Capacitor WebView）：移动端按返回 = 回到连接登录页（离开隧道），不再停留会话目录 ----
try {
  const cap = window.Capacitor;
  if (cap && cap.Plugins && cap.Plugins.App) {
    cap.Plugins.App.addListener('backButton', () => {
      if (mobile) goBackToLogin();
    });
  }
} catch (e) {}
</script></body></html>"""


class RemoteMgr:
    """公网隧道（cloudflared trycloudflare）+ 一次性密码 + 设备配对。

    - `termetron remote on`   下载/启动 cloudflared 临时隧道，生成一次性密码 token；
    - 手机访问 URL，输一次性密码，展示随机设备密钥请求配对；
    - 电脑端 `termetron allow <key>` 人工放行 -> 设备进入 allowed；
    - `termetron remote off`  关闭隧道，URL/密码/配对全部失效（临时性）。
    """

    def __init__(self):
        self.proc = None            # cloudflared 子进程
        self.url: str | None = None
        self.token: str | None = None
        self.status = "off"         # off / starting / on / error
        self.error: str | None = None
        self.pending: dict = {}     # device_key -> created_at
        self.allowed: set = set()   # 已放行 device_key
        self.lock = threading.Lock()
        self._probe_fail = 0        # 连续探测失败次数（隧道活跃性 watchdog）

    @property
    def on(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _bin_dir(self) -> str:
        # 优先随文件部署的 bin/（手动部署 lib/termetron/bin 已在，直接用）；
        # 扩展服务器（vsix 打包只含 out/server/，无 bin/）回退到用户缓存目录，
        # 让 cloudflared 跨实例/跨版本复用 —— 否则每次更新扩展都会重新下载 ~50MB，
        # 导致 remote on 慢 + 隧道迟迟未就绪（手机端报 TUNNEL CLOSED）。
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
        if os.path.isdir(local):
            return local
        cache = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                             "Termetron", "bin")
        try:
            os.makedirs(cache, exist_ok=True)
        except OSError:
            cache = local
            os.makedirs(cache, exist_ok=True)
        return cache

    def _bin_path(self) -> str:
        sysname = platform.system()
        machine = platform.machine().lower()
        if sysname == "Windows":
            return os.path.join(self._bin_dir(), "cloudflared.exe")
        if sysname == "Darwin":
            arch = "arm64" if "arm" in machine else "amd64"
            return os.path.join(self._bin_dir(), f"cloudflared-darwin-{arch}")
        arch = "arm64" if "arm" in machine else "amd64"
        return os.path.join(self._bin_dir(), f"cloudflared-linux-{arch}")

    def _download_bin(self, binp: str) -> None:
        sysname = platform.system()
        machine = platform.machine().lower()
        if sysname == "Windows":
            name = "cloudflared-windows-amd64.exe"
        elif sysname == "Darwin":
            name = "cloudflared-darwin-arm64" if "arm" in machine else "cloudflared-darwin-amd64"
        else:
            name = "cloudflared-linux-arm64" if "arm" in machine else "cloudflared-linux-amd64"
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/" + name
        req = urllib.request.Request(url, headers={"User-Agent": "termetron"})
        with urllib.request.urlopen(req, timeout=120) as r, open(binp, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
        if sysname != "Windows":
            os.chmod(binp, 0o755)

    def start(self, local_port: int) -> dict:
        with self.lock:
            if self.on:
                return {"ok": True, "url": self.url, "token": self.token, "status": self.status}
            binp = self._bin_path()
            if not os.path.exists(binp):
                self.status = "starting"
                try:
                    self._download_bin(binp)
                except Exception as e:  # noqa: BLE001
                    self.status = "error"
                    self.error = f"cloudflared download failed: {e}"
                    return {"ok": False, "error": self.error}
            self.token = secrets.token_hex(3)   # 6 位一次性密码
            self.url = None
            self.status = "starting"
            self.pending = {}
            self.allowed = set()
            self.error = None
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            try:
                self.proc = subprocess.Popen(
                    [binp, "tunnel", "--url", f"http://127.0.0.1:{local_port}", "--no-autoupdate"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    creationflags=flags)
            except Exception as e:  # noqa: BLE001
                self.status = "error"
                self.error = f"cloudflared start failed: {e}"
                return {"ok": False, "error": self.error}
            self._probe_fail = 0
            threading.Thread(target=self._read_url, daemon=True).start()
            threading.Thread(target=self._watchdog_loop, daemon=True).start()
            return {"ok": True, "token": self.token, "status": "starting"}

    def _read_url(self) -> None:
        """从 cloudflared stdout 解析 trycloudflare URL。"""
        try:
            for line in self.proc.stdout:  # type: ignore[union-attr]
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m:
                    with self.lock:
                        self.url = m.group(0)
                        self.status = "on"
                    return
        except Exception:  # noqa: BLE001
            pass

    def _probe_alive(self) -> bool:
        """探测隧道是否真的可达：GET https://<url>/api/remote/status 能通即活着。"""
        if not self.url:
            return False
        try:
            req = urllib.request.Request(
                self.url + "/api/remote/status",
                headers={"User-Agent": "termetron-watchdog"})
            with urllib.request.urlopen(req, timeout=6) as r:
                r.read(256)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _watchdog_loop(self) -> None:
        """隧道活跃性看门狗：每 30s 探测；连续 3 次失败判隧道失效。

        cloudflared 进程活着不代表隧道可用（边缘断开/超时/DNS 失败时进程
        仍在但 URL 失效，手机连不上）。探测失败 -> status=error，前端据此
        提示用户重新 remote on。
        """
        while True:
            time.sleep(30)
            with self.lock:
                if self.status != "on" or not self.url:
                    self._probe_fail = 0
                    continue
                url = self.url
            alive = self._probe_alive()
            with self.lock:
                if alive:
                    self._probe_fail = 0
                else:
                    self._probe_fail += 1
                    if self._probe_fail >= 3:
                        dead = self.proc is not None and self.proc.poll() is not None
                        self.status = "off" if dead else "error"
                        self.error = (f"tunnel lost: probes failed via {url}"
                                      if not dead else "tunnel process exited")
                        self._probe_fail = 0

    def stop(self) -> None:
        with self.lock:
            if self.proc is not None:
                try:
                    self.proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
                self.proc = None
            self.url = None
            self.token = None
            self.status = "off"
            self.error = None
            self.pending = {}
            self.allowed = set()
            self._probe_fail = 0

    def status_json(self) -> dict:
        with self.lock:
            return {"status": self.status, "url": self.url, "token": self.token,
                    "pending": sorted(self.pending), "allowed": len(self.allowed),
                    "error": self.error}

    def request_pair(self, device_key: str) -> dict:
        with self.lock:
            if self.status != "on":
                return {"ok": False, "error": "remote not on"}
            if device_key in self.allowed:
                return {"ok": True, "allowed": True}
            self.pending.setdefault(device_key, time.time())
            return {"ok": True, "pending": True}

    def allow(self, device_key: str) -> dict:
        with self.lock:
            if device_key in self.pending:
                self.allowed.add(device_key)
                del self.pending[device_key]
                return {"ok": True}
            return {"ok": False, "error": "no pending device: " + device_key}

    def deny(self, device_key: str) -> dict:
        """拒绝配对：从 pending 移除该设备（不进入 allowed）。"""
        with self.lock:
            if device_key in self.pending:
                del self.pending[device_key]
            return {"ok": True}


REMOTE = RemoteMgr()
_LOCAL_PORT = 8899


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str,
              extra_headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        # CORS：允许 App（Capacitor WebView，跨域）探测隧道可达性（/api/remote/status）
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _query(self, name: str) -> str:
        if "?" not in self.path:
            return ""
        q = self.path.split("?", 1)[1]
        m = re.search(r"(?:^|&)" + re.escape(name) + r"=([^&]*)", q)
        return m.group(1) if m else ""

    def _remote(self) -> bool:
        """是否远程请求（经公网隧道）。cloudflared 转发到本地时源 IP 是 127.0.0.1，
        但会附加 Cf-Connecting-Ip / X-Forwarded-For 头——用它区分本地/远程。"""
        if self.headers.get("Cf-Connecting-Ip") or self.headers.get("X-Forwarded-For"):
            return True
        ip = self.client_address[0]
        return ip not in ("127.0.0.1", "::1", "localhost")

    def _authed(self) -> bool:
        """本地免认证；远程需 有效一次性密码 + 设备已配对（cookie qt_auth=token:key）。"""
        if not self._remote():
            return True
        if REMOTE.status != "on" or not REMOTE.token:
            return False
        cookie = self.headers.get("Cookie") or ""
        m = re.search(r"qt_auth=([^;]+)", cookie)
        if m:
            parts = m.group(1).split(":", 1)
            if len(parts) == 2 and parts[0] == REMOTE.token and parts[1] in REMOTE.allowed:
                return True
        return False

    def do_OPTIONS(self) -> None:  # noqa: N802
        # CORS 预检：App（Capacitor WebView）跨域 fetch 带非 safelisted 请求头
        # （如 cache:'no-store' → Cache-Control）时会先发 OPTIONS；不应答会返回
        # 501 导致跨域请求被浏览器/WebView 拦截（App 探测隧道失败报 tunnel closed）
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            html = _INDEX.replace("__QT_ABOUT__", json.dumps(_QT_ABOUT))
            self._send(200, html.encode(), "text/html; charset=utf-8")
        elif path == "/api/remote/status":
            st = REMOTE.status_json()
            st["remote"] = self._remote()
            st["authenticated"] = self._authed()
            st["auth_required"] = self._remote() and not self._authed()
            self._json(st)
        elif path == "/api/remote/pairstatus":
            key = self._query("key")
            if REMOTE.status == "on" and key and key in REMOTE.allowed:
                cookie = f"qt_auth={REMOTE.token}:{key}; Path=/; Max-Age=2592000; HttpOnly"
                self._send(200, json.dumps({"allowed": True}).encode(),
                           "application/json", extra_headers={"Set-Cookie": cookie})
            else:
                self._json({"allowed": False})
        elif path == "/api/remote/qrcode":
            if not self._authed():
                self._json({"error": "auth required", "auth_required": True}, 401)
                return
            if REMOTE.status != "on" or not REMOTE.url or not REMOTE.token:
                self._json({"ok": False, "error": "remote not on"}, 400)
                return
            try:
                import qrcode  # 可选依赖：无则前端降级为文本输入
            except Exception:  # noqa: BLE001
                self._json({"ok": False, "error": "qrcode not installed"}, 400)
                return
            # 安全：二维码只含 URL，不含一次性密码（密码单独在屏幕上显示、面对面输入）
            # ——否则扫码即得密码，一次性密码保护形同虚设（二维码可能被截图转发）
            payload = REMOTE.url
            qr = qrcode.QRCode(border=1, box_size=1)
            qr.add_data(payload)
            qr.make(fit=True)
            self._json({"ok": True, "matrix": qr.get_matrix(),
                        "payload": payload, "url": REMOTE.url, "token": REMOTE.token})
        elif path.startswith("/api/"):
            if not self._authed():
                self._json({"error": "auth required", "auth_required": True}, 401)
                return
            if path == "/api/sessions":
                self._json({
                    n: {"status": "done" if s.done else "running", "cmd": s.cmd,
                        "progress": s.prog, "busy": s.busy, "script": s.script}
                    for n, s in SESSIONS.items()
                })
            elif path.startswith("/api/output/"):
                name = path.rsplit("/", 1)[-1]
                s = SESSIONS.get(name)
                if s:
                    self._json(s.snapshot())
                else:
                    self._json({"error": "not found"}, 404)
            else:
                self._json({"error": "not found"}, 404)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        # ---- 远程（公网隧道）----
        if self.path == "/api/remote/start":
            if not self._authed():
                self._json({"error": "auth required", "auth_required": True}, 401)
                return
            self._json(REMOTE.start(_LOCAL_PORT))
            return
        if self.path == "/api/remote/stop":
            if not self._authed():
                self._json({"error": "auth required", "auth_required": True}, 401)
                return
            REMOTE.stop()
            self._json({"ok": True})
            return
        if self.path == "/api/remote/restart":
            # 隧道失效后重开：停旧隧道 → 开新隧道（新 URL + 新一次性密码）
            if not self._authed():
                self._json({"error": "auth required", "auth_required": True}, 401)
                return
            REMOTE.stop()
            time.sleep(0.5)
            self._json(REMOTE.start(_LOCAL_PORT))
            return
        if self.path == "/api/remote/auth":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad request"}, 400)
                return
            token = (body.get("token") or "").strip()
            key = (body.get("device_key") or "").strip()
            if REMOTE.status != "on" or token != (REMOTE.token or ""):
                self._json({"ok": False, "error": "invalid one-time password"}, 401)
                return
            if not key:
                self._json({"ok": False, "error": "device key required"}, 400)
                return
            self._json(REMOTE.request_pair(key))
            return
        if self.path == "/api/remote/allow":
            if not self._authed():
                self._json({"error": "auth required", "auth_required": True}, 401)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad request"}, 400)
                return
            self._json(REMOTE.allow((body.get("key") or "").strip()))
            return
        if self.path == "/api/remote/deny":
            if not self._authed():
                self._json({"error": "auth required", "auth_required": True}, 401)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad request"}, 400)
                return
            self._json(REMOTE.deny((body.get("key") or "").strip()))
            return
        # 清屏（cls/clear 在管道模式下无效，由这里真正清空输出）
        if self.path.startswith("/api/sessions/") and self.path.endswith("/clear"):
            name = self.path.split("/")[3]
            s = SESSIONS.get(name)
            if not s:
                self._json({"error": "not found"}, 404)
                return
            s.clear()
            self._json({"ok": True})
        # 输入命令到会话
        elif self.path.startswith("/api/sessions/") and self.path.endswith("/input"):
            name = self.path.split("/")[3]
            s = SESSIONS.get(name)
            if not s:
                self._json({"error": "not found"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad request"}, 400)
                return
            text = (body.get("text") or "").strip()
            if not text:
                self._json({"error": "empty input"}, 400)
                return
            scr = _script_from_cmd(text)
            if scr:
                s.script = scr
            ok = s.send(text)
            self._json({"ok": ok})
        # 中断当前进程（杀进程树并重建 shell）
        elif self.path.startswith("/api/sessions/") and self.path.endswith("/stop"):
            name = self.path.split("/")[3]
            s = SESSIONS.get(name)
            if not s:
                self._json({"error": "not found"}, 404)
                return
            s.stop()
            self._json({"ok": True})
        # 重命名会话
        elif self.path.startswith("/api/sessions/") and self.path.endswith("/rename"):
            old = self.path.split("/")[3]
            s = SESSIONS.get(old)
            if not s:
                self._json({"error": "not found"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad request"}, 400)
                return
            new = (body.get("name") or "").strip()
            if not new:
                self._json({"error": "name required"}, 400)
                return
            if new in SESSIONS:
                self._json({"error": "session exists"}, 409)
                return
            SESSIONS[new] = s
            s.name = new
            del SESSIONS[old]
            self._json({"ok": True, "name": new})
        elif self.path == "/api/sessions":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad request"}, 400)
                return
            name = (body.get("name") or "").strip()
            cmd = (body.get("cmd") or "").strip() or None
            if not name:
                self._json({"error": "name required"}, 400)
                return
            if name in SESSIONS:
                self._json({"error": "session exists"}, 409)
                return
            SESSIONS[name] = Session(name, cmd)
            self._json({"ok": True, "name": name})
        else:
            self._json({"error": "not found"}, 404)

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path.startswith("/api/sessions/"):
            name = self.path.rsplit("/", 1)[-1]
            s = SESSIONS.pop(name, None)
            if s:
                s.stop(rebuild=False)
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, *args):  # noqa: A002
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Termetron local web UI")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    global _LOCAL_PORT
    _LOCAL_PORT = args.port

    # 启动即建一个默认交互 shell 会话，打开浏览器就有可用的终端
    if not SESSIONS:
        try:
            SESSIONS["shell"] = Session("shell", None)
            print("[termetron] created default session 'shell'")
        except Exception as e:  # noqa: BLE001
            print(f"[termetron] warn: cannot create default session: {e}")

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Termetron running at http://{args.host}:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        for s in list(SESSIONS.values()):
            s.stop(rebuild=False)
        print("\nshutdown")


if __name__ == "__main__":
    main()
