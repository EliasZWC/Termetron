# Termetron

**Version:** v0.4.14

A geometric metron terminal — a standalone single-page web terminal for running
long background jobs with a live progress bar. Pure Python stdlib, zero
third-party dependencies. Ships with an Android companion app (`app/`) that
connects over a secure temporary tunnel for on-the-go access. The name fuses
Latin **Term** (terminal) and **Metron** (measure).

> **Version policy** — the version number lives here in `README.md`
> (`**Version:**`) as the single source of truth: the web UI About reads it from
> the server, and the mobile app mirrors it (`package.json` + Android
> `versionName`). Bump it on every iteration.

## Quick start

```bash
# from inside this folder (`cd lib/termetron`)
python termetron.py                     # starts the server, opens the default browser

# start without auto-opening a browser
python termetron.py --no-open

# custom port
python termetron.py --port 9000

# or run the raw server directly (default session `shell` auto-created)
python quant_terminal.py                # default port 8899
python quant_terminal.py --port 8900
```

Open the UI at **http://127.0.0.1:8899** (auto-opened unless `--open none`).

## Requirements

- **Python ≥ 3.10** (uses `X | None` union syntax) — stdlib only, no third-party
  packages required.
- **OS** — each session spawns the platform shell: `cmd.exe` on Windows (commands
  run in the ANSI codepage GBK), `/bin/sh` elsewhere. Windows is the primary
  tested platform; cloudflared auto-downloads the right binary for all three OSes.
- **cloudflared** (for `termetron remote on`): auto-downloaded on first use, kept in
  `bin/`.
- **qrcode** (optional): `pip install qrcode` enables the QR code; without it the
  URL + password are shown as text instead (still works).

## Files

| File               | Purpose                                                      |
|--------------------|--------------------------------------------------------------|
| `quant_terminal.py`| Web terminal server (browser UI + per-session cmd shells)    |
| `termetron_exec.py`| CLI to push a command into a terminal session from the shell |
| `termetron.py`     | One-shot launcher (`python termetron.py`, opens default browser) |
| `agent.py`         | Agent channel CLI (status/watch/wait — let an AI agent monitor sessions) |
| `release.py`       | One-click publish: bump version + push -> CI build -> auto Release |
| `app/`             | Android APK client (Capacitor WebView wrapper), repo subdir   |
| `app/assets/`      | App icon sources + `generate-icons.py` (Pillow)              |
| `vscode/`          | VS Code extension (embeds the termetron web UI, see its README) |
| `.github/workflows/build.yml` | CI: builds APK on push + auto-publishes Release (App update source) |
| `.github/workflows/build-vsix.yml` | CI: builds the VS Code extension (.vsix) |

## Features

- **Multi-session tabs** — each tab runs its own interactive `cmd.exe` shell;
  tab bar styled like a browser, active tab merges with the content area.
- **Progress bar** — tqdm / step progress (`\r` updates) is parsed into a
  bottom bar; stdout stays clean.
- **Idle input state** — when nothing is running the input line sits at the
  bottom and is auto-focused (keyboard-first, no mouse needed). While a job
  runs it shows `running ...` + a stop button in the same spot.
- **History** — ↑/↓ per-session command history; new sessions start empty.
- **Long input** — the textarea grows without limit; multi-line commands via
  Shift+Enter.
- **Clean output** — ANSI codes stripped; the shell prompt is rewritten to the
  the termetron style `name $ cmd` in history.
- **Responsive UI** — desktop (mouse) uses a tab bar; mobile (touch) uses a
  session directory page → full-screen session view. Device is detected by
  pointer capability, not screen width.
- **☰ dropdown menu** — MANAGE SESSIONS / MORE SETTINGS (full-screen settings
  page: font size, theme color, About).
- **⋯ session actions** — inside a mobile session view: rename / delete dropdown.
- **Theme & font** — accent color and output font size persist in `localStorage`.

## Responsive UI (desktop vs mobile)

Device mode is detected by **pointer capability** (`hover` / `pointer` media
queries), NOT screen width — a narrow desktop window still gets the desktop
layout, a touch phone always gets the mobile layout.

