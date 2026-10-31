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
// 弹窗 / 菜单的样式在运行时注入。为什么不全放 styles.css：
// 这段是动态创建的 DOM，样式跟着 JS 一起走更省心（参考应用 Livolog 的
// .menu 也是运行时注入的）。
//
// ⚠️ 颜色一律用 CSS 变量，**不要写死色值**。
//    写死的话 App 切到日间主题后，弹窗仍是深色底 + 深色文字，
//    在浅色页面上一片糊（v0.10.5 → v0.10.6 修的就是这个）。
//    变量来自 styles.css 的 :root / :root[data-theme="light"]，自动跟随。
const TMT_CSS = `
.tmt-overlay{position:fixed;inset:0;background:rgba(0,0,0,.32);display:flex;align-items:flex-end;justify-content:center;z-index:9999;font-family:'Termetron Mono','Roboto Mono','Droid Sans Mono',monospace}
.tmt-modal{background:var(--surface);border:0;border-radius:20px 20px 0 0;padding:8px 20px calc(20px + env(safe-area-inset-bottom));width:min(560px,100%);color:var(--on-surface);box-shadow:0 -8px 32px rgba(0,0,0,.28);text-align:left}
.tmt-modal h3{color:var(--nav-active);margin:12px 0 8px;font-size:15px;font-weight:700}
.tmt-modal p{margin:12px 0 8px;font-size:13px;line-height:1.55;color:var(--on-surface-variant);white-space:pre-line;word-break:break-word}
.tmt-actions{display:flex;align-items:center;gap:12px;justify-content:space-between;padding-top:18px;margin-top:0}
/* 只有一个按钮时居中（Scholarius 的 .setting-action.setting-center） */
.tmt-actions:has(:only-child){justify-content:center}
.tmt-btn{padding:14px 4px;border:0;border-radius:0;background:none;font:inherit;font-size:15px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;cursor:pointer;color:var(--success);-webkit-tap-highlight-color:transparent;transition:opacity .12s ease}
.tmt-btn:active{opacity:.55}
.tmt-btn.cancel{color:var(--on-surface-variant)}
.tmt-input{width:100%;box-sizing:border-box;padding:2px 0;border:0;border-radius:0;background:transparent;color:var(--on-surface);font:inherit;font-size:15px;outline:none}
.tmt-input::placeholder{color:var(--on-surface-variant);opacity:.55}
/* ── 下拉菜单：逐项照 Livolog 的 .menu / .menu-item 实测值 ────────────
   Livolog 实测：背景 var(--surface)、圆角 12px、边框 1px var(--outline)、
   padding 6px、min-width 160px、阴影 0 8px 24px rgba(0,0,0,.22)、
   项 padding 10px 14px / 14px 字号、选中项只加粗到 600 不改色。 */
.tmt-menu{position:fixed;z-index:10000;min-width:160px;max-height:min(60vh,320px);overflow-y:auto;box-sizing:border-box;padding:6px;background:var(--surface);border:1px solid var(--outline);border-radius:12px;box-shadow:0 8px 24px rgba(0,0,0,.22);font-family:'Termetron Mono','Roboto Mono','Droid Sans Mono',monospace}
.tmt-menu-item{display:block;width:100%;padding:10px 14px;border:0;border-radius:8px;background:transparent;color:var(--on-surface);font:inherit;font-size:14px;font-weight:400;line-height:1.45;text-align:left;cursor:pointer;-webkit-tap-highlight-color:transparent}
.tmt-menu-item:active{background:var(--surface-raised)}
.tmt-menu-item.is-selected{font-weight:600}
/* ── 字号表单：中间「减 / 数值 / 加」一行 + 下方滑块 ───────────────────
   按钮/输入框尺寸照 Scholarius 阅读视图 .reader-size-controls 实测值：
   按钮 32×32 圆形、输入 60×28 / 14px / 居中 / 1px var(--outline) / 圆角 8px。 */
.tmt-body{margin:4px 0 8px}
.tmt-size-row{display:flex;align-items:center;justify-content:center;gap:4px}
.tmt-size-row .size-btn{display:grid;place-items:center;width:32px;height:32px;padding:0;border:0;border-radius:50%;background:none;color:var(--on-surface-variant);cursor:pointer;-webkit-tap-highlight-color:transparent}
.tmt-size-row .size-btn:active{opacity:.55}
.tmt-size-row .size-btn:disabled{opacity:.32;pointer-events:none}
.tmt-size-row .size-btn svg{width:22px;height:22px;fill:currentColor}
.tmt-size-row .size-input{width:60px;height:28px;box-sizing:border-box;padding:0 6px;border:1px solid var(--outline);border-radius:8px;background:transparent;color:var(--on-surface);font:inherit;font-size:14px;font-variant-numeric:tabular-nums;text-align:center;outline:none;-moz-appearance:textfield;appearance:textfield}
.tmt-size-row .size-input::-webkit-outer-spin-button,
.tmt-size-row .size-input::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
.tmt-size-row .size-input:focus{border-color:var(--on-surface-variant)}
.tmt-size-range{display:block;width:100%;height:24px;margin:14px 0 0;padding:0;background:transparent;-webkit-appearance:none;appearance:none}
.tmt-size-range::-webkit-slider-runnable-track{height:3px;border-radius:2px;background:var(--outline)}
.tmt-size-range::-moz-range-track{height:3px;border-radius:2px;background:var(--outline)}
.tmt-size-range::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:16px;height:16px;margin-top:-6.5px;border:0;border-radius:50%;background:var(--nav-active);box-shadow:0 1px 4px rgba(0,0,0,.4)}
.tmt-size-range::-moz-range-thumb{width:16px;height:16px;border:0;border-radius:50%;background:var(--nav-active);box-shadow:0 1px 4px rgba(0,0,0,.4)}
.tmt-size-hint{margin:8px 0 0;font-size:12px;color:var(--on-surface-variant);text-align:center}
/* ── 连接表单 ─────────────────────────────────────────────────────────── */
.tmt-field{display:block;margin:0 0 14px}
.tmt-field-label{display:block;margin:0 0 4px;font-size:12px;letter-spacing:.4px;color:var(--on-surface-variant)}
.tmt-field .tmt-input{border-bottom:1px solid var(--outline);padding:4px 0}
.tmt-field .tmt-input:focus{border-bottom-color:var(--on-surface-variant)}
.tmt-conn-status{margin:4px 0 0;font-size:13px;line-height:1.5;white-space:pre-line;word-break:break-word}
.tmt-conn-status[data-kind="good"]{color:var(--success)}
.tmt-conn-status[data-kind="bad"]{color:var(--danger)}
.tmt-conn-status[data-kind="idle"]{color:var(--on-surface-variant)}
.tmt-conn-test{display:block;width:100%;margin:14px 0 0;padding:11px 0;border:1px solid var(--outline);border-radius:10px;background:transparent;color:var(--on-surface);font:inherit;font-size:13px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;cursor:pointer;-webkit-tap-highlight-color:transparent}
.tmt-conn-test:active{background:var(--surface-raised)}
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
    notifyOverlayChange();

    const finish = (val) => {
      overlay.remove();
      document.removeEventListener('keydown', kd);
      notifyOverlayChange();
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

  // 右格：终端页始终显示「复制 + 中断」。
  // ⚠️ 中断按钮**不按 busy 显隐**：它等价于键盘上的 Ctrl+C —— 键盘不会
  //    只在程序运行时才出现，按钮也不该。按一下没有程序在跑时，只是往
  //    shell 发一个空中断（cmd 会静默忽略），代价为零；
  //    而"按钮忽然出现/消失"反而让人在需要时找不到它。
  const copyBtn = document.getElementById('btn-term-copy');
  if (copyBtn) copyBtn.hidden = !isTerminal;
  const ctrlBtn = document.getElementById('btn-term-ctrlc');
  if (ctrlBtn) ctrlBtn.hidden = !isTerminal;

  // ⚠️ 终端页不显示底部导航：终端是「全屏工作区」，多一条 64px 的导航条
  //    会白吃掉一整行高度，而且用户在终端里只会敲命令、不会切 tab
  //    （返回靠标题栏的左箭头）。其余页面保留导航。
  const bottomNav = document.querySelector('.bottom-nav');
  if (bottomNav) bottomNav.hidden = isTerminal;

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

// ⚠️ 用**惰性获取**而不是模块级常量：
//    `const el = document.getElementById(...)` 在模块顶层执行，一旦脚本
//    的执行时机早于 DOM 解析（打包顺序、WebView 的 module 加载差异），
//    拿到的是 null，之后所有 `el.hidden = ...` 都静默失效 —— 表现就是
//    "空状态永远不出现，也没有任何报错"。每次取反而更稳。
const sessionListEl = () => document.getElementById('session-list');
const sessionsEmptyEl = () => document.getElementById('sessions-empty');

// 会话行：名称在上、绝对路径在下（同一列），状态固定在右侧
//   <li class="session-row">
//     <button class="session-btn">
//       <span class="session-text">            ← 左侧竖排两行，占满剩余宽度
//         <span class="session-name">名字</span>
//         <span class="session-cwd">E:\code\quant</span>
//       <span class="session-state">running</span>
function sessionRow(name, info) {
  const inf = info || {};
  const li = document.createElement('li');
  li.className = 'session-row';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'session-btn';

  const text = document.createElement('span');
  text.className = 'session-text';

  const label = document.createElement('span');
  label.className = 'session-name';
  label.textContent = name;
  text.appendChild(label);

  // 当前工作目录：后端从 cmd 提示符解析，取不到（如 agent 会话）就不渲染
  if (inf.cwd) {
    const cwd = document.createElement('span');
    cwd.className = 'session-cwd';
    cwd.textContent = inf.cwd;
    cwd.title = inf.cwd;
    text.appendChild(cwd);
  }

  const value = document.createElement('span');
  value.className = 'session-state';
  const done = inf.done && !inf.busy;
  value.textContent = i18n(inf.busy ? 'session.running' : done ? 'session.done' : 'session.idle');
  if (inf.busy) value.classList.add('is-running');

  btn.append(text, value);
  btn.addEventListener('click', () => openSession(name));
  li.appendChild(btn);
  return li;
}

async function refreshSessions() {
  // 空状态只有**一种**：没有会话就显示「暂无会话」。
  // ⚠️ 不再按「未配置 IP / 连不上 / 无会话」分三种文案 —— 用户的判断很简单：
  //    列表空着就该有提示。分三种反而在"配置了但后端返回空"时漏掉提示
  //    （v0.10.7 的真机 bug 就出在这）。
  //    连接相关的提示交给「连接」按钮和设置页，不在列表区表达。
  const showEmpty = () => {
    const list = sessionListEl();
    const empty = sessionsEmptyEl();
    if (list) list.innerHTML = '';
    if (!empty) {
      console.warn('[termetron] #sessions-empty not found in DOM');
      return;
    }
    empty.hidden = false;
    const t = empty.querySelector('.empty-title');
    const h = empty.querySelector('.empty-hint');
    if (t) t.textContent = i18n('empty.noSessions');
    if (h) h.textContent = i18n('empty.noSessionsHint');
  };

  // 没配置连接：仍然列不出会话 → 同样是"无会话"空状态。
  if (!hasConnection()) {
    showEmpty();
    return;
  }
  try {
    const data = await api('/api/sessions');
    const list = sessionListEl();
    const empty = sessionsEmptyEl();
    if (list) list.innerHTML = '';
    const names = Object.keys(data || {});
    if (!names.length) {
      showEmpty();
      return;
    }
    if (empty) empty.hidden = true;
    // 单行渲染失败不拖垮整个列表：一行坏掉时其余照常显示，
    // 全部失败才回落到空状态。
    let ok = 0;
    for (const n of names) {
      try {
        list?.appendChild(sessionRow(n, data[n]));
        ok += 1;
      } catch (err) {
        console.warn('[termetron] failed to render session', n, err);
      }
    }
    if (!ok) showEmpty();
  } catch (err) {
    // 连不上时列表里本来也没有会话 → 依然是空状态。
    console.warn('[termetron] refreshSessions failed', err);
    showEmpty();
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
const btnTermCtrlC = document.getElementById('btn-term-ctrlc');
const btnTermCopy = document.getElementById('btn-term-copy');

// 最近一次渲染的**纯文本**（去掉控制序列），供复制按钮使用。
// 不直接读 termOut.textContent：那里已经是 HTML 结构，且含行间空白。
let lastOutputText = '';

// ── 轮询节奏 ─────────────────────────────────────────────────────────
// 原来固定 1s，交互式程序（逐字符回显、REPL）会明显卡顿。
// 改成分档：
//   · 有程序在跑（busy）→ 120ms，接近"实时"
//   · 空闲（等输入）    → 400ms，够快且省电
//   · 页面不可见        → 停止（省流量，回来时立刻补一次）
const POLL_BUSY_MS = 120;
const POLL_IDLE_MS = 400;
let pollDelay = POLL_BUSY_MS;

/** 用 setTimeout 自调度代替 setInterval，才能动态改间隔。 */
function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    await pullOutput();
    if (currentPage === 'terminal' && currentSession && !document.hidden) {
      schedulePoll();
    }
  }, pollDelay);
}

async function openSession(name) {
  currentSession = name;
  showPage('terminal');
  termOut.textContent = '';
  termOut._last = '';
  lastOutputText = '';
  pollDelay = POLL_BUSY_MS;   // 刚进会话，先按"忙"的节奏抢一次
  // ⚠️ 先算好字符网格再拉输出：pullOutput 要用 lastGrid.cols 做折行，
  //    顺序反了第一次渲染就没有折行宽度。
  await syncTerminalSize();
  await pullOutput();
  schedulePoll();
}

// ── ANSI → HTML ────────────────────────────────────────────────────────
// 后端用真 PTY（ConPTY），输出带颜色/光标序列。不渲染就只是一堆乱码。
// 只支持「显示相关」的子集：SGR 颜色/粗体、光标定位/清行、清屏。
// ⚠️ 其余序列一律丢弃 —— 显示不出来总比露出 `\x1b[?25l` 强。
const ANSI_FG = {
  30: '#4a4a4a', 31: '#E06C5E', 32: '#7EE2A8', 33: '#D7BA7D',
  34: '#6EA8FE', 35: '#C586C0', 36: '#4EC9B0', 37: '#ECECEA',
  90: '#7E7E7C', 91: '#F48771', 92: '#A5E8C0', 93: '#E8CFA0',
  94: '#9CC5FF', 95: '#D7A3D7', 96: '#7EE0D0', 97: '#FFFFFF',
};

function escHtml(s) {
  return s.replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/**
 * 算出当前输出区能放下多少个**等宽字符**（列）和多少行。
 *
 * 这是"屏幕适配"的核心：终端里所有内容都是等宽排版，列数决定了
 * 程序在哪里换行（`ls` 分几列、`top` 的表格宽度、长命令怎么断行）。
 * 只要 PTY 的 cols 和屏幕上真实能放下的字符数一致，显示就和真终端一样，
 * 不会出现"程序按 200 列排，在 40 列的手机上被二次折行"的错乱。
 *
 * 做法：
 *   1. 用 offscreen 的 canvas measureText 量出**当前字体下**一个 `M`
 *      字符的宽度（不同字体/字号下 monospace 宽度不同，硬编码会错）。
 *   2. 用输出区的 content 宽度（已扣掉 padding）除以单字符宽度。
 *   3. 行数 = content 高度 / 行高。
 */
let charW = 0;
function measureCharWidth(el) {
  if (charW > 0) return charW;
  const cs = getComputedStyle(el);
  const cv = document.createElement('canvas');
  const ctx = cv.getContext('2d');
  // 拼出完整 font shorthand，否则 canvas 会忽略 weight/family
  ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  const w = ctx.measureText('M').width;
  // 兜底：measureText 异常时按 fontSize * 0.6 估算（等宽字常见比例）
  charW = w > 0 ? w : parseFloat(cs.fontSize) * 0.6;
  return charW;
}

function computeGrid() {
  const cw = measureCharWidth(termOut);
  const cs = getComputedStyle(termOut);
  const padX = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
  const padY = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
  const availW = termOut.clientWidth - padX;
  const availH = termOut.clientHeight - padY;
  const lineH = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.5;
  // 留 1 列余量：浏览器与终端对"最后一个字符是否换行"的边界处理
  // 常差 1 格，宁可少 1 列也不要出现"每个满行末尾都折一个字符"。
  const cols = Math.max(20, Math.floor(availW / cw) - 1);
  const rows = Math.max(6, Math.floor(availH / lineH));
  return { cols, rows };
}

/** 把当前字符网格同步给后端 PTY（尺寸变了才发请求）。 */
let lastGrid = { cols: 0, rows: 0 };
let gridTimer = null;
async function syncTerminalSize(force) {
  if (!currentSession || currentPage !== 'terminal') return;
  const g = computeGrid();
  if (!force && g.cols === lastGrid.cols && g.rows === lastGrid.rows) return;
  lastGrid = g;
  // ⚠️ 本地立刻按新列数重绘：折行是前端做的，后端 resize 只影响程序的
  //    排版宽度。等后端返回再重绘会有明显延迟，旋转屏幕时很难受。
  if (currentPage === 'terminal') termOut._last = '';
  try {
    await api(`/api/sessions/${encodeURIComponent(currentSession)}/resize`, {
      method: 'POST',
      body: JSON.stringify(g),
    });
  } catch {
    /* 老后端没有这个接口时静默忽略（仍能工作，只是换行由后端决定） */
  }
  // 尺寸变了要立即重渲染一次，让折行位置马上跟着变
  await pullOutput();
}

/** 尺寸变化要做防抖：旋转屏幕/拖动窗口会连续触发。 */
function scheduleSizeSync() {
  clearTimeout(gridTimer);
  gridTimer = setTimeout(() => {
    // ⚠️ 必须清掉字符宽缓存：字号不变时它是对的，但一旦字号/字体变了
    //    （用户调了字号、系统字体缩放），旧值会让列数算错。
    charW = 0;
    const before = lastGrid;
    const g = computeGrid();
    // 只有网格真的变了才发请求（避免抖动期间狂发 resize）
    if (g.cols === before.cols && g.rows === before.rows) return;
    lastGrid = g;
    syncTerminalSize(true);
  }, 120);
}

window.addEventListener('resize', scheduleSizeSync);
window.addEventListener('orientationchange', () => setTimeout(scheduleSizeSync, 200));

/**
 * 判断一个字符在等宽终端里占几列（1 还是 2）。
 *
 * ⚠️ 终端里中文/日文/韩文和 emoji 是**双宽**的（占两格），而 JS 的
 *    `String.length` 把它们各算 1 个。不修正的话含中文的行会少折一半，
 *    屏幕上就被挤压。
 *    这里覆盖主要的宽字符区段（CJK 统一表意文字、假名、全角符号、emoji）。
 */
function charWidth(ch) {
  const c = ch.codePointAt(0);
  // 组合符号 / 零宽字符：不占列
  if (c === 0x200b || (c >= 0x0300 && c <= 0x036f)) return 0;
  // CJK、假名、谚文、全角符号、常见 emoji
  if (
    (c >= 0x1100 && c <= 0x115f) ||   // 谚文字母
    (c >= 0x2e80 && c <= 0xa4cf) ||   // CJK 部首 + 汉字 + 假名
    (c >= 0xac00 && c <= 0xd7a3) ||   // 谚文音节
    (c >= 0xf900 && c <= 0xfaff) ||   // CJK 兼容汉字
    (c >= 0xfe30 && c <= 0xfe6f) ||   // CJK 兼容符号
    (c >= 0xff00 && c <= 0xff60) ||   // 全角 ASCII
    (c >= 0xffe0 && c <= 0xffe6) ||   // 全角符号
    (c >= 0x1f300 && c <= 0x1f64f) || // emoji
    (c >= 0x1f900 && c <= 0x1f9ff) ||
    (c >= 0x20000 && c <= 0x3fffd)    // CJK 扩展
  ) return 2;
  return 1;
}

/**
 * 把终端原始文本转成"人类可读的纯文本"，用于复制。
 *
 * ⚠️ 只做"看得见"的转换，不做覆盖/重画 —— 这里的目标是让粘贴出来的
 *    内容干净可读，而不是复刻屏幕像素。
 *    · 删除所有 CSI / OSC 控制序列（颜色、光标、清屏）
 *    · `\r\n` → `\n`
 */
function stripAnsiForCopy(text) {
  if (!text) return '';
  return text
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '')  // OSC
    .replace(/\x1b\[[0-9;?<=>]*[A-Za-z]/g, '')           // CSI
    .replace(/\x1b[=>]|\x1b[()][A-Za-z0-9]/g, '')        // 其它短序列
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n');
}

/**
 * 把一屏带 ANSI 的文本转成 HTML。
 * @param {string} text 后端给的原始文本（含 \r、\x1b[...m 等）
 * @param {number} cols 屏幕能容纳的列数（0 = 不折行）
 */
function ansiToHtml(text, cols) {
  // ⚠️ 先把 CRLF 归一成 LF。后端历史里每行以 `\r` 结尾（ConPTY 原样保留），
  //    join('\n') 之后就是 `\r\n`；浏览器会把 `\r\n` 当**两个**换行，
  //    屏上立刻多出成倍空行。归一化必须在任何分行之前。
  text = text.replace(/\r\n/g, '\n');

  // 再把同一行内的 \r 当"回到行首覆盖"处理（进度条/top 的刷新语义）。
  // 此时的 \r 已经是"行内覆盖"而不是换行。
  const lines = text.split('\n').map((line) => {
    if (!line.includes('\r')) return line;
    let out = '';
    for (const seg of line.split('\r')) {
      out = seg + out.slice(seg.length);
    }
    return out;
  });

  // 折叠连续空行：后端的历史里会有成片的空行（ConPTY 重绘刷出来的），
  // 全渲染出来屏上就是一大段空白。最多保留一个。
  const collapsed = [];
  for (const ln of lines) {
    if (!ln.trim() && collapsed.length && !collapsed[collapsed.length - 1].trim()) {
      continue;
    }
    collapsed.push(ln);
  }

  let html = '';
  for (const raw of collapsed) {
    // ── 1. 先把一行拆成「文本片段 + 颜色状态」序列 ──────────────────
    // ⚠️ 不能在字符串里直接拼 <span>：折行是按**字符**切的，必须知道
    //    每个字符当前是什么颜色，才能在折行处正确地断开/续上标签。
    let s = raw.replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '');  // OSC
    const runs = [];      // [{ text, color, bold }]
    let cur = { text: '', color: null, bold: false };
    let i = 0;
    while (i < s.length) {
      // SGR？
      if (s[i] === '\x1b' && s[i + 1] === '[') {
        const end = s.indexOf('m', i);
        if (end > 0 && /^[0-9;]*$/.test(s.slice(i + 2, end))) {
          if (cur.text) { runs.push(cur); cur = { text: '', ...cur, }; }
          for (const c of s.slice(i + 2, end).split(';').filter(Boolean).map(Number)) {
            if (c === 0) { cur.color = null; cur.bold = false; }
            else if (c === 1) cur.bold = true;
            else if (ANSI_FG[c]) cur.color = ANSI_FG[c];
          }
          i = end + 1;
          continue;
        }
      }
      // 其它控制序列：整段丢掉
      if (s[i] === '\x1b') {
        const m = /^\x1b\[[0-9;?<=>]*[A-Za-z]/.exec(s.slice(i));
        i += m ? m[0].length : 1;
        continue;
      }
      cur.text += s[i];
      i += 1;
    }
    if (cur.text) runs.push(cur);

    // ── 2. 按显示列数折行，同时维护颜色标签 ──────────────────────────
    // 折行是纯显示行为：不改内容，只决定在哪里断开。
    // 每行独立开关 <span>，避免标签跨行导致后续行继承错误颜色。
    const colsEff = cols > 0 ? cols : Infinity;
    let lineHtml = '';
    let width = 0;
    let openTag = null;    // 当前打开着的 <span ...>
    const closeTag = () => { if (openTag) { lineHtml += '</span>'; openTag = null; } };
    const openFor = (color, bold) => {
      const want = color || bold;
      const sig = (color || '') + (bold ? 'b' : '');
      if (openTag && openTag.sig === sig) return;
      closeTag();
      if (want) {
        const style = [
          color ? `color:${color}` : '',
          bold ? 'font-weight:700' : '',
        ].filter(Boolean).join(';');
        lineHtml += `<span style="${style}">`;
        openTag = { sig };
      }
    };

    for (const run of runs) {
      for (const ch of run.text) {
        const cw = charWidth(ch);
        if (width + cw > colsEff) {
          closeTag();
          lineHtml += '\n';
          width = 0;
        }
        openFor(run.color, run.bold);
        lineHtml += escHtml(ch);
        width += cw;
      }
    }
    closeTag();
    html += lineHtml + '\n';
  }
  return html;
}

async function pullOutput() {
  if (!currentSession || currentPage !== 'terminal') return;
  try {
    const data = await api(`/api/output/${encodeURIComponent(currentSession)}`);
    // ⚠️ /api/output/<name> 返回的是 **JSON 对象**，不是纯文本：
    //    { lines, progress, done, busy, script, cmd, cwd, interactive, tail }
    const raw = Array.isArray(data?.lines) ? data.lines : [String(data ?? '')];
    // tail 是「尚未遇到换行」的当前行（REPL 的 `>>> ` 就在这里），
    // 不带它会让交互式程序看起来"卡住不动"。
    let all = localizeServerLines(raw).join('\n');
    if (data?.tail) all += '\n' + data.tail;

    // 折行列数用**自己算出来的网格**，不用后端返回的 cols：
    // 后端可能在 resize 之前还是旧值，而本地网格始终反映当前屏幕。
    const html = ansiToHtml(all, lastGrid.cols || 0);
    // 复制用的文本每次都更新，不能只在"画面变了"时更新 ——
    // 否则用户在内容滚出首屏后点复制，拿到的还是旧的一屏。
    lastOutputText = stripAnsiForCopy(all);
    if (termOut._last !== html) {
      const atBottom =
        termOut.scrollTop + termOut.clientHeight >= termOut.scrollHeight - 24;
      termOut.innerHTML = html;
      termOut._last = html;
      if (atBottom) termOut.scrollTop = termOut.scrollHeight;
    }

    // 节奏：有程序在跑就加快，空闲放慢。
    // ⚠️ 不要在这里动中断按钮的显隐 —— 它是常驻的（见 showPage 里的说明）。
    pollDelay = data?.busy ? POLL_BUSY_MS : POLL_IDLE_MS;
  } catch {
    /* 断线时静默，下个 tick 重试 */
  }
}

termForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  // ⚠️ 不再 trim：REPL 里空行回车有意义，且前导空格是命令的一部分。
  const text = termInput.value;
  if (!currentSession) return;
  termInput.value = '';
  try {
    await api(`/api/sessions/${encodeURIComponent(currentSession)}/input`, {
      method: 'POST',
      body: JSON.stringify({ text, raw: true, submit: true }),
    });
    setTimeout(pullOutput, 120);
  } catch (err) {
    tmtAlert(i18n('session.errSend', { err: err.message }));
  }
});

// Ctrl+C：真正的终端中断信号（写 0x03 到 PTY），不是杀进程
btnTermCtrlC?.addEventListener('click', async () => {
  if (!currentSession) return;
  try {
    await api(`/api/sessions/${encodeURIComponent(currentSession)}/input`, {
      method: 'POST',
      body: JSON.stringify({ text: '\u0003', raw: true }),
    });
    setTimeout(pullOutput, 150);
  } catch (err) {
    tmtAlert(i18n('session.errSend', { err: err.message }));
  }
});

// 复制当前会话的全部输出（纯文本，已剥掉 ANSI）
btnTermCopy?.addEventListener('click', async () => {
  if (!lastOutputText) {
    tmtToast(i18n('term.copyEmpty'));
    return;
  }
  try {
    await navigator.clipboard.writeText(lastOutputText);
    tmtToast(i18n('term.copied'));
  } catch {
    // WebView 里 clipboard API 可能不可用（需要安全上下文）：
    // 退回 textarea + execCommand，仍然能复制。
    try {
      const ta = document.createElement('textarea');
      ta.value = lastOutputText;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      tmtToast(i18n('term.copied'));
    } catch {
      tmtAlert(i18n('term.copyFailed'));
    }
  }
});

// 页面重新可见时立刻补一次（移动端切回前台要马上看到最新输出）
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && currentPage === 'terminal' && currentSession) {
    pollDelay = POLL_BUSY_MS;
    pullOutput();
    schedulePoll();
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

// ── 弹窗遮罩 ↔ 原生状态栏 ────────────────────────────────────────────
// 弹窗的 .tmt-overlay 是 fixed inset:0，但它**盖不到原生状态栏**
// （状态栏在 WebView 外面）。所以画面上“页面暗了一档”时，最顶上那条
// 仍是原亮度，看起来像一道割裂的亮带。
// 做法：弹窗打开期间把状态栏换成遮罩叠在页面底色上的等效色
// （即 rgba(0,0,0,.32) 混合后的结果），关掉后恢复。
// 用计数器是因为可能同时存在多层（例如弹窗里再开一个输入框）。
let openOverlays = 0;
const OVERLAY_ALPHA = 0.32;

/** 把 rgba(0,0,0,alpha) 叠在 hex 底色上，算出等效不透明色。 */
function blendOverlay(hex, alpha) {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  const k = 1 - alpha;
  const mix = (c) => Math.round(c * k).toString(16).padStart(2, '0');
  return `#${mix(r)}${mix(g)}${mix(b)}`.toUpperCase();
}

