# Termetron for VS Code

Embed the **Termetron** web terminal inside VS Code as a webview panel — multi-session
interactive shells with live tqdm progress bars, and **mobile remote access** over a
temporary Cloudflare tunnel (scan QR / enter one-time password / pair device).

## Features

- One panel embeds the full Termetron web UI — nothing is rewritten.
- Multi-session tabs, per-session history, Ctrl+C/Ctrl+L/Ctrl+K shortcuts.
- Live progress bar for long jobs (tqdm/step output parsed into a bottom bar).
- Mobile remote access: `termetron remote on` in the panel → scan QR on your phone.

## Usage

1. Install the `.vsix` (Terminal → Extensions → ⋯ → Install from VSIX).
2. Command Palette → **Termetron: Open Terminal**.
3. A panel opens with the terminal; the Python server is started automatically
   (free port; falls back if 8900 is taken by a manual instance).

Commands:

| Command | Action |
|---|---|
| Termetron: Open Terminal | open the embedded terminal panel |
| Termetron: Restart Server | kill the Python server and reopen |
| Termetron: Stop Server | stop the Python server |

## Extension API

Other extensions can drive Termetron programmatically:

```ts
const tmt = vscode.extensions.getExtension('eliaszhang.termetron')?.exports;
await tmt.open();                              // open the embedded terminal panel
await tmt.openInBrowser();                     // open in the system browser
await tmt.restart();                           // restart the server + reopen
await tmt.stop();                              // stop the server
const { running, port } = await tmt.getStatus(); // server state
await tmt.exec('shell', 'python run/run_demo.py'); // run a command in a session
```

Type declarations are provided in `api.d.ts` in the extension folder.

## Requirements

- **Python ≥ 3.10** on PATH (`quant_terminal.py` is stdlib-only).
- **cloudflared** for remote access — auto-downloaded on first `termetron remote on`.
- (Optional) `pip install qrcode` for the QR code.

## Build from source

```bash
cd vscode
npm install
npm run compile          # tsc -> out/
npx @vscode/vsce package # -> termetron-0.4.14.vsix
```

## Release

Version is synced with the Termetron repo (`../release.py`). Bump it the same way;
the CI workflow `.github/workflows/build-vsix.yml` builds and (with a publisher
token) publishes to the VS Code Marketplace.
