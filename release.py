"""Termetron 一键发布：版本号同步 + 推送 GitHub + 触发 CI 自动 build & release.

流程：
  1. 读当前版本 —— 单一来源 = README.md 的 `**Version:** vX.Y.Z`；
     校验并同步 app/package.json 的 "version"（App 版本镜像服务端）。
  2. 递增版本（--bump patch|minor|major，默认 patch）或用 --version 显式指定。
  3. 同步写入 README.md 与 app/package.json。
  4. git commit + push origin main → 触发 GitHub Actions build-apk。
  5. CI 构建成功后自动发布 GitHub Release v<version> + app-debug.apk
     （App 内更新的更新源，查 /releases/latest）。
     —— 注意：CI 检测到远程已有同名 tag 会跳过发布，所以**不要**本地打 tag，
        让 CI 的 action-gh-release 创建 tag + release。
  6. --wait：推送后轮询 GitHub Releases API，直到 v<version> 出现（= 构建完成）。

用法：
    python lib/termetron/release.py                 # patch +1 并推送
    python lib/termetron/release.py --bump minor
    python lib/termetron/release.py --version 0.5.0
    python lib/termetron/release.py --message "fix tunnel watchdog"  # 提交说明
    python lib/termetron/release.py --no-push       # 只改版本号不推
    python lib/termetron/release.py --wait          # 推送后等 release 出现
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
README = os.path.join(ROOT, "README.md")
PKG = os.path.join(ROOT, "app", "package.json")
REPO = "EliasZWC/Termetron"
_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases?per_page=1"
_VER_RE = re.compile(r"(\*\*Version:\*\* v)(\d+)\.(\d+)\.(\d+)")
_BRANCH = "main"


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _read_version() -> tuple[str, str, str, str]:
    """返回 (readme_ver, pkg_ver, full_text, ...) 或抛错。"""
    with open(README, encoding="utf-8") as f:
        rm = f.read()
    m = _VER_RE.search(rm)
    if not m:
        sys.exit(f"[error] cannot find `**Version:** vX.Y.Z` in {README}")
    rv = f"{m.group(2)}.{m.group(3)}.{m.group(4)}"
    with open(PKG, encoding="utf-8") as f:
        pv = json.load(f).get("version")
    if pv != rv:
        print(f"[warn] version mismatch: README={rv} package.json={pv} (will sync to {rv})")
    return rv, pv, rm


def _bump(ver: str, part: str) -> str:
    a, b, c = (int(x) for x in ver.split("."))
    if part == "major":
        return f"{a + 1}.0.0"
    if part == "minor":
        return f"{a}.{b + 1}.0"
    return f"{a}.{b}.{c + 1}"


def _write_version(rm_text: str, new: str) -> None:
    rm_new = _VER_RE.sub(lambda m: f"{m.group(1)}{new}", rm_text, count=1)
    with open(README, "w", encoding="utf-8") as f:
        f.write(rm_new)
    with open(PKG, encoding="utf-8") as f:
        pkg = json.load(f)
    pkg["version"] = new
    with open(PKG, "w", encoding="utf-8", newline="\n") as f:
        json.dump(pkg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _release_exists(tag: str) -> bool:
    try:
        with _opener().open(_RELEASE_API, timeout=15) as r:
            rels = json.loads(r.read().decode())
        return bool(rels and rels[0].get("tag_name") == tag)
    except Exception:  # noqa: BLE001
        return False


def _wait_release(tag: str, timeout: float) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _release_exists(tag):
            return True
        time.sleep(10)
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Termetron 一键发布（push 触发 CI build + release）")
    ap.add_argument("--bump", choices=["patch", "minor", "major"], default="patch")
    ap.add_argument("--version", default=None, help="显式指定新版本 X.Y.Z")
    ap.add_argument("--message", default="release", help="commit 说明（默认 'release'）")
    ap.add_argument("--no-push", action="store_true", help="只改版本号，不推 GitHub")
    ap.add_argument("--wait", action="store_true", help="推送后等 v<new> release 出现")
    ap.add_argument("--timeout", type=float, default=900.0, help="--wait 超时秒数（默认 900）")
    args = ap.parse_args()

    t0 = time.time()
    print(f"[1/5] Reading current version (single source = README)")
    rv, pv, rm_text = _read_version()

    new = args.version or _bump(rv, args.bump)
    print(f"       {rv} -> {new}   (bump={args.bump})")
    if new == rv:
        sys.exit("[error] target version == current; use --bump or --version")

    print("[2/5] Syncing README + app/package.json")
    _write_version(rm_text, new)

    print("[3/5] git add + commit")
    r = _git("add", "-A")
    if r.returncode != 0:
        sys.exit(f"[error] git add failed:\n{r.stderr}")
    r = _git("commit", "-m", f"v{new}: {args.message}")
    if r.returncode != 0:
        print(f"       (commit skipped: {r.stderr.strip() or 'nothing to commit'})")

    if args.no_push:
        print(f"[done] version bumped to v{new} locally (no push). Push later to release.")
        return

    print("[4/5] git push origin main -> triggers GitHub Actions build-apk")
    r = _git("push", "origin", _BRANCH)
    if r.returncode != 0:
        sys.exit(f"[error] push failed:\n{r.stderr}")
    print(f"       pushed; CI will build & auto-publish release v{new} (tag must not exist)")

    if args.wait:
        print(f"[5/5] Waiting for release v{new} on {REPO} (timeout {args.timeout:.0f}s) ...")
        ok = _wait_release(f"v{new}", args.timeout)
        if ok:
            print(f"       [ok] Release v{new} published — App in-app update source ready")
        else:
            print("       [timeout] release not seen yet; check GitHub Actions")
            sys.exit(1)
    else:
        print("[5/5] (skip --wait) — run `python lib/termetron/release.py --wait` to watch")

    print(f"[done] elapsed {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