|                | Desktop (mouse)                 | Mobile (touch)                  |
|----------------|---------------------------------|---------------------------------|
| Session switch | tab bar (click to switch)       | session directory page → tap in |
| Top-right      | ☰ menu button                   | ☰ on directory page; ⋯ (rename/delete) inside a session view |
| Layout         | tabs + output + bottom bar      | full-screen session view with `‹` back |

Force a mode from any browser (useful for preview):
- `http://127.0.0.1:8899/?m=1` — mobile layout
- `http://127.0.0.1:8899/?d=1` — desktop layout

## Menu & settings

- **☰** (top-right) → dropdown:
  - **MANAGE SESSIONS** — list all sessions, delete each, create new
  - **MORE SETTINGS** — full-screen page: FONT SIZE (`A-`/`A+`), THEME color
    swatches, ABOUT
- **⋯** (inside a mobile session view) → dropdown: RENAME SESSION /
  DELETE SESSION (red)
- Font size & accent color persist in `localStorage` across reloads

## UI conventions

- Icon buttons are **pure SVG icons** — no box, border or background; hover
  only changes opacity.
- Headers: the `TERMETRON` logo uses the brand font (Segoe UI 800 +
  wide letter-spacing); session/settings titles use **Consolas bold**.
- Modals are responsive (`width:min(440px, 92vw)`) — never overflow narrow
  screens.
- Server sends `Cache-Control: no-store` — reloads always get the latest UI.
- App / favicon icon has built-in padding so the terminal glyph never touches
  the edges.

## Keyboard shortcuts

| Key        | Action                          |
|------------|---------------------------------|
| `Enter`    | submit command                  |
| `Shift+Enter` | newline inside input         |
| `↑` / `↓`  | command history                 |
| `Ctrl+C`   | interrupt current process (busy)|
| `Ctrl+L`   | clear current session output    |
| `Ctrl+K`   | new session                     |
| `Ctrl+1..9`| switch to the Nth session       |

## termetron commands (keyboard-only session management)

Type `termetron <command>` in the input line; anything starting with `termetron ` is handled
by the terminal and **never** sent to the shell. All other input goes straight
to the terminal (shell commands stay untouched).

| Command                     | Description                                   |
|-----------------------------|-----------------------------------------------|
| `termetron new <name>`      | create a session and switch to it             |
| `termetron use <name>`      | switch to a session                           |
| `termetron del [name]`      | delete a session (default: current)           |
| `termetron rename <old> <new>` | rename a session                           |
| `termetron ls`              | list sessions                                 |
| `termetron clear`           | clear current session output (alias: `cls`)   |
| `termetron stop`            | interrupt the current process                 |
| `termetron remote on`       | open a public tunnel + QR code for phone      |
| `termetron remote off`      | close tunnel (URL / password / pairings die)  |
| `termetron remote status`   | show tunnel / password / pending devices      |
| `termetron allow <key>`     | approve a phone's pairing request             |
| `termetron help`            | show this command list                        |

> Terminal commands (`python ...`, `ls`, `pip ...`) are sent to the shell as-is
> and **must not** be prefixed with `termetron`.

## Remote access (phone / away from desk)

Termetron is local-only by default (`127.0.0.1`). To reach it from your phone:

```
computer:  termetron remote on
           -> downloads cloudflared (once) -> opens a temp public HTTPS tunnel
           -> shows a QR code (URL only) + the one-time password, separately
phone:     open Termetron app (or browser) -> scan the QR (or paste URL)
           -> type the one-time password shown on the computer screen
           -> a device key appears on the phone
computer:  a PAIRING REQUEST pops up automatically -> verify the device key
           and click ALLOW (or run `termetron allow <key>` / DENY to refuse)
phone:     connected — full terminal, realtime
```

Security note: the QR code carries **only the URL** — the one-time password is
**never** embedded in it (it would leak the password to anyone who screenshots
or forwards the QR). The password is shown separately on the computer screen
and typed by hand on the phone, so the URL and the password travel through two
independent channels.

Session persistence: the phone stays connected across screen-off / re-open
until the computer runs `termetron remote off` — no auto-logout, no re-pairing.

Security model (three gates):

