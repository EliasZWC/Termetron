"""最小可用的 ConPTY 封装（Windows 10 1809+，纯 ctypes，无第三方依赖）。

为什么要它：
    `subprocess.PIPE` 下 cmd.exe 是**管道**，python REPL 的 `>>> ` 不带
    换行且不 flush，read() 永远拿不到 —— 交互式程序（python / ssh / vim /
    top）在这种链路下根本不能用。ConPTY 给子进程一个**真正的控制台**，
    输出是流式的，行为与本地开终端一致。

对外接口：
    ConPty(shell_cmd, cols, rows) -> .handle (可读的管道句柄) / .write(bytes)
    .resize(cols, rows) / .close()
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ── 需要用到的常量 ─────────────────────────────────────────────────────
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
HANDLE_FLAG_INHERIT = 0x00000001
STARTF_USESTDHANDLES = 0x00000100
CREATE_UNICODE_ENVIRONMENT = 0x00000400


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wt.DWORD),
        ("lpSecurityDescriptor", wt.LPVOID),
        ("bInheritHandle", wt.BOOL),
    ]


class COORD(ctypes.Structure):
    _fields_ = [("X", wt.SHORT), ("Y", wt.SHORT)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("lpReserved", wt.LPWSTR),
        ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR),
        ("dwX", wt.DWORD),
        ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD),
        ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD),
        ("dwYCountChars", wt.DWORD),
        ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("wShowWindow", wt.WORD),
        ("cbReserved2", wt.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wt.HANDLE),
        ("hStdOutput", wt.HANDLE),
        ("hStdError", wt.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wt.HANDLE),
        ("hThread", wt.HANDLE),
        ("dwProcessId", wt.DWORD),
        ("dwThreadId", wt.DWORD),
    ]


# ── 函数签名 ───────────────────────────────────────────────────────────
kernel32.CreatePipe.argtypes = [
    ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE),
    ctypes.POINTER(SECURITY_ATTRIBUTES), wt.DWORD,
]
kernel32.CreatePipe.restype = wt.BOOL

kernel32.SetHandleInformation.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD]
kernel32.SetHandleInformation.restype = wt.BOOL

_k32 = kernel32
_k32.CreatePseudoConsole.argtypes = [
    COORD, wt.HANDLE, wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE),
]
_k32.CreatePseudoConsole.restype = ctypes.c_long

_k32.ResizePseudoConsole.argtypes = [wt.HANDLE, COORD]
_k32.ResizePseudoConsole.restype = ctypes.c_long

_k32.ClosePseudoConsole.argtypes = [wt.HANDLE]
_k32.ClosePseudoConsole.restype = None

_k32.InitializeProcThreadAttributeList.argtypes = [
    ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t),
]
_k32.InitializeProcThreadAttributeList.restype = wt.BOOL

_k32.UpdateProcThreadAttribute.argtypes = [
    ctypes.c_void_p, wt.DWORD, ctypes.c_size_t, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p,
]
_k32.UpdateProcThreadAttribute.restype = wt.BOOL

_k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
_k32.DeleteProcThreadAttributeList.restype = None

_k32.CreateProcessW.argtypes = [
    wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL,
    wt.DWORD, ctypes.c_void_p, wt.LPCWSTR,
    ctypes.POINTER(STARTUPINFOEXW), ctypes.POINTER(PROCESS_INFORMATION),
]
_k32.CreateProcessW.restype = wt.BOOL

_k32.CloseHandle.argtypes = [wt.HANDLE]
_k32.CloseHandle.restype = wt.BOOL

_k32.PeekNamedPipe.argtypes = [
    wt.HANDLE, ctypes.c_void_p, wt.DWORD,
    ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
]
_k32.PeekNamedPipe.restype = wt.BOOL

_k32.ReadFile.argtypes = [
    wt.HANDLE, ctypes.c_void_p, wt.DWORD,
    ctypes.POINTER(wt.DWORD), ctypes.c_void_p,
]
_k32.ReadFile.restype = wt.BOOL

_k32.WriteFile.argtypes = [
    wt.HANDLE, ctypes.c_void_p, wt.DWORD,
    ctypes.POINTER(wt.DWORD), ctypes.c_void_p,
]
_k32.WriteFile.restype = wt.BOOL

_k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
_k32.GetExitCodeProcess.restype = wt.BOOL

_k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_k32.WaitForSingleObject.restype = wt.DWORD


class ConPtyError(RuntimeError):
    pass


def available() -> bool:
    """当前平台是否支持 ConPTY（Windows 10 1809+）。"""
    return sys.platform == "win32" and hasattr(kernel32, "CreatePseudoConsole")


class ConPty:
    """把子进程跑在真正的控制台里，输出走管道给我们读。"""

    def __init__(self, argv: list[str], cols: int = 200, rows: int = 50,
                 cwd: str | None = None):
        if not available():
            raise ConPtyError("ConPTY not available on this platform")

        self.cols, self.rows = cols, rows
        self.hpc = wt.HANDLE()
        self.hprocess = wt.HANDLE()
        self.hthread = wt.HANDLE()
        self._pi = PROCESS_INFORMATION()
        self._attr = None
        self._closed = False

        # 我们读的一端（pseudo console 的"输出"进这里）
        in_read, in_write = wt.HANDLE(), wt.HANDLE()
        out_read, out_write = wt.HANDLE(), wt.HANDLE()

        sa = SECURITY_ATTRIBUTES()
        sa.nLength = ctypes.sizeof(sa)
        sa.bInheritHandle = True
        sa.lpSecurityDescriptor = None

        if not kernel32.CreatePipe(ctypes.byref(in_read), ctypes.byref(in_write),
                                   ctypes.byref(sa), 0):
            raise ConPtyError(f"CreatePipe(in) failed: {ctypes.get_last_error()}")
        if not kernel32.CreatePipe(ctypes.byref(out_read), ctypes.byref(out_write),
                                   ctypes.byref(sa), 0):
            raise ConPtyError(f"CreatePipe(out) failed: {ctypes.get_last_error()}")

        # 我们这一端不能被继承（否则子进程关闭后管道仍不 EOF）
        kernel32.SetHandleInformation(in_write, HANDLE_FLAG_INHERIT, 0)
        kernel32.SetHandleInformation(out_read, HANDLE_FLAG_INHERIT, 0)

        size = COORD(cols, rows)
        hr = kernel32.CreatePseudoConsole(size, in_read, out_write, 0,
                                          ctypes.byref(self.hpc))
        if hr != 0:
            raise ConPtyError(f"CreatePseudoConsole failed: {hex(hr & 0xFFFFFFFF)}")

        # 这两个句柄已交给 pseudo console，本进程不再需要
        kernel32.CloseHandle(in_read)
        kernel32.CloseHandle(out_write)

        self._in_write = in_write     # 往这里写 = 往终端键盘输入
        # 输出句柄的**整数值**：os.read / select 只认 int，ctypes 的
        # c_void_p 会抛 "cannot be interpreted as an integer"。
        self.handle = out_read.value if hasattr(out_read, "value") else int(out_read)
        self._out_read = out_read

        # ── 启动子进程，把 pseudo console 挂到它的属性列表 ──────────────
        attr_size = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, 1, 0,
                                                   ctypes.byref(attr_size))
        attr_buf = ctypes.create_string_buffer(attr_size.value)
        self._attr = attr_buf
        if not kernel32.InitializeProcThreadAttributeList(
                ctypes.byref(attr_buf), 1, 0, ctypes.byref(attr_size)):
            raise ConPtyError(f"InitializeProcThreadAttributeList failed: "
                              f"{ctypes.get_last_error()}")

        if not kernel32.UpdateProcThreadAttribute(
                ctypes.byref(attr_buf), 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                ctypes.cast(self.hpc, ctypes.c_void_p),
                ctypes.sizeof(ctypes.c_void_p), None, None):
            raise ConPtyError(f"UpdateProcThreadAttribute failed: "
                              f"{ctypes.get_last_error()}")

        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(si)
        si.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p)

        # ⚠️ 关键：子进程的 stdin/stdout/stderr 必须清零 + 显式不可继承。
        #    否则在 ConPTY 建立前，子进程会继承**父进程的控制台句柄**，
        #    输出直接打到这里（表现：python 的 banner 出现在父终端里，
        #    而我们的管道永远收不到数据）。
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        si.StartupInfo.hStdInput = None
        si.StartupInfo.hStdOutput = None
        si.StartupInfo.hStdError = None

        cmdline = ctypes.create_unicode_buffer(subprocess_list2cmdline(argv))
        ok = kernel32.CreateProcessW(
            None, cmdline, None, None, False,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
            None, cwd, ctypes.byref(si), ctypes.byref(self._pi),
        )
        if not ok:
            raise ConPtyError(f"CreateProcessW failed: {ctypes.get_last_error()}")

        self.hprocess = self._pi.hProcess
        self.hthread = self._pi.hThread
        self.pid = self._pi.dwProcessId

    # ── 与 subprocess 形状对齐的便捷方法 ────────────────────────────────
    def write(self, data: bytes) -> None:
        if self._closed:
            return
        n = wt.DWORD(0)
        kernel32.WriteFile(self._in_write, data, len(data),
                           ctypes.byref(n), None)

    def read(self, size: int = 4096) -> bytes:
        """从伪控制台读输出（**阻塞**到有数据或管道关闭）。

        ⚠️ 不要用 `PeekNamedPipe` 轮询：`CreatePipe` 建的是**匿名管道**，
           在它上面 Peek 的行为不可靠（实测在某些时刻恒返回"无数据"），
           会让读线程永久卡住 —— 表现就是 python 的 `>>> ` 永远不出现。

        ⚠️ 也不能 `ReadFile` 返回 0 就当 EOF：ConPTY 的输出管道在子进程
           存活期间不会 EOF，返回 0 只可能是尺寸参数问题。真正的 EOF
           由 ReadFile 失败（ERROR_BROKEN_PIPE）表达。
        """
        if self._closed:
            return b""
        buf = ctypes.create_string_buffer(size)
        while True:
            if self._closed:
                return b""
            n = wt.DWORD(0)
            ok = kernel32.ReadFile(self._out_read, buf, size,
                                   ctypes.byref(n), None)
            if not ok:
                err = ctypes.get_last_error()
                print(f"[conpty] ReadFile failed err={err}")
                return b""
            if n.value > 0:
                return buf.raw[: n.value]
            time.sleep(0.02)

    def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        self.cols, self.rows = cols, rows
        kernel32.ResizePseudoConsole(self.hpc, COORD(cols, rows))

    def is_alive(self) -> bool:
        if self._closed:
            return False
        code = wt.DWORD()
        if not kernel32.GetExitCodeProcess(self.hprocess, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE

    def wait(self) -> int:
        kernel32.WaitForSingleObject(self.hprocess, 0xFFFFFFFF)
        code = wt.DWORD()
        kernel32.GetExitCodeProcess(self.hprocess, ctypes.byref(code))
        return code.value

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            kernel32.ClosePseudoConsole(self.hpc)
        except Exception:  # noqa: BLE001
            pass
        for h in (self._in_write, getattr(self, "handle", None),
                  self.hprocess, self.hthread):
            if h:
                try:
                    kernel32.CloseHandle(h)
                except Exception:  # noqa: BLE001
                    pass
        if self._attr is not None:
            try:
                kernel32.DeleteProcThreadAttributeList(ctypes.byref(self._attr))
            except Exception:  # noqa: BLE001
                pass


def subprocess_list2cmdline(argv: list[str]) -> str:
    import subprocess
    return subprocess.list2cmdline(argv)


if __name__ == "__main__":
    # 自检：起一个 cmd，写 dir 命令，读回输出
    import io
    import time

    if not available():
        print("ConPTY unavailable")
        raise SystemExit(1)

    p = ConPty(["cmd.exe"], cols=120, rows=40)
    print("pid:", p.pid)
    time.sleep(0.6)
    p.write(b"echo PTY_HELLO\r\n")
    time.sleep(0.6)
    p.write(b"python -c \"print('FROM_PYTHON')\"\r\n")
    time.sleep(2.0)

    # 非阻塞读：用 PeekNamedPipe 判断有没有数据，避免 read 阻塞卡死。
    # 真实运行里 Session 的读线程是独立线程，阻塞也无妨；自检必须能停下来。
    kernel32.PeekNamedPipe.argtypes = [
        wt.HANDLE, ctypes.c_void_p, wt.DWORD,
        ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
    ]
    buf = b""
    deadline = time.time() + 5.0
    while time.time() < deadline:
        avail = wt.DWORD(0)
        ok = kernel32.PeekNamedPipe(p._out_read, None, 0, None,
                                    ctypes.byref(avail), None)
        if ok and avail.value:
            chunk = p.read(avail.value)
            if not chunk:
                break
            buf += chunk
        else:
            time.sleep(0.05)
    text = buf.decode("utf-8", "replace").replace("\x1b", "\\e")
    print("--- output ---")
    print(text[:800])
    print("--- checks ---")
    print("PTY_HELLO:", "OK" if "PTY_HELLO" in text else "FAIL")
    print("FROM_PYTHON:", "OK" if "FROM_PYTHON" in text else "FAIL")
    p.close()
