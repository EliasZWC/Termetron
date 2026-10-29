// ═══════════════════════════════════════════════════════════════════════
// Termetron Mobile —— 直连 Tailscale IP，无登录页
// -----------------------------------------------------------------------
// 与 Livolog 一致的交互模型：
//   · 冷启动直接进入应用内部（无登录/连接页）
//   · 底部两个 tab：Sessions / Settings，图标+名称，选中切换线性→实心
//   · 设置页照 Livolog 的 .setting-group / .setting-list / .setting-item
//
// ⚠️ 连接方式已从「隧道 URL + 一次性密码 + 配对码」改为
//    「Tailscale IP + 端口」直连（用户在设置页填一次，持久化）。
// ═══════════════════════════════════════════════════════════════════════

import { App } from '@capacitor/app';
import { checkForUpdate } from './updater';

// ── 设置持久化 ──────────────────────────────────────────────────────────
const STORE_KEY = 'termetron.settings.v1';

const DEFAULTS = {
  language: 'en',
  theme: 'dark',
  fontSize: 13,
  tsIp: '',
  port: 8899,
};

function loadSettings() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return { ...DEFAULTS };
    return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch {
    return { ...DEFAULTS };
  }
}

function saveSettings() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(settings));
  } catch {
    /* 存储满/被禁用时静默失败 */
  }
}

let settings = loadSettings();

// ── 轻量提示（底部 toast）────────────────────────────────────────────────
const TMT_TOAST_CSS = '.tmt-toast{position:fixed;left:50%;bottom:88px;transform:translateX(-50%);background:rgba(36,36,36,.97);border:1px solid #333333;color:#ECECEA;padding:10px 18px;border-radius:8px;font-size:13px;z-index:10000;pointer-events:none;font-family:\'Termetron Mono\',\'Roboto Mono\',\'Droid Sans Mono\',monospace;box-shadow:0 8px 24px rgba(0,0,0,.55)}';

function tmtToast(msg) {
  if (!document.getElementById('tmt-toast-css')) {
    const s = document.createElement('style');
    s.id = 'tmt-toast-css';
    s.textContent = TMT_TOAST_CSS;
    document.head.appendChild(s);
  }
  const el = document.createElement('div');
  el.className = 'tmt-toast';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 1800);
}

// ── 弹窗（照 Scholarius 的 .sheet 抽屉）─────────────────────────────────
const TMT_CSS = `
.tmt-overlay{position:fixed;inset:0;background:rgba(0,0,0,.32);display:flex;align-items:flex-end;justify-content:center;z-index:9999;font-family:'Termetron Mono','Roboto Mono','Droid Sans Mono',monospace}
.tmt-modal{background:#242424;border:0;border-radius:20px 20px 0 0;padding:8px 20px calc(20px + env(safe-area-inset-bottom));width:min(560px,100%);color:#ECECEA;box-shadow:0 -8px 32px rgba(0,0,0,.28);text-align:left}
.tmt-modal h3{color:#F2F2F0;margin:12px 0 8px;font-size:15px;font-weight:700}
.tmt-modal p{margin:12px 0 8px;font-size:13px;line-height:1.55;color:#9A9A98;white-space:pre-line;word-break:break-word}
.tmt-actions{display:flex;align-items:center;gap:12px;justify-content:space-between;padding-top:18px;margin-top:0}
/* 只有一个按钮时居中（Scholarius 的 .setting-action.setting-center） */
.tmt-actions:has(:only-child){justify-content:center}
.tmt-btn{padding:14px 4px;border:0;border-radius:0;background:none;font:inherit;font-size:15px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;cursor:pointer;color:#7EE2A8;-webkit-tap-highlight-color:transparent;transition:opacity .12s ease}
.tmt-btn:active{opacity:.55}
.tmt-btn.cancel{color:#9A9A98}
.tmt-input{width:100%;box-sizing:border-box;padding:2px 0;border:0;border-radius:0;background:transparent;color:#ECECEA;font:inherit;font-size:15px;outline:none}
.tmt-input::placeholder{color:#9A9A98;opacity:.55}
`;

function ensureModalCss() {
  if (!document.getElementById('tmt-css')) {
    const s = document.createElement('style');
    s.id = 'tmt-css';
    s.textContent = TMT_CSS;
    document.head.appendChild(s);
  }
}

/**
 * 通用弹窗。
 * @param {{title?:string, message?:string, input?:{placeholder?:string,value?:string,type?:string},
 *          okText?:string, cancelText?:string|null}} opts
 * @returns {Promise<string|boolean|null>} 输入框弹窗返回字符串；其它返回 true/false
 */
