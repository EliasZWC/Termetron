"""Termetron 启动入口。

用法（在项目根目录）:
    python termetron.py              # 启动服务器并自动打开默认浏览器
    python termetron.py --no-open    # 只启动服务器，不自动打开浏览器
    python termetron.py --port 9000  # 自定义端口

服务器常驻运行，Ctrl+C 停止。
浏览器打开方式交给系统默认浏览器；如需在 VS Code 内查看，请自行用
Simple Browser 打开 http://127.0.0.1:<port>。
"""
import os
import subprocess
import sys
import threading
import time
import webbrowser

PORT = 8900
ROOT = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    port = PORT
    no_open = False
    if "--port" in sys.argv:
        i = sys.argv.index("--port")
        if i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    if "--no-open" in sys.argv:
        no_open = True
    url = f"http://127.0.0.1:{port}"

    if not no_open:
        threading.Thread(
            target=lambda: (time.sleep(2), webbrowser.open(url)),
            daemon=True,
        ).start()
    print(f"[termetron] Termetron -> {url}")
    print("[termetron] server is long-running — press Ctrl+C to stop it.")

    target = os.path.join(ROOT, "quant_terminal.py")
    subprocess.run([sys.executable, target, "--port", str(port)])


if __name__ == "__main__":
    main()
