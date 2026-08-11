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
  dlog('port=' + port);
  panel.webview.options = { enableScripts: true };

  // spawn() returns before the Python server starts listening; wait for it to
  // be ready (retry GET /api/sessions) before fetching the HTML — otherwise the
  // immediate fetch hits ECONNREFUSED and the panel stays blank.
  let ready = false;
  for (let i = 0; i < 60 && !ready; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/sessions`);
      if (r.ok) {
        ready = true;
      }
    } catch {
      // not up yet
    }
    if (!ready) {
      await new Promise((r) => setTimeout(r, 200));
    }
  }
  dlog('server ready=' + ready);
  if (!ready) {
    dlog('server not ready after retries');
    return;
  }

  // ---- postMessage-proxy approach (no iframe, no localhost loading) ----
  // This VS Code build blocks webview iframes from loading http://localhost
  // ('local-network-access'). Instead: fetch the server HTML in the extension
  // host (which CAN reach localhost), embed it as the webview content directly,
  // and proxy every front-end api() call back to the Python server through
  // webview postMessage. This fully sidesteps the localhost restriction.
  const resp = await fetch(`http://127.0.0.1:${port}/`);
  let html = await resp.text();
  dlog('fetch status=' + resp.status + ' len=' + html.length);
  if (!panel) {
    return;
  }
  // termtron HTML has no CSP meta, so VS Code would inject a strict default
  // CSP (default-src 'none' without script-src) that blocks ALL inline JS.
  // Inject our own CSP: inline scripts allowed (everything is inline), and
  // connect-src limited to the local server in case any fetch bypasses api().
  const cspMeta = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; img-src data: https:; font-src data:; connect-src http://127.0.0.1:${port} http://localhost:${port};">`;
  html = html.replace('<head>', '<head>' + cspMeta);
  const bridge = `
<script>
(function(){
  // Force desktop mode: VS Code webview has no hover/coarse pointer, so termtron's
  // matchMedia('(hover: none) and (pointer: coarse)...') would mark it mobile and
  // show the tunnel-closed mask + small-screen layout. Mock to desktop semantics.
  (function(){
    var _orig = window.matchMedia ? window.matchMedia.bind(window) : null;
    var _stub = function(media, matches){ return { matches: !!matches, media: media, onchange: null,
      addListener: function(){}, removeListener: function(){},
      addEventListener: function(){}, removeEventListener: function(){},
      dispatchEvent: function(){ return false; } }; };
    window.matchMedia = function(query){
      if (/pointer\s*:\s*(coarse|none)|hover\s*:\s*none/.test(query)) return _stub(query, false);
      if (/hover\s*:\s*hover|pointer\s*:\s*fine/.test(query)) return _stub(query, true);
      return _orig ? _orig(query) : _stub(query, false);
    };
  })();
  var vscode = acquireVsCodeApi();
  var seq = 0;
  var pending = {};
  window.addEventListener('message', function(e){
    var m = e.data;
    if (m && m.__apiResp && pending[m.id]) {
      var p = pending[m.id]; delete pending[m.id];
      if (m.error) p.reject(new Error(m.error)); else p.resolve(m.data);
    }
  });
  window.__termetronBridge = function(path, opts){
    return new Promise(function(resolve, reject){
      var id = ++seq;
      pending[id] = { resolve: resolve, reject: reject };
      vscode.postMessage({ command:'api', id:id, path:path,
        method:(opts && opts.method) || 'GET',
        body: opts && opts.body });
    });
  };
})();
</script>`;
  html = html.replace('</head>', bridge + '</head>');
  html = html.replace(
    'async function api(path, opts) { const r = await fetch(path, opts); return r.json(); }',
    'async function api(path, opts) { return window.__termetronBridge(path, opts); }',
  );
  dlog('injected bridge=' + (html.includes('__termetronBridge')) +
       ' apiReplaced=' + (html.includes('window.__termetronBridge(path, opts)')) +
       ' csp=' + (html.includes('Content-Security-Policy')));
  panel.webview.html = html;

  // Proxy /api requests from the embedded front-end to the Python server.
  panel.webview.onDidReceiveMessage(async (msg: any) => {
    dlog('webview msg: ' + (msg && msg.command) + ' ' + (msg && msg.path));
    if (!msg || msg.command !== 'api') {
      return;
    }
    const url = `http://127.0.0.1:${serverPort}${msg.path}`;
    try {
      const init: any = { method: msg.method || 'GET' };
      if (msg.body) {
        init.headers = { 'Content-Type': 'application/json' };
        init.body = msg.body;
      }
      const r = await fetch(url, init);
      const ct = (r.headers.get('content-type') || '').toLowerCase();
      const data = ct.includes('json') ? await r.json() : await r.text();
      panel?.webview.postMessage({ __apiResp: true, id: msg.id, data });
    } catch (e: any) {
      panel?.webview.postMessage({ __apiResp: true, id: msg.id, data: {}, error: String(e) });
    }
  });
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
