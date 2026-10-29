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
    echo "[termetron] 停止并移除服务..."
    sudo systemctl stop "$SERVICE" 2>/dev/null || true
    sudo systemctl disable "$SERVICE" 2>/dev/null || true
    sudo rm -f "$UNIT"
    sudo systemctl daemon-reload
    echo "[termetron] 已移除。"
    exit 0
fi

# 检查 systemd 是否可用
if ! command -v systemctl >/dev/null 2>&1; then
    echo "[termetron] 错误：这个系统没有 systemctl（不是 systemd 系统）。" >&2
    echo "[termetron]   WSL 请先在 /etc/wsl.conf 里加：" >&2
    echo "[termetron]     [boot]" >&2
    echo "[termetron]     systemd=true" >&2
    echo "[termetron]   然后执行 wsl --shutdown 再进入。" >&2
    exit 1
fi

if ! systemctl is-system-running >/dev/null 2>&1; then
    echo "[termetron] 警告：systemd 似乎没在运行（systemctl is-system-running 失败）。" >&2
    echo "[termetron]   继续安装，但服务可能起不来。" >&2
fi

# 确保 termetron 可执行且是 LF 换行
chmod +x "$DIR/termetron"
sed -i 's/\r$//' "$DIR/termetron" 2>/dev/null || true

echo "[termetron] 安装到 $UNIT"
echo "[termetron] 工作目录: $DIR"

sed -e "s|^WorkingDirectory=.*|WorkingDirectory=$DIR|" \
    -e "s|^ExecStart=.*|ExecStart=$DIR/termetron --port 8899|" \
    "$DIR/deploy/termetron.service" | sudo tee "$UNIT" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
sudo systemctl restart "$SERVICE"

sleep 2
echo ""
echo "[termetron] 状态："
sudo systemctl --no-pager --lines=0 status "$SERVICE" || true

echo ""
echo "[termetron] 最近日志："
sudo journalctl -u "$SERVICE" --no-pager --lines=12 || true

echo ""
IP=$(python3 "$DIR/tools/ts_ip.py" 2>/dev/null || true)
if [ -n "$IP" ]; then
    echo "[termetron] 手机端设置里填：$IP  端口 8899"
else
    echo "[termetron] 未能检测到 Tailscale IP —— 请确认 tailscale 已登录。"
fi

echo ""
echo "常用命令："
echo "  sudo systemctl status $SERVICE      # 看状态"
echo "  sudo journalctl -u $SERVICE -f      # 跟踪日志"
echo "  sudo systemctl restart $SERVICE     # 重启"
echo "  ./deploy/install-service.sh --uninstall  # 卸载"
