import * as vscode from 'vscode';
import { spawn, ChildProcess } from 'child_process';
import * as path from 'path';
import * as net from 'net';

const SERVER_PY = 'quant_terminal.py';
const PREFERRED_PORT = 8900;
// Port the webview uses internally (a dedicated port, NOT 8900 which may be a
// manually-started Termetron); mapped to the real server port via portMapping.
// portMapping only intercepts "localhost" — the iframe MUST use localhost, not 127.0.0.1.
const WEBVIEW_PORT = 9898;

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
 * Open the Termetron panel: a webview that embeds the full Termetron web UI
 * (multi-session terminal, progress bars, and mobile remote access via tunnel).
 */
async function openPanel(): Promise<void> {
  if (panel) {
    panel.reveal();
    return;
  }
  panel = vscode.window.createWebviewPanel(
    'termetron',
    'Termetron',
    vscode.ViewColumn.One,
    { enableScripts: true },
  );
  panel.onDidDispose(() => {
    panel = undefined;
  });

  const port = await startServer();
  if (!panel) {
    return;
  }
  // Port mapping: the webview loads http://localhost:<WEBVIEW_PORT>, which VS Code
  // transparently forwards to the actual extension-host server port. This is the
  // officially recommended way to embed a local web server in a webview.
  // NOTE: portMapping only intercepts the "localhost" host — use localhost, not
  // 127.0.0.1, in the iframe src.
  panel.webview.options = {
    enableScripts: true,
    portMapping: [{ webviewPort: WEBVIEW_PORT, extensionHostPort: port }],
  };
  const csp = panel.webview.cspSource;
  panel.webview.html = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy"
  content="default-src 'none'; frame-src ${csp} http://localhost:${WEBVIEW_PORT} http://127.0.0.1:${WEBVIEW_PORT}; style-src ${csp} 'unsafe-inline'; img-src ${csp} https: data:;">
<style>
  html,body{margin:0;padding:0;height:100%;background:#0a0e14}
  iframe{width:100%;height:100%;border:0;display:block}
</style>
</head>
<body><iframe src="http://localhost:${WEBVIEW_PORT}"></iframe></body>
</html>`;
}

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand('termetron.open', () => {
      void openPanel();
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