function tmtPrompt({ title, message, input, okText = 'OK', cancelText = null }) {
  ensureModalCss();
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'tmt-overlay';
    const hasInput = !!input;
    overlay.innerHTML =
      '<div class="tmt-modal">' +
      (title ? '<h3></h3>' : '') +
      (message || hasInput ? '<p></p>' : '') +
      (hasInput ? '<input class="tmt-input">' : '') +
      '<div class="tmt-actions">' +
      (cancelText ? '<button class="tmt-btn cancel"></button>' : '') +
      '<button class="tmt-btn ok"></button></div></div>';

    const h = overlay.querySelector('h3');
    if (h) h.textContent = title;
    const p = overlay.querySelector('p');
    if (p) p.textContent = message || '';
    const inp = overlay.querySelector('.tmt-input');
    if (inp) {
      inp.type = input.type || 'text';
      inp.placeholder = input.placeholder || '';
      inp.value = input.value ?? '';
      inp.inputMode = input.type === 'number' ? 'numeric' : 'text';
      inp.autocapitalize = 'off';
      inp.spellcheck = false;
    }
    const ok = overlay.querySelector('.tmt-btn.ok');
    const cancel = overlay.querySelector('.tmt-btn.cancel');
    ok.textContent = okText;
    if (cancel) cancel.textContent = cancelText;

    document.body.appendChild(overlay);

    const finish = (val) => {
      overlay.remove();
      document.removeEventListener('keydown', kd);
      resolve(val);
    };
    const accept = () => finish(hasInput ? inp.value.trim() : true);
    const kd = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); finish(hasInput ? null : false); }
      else if (e.key === 'Enter') { e.preventDefault(); accept(); }
    };
    document.addEventListener('keydown', kd);
    ok.onclick = accept;
    if (cancel) cancel.onclick = () => finish(hasInput ? null : false);
    if (inp) inp.focus(); else ok.focus();
  });
}

function tmtAlert(message, title = 'TERMETRON') {
  return tmtPrompt({ title, message, okText: 'OK', cancelText: null });
}

function tmtConfirm(message, { title = 'TERMETRON', okText = 'OK', cancelText = 'CANCEL' } = {}) {
  return tmtPrompt({ title, message, okText, cancelText });
}

// ═══════════════════════════════════════════════════════════════════════
// 连接层：Tailscale IP 直连
// ═══════════════════════════════════════════════════════════════════════

