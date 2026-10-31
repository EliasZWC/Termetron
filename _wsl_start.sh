#!/bin/bash
# termetron 在 WSL 内启动，自动绑定检测到的 Tailscale IP（100.x），
# 手机装 Tailscale 后即可通过 tailnet 直连。
# 用法：wsl -e bash /mnt/e/product/Termetron/_wsl_start.sh
cd /mnt/e/product/Termetron || exit 1
exec python3 quant_terminal.py --port 8899