// ⚠️ overlay:false 是**一次性全局设置**，必须在启动时设，不能每次
//    syncNativeBars 都重设 —— 实测在 Android 上重复调用
//    setOverlaysWebView 会让状态栏重新覆盖 WebView，随后 setBackgroundColor
//    就被遮住看不见了（表现：弹窗打开后状态栏颜色没变）。
let statusBarReady = false;

async function initNativeBars() {
  const isAndroid = globalThis.Capacitor?.getPlatform?.() === 'android';
  try {
    if (isAndroid) {
      // 让 WebView 从状态栏下方开始（不覆盖），这样状态栏颜色才看得见
      await StatusBar.setOverlaysWebView({ overlay: false });
    }
    statusBarReady = true;
  } catch {
    /* 浏览器预览 / 插件不可用时忽略 */
  }
}

// ⚠️ 并发保护：syncNativeBars 会被连续调用（弹窗开 + 关、主题切换等），
//    多个 await 交错执行时后发的可能先完成，状态栏就停在错误的颜色上。
//    用一个递增序号只应用最后一次的结果。
let barSeq = 0;

async function syncNativeBars() {
  // 优先级：启动页 > 弹窗遮罩 > 主题
  //   · 启动期间：跟启动页固定底色（深色）
  //   · 有弹窗时：跟遮罩混合后的色，这样状态栏与页面“一起变暗”
  //   · 否则：跟当前主题
  let cfg;
  if (splashActive) {
    cfg = { bar: SPLASH_BG, style: Style.Dark };        // 深底 → 浅色图标
  } else if (openOverlays > 0) {
    const base = THEME_NATIVE[settings.theme] || THEME_NATIVE.dark;
    cfg = { bar: blendOverlay(base.bar, OVERLAY_ALPHA), style: base.style };
  } else {
    cfg = THEME_NATIVE[settings.theme] || THEME_NATIVE.dark;
  }
  const mySeq = ++barSeq;
  try {
    if (!statusBarReady) await initNativeBars();
    if (mySeq !== barSeq) return;      // 已有更新的请求，放弃这次
    await StatusBar.setBackgroundColor({ color: cfg.bar });
    await StatusBar.setStyle({ style: cfg.style });
  } catch {
    /* 浏览器预览 / 插件不可用时忽略：页面内主题已由 data-theme 控制 */
  }
}