| Gate   | What stops an attacker                                |
|--------|-------------------------------------------------------|
| URL    | temp tunnel; gone after `termetron remote off`               |
| 1-time password | random per session; dies with the tunnel      |
| pairing | `termetron allow` is a human approval on the computer — even with URL+password leaked, an attacker cannot complete pairing |

Local access (`127.0.0.1`) needs no login; remote (via tunnel) needs all three.

## Mobile app

An Android APK (Capacitor WebView wrapper) is in `app/` — standalone icon,
QR scanning, no native code. Build steps in `app/README.md`. iOS requires a Mac
+ Apple developer account (defer until needed).

> **Open source**: this whole repo (server + `app/`) lives at
> `github.com/EliasZWC/Termetron`. `app/android-debug.p12` is the CI debug
> signing key — all published APKs are signed with it, don't replace it casually.

## Deployment / distribution

Termetron is a single self-contained Python file (stdlib only) — hand it to a
classmate and they run it on their own machine:

```bash
python quant_terminal.py                 # each machine = own instance (default port 8899)
```

- **Zero maintenance for you**: every instance is independent; no central server.
- **Optional QR**: `pip install qrcode` enables the QR code; without it the URL
  + password are shown as text instead (still works).
- **cloudflared**: auto-downloaded on first `termetron remote on` (per OS), kept in
  `bin/` (not committed — ~50MB, fetched on demand).

## Release (publish a new version)

One command bumps the version and runs the whole release pipeline — CI builds
the APK and auto-publishes a GitHub Release (the App's in-app update source):

```bash
python release.py --wait        # patch +1 (0.4.14 -> 0.4.15), push, wait for Release
python release.py --bump minor  # 0.4.14 -> 0.5.0
python release.py --version 0.5.0
python release.py --no-push     # only bump locally
```

How it works (details in `release.py`):

- **Version single-source**: `**Version:**` in this README; it is synced into
  `app/package.json` (the App version mirrors it).
- `release.py` bumps the version, commits, and pushes `main` → GitHub Actions
  `build-apk` runs → on success `action-gh-release` publishes
  `Release v<version>` with `app-debug.apk`.
- **Do NOT create the git tag locally**: the CI skips publishing if the tag
  already exists on the remote. Let CI create tag + release (avoids the
  "build ok but no release" gotcha).

## Troubleshooting

| Symptom                             | Fix                                    |
|-------------------------------------|----------------------------------------|
| `termetron remote on` stuck at "starting"  | wait a few seconds; check network to github.com (cloudflared download / Cloudflare edge) |
| QR not showing                      | `pip install qrcode`; restart Termetron |
| Phone says "auth failed"            | password wrong or tunnel expired — run `termetron remote off` then `termetron remote on` again |
| Pairing not approved                | on the computer run `termetron allow <key>` with the key the phone shows |
| Port in use                         | `--port <other>`; update phone URL     |

## Pushing commands from the terminal

```bash
# fire a command into session `demo` and return immediately
python termetron_exec.py demo "python run/run_demo.py"

# push and echo the session output locally for 20s (short jobs)
python termetron_exec.py --watch demo "python run/run_demo.py"

# custom port (default 8899)
python termetron_exec.py --port 8900 demo "python run/run_demo.py"
```

Sessions are created on demand; a long task can be started here and watched
from the browser.

## Agent channel (let an AI agent monitor)

The server already exposes session state and output over HTTP (`/api/sessions`:
lines[-600] / progress / busy / script / cmd / updated). `agent.py` is a small CLI
an AI agent (e.g. GitHub Copilot) can call to see what's running — no screen
sharing needed:

```bash
python agent.py status                    # all sessions: busy/script/cmd/progress/tail
python agent.py status --auto             # auto-detect server ports (incl. the VS Code ext)
python agent.py watch shell --lines 50    # tail a session's output
python agent.py wait shell --timeout 900  # block until a session is idle
```

## Notes

- Port defaults to `8899`; change with `--port`.
- Sessions live in memory only — restarting the server clears them; start
  long jobs again after a restart (jobs are resumable by design).
- Chinese text: commands are sent to `cmd.exe` in the ANSI codepage (GBK);
  do not run `chcp 65001` (breaks pipe decoding).
