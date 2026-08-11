import * as vscode from 'vscode';
import { spawn, ChildProcess } from 'child_process';
import * as path from 'path';
import * as net from 'net';
import * as fs from 'fs';
import * as os from 'os';

const SERVER_PY = 'quant_terminal.py';
const PREFERRED_PORT = 8900;

let serverProc: ChildProcess | null = null;
let serverPort = PREFERRED_PORT;
let panel: vscode.WebviewPanel | undefined;

/**
 * Find a free TCP port: try the preferred port, fall back to an OS-assigned one
 * if it is already taken (e.g. a manually-started Termetron on 8900).
 */
function findFreePort(preferred: number): Promise<number> {
  return new Promise((resolve) => {
    const tryListen = (port: number) => {
      const srv = net.createServer();
      srv.once('error', () => tryListen(0)); // fall back to OS-assigned
      srv.listen(port, '127.0.0.1', () => {
        const addr = srv.address() as net.AddressInfo;
        srv.close(() => resolve(addr.port));
      });
    };
    tryListen(preferred);
  });
}

/**
 * Start the Termetron Python server (quant_terminal.py) as a child process.
 * Returns the port it is listening on.
 */
const DIAG = path.join(os.tmpdir(), 'termetron-ext.log');
function dlog(msg: string): void {
  try {
    fs.appendFileSync(DIAG, `${new Date().toISOString()} ${msg}\n`);
  } catch {
    // ignore
  }
}

async function startServer(): Promise<number> {
  if (serverProc && serverProc.exitCode === null) {
    return serverPort;
  }
  // The Python server is bundled next to the compiled JS at out/server/quant_terminal.py
  // (copied by `npm run copy-server`). cwd = current workspace if open, else ext dir.
  const root = path.join(__dirname, '..');
  const py = path.join(__dirname, 'server', SERVER_PY);
  const wf = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  const cwd = wf ?? root;
  serverPort = await findFreePort(PREFERRED_PORT);
  // quant_terminal.py takes only --port/--host (it never opens a browser;
  // --no-open is a termetron.py launcher arg). Don't pass it.
  const p = spawn('python', [py, '--port', String(serverPort)], {
    cwd,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  p.stdout?.on('data', (d: Buffer) => console.log('[termetron]', d.toString().trim()));
  p.stderr?.on('data', (d: Buffer) => console.log('[termetron]', d.toString().trim()));
  p.on('error', (e: Error) => {
    console.error('[termetron] spawn error:', e.message);
    if (serverProc === p) {
      serverProc = null;
    }
  });
  p.on('exit', () => {
    if (serverProc === p) {
      serverProc = null;
    }
  });
  serverProc = p;
  return serverPort;
}

function stopServer(): void {
  if (serverProc) {
    try {
      serverProc.kill();
    } catch {
      // ignore
    }
    serverProc = null;
  }
}

/**
 * Wait until the Python server responds (GET /api/sessions returns ok).
 * spawn() returns before the server starts listening; without this the first
 * fetch would hit ECONNREFUSED.
 */
async function waitReady(port: number): Promise<boolean> {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/sessions`);
      if (r.ok) {
        return true;
      }
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
}

/**
 * Open Termetron in the system default browser. This is the primary UX: the
 * real web app runs natively — no webview CSP/bridge/device hacks, so it is
 * fast, fills the window, and the pairing prompt + desktop detection behave
 * exactly as in any browser.
 */
async function openBrowser(): Promise<void> {
  const port = await startServer();
  dlog('openBrowser port=' + port);
  if (!(await waitReady(port))) {
    dlog('openBrowser: server not ready');
    vscode.window.showWarningMessage('Termetron server did not start in time.');
    return;
  }
  const url = `http://127.0.0.1:${port}`;
  dlog('openBrowser url=' + url);
  await vscode.env.openExternal(vscode.Uri.parse(url));
}

/**
 * Open Termetron inside a VS Code panel as an embedded browser: a webview that
 * renders the REAL Termetron page via an <iframe> + webview portMapping.
 * portMapping transparently routes localhost:port inside the webview to the
 * Python server through the extension host, so the page runs natively: hostname
 * stays 'localhost' (desktop mode + pairing prompt + layout all work) and there
 * is no postMessage bridge, no CSP injection, no device mocking — just the real
 * page, like the built-in browser.
 */
async function openPanel(): Promise<void> {
  if (panel) {
    panel.reveal();
    return;
  }
  const port = await startServer();
  if (!(await waitReady(port))) {
    dlog('panel: server not ready');
    vscode.window.showWarningMessage('Termetron server did not start in time.');
    return;
  }
  panel = vscode.window.createWebviewPanel(
    'termetron',
    'Termetron',
    vscode.ViewColumn.One,
    {
      enableScripts: true,
      // localhost:port inside the webview (and its iframe) is resolved to the
      // Python server via the extension host — the supported way for a webview
      // to load a local service.
      portMapping: [{ webviewPort: port, extensionHostPort: port }],
    },
  );
  panel.onDidDispose(() => {
    panel = undefined;
  });
  // Shell page: an iframe pointing at localhost:port. CSP only allows that
  // frame source (and inline styles); the shell has a tiny bridge script that
  // forwards "open in browser" requests from the embedded page to the host.
  panel.webview.html = `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; frame-src http://localhost:${port}; style-src 'unsafe-inline';">
<title>Termetron</title>
<style>html,body{margin:0;padding:0;width:100%;height:100%;overflow:hidden;background:#0a0e14}
iframe{display:block;width:100%;height:100%;border:none}</style>
</head><body><iframe src="http://localhost:${port}" allow="clipboard-read; clipboard-write"></iframe>
<script>
// Bridge: the embedded iframe can't open external windows, so it posts a
// message up to this shell page; forward it to the extension host, which calls
// vscode.env.openExternal to open the system default browser.
window.addEventListener('message', function (e) {
  var d = e.data;
  if (d && d.kind === 'termetron:openExternal' && d.url) {
    try { acquireVsCodeApi().postMessage({ command: 'openExternal', url: d.url }); } catch (err) { /* ignore */ }
  }
});
</script>
</body></html>`;
  dlog('panel iframe http://localhost:' + port);

  // Open-in-browser requests from the embedded page → system default browser.
  panel.webview.onDidReceiveMessage((msg: any) => {
    if (msg && msg.command === 'openExternal' && msg.url) {
      dlog('openExternal ' + msg.url);
      void vscode.env.openExternal(vscode.Uri.parse(msg.url));
    }
  });
}

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand('termetron.open', () => {
      void openPanel();
    }),
    vscode.commands.registerCommand('termetron.openBrowser', () => {
      void openBrowser();
    }),
    vscode.commands.registerCommand('termetron.restart', () => {
      stopServer();
      void openPanel();
    }),
    vscode.commands.registerCommand('termetron.stop', () => {
      stopServer();
    }),
  );
}

export function deactivate(): void {
  stopServer();
}
