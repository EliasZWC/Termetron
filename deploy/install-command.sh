#!/usr/bin/env sh
# 把 termetron 装成可直接调用的命令（**不需要 sudo**）
#
# 做法：在 ~/.local/bin 建软链接，并把该目录加进 PATH。
# 好处：
#   · 不需要 root 密码
#   · 不会开机自启（只是一个命令，不运行任何东西）
#   · 想停就 Ctrl+C，关了之后手机端立刻连不上
set -e

BIN="$HOME/.local/bin"
TARGET="/mnt/e/product/Termetron/termetron"

mkdir -p "$BIN"
ln -sf "$TARGET" "$BIN/termetron"
chmod +x "$TARGET"
echo "[ok] symlink created: $BIN/termetron -> $TARGET"

# 把 ~/.local/bin 加进 PATH（写入 shell 启动文件，幂等）
add_path() {
    rc="$1"
    [ -f "$rc" ] || return 0
    if grep -q '.local/bin' "$rc" 2>/dev/null; then
        echo "[skip] $rc already contains ~/.local/bin"
        return 0
    fi
    printf '\n# user-level commands (termetron, ...)\ncase ":$PATH:" in\n    *":$HOME/.local/bin:"*) ;;\n    *) export PATH="$HOME/.local/bin:$PATH" ;;\nesac\n' >> "$rc"
    echo "[ok] updated $rc"
}

add_path "$HOME/.profile"
add_path "$HOME/.bashrc"

echo
echo "Done. Reopen your terminal (or run: source ~/.bashrc), then:"
echo "    termetron"
