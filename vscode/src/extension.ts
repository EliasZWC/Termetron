import * as vscode from 'vscode';
import { spawn, execFile, ChildProcess } from 'child_process';
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
// Two-way bridge: the embedded iframe talks to the extension through this shell.
//  - iframe -> shell -> extension: openExternal / req (id, cmd, payload)
//  - extension -> shell -> iframe: __reqResp responses
var __vs = acquireVsCodeApi(); // acquireVsCodeApi may only be called once
window.addEventListener('message', function (e) {
  var d = e.data;
  var iframe = document.querySelector('iframe');
  var fromIframe = iframe && e.source === iframe.contentWindow;
  if (fromIframe) {
    if (d && d.kind === 'termetron:openExternal' && d.url) {
      try { __vs.postMessage({ command: 'openExternal', url: d.url }); } catch (err) { /* ignore */ }
    } else if (d && d.kind === 'termetron:req') {
      try { __vs.postMessage({ command: 'req', id: d.id, cmd: d.cmd, payload: d.payload }); } catch (err) { /* ignore */ }
    }
  } else if (d && d.__reqResp && iframe) {
    iframe.contentWindow.postMessage(d, '*');
  }
});
</script>
</body></html>`;
  dlog('panel iframe http://localhost:' + port);

  // Open-in-browser requests from the embedded page → system default browser.
  panel.webview.onDidReceiveMessage(async (msg: any) => {
    if (msg && msg.command === 'openExternal' && msg.url) {
      dlog('openExternal ' + msg.url);
      void vscode.env.openExternal(vscode.Uri.parse(msg.url));
    } else if (msg && msg.command === 'req') {
      dlog('req ' + msg.cmd);
      let data: any = { error: 'unknown command ' + msg.cmd };
      try {
        switch (msg.cmd) {
          case 'listServers':
            data = await scanServers();
            break;
          case 'startServer':
            data = { port: await startServer() };
            break;
          case 'stopServer': {
            const p = msg.payload && msg.payload.port;
            if (typeof p === 'number' && p === serverPort && serverProc && serverProc.exitCode === null) {
              stopServer();
              data = { ok: true };
            } else {
              data = { ok: false, error: 'not managed by this extension' };
            }
            break;
          }
          case 'connectServer': {
            const p = Number(msg.payload && msg.payload.port);
            await openPanelAt(p);
            data = { ok: true, port: p };
            break;
          }
        }
      } catch (e: any) {
        data = { error: String(e) };
      }
      try { panel?.webview.postMessage({ __reqResp: true, id: msg.id, data }); } catch (err) { /* ignore */ }
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

// ---- Agent 桥：扫描所有本地 termetron 服务器上的 agent 会话 → Copilot → 回复 ----
const AGENT_POLL_MS = 2500;
let agentPollTimer: NodeJS.Timeout | null = null;
const agentBusy = new Set<string>();  // "port:name" 正在处理（防并发重复）
let extContext: vscode.ExtensionContext | null = null;  // 保存 context（Copilot 授权 prime 用）
let primeInFlight = false;  // 防并发 prime

const AGENT_DEFAULT_PROMPT =
  'You are Termetron\'s remote agent. The user is talking to you through the ' +
  'Termetron terminal UI (possibly from a mobile phone over a tunnel). You run on ' +
  'their desktop inside VS Code with GitHub Copilot. Help with code, quant/trading ' +
  'strategies, Python, math, and general tech. Keep replies concise and actionable.';

/** Start the background poller that turns agent-session messages into Copilot replies. */
function startAgentBridge(context: vscode.ExtensionContext): void {
  agentPollTimer = setInterval(() => { void pollAgent(); }, AGENT_POLL_MS);
  context.subscriptions.push({
    dispose: () => { if (agentPollTimer) { clearInterval(agentPollTimer); agentPollTimer = null; } },
  });
  dlog('agent bridge started (poll ' + AGENT_POLL_MS + 'ms)');
}

/** 收集所有候选端口：配置端口 + 面板端口 + 扩展自己的服务器；每 ~30s 全量扫描补充一次。 */
let lastFullScan = 0;
async function collectPorts(): Promise<Set<number>> {
  const ports = new Set<number>();
  ports.add(vscode.workspace.getConfiguration('termetron').get<number>('serverPort', 8900));
  if (panelPort) ports.add(panelPort);
  if (serverProc && serverProc.exitCode === null) ports.add(serverPort);
  // 全量扫描（spawn powershell）较慢且可能卡住：限制频率 + 超时兜底
  const now = Date.now();
  if (now - lastFullScan > 30000) {
    lastFullScan = now;
    try {
      const srv = await Promise.race([
        scanServers(),
        new Promise<ServerInfo[] | null>((res) => setTimeout(() => res(null), 3000)),
      ]);
      if (srv) for (const s of srv) ports.add(s.port);
    } catch { /* ignore */ }
  }
  return ports;
}

/** 轮询所有服务器上所有 agent 会话；有待回复消息则调 Copilot 并写回。 */
async function pollAgent(): Promise<void> {
  const ports = await collectPorts();
  for (const port of ports) {
    await pollPort(port);
  }
}

/** 扫一个服务器上的全部 agent 会话，处理待回复消息。 */
/**
 * Copilot 首次授权（vscode.lm consent）提前到“创建 agent 会话后”弹出，
 * 而不是首次发消息时：poll 到任意 agent 会话且未授权（canSendRequest 为
 * undefined）就发一次 priming 请求触发同意框；用户允许后固化，之后不再弹。
 */
async function ensureCopilotPrimed(): Promise<void> {
  if (primeInFlight || !extContext) return;
  const lm = (vscode as any).lm;
  if (!lm || typeof lm.selectChatModels !== 'function') return;
  let model: any = null;
  try {
    const models = await lm.selectChatModels({ vendor: 'copilot' });
    model = models && models[0];
  } catch {
    return;
  }
  if (!model) return;
  const acc = (extContext as any).languageModelAccessInformation;
  // undefined = 尚未询问过授权 → 触发一次 priming（弹同意框）；true/false 已定，不再弹
  if (!acc || acc.canSendRequest(model) !== undefined) return;
  primeInFlight = true;
  try {
    const msgs = [vscode.LanguageModelChatMessage.User('Reply with a single word: OK')];
    const token = new vscode.CancellationTokenSource().token;
    const resp = await model.sendRequest(msgs, {
      justification: 'Termetron agent: pre-authorize Copilot access when you create an agent session.',
    }, token);
    for await (const chunk of resp.text) { /* drain */ }
    dlog('agent copilot primed (consent granted)');
  } catch (e: any) {
    dlog('agent copilot prime failed: ' + (e && e.message ? e.message : String(e)));
  } finally {
    primeInFlight = false;
  }
}

async function pollPort(port: number): Promise<void> {
  let list: Record<string, any>;
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/sessions`);
    if (!r.ok) return;
    list = (await r.json()) as Record<string, any>;
  } catch {
    return;
  }
  for (const name of Object.keys(list)) {
    if ((list[name] || {}).kind !== 'agent') continue;
    const key = port + ':' + name;
    if (agentBusy.has(key)) continue;
    let snap: any;
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/output/${encodeURIComponent(name)}`);
      if (!r.ok) continue;
      snap = await r.json();
    } catch {
      continue;
    }
    if (!snap || snap.kind !== 'agent') continue;
    if (snap.pending && snap.busy) {
      const history: any[] = snap.messages || [];
      const last = history[history.length - 1];
      if (!last || last.role !== 'user') continue;  // 尾部须是待回复的 user 消息
      agentBusy.add(key);
      void (async () => {
        try {
          await replyAsAgent(port, name, history, snap.system_prompt || null);
        } finally {
          agentBusy.delete(key);
        }
      })();
    } else {
      // 无待回复消息：Copilot 尚未授权则 prime 一次（创建 agent 会话后即弹同意框）
      void ensureCopilotPrimed();
    }
  }
}

/** Ask Copilot (vscode.lm) for a reply and write it back to the agent session. */
async function replyAsAgent(port: number, name: string, history: any[], systemPrompt: string | null): Promise<void> {
  const fail = async (msg: string) => { try { await postReply(port, name, msg); } catch { /* ignore */ } };
  try {
    const lm = (vscode as any).lm;
    if (!lm || typeof lm.selectChatModels !== 'function') {
      await fail('(Copilot unavailable: requires VS Code 1.90+ with GitHub Copilot signed in)');
      return;
    }
    const models = await lm.selectChatModels({ vendor: 'copilot' });
    if (!models || models.length === 0) {
      await fail('(Copilot unavailable: no model found — check GitHub Copilot sign-in)');
      return;
    }
    const model = models[0];
    const basePrompt = systemPrompt && systemPrompt.trim() ? systemPrompt : AGENT_DEFAULT_PROMPT;
    const projOverview = await buildWorkspaceContext();
    const system = basePrompt +
      '\n\nYou are running on the user\'s desktop inside VS Code, authorized to help with their code. ' +
      'The workspace shown below is the user\'s own project, which they have opened in VS Code and explicitly ' +
      'authorized you to inspect AND modify — reading files, listing directories, and writing/editing files here ' +
      'is normal, expected, and permitted. When asked whether you can see their project, answer yes and describe ' +
      'what you see from the overview below. If they ask you to change code, read the relevant files, make the ' +
      'edit, and write the updated file back.' +
      '\n\nYou have access to the user\'s project via tools: termetron_workspace_context (project overview), ' +
      'termetron_read_file (read a file), termetron_list_dir (list a directory), termetron_write_file ' +
      '(write/overwrite a file). Paths are relative to the workspace root.' +
      '\n\nCurrent project (workspace) overview:\n' + projOverview;
    // system 提示前置（User 角色，兼容无 System 角色的旧 API）；历史只带最近 40 条
    const hist: vscode.LanguageModelChatMessage[] = history.slice(-40).map((m) =>
      m.role === 'user'
        ? vscode.LanguageModelChatMessage.User(String(m.text || ''))
        : vscode.LanguageModelChatMessage.Assistant(String(m.text || '')),
    );
    const msgs: vscode.LanguageModelChatMessage[] = [
      vscode.LanguageModelChatMessage.User(system),
      ...hist,
    ];
    const token = new vscode.CancellationTokenSource().token;
    // 启用工作区工具（硬编码定义，确保模型可见）
    const tools = getAgentTools();
    const reqOpts: vscode.LanguageModelChatRequestOptions = {
      justification: 'Termetron remote agent: forwards messages you send via termetron to Copilot and shows the reply back in termetron.',
    };
    if (tools.length > 0) {
      reqOpts.tools = tools as any;
      reqOpts.toolMode = vscode.LanguageModelChatToolMode.Auto;
    }
    // 工具调用循环：模型可请求调用工具（读文件/列目录/项目概览），结果回传后继续
    let text = '';
    for (let round = 0; round < 8; round++) {
      const resp = await model.sendRequest(msgs, reqOpts, token);
      const parts: any[] = [];
      for await (const part of resp.stream) parts.push(part);
      const texts = parts.filter((p) => p instanceof vscode.LanguageModelTextPart).map((p: any) => p.value).join('');
      if (texts) text = texts;
      const calls = parts.filter((p) => p instanceof vscode.LanguageModelToolCallPart);
      if (calls.length === 0) break;  // 无工具调用 → 完成
      for (const call of calls) {
        let result: any;
        try {
          result = await vscode.lm.invokeTool(call.name, { toolInvocationToken: undefined, input: call.input }, token);
        } catch (e: any) {
          result = new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart(`(tool ${call.name} failed: ${e.message})`)]);
        }
        msgs.push(vscode.LanguageModelChatMessage.Assistant([new vscode.LanguageModelToolCallPart(call.callId, call.name, call.input)]));
        msgs.push(vscode.LanguageModelChatMessage.User([new vscode.LanguageModelToolResultPart(call.callId, result)]));
      }
    }
    await postReply(port, name, text.trim() || '(empty response)');
  } catch (e: any) {
    const msg = e && e.message ? e.message : String(e);
    await fail('(Copilot reply failed: ' + String(msg).slice(0, 300) + ')');
  }
}

/** Write the assistant reply (or error note) back to the agent session. */
async function postReply(port: number, name: string, text: string): Promise<void> {
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/agent/${encodeURIComponent(name)}/reply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    dlog('agent reply -> :' + port + '/' + name + ' (' + (r.ok ? 'ok' : 'HTTP ' + r.status) + ')');
  } catch { /* ignore */ }
}

// ---- 工作区工具：让 termetron agent 能查看用户的项目目录（像当前 Copilot 一样）----
function workspaceBase(): string | null {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? null;
}

/** 解析路径（绝对或相对工作区根），限制在工作区内；越界返回 null。 */
function resolveWSPath(p: string): string | null {
  const base = workspaceBase();
  if (!base) return null;
  let full = (p || '').trim();
  if (!full) return base;
  if (!path.isAbsolute(full)) full = path.join(base, full);
  const norm = path.normalize(full);
  return norm.toLowerCase().startsWith(base.toLowerCase()) ? norm : null;
}

async function wsReadDir(dir: string, limit: number): Promise<string[]> {
  const out: string[] = [];
  try {
    const names = await fs.promises.readdir(dir);
    for (const n of names.slice(0, limit)) {
      let k = '?';
      try { k = (await fs.promises.stat(path.join(dir, n))).isDirectory() ? 'D' : 'F'; } catch { /* ignore */ }
      out.push(`${k} ${n}`);
    }
  } catch { /* ignore */ }
  return out;
}

/** 构建项目概览文本（工作区根 + git 状态 + 顶层条目）。 */
async function buildWorkspaceContext(): Promise<string> {
  const base = workspaceBase();
  if (!base) return '(no workspace folder open)';
  let git = '(git unavailable)';
  try {
    const r = await execFile('git', ['-C', base, 'status', '--short'], { timeout: 5000, encoding: 'utf-8' }) as any;
    git = (r.stdout || '').trim() || '(clean)';
  } catch { /* ignore */ }
  const entries = await wsReadDir(base, 120);
  return `workspace root: ${base}\n\ngit status:\n${git}\n\ntop-level:\n${entries.join('\n') || '(empty)'}`;
}

/** 硬编码的工具定义（直接传给模型；不依赖 lm.tools 是否可见）。 */
function getAgentTools(): any[] {
  return [
    { name: 'termetron_workspace_context', description: "Get an overview of the user's project: workspace root, git status, and top-level files/directories.", inputSchema: { type: 'object', properties: {} } },
    { name: 'termetron_read_file', description: 'Read the content of a file inside the workspace. Provide { "path": "relative or absolute path" }.', inputSchema: { type: 'object', properties: { path: { type: 'string' }, maxBytes: { type: 'number' } }, required: ['path'] } },
    { name: 'termetron_list_dir', description: 'List files and directories inside a workspace directory. Provide { "path": "relative or absolute path" }.', inputSchema: { type: 'object', properties: { path: { type: 'string' } } } },
    { name: 'termetron_write_file', description: 'Write or overwrite a file inside the workspace (creates parent directories). User-authorized. Provide { "path": "...", "content": "..." }.', inputSchema: { type: 'object', properties: { path: { type: 'string' }, content: { type: 'string' } }, required: ['path', 'content'] } },
  ];
}

function registerWorkspaceTools(context: vscode.ExtensionContext): void {
  // 项目概览：工作区根 + git 状态 + 顶层条目
  context.subscriptions.push(vscode.lm.registerTool('termetron_workspace_context', {
    async invoke(options: any, token: vscode.CancellationToken) {
      const base = workspaceBase();
      if (!base) return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart('(no workspace folder open)')]);
      let git = '(git unavailable)';
      try {
        const r = await execFile('git', ['-C', base, 'status', '--short'], { timeout: 5000, encoding: 'utf-8' }) as any;
        git = (r.stdout || '').trim() || '(clean)';
      } catch { /* ignore */ }
      const entries = await wsReadDir(base, 120);
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart(`workspace root: ${base}\n\ngit status:\n${git}\n\ntop-level:\n${entries.join('\n') || '(empty)'}`),
      ]);
    },
  }));
  // 读文件（限制工作区内 + 截断）
  context.subscriptions.push(vscode.lm.registerTool('termetron_read_file', {
    async invoke(options: any, token: vscode.CancellationToken) {
      const full = resolveWSPath(String((options.input || {}).path || ''));
      if (!full) return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart('(path must be inside the workspace)')]);
      try {
        const maxBytes = Number((options.input || {}).maxBytes) || 40000;
        const data = await fs.promises.readFile(full, 'utf-8');
        const out = data.length > maxBytes ? data.slice(0, maxBytes) + `\n… [truncated, ${data.length} bytes total]` : data;
        return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart(`--- ${full} ---\n${out}`)]);
      } catch (e: any) {
        return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart(`(read failed: ${e.message})`)]);
      }
    },
  }));
  // 列目录
  context.subscriptions.push(vscode.lm.registerTool('termetron_list_dir', {
    async invoke(options: any, token: vscode.CancellationToken) {
      const full = resolveWSPath(String((options.input || {}).path || ''));
      if (!full) return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart('(path must be inside the workspace)')]);
      const entries = await wsReadDir(full, 200);
      return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart(`--- ${full} ---\n${entries.join('\n') || '(empty)'}`)]);
    },
  }));
  // 写/改文件（覆盖或创建，限工作区内 + 自动建父目录）
  context.subscriptions.push(vscode.lm.registerTool('termetron_write_file', {
    async invoke(options: any, token: vscode.CancellationToken) {
      const input = options.input || {};
      const full = resolveWSPath(String(input.path || ''));
      if (!full) return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart('(path must be inside the workspace)')]);
      const content = String(input.content ?? '');
      try {
        await fs.promises.mkdir(path.dirname(full), { recursive: true });
        await fs.promises.writeFile(full, content, 'utf-8');
        dlog('workspace write: ' + full + ' (' + content.length + ' chars)');
        return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart(`written: ${full} (${content.length} chars)`)]);
      } catch (e: any) {
        return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart(`(write failed: ${e.message})`)]);
      }
    },
  }));
  dlog('workspace tools registered (termetron_workspace_context / read_file / list_dir / write_file)');
}

export function activate(context: vscode.ExtensionContext): TermetronApi {
  extContext = context;
  registerWorkspaceTools(context);
  startAgentBridge(context);
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