function baseUrl() {
  const ip = (settings.tsIp || '').trim();
  if (!ip) return '';
  // 允许用户直接粘 http://ip 或 ip:port
  if (/^https?:\/\//i.test(ip)) return ip.replace(/\/+$/, '');
  return `http://${ip}:${settings.port}`;
}

function hasConnection() {
  return !!baseUrl();
}

async function api(path, opts = {}) {
  const base = baseUrl();
  if (!base) throw new Error('NO_CONNECTION');
  const res = await fetch(base + path, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const ct = res.headers.get('content-type') || '';
  return ct.includes('json') ? res.json() : res.text();
}

// ═══════════════════════════════════════════════════════════════════════
// 页面路由（底部两个 tab，+ 终端页）
// ═══════════════════════════════════════════════════════════════════════

const PAGES = {
  sessions: { el: 'page-sessions', tab: 'tab-sessions', title: 'Sessions', showAdd: true },
  setting: { el: 'page-setting', tab: 'tab-setting', title: 'Settings', showAdd: false },
  terminal: { el: 'page-terminal', tab: 'tab-sessions', title: '', showAdd: false },
};

let currentPage = 'sessions';
let currentSession = null;
// ⚠️ pollTimer 必须在使用它的 showPage **之前**声明：
//    `let` 声明的变量在初始化前处于 TDZ，连 `typeof pollTimer` 都会抛
//    ReferenceError（不像 var）。而 showPage 在启动时立刻被调用。
let pollTimer = null;

const titleEl = document.getElementById('topbar-title');
const addBtn = document.getElementById('btn-add');

function showPage(name) {
  currentPage = name;
  const def = PAGES[name];
  if (!def) return;

  // 离开终端页就停止输出轮询（否则后台一直请求）
  if (name !== 'terminal' && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }

  for (const key of Object.keys(PAGES)) {
    const node = document.getElementById(PAGES[key].el);
    if (node) node.hidden = key !== name;
  }
  // 终端页顶部显示会话名，其它显示固定标题
  titleEl.textContent = name === 'terminal' ? (currentSession || 'Terminal') : def.title;
  // FAB 只在会话列表出现（照 Livolog：FAB 是「新建」动作，属于列表页）
  addBtn.hidden = !def.showAdd;
  // 终端页左上角显示返回键；同时右侧补一个等宽占位，标题才真居中。
  // ⚠️ 非终端页**不要**任何占位：那时左右都空，标题靠 text-align:center
  //    自然就居中；多放一个左侧占位会把它挤偏 20px
  //    （实测踩过：标题中心 238 vs 视口中心 218）。
  const isTerminal = name === 'terminal';
  const backBtn = document.getElementById('btn-back');
  const slot = document.getElementById('topbar-slot');
  if (backBtn) backBtn.hidden = !isTerminal;
  if (slot) slot.hidden = !isTerminal;

  // tab 高亮：终端页归属 Sessions 这个 tab
  document.querySelectorAll('.nav-item').forEach((btn) => {
    const on = btn.dataset.page === def.tab.replace('tab-', '');
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
  });

  if (name === 'sessions') refreshSessions();
  if (name === 'setting') renderSettings();
  if (name === 'terminal') document.getElementById('term-input')?.focus();
}

document.querySelectorAll('.nav-item').forEach((btn) => {
  btn.addEventListener('click', () => showPage(btn.dataset.page));
});

// 终端页左上角返回键：回到会话列表
document.getElementById('btn-back')?.addEventListener('click', () => showPage('sessions'));

// Android 返回键：终端页 → 回会话列表；其它 → 退出
App.addListener('backButton', ({ canGoBack }) => {
  if (currentPage === 'terminal') {
    showPage('sessions');
    return;
  }
  App.exitApp();
});

// ═══════════════════════════════════════════════════════════════════════
// 会话页
// ═══════════════════════════════════════════════════════════════════════

const sessionList = document.getElementById('session-list');
const sessionsEmpty = document.getElementById('sessions-empty');

function sessionRow(name, info) {
  const li = document.createElement('li');
  li.className = 'setting-item';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'setting-action';

  const label = document.createElement('span');
  label.className = 'setting-label';
  label.textContent = name;

  const value = document.createElement('span');
  value.className = 'setting-value';
  value.textContent = info && info.busy ? 'running' : 'idle';

  btn.append(label, value);
  btn.addEventListener('click', () => openSession(name));
  li.appendChild(btn);
  return li;
}

async function refreshSessions() {
  if (!hasConnection()) {
    sessionList.innerHTML = '';
    sessionsEmpty.hidden = false;
    sessionsEmpty.querySelector('.empty-title').textContent = 'Not connected';
    sessionsEmpty.querySelector('.empty-hint').textContent =
      'Set your computer\'s Tailscale IP in Settings to start.';
    return;
  }
  try {
    const data = await api('/api/sessions');
    sessionList.innerHTML = '';
    const names = Object.keys(data || {});
    if (!names.length) {
      sessionsEmpty.hidden = false;
      sessionsEmpty.querySelector('.empty-title').textContent = 'No sessions';
      sessionsEmpty.querySelector('.empty-hint').textContent =
        'Tap + to create a session on your computer.';
      return;
    }
    sessionsEmpty.hidden = true;
    names.forEach((n) => sessionList.appendChild(sessionRow(n, data[n])));
  } catch (err) {
    sessionList.innerHTML = '';
    sessionsEmpty.hidden = false;
    sessionsEmpty.querySelector('.empty-title').textContent = 'Cannot reach computer';
    sessionsEmpty.querySelector('.empty-hint').textContent =
      `Check that Termetron is running on ${settings.tsIp}:${settings.port} and Tailscale is connected.`;
  }
}

// 新建会话
addBtn.addEventListener('click', async () => {
  if (!hasConnection()) {
    tmtAlert('Set your computer\'s Tailscale IP in Settings first.');
    return;
  }
  const name = await tmtPrompt({
    title: 'New session',
    message: 'Name for the new terminal session',
    input: { placeholder: 'session name' },
    okText: 'CREATE',
    cancelText: 'CANCEL',
  });
  if (!name) return;
  try {
    await api('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({ name, cmd: null }),
    });
    await refreshSessions();
  } catch (err) {
    tmtAlert(`Could not create session:\n${err.message}`);
  }
});

// ═══════════════════════════════════════════════════════════════════════
// 终端页
// ═══════════════════════════════════════════════════════════════════════

const termOut = document.getElementById('term-out');
const termForm = document.getElementById('term-form');
const termInput = document.getElementById('term-input');

async function openSession(name) {
  currentSession = name;
  showPage('terminal');
  termOut.textContent = '';
  await pullOutput();
  clearInterval(pollTimer);
  pollTimer = setInterval(pullOutput, 1000);
}

async function pullOutput() {
  if (!currentSession || currentPage !== 'terminal') return;
  try {
    const data = await api(`/api/output/${encodeURIComponent(currentSession)}`);
    // ⚠️ /api/output/<name> 返回的是 **JSON 对象**，不是纯文本：
    //    { lines: [...], progress, done, busy, script, cmd, updated }
    //    （Session 与 AgentSession 共用这个形状）。
    //    早期我按字符串处理会导致 `[object Object]`，这里显式取 lines。
    const text = Array.isArray(data?.lines) ? data.lines.join('\n') : String(data ?? '');
    if (termOut.textContent !== text) {
      const atBottom =
        termOut.scrollTop + termOut.clientHeight >= termOut.scrollHeight - 24;
      termOut.textContent = text;
      if (atBottom) termOut.scrollTop = termOut.scrollHeight;
    }
  } catch {
    /* 断线时静默，下个 tick 重试 */
  }
}

termForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = termInput.value;
  if (!text.trim() || !currentSession) return;
  termInput.value = '';
  try {
    await api(`/api/sessions/${encodeURIComponent(currentSession)}/input`, {
      method: 'POST',
      body: JSON.stringify({ text: text + '\n' }),
    });
    setTimeout(pullOutput, 120);
  } catch (err) {
    tmtAlert(`Send failed:\n${err.message}`);
  }
});

