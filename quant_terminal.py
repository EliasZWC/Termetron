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

# 会话输出持久化（见 tools/session_log.py）。
# ⚠️ 用 try/except 而不是硬导入：发布包里可能没有 tools/（namespace package
#    在某些打包方式下会丢）。持久化是"加分项"，缺了也要能正常跑。
try:
    from tools.session_log import SessionLog as _SessionLog, remove_session as _remove_session_file
except Exception:  # noqa: BLE001
    try:
        _os_dir = os.path.dirname(os.path.abspath(__file__))
        if _os_dir not in sys.path:
            sys.path.insert(0, _os_dir)
        sys.path.insert(0, os.path.join(_os_dir, 'tools'))
        from session_log import SessionLog as _SessionLog, remove_session as _remove_session_file
    except Exception:  # noqa: BLE001
        class _SessionLog:  # type: ignore[no-redef]
            """持久化不可用时的空实现（接口保持兼容）。"""

            def __init__(self, name: str, directory: str | None = None):
                self.path = ""

            def append(self, lines, *, force: bool = False) -> None:
                return

            def flush(self) -> None:
                return

            def load(self, limit: int = 600) -> list:
                return []

            def truncate(self) -> None:
                return

            def compact(self, keep: int = 600) -> None:
                return

        def _remove_session_file(name: str) -> None:  # type: ignore[misc]
            return


_TQDM_RE = re.compile(r"^(.*?)[:]?\s*(\d+)%\|.*?\|\s*(\d+)/(\d+)")

# ANSI 转义序列（光标移动/清行等）：剥离，避免污染 lines 与 tqdm 解析
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# 只匹配 OSC（操作系统命令）：`\x1b]0;标题\x07` 这类**设置窗口标题**的序列。
# 交互式模式下要保留 CSI（颜色/光标），但 OSC 标题没有显示意义，
# 留着会污染首行（ConPTY 每次切前台进程都会发一条）。
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _strip_osc(s: str) -> str:
    """只剥 OSC 标题等非显示序列，保留颜色/光标（交互式模式用）。"""
    return _OSC_RE.sub("", s)


# 屏幕上"没有视觉位置"的控制序列：清行、光标定位、光标显隐、模式切换。
# 它们描述的是"画在哪"，而不是"画什么" —— 落到行文本里只会变成乱码。
# ⚠️ 不含 `m`（SGR）：颜色是"画什么"，要保留给前端渲染。
# ⚠️ 参数部分要允许 `?`（`\x1b[?25h` 光标显隐就是这种），否则会残留 `?25h`。
_CTRL_RE = re.compile(
    r"\x1b\[[0-9;?<=>]*[ABCDEFGHJKSTfhlnsut]"
    r"|\x1b[=>]"
    r"|\x1b[()][A-Za-z0-9]"
    r"|\x00"
)


def _pending_escape(s: str) -> str:
    r"""返回 `s` 末尾**未完结**的转义序列（可能是空串）。

    转义序列被 read 边界切断时，后半截（如 `[?25h`）会被当成普通文本渲染成
    乱码。把未完结的部分留到下一轮拼接即可。

    三种形态：
      - `\x1b` 单独结尾
      - `\x1b[` + 参数（还没到终止字母）
      - `\x1b]` + OSC 内容（还没到 BEL / ST）
    """
    if not s:
        return ""
    i = s.rfind("\x1b")
    if i < 0:
        return ""
    tail = s[i:]
    # CSI：ESC [ 后跟若干 0-9;:?<=>!\"#$%&'()*+,-./ 直到 0x40-0x7E 的终止符
    if len(tail) >= 2 and tail[1] == "[":
        if re.fullmatch(r"\x1b\[[0-9;:?<=>]*", tail):
            return tail          # 还没遇到终止字母
        return ""
    # OSC：ESC ] ... 直到 BEL 或 ST(ESC \)
    if len(tail) >= 2 and tail[1] == "]":
        if "\x07" not in tail and not tail.endswith("\x1b\\"):
            return tail
        return ""
    # 只有 ESC 还没收到第二个字节
    if len(tail) == 1:
        return tail
    # 其它双字节序列（ESC ( / ESC ) / ESC = 等）—— 若只有 2 字节也算完整
    if len(tail) == 2:
        return tail
    return ""


def _visible_line(s: str) -> str:
    r"""把一段带控制序列的终端输出转成"这一行看起来是什么"。

    做四件事：
      1. `[K`（清行）—— 之后的内容从行首重画，所以取最后一个 `[K` 之后。
      2. `\r` 当回到行首覆盖（`a\rb` → `b`）。
      3. 去掉光标定位/清屏/光标显隐 —— Web 端不逐格定位。
      4. 保留 SGR（`\x1b[..m`），前端据此上色。
    """
    if not s:
        return ""
    s = _strip_osc(s)
    # 归一 CRLF 与行尾单 \r —— 这类 \r 只是"换行的一部分"，不是覆盖指令
    if "\r\n" in s:
        s = s.replace("\r\n", "\n")
    s = s.rstrip("\r")

    # ⚠️ 顺序很关键：**先**把 ANSI 控制序列全部剥掉，**再**处理 `\r` 覆盖。
    #    反过来的话，`\r` 覆盖会把"前一段的 ANSI"和"后一段的文本"拼在一起，
    #    留下 `\x1b[?25h` 这种半截序列（实测就得到 `>>> prin?25h`）。
    #    `\x1b[K` 是「清除光标到行尾」，光标通常停在行尾 → 直接删掉。
    while "\x1b[K" in s:
        s = s.replace("\x1b[K", "")
    s = _CTRL_RE.sub("", s)

    # \r 覆盖：逐段叠加，后一段盖住前一段的同位置部分
    if "\r" in s:
        out = ""
        for seg in s.split("\r"):
            out = seg + out[len(seg):]
        s = out
    return s.rstrip("\x00").rstrip()


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)
# 匹配 Windows cmd 提示符（如 E:\code\quant>）：行出现即 shell 空闲
_PROMPT_RE = re.compile(r"^[A-Za-z]:\\.*> *$")
# 从提示符行提取当前目录：`E:\code\quant>` → `E:\code\quant`
# ⚠️ 用贪婪 `.*` 匹配到**最后一个** `>` 前，而不是 `[^>]*?`：
#    目录名本身可以含 `>`（如 D:\a>b\c），非贪婪会在第一个 `>` 处截断。
_PROMPT_CWD_RE = re.compile(r"^([A-Za-z]:\\.*)>+\s*$")
# 命令回显行 `E:\code\quant>cmd`：提取命令，改写成 termetron 提示符风格 `$ cmd`
_CMD_ECHO_RE = re.compile(r"^[A-Za-z]:\\.*>\s*(.*)$")
# 行尾出现 cmd 提示符（`...输出E:\code\quant>`）。
# 用于判断 shell 是否已回到待输入状态：`ping` 这类程序最后一行不带换行，
# 提示符会**粘在输出尾部**，用整行匹配 `^...>$` 是匹配不到的。
_PROMPT_TAIL_RE = re.compile(r"[A-Za-z]:\\[^>]*>\s*$")


def _cwd_from_prompt(line: str) -> str | None:
    """从 cmd 提示符行取当前工作目录。

    cmd.exe 的提示符就是 cwd（如 `E:\code\quant>`），所以不用额外执行 pwd。
    取不到（非 Windows 提示符 / UNC 路径等）时返回 None，前端就不显示第二行。
    """
    m = _PROMPT_CWD_RE.match(line or "")
    if not m:
        return None
    path = m.group(1)
    # 去掉多余的尾部反斜杠，但**保留盘符根目录的那一个**（`E:\` 不能变成 `E:`）
    if len(path) > 3:
        path = path.rstrip("\\")
    return path or None

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


class _PersistentLines(list):
    """自动把 append 的内容同步到磁盘日志的 list。

    为什么用子类而不是在每个 append 点手动同步：
        `Session.lines.append(...)` 在代码里有 **十几处**（banner、命令回显、
        tqdm 进度、脚本结束提示……），逐处插同步必然漏。
        换成 list 子类后，`self.lines` 的所有用法（append / clear / 切片 /
        len / 迭代）都不变，只有 append 会被额外记录。

    ⚠️ 只重写 append 和 clear：其它变更操作（`lines[0] = x`、`del lines[:]`）
       在本项目里不存在；将来若引入，需要在对应方法里补同步。
    """

    __slots__ = ("_log",)

    def __init__(self, log):
        super().__init__()
        self._log = log

    def append(self, item) -> None:  # type: ignore[override]
        super().append(item)
        try:
            self._log.append([item])
        except Exception:  # noqa: BLE001
            # 持久化永远不能影响主流程
            pass

    def __setitem__(self, index, value):  # type: ignore[override]
        """支持 `lines[-1] = x`。

        ConPTY 的逐字符回显会反复重画同一行，`_commit_line` 用
        `lines[-1] = line` 就地更新。磁盘日志是追加式，没法改历史记录，
        所以写一条带 `\\r` 的覆盖记录：恢复时 `_visible_line` 的 `\\r`
        覆盖语义会自动把它合并成同一行的最终内容。
        """
        super().__setitem__(index, value)
        try:
            self._log.append(["\r" + str(value)])
        except Exception:  # noqa: BLE001
            pass

    def clear(self) -> None:
        super().clear()
        try:
            self._log.truncate()
        except Exception:  # noqa: BLE001
            pass