/** 弹窗数量变化时调用：无条件重算（内容层数变化也要跟上）。 */
function notifyOverlayChange() {
  openOverlays = document.querySelectorAll('.tmt-overlay, .ut-overlay').length;
  syncNativeBars();
}

// updater.js 的弹窗（升级提示）自己创建 overlay，通过事件通知过来。
document.addEventListener('termetron:overlays-changed', notifyOverlayChange);

// updater.js 的弹窗（升级提示）自己创建 overlay，通过事件通知过来。
// 不直接 import 它的函数 —— 两模块互相 import 会形成循环依赖。
document.addEventListener('termetron:overlays-changed', notifyOverlayChange);

function err(msg) {
  // 统一在设置页给出反馈，不用弹窗打断
  tmtToast(msg);
}

// 启动页的固定底色 —— 必须与 styles.css 的 `.splash` background 和
// Android 原生启动屏（SPLASH_BG / windowSplashScreenBackground）三者一致。
// ⚠️ 不用 --bg：启动页是一次性品牌画面，不跟随应用内主题。
const SPLASH_BG = '#1B1B1B';

// 启动页是否还在屏幕上。为 true 时状态栏跟启动页底色走（见 syncNativeBars）。
// ⚠️ 必须在 applyTheme 之前声明：applyTheme → syncNativeBars 会在启动时
//    立即被调用，`let` 在初始化前处于 TDZ，连读取都会抛 ReferenceError。
let splashActive = !!document.getElementById('splash');

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