// 离开终端页时停止轮询
// ⚠️ 不用「重新赋值 showPage」那种写法：函数声明可以被覆盖，但一旦
//    别处提前导出了引用、或将来有人把 showPage 改成 const，就会静默失效。
//    直接在 showPage 内部清理更可靠（见 showPage 的 clearInterval）。

// ═══════════════════════════════════════════════════════════════════════
// 设置页
// ═══════════════════════════════════════════════════════════════════════

const LANGS = [
  { id: 'en', name: 'English' },
  { id: 'zh', name: '简体中文' },
];
const THEMES = [
  { id: 'dark', name: 'Dark' },
  { id: 'light', name: 'Light' },
];
const FONT_SIZES = [11, 12, 13, 14, 15, 16, 18];

function err(msg) {
  // 统一在设置页给出反馈，不用弹窗打断
  tmtToast(msg);
}

function applyTheme() {
  // ⚠️ 用 `data-theme` 属性切换，**不要**在 JS 里逐条 setProperty：
  //    · 颜色值的唯一来源是 styles.css 的 :root / :root[data-theme="light"]，
  //      两处写一遍必然漂移（早期版本我在 JS 里自创过两个不在
  //      参考应用调色板中的浅色值，被 check_palette 抓到）
  //    · 参考应用（Livolog / Scholarius）也是这个做法
  document.documentElement.setAttribute('data-theme', settings.theme);
}

function applyFontSize() {
  document.documentElement.style.setProperty('--fs', `${settings.fontSize}px`);
}

