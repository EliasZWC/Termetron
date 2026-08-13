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
let panelPort: number | null = null; // port the panel is currently connected to

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

/** Info about a discovered local Termetron server. */
export interface ServerInfo {
  port: number;
  sessions: string[];
  own: boolean;
}

/** Probe one local port to see if a Termetron server is listening. */
async function probePort(port: number): Promise<ServerInfo | null> {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 2000);
    const r = await fetch(`http://127.0.0.1:${port}/api/sessions`, { signal: ctl.signal });
    clearTimeout(t);
    if (!r.ok) return null;
    const data = (await r.json()) as Record<string, unknown>;
    const sessions = Object.keys(data).filter(
      (k) => data[k] && typeof data[k] === 'object' && 'status' in (data[k] as object),
    );
    if (Object.keys(data).length === 0) return null; // not a Termetron server
    return { port, sessions, own: serverProc !== null && serverPort === port };
  } catch {
    return null;
  }
}

/**
 * Discover all local Termetron servers: the preferred port plus any
 * quant_terminal.py --port from running python processes.
 */
async function scanServers(): Promise<ServerInfo[]> {
  const ports = new Set<number>();
  ports.add(vscode.workspace.getConfiguration('termetron').get<number>('serverPort', PREFERRED_PORT));
  try {
    const script =
      process.platform === 'win32'
        ? 'powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \\"Name like \'python%\'\\" | Where-Object { $_.CommandLine -like \'*quant_terminal.py*\' } | ForEach-Object { $_.CommandLine }"'
        : 'ps -eo args | grep -i quant_terminal.py';
    const out = await new Promise<string>((resolve) => {
      const cp = spawn(script, { shell: true, windowsHide: true });
      let s = '';
      cp.stdout?.on('data', (d: Buffer) => (s += d.toString()));
      cp.stderr?.on('data', (d: Buffer) => (s += d.toString()));
      cp.on('close', () => resolve(s));
      setTimeout(() => { try { cp.kill(); } catch { /* ignore */ } }, 3000);
    });
    const re = /--port\s+(\d+)/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(out))) ports.add(Number(m[1]));
  } catch { /* ignore */ }
  const found: ServerInfo[] = [];
  for (const p of ports) {
    const info = await probePort(p);
    if (info) found.push(info);
  }
  return found.sort((a, b) => a.port - b.port);
}

/**
 * Create (or recreate) the embedded-browser webview panel connected to the
 * Termetron server on `port` (must already be running). If a panel exists it is
 * disposed and recreated so the portMapping points at the chosen server.
 */
async function openPanelAt(port: number): Promise<void> {
  if (panel) {
    panel.dispose();
    panel = undefined;
  }
  if (!(await waitReady(port))) {
    dlog('openPanelAt: server not ready on ' + port);
    vscode.window.showWarningMessage(`Termetron: no server on port ${port}.`);
    return;
  }
  panel = vscode.window.createWebviewPanel(
    'termetron',
    'Termetron',
    vscode.ViewColumn.One,
    {
      enableScripts: true,
      // localhost:port inside the webview (and its iframe) is resolved to the
      // server via the extension host — the supported way for a webview to load
      // a local service.
      portMapping: [{ webviewPort: port, extensionHostPort: port }],
    },
  );
  panelPort = port;
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
  } else if (d && d.kind === 'termetron:connect') {
    try { acquireVsCodeApi().postMessage({ command: 'connect' }); } catch (err) { /* ignore */ }
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
    } else if (msg && msg.command === 'connect') {
      dlog('menu: connect to server');
      void connectToServer();
    }
  });
}

/**
 * Open Termetron: reuse an existing server on the preferred port if one is
 * running, otherwise start the extension's own server.
 */
async function openPanel(): Promise<void> {
  if (panel) {
    panel.reveal();
    return;
  }
  const preferred = vscode.workspace.getConfiguration('termetron').get<number>('serverPort', PREFERRED_PORT);
  const existing = await probePort(preferred);
  const port = existing ? preferred : await startServer();
  await openPanelAt(port);
}

