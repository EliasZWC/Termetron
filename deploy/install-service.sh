#!/usr/bin/env sh
# 把 Termetron 安装成 systemd 服务（开机自启 + 崩溃自动重启）
#
# 用法：
#   ./deploy/install-service.sh          # 安装并启动
#   ./deploy/install-service.sh --uninstall  # 停止并移除
#
# ⚠️ 需要 sudo（要写 /etc/systemd/system）
# ⚠️ 只适用于 systemd 系统（WSL 需要已启用 systemd：/etc/wsl.conf 里
#    [boot] systemd=true，然后 `wsl --shutdown` 重启）

set -e

SERVICE=termetron
UNIT=/etc/systemd/system/$SERVICE.service
DIR=$(cd "$(dirname "$0")/.." && pwd)

if [ "$1" = "--uninstall" ]; then
    echo "[termetron] stopping and removing the service..."
    sudo systemctl stop "$SERVICE" 2>/dev/null || true
    sudo systemctl disable "$SERVICE" 2>/dev/null || true
    sudo rm -f "$UNIT"
    sudo systemctl daemon-reload
    echo "[termetron] removed."
    exit 0
fi

# 检查 systemd 是否可用
if ! command -v systemctl >/dev/null 2>&1; then
    echo "[termetron] error: systemctl not found (this is not a systemd system)." >&2
    echo "[termetron]   On WSL, add these lines to /etc/wsl.conf:" >&2
    echo "[termetron]     [boot]" >&2
    echo "[termetron]     systemd=true" >&2
    echo "[termetron]   then run: wsl --shutdown" >&2
    exit 1
fi

if ! systemctl is-system-running >/dev/null 2>&1; then
    echo "[termetron] warning: systemd does not seem to be running (systemctl is-system-running failed)." >&2
    echo "[termetron]   Continuing, but the service may fail to start." >&2
fi

# 确保 termetron 可执行且是 LF 换行
chmod +x "$DIR/termetron"
sed -i 's/\r$//' "$DIR/termetron" 2>/dev/null || true

# ── 安装可选依赖 ────────────────────────────────────────────────────────
# 为什么放在这里：用户装 termetron 时最不想做的事就是"再一个个 pip install"。
# 核心功能只用标准库，所以**装不上也继续**（只提示缺什么），绝不因为
# 一个可选包而让整个安装失败。
#
# ⚠️ Ubuntu/Debian 的系统 Python 受 PEP 668 保护，全局 pip 会被拒绝。
#    按「发行版包 → 用户级 pip」的顺序尝试，都不用 --break-system-packages
#    （那会弄坏系统环境，绝对不能做）。
install_optional_deps() {
    if python3 -c "import qrcode" >/dev/null 2>&1; then
        echo "[termetron] deps: qrcode already available"
        return 0
    fi
    echo "[termetron] deps: installing qrcode (for the scannable QR)..."

    # 1) 发行版打包版最省事，也不需要 sudo 之外的配合
    if command -v apt-get >/dev/null 2>&1; then
        if sudo apt-get install -y python3-qrcode >/dev/null 2>&1; then
            echo "[termetron] deps: installed via apt (python3-qrcode)"
            return 0
        fi
    fi
    # 2) 用户级 pip（--user 不需要 PEP 668 豁免）
    if python3 -m pip install --user -r "$DIR/requirements.txt" >/dev/null 2>&1; then
        echo "[termetron] deps: installed via pip --user"
        return 0
    fi

    echo "[termetron] deps: could not install qrcode automatically." >&2
    echo "[termetron]   Everything still works; 'termetron remote' will print the" >&2
    echo "[termetron]   address as text instead of a QR code." >&2
    echo "[termetron]   To enable the QR:  python3 -m pip install --user qrcode" >&2
    return 0
}
install_optional_deps

echo "[termetron] installing to $UNIT"
echo "[termetron] working directory: $DIR"

sed -e "s|^WorkingDirectory=.*|WorkingDirectory=$DIR|" \
    -e "s|^ExecStart=.*|ExecStart=$DIR/termetron --port 8899|" \
    "$DIR/deploy/termetron.service" | sudo tee "$UNIT" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
sudo systemctl restart "$SERVICE"

sleep 2
echo ""
echo "[termetron] status:"
sudo systemctl --no-pager --lines=0 status "$SERVICE" || true

echo ""
echo "[termetron] recent logs:"
sudo journalctl -u "$SERVICE" --no-pager --lines=12 || true

echo ""
IP=$(python3 "$DIR/tools/ts_ip.py" 2>/dev/null || true)
if [ -n "$IP" ]; then
    echo "[termetron] On your phone: set Tailscale IP to $IP, port 8899"
else
    echo "[termetron] No Tailscale IP detected - make sure tailscale is signed in."
fi

echo ""
echo "Useful commands:"
echo "  sudo systemctl status $SERVICE      # status"
echo "  sudo journalctl -u $SERVICE -f      # follow logs"
echo "  sudo systemctl restart $SERVICE     # restart"
echo "  ./deploy/install-service.sh --uninstall  # remove"