function renderSettings() {
  document.getElementById('setting-language-value').textContent =
    (LANGS.find((l) => l.id === settings.language) || LANGS[0]).name;
  document.getElementById('setting-theme-value').textContent =
    (THEMES.find((t) => t.id === settings.theme) || THEMES[0]).name;
  document.getElementById('setting-fontsize-value').textContent = `${settings.fontSize}px`;
  document.getElementById('setting-tsip-value').textContent = settings.tsIp || 'not set';
  document.getElementById('setting-port-value').textContent = String(settings.port);
  document.getElementById('setting-version').textContent =
    'v' + (typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.0.0');
  applyTheme();
  applyFontSize();
}

// 通用「选项列表」弹窗：照 Scholarius 的 .sheet-picker-list
function pickOption(title, options, currentId) {
  return new Promise((resolve) => {
    ensureModalCss();
    const overlay = document.createElement('div');
    overlay.className = 'tmt-overlay';
    const items = options.map((o, i) =>
      `<button class="tmt-opt" data-i="${i}" style="display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;padding:13px 12px;border:0;border-radius:10px;background:transparent;color:${o.id === currentId ? '#7EE2A8' : '#ECECEA'};font:inherit;font-size:15px;line-height:1.4;text-align:left;cursor:pointer">${o.name}</button>`
    ).join('');
    overlay.innerHTML =
      `<div class="tmt-modal"><h3 style="margin:12px 0 8px">${title}</h3>` +
      `<div style="max-height:52vh;overflow-y:auto;margin:0 -8px">${items}</div>` +
      '<div class="tmt-actions"><button class="tmt-btn cancel">CANCEL</button></div></div>';
    document.body.appendChild(overlay);
    const finish = (v) => { overlay.remove(); resolve(v); };
    overlay.querySelectorAll('.tmt-opt').forEach((btn) => {
      btn.onclick = () => finish(options[Number(btn.dataset.i)].id);
    });
    overlay.querySelector('.tmt-btn.cancel').onclick = () => finish(null);
  });
}

document.getElementById('setting-language').addEventListener('click', async () => {
  const id = await pickOption('Language', LANGS, settings.language);
  if (id) { settings.language = id; saveSettings(); renderSettings(); }
});

document.getElementById('setting-theme').addEventListener('click', async () => {
  const id = await pickOption('Theme', THEMES, settings.theme);
  if (id) { settings.theme = id; saveSettings(); renderSettings(); }
});

document.getElementById('setting-fontsize').addEventListener('click', async () => {
  const opts = FONT_SIZES.map((n) => ({ id: n, name: `${n}px` }));
  const id = await pickOption('Font size', opts, settings.fontSize);
  if (id) { settings.fontSize = Number(id); saveSettings(); renderSettings(); }
});

document.getElementById('setting-tsip').addEventListener('click', async () => {
  const v = await tmtPrompt({
    title: 'Tailscale IP',
    message: 'Your computer\'s Tailscale IP, e.g. 100.101.102.103',
    input: { placeholder: '100.x.y.z', value: settings.tsIp },
    okText: 'SAVE',
    cancelText: 'CANCEL',
  });
  if (v === null) return;
  settings.tsIp = v;
  saveSettings();
  renderSettings();
});

document.getElementById('setting-port').addEventListener('click', async () => {
  const v = await tmtPrompt({
    title: 'Port',
    message: 'Port Termetron listens on (default 8899)',
    input: { placeholder: '8899', value: String(settings.port), type: 'number' },
    okText: 'SAVE',
    cancelText: 'CANCEL',
  });
  if (v === null) return;
  const n = parseInt(v, 10);
  if (!n || n < 1 || n > 65535) { err('Port must be 1–65535'); return; }
  settings.port = n;
  saveSettings();
  renderSettings();
});

document.getElementById('setting-test').addEventListener('click', async () => {
  if (!hasConnection()) { err('Set Tailscale IP first'); return; }
  try {
    const data = await api('/api/sessions');
    const n = Object.keys(data || {}).length;
    tmtToast(`Connected · ${n} session${n === 1 ? '' : 's'}`);
  } catch (e2) {
    tmtAlert(`Cannot reach ${settings.tsIp}:${settings.port}\n\n${e2.message}`);
  }
});

document.getElementById('setting-contact').addEventListener('click', () => {
  const url = 'https://github.com/EliasZWC/Termetron';
  if (window.Capacitor?.Plugins?.Browser) {
    window.Capacitor.Plugins.Browser.open({ url });
  } else {
    window.open(url, '_blank');
  }
});

// ═══════════════════════════════════════════════════════════════════════
// Splash & 启动
// ═══════════════════════════════════════════════════════════════════════

const splashEl = document.getElementById('splash');
if (splashEl) {
  // ⚠️ 必须判 `e.target === splashEl`：animationend 会从子元素冒泡上来，
  //    splash 里 `>` / 圆点 / `_` / 9 个字母各有动画，任一结束都会触发回调。
  //    实测（v0.8.18 bug）：`>` 在 918ms 画完就触发，整块被 remove，
  //    圆点、`_`、品牌字全没出现。
  // ⚠️ 不要加 { once: true }：第一个子元素的 animationend 会消耗掉监听器。
  splashEl.addEventListener('animationend', (e) => {
    if (e.target === splashEl) splashEl.remove();
  });
  setTimeout(() => splashEl.remove(), 4200);
}

// 启动：直接进 Sessions（无登录页）
applyTheme();
applyFontSize();
showPage('sessions');

// 有连接时才后台查更新，避免无网络时反复弹错
setTimeout(() => {
  if (hasConnection()) checkForUpdate().catch(() => {});
}, 2500);
