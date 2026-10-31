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
import { StatusBar, Style } from '@capacitor/status-bar';
import { checkForUpdate } from './updater';
import { i18n, setLang, applyI18n, localizeServerLines, getLang } from './i18n';

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
// 弹窗 / 菜单的样式。**必须在这里写全**——不能用 app/src/styles.css，
// 因为这段在 quant_terminal.py 的内联页面里也复用不到（那边是另一套 DOM），
// 而且参考应用 Livolog 的 .menu 也是运行时注入的。
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
/* ── 下拉菜单：逐项照 Livolog 的 .menu / .menu-item 实测值 ────────────
   Livolog 实测：背景 #242424、圆角 12px、边框 1px #333333、
   padding 6px、min-width 160px、阴影 0 8px 24px rgba(0,0,0,.22)、
   项 padding 10px 14px / 14px 字号、选中项只加粗到 600 不改色。 */
.tmt-menu{position:fixed;z-index:10000;min-width:160px;max-height:min(60vh,320px);overflow-y:auto;box-sizing:border-box;padding:6px;background:#242424;border:1px solid #333333;border-radius:12px;box-shadow:0 8px 24px rgba(0,0,0,.22);font-family:'Termetron Mono','Roboto Mono','Droid Sans Mono',monospace}
.tmt-menu-item{display:block;width:100%;padding:10px 14px;border:0;border-radius:8px;background:transparent;color:#ECECEA;font:inherit;font-size:14px;font-weight:400;line-height:1.45;text-align:left;cursor:pointer;-webkit-tap-highlight-color:transparent}
.tmt-menu-item:active{background:#303030}
.tmt-menu-item.is-selected{font-weight:600}
/* ── 字号表单：中间「减 / 数值 / 加」一行 + 下方滑块 ───────────────────
   按钮/输入框尺寸照 Scholarius 阅读视图 .reader-size-controls 实测值：
   按钮 32×32 圆形、输入 60×28 / 14px / 居中 / 1px #333333 / 圆角 8px。 */
.tmt-body{margin:4px 0 8px}
.tmt-size-row{display:flex;align-items:center;justify-content:center;gap:4px}
.tmt-size-row .size-btn{display:grid;place-items:center;width:32px;height:32px;padding:0;border:0;border-radius:50%;background:none;color:#9A9A98;cursor:pointer;-webkit-tap-highlight-color:transparent}
.tmt-size-row .size-btn:active{opacity:.55}
.tmt-size-row .size-btn:disabled{opacity:.32;pointer-events:none}
.tmt-size-row .size-btn svg{width:22px;height:22px;fill:currentColor}
.tmt-size-row .size-input{width:60px;height:28px;box-sizing:border-box;padding:0 6px;border:1px solid #333333;border-radius:8px;background:transparent;color:#ECECEA;font:inherit;font-size:14px;font-variant-numeric:tabular-nums;text-align:center;outline:none;-moz-appearance:textfield;appearance:textfield}
.tmt-size-row .size-input::-webkit-outer-spin-button,
.tmt-size-row .size-input::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
.tmt-size-row .size-input:focus{border-color:#9A9A98}
.tmt-size-range{display:block;width:100%;height:24px;margin:14px 0 0;padding:0;background:transparent;-webkit-appearance:none;appearance:none}
.tmt-size-range::-webkit-slider-runnable-track{height:3px;border-radius:2px;background:#333333}
.tmt-size-range::-moz-range-track{height:3px;border-radius:2px;background:#333333}
.tmt-size-range::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:16px;height:16px;margin-top:-6.5px;border:0;border-radius:50%;background:#F2F2F0;box-shadow:0 1px 4px rgba(0,0,0,.4)}
.tmt-size-range::-moz-range-thumb{width:16px;height:16px;border:0;border-radius:50%;background:#F2F2F0;box-shadow:0 1px 4px rgba(0,0,0,.4)}
.tmt-size-hint{margin:8px 0 0;font-size:12px;color:#9A9A98;text-align:center}
/* ── 连接表单 ─────────────────────────────────────────────────────────── */
.tmt-field{display:block;margin:0 0 14px}
.tmt-field-label{display:block;margin:0 0 4px;font-size:12px;letter-spacing:.4px;color:#9A9A98}
.tmt-field .tmt-input{border-bottom:1px solid #333333;padding:4px 0}
.tmt-field .tmt-input:focus{border-bottom-color:#9A9A98}
.tmt-conn-status{margin:4px 0 0;font-size:13px;line-height:1.5;white-space:pre-line;word-break:break-word}
.tmt-conn-status[data-kind="good"]{color:#7EE2A8}
.tmt-conn-status[data-kind="bad"]{color:#E06C5E}
.tmt-conn-status[data-kind="idle"]{color:#9A9A98}
.tmt-conn-test{display:block;width:100%;margin:14px 0 0;padding:11px 0;border:1px solid #333333;border-radius:10px;background:transparent;color:#ECECEA;font:inherit;font-size:13px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;cursor:pointer;-webkit-tap-highlight-color:transparent}
.tmt-conn-test:active{background:#303030}
.tmt-conn-test:disabled{opacity:.45;pointer-events:none}
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
 * @param {{title?:string, message?:string, body?:string,
 *          input?:{placeholder?:string,value?:string,type?:string},
 *          okText?:string, cancelText?:string|null,
 *          setup?:(bodyEl:HTMLElement)=>void,
 *          onAccept?:(bodyEl:HTMLElement|null)=>boolean|void}} opts
 * @returns {Promise<string|boolean|null>} 输入框弹窗返回字符串；其它返回 true/false
 *
 * `body` 是一段自定义 HTML，插在消息下面、按钮上面；`setup` 在挂载后调用，
 * 用于绑事件、拿引用。字号表单（滑块 + 加减 + 输入）就走这条路。
 *
 * `onAccept` 在点 OK 时调用，**返回 false 则不关闭**（表单校验失败时用）。
 */
function tmtPrompt({ title, message, body, input, okText = 'OK', cancelText = null,
                     setup, onAccept }) {
  ensureModalCss();
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'tmt-overlay';
    const hasInput = !!input;
    overlay.innerHTML =
      '<div class="tmt-modal">' +
      (title ? '<h3></h3>' : '') +
      (message || hasInput ? '<p></p>' : '') +
      (body ? '<div class="tmt-body"></div>' : '') +
      (hasInput ? '<input class="tmt-input">' : '') +
      '<div class="tmt-actions">' +
      (cancelText ? '<button class="tmt-btn cancel"></button>' : '') +
      '<button class="tmt-btn ok"></button></div></div>';

    const h = overlay.querySelector('h3');
    if (h) h.textContent = title;
    const p = overlay.querySelector('p');
    if (p) p.textContent = message || '';
    const bodyEl = overlay.querySelector('.tmt-body');
    if (bodyEl && body) bodyEl.innerHTML = body;
    const inp = overlay.querySelector('.tmt-input');
    // ⚠️ 只有传了 input（简易单行输入）才处理；用 body 自定义表单时
    //    input 是 undefined，这里若不加判空会直接崩
    //    `Cannot read properties of undefined (reading 'type')`。
    if (inp && input) {
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
    const accept = () => {
      // 自定义校验：返回 false 则不关闭弹窗
      if (typeof onAccept === 'function' && onAccept(bodyEl) === false) return;
      finish(hasInput ? inp.value.trim() : true);
    };
    const kd = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); finish(hasInput ? null : false); }
      else if (e.key === 'Enter') { e.preventDefault(); accept(); }
    };
    document.addEventListener('keydown', kd);
    ok.onclick = accept;
    if (cancel) cancel.onclick = () => finish(hasInput ? null : false);

    // 自定义内容的挂载钩子（字号控件等）
    if (bodyEl && typeof setup === 'function') setup(bodyEl);

    // ⚠️ 点表单外区域 = 取消。用 pointerdown 的**捕获**阶段：
    //    这样在 mousedown → mouseup → click 序列里先一步判定，
    //    且点 modal 内部不会误关（contains 检查）。
    //    挂载延后一拍，避免触发本弹窗的那次 pointerdown 立刻把它关掉。
    const onOutside = (ev) => {
      const modal = overlay.querySelector('.tmt-modal');
      if (modal && modal.contains(ev.target)) return;
      finish(hasInput ? null : false);
    };
    setTimeout(() => {
      overlay.addEventListener('pointerdown', onOutside);
    }, 0);

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
  sessions: { el: 'page-sessions', tab: 'tab-sessions', titleKey: 'page.sessions', showAdd: true },
  setting: { el: 'page-setting', tab: 'tab-setting', titleKey: 'page.settings', showAdd: false },
  terminal: { el: 'page-terminal', tab: 'tab-sessions', titleKey: 'page.terminal', showAdd: false },
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
  titleEl.textContent =
    name === 'terminal'
      ? (currentSession || i18n('page.terminal'))
      : i18n(def.titleKey);
  // Scholarius 式：新建按钮是标题栏右侧的图标按钮（而非 Livolog 的悬浮 FAB）
  addBtn.hidden = !def.showAdd;
  // 左格：会话页显示「连接」图标，终端页显示「返回」，设置页两者都不显示。
  // 两者占据同一个左侧位置（返回键在 DOM 里紧跟连接键，同一时刻只显示一个），
  // 因此标题的绝对居中不受影响。
  const isTerminal = name === 'terminal';
  const backBtn = document.getElementById('btn-back');
  const connBtn = document.getElementById('btn-conn');
  if (backBtn) backBtn.hidden = !isTerminal;
  if (connBtn) connBtn.hidden = name !== 'sessions';

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
  value.textContent = i18n(info && info.busy ? 'session.running' : 'session.idle');

  btn.append(label, value);
  btn.addEventListener('click', () => openSession(name));
  li.appendChild(btn);
  return li;
}

async function refreshSessions() {
  if (!hasConnection()) {
    sessionList.innerHTML = '';
    sessionsEmpty.hidden = false;
    sessionsEmpty.querySelector('.empty-title').textContent = i18n('empty.notConnected');
    sessionsEmpty.querySelector('.empty-hint').textContent =
      i18n('empty.notConnectedHint');
    return;
  }
  try {
    const data = await api('/api/sessions');
    sessionList.innerHTML = '';
    const names = Object.keys(data || {});
    if (!names.length) {
      sessionsEmpty.hidden = false;
      sessionsEmpty.querySelector('.empty-title').textContent = i18n('empty.noSessions');
      sessionsEmpty.querySelector('.empty-hint').textContent =
        i18n('empty.noSessionsHint');
      return;
    }
    sessionsEmpty.hidden = true;
    names.forEach((n) => sessionList.appendChild(sessionRow(n, data[n])));
  } catch (err) {
    sessionList.innerHTML = '';
    sessionsEmpty.hidden = false;
    sessionsEmpty.querySelector('.empty-title').textContent = i18n('empty.unreachable');
    sessionsEmpty.querySelector('.empty-hint').textContent =
      i18n('empty.unreachableHint', { ip: settings.tsIp, port: settings.port });
  }
}

// 新建会话
addBtn.addEventListener('click', async () => {
  if (!hasConnection()) {
    tmtAlert(i18n('session.errNoConnection'));
    return;
  }
  const name = await tmtPrompt({
    title: i18n('session.new'),
    message: i18n('session.nameHint'),
    input: { placeholder: i18n('session.namePlaceholder') },
    okText: i18n('action.create'),
    cancelText: i18n('action.cancel'),
  });
  if (!name) return;
  try {
    await api('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({ name, cmd: null }),
    });
    await refreshSessions();
  } catch (err) {
    tmtAlert(i18n('session.errCreate', { err: err.message }));
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
    const raw = Array.isArray(data?.lines) ? data.lines : [String(data ?? '')];
    // 服务端发的是中立标记（如 [termetron:restored]），在这里按语言渲染
    const text = localizeServerLines(raw).join('\n');
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
    tmtAlert(i18n('session.errSend', { err: err.message }));
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

// ── 服务端中立标记 → 本地化文案：见 i18n.js 的 localizeServerLines ──────
// （此处保留说明，实现已移到 i18n 模块，供 pullOutput 直接使用）

// 字号范围：照 Scholarius 阅读视图（min 12 / max 40），
// 这里收窄到 11–24 —— 终端等宽字更大就撑破行宽了。
const FONT_MIN = 11;
const FONT_MAX = 24;
const CONTACT_EMAIL = 'eliaschang@163.com';

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
  syncNativeBars();
}

// ── 原生状态栏 / 导航栏跟随主题 ──────────────────────────────────────
// ⚠️ 这是 v0.10.4 修的 bug：状态栏色原写在 Android 资源里（固定 #1E1E1E，
//    且 windowLightStatusBar=false），App 内切到日间主题后，页面变白了、
//    状态栏仍是夜间黑，顶部一条明显色阶。
//    修法：构建时注入**两套资源**（values / values-night）保证启动瞬间就对，
//    运行时再由这里用 StatusBar 插件按当前主题实时切换（含图标深浅）。
const THEME_NATIVE = {
  dark:  { bar: '#1E1E1E', style: Style.Dark  },  // 深色底 → 浅色图标
  light: { bar: '#EFEFED', style: Style.Light },  // 浅色底 → 深色图标
};

async function syncNativeBars() {
  const cfg = THEME_NATIVE[settings.theme] || THEME_NATIVE.dark;
  try {
    await StatusBar.setBackgroundColor({ color: cfg.bar });
    await StatusBar.setStyle({ style: cfg.style });
    // Android 上还要单独管导航栏（手势条区域）
    if (globalThis.Capacitor?.getPlatform?.() === 'android') {
      await StatusBar.setOverlaysWebView({ overlay: false });
      await StatusBar.setBackgroundColor({ color: cfg.bar });
    }
  } catch {
    /* 浏览器预览 / 插件不可用时忽略：页面内主题已由 data-theme 控制 */
  }
}

function applyFontSize() {
  document.documentElement.style.setProperty('--fs', `${settings.fontSize}px`);
}

function renderSettings() {
  document.getElementById('setting-language-value').textContent =
    (LANGS.find((l) => l.id === settings.language) || LANGS[0]).name;
  document.getElementById('setting-theme-value').textContent =
    i18n('theme.' + settings.theme);
  renderFontSize();
  document.getElementById('setting-tsip-value').textContent =
    settings.tsIp || i18n('value.notSet');
  document.getElementById('setting-port-value').textContent = String(settings.port);
  document.getElementById('setting-version').textContent =
    'v' + (typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.0.0');
  // 语言可能刚变过：先把 DOM 上的 data-i18n 文案全部刷新一遍
  setLang(settings.language);
  applyI18n();
  // ⚠️ applyI18n 会无脑覆盖 [data-i18n] 元素的 textContent，
  //    而 #setting-tsip-value 同时带 data-i18n="value.notSet" ——
  //    有 IP 时会被打回 "not set"（实测 tsIp 存了 100.1.2.3 却显示"未设置"）。
  //    所以必须在 applyI18n **之后**重新写一次动态值。
  document.getElementById('setting-tsip-value').textContent =
    settings.tsIp || i18n('value.notSet');
  // 顶栏标题不是 data-i18n（终端页要显示会话名），单独刷
  if (currentPage !== 'terminal') {
    titleEl.textContent = i18n(PAGES[currentPage]?.titleKey || 'page.sessions');
  }
  applyTheme();
  applyFontSize();
}

function renderFontSize() {
  document.getElementById('setting-fontsize-value').textContent = `${settings.fontSize}px`;
}

function setFontSize(n) {
  const v = Math.round(Number(n));
  if (!Number.isFinite(v)) return;
  settings.fontSize = Math.min(FONT_MAX, Math.max(FONT_MIN, v));
  saveSettings();
  applyFontSize();
  renderFontSize();
}

// 通用「下拉选项」菜单：照 Livolog 的 .menu / .menu-item。
// ⚠️ 与旧实现（底部 sheet 弹窗）的区别：这是**锚定在触发按钮上的浮动菜单**，
//    不遮罩全屏，点外面任意位置即关闭。Livolog 的 Language / Theme 就是这种。
// 返回选中的 id；点外部或按 Esc 返回 null。
function pickOption(title, options, currentId, anchorEl) {
  return new Promise((resolve) => {
    ensureModalCss();

    const menu = document.createElement('div');
    menu.className = 'tmt-menu';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', title || '');
    menu.innerHTML = options
      .map((o, i) =>
        `<button type="button" role="menuitem" class="tmt-menu-item${o.id === currentId ? ' is-selected' : ''}" data-i="${i}">${o.name}</button>`)
      .join('');
    document.body.appendChild(menu);

    // 定位：优先对齐触发按钮的左缘，下方；下方空间不够则翻到上方。
    const anchor = anchorEl || null;
    const place = () => {
      const r = anchor.getBoundingClientRect();
      const mw = menu.offsetWidth;
      const mh = menu.offsetHeight;
      let left = r.left;
      if (left + mw > window.innerWidth - 8) left = Math.max(8, window.innerWidth - mw - 8);
      let top = r.bottom + 6;
      if (top + mh > window.innerHeight - 8) {
        const upTop = r.top - mh - 6;
        top = upTop >= 8 ? upTop : Math.max(8, window.innerHeight - mh - 8);
      }
      menu.style.left = `${left}px`;
      menu.style.top = `${top}px`;
    };
    if (anchor) place();

    let done = false;
    const finish = (val) => {
      if (done) return;
      done = true;
      document.removeEventListener('pointerdown', onOutside, true);
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('resize', place);
      menu.remove();
      if (anchor) anchor.setAttribute('aria-expanded', 'false');
      resolve(val);
    };

    // ⚠️ 用捕获阶段 + pointerdown：菜单项自己的 click 还在冒泡途中，
    //    若用冒泡阶段的 document click，点菜单项会先被当成「外部点击」关掉。
    const onOutside = (e) => {
      if (menu.contains(e.target)) return;
      if (anchor && anchor.contains(e.target)) return;
      finish(null);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(null); }
    };

    menu.querySelectorAll('.tmt-menu-item').forEach((btn) => {
      btn.addEventListener('click', () => finish(options[Number(btn.dataset.i)].id));
    });
    if (anchor) anchor.setAttribute('aria-expanded', 'true');

    // 延后一拍再挂监听：触发菜单的那次 pointerdown/click 还没结束
    setTimeout(() => {
      document.addEventListener('pointerdown', onOutside, true);
      document.addEventListener('keydown', onKey, true);
      window.addEventListener('resize', place);
    }, 0);
  });
}

document.getElementById('setting-language').addEventListener('click', async (e) => {
  const id = await pickOption(
    i18n('setting.language'),
    LANGS,
    settings.language,
    e.currentTarget,
  );
  if (id) {
    settings.language = id;
    saveSettings();
    // setLang 必须在 renderSettings 之前 —— 否则这一帧还是旧语言
    setLang(id);
    renderSettings();
    // 回会话页时标题也要跟着变；如果当前就在设置页，刷新一次即可
    showPage(currentPage);
  }
});

document.getElementById('setting-theme').addEventListener('click', async (e) => {
  const id = await pickOption(i18n('setting.theme'), THEMES, settings.theme, e.currentTarget);
  if (id) { settings.theme = id; saveSettings(); renderSettings(); }
});

// ── 字号：设置项只显示当前值，点开在表单里调 ────────────────────────────
// 表单内容照 Scholarius 阅读视图的 .reader-size-controls：
// 「减 / 数值输入 / 加」，外加一条滑块（同参考应用的交互习惯）。
// ⚠️ 调节时**实时**作用于页面（applyFontSize 直接改 --fs），这样用户能边调边看；
//    点「CANCEL」回滚到打开表单时的值，点「SAVE」保留。
document.getElementById('setting-fontsize').addEventListener('click', async () => {
  const original = settings.fontSize;
  const body =
    '<div class="tmt-size-row">' +
    `<button class="size-btn" type="button" data-step="-1" aria-label="${i18n('aria.decreaseFont')}">` +
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M19 13H5v-2h14v2z"/></svg></button>' +
    `<input class="size-input" type="number" inputmode="numeric" min="${FONT_MIN}" max="${FONT_MAX}" step="1" aria-label="${i18n('font.aria')}" value="${original}">` +
    `<button class="size-btn" type="button" data-step="1" aria-label="${i18n('aria.increaseFont')}">` +
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg></button>' +
    '</div>' +
    `<input class="tmt-size-range" type="range" min="${FONT_MIN}" max="${FONT_MAX}" step="1" aria-label="${i18n('font.aria')}">` +
    `<p class="tmt-size-hint">${i18n('font.range', { min: FONT_MIN, max: FONT_MAX })}</p>`;

  const result = await tmtPrompt({
    title: i18n('font.title'),
    body,
    okText: i18n('action.save'),
    cancelText: i18n('action.cancel'),
    setup(bodyEl) {
      const num = bodyEl.querySelector('.size-input');
      const range = bodyEl.querySelector('.tmt-size-range');
      const sync = (val) => {
        const v = Math.min(FONT_MAX, Math.max(FONT_MIN, Math.round(val)));
        num.value = String(v);
        range.value = String(v);
        bodyEl.querySelector('[data-step="-1"]').disabled = v <= FONT_MIN;
        bodyEl.querySelector('[data-step="1"]').disabled = v >= FONT_MAX;
        setFontSize(v);
      };
      sync(settings.fontSize);
      bodyEl.querySelectorAll('.size-btn').forEach((btn) => {
        btn.addEventListener('click', () => sync(Number(num.value) + Number(btn.dataset.step)));
      });
      // range 用 input 事件实时跟手；number 同理，但允许中间态为空/超范围
      range.addEventListener('input', () => sync(Number(range.value)));
      num.addEventListener('input', () => {
        const raw = num.value.trim();
        if (raw === '') return;
        const n = Number(raw);
        if (!Number.isFinite(n) || n < FONT_MIN || n > FONT_MAX) return;
        sync(n);
      });
      num.addEventListener('blur', () => {
        const n = Number(num.value.trim());
        sync(Number.isFinite(n) && n > 0 ? n : settings.fontSize);
      });
    },
  });

  // 取消 → 回滚到打开时的值（先于任何后续重渲染）
  if (result === null || result === false) {
    setFontSize(original);
  } else {
    setFontSize(settings.fontSize);
  }
  renderSettings();
});

document.getElementById('setting-tsip').addEventListener('click', async () => {
  const v = await tmtPrompt({
    title: i18n('setting.tsIp'),
    message: i18n('tsip.prompt'),
    input: { placeholder: '100.x.y.z', value: settings.tsIp },
    okText: i18n('action.save'),
    cancelText: i18n('action.cancel'),
  });
  if (v === null) return;
  settings.tsIp = v;
  saveSettings();
  renderSettings();
});

document.getElementById('setting-port').addEventListener('click', async () => {
  const v = await tmtPrompt({
    title: i18n('setting.port'),
    message: i18n('port.prompt'),
    input: { placeholder: '8899', value: String(settings.port), type: 'number' },
    okText: i18n('action.save'),
    cancelText: i18n('action.cancel'),
  });
  if (v === null) return;
  const n = parseInt(v, 10);
  if (!n || n < 1 || n > 65535) { err(i18n('port.invalid')); return; }
  settings.port = n;
  saveSettings();
  renderSettings();
});

// ── 连接设置：顶栏左侧的链条图标 → 表单（IP + 端口），可当场测试 ────────
// 设置页里的 Tailscale IP / Port 两项与这里读写同一份 settings，
// 任何一处改动都会同步（saveSettings → renderSettings 刷新设置页显示值）。
// ⚠️ 原来是设置页里一个「Test Connection」行；现在改成顶栏入口，
//    因为连接是高频动作，不该埋在设置里翻两层。
const CONN_FORM_BODY = `
  <label class="tmt-field">
    <span class="tmt-field-label" data-i18n="conn.ip">Tailscale IP</span>
    <input class="tmt-input tmt-conn-ip" type="text" inputmode="decimal"
           autocomplete="off" autocapitalize="off" spellcheck="false"
           placeholder="100.x.y.z">
  </label>
  <label class="tmt-field">
    <span class="tmt-field-label" data-i18n="conn.port">Port</span>
    <input class="tmt-input tmt-conn-port" type="number" inputmode="numeric"
           min="1" max="65535" placeholder="8899">
  </label>
  <p class="tmt-conn-status" hidden></p>
  <button class="tmt-conn-test" type="button" data-i18n="conn.test">TEST CONNECTION</button>
`;

function openConnectionForm() {
  return tmtPrompt({
    title: i18n('conn.title'),
    body: CONN_FORM_BODY,
    okText: i18n('action.save'),
    cancelText: i18n('action.cancel'),
    setup(bodyEl) {
      const ipEl = bodyEl.querySelector('.tmt-conn-ip');
      const portEl = bodyEl.querySelector('.tmt-conn-port');
      const stat = bodyEl.querySelector('.tmt-conn-status');
      const testBtn = bodyEl.querySelector('.tmt-conn-test');
      ipEl.value = settings.tsIp || '';
      portEl.value = String(settings.port || 8899);
      // 表单内文案也要跟随语言（body 是模板字符串，写死的是英文）
      bodyEl.querySelectorAll('[data-i18n]').forEach((el) => {
        el.textContent = i18n(el.dataset.i18n);
      });

      const say = (msg, kind) => {
        stat.textContent = msg;
        stat.hidden = !msg;
        stat.dataset.kind = kind || '';
      };
      const readForm = () => ({
        ip: ipEl.value.trim(),
        port: parseInt(portEl.value, 10),
      });
      const isValid = ({ ip, port }) =>
        ip.length > 0 && Number.isFinite(port) && port >= 1 && port <= 65535;

      testBtn.addEventListener('click', async () => {
        const form = readForm();
        if (!isValid(form)) { say(i18n('conn.invalid'), 'bad'); return; }
        say(i18n('conn.testing'), 'idle');
        testBtn.disabled = true;
        // 用**表单里的值**去测，不是 settings —— 用户此刻还没点保存
        const url = /^https?:\/\//i.test(form.ip)
          ? form.ip.replace(/\/+$/, '')
          : `http://${form.ip}:${form.port}`;
        try {
          const res = await fetch(`${url}/api/sessions`, {
            headers: { 'Content-Type': 'application/json' },
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = await res.json();
          const n = Object.keys(data || {}).length;
          say(i18n('conn.ok').replace('{n}', String(n)), 'good');
        } catch (e2) {
          say(i18n('conn.fail').replace('{err}', e2.message), 'bad');
        } finally {
          testBtn.disabled = false;
        }
      });
    },
    onAccept(bodyEl) {
      const ipEl = bodyEl?.querySelector('.tmt-conn-ip');
      const portEl = bodyEl?.querySelector('.tmt-conn-port');
      if (!ipEl || !portEl) return true;
      const ip = ipEl.value.trim();
      const port = parseInt(portEl.value, 10);
      // 校验失败就不关闭弹窗，让用户改
      if (!ip || !Number.isFinite(port) || port < 1 || port > 65535) {
        const stat = bodyEl.querySelector('.tmt-conn-status');
        if (stat) {
          stat.textContent = i18n('conn.invalid');
          stat.hidden = false;
          stat.dataset.kind = 'bad';
        }
        return false;
      }
      settings.tsIp = ip;
      settings.port = port;
      saveSettings();
      renderSettings();
      return true;
    },
  });
}

document.getElementById('btn-conn')?.addEventListener('click', () => {
  openConnectionForm();
});

document.getElementById('setting-contact').addEventListener('click', () => {
  // 照 Livolog / Scholarius 的做法：Contact 显示邮箱，点击直接发信
  const url = `mailto:${CONTACT_EMAIL}`;
  if (window.Capacitor?.Plugins?.Browser) {
    window.Capacitor.Plugins.Browser.open({ url });
  } else {
    window.location.href = url;
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
// 启动：直接进 Sessions（无登录页）
// ⚠️ setLang + applyI18n 必须在第一次 showPage 之前 —— showPage 会读
//    i18n(def.titleKey) 写标题，晚一步就会先闪一下默认语言。
setLang(settings.language);
applyI18n();
applyTheme();
applyFontSize();
showPage('sessions');

// ══════════════════════════════════════════════════════════════════════
// 自动更新
// ══════════════════════════════════════════════════════════════════════
// ⚠️⚠️ 更新检查**不能**依赖 hasConnection()。
//     hasConnection() 判断的是「有没有填 Tailscale IP」，而升级检查访问的是
//     **GitHub Releases**，两者毫无关系。
//     重构时我把它包在 `if (hasConnection())` 里，后果是：
//       · 首次安装（还没填 IP）→ 永远不检查更新
//       · 填了 IP 但电脑关机 → 也不检查
//     等于自动更新能力基本废掉。
//     旧版就是无条件在启动 1.5s 后检查一次、回到前台再检查。

/** 检查更新；失败静默（离线/被墙/GitHub 抽风都不该打扰用户）。 */
function runUpdateCheck() {
  checkForUpdate().catch(() => {});
}

// 1) 冷启动：等品牌动画播完再查，避免启动瞬间抢带宽
setTimeout(runUpdateCheck, 1500);

// 2) 回到前台时再查一次 —— 手机上的 App 很少真正"重启"，
//    大部分时间是从后台切回来。不监听这个事件的话，用户可能几天都收不到更新。
//    updater.js 内部有 30s 的防抖，重复触发无害。
App.addListener('appStateChange', ({ isActive }) => {
  if (isActive) runUpdateCheck();
});

// 3) 网络恢复时也查一次（例如从飞行模式切回来）
window.addEventListener('online', runUpdateCheck);