/** 启动页退场：移除元素，并把状态栏交回主题控制。 */
function dismissSplash() {
  if (!splashActive) return;
  splashActive = false;
  splashEl?.remove();
  // 启动期间状态栏是启动页色，这里切回当前主题色（深/浅各自对应）
  syncNativeBars();
}

if (splashEl) {
  // ⚠️ 必须判 `e.target === splashEl`：animationend 会从子元素冒泡上来，
  //    splash 里 `>` / 圆点 / `_` / 9 个字母各有动画，任一结束都会触发回调。
  //    实测（v0.8.18 bug）：`>` 在 918ms 画完就触发，整块被 remove，
  //    圆点、`_`、品牌字全没出现。
  // ⚠️ 不要加 { once: true }：第一个子元素的 animationend 会消耗掉监听器。
  splashEl.addEventListener('animationend', (e) => {
    if (e.target === splashEl) dismissSplash();
  });
  // 兜底：某些 WebView 下 animationend 可能不来，超时也要退场
  setTimeout(dismissSplash, 4200);
}

// 启动：直接进 Sessions（无登录页）
// ⚠️ setLang + applyI18n 必须在第一次 showPage 之前 —— showPage 会读
//    i18n(def.titleKey) 写标题，晚一步就会先闪一下默认语言。
setLang(settings.language);
applyI18n();
applyTheme();      // 内部会调 syncNativeBars；此时 splashActive=true → 状态栏=启动页色
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