class Session:
    def __init__(self, name: str, cmd: str | None = None, *, restore: bool = True):
        self.name = name
        self.cmd = cmd or "(interactive shell)"
        # 磁盘日志：每个会话一个 JSONL，进程重启后能读回最后 600 行
        self._log = _SessionLog(name)
        self.lines = _PersistentLines(self._log)
        self.pending: str | None = None
        self.prog: dict | None = None
        self.done = False
        self.busy = False  # 有前台命令在跑（发送命令到下次出现提示符之间）
        self._startup = True  # 丢弃 cmd 启动 banner（新建会话应干净）
        self._startup_t0 = time.time()
        self._tqdm_t0: float | None = None  # 本进程 tqdm 起始时间（用于 ETA 估算）
        self.updated = time.time()
        self.script: dict | None = _script_from_cmd(cmd)
        # 当前工作目录：由 cmd 提示符行（`E:\code\quant>`）解析得到。
        # 不主动跑 `cd` 去问 —— 提示符本身就是 cwd，白拿的信息。
        self.cwd: str | None = None
        # ── 交互式程序模式 ────────────────────────────────────────────
        # 为 True 时：输出**不剥 ANSI、不按行切分**，原样流式转发，
        # 让 python REPL / ssh / vim / top 这类程序能正常工作。
        # ConPTY 可用时默认就是 True（真终端语义）；退回管道模式时只能
        # 保持 False（管道下拿不到不带换行的提示符，交互式程序本来也没法用）。
        self.interactive = False
        self.interactive_buf: str | None = None   # 尚未遇到换行的当前行
        self.interactive_tail: str = ""           # 该行的最新快照（含 \r 覆盖结果）
        # 终端尺寸（列 × 行）。由前端按自己窗口能容纳的**字符数**上报，
        # 这样换行位置就和手机屏幕上看到的完全一致 —— 否则程序按 200 列
        # 排版，在 40 列的手机上会被二次折行，看起来就是"乱的"。
        self.cols: int = 0
        self.rows: int = 0
        # resize 之后 ConPTY 会重发整屏内容用于重排；到这个世界点之前
        # 收到的输出全部丢弃（否则历史里会出现整段重复）。
        self._suppress_until: float = 0.0

        # ── 恢复上次的输出 ──────────────────────────────────────────────
        # ⚠️ 必须在 _spawn() **之前**读回：新进程启动后马上会有输出，
        #    如果先 spawn 再 load，历史会被追加到新输出后面（顺序错乱）。
        # ⚠️ 只恢复「没有指定 cmd」的交互式会话。带 cmd 的会话是"重新跑一个
        #    脚本"，用户期望看到的是这次的输出，不是上次的。
        restored: list[str] = []
        if restore and not cmd:
            try:
                restored = self._log.load()
            except Exception:  # noqa: BLE001
                restored = []

        self._spawn()
        # PTY 模式下从一开始就是真终端语义（不剥 ANSI、不按行切分），
        # 否则连 shell 自己的彩色提示符都会被吃掉。
        if getattr(self, "pty", None) is not None:
            self.interactive = True
        if cmd:
            self.send(cmd)
        elif restored:
            # 直接塞进 list（绕过 append），避免把恢复的内容又写回磁盘。
            # ⚠️ 磁盘日志里有 `\r` 覆盖记录（逐字符回显写入的"同一行重画"），
            #    必须合并成最终内容，否则读回的历史满是半成品行。
            #    这里**不调用 _commit_line**：历史行当初已经清洗过，
            #    再洗一遍会把有意保留的 SGR 颜色也弄乱。
            for ln in restored:
                if ln.startswith("\r"):
                    if self.lines:
                        try:
                            list.__setitem__(self.lines, -1, ln[1:])
                        except Exception:  # noqa: BLE001
                            pass
                    continue
                if self.lines and not ln.strip() and not self.lines[-1].strip():
                    continue          # 连续空行只留一个
                list.append(self.lines, ln)
            # ⚠️ 这条会出现在**手机 App 的输出区**，所以用中立标记而不是
            #    某一种语言的句子：App 端（main.js）会把它替换成本地化文案。
            #    服务端读不到用户的语言设置，只能这样分工。
            list.append(self.lines, "[termetron:restored]")
            self._log.append(["[termetron:restored]"], force=True)
        # 会话开启输出：终端风格欢迎横幅（纯文本 `***` 分隔，醒目且不依赖 CSS 样式）
        self.lines.append("*" * 44)
        self.lines.append(
            f"[termetron] session '{self.name}' ready — type /help for commands or "
            f"run a shell command ({time.strftime('%Y-%m-%d %H:%M:%S')})"
        )
        self.lines.append("*" * 44)

    def _spawn(self) -> None:
        """(Re)start the interactive shell process and its reader thread.

        ⚠️ 优先用 **ConPTY**（真控制台）。原因不是"高级"，而是必须：
           `subprocess.PIPE` 下 cmd.exe 是管道，python REPL 的 `>>> ` 不带
           换行也不 flush —— read() 永远拿不到那几字节，交互式程序
           （python / ssh / vim / top）在管道链路下**根本无法使用**。
           ConPTY 给子进程一个真控制台，输出是行/字节级流式推进的。

           取不到 ConPTY（老 Windows / 非 Windows）时退回管道 + 行模式，
           功能降级但不至于起不来。
        """
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")

        self.pty = None
        self.proc = None
        try:
            # ⚠️ 用**文件所在目录**而不是 cwd：服务可能从任意目录启动
            #    （systemd / 计划任务 / 双击），`from conpty import` 会
            #    ImportError，于是静默降级到管道 —— 表现就是"交互式程序
            #    用不了，且输出里全是 `\x1b[K\r` 空行"。
            _here = os.path.dirname(os.path.abspath(__file__))
            if _here not in sys.path:
                sys.path.insert(0, _here)
            from conpty import ConPty, available as _conpty_available
        except Exception as exc:  # noqa: BLE001
            print(f"[termetron] conpty import failed ({exc}); "
                  f"interactive programs will not work")
            _conpty_available = lambda: False  # noqa: E731
            ConPty = None  # type: ignore[assignment]

        if ConPty is not None and _conpty_available():
            try:
                # 初始尺寸先用一个宽值；前端连上后会按自己的实际列数
                # 调 POST /api/sessions/<name>/resize 改小（手机约 40 列）。
                self.pty = ConPty(_shell_cmd(),
                                  cols=self.cols or 200,
                                  rows=self.rows or 50)
                threading.Thread(target=self._read_pty, daemon=True).start()
                return
            except Exception as exc:  # noqa: BLE001
                # 起不来就降级：宁可没有交互式能力，也不能整个会话创建失败
                print(f"[termetron] ConPTY unavailable, falling back to pipe: {exc}")
                self.pty = None

        self.proc = subprocess.Popen(
            _shell_cmd(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0, env=env,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _running(self) -> bool:
        """子进程是否还活着（PTY / 管道两种模式统一判断）。"""
        if getattr(self, "pty", None) is not None:
            return self.pty.is_alive()
        p = getattr(self, "proc", None)
        return p is not None and p.poll() is None

    def resize(self, cols: int, rows: int) -> bool:
        """调整 PTY 尺寸（前端上报的字符列数/行数）。

        这是"屏幕适配"的关键：程序按 PTY 的列数决定在哪里换行。
        手机屏幕宽度只能放下 ~40 个等宽字符，如果 PTY 还是 200 列，
        程序会排成 200 宽的行，在手机上被二次折行 —— 看起来就是乱序。
        让 PTY 与屏幕同宽，换行位置就和真终端一致，还能顺带获得
        `ls` 多列、`top` 自适应等好处。
        """
        cols = max(20, min(int(cols or 0), 500))
        rows = max(6, min(int(rows or 0), 200))
        if cols == self.cols and rows == self.rows:
            return False
        self.cols, self.rows = cols, rows
        pty = getattr(self, "pty", None)
        if pty is None:
            return False
        try:
            # ⚠️ ResizePseudoConsole 会让 ConPTY **重发整屏内容**（按新宽度
            #    重排）。这些行如果不丢弃，历史里会出现一整段重复输出。
            #    窗口取 0.35s —— 重绘是 resize 之后立刻发生的，很短。
            self._suppress_until = time.time() + 0.35
            pty.resize(cols, rows)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _track_busy_pty(self) -> None:
        """PTY 模式下根据「当前行是否停在 cmd 提示符」判定 busy。

        为什么需要：`send()` / `send_raw()` 一开始就把 `busy=True`，
        而管道模式靠 `_feed` 里的 `_PROMPT_RE` 把它清回 False。
        PTY 模式不按行切分、也不走 `_feed`，**没人清** → 中断按钮永远
        显示（用户实测 ping 结束后按钮还在）。

        判据：当前未完结行（`interactive_tail`）完整匹配 `X:\\...>`，
        说明 shell 已经回到提示符 → 前台没有程序在跑。
        只在 `busy` 为真时检查，避免空闲时的重复赋值。
        """
        if not self.busy:
            return
        # 提示符可能还在"未完结行"里，也可能已经被 `\n` 提交进历史。
        # ⚠️ 还有第三种常见形态：提示符**粘在上一行输出尾部**，没有换行。
        #    例如 `ping -n 4 ...` 的最后一行是
        #    `... 平均 = 0msE:\product\Termetron>`，用 `^...>$` 匹配不到。
        #    所以这里用"行尾匹配"而不是"整行匹配"。
        tail = (self.interactive_tail or "").strip()
        if tail and _PROMPT_TAIL_RE.search(tail):
            self.busy = False
            return
        if self.lines:
            last = (self.lines[-1] or "").strip()
            if last and _PROMPT_TAIL_RE.search(last):
                self.busy = False
        # Python REPL 的 `>>> ` 也算"回到等待输入" —— 此时 ctrl+C 仍可用
        # （中断一个正在求值的表达式），但普通命令已经结束了。
        # 这里保持 busy=True 更诚实：REPL 本身是前台进程。
        # 所以不做额外处理。

    def _read_pty(self) -> None:
        """PTY 模式的读线程：字节流原样进 _append_raw。

        PTY 输出天然带 ANSI（颜色 / 光标 / 清屏），且不带换行的提示符
        （`>>> `）也是流式到达的。这里**不做任何剥离或按行切分** ——
        那正是管道模式做不到交互式程序的三个原因。
        """
        pty = self.pty
        if pty is None:
            return
        buf = b""
        while True:
            try:
                raw = pty.read(8192)
            except Exception as exc:  # noqa: BLE001
                print(f"[termetron] _read_pty read error: {exc!r}")
                break
            if not raw:
                import traceback
                print(f"[termetron] _read_pty got EOF; pty._closed={getattr(pty,'_closed',None)}")
                traceback.print_stack()
                break
            buf += raw
            text, buf = _smart_decode(buf)
            # 启动窗口内丢弃 cmd 的 banner/版本行，保证新会话干净。
            # ⚠️ 窗口只有 0.4s，且**只丢一次**：一旦超时就永久放行，
            #    否则慢机器上会把用户第一条命令的输出也吞掉。
            if self._startup:
                if time.time() - self._startup_t0 >= 0.4:
                    self._startup = False
                else:
                    continue
            # resize 后的整屏重发：丢掉，避免历史出现重复段
            if time.time() < self._suppress_until:
                continue
            self._append_raw(text)
            self._update_cwd_from(text)
            self._track_busy_pty()
            self.updated = time.time()
        if buf:
            text, _ = _smart_decode(buf)
            self._append_raw(text)
        pty.wait()
        if self.pending:
            self._append_raw(self.pending)
        if self.pty is pty:
            self.done = True
            self.busy = False
        self.updated = time.time()

    def send(self, text: str) -> bool:
        if not self._running():
            return False
        self.cmd = text  # 最近提交的命令（agent 通道读取）
        try:
            if getattr(self, "pty", None) is not None:
                # PTY 里回车是 `\r`（不是 `\r\n`，否则 REPL 多一个空行）
                self.pty.write((text + "\r").encode("utf-8", "replace"))
            else:
                # cmd.exe on zh-CN Windows reads stdin in the ANSI codepage (GBK);
                # encode commands accordingly so Chinese text typed in the box works.
                enc = "cp936" if os.name == "nt" else "utf-8"
                assert self.proc is not None and self.proc.stdin is not None
                self.proc.stdin.write((text + "\r\n").encode(enc, "replace"))
                self.proc.stdin.flush()
            self.busy = True  # 命令开始运行，直到下一个提示符
            return True
        except Exception:  # noqa: BLE001
            return False

    def send_raw(self, text: str, *, submit: bool = False) -> bool:
        """把 text **原样**写进 stdin，不 strip、不加换行。

        交互式程序（python REPL / ssh / vim / top）需要逐字节输入：
        `send()` 总是补回车并把整段当一条命令，在 REPL 里会多出空行。
        这里只依赖调用方给的字节；submit=True 时才补一次回车。
        """
        if not self._running():
            return False
        try:
            payload = text + ("\r\n" if submit else "")
            if getattr(self, "pty", None) is not None:
                self.pty.write(payload.encode("utf-8", "replace"))
            else:
                enc = "cp936" if os.name == "nt" else "utf-8"
                assert self.proc is not None and self.proc.stdin is not None
                self.proc.stdin.write(payload.encode(enc, "replace"))
                self.proc.stdin.flush()
            self.busy = True
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
                        # 顺手记下当前目录：提示符就是 cwd，不额外开销
                        cwd = _cwd_from_prompt(line)
                        if cwd:
                            self.cwd = cwd
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
        r"""提示符出现后延迟判定空闲（命令回显 `X:\...>` 会先经过提示符形态）。

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
        if proc is None or proc.stdout is None:   # PTY 模式不走这里
            return
        buf = b""
        while True:
            raw = proc.stdout.read(8192)
            if not raw:
                break
            buf += raw
            text, buf = _smart_decode(buf)
            if self.interactive:
                # 交互式程序：**原样**转发，不剥 ANSI、不按行切分。
                # 终止符（如 REPL 的 >>>）不带换行，按行切分会永远攒在
                # pending 里不显示 —— 这正是之前 python/ssh 用不了的原因。
                self._append_raw(text)
            else:
                self._feed(_strip_ansi(text).replace("\r\n", "\n"))
            self.updated = time.time()
        # 进程结束：剩余字节按当前模式收尾
        if buf:
            text, _ = _smart_decode(buf)
            if self.interactive:
                self._append_raw(text)
            else:
                self._feed(_strip_ansi(text).replace("\r\n", "\n"))
        proc.wait()
        if self.pending:
            if self.interactive:
                self._append_raw(self.pending)
            else:
                hit = _parse_tqdm(self.pending)
                if hit:
                    self._set_prog(hit)
                else:
                    self.lines.append(self.pending)
        if self.proc is proc:  # 只有当前 shell 结束才标记 done
            self.done = True
            self.busy = False
            self.interactive = False
        self.updated = time.time()

    def _update_cwd_from(self, text: str) -> None:
        """从新到达的输出里找 cmd 提示符，更新 cwd。

        PTY 模式的输出不按行切分，且提示符被 ANSI 光标序列包着，
        所以要先用 `_visible_line` 还原成"看起来的那一行"再匹配。
        `\r` 在这里当作行分隔（ConPTY 用 \r 归位提示符）。
        """
        plain = _visible_line(text)
        for line in plain.replace("\r", "\n").split("\n"):
            m = _PROMPT_CWD_RE.match(line.strip())
            if m:
                path = m.group(1)
                if len(path) > 3:
                    path = path.rstrip("\\")
                if path:
                    self.cwd = path
                    return

    def _append_raw(self, text: str) -> None:
        r"""交互式落字：**原样**保留 ANSI 与 `\r`，只按 `\n` 切行。

        三件必须做对的事：
          1. **不扔 ANSI** —— 颜色、清屏、光标定位都靠它。
          2. **不带换行的提示符也要显示** —— REPL 的 `>>> ` 后面没有 \n，
             未完结的当前行放在 `interactive_tail`，由 snapshot 带出去。
          3. **`\r` 原样保留** —— 它是"光标回到行首"，不是换行也不是
             覆盖。真正的覆盖由前端按终端语义渲染（`\r` 后重新写这一行）。
             之前在这里 `rsplit("\r")[-1]` 会把 ConPTY 的标题序列
             （`\x1b]0;title\x07`）之后的内容全丢掉，导致输出空白。
        """
        if not text:
            return
        # ⚠️ 先拼接再做任何清洗。`_strip_osc` 之类的正则依赖完整的转义
        #    序列；如果只对新到达的块做，被 read 边界切开的序列
        #    （`\x1b` 在上一块、`]0;title\x07` 在这一块）会漏处理，
        #    反过来也可能把上一块遗留的 `\x1b` 孤立掉。
        cur = self.interactive_buf or ""
        buf = cur + text

        # 剥 OSC 标题（`\x1b]0;...\x07`）—— 没有显示意义，且每个前台
        # 进程切换时 ConPTY 都会发一条。
        buf = _strip_osc(buf)

        # ⚠️ ConPTY 的换行是 `\r\n`。`\r` 在这里**只是回车**，不是"清行重画"
        #    （那是单独的 `\x1b[K`）。若把 \r\n 里的 \r 留下来，_visible_line
        #    会按覆盖语义把整行抹掉，导致满屏空行。
        #    ⚠️ 必须在算 hold 之前归一：否则被切在 `\r` 与 `\n` 之间时，
        #    hold 会把 `\r` 带进缓冲区，下一轮拼出 `\r\n` 却被当成行内回车。
        buf = buf.replace("\r\n", "\n")

        # ⚠️ 转义序列会被 read 边界切断（`\x1b` 在上一块末尾、`[?25h` 在下一块），
        #    切开后 `[?25h` 就成了可见乱码。这里把**末尾未完结的 ESC 序列**
        #    留在缓冲区，等下一块拼齐再处理。
        hold = _pending_escape(buf)
        if hold:
            buf = buf[: -len(hold)]

        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            self._commit_line(line)

        # 把被切断的尾部接回去（下次 read 会拼完整）
        self.interactive_buf = buf + hold
        self.interactive_tail = _visible_line(buf)

    def _sanitize_history(self) -> None:
        """对历史做一次就地清洗（幂等）。

        为什么需要：ConPTY 的增量重绘会在历史里留下两类垃圾 ——
          1. 只含控制序列的"空行"（`\\x1b[K` + `\\r`）；
          2. 行尾残留的 `\\r`（CRLF 被切开的产物）。
        这些行在 `_visible_line` 下都是空串，但一旦落到 `self.lines`
        就会在 Web 端变成成片的空白。这里统一清掉，保证对外暴露的
        历史永远是"看得见的内容"。
        """
        cleaned: list[str] = []
        for raw in list(self.lines):
            line = _visible_line(raw)
            if not line.strip():
                continue
            if cleaned and cleaned[-1] == line:
                continue
            cleaned.append(line)
        if len(cleaned) != len(self.lines):
            list.clear(self.lines)
            for line in cleaned:
                list.append(self.lines, line)

    def _commit_line(self, raw: str) -> None:
        """提交一整行到历史。

        ⚠️ ConPTY 的「逐字符回显」本质上是在同一行上反复重画：
           每敲一个键，输出里都会出现一次当前这一行的完整内容。
           如果每段都 append，打 `x = [i*i for i in range(4)]` 会留下
           十几行"半成品"（`>>> x`、`>>> x =`、`>>> x = [i*` …）。

        判据：**如果新行是上一行的前缀延伸，就替换上一行而不是追加。**
        这正是"同一行在增长"的特征；而真正的两行输出（如 `42` 后跟
        `>>>`）互不为前缀，会被正常保留。

        空行：ConPTY 重绘会刷出海量空行，这里**只在必要时**记录一个，
        并且不记录纯空白（历史里留空行只会让屏幕出现大片空白）。
        """
        line = _visible_line(raw)
        if not line.strip():
            return
        if self.lines:
            prev = self.lines[-1]
            if line == prev:
                return
            # 新行以旧行开头（或旧行以新行开头）= 同一行在重画
            if line.startswith(prev) or prev.startswith(line):
                try:
                    self.lines[-1] = line
                except Exception:  # noqa: BLE001
                    self.lines.append(line)
                return
            # ⚠️ ConPTY 的逐字符回显会产生 `>>> p` / `>>> pr` / `>>> pri`
            #    这类"同一行以不同长度重复出现"的中间帧，但它们之间
            #    可能夹着控制序列导致 startswith 判断失效。这里再用
            #    "去掉尾部控制字符后互为前缀"兜底一次。
            a = line.rstrip()
            b = prev.rstrip()
            if a.startswith(b) or b.startswith(a):
                try:
                    self.lines[-1] = line if len(line) >= len(prev) else prev
                except Exception:  # noqa: BLE001
                    pass
                return
        self.lines.append(line)

    def snapshot(self) -> dict:
        # ⚠️ 先清洗历史：ConPTY 的增量重绘会往 lines 里写入只含控制序列的
        #    "空行"（`\x1b[K\r`）和行尾 `\r`。不清洗的话前端拿到的
        #    lines 里一半是空串，Web 上一渲染就是成片空白。
        #    这一步是幂等的，频繁调用也不会改变已清洗的内容。
        if self.interactive:
            self._sanitize_history()

        out = {
            "lines": self.lines[-600:],
            "progress": self.prog,
            "done": self.done,
            "busy": self.busy,
            "script": self.script,
            "cmd": self.cmd,
            "cwd": self.cwd,
            "interactive": self.interactive,
            "tail": self.interactive_tail if self.interactive else "",
            "cols": getattr(self, "cols", 0),
            "rows": getattr(self, "rows", 0),
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
        pty = getattr(self, "pty", None)
        pid = pty.pid if pty is not None else self.proc.pid
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=15,
                )
            elif pty is not None:
                pty.close()
            else:
                self.proc.kill()
        except Exception:  # noqa: BLE001
            try:
                if pty is not None:
                    pty.close()
                else:
                    self.proc.kill()
            except Exception:  # noqa: BLE001
                pass
        if pty is not None:
            try:
                pty.close()
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


_DEFAULT_AGENT_PROMPT = (
    "You are Termetron's remote agent. The user is talking to you through the "
    "Termetron terminal UI (possibly from a mobile phone over a tunnel). You run "
    "on their desktop inside VS Code with GitHub Copilot. Help with code, "
    "quant/trading strategies, Python, math, and general tech. Keep replies "
    "concise and actionable."
)


class AgentSession:
    """Agent 聊天会话：非 shell，纯消息队列。

    用户（手机/电脑）在这里发消息 → 扩展桥（VS Code 内）轮询收件箱 →
    调 Copilot 生成回复 → POST /api/agent/<name>/reply 写回 → 前端显示。
    与 Session 兼容：有 snapshot / send / clear / stop，可放进 SESSIONS 字典，
    /api/sessions 与 /api/output/<name> 路由无需改动即可工作。
    通过新建会话（kind='agent'）创建，可填名称 + 角色设定（system_prompt）。
    """

    def __init__(self, name: str = "agent", system_prompt: str | None = None):
        self.name = name
        self.cmd = "(agent chat)"
        self.system_prompt = system_prompt or _DEFAULT_AGENT_PROMPT
        self.lines: list[str] = []
        self.messages: list[dict] = []  # [{role:'user'|'assistant', text, ts}]
        self.pending = False            # 有未回复的 user 消息
        self.busy = False               # agent 正在思考（等待回复）
        self.done = False
        self.prog = None
        self.script = None
        self._tqdm_t0 = None
        self.updated = time.time()
        self.messages.append({
            "role": "assistant",
            "text": f"agent session '{name}' ready — type a message here; the desktop "
                    "Copilot will reply. (Requires VS Code running on the desktop with "
                    "GitHub Copilot signed in; reachable from mobile over the tunnel.)",
            "ts": time.strftime("%H:%M:%S"),
        })

    def snapshot(self) -> dict:
        return {
            "kind": "agent",
            "system_prompt": self.system_prompt,
            "lines": self.lines[-600:],
            "messages": self.messages[-200:],
            "pending": self.pending,
            "progress": None,
            "done": self.done,
            "busy": self.busy,
            "script": None,
            "cmd": self.cmd,
            "interactive": False,
            "cols": 0,
            "rows": 0,
            "updated": time.strftime("%H:%M:%S", time.localtime(self.updated)),
        }

    def send(self, text: str) -> bool:
        """用户消息入队（扩展桥轮询后转给 Copilot）。"""
        self.messages.append({"role": "user", "text": text, "ts": time.strftime("%H:%M:%S")})
        self.pending = True
        self.busy = True  # 等待 agent 回复（前端显示 thinking）
        self.updated = time.time()
        return True

    def reply(self, text: str) -> None:
        """扩展桥写入 agent 回复；text 空串视为取消（清 pending 但补一条提示）。"""
        if text:
            self.messages.append({"role": "assistant", "text": text, "ts": time.strftime("%H:%M:%S")})
        else:
            self.messages.append({"role": "assistant", "text": "(no response)",
                                  "ts": time.strftime("%H:%M:%S")})
        self.pending = False
        self.busy = False
        self.updated = time.time()

    def clear(self) -> None:
        self.messages.clear()
        self.pending = False
        self.busy = False
        self.updated = time.time()

    def stop(self, rebuild: bool = True) -> None:
        """兼容 DELETE/stop 路由：清 pending（不杀进程，本类无进程）。"""
        self.pending = False
        self.busy = False
        self.updated = time.time()


SESSIONS: dict[str, Session | AgentSession] = {}

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
 /* 字体：与 Scholarius / Livolog 同一份 Sarasa Mono SC 子集（见 app/public/fonts/，
   由 /fonts/ 路由提供）。字重映射同两款应用：Regular 抵 400/500，Bold 抵 600/700。 */
 @font-face{font-family:'Termetron Mono';src:url('/fonts/termetron-mono-regular.woff2') format('woff2');font-weight:400 500;font-style:normal;font-display:swap}
 @font-face{font-family:'Termetron Mono';src:url('/fonts/termetron-mono-bold.woff2') format('woff2');font-weight:600 700;font-style:normal;font-display:swap}
 /*
  配色 = Scholarius / Livolog 同一套中性灰阶设计语言（黑白，不用纯黑 #000 / 纯白 #fff）。
  --bg 页面底（比栏更深）/ --panel 卡片与输入框 / --bar 顶栏与底栏 / --border 分隔线 /
  --txt 正文 / --dim 次要文字 / --acc 强调色 = 与正文同色的墨黑，靠反色块表达主次。
 */
 :root{--bg:#1E1E1E;--panel:#242424;--bar:#131313;--raised:#303030;--border:#333333;--txt:#ECECEA;--dim:#9A9A98;--acc:#F2F2F0;--danger:#E06C5E;--success:#7EE2A8;--on-acc:#1B1B1B;--acc-soft:rgba(242,242,240,.10);--acc-soft2:rgba(242,242,240,.16)}
 *{box-sizing:border-box;font-family:'Termetron Mono','Roboto Mono','Noto Sans Mono','Droid Sans Mono',Menlo,Monaco,'Courier New',monospace;-webkit-tap-highlight-color:transparent!important;-webkit-touch-callout:none}
 *:focus{outline:none}
 button:focus{box-shadow:0 0 0 2px var(--acc-soft2)}
 body{margin:0;background:var(--bg);color:var(--txt);font-size:13px;display:flex;flex-direction:column;height:100vh;overflow:hidden}
 ::-webkit-scrollbar{width:10px;height:10px}
 ::-webkit-scrollbar-track{background:transparent}
 ::-webkit-scrollbar-thumb{background:var(--border);border:2px solid var(--bg);border-radius:6px}
 ::-webkit-scrollbar-thumb:hover{background:var(--dim)}
 ::-webkit-scrollbar-corner{background:transparent}
 header{display:flex;align-items:center;gap:12px;background:var(--bar);border-bottom:none;padding:12px 18px;position:relative}
 /* header 的品牌标志：纯 `>_` 字符（用户要求应用内除 Splash 页外都用字符）。
    ⚠️ 不要换回 SVG 图形：那个图形是「带孤立圆点的折线」，在小尺寸下
       圆点会糊成一个点，看不出是字。纯字符最清晰。
    等宽字体保证 `>` 和 `_` 对齐（比例字体下 `_` 会偏移）。 */
 .cur{display:inline-flex;align-items:center;color:var(--acc);line-height:24px;font-family:'Termetron Mono','Roboto Mono','Droid Sans Mono',monospace;font-size:17px;font-weight:700;letter-spacing:0.5px;white-space:nowrap}
 /* ⚠️ 字号/字距必须与 Scholarius 的 .page-title 一致：17px / 700 / uppercase
    / letter-spacing 0.5px / line-height 24px。之前 .ttl 是 15px + 3px 字距，
    与 `>_` 明显不是一条行内标题。 */
 .ttl{font-family:inherit;font-size:17px;font-weight:700;color:var(--acc);letter-spacing:0.5px;line-height:24px}
 .client-tag{font-size:11px;color:var(--dim);letter-spacing:1px;align-self:center;margin-left:2px;padding-top:4px}
 #curtitle{font-family:inherit;font-weight:700;letter-spacing:0}
 .tabs{display:flex;align-items:flex-end;gap:10px;background:var(--bar);padding:8px 14px 0;border-bottom:none;overflow-x:auto}
 .tab{font-size:13px;color:var(--txt);font-weight:600;padding:6px 16px 8px;border-radius:8px 8px 0 0;display:inline-flex;align-items:center;justify-content:center;min-width:100px;flex-shrink:0;cursor:pointer;border:none;background:var(--raised);white-space:nowrap;transition:all .12s ease}
 .tab:hover{background:var(--acc-soft);color:var(--txt)}
 .tab.act{background:var(--bg);color:var(--acc);font-weight:700}
 .btn{font-size:14px;width:26px;height:26px;line-height:1;border-radius:6px;border:1px solid var(--border);background:var(--panel);color:var(--acc);cursor:pointer;flex:none;transition:all .12s ease}
 .btn.add{width:22px;height:22px;padding:0;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;background:var(--bar);border:none;color:var(--acc);font-size:15px;font-weight:600;line-height:1;align-self:center}
 .btn.add:hover{background:var(--acc);color:var(--on-acc)}
 .btn.stop{width:30px;height:30px;padding:0;border-radius:50%;display:none;align-items:center;justify-content:center;color:var(--acc);border-color:var(--border)}
 .btn.stop svg{width:16px;height:16px;display:block}
 .btn.stop:hover{background:var(--acc);color:var(--on-acc);border-color:var(--acc)}
 .btn.set{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;flex:none;padding:0;border:none;background:none;color:var(--acc);cursor:pointer;line-height:1;-webkit-tap-highlight-color:transparent;transition:opacity .15s ease}
 .btn.set:hover{opacity:.7}
 .btn.set svg{width:22px;height:22px}
 .btn.hm{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;flex:none;padding:0;border:none;background:none;color:var(--acc);cursor:pointer;line-height:1;-webkit-tap-highlight-color:transparent;transition:opacity .15s ease}
 .btn.hm:hover{opacity:.7}
 .btn.hm svg{width:22px;height:22px}
 .hbtns{margin-left:auto;display:inline-flex;align-items:center;gap:2px} /* 右侧按钮组：菜单/连接/会话等水平罗列，整体靠右 */
 .menu-opt.del{color:var(--danger)}
 .wrap{flex:1;min-height:0;display:flex;flex-direction:column;padding:12px 20px 0;overflow-y:auto}
 body.tunnel .wrap{visibility:hidden} /* 远程进入检测期间隐藏会话内容，检测通过才展示 */
 .inrow{flex:none;display:flex;align-items:flex-start;gap:8px;padding:10px 0 16px}
 .d{color:var(--acc);font-weight:700;font-size:14px;line-height:1.4}
 .inrow .d{align-self:flex-start;padding-top:5px}
 .inrow textarea{flex:1;background:none;border:none;color:var(--txt);font-size:13px;outline:none;padding:5px 2px;caret-color:var(--acc);resize:none;overflow-y:auto;min-height:24px;white-space:pre-wrap;word-break:break-all;line-height:1.4;font-family:inherit}
 .inrow input::placeholder{color:#767674}
 .running{color:var(--acc);font-size:14px;font-weight:700;letter-spacing:2px;line-height:1.4;align-self:flex-start;padding-top:5px;display:none}
 .ol{white-space:pre-wrap}
 .tblwrap{overflow-x:auto;margin:8px 0;border:1px solid var(--border);border-radius:6px}
 .tblwrap table{border-collapse:collapse;font-size:12px;width:100%}
 .tblwrap th,.tblwrap td{border:1px solid var(--border);padding:4px 12px;text-align:right;white-space:nowrap}
 .tblwrap th{color:var(--acc);background:var(--acc-soft);font-weight:600;text-align:center}
 .tblwrap td:first-child,.tblwrap th:first-child{text-align:left}
 .tblwrap tr:last-child td{border-bottom:none}
 .tblwrap tr:hover td{background:var(--acc-soft)}
 pre{flex:1 1 auto;min-height:0;margin:0;overflow-y:auto;line-height:1.55;white-space:pre-wrap;word-break:break-all;margin-right:-14px;padding-right:14px}
 .empty{display:none;flex:1;align-items:center;justify-content:center;color:var(--dim);font-size:20px;letter-spacing:2px}
 .empty.show{display:flex}
 .fbar{flex:none;background:var(--bar);border-top:1px solid var(--border);padding:10px 18px;position:relative}
 .prow{display:flex;align-items:center;gap:12px;font-size:12px;margin-bottom:6px}
 .ppct{color:var(--acc);font-weight:700;font-size:16px;min-width:46px}
 .pdesc{color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .pnum{color:var(--dim);font-size:11px;margin-left:auto}
 .pbar{height:16px;background:var(--bg);border:1px solid var(--border);overflow:hidden}
 .pfill{height:100%;background:var(--acc);transition:width .4s;width:0%}
 .pmeta{display:flex;align-items:center;gap:8px;font-size:10px;color:var(--dim);margin-top:7px;letter-spacing:.5px}
 .pmeta .k{color:var(--acc)}
 .menu{position:relative;margin-left:auto}
 .menu-btn{display:inline-flex;align-items:center;justify-content:center;background:none;border:none;color:var(--dim);cursor:pointer;padding:3px 6px;transition:all .12s ease;border-radius:6px}
 .menu-btn svg{display:block}
 .menu-btn:hover{background:var(--acc);color:var(--on-acc)}
 .menu-pop{position:absolute;bottom:26px;right:0;background:var(--panel);border:1px solid var(--border);border-radius:6px;min-width:170px;padding:4px;display:none;z-index:20;box-shadow:0 6px 24px rgba(0,0,0,.5)}
 .menu-pop.open{display:block}
 .menu-item{display:block;width:100%;text-align:left;background:none;border:none;color:var(--txt);padding:7px 10px;font-size:12px;border-radius:4px;cursor:pointer;white-space:nowrap;transition:all .12s ease}
 .menu-item:hover{background:var(--acc);color:var(--on-acc)}
 .menu-item.del{color:var(--danger)}
 .menu-item.del:hover{background:var(--acc);color:var(--on-acc)}
 /*
  ══ 弹窗：照搬 Scholarius 的 .sheet（底部抽屉）══

  Scholarius 的弹层不是居中的对话框，而是**从底部升起的抽屉**：
      .sheet  background:--surface  border-radius:20px 20px 0 0
              box-shadow:0 -8px 32px rgba(0,0,0,.28)
              padding:8px 20px 20px
      .sheet-scrim  background:rgba(0,0,0,.32)

  ⚠️ 旧样式是「居中 + 1px 边框 + 8px 圆角」的对话框，风格完全不同。
  ⚠️ .overlay 改成 align-items:flex-end 让弹窗贴底；justify-content 保留居中，
     这样窄屏时 .modal 的 width:min(440px,92vw) 仍然水平居中。
  ⚠️ 遮罩透明度从 .66 降到 .32：Scholarius 的 scrim 很淡，靠抽屉自身的
     阴影分层，不是靠把背景压黑。
  */
 .overlay{position:fixed;inset:0;background:rgba(0,0,0,.32);display:none;align-items:flex-end;justify-content:center;z-index:70}
 #rmask{background:var(--bg)} /* 认证遮罩不透明：挡住背景会话目录，防止输入密码时泄露信息 */
 .modal{background:var(--panel);border:0;border-radius:20px 20px 0 0;width:min(560px,100%);padding:8px 20px calc(20px + env(safe-area-inset-bottom));box-shadow:0 -8px 32px rgba(0,0,0,.28)}
 .mtitle{font-size:13px;font-weight:700;color:var(--acc);letter-spacing:.5px;margin:12px 0 12px}
 /*
  ══ 表单样式：直接照搬 Scholarius ══

  Scholarius 的深色主题变量与 Termetron 的**完全相同**，所以是 1:1 映射，
  不需要重新设计：
      Scholarius            Termetron
      --surface    #242424  --panel
      --outline    #333333  --border
      --on-surface #ECECEA  --txt
      --on-surface-variant #9A9A98  --dim

  取值全部来自 Scholarius 的 .detail-input / .detail-field-label：
      输入框  background:surface  border:1px outline  radius:10px
              padding:9px 12px  font-size:14px  min-height:38px
              :focus -> border-color: on-surface-variant（不是强调色）
      label   15px / 700 / on-surface

  ⚠️ 旧样式是 4px 圆角 + var(--bg) 底 + var(--acc) 焦点边框 + 12px 字号，
     与 Scholarius 差异明显（圆角、底色、焦点色都不对）。
  ⚠️ focus 用 --dim 而不是 --acc：Scholarius 的焦点是「边框稍微变亮」，
     不换色相。用 --acc（近白）会变成一条刺眼的白边。
 */
 .field{margin-bottom:2px}
 .field label{display:block;font-size:15px;font-weight:700;color:var(--txt);margin-bottom:2px;letter-spacing:0;text-align:left}
 /* ⚠️⚠️ 输入框**必须无框、透明底、无圆角** —— 这是 Scholarius 的唯一形态：
    · `.setting-action` 行：`border:0; background:none; padding:0`
    · `.detail-inline-input`：`background:transparent; border:0;
      border-radius:0; font-size:15px; text-align:right`
   Scholarius 全站**没有任何带边框/圆角/卡片底的输入框**。
   之前我写 `border:1px solid; border-radius:10px; background:--panel`，
   等于凭空造了第三种形态，与它完全不同。
   ⚠️ 纵向 padding 必须小（Scholarius 是 0）：行高不一致会让
      「标签到输入框」的间距随行高漂移，看起来每项松紧不一。 */
 .field input,.field textarea{width:100%;box-sizing:border-box;background:transparent;border:0;border-radius:0;color:var(--txt);padding:2px 0;font-size:15px;font-family:inherit;outline:none;min-height:0}
 .field input::placeholder,.field textarea::placeholder{color:var(--dim);opacity:.55}
 /* focus 也不画边框/下划线（与 Scholarius 一致），只把文字提亮 */
 .field input:focus,.field textarea:focus{color:var(--acc)}
 .field textarea{resize:vertical;min-height:56px;line-height:1.5}
 /* 行分隔：Scholarius 靠行之间的距离分层，不画线；这里只在字段间留 10px */
 .field + .field{margin-top:10px}
 .mmsg{font-size:13px;color:var(--txt);margin:4px 0}
 .srow{display:flex;align-items:center;gap:10px;padding:10px 4px;border-bottom:1px solid var(--border)}
 .srow.cur{color:var(--acc)}
 .srv-p{font-weight:700;color:var(--acc);min-width:64px}
 .srv-m{flex:1;font-size:11px;color:var(--dim)}
 .srv-acts{display:inline-flex;gap:6px}
 .sbtn{padding:10px 16px;border:0;border-radius:10px;background:var(--raised);color:var(--txt);font:inherit;font-size:15px;line-height:1.4;cursor:pointer;-webkit-tap-highlight-color:transparent}
 .sbtn:active{opacity:.75}
 .sbtn.del{color:var(--danger)}
 .sbtn.full{width:100%;margin-top:10px;padding:14px;font-weight:700;text-transform:uppercase;letter-spacing:.6px}
 .mmsg b{color:var(--acc)}
 /*
  ══ 弹窗按钮：照搬 Scholarius 的 .btn / .btn-ghost ══

  Scholarius 的按钮是**无边框、无底色、纯文字**，靠颜色区分层级：
      .btn          15px / 700 / uppercase / letter-spacing .6px
      .btn-action   --nav-active  (#F2F2F0)  普通操作
      .btn-primary  --success     (#7EE2A8)  主操作（确认/升级）
      .btn-danger   --danger      (#E06C5E)  危险操作（删除）
      .btn-ghost    全宽 / radius 14px / 透明底 / #9A9A98

  ⚠️ 旧样式是「1px 边框 + --panel 底色 + 5px 圆角」的方块按钮，
     与 Scholarius 完全不是一个风格。
  ⚠️ 主操作不用 --acc（近白）而用 --success（绿）：Scholarius 就是这么分的，
     而且绿色在深底上能一眼看出「这是继续按钮」。
 */
 /* ⚠️ 照搬 Scholarius 的 .form-actions：
   `justify-content: space-between` —— **取消靠左、确定靠右**。
   之前写的是 flex-end（两个按钮都挤在右下角），与 Scholarius 明显不同。 */
.mactions{display:flex;justify-content:space-between;align-items:center;gap:12px;padding-top:18px;margin-top:0}
/* ⚠️ 只有一个按钮时必须居中（Scholarius 的 .setting-action.setting-center）：
   双按钮 = 取消左 / 确定右；单按钮再走 space-between 就会贴左边，
   看着像「右边还该有个按钮但没渲染出来」。
   ⚠️ 不能用 `:has(:only-child)`：取消键是 `display:none` 隐藏的，
     它在 DOM 里**仍然是子元素**，:only-child 不会命中 —— 实测单按钮时
     justify 依然是 space-between、按钮贴左。改用类名标记（openModal 里加）。 */
.mactions.single{justify-content:center}
 /* ⚠️ 照搬 Scholarius 的 .btn（底部取消/确定）：
    · border:0 + border-radius:0 + background:none → **完全无框**
    · padding:14px 4px（左右只留 4px，不撑开热区，避免无形框感）
    · 15px / 700 / uppercase / letter-spacing .6px
    · :active 用 opacity .55，**不要**用 hover 变色 —— 移动端没有 hover，
      而且 Scholarius 的 .btn 根本没有 hover 规则。
    取消 = --dim（≈ Scholarius 的 --nav-inactive），确定 = --success（绿）。 */
 .mbtn{padding:14px 4px;border:0;border-radius:0;background:none;color:var(--dim);cursor:pointer;font:inherit;font-size:15px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;outline:none;-webkit-tap-highlight-color:transparent;transition:opacity .12s ease,color .16s ease}
 .mbtn:active{opacity:.55}
 .mbtn.ok{color:var(--success)}
 .mbtn.del{color:var(--danger)}
 .mbtn.del:hover{color:var(--danger);opacity:.82}
 .merr{color:var(--danger);font-size:12px;margin:8px 0 0;font-weight:600}
.btn.back{display:inline-flex;align-items:center;justify-content:center;width:38px;height:38px;flex:none;padding:0;border:none;background:none;color:var(--acc);cursor:pointer;line-height:1;-webkit-tap-highlight-color:transparent;transition:opacity .15s}
.btn.back:hover{opacity:.7}
.btn.back svg{width:26px;height:26px}
/* 移动端（窄屏）：会话目录页布局，电脑端保持标签栏 */
body.mobile .tabs{display:none}body.mobile #sesslist{flex:1;overflow-y:auto;padding:12px 14px;gap:10px;flex-direction:column}body.mobile .sess-item{text-align:left;background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:12px 14px;color:var(--txt);cursor:pointer;font-size:13px;display:flex;flex-direction:column;gap:6px}body.mobile .sess-item:active{background:var(--acc-soft2)}body.mobile .sess-item .si-line{display:flex;align-items:center;gap:8px;justify-content:space-between}body.mobile .sess-item .si-name{font-weight:700;color:var(--acc);letter-spacing:.5px}body.mobile .sess-item .si-busy{color:var(--acc);font-size:10px;font-weight:700;letter-spacing:1px}body.mobile .sess-item .si-idle{color:var(--dim);font-size:10px}body.mobile .sess-item .si-cmd{color:var(--dim);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}body.mobile .sess-item.new{justify-content:center;align-items:center;border-style:dashed;color:var(--acc);font-weight:600;letter-spacing:1px}body.mobile .sess-empty{color:var(--dim);text-align:center;padding:40px 0;letter-spacing:2px;font-size:12px}.chat{display:flex;flex-direction:column;gap:10px;padding:6px 2px}.chat-msg{max-width:86%;padding:8px 12px;border-radius:10px;font-size:13px;line-height:1.55;white-space:pre-wrap;word-break:break-word}.chat-user{align-self:flex-end;background:var(--acc-soft2);border:1px solid var(--border);color:var(--txt)}.chat-agent{align-self:flex-start;background:var(--panel);border:1px solid var(--border);color:var(--txt)}.chat-txt{display:block}.chat-meta{font-size:10px;color:var(--dim);margin-top:5px;opacity:.8}.chat-thinking{align-self:flex-start;color:var(--dim);font-style:italic;font-size:12px;padding:4px 2px}.modal-new{min-height:320px}  /* 新建会话弹窗固定高度：TERMINAL/AGENT 切换不跳变 */.seg{display:flex;gap:8px;margin:2px 0 10px}
/* 分段按钮：照搬 Scholarius 的列表项形态（无边框，选中态靠底色 + 文字色区分） */
.seg-btn{flex:1;padding:12px;border:0;border-radius:10px;background:transparent;color:var(--dim);font:inherit;font-size:15px;font-weight:700;letter-spacing:.6px;cursor:pointer;-webkit-tap-highlight-color:transparent;transition:background .12s ease,color .16s ease}
.seg-btn.act{color:var(--txt);background:var(--raised)}
.sess-item.agent{border:1px solid var(--acc-soft2);background:var(--acc-soft)}.sess-item .si-tag{font-size:9px;letter-spacing:1.5px;color:var(--acc);border:1px solid var(--acc-soft2);border-radius:3px;padding:1px 5px;font-weight:700;vertical-align:middle;margin-left:4px}.tab.agent{color:var(--acc)}body.mobile .fbar .prow{flex-wrap:wrap}body.mobile .fbar .pmeta{flex-wrap:wrap}body.mobile #connbtn{display:none}body.mobile header{background:var(--bar);border-bottom:1px solid var(--border)}body.mobile .fbar #pmeta-ses{flex-direction:column;align-items:stretch;gap:2px}body.mobile .fbar .pm-item{display:inline-flex;gap:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}body.mobile .fbar #pmeta-ses .menu{position:absolute;right:12px;bottom:6px}
:root{--fs:13px}
#out{font-size:var(--fs)}
.inrow textarea{font-size:var(--fs)}
.modal{max-height:82vh;overflow-y:auto}
/* 设置面板 */
.set-sec{margin:0 0 14px;text-align:left}
.set-h{font-size:11px;font-weight:700;letter-spacing:1px;color:var(--dim);margin:0 0 8px}
.set-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.set-mini{padding:10px 14px;border:0;border-radius:10px;background:var(--raised);color:var(--txt);font:inherit;font-size:15px;line-height:1.4;cursor:pointer;-webkit-tap-highlight-color:transparent}
.set-mini:active{opacity:.75}
.set-mini.del{color:var(--danger);font-size:14px}
.set-val{color:var(--txt);font-size:13px;min-width:44px;text-align:center;font-weight:600}
.set-sess{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px}
.ss-name{font-size:12px;color:var(--txt);font-weight:600}
.set-empty{color:var(--dim);font-size:12px}
.set-about{color:var(--dim);font-size:12px;line-height:1.7}
/* 选项行：照搬 Scholarius 的 .sheet-picker-item
   （透明底 / 无边框 / radius 10px / 15px；靠分隔线与缩进分层，不靠卡片框） */
.menu-opt{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;padding:13px 12px;margin-bottom:2px;background:transparent;border:0;border-radius:10px;color:var(--txt);font:inherit;font-size:15px;line-height:1.4;cursor:pointer;text-align:left;-webkit-tap-highlight-color:transparent}
.menu-opt:active{background:var(--raised)}
.menu-opt .mo-arr{color:var(--dim);font-size:16px}
/* 设置下拉框（图标下方）—— 照搬 Scholarius 的 .sheet：
   底部抽屉形态（贴底、上方圆角 20px、无边框、向上阴影）。
   ⚠️ 不要写成带 border 的浮动卡片：Scholarius 的所有选项层
      （授权确认、选择器、设置面板）都是同一个 .sheet，
      没有第二个「带边框的浮层」样式。这里跟它统一。 */
#dropdown,#hdropdown{position:fixed;left:0;right:0;bottom:0;top:auto;background:var(--panel);border:0;border-radius:20px 20px 0 0;padding:8px 20px calc(20px + env(safe-area-inset-bottom));min-width:0;box-shadow:0 -8px 32px rgba(0,0,0,.28);z-index:70;max-height:calc(100dvh - 24px);overflow-y:auto;overscroll-behavior:contain}
.dd-item{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;padding:13px 12px;border:0;border-radius:10px;background:transparent;color:var(--txt);font:inherit;font-size:15px;line-height:1.4;cursor:pointer;text-align:left;-webkit-tap-highlight-color:transparent}
.dd-item:active{background:var(--raised)}
#hd-del{color:var(--danger)}
.dd-item .mo-arr{color:var(--dim)}
/* 全屏设置页 */
.fullpage{position:fixed;inset:0;background:var(--bg);z-index:60;display:flex;flex-direction:column}
.fp-header{display:flex;align-items:center;gap:8px;background:var(--bar);padding:12px 16px;flex:none}
.fp-back{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;padding:0;border:none;background:none;color:var(--acc);cursor:pointer;line-height:1;-webkit-tap-highlight-color:transparent}
.fp-back svg{width:24px;height:24px}
.fp-title{font-family:inherit;font-size:15px;font-weight:700;letter-spacing:0;color:var(--acc)}
.fp-body{flex:1;overflow-y:auto;padding:18px}
</style><meta name="theme-color" content="#1E1E1E">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
</head><body>
<header><span class="cur">>_</span><button class="btn back" id="backbtn" style="display:none" title="all sessions"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg></button><span class="ttl" id="brand">TERMETRON</span><span class="client-tag" id="clienttag"></span><span class="ttl" id="curtitle" style="display:none"></span><span class="hbtns"><button class="btn hm" id="hmbtn" style="display:none" title="session actions"><svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.7"></circle><circle cx="12" cy="12" r="1.7"></circle><circle cx="19" cy="12" r="1.7"></circle></svg></button><button class="btn hm" id="connbtn" title="connect (termetron remote on)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg></button><button class="btn set" id="setbtn" title="menu"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"></line><line x1="3" y1="12" x2="21" y2="12"></line><line x1="3" y1="18" x2="21" y2="18"></line></svg></button></span>
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
<div class="overlay" id="cmask" style="display:none;background:var(--bg)">
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
function openModal(title, body, okText, onOk, cancelText) {
  document.getElementById('mtitle').textContent = title;
  document.getElementById('mbody').innerHTML = body;
  const ok = document.getElementById('mok');
  const cancel = document.getElementById('mcancel');
  ok.textContent = okText;
  ok.onclick = async () => { if (await onOk()) closeModal(); };
  // ⚠️ cancelText === null 表示**只有一个按钮**（纯提示框）：
  //    此时隐藏取消键，由 .mactions:has(:only-child) 让确定键居中。
  //    这与 Scholarius 的 showModal({cancelText: null}) 是同一套约定。
  //    不传 cancelText（undefined）时保持原来的「CANCEL + OK」双按钮。
  if (cancelText === null) {
    cancel.style.display = 'none';
    document.querySelector('#overlay .mactions').classList.add('single');
  } else {
    cancel.style.display = '';
    cancel.textContent = cancelText || 'CANCEL';
    cancel.onclick = closeModal;
    document.querySelector('#overlay .mactions').classList.remove('single');
  }
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
  document.onkeydown = (e) => { if (e.key === 'Escape') { e.preventDefault(); closeModal(); } };
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
      a.className = 'tab' + (n === current ? ' act' : '') + (s[n].kind === 'agent' ? ' agent' : '');
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
      const pm = raw.match(/^(\\$) (.*)$/) || raw.match(/^(.+? \\$) (.*)$/);
      if (pm) {
        const nm = pm[1].replace(/\\s+\\$$/, '').trim();
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
// agent 聊天会话渲染：用户消息靠右（紫），agent 回复靠左（面板色）；busy 时显示 thinking
function renderAgentChat(pre, o) {
  const msgs = o.messages || [];
  const key = JSON.stringify(msgs) + '|' + (o.busy ? 'B' : '');
  if (pre.__lastKey === key) return;
  pre.__lastKey = key;
  const prevTop = pre.scrollTop, prevH = pre.scrollHeight;
  pre.innerHTML = '<div class="chat">' + msgs.map(m =>
    '<div class="chat-msg ' + (m.role === 'user' ? 'chat-user' : 'chat-agent') + '">' +
    '<span class="chat-txt">' + esc(m.text || '') + '</span>' +
    '<div class="chat-meta">' + (m.role === 'user' ? 'you' : 'agent') + ' · ' + esc(m.ts || '') + '</div>' +
    '</div>').join('') +
    (o.busy && o.pending ? '<div class="chat-thinking">agent is thinking…</div>' : '') +
    '</div>';
  if (autoScroll) pre.scrollTop = pre.scrollHeight;
  else pre.scrollTop = prevTop + (pre.scrollHeight - prevH);
}
async function refreshOutput() {
  if (mobile && view !== 'session') return;  // 移动端目录页不刷输出
  if (!current) { document.getElementById('out').innerHTML = ''; setProgress(null); return; }
  const o = await api('/api/output/' + current);
  if (!o) return;
  const pre = document.getElementById('out');
  // agent 聊天会话：独立气泡渲染（不走终端 renderOutput）
  if (o.kind === 'agent') { renderAgentChat(pre, o); setProgress(null, null, false); return; }
  if (!o.lines) return;
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
    '<div class="field"><label>session type</label>' +
    '<div class="seg" id="ntype"><button class="seg-btn act" data-t="term" type="button">TERMINAL</button>' +
    '<button class="seg-btn" data-t="agent" type="button">AGENT</button></div></div>' +
    '<div class="field"><label>session name</label><input id="nname" spellcheck="false" placeholder="e.g. demo"></div>' +
    '<div class="field" id="ncmd-w"><label>command (optional)</label><textarea id="ncmd" rows="3" spellcheck="false" placeholder="e.g. python run/demo.py"></textarea></div>' +
    '<div class="field" id="nsp-w" style="display:none"><label>prompt (optional)</label>' +
    '<textarea id="nsp" rows="3" spellcheck="false" placeholder="e.g. quant strategy assistant for factor research, backtesting and risk"></textarea></div>',
    'Create', async () => {
      const name = document.getElementById('nname').value.trim();
      if (!name) { modalError('session name required'); return false; }
      const kind = (document.querySelector('#ntype .seg-btn.act') || {}).dataset?.t || 'term';
      const body = { name, cmd: kind === 'agent' ? null : (document.getElementById('ncmd').value.trim() || null) };
      if (kind === 'agent') {
        body.kind = 'agent';
        body.agent = { system_prompt: document.getElementById('nsp').value.trim() || null };
      }
      const r = await api('/api/sessions', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
      if (r.error) { modalError(r.error); return false; }
        hist[name] = []; histIdx[name] = 0;  // 新会话无历史（防同名重建残留）
        current = name; await refreshAll();  // 等会话列表刷新完成，管理页才能显示新卡片
        // 管理会话页内新建：留在管理页并刷新列表（不退出该页面）；否则进入新会话
        const fp = document.getElementById('fullpage');
        if (fp && fp.style.display === 'flex') { openSessionManager(); } else { document.getElementById('in').focus(); }
        return true;
    });
  const nm = document.querySelector('#overlay .modal');
  if (nm) nm.classList.add('modal-new');  // 固定弹窗高度：TERMINAL/AGENT 切换不跳变
  // 类型切换：agent 显示角色表单（隐藏 command），普通终端反之
  document.querySelectorAll('#ntype .seg-btn').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll('#ntype .seg-btn').forEach(b => b.classList.toggle('act', b === btn));
      const isAgent = btn.dataset.t === 'agent';
      document.getElementById('ncmd-w').style.display = isAgent ? 'none' : '';
      document.getElementById('nsp-w').style.display = isAgent ? '' : 'none';
    };
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
function showMsg(msg) { openModal('TERMETRON', '<p class="mmsg">' + msg + '</p>', 'OK', () => true, null); }
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
  const parts = text.trim().split(/\\s+/);
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
      openModal('SESSIONS', '<p class="mmsg" style="line-height:1.8;white-space:pre-wrap">' + (lines.join('\\n') || '(none)') + '</p>', 'OK', () => true, null);
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
      openModal('COMMANDS', HELP_HTML, 'OK', () => true, null);
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
    const isAgent = s.kind === 'agent';
    const it = document.createElement('button');
    it.className = 'sess-item' + (n === current ? ' act' : '') + (isAgent ? ' agent' : '');
    it.innerHTML = '<span class="si-line"><span class="si-name">' + esc(n) + (isAgent ? ' <span class="si-tag">AGENT</span>' : '') + '</span>' +
      (isAgent ? (s.busy ? '<span class="si-busy">thinking</span>' : '<span class="si-idle">chat</span>') :
        (s.busy ? '<span class="si-busy">running</span>' : '<span class="si-idle">idle</span>')) + '</span>' +
      '<span class="si-cmd">' + esc(isAgent ? ((s.system_prompt || 'agent chat').replace(/\\s+/g, ' ').slice(0, 42)) : (s.cmd || '')) + '</span>';
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
function currentTheme(){ try{ return localStorage.getItem('qt_theme') || '#F2F2F0'; }catch(e){ return '#F2F2F0'; } }
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
  // 设计语言与 Scholarius / Livolog 一致：中性灰阶单一强调色，不再提供彩色主题切换。
  // 旧的 qt_theme 偏好直接清掉，避免残留的紫色覆盖新强调色。
  try{ localStorage.removeItem('qt_theme'); }catch(e){}
  document.documentElement.style.removeProperty('--acc');
  openFullPage('MORE SETTINGS',
    '<div class="set-sec"><div class="set-h">FONT SIZE</div><div class="set-row"><button class="set-mini" id="fs-dec">A-</button><span class="set-val" id="fs-val"></span><button class="set-mini" id="fs-inc">A+</button></div></div>' +
    '<div class="set-sec"><div class="set-h">ABOUT</div><div class="set-about">' + QT_ABOUT.summary + '<br>' + QT_ABOUT.version + '<br>docs: ' + esc(QT_ABOUT.readme) + '</div></div>');
  const fsv = document.getElementById('fs-val');
  const upd = () => { fsv.textContent = getFs() + 'px'; };
  upd();
  document.getElementById('fs-inc').onclick = () => { setFs(Math.min(22, getFs() + 1)); upd(); };
  document.getElementById('fs-dec').onclick = () => { setFs(Math.max(9, getFs() - 1)); upd(); };
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
    const key = /^\\d{4}$/.test(saved) ? saved : String(Math.floor(1000 + Math.random() * 9000));
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
  // ⚠️ 应用内除 Splash 页外一律用纯 `>_` 字符（用户要求）。
  //    这里用 SVG <text> 画字符，而不是复用图标的折线几何 ——
  //    图标那个图形带孤立圆点，缩小到 32px 标签页尺寸会糊成噪点。
  //    颜色仍用图标的两端极值 #FFFFFF / #6E6E6E。
  const icon = 'data:image/svg+xml;utf8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">' +
    '<rect width="100" height="100" fill="#1B1B1B"/>' +
    '<text x="50" y="66" text-anchor="middle" ' +
    'font-family="monospace" font-size="52" font-weight="700" ' +
    'fill="#FFFFFF">&gt;_</text>' +
    '</svg>');
  const fav = document.createElement('link'); fav.rel = 'icon'; fav.href = icon; document.head.appendChild(fav);
  const man = {name:'Termetron', short_name:'TMT', start_url:'.', display:'standalone',
    background_color:'#1E1E1E', theme_color:'#1E1E1E',
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
      if (!/^\\d{4}$/.test(code)) {
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
        # ⚠️ extra_headers 先发，默认的 no-store 只在没被覆盖时发 ——
        #    否则字体的 Cache-Control 会被后面的 no-store 盖掉（重复头，浏览器取第一个）。
        #    页面本身必须 no-store（保证 reload 拿到最新 UI），字体则可以长缓存。
        cache = (extra_headers or {}).get("Cache-Control")
        if cache is None:
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

    # 字体：与 Scholarius / Livolog 同一份 Sarasa Mono SC 子集（放在 app/public/fonts/，
    # 构建后也会被 vite 拷到 www/fonts/）。这里直接读源文件，保证桌面端/手机端是同一份字体。
    _FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "app", "public", "fonts")
    _FONT_MIME = {".woff2": "font/woff2", ".ttf": "font/ttf"}

    def _serve_font(self, path: str) -> None:
        """只允许白名单文件名，避免路径穿越读任意文件。"""
        name = path.rsplit("/", 1)[-1]
        if name not in ("termetron-mono-regular.woff2", "termetron-mono-bold.woff2"):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        full = os.path.join(self._FONT_DIR, name)
        if not os.path.isfile(full):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        with open(full, "rb") as f:
            body = f.read()
        ext = os.path.splitext(name)[1].lower()
        self._send(200, body, self._FONT_MIME.get(ext, "application/octet-stream"),
                   extra_headers={"Cache-Control": "public, max-age=604800"})

    def _query(self, name: str) -> str:
        if "?" not in self.path:
            return ""
        q = self.path.split("?", 1)[1]
        m = re.search(r"(?:^|&)" + re.escape(name) + r"=([^&]*)", q)
        return m.group(1) if m else ""

    def _remote(self) -> bool:
        """是否来自「非本机回环」的请求。

        ⚠️ 2026-10-30 起不再有公网隧道：连接方式是 **Tailscale 直连**
           （手机在同一个 tailnet 里，直接访问本机的 Tailscale IP）。
           保留这个判断仍有意义 —— 它区分「本机浏览器」与「手机访问」，
           用于 UI 上的客户端标识、以及是否需要额外防护。
        """
        if self.headers.get("Cf-Connecting-Ip") or self.headers.get("X-Forwarded-For"):
            return True
        ip = self.client_address[0]
        return ip not in ("127.0.0.1", "::1", "localhost")

    def _authed(self) -> bool:
        """所有请求一律放行。

        ⚠️ 2026-10-30：**应用层认证已移除**。
        用户选择的方案是「完全去掉密码，只填 Tailscale IP + 端口就连上」。
        理由：Tailscale 本身已是经过设备认证的加密网络层（WireGuard +
        tailnet ACL），能连到本机 8899 的只可能是同一 tailnet 内的已授权设备。
        在它之上再加一次性密码 + 4 位配对码属于重复防护，且让手机端每次
        重装 App 都要重新配对，体验很差。

        残留的 REMOTE.token / REMOTE.allowed 只服务于 **桌面端**的旧 remote
        面板（本机浏览器里看二维码/状态），不影响手机连接。
        """
        return True

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
        elif path.startswith("/fonts/"):
            self._serve_font(path)
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
                    n: ({"status": "agent", "cmd": s.cmd, "progress": None, "busy": s.busy,
                         "script": None, "kind": "agent", "system_prompt": s.system_prompt,
                         "cwd": getattr(s, "cwd", None)}
                         if isinstance(s, AgentSession) else
                         {"status": "done" if s.done else "running", "cmd": s.cmd,
                          "progress": s.prog, "busy": s.busy, "script": s.script,
                          "cwd": s.cwd, "interactive": s.interactive})
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
        # agent 回复（扩展桥写入 Copilot 回复 / 取消）
        if self.path.startswith("/api/agent/") and self.path.endswith("/reply"):
            name = self.path.split("/")[3]
            s = SESSIONS.get(name)
            if not isinstance(s, AgentSession):
                self._json({"error": "agent session not found"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad request"}, 400)
                return
            s.reply((body.get("text") or "").strip())
            self._json({"ok": True})
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
            text = body.get("text") or ""
            raw = bool(body.get("raw"))
            submit = bool(body.get("submit"))
            # 交互式模式开关：显式传入就切换，不传则保持原状
            if "interactive" in body:
                want = bool(body.get("interactive"))
                if want != s.interactive:
                    s.interactive = want
                    # 进入时把已攒的半行收尾，避免与旧的 pending 混在一起
                    if want:
                        if s.pending:
                            s.lines.append(s.pending)
                            s.pending = None
                    else:
                        if s.interactive_buf:
                            s.lines.append(s.interactive_buf)
                        s.interactive_buf = None
                        s.interactive_tail = ""
            if not raw:
                # 行模式：沿用旧规则（去空白、当一条命令），但**不清空**
                # 纯空白输入：允许发送空行（REPL 里回车有意义）
                if not text.strip():
                    self._json({"error": "empty input"}, 400)
                    return
                scr = _script_from_cmd(text)
                if scr:
                    s.script = scr
                ok = s.send(text)
                self._json({"ok": ok, "interactive": s.interactive})
                return
            # 原样模式：不 strip、不补换行。submit=True 时补一个 \r。
            if text == "" and not submit:
                self._json({"error": "empty input"}, 400)
                return
            ok = s.send_raw(text, submit=submit)
            self._json({"ok": ok, "interactive": s.interactive})
        # 中断当前进程（杀进程树并重建 shell）
        elif self.path.startswith("/api/sessions/") and self.path.endswith("/stop"):
            name = self.path.split("/")[3]
            s = SESSIONS.get(name)
            if not s:
                self._json({"error": "not found"}, 404)
                return
            s.stop()
            self._json({"ok": True})
        # 终端尺寸：前端按自己屏幕能容纳的**字符列数**上报。
        # 这是"屏幕适配"的落点 —— PTY 与屏幕同宽，程序换行位置才和真终端一致。
        elif self.path.startswith("/api/sessions/") and self.path.endswith("/resize"):
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
            changed = False
            if hasattr(s, "resize"):
                changed = s.resize(body.get("cols"), body.get("rows"))
            self._json({"ok": True, "changed": changed,
                        "cols": getattr(s, "cols", 0),
                        "rows": getattr(s, "rows", 0)})
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
            kind = (body.get("kind") or "").strip()
            if kind == "agent":
                # 新建 agent 聊天会话：表单填名称 + 角色设定（system_prompt）
                acfg = body.get("agent") or {}
                sp = (acfg.get("system_prompt") or "").strip()
                SESSIONS[name] = AgentSession(name, sp or None)
            else:
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
    # ⚠️ 默认 host 改由 Tailscale 决定（见下）。--host 保留为显式覆盖，
    #    本地开发/无 Tailscale 时仍可用 127.0.0.1。
    ap.add_argument("--host", default=None,
                    help="监听地址；默认自动使用 Tailscale IP，检测不到则 127.0.0.1")
    ap.add_argument("--local-only", action="store_true",
                    help="强制只监听 127.0.0.1（即使检测到 Tailscale IP）")
    args = ap.parse_args()
    global _LOCAL_PORT
    _LOCAL_PORT = args.port

    # 监听地址：显式 --host > --local-only > Tailscale IP > 127.0.0.1
    if args.host:
        host = args.host
        why = "explicit --host"
    elif args.local_only:
        host = "127.0.0.1"
        why = "--local-only"
    else:
        try:
            from tools.ts_ip import detect_tailscale_ip
            ts_ip = detect_tailscale_ip()
        except Exception:  # noqa: BLE001
            # ⚠️ 发布包可能不含 tools/（namespace package 在某些打包方式下会丢），
            #    所以这里再试一次同目录直接导入，最后才放弃。
            try:
                import os as _os
                import sys as _sys
                _sys.path.insert(0, _os.path.join(
                    _os.path.dirname(_os.path.abspath(__file__)), 'tools'))
                from ts_ip import detect_tailscale_ip  # type: ignore
                ts_ip = detect_tailscale_ip()
            except Exception:  # noqa: BLE001
                ts_ip = None
        if ts_ip:
            host = ts_ip
            why = "tailscale"
        else:
            host = "127.0.0.1"
            why = "no tailscale IP found"

    # ── 启动时恢复会话 ──────────────────────────────────────────────
    # ⚠️ 必须**先把磁盘上所有有日志的会话都重建出来**，再建默认 shell。
    #    只建 shell 是不够的：`Session.__init__` 只有在会话被创建时才会去
    #    load 日志，进程崩溃后那些没被重建的会话，日志文件还在但永远读不回来
    #    （实测：persist-test 的 jsonl 有 361 字节，重启后 API 里根本看不到它）。
    try:
        from tools.session_log import list_session_names as _list_names
    except Exception:  # noqa: BLE001
        try:
            from session_log import list_session_names as _list_names  # type: ignore
        except Exception:  # noqa: BLE001
            _list_names = lambda: []  # type: ignore

    restored_info = []
    for name in _list_names():
        if name in SESSIONS:
            continue
        try:
            SESSIONS[name] = Session(name, None)
            restored_info.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"[termetron] warn: failed to restore session '{name}': {e}")

    if not SESSIONS:
        try:
            SESSIONS["shell"] = Session("shell", None)
            print("[termetron] created default session 'shell'")
        except Exception as e:  # noqa: BLE001
            print(f"[termetron] warn: cannot create default session: {e}")
    # agent 聊天会话由用户通过“新建会话 → agent 类型 → 填表单”创建（不预置）

    if restored_info:
        print(f"[termetron] restored {len(restored_info)} session(s): "
              + ", ".join(restored_info[:8])
              + ("..." if len(restored_info) > 8 else ""))

    srv = ThreadingHTTPServer((host, args.port), Handler)
    print(f"Termetron running at http://{host}:{args.port}  ({why})")
    if why == "tailscale":
        print(f"[termetron] On your phone: set Tailscale IP to {host}, port {args.port}")
    elif why != "explicit --host":
        print("[termetron] No Tailscale IP detected - the phone app cannot connect.")
        print("[termetron] Install and sign in to Tailscale, or pass --host <ip>.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        for s in list(SESSIONS.values()):
            s.stop(rebuild=False)
        print("\nshutdown")


if __name__ == "__main__":
    main()