/** Command: pick a local Termetron server (or start a new one) to open. */
async function connectToServer(): Promise<void> {
  const servers = await scanServers();
  const items: vscode.QuickPickItem[] = servers.map((s) => ({
    label: `Port ${s.port}${s.port === panelPort ? ' (current)' : ''}`,
    description: `${s.sessions.length} session(s)${s.own ? ' · this extension' : ''}`,
  }));
  items.push({ label: 'Start new server', description: `start a new instance on ${PREFERRED_PORT}` });
  const pick = await vscode.window.showQuickPick(items, { placeHolder: 'Choose a Termetron server' });
  if (!pick) return;
  if (pick.label === 'Start new server') {
    await openPanel();
    return;
  }
  await openPanelAt(Number(pick.label.replace('Port ', '')));
}

/** Termetron VS Code extension public API. Obtained by other extensions via
 *  vscode.extensions.getExtension('eliaszhang.termetron')?.exports. */
export interface TermetronApi {
  /** Open the embedded terminal panel (starts the Python server if needed). */
  open(): Promise<void>;
  /** Open Termetron in the system default browser. */
  openInBrowser(): Promise<void>;
  /** Restart the Python server and reopen the panel. */
  restart(): Promise<void>;
  /** Stop the Python server. */
  stop(): Promise<void>;
  /** Current server state. */
  getStatus(): Promise<{ running: boolean; port: number | null }>;
  /** The port the server is listening on (or null if not running). */
  getPort(): Promise<number | null>;
  /** Send a command to a session (created on demand); output appears in the panel. */
  exec(session: string, command: string): Promise<{ ok: boolean; error?: string }>;
  /** Discover all local Termetron servers. */
  listServers(): Promise<ServerInfo[]>;
  /** Open the panel connected to the given local port (server must be running). */
  connect(port: number): Promise<void>;
}

/** Post a command to a Termetron session via the local server API. */
async function execInSession(session: string, command: string): Promise<{ ok: boolean; error?: string }> {
  if (!serverProc || serverProc.exitCode !== null) {
    return { ok: false, error: 'Termetron server is not running; call open() first' };
  }
  const base = `http://127.0.0.1:${serverPort}`;
  try {
    // ensure the session exists (input returns 404 otherwise)
    const list = (await (await fetch(base + '/api/sessions')).json()) as Record<string, unknown>;
    if (!Object.prototype.hasOwnProperty.call(list, session)) {
      await fetch(base + '/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: session, cmd: null }),
      });
    }
    const r = await fetch(base + '/api/sessions/' + encodeURIComponent(session) + '/input', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: command }),
    });
    if (!r.ok) {
      return { ok: false, error: 'input failed: HTTP ' + r.status };
    }
    return { ok: true };
  } catch (e: any) {
    return { ok: false, error: String(e) };
  }
}

export function activate(context: vscode.ExtensionContext): TermetronApi {
  context.subscriptions.push(
    vscode.commands.registerCommand('termetron.open', () => {
      void openPanel();
    }),
    vscode.commands.registerCommand('termetron.openBrowser', () => {
      void openBrowser();
    }),
    vscode.commands.registerCommand('termetron.connect', () => {
      void connectToServer();
    }),
    vscode.commands.registerCommand('termetron.restart', () => {
      stopServer();
      void openPanel();
    }),
    vscode.commands.registerCommand('termetron.stop', () => {
      stopServer();
    }),
  );
  const running = () => serverProc !== null && serverProc.exitCode === null;
  return {
    open: () => openPanel(),
    openInBrowser: () => openBrowser(),
    restart: async () => {
      stopServer();
      await openPanel();
    },
    stop: () => {
      stopServer();
      return Promise.resolve();
    },
    getStatus: async () => ({ running: running(), port: running() ? serverPort : null }),
    getPort: async () => (running() ? serverPort : null),
    exec: (session, command) => execInSession(session, command),
    listServers: () => scanServers(),
    connect: (port) => openPanelAt(port),
  };
}

export function deactivate(): void {
  stopServer();
}
