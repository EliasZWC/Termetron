import * as vscode from 'vscode';
import { spawn, ChildProcess } from 'child_process';
import * as path from 'path';
import * as net from 'net';

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
  const p = spawn('python', [py, '--port', String(serverPort), '--no-open'], {
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
  // asExternalUri turns the local server into a URI the webview can load
  // (handles port mapping + CSP for us).
  const extUri = await vscode.env.asExternalUri(
    vscode.Uri.parse(`http://localhost:${port}`),
  );
  if (!panel) {
    return;
  }
  const csp = panel.webview.cspSource;
  panel.webview.html = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy"
  content="default-src 'none'; frame-src ${csp} https: vscode-webview:; style-src ${csp} 'unsafe-inline'; img-src ${csp} https: data:;">
<style>
  html,body{margin:0;padding:0;height:100%;background:#0a0e14}
  iframe{width:100%;height:100%;border:0;display:block}
</style>
</head>
<body><iframe src="${extUri}"></iframe></body>
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
