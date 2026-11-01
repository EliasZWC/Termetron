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
  // ── 隧道身份（长期不变）────────────────────────────────────────
  // ⚠️ 设计：**隧道码是身份，URL 是运行时产物**。
  //    · tunnelCode / tunnelPassword 由用户设置一次，之后永不改变
  //    · address 只是"最近一次可用的 URL"，会随隧道重建而变
  //    重连时：用 tunnelCode+password 向服务端确认身份，URL 由服务端告知。
  //    这样用户不必记住一串随机域名。
  tunnelCode: '',
  tunnelPassword: '',
  // 最近一次可用的地址（完整 URL）。会自动更新。
  address: '',
  // 兼容旧字段
  tsIp: '',
  port: 8899,
  // 备用地址列表：URL 失效时按顺序接力尝试。
  altEndpoints: [],
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
.tmt-menu-item{display:flex;align-items:center;width:100%;padding:10px 14px;border:0;border-radius:8px;background:transparent;color:var(--on-surface);font:inherit;font-size:14px;font-weight:400;line-height:1.45;text-align:left;cursor:pointer;-webkit-tap-highlight-color:transparent}
/* 图标固定 20px，文字占余下宽度；没有图标时 .tmt-menu-text 自然占满。
   ⚠️ 统一用 flex，不要为"有无图标"写两套 display —— :has() 在旧 WebView
      上支持不完整，一旦不生效就退化成 block，图标和文字会错位。 */
.tmt-menu-icon{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;margin-right:12px;flex:0 0 20px}
.tmt-menu-icon svg{fill:currentColor;display:block}
.tmt-menu-text{flex:1 1 auto;min-width:0}
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
.tmt-conn-test:disabled{opacity:.5}
.tmt-conn-test:active{background:var(--surface-raised)}
.tmt-conn-test:disabled{opacity:.45;pointer-events:none}
/* 高级折叠（地址）：默认收起，避免把传输层当成用户配置项。
   用原生 <details> 而不是自己管开关状态 —— 少一份 JS 就少一处状态不同步。 */
.tmt-adv{margin:2px 0 6px;font-family:'Termetron Mono','Roboto Mono','Droid Sans Mono',monospace}
.tmt-adv-summary{display:flex;align-items:center;gap:6px;list-style:none;cursor:pointer;padding:6px 0;font-size:12px;letter-spacing:.4px;text-transform:uppercase;color:var(--on-surface-variant);-webkit-tap-highlight-color:transparent}
.tmt-adv-summary::-webkit-details-marker{display:none}
/* 右侧指示箭头：官方 Material Symbols "expand_more" 的 path，
   展开时旋转 180°。不加箭头用户看不出那行字可以点。 */
.tmt-adv-summary::after{content:'';display:inline-block;width:16px;height:16px;flex:none;background:currentColor;opacity:.7;transition:transform .18s ease;
  -webkit-mask:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 -960 960 960'%3E%3Cpath d='M480-360 280-560h400L480-360Z'/%3E%3C/svg%3E") center/16px no-repeat;
  mask:url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 -960 960 960'%3E%3Cpath d='M480-360 280-560h400L480-360Z'/%3E%3C/svg%3E") center/16px no-repeat}
.tmt-adv[open] .tmt-adv-summary::after{transform:rotate(180deg)}
.tmt-adv-summary:active{opacity:.6}
.tmt-adv[open] .tmt-adv-summary{margin-bottom:2px}
.tmt-adv-body{margin:6px 0 0}
.tmt-field-hint{display:block;margin:6px 0 0;font-size:12px;line-height:1.4;color:var(--on-surface-variant);opacity:.75}
/* ── 扫码入口 ─────────────────────────────────────────────────────────
   放在表单最上方，是「配对」的首选路径：电脑端打出的二维码里带着
   隧道码和当前地址，扫一下就填好，用户完全不用碰网址。
   样式跟 TEST CONNECTION 一致：细边框、透明底。 */
.tmt-scan-btn{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;margin:0 0 6px;padding:14px 0;border:1px solid var(--outline);border-radius:10px;background:transparent;color:var(--on-surface);font:inherit;font-size:13px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;cursor:pointer;-webkit-tap-highlight-color:transparent}
.tmt-scan-btn svg{width:20px;height:20px;fill:currentColor;flex:none}
.tmt-scan-btn:active{background:var(--surface-raised)}
.tmt-scan-btn:disabled{opacity:.5;pointer-events:none}
.tmt-scan-status{margin:4px 0 0;font-size:13px;line-height:1.5;white-space:pre-line;word-break:break-word}
.tmt-scan-status[data-kind="good"]{color:var(--success)}
.tmt-scan-status[data-kind="bad"]{color:var(--danger)}
.tmt-scan-status[data-kind="idle"]{color:var(--on-surface-variant)}
/* 分隔线：把「扫码」和「手输」明确分成两条路 */
.tmt-or{display:flex;align-items:center;gap:10px;margin:14px 0}
.tmt-or::before,.tmt-or::after{content:'';flex:1;height:1px;background:var(--outline)}
.tmt-or span{font-size:11px;letter-spacing:1px;color:var(--on-surface-variant);opacity:.8}
/* ── 连接进度弹窗（.ut-*）───────────────────────────────────────────────
   ⚠️ 这套类名属于 updater.js 的 MODAL_CSS，但那个文件只在自己弹更新框时
      注入样式。showConnProgress() 直接用 .ut-overlay 却没注入 → 元素没有
      position:fixed，会按普通流排在页面里（表现就是"缓冲条出现在导航栏
      下方"）。这里必须自带一份，不能指望 updater 先跑过。
      数值与 updater.js 的 MODAL_CSS 保持一致，改一边要同步另一边。 */
.ut-overlay{position:fixed;inset:0;background:rgba(0,0,0,.32);display:flex;align-items:flex-end;justify-content:center;z-index:9999;font-family:'Termetron Mono','Roboto Mono','Droid Sans Mono',monospace}
.ut-modal{background:var(--surface);border:0;border-radius:20px 20px 0 0;padding:8px 20px calc(20px + env(safe-area-inset-bottom));width:min(560px,100%);color:var(--on-surface);box-shadow:0 -8px 32px rgba(0,0,0,.28);text-align:left}
.ut-modal h3{color:var(--nav-active);margin:12px 0 8px;font-size:15px;font-weight:700}
.ut-modal p{margin:0 0 8px;font-size:13px;line-height:1.55;color:var(--on-surface-variant);white-space:pre-line;word-break:break-word}
.ut-pbar{height:6px;background:var(--surface-raised);border-radius:3px;overflow:hidden;margin:2px 0 10px;position:relative}
.ut-pfill{height:100%;width:0;background:var(--nav-active);transition:width .2s ease}
.ut-pbar.indet .ut-pfill{width:36%;animation:ut-indet 1.4s ease-in-out infinite}
@keyframes ut-indet{0%{transform:translateX(-120%)}100%{transform:translateX(320%)}}
.ut-ppct{color:var(--on-surface-variant);font-size:12px;text-align:left;margin:0}
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
  // 隧道方案：地址就是一个完整 URL（https://xxx.trycloudflare.com）。
  // settings.address 是主字段；tsIp 保留兼容旧数据（曾存 IP 或完整 URL）。
  const raw = (settings.address || settings.tsIp || '').trim();
  if (!raw) return '';
  if (/^https?:\/\//i.test(raw)) return raw.replace(/\/+$/, '');
  // 裸地址（无协议）：补 http，并按需带端口
  return `http://${raw}:${settings.port}`;
}

function hasConnection() {
  return !!baseUrl();
}

// ⚠️ 所有请求都必须带超时。Android WebView 连一个**不可达**的 IP 时，
//    TCP 握手会挂到系统默认超时（实测 75~130 秒），界面就卡在那里不动。
//    默认 8 秒：局域网 / Tailscale 的正常往返都在毫秒级，8 秒已非常宽裕；
//    确实需要更久的接口用 `timeout` 单独放宽。
const API_TIMEOUT_MS = 8000;
// 接力探测要快：用户正在等，不能让每个候选都耗满 8 秒
const FAILOVER_TIMEOUT_MS = 3500;

/** 记录一个可用端点（去重、限量、最近使用的排第一）。 */
function rememberEndpoint(base) {
  if (!base) return;
  const list = [base, ...(settings.altEndpoints || []).filter((u) => u !== base)];
  settings.altEndpoints = list.slice(0, 6);
  saveSettings();
}

/**
 * 依次尝试主地址与备用地址，返回第一个**通过隧道码校验**的 base。
 *
 * ⚠️ 这是「接力」的信任根：URL 会变，隧道码不会。
 *    所以判断「这条地址是不是我的隧道」不能只看它能不能连通
 *    （任何 HTTP 服务都能 200），必须拿隧道码 + 密码去验证：
 *      GET /api/tunnel/verify?code=X&password=Y
 *    只有校验通过才认，否则继续试下一个候选。
 */
async function resolveBase() {
  const primary = baseUrl();
  const candidates = [primary, ...(settings.altEndpoints || [])]
    .filter(Boolean)
    .filter((u, i, a) => a.indexOf(u) === i);
  if (!candidates.length) return '';

  const code = settings.tunnelCode || '';
  const password = settings.tunnelPassword || '';

  const check = async (base) => {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), FAILOVER_TIMEOUT_MS);
    try {
      if (code && password) {
        // 有身份 → 严格校验：「这个地址后面是不是我那条隧道」
        const q = `code=${encodeURIComponent(code)}&password=${encodeURIComponent(password)}`;
        const res = await fetch(`${base}/api/tunnel/verify?${q}`, {
          signal: ac.signal,
          // 这一步同时负责「获取认证 cookie」—— 服务端在校验通过时下发
          // tmt_session，后续所有请求靠它过关。不 include 就白验一遍。
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
        });
        if (!res.ok) throw new Error('verify failed');
        const d = await res.json();
        if (!d?.ok) throw new Error('not my tunnel');
        // ⚠️ 校验通过时服务端会顺带回传**它自己当前的 tunnelUrl**。
        //    这是接力的关键：URL 变了，但隧道码没变，服务端知道新 URL，
        //    我们照单接管即可 —— 不需要用户手动改地址。
        if (d.tunnelUrl) return { ok: true, next: d.tunnelUrl };
        return { ok: true };
      }
      // 没配身份（本机调试等场景）→ 退化为「确认对端是 termetron」
      const res = await fetch(`${base}/api/discover`, {
        signal: ac.signal,
        headers: { 'Content-Type': 'application/json' },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      if (d?.app !== 'termetron') throw new Error('not termetron');
      if (d.tunnelUrl) return { ok: true, next: d.tunnelUrl };
      return { ok: true };
    } catch {
      return { ok: false };
    } finally {
      clearTimeout(timer);
    }
  };

  for (const base of candidates) {
    const r = await check(base);
    if (!r.ok) continue;
    // 服务端报告它现在挂在别的 URL 上 → 立刻改用它。
    // 旧地址一旦关闭，下一次握手就会失败，所以这里必须跟着走。
    if (r.next && r.next !== base) {
      adoptEndpoint(r.next);
      rememberEndpoint(r.next);
      // 新地址也要验一遍，避免接管到一个刚好也返回了 URL 的坏端点
      const r2 = await check(r.next);
      if (r2.ok) return r.next;
    }
    if (base !== primary) adoptEndpoint(base);
    rememberEndpoint(base);
    return base;
  }
  return '';
}

/** 把某个地址设为当前使用的地址（同步旧字段，保证 baseUrl() 一致）。 */
function adoptEndpoint(base) {
  if (!base) return;
  settings.address = base;
  settings.tsIp = base;
  saveSettings();
}

async function api(path, opts = {}) {
  const base = baseUrl();
  if (!base) throw new Error('NO_CONNECTION');
  const { timeout = API_TIMEOUT_MS, ...rest } = opts;
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeout);
  try {
    const res = await fetch(base + path, {
      ...rest,
      // ⚠️ credentials: 'include' 必须有。
      //    服务端在 /api/tunnel/verify 成功后下发 tmt_session cookie，
      //    后续接口靠它认证。App 的页面在 https://localhost，API 在
      //    *.trycloudflare.com —— 跨站请求，不显式声明就不会带 cookie，
      //    表现就是"测试连接成功，但列表为空、新建会话失败"。
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', ...(rest.headers || {}) },
      signal: ac.signal,
    });
    if (!res.ok) {
      // 401 不是「地址不对」，是「没配对」—— 交给调用方处理，不要接力
      if (res.status === 401) {
        const e = new Error('UNAUTHORIZED');
        e.status = 401;
        throw e;
      }
      // ⚠️ 优先用服务端返回的 error 文案，而不是只抛 "HTTP 409"。
      //    服务端对每种失败都有具体说明（session exists / name required ...），
      //    只显示状态码的话用户完全不知道自己哪里做错了 —— 实测有人为此困惑。
      let detail = '';
      try {
        const ct = res.headers.get('content-type') || '';
        if (ct.includes('json')) {
          const j = await res.json();
          detail = (j && (j.error || j.message)) || '';
        }
      } catch {
        /* 读不出正文就退回状态码 */
      }
      const e = new Error(detail || `HTTP ${res.status}`);
      e.status = res.status;
      e.detail = detail;
      throw e;
    }
    const ct = res.headers.get('content-type') || '';
    return ct.includes('json') ? res.json() : res.text();
  } catch (e) {
    // AbortError 的 message 在部分 WebView 上是空的，统一换成可读文案，
    // 否则调用方会把空字符串展示给用户。
    if (e.name === 'AbortError') throw new Error(i18n('conn.timeout'));
    // 网络层失败（不可达 / 隧道失效）→ 尝试接力到备用地址
    if (e.status !== 401 && (e.message === 'Failed to fetch' || e.name === 'TypeError')) {
      const alt = await resolveBase();
      if (alt && alt !== base) {
        return api(path, { ...opts, timeout });
      }
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
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

// ⚠️ 同理，**不要**在模块顶层缓存 DOM 引用。
//    `const titleEl = document.getElementById('topbar-title')` 若在 DOM 就绪前
//    执行，会得到 null，随后 showPage 里 `titleEl.textContent = ...` 抛
//    TypeError，**整个 showPage 中断** —— 底部那行
//    `if (name === 'sessions') refreshSessions()` 永远执行不到，
//    表现就是"会话列表一片空白、空状态也不出现、控制台没有明显线索"。
//    这正是 v0.10.7 / v0.10.8 三次都没修好空状态的根因。
const titleEl = () => document.getElementById('topbar-title');
const addBtnEl = () => document.getElementById('btn-add');

function showPage(name) {
  currentPage = name;
  const def = PAGES[name];
  if (!def) return;

  // 离开终端页就停止输出轮询（否则后台一直请求）
  if (name !== 'terminal') stopStream();

  for (const key of Object.keys(PAGES)) {
    const node = document.getElementById(PAGES[key].el);
    if (node) node.hidden = key !== name;
  }
  // 终端页顶部显示会话名，其它显示固定标题
  // ⚠️ 全部加空值守卫：任何一个元素缺失都不能让 showPage 中断，
  //    否则末尾的 refreshSessions() 会被跳过（空状态就不出现了）。
  const title = titleEl();
  if (title) {
    title.textContent =
      name === 'terminal'
        ? (currentSession || i18n('page.terminal'))
        : i18n(def.titleKey);
  }
  // Scholarius 式：新建按钮是标题栏右侧的图标按钮（而非 Livolog 的悬浮 FAB）
  const addBtn = addBtnEl();
  if (addBtn) addBtn.hidden = !def.showAdd;
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
  // 右格：终端页只留「中断 + 菜单」两个高频按钮。
  // ⚠️ 复制已移入菜单 —— 它不是高频操作（读完输出才复制一次），
  //    占着标题栏反而挤掉了更常用的位置。
  // ⚠️ 中断按钮**不按 busy 显隐**：它等价于键盘上的 Ctrl+C —— 键盘不会
  //    只在程序运行时才出现，按钮也不该。
  const ctrlBtn = document.getElementById('btn-term-ctrlc');
  if (ctrlBtn) ctrlBtn.hidden = !isTerminal;
  // 会话菜单：终端页才显示（复制/重命名/删除/信息都是针对当前会话的）
  const menuBtn = document.getElementById('btn-term-menu');
  if (menuBtn) menuBtn.hidden = !isTerminal;

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

// Android 返回键：**逐层回退**，最后才退出 App。
// ⚠️ 旧实现只有「终端页 → 会话列表」一层，其它页面按返回**直接退出应用** ——
//    用户从设置页按返回，整个 App 就没了，非常突然。
//    正确的层级（由近及远）：
//      1. 有弹窗/菜单打开 → 关掉它（相当于取消）
//      2. 终端页         → 回会话列表
//      3. 设置页         → 回会话列表（tab 的上一级）
//      4. 已在会话列表   → 退出 App
//    这样「返回」永远先收一层，符合用户对 Android 的既有预期。
App.addListener('backButton', () => {
  // 1) 先关最上层的浮层：菜单 / 弹窗 / 下拉
  const overlays = document.querySelectorAll('.tmt-overlay, .ut-overlay, .tmt-menu');
  if (overlays.length) {
    const top = overlays[overlays.length - 1];
    // 弹窗通过点「取消」关闭，才能走它自己的清理（resolve、移除监听、通知状态栏）
    const cancel = top.querySelector('.tmt-btn.cancel');
    if (cancel) {
      cancel.click();
    } else {
      // 下拉菜单没有取消按钮：派发一次 Esc 让它的 onKey 处理
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    }
    return;
  }
  // 2) 终端页 → 会话列表
  if (currentPage === 'terminal') {
    showPage('sessions');
    return;
  }
  // 3) 设置页 → 会话列表
  if (currentPage === 'setting') {
    showPage('sessions');
    return;
  }
  // 4) 已在顶层页面，才真正退出
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
  // 空状态分两种，因为用户该做的事完全不同：
  //   · 没连上 → 先连接（提示怎么连，并给个直达按钮）
  //   · 连上了但没会话 → 点 + 新建
  // ⚠️ 这两者**必须区分**：只显示「暂无会话」的话，没连接的用户会去点 +
  //    然后被拒，绕一圈才知道要先连。
  const showEmpty = (kind) => setSessionsEmpty(true, kind);

  // ⚠️⚠️ 关键：**先无条件把空状态设为可见**，再去拉数据。
  //    之前是先拉数据、按结果再决定显隐，走了三次真机都没生效 ——
  //    无论中间哪一步（hasConnection、fetch、JSON 解析、sessionRow）在
  //    真机上抛了什么，只要那段没跑完，空状态就永远停在 HTML 初始的
  //    `hidden` 上，而控制台又看不到（真机不方便开）。
  //    现在把它提到最前面：哪怕后面全炸了，用户至少看到提示。
  showEmpty('offline');

  if (!hasConnection()) return;
  try {
    const data = await api('/api/sessions');
    const list = sessionListEl();
    const names = Object.keys(data || {});
    if (!names.length) {
      showEmpty('noSessions');   // 连上了，只是还没有会话
      return;
    }
    let ok = 0;
    for (const n of names) {
      try {
        list?.appendChild(sessionRow(n, data[n]));
        ok += 1;
      } catch (err) {
        console.warn('[termetron] failed to render session', n, err);
      }
    }
    if (ok) setSessionsEmpty(false);
  } catch (err) {
    // 连不上时列表里本来也没有会话 → 保留空状态。
    console.warn('[termetron] refreshSessions failed', err);
    // 断线：安排一次有上限的自动重连（内部会判断是否已用完次数）
    noteConnectionLost();
  }
}

/**
 * 显示/隐藏会话空状态，并同步清空列表。
 * @param {boolean} show
 * @param {'offline'|'noSessions'} kind
 *   offline    —— 还没连上电脑：提示去连接
 *   noSessions —— 已连上但没有会话：提示点 + 新建
 */
function setSessionsEmpty(show, kind = 'noSessions') {
  const list = sessionListEl();
  const empty = sessionsEmptyEl();
  if (!empty) return;
  if (show) {
    if (list) list.innerHTML = '';
    empty.hidden = false;
    // 内联 display 兜底：万一某条 CSS 把 [hidden] 的 !important 压过去，
    // 也能保证元素真的可见（反过来设为 'none' 时同理）。
    empty.style.display = 'block';
    const t = empty.querySelector('.empty-title');
    const h = empty.querySelector('.empty-hint');
    if (kind === 'offline') {
      if (t) t.textContent = i18n('empty.offline') || 'Not Connected';
      if (h) h.textContent = i18n('empty.offlineHint') || '';
    } else {
      if (t) t.textContent = i18n('empty.noSessions') || 'No Session';
      if (h) h.textContent = i18n('empty.noSessionsHint') || '';
    }
  } else {
    empty.hidden = true;
    empty.style.display = '';
  }
}

// 新建会话
// ⚠️ 顺序很重要：**先确认能连上，再让用户输名字**。
//    旧实现只判 hasConnection()（"填过地址没有"），地址填了但隧道已失效时，
//    用户照样要输完名字、点了创建，才在最后一步看到失败 —— 白费一次输入。
//    现在先发一个轻量探测：连不上就直接提示"先远程连接"，根本不出表单。
addBtnEl()?.addEventListener('click', async () => {
  if (!hasConnection()) {
    await promptConnectFirst();
    return;
  }
  // 真正探一次，而不是只看"填过地址"
  const reachable = await probeServer();
  if (!reachable) {
    await promptConnectFirst();
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
    // 409 = 同名会话已存在。这不是故障，是用户取名撞了，提示要直接说清楚。
    if (err.status === 409) {
      tmtAlert(i18n('session.nameTaken', { name: name }));
      return;
    }
    tmtAlert(i18n('session.errCreate', { err: err.message }));
  }
});

/**
 * 轻量探测服务器是否可达（带接力）。
 * 返回 true/false，**不抛异常** —— 调用方只关心能不能继续。
 * 复用 resolveBase()：它本身就会按候选列表逐个试，命中即切换主地址，
 * 正好顺便把"隧道换了 URL"这件事修好。
 */
async function probeServer() {
  const base = await resolveBase();
  return !!base;
}

/**
 * 「还没连上」的统一出口：解释原因，并直接把用户送到连接表单。
 * 只弹一句"未连接"然后让用户自己去找入口，等于把问题丢回给用户。
 */
async function promptConnectFirst() {
  const ok = await tmtConfirm(i18n('session.needConnection'), {
    title: i18n('session.new'),
    okText: i18n('action.connect'),
    cancelText: i18n('action.cancel'),
  });
  if (ok) openConnectionForm();
}

// ═══════════════════════════════════════════════════════════════════════
// 终端页
// ═══════════════════════════════════════════════════════════════════════

const termOut = document.getElementById('term-out');
const termForm = document.getElementById('term-form');
const termInput = document.getElementById('term-input');
const btnTermCtrlC = document.getElementById('btn-term-ctrlc');
const btnTermMenu = document.getElementById('btn-term-menu');

// 最近一次渲染的**纯文本**（去掉控制序列），供复制按钮使用。
// 不直接读 termOut.textContent：那里已经是 HTML 结构，且含行间空白。
let lastOutputText = '';

// ── 轮询节奏 ─────────────────────────────────────────────────────────
// 原来固定 1s，交互式程序（逐字符回显、REPL）会明显卡顿。
// 改成分档：
//   · 有程序在跑（busy）→ 120ms，接近"实时"
//   · 空闲（等输入）    → 400ms，够快且省电
//   · 页面不可见        → 停止（省流量，回来时立刻补一次）
//   · 经隧道            → 不低于 300ms：公网往返本身就 100ms+，
//                        再密集只会让请求排队变慢，不会更快
const POLL_BUSY_MS = 120;
const POLL_IDLE_MS = 400;
const POLL_TUNNEL_MIN_MS = 300;
let pollDelay = POLL_BUSY_MS;

/** 当前是否经公网隧道（延迟高、有并发上限）。 */
function overTunnel() {
  return /\.trycloudflare\.com/i.test(baseUrl() || '');
}

/** 用 setTimeout 自调度代替 setInterval，才能动态改间隔。 */
function schedulePoll() {
  clearTimeout(pollTimer);
  const delay = overTunnel() ? Math.max(pollDelay, POLL_TUNNEL_MIN_MS) : pollDelay;
  pollTimer = setTimeout(async () => {
    await pullOutput();
    if (currentPage === 'terminal' && currentSession && !document.hidden) {
      schedulePoll();
    }
  }, delay);
}

// ── 服务端推送（SSE）────────────────────────────────────────────────
// 轮询的问题：经公网隧道时单次往返 100ms+，而轮询间隔 120ms ——
// 相当于每次刷新都要付一次过路费，输入会明显滞后。
// 推送后：服务端一有输出就发，客户端等着即可，延迟 = 一次单向传输。
//
// ⚠️ 必须有降级：某些隧道 / 反代会缓冲 SSE（数据攒着不发），表现为
//    「连上了但一直没输出」。所以连上后若 3 秒内没收到任何数据，
//    自动退回轮询。宁可慢一点，不能黑屏。
let sseSource = null;
let sseGotData = false;
let sseWatchdog = null;

function stopStream() {
  if (sseSource) {
    try { sseSource.close(); } catch { /* ignore */ }
    sseSource = null;
  }
  clearTimeout(sseWatchdog);
  sseWatchdog = null;
  clearTimeout(pollTimer);
  pollTimer = null;
}

function startStream(name) {
  stopStream();
  if (!name || typeof EventSource === 'undefined') {
    schedulePoll();
    return;
  }
  const base = baseUrl();
  if (!base) { schedulePoll(); return; }
  // ⚠️ Cloudflare Quick Tunnel **明确不支持 SSE**
  //    （官方文档 Limitations：「Quick Tunnels do not support Server-Sent
  //    Events (SSE)」）。在隧道下建 EventSource 只会挂住然后在 watchdog
  //    超时后退回轮询 —— 白等 3 秒，还多一次失败的连接。
  //    所以：认出是 trycloudflare 地址就直接用轮询，跳过试探。
  if (/\.trycloudflare\.com/i.test(base)) {
    schedulePoll();
    return;
  }
  sseGotData = false;
  try {
    sseSource = new EventSource(
      `${base}/api/stream/${encodeURIComponent(name)}`,
    );
  } catch {
    schedulePoll();
    return;
  }
  sseSource.onmessage = (ev) => {
    sseGotData = true;
    try {
      renderSnapshot(JSON.parse(ev.data));
    } catch { /* 半个包/坏包：忽略，下一条会补上 */ }
  };
  sseSource.onerror = () => {
    // 服务端关闭或网络断开：关掉并回到轮询兜底（轮询还能重连）
    if (sseSource) {
      try { sseSource.close(); } catch { /* ignore */ }
      sseSource = null;
    }
    schedulePoll();
  };
  // 看门狗：3 秒内一条数据都没有 → 判定这条链路不支持流式，退回轮询。
  sseWatchdog = setTimeout(() => {
    if (!sseGotData) {
      if (sseSource) {
        try { sseSource.close(); } catch { /* ignore */ }
        sseSource = null;
      }
      schedulePoll();
    }
  }, 3000);
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
  // 首帧用轮询拿一次（保证立即有内容），之后交给推送。
  startStream(name);
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

/**
 * 把一份快照渲染到屏幕。SSE 推送与轮询降级**共用**这一份逻辑 ——
 * 否则两条路径迟早渲染不一致（一处改了折行、另一处忘改）。
 */
function renderSnapshot(data) {
  // ⚠️ /api/output/<name> 与 /api/stream/<name> 返回同一种结构：
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
}

async function pullOutput() {
  if (!currentSession || currentPage !== 'terminal') return;
  try {
    const data = await api(`/api/output/${encodeURIComponent(currentSession)}`);
    renderSnapshot(data);
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
// ⚠️ 已从标题栏移入菜单，见 btnTermMenu 的处理。
async function copyOutput() {
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
}

// ── 会话菜单：重命名 / 删除 / 查看信息 ──────────────────────────────
btnTermMenu?.addEventListener('click', async (e) => {
  if (!currentSession) return;
  const anchor = e.currentTarget;
  const action = await pickOption(
    currentSession,
    [
      { id: 'copy', name: i18n('term.copyOutput'), icon: 'copy' },
      { id: 'rename', name: i18n('session.rename'), icon: 'edit' },
      { id: 'delete', name: i18n('session.delete'), icon: 'delete' },
      { id: 'info', name: i18n('session.info'), icon: 'info' },
    ],
    null,
    anchor,
  );
  if (action === 'copy') return copyOutput();
  if (action === 'rename') return renameSession();
  if (action === 'delete') return deleteSession();
  if (action === 'info') return showSessionInfo();
});

async function renameSession() {
  if (!currentSession) return;
  const old = currentSession;
  const next = await tmtPrompt({
    title: i18n('session.rename'),
    message: i18n('session.renameHint'),
    input: { value: old, placeholder: i18n('session.namePlaceholder') },
    okText: i18n('action.save'),
    cancelText: i18n('action.cancel'),
  });
  if (!next || next === old) return;
  try {
    await api(`/api/sessions/${encodeURIComponent(old)}/rename`, {
      method: 'POST',
      body: JSON.stringify({ name: next }),
    });
    currentSession = next;
    // 标题要跟着改名；同时重建流（新名字是新端点）
    const title = titleEl();
    if (title) title.textContent = next;
    startStream(next);
    tmtToast(i18n('session.renamed'));
  } catch (err) {
    tmtAlert(i18n('session.errRename', { err: err.message }));
  }
}

async function deleteSession() {
  if (!currentSession) return;
  const name = currentSession;
  const ok = await tmtConfirm(i18n('session.deleteConfirm', { name }), {
    title: i18n('session.delete'),
    okText: i18n('action.delete'),
    cancelText: i18n('action.cancel'),
  });
  if (!ok) return;
  try {
    await api(`/api/sessions/${encodeURIComponent(name)}`, { method: 'DELETE' });
    stopStream();
    currentSession = null;
    showPage('sessions');
    tmtToast(i18n('session.deleted'));
  } catch (err) {
    tmtAlert(i18n('session.errDelete', { err: err.message }));
  }
}

/**
 * 会话信息：把「当前它在用什么地址」明确展示出来。
 * 用户能直接看到 URL 是什么，隧道换了地址也能自己判断该更新成什么。
 */
async function showSessionInfo() {
  if (!currentSession) return;
  let remote = null;
  try {
    remote = await api('/api/remote/status');
  } catch { /* 本机直连时可能取不到，不影响显示其它字段 */ }
  const base = baseUrl() || '—';
  const rows = [
    [i18n('info.name'), currentSession],
    [i18n('info.endpoint'), base],
    [i18n('info.tunnel'), remote?.url || i18n('info.tunnelOff')],
    [i18n('info.auth'), remote?.url
      ? (remote?.authenticated ? i18n('info.paired') : i18n('info.notPaired'))
      : '—'],
  ];
  await tmtAlert(
    rows.map(([k, v]) => `${k}: ${v}`).join('\n'),
    i18n('session.info'),
  );
}


document.addEventListener('visibilitychange', () => {
  if (!document.hidden && currentPage === 'terminal' && currentSession) {
    pollDelay = POLL_BUSY_MS;
    pullOutput();
    // 切到后台时浏览器会关掉 SSE；回前台重连，重连前先用轮询兜着。
    startStream(currentSession);
  }
  // 回到前台 = 新的一轮：重试次数归零。
  // 用户可能刚路由好网络 / 电脑刚开机完成，不该因为上次用完 5 次就永远不试了。
  if (!document.hidden) resetAutoRetry();
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
  document.getElementById('setting-version').textContent =
    'v' + (typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.0.0');
  // 语言可能刚变过：先把 DOM 上的 data-i18n 文案全部刷新一遍
  setLang(settings.language);
  applyI18n();
  // ⚠️ applyI18n 会无脑覆盖 [data-i18n] 元素的 textContent，
  //    而 #setting-tsip-value 同时带 data-i18n="value.notSet" ——
  //    有地址时会被打回 "not set"。所以必须在 applyI18n **之后**重写动态值。
  document.getElementById('setting-tsip-value').textContent =
    settings.address || settings.tsIp || i18n('value.notSet');
  // 隧道码 / 密码：密码不明文显示，只显示「已设置」，避免旁人一眼看到
  const codeEl = document.getElementById('setting-tunnel-code-value');
  if (codeEl) codeEl.textContent = settings.tunnelCode || i18n('value.notSet');
  const passEl = document.getElementById('setting-tunnel-pass-value');
  if (passEl) {
    passEl.textContent = settings.tunnelPassword
      ? '••••••••'
      : i18n('value.notSet');
  }
  // 顶栏标题不是 data-i18n（终端页要显示会话名），单独刷
  if (currentPage !== 'terminal') {
    const title = titleEl();
    if (title) title.textContent = i18n(PAGES[currentPage]?.titleKey || 'page.sessions');
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
// 官方 Material Symbols 图标（viewBox 0 -960 960 960 的原始 path）。
// ⚠️ 一律用谷歌官方数据，不要手绘 —— 手绘的风格与 Scholarius/Livolog 不一致，
//    而且以后要换图标时找不到来源。
const ICONS = {
  copy: 'M360-240q-33 0-56.5-23.5T280-320v-480q0-33 23.5-56.5T360-880h360q33 0 56.5 23.5T800-800v480q0 33-23.5 56.5T720-240H360Zm0-80h360v-480H360v480ZM200-80q-33 0-56.5-23.5T120-160v-560h80v560h440v80H200Zm160-240v-480 480Z',
  edit: 'M200-200h57l391-391-57-57-391 391v57Zm-80 80v-170l528-527q12-11 26.5-17t30.5-6q16 0 31 6t26 18l55 56q12 11 17.5 26t5.5 30q0 16-5.5 30.5T817-647L290-120H120Zm640-584-56-56 56 56Zm-141 85-28-29 57 57-29-28Z',
  delete: 'M280-120q-33 0-56.5-23.5T200-200v-520h-40v-80h200v-40h240v40h200v80h-40v520q0 33-23.5 56.5T680-120H280Zm400-600H280v520h400v-520ZM360-280h80v-360h-80v360Zm160 0h80v-360h-80v360ZM280-720v520-520Z',
  info: 'M480-280q17 0 28.5-11.5T520-320q0-17-11.5-28.5T480-360q-17 0-28.5 11.5T440-320q0 17 11.5 28.5T480-280Zm-40-160h80v-240h-80v240ZM200-120q-33 0-56.5-23.5T120-200v-560q0-33 23.5-56.5T200-840h560q33 0 56.5 23.5T840-760v560q0 33-23.5 56.5T760-120H200Zm0-80h560v-560H200v560Zm0 0v-560 560Z',
  remove: 'M240-440v-80h480v80H240Z',
  add: 'M440-440H200v-80h240v-240h80v240h240v80H520v240h-80v-240Z',
};

/** 生成一个官方图标的 <svg>。 */
function iconSvg(name, size = 20) {
  const d = ICONS[name];
  if (!d) return '';
  return `<svg viewBox="0 -960 960 960" width="${size}" height="${size}" aria-hidden="true" focusable="false"><path d="${d}"/></svg>`;
}

function pickOption(title, options, currentId, anchorEl) {
  return new Promise((resolve) => {
    ensureModalCss();

    const menu = document.createElement('div');
    menu.className = 'tmt-menu';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', title || '');
    // 选项可带 icon（ICONS 里的键名）。有图标的项会渲染成「图标 + 文字」，
    // 没有图标时保持纯文字（Language / Theme 这类不需要图标）。
    menu.innerHTML = options
      .map((o, i) => {
        const cls = `tmt-menu-item${o.id === currentId ? ' is-selected' : ''}`;
        const ic = o.icon ? `<span class="tmt-menu-icon">${iconSvg(o.icon)}</span>` : '';
        return `<button type="button" role="menuitem" class="${cls}" data-i="${i}">${ic}<span class="tmt-menu-text">${o.name}</span></button>`;
      })
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

// ── 连接身份：隧道码 / 密码 ────────────────────────────────────────────
// 这两项是**身份**，填一次长期不变。地址会变，但不在这里手改
// （刷新按钮会自动接管新地址）。
document.getElementById('setting-tunnel-code')?.addEventListener('click', async () => {
  const v = await tmtPrompt({
    title: i18n('conn.code'),
    message: i18n('conn.codeHint'),
    input: {
      placeholder: 'ABC123',
      value: settings.tunnelCode || '',
    },
    okText: i18n('action.save'),
    cancelText: i18n('action.cancel'),
  });
  if (v === null) return;
  settings.tunnelCode = String(v).trim().toUpperCase();
  saveSettings();
  renderSettings();
});

document.getElementById('setting-tunnel-pass')?.addEventListener('click', async () => {
  const v = await tmtPrompt({
    title: i18n('conn.password'),
    message: i18n('conn.passwordHint'),
    input: {
      placeholder: '••••••••',
      value: settings.tunnelPassword || '',
    },
    okText: i18n('action.save'),
    cancelText: i18n('action.cancel'),
  });
  if (v === null) return;
  settings.tunnelPassword = String(v);
  saveSettings();
  renderSettings();
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
    `<button class="size-btn" type="button" data-step="-1" aria-label="${i18n('aria.decreaseFont')}">${iconSvg('remove', 22)}</button>` +
    `<input class="size-input" type="number" inputmode="numeric" min="${FONT_MIN}" max="${FONT_MAX}" step="1" aria-label="${i18n('font.aria')}" value="${original}">` +
    `<button class="size-btn" type="button" data-step="1" aria-label="${i18n('aria.increaseFont')}">${iconSvg('add', 22)}</button>` +
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

// ⚠️ 地址不再提供编辑入口（设置页那一行只是只读显示）。
//    原因是模型上「隧道码 = 根，地址 = 可丢弃的传输」：
//       · 隧道重建后 URL 会变，由 refreshConnection() 按码自动发现并改写；
//       · 允许手填就等于鼓励用户去见一个固定域名，那就不是用 Cloudflare 的
//         隧道了，而是套壳——正是当初否掉的方案。
//    真要抢修（自动发现全部失败）时，走连接表单里的「暴露高级」入口，
//    而不是把它放在设置首页当常规配置。

// ⚠️ 端口项已移除：隧道地址是完整 URL（自带域名，无端口），
//    单独的端口输入只会让用户困惑。settings.port 仅保留给
//    「裸 IP / 主机名」这种旧式输入时的兜底拼接。

// ── 连接设置：顶栏左侧的链条图标 → 表单（IP + 端口），可当场测试 ────────
// 设置页里的 Tailscale IP / Port 两项与这里读写同一份 settings，
// 任何一处改动都会同步（saveSettings → renderSettings 刷新设置页显示值）。
// ⚠️ 原来是设置页里一个「Test Connection」行；现在改成顶栏入口，
//    因为连接是高频动作，不该埋在设置里翻两层。
// ⚠️ 三栏的含义，别混：
//   · Tunnel Code   —— 身份，长期不变。换 URL 也不用重填。
//   · Password      —— 身份凭据，长期不变。
//   · Address (URL) —— 传输地址，会随隧道重建而变；平时由「刷新」自动更新。
const CONN_FORM_BODY = `
  <!-- 扫码入口：放在最上面。
       电脑端 termetron remote 会打出二维码，里面带「隧道码 + 当前地址」，
       扫一下就填好这两项 —— 用户完全不用碰网址。
       密码不在二维码里（它在屏幕上，可能被人看到），扫码后仍需手输密码。 -->
  <button class="tmt-scan-btn" type="button">
    <svg viewBox="0 -960 960 960" aria-hidden="true" focusable="false"><path d="M80-680v-200h200v80H160v120H80Zm680 0v-120H640v-80h200v200h-80ZM80-80v-200h80v120h120v80H80Zm760 0H640v-80h120v-120h80v200ZM160-280v-400h80v400h-80Zm120 0v-400h80v400h-80Zm120 0v-400h120v400H400Zm160 0v-400h80v400h-80Zm120 0v-400h120v400H680Z"/></svg>
    <span class="tmt-scan-text"></span>
  </button>
  <p class="tmt-scan-status" hidden></p>

  <div class="tmt-or"><span></span></div>

  <label class="tmt-field">
    <span class="tmt-field-label" data-i18n="conn.code">Tunnel Code</span>
    <input class="tmt-input tmt-conn-code" type="text"
           autocomplete="off" autocapitalize="characters" spellcheck="false"
           placeholder="ABC123">
  </label>
  <label class="tmt-field">
    <span class="tmt-field-label" data-i18n="conn.password">Password</span>
    <input class="tmt-input tmt-conn-pass" type="password"
           autocomplete="off" spellcheck="false" placeholder="••••••••">
  </label>
  <!-- 地址：**可选**，但它必须看得见。
       自动接管只在「手里还有一个能连的地址」时才生效；地址彻底失效
       （隧道换过好几次、或换了一台机器）时，这里是唯一的救场入口。 -->
  <label class="tmt-field">
    <span class="tmt-field-label" data-i18n="conn.address">Address</span>
    <input class="tmt-input tmt-conn-addr" type="url" inputmode="url"
           autocomplete="off" autocapitalize="off" spellcheck="false"
           placeholder="https://xxxx.trycloudflare.com">
    <span class="tmt-field-hint" data-i18n="conn.addressHint">Filled in automatically when you reconnect.</span>
  </label>
  <p class="tmt-conn-status" hidden></p>
  <button class="tmt-conn-test" type="button" data-i18n="conn.test">TEST CONNECTION</button>
`;

/**
 * 扫码，返回 {code, url} 或 null。
 *
 * @capacitor-mlkit/barcode-scanning 只在原生环境可用；浏览器里直接
 * 返回 null，由调用方提示"扫码只在 App 内可用"。这样 web 预览不会崩。
 */
async function scanPairQr() {
  const BC = globalThis.Capacitor?.Plugins?.BarcodeScanner;
  if (!BC) return { error: 'unavailable' };
  try {
    const perm = await BC.checkPermissions();
    if (perm.camera !== 'granted') {
      const req = await BC.requestPermissions({ permissions: ['camera'] });
      if (req.camera !== 'granted') return { error: 'denied' };
    }
    const { barcodes } = await BC.scan({ formats: ['QR_CODE'] });
    const raw = barcodes?.[0]?.rawValue || '';
    if (!raw) return { error: 'empty' };
    // 协议与 termqr.py 的 payload() 保持一致：
    //   termetron://pair?code=XXX&url=https%3A%2F%2F...
    if (!raw.startsWith('termetron://pair')) return { error: 'foreign', raw };
    const q = raw.split('?')[1] || '';
    const p = new URLSearchParams(q);
    const code = (p.get('code') || '').trim().toUpperCase();
    const url = (p.get('url') || '').trim();
    if (!code || !/^https?:\/\//i.test(url)) return { error: 'foreign', raw };
    return { code, url };
  } catch (e) {
    return { error: 'failed', detail: String(e && e.message || e) };
  }
}

function openConnectionForm() {
  return tmtPrompt({
    title: i18n('conn.title'),
    body: CONN_FORM_BODY,
    okText: i18n('action.save'),
    cancelText: i18n('action.cancel'),
    setup(bodyEl) {
      const codeEl = bodyEl.querySelector('.tmt-conn-code');
      const passEl = bodyEl.querySelector('.tmt-conn-pass');
      const addrEl = bodyEl.querySelector('.tmt-conn-addr');
      const stat = bodyEl.querySelector('.tmt-conn-status');
      const testBtn = bodyEl.querySelector('.tmt-conn-test');
      codeEl.value = settings.tunnelCode || '';
      passEl.value = settings.tunnelPassword || '';
      addrEl.value = baseUrl() || '';

      const say = (msg, kind) => {
        stat.textContent = msg;
        stat.hidden = !msg;
        stat.dataset.kind = kind || '';
      };
      // 表单内文案也要跟随语言（body 是模板字符串，写死的是英文）
      bodyEl.querySelectorAll('[data-i18n]').forEach((el) => {
        el.textContent = i18n(el.dataset.i18n);
      });

      // ── 扫码 ──────────────────────────────────────────────────────
      const scanBtn = bodyEl.querySelector('.tmt-scan-btn');
      const scanStat = bodyEl.querySelector('.tmt-scan-status');
      bodyEl.querySelector('.tmt-scan-text').textContent = i18n('conn.scan');
      bodyEl.querySelector('.tmt-or span').textContent = i18n('conn.or');
      const sayScan = (msg, kind) => {
        scanStat.textContent = msg;
        scanStat.hidden = !msg;
        scanStat.dataset.kind = kind || '';
      };
      scanBtn.addEventListener('click', async () => {
        sayScan(i18n('conn.scanning'), 'idle');
        scanBtn.disabled = true;
        const r = await scanPairQr();
        scanBtn.disabled = false;
        if (r.error === 'unavailable') { sayScan(i18n('conn.scanNativeOnly'), 'bad'); return; }
        if (r.error === 'denied') { sayScan(i18n('conn.scanDenied'), 'bad'); return; }
        if (r.error === 'foreign') { sayScan(i18n('conn.scanForeign'), 'bad'); return; }
        if (r.error) { sayScan(i18n('conn.scanFailed', { err: r.detail || r.error }), 'bad'); return; }
        // 扫码成功：填好隧道码 + 地址，把光标放到密码框
        // （密码不经过二维码，是手机上唯一要手输的东西）
        codeEl.value = r.code;
        addrEl.value = r.url;
        sayScan(i18n('conn.scanOk'), 'good');
        passEl.focus();
      });
      // 地址归一化：允许 https://域名、http://ip:port；裸地址自动补 http。
      const normalize = (raw) => {
        if (!raw) return '';
        let s = raw.trim();
        if (!/^https?:\/\//i.test(s)) s = `http://${s}`;
        return s.replace(/\/+$/, '');
      };
      const isValid = (raw) => {
        const s = normalize(raw);
        if (!s) return false;
        try {
          return !!new URL(s).hostname;
        } catch {
          return false;
        }
      };

      testBtn.addEventListener('click', async () => {
        const raw = addrEl.value;
        if (!isValid(raw)) { say(i18n('conn.invalid'), 'bad'); return; }
        say(i18n('conn.testing'), 'idle');
        testBtn.disabled = true;
        // 用**表单里的值**去测，不是 settings —— 用户此刻还没点保存
        const url = normalize(raw);
        // ⚠️ fetch 必须带超时。Android WebView 里连一个**不可达**的 IP 时，
        //    TCP 握手会一直挂到系统默认超时（实测 75~130 秒，视内核参数而定），
        //    期间界面就停在 "Testing…" 一动不动，用户以为卡死。
        //    用 AbortController 主动掐断，8 秒足够局域网 / Tailscale 往返；
        //    超时后给出明确原因（超时），而不是让用户等两分钟再看一句笼统的失败。
        const ac = new AbortController();
        const timer = setTimeout(() => ac.abort(), 8000);
        try {
          // ⚠️ 有隧道码 + 密码时必须走 /api/tunnel/verify：
          //    只确认「对端是 termetron」是不够的 —— 换 URL 后可能连到
          //    别人的实例上。隧道码才是判断「是不是我这条」的根。
          const code = (codeEl.value || '').trim().toUpperCase();
          const pass = passEl.value || '';
          if (code && pass) {
            const q = `code=${encodeURIComponent(code)}&password=${encodeURIComponent(pass)}`;
            const res = await fetch(`${url}/api/tunnel/verify?${q}`, {
              credentials: 'include',   // 顺带把认证 cookie 拿到手
              headers: { 'Content-Type': 'application/json' },
              signal: ac.signal,
            });
            if (res.status === 401) throw new Error(i18n('conn.badCode'));
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const d = await res.json();
            if (!d?.ok) throw new Error(i18n('conn.badCode'));
            say(i18n('conn.ok').replace('{v}', code), 'good');
          } else {
            const res = await fetch(`${url}/api/discover`, {
              headers: { 'Content-Type': 'application/json' },
              signal: ac.signal,
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const d = await res.json();
            if (d?.app !== 'termetron') throw new Error(i18n('conn.notTermetron'));
            say(i18n('conn.ok').replace('{v}', d.version || '?'), 'good');
          }
        } catch (e2) {
          // AbortError 的 message 在部分 WebView 上是空的，单独判一下，
          // 统一换成可读的「超时」提示。
          const msg = e2.name === 'AbortError'
            ? i18n('conn.timeout')
            : (e2.message || String(e2));
          say(i18n('conn.fail').replace('{err}', msg), 'bad');
        } finally {
          clearTimeout(timer);
          testBtn.disabled = false;
        }
      });
    },
    onAccept(bodyEl) {
      const codeEl = bodyEl?.querySelector('.tmt-conn-code');
      const passEl = bodyEl?.querySelector('.tmt-conn-pass');
      const addrEl = bodyEl?.querySelector('.tmt-conn-addr');
      if (!addrEl) return true;
      const bad = (msgKey) => {
        const stat = bodyEl.querySelector('.tmt-conn-status');
        if (stat) {
          stat.textContent = i18n(msgKey);
          stat.hidden = false;
          stat.dataset.kind = 'bad';
        }
        return false;
      };
      const raw = addrEl.value.trim();
      const code = (codeEl?.value || '').trim().toUpperCase();
      const pass = passEl?.value || '';
      // 有隧道码就不用逼用户填地址：地址由刷新自动发现。
      // 既没码又没地址才是真的没法连。
      if (!raw) {
        if (!code) return bad('conn.invalid');
        settings.tunnelCode = code;
        settings.tunnelPassword = pass;
        saveSettings();
        renderSettings();
        // 立刻试一次自动发现，省得用户还要再点一次刷新
        setTimeout(() => refreshConnection(), 0);
        return true;
      }
      let s = raw;
      if (!/^https?:\/\//i.test(s)) s = `http://${s}`;
      s = s.replace(/\/+$/, '');
      try {
        if (!new URL(s).hostname) return bad('conn.invalid');
      } catch {
        return bad('conn.invalid');
      }
      // 存三个字段：
      //   tunnelCode / tunnelPassword —— 身份，长期不变（接力时的校验根）
      //   address                     —— 传输地址，可随隧道重建而变
      settings.tunnelCode = code;
      settings.tunnelPassword = pass;
      settings.address = s;
      settings.tsIp = s;   // 兼容旧路径
      rememberEndpoint(s);
      saveSettings();
      renderSettings();
      return true;
    },
  });
}

// ── 刷新连接 ────────────────────────────────────────────────────────
// 「刷新」= 重新解析当前可用地址并重连。它做三件事：
//   1. 按隧道码 + 密码验证候选地址，挑出确实属于我的那条隧道；
//   2. 若地址变了，自动更新并记住（用户不需要手动改 URL）；
//   3. 刷新会话列表。
// 连接按钮 = 打开连接表单（扫码 / 手动输入二选一）。
//
// ⚠️ 去掉了原来的「单击刷新 + 长按配置」：那个交互用户根本发现不了 ——
//    长按没有任何视觉提示，实测用户只会单击，然后永远进不到配置页。
//    现在单击就是打开表单，语义直白。
//    重连本身（握手、接力新 URL）仍然全自动，不需要按钮。
document.getElementById('btn-conn')?.addEventListener('click', () => {
  openConnectionForm();
});

/**
 * 刷新连接：用隧道码校验并接管新地址，然后重新拉会话。
 * 出错时给出**可操作**的说明，而不是一句"连接失败"。
 *
 * `silent`：后台自动重试用，失败不弹窗（否则每次重试都弹一个框）。
 */
async function refreshConnection(silent = false) {
  if (!hasConnection() && !settings.tunnelCode) {
    openConnectionForm();
    return false;
  }
  // 进度弹窗：远程连接可能耗时数秒（要逐个试候选地址），
  // 不给反馈的话用户会以为按钮没反应，反复去点。
  const prog = silent ? null : showConnProgress(i18n('conn.refreshing'));
  let base = '';
  try {
    base = await resolveBase();
  } finally {
    prog?.close();
  }
  if (!base) {
    // 所有候选都不通：可能隧道重建到全新地址了
    if (silent) return false;
    const ok = await tmtConfirm(i18n('conn.allLost'), {
      title: i18n('conn.title'),
      okText: i18n('action.editAddress'),
      cancelText: i18n('action.cancel'),
    });
    if (ok) {
      // 直接打开表单：地址现在就在主表单上，一眼可见
      openConnectionForm();
    }
    return false;
  }
  await refreshSessions();
  if (!silent) tmtToast(i18n('conn.refreshed'));
  return true;
}

// ── 断线自动重试（有上限，绝不死循环）─────────────────────────────────
// 用户明确要求：自动重试，但必须限次数 —— 万一服务端真的没了，
// 无上限重试只会让 App 一直空转、耗电、发热，还掩盖了"确实连不上"的事实。
//
// 策略：指数退避，总次数封顶。用完之后停在"未连接"，等用户手动点刷新
// 或重新进入 App 才会再来一轮（见 resetAutoRetry 的调用点）。
const AUTO_RETRY_MAX = 5;
const AUTO_RETRY_BASE_MS = 2000;   // 2s, 4s, 8s, 16s, 32s
let autoRetryCount = 0;
let autoRetryTimer = null;

function stopAutoRetry() {
  if (autoRetryTimer) {
    clearTimeout(autoRetryTimer);
    autoRetryTimer = null;
  }
}

/** 每轮重试的起点：进入 App、切回前台、用户手动刷新时调用。 */
function resetAutoRetry() {
  stopAutoRetry();
  autoRetryCount = 0;
}

function scheduleAutoRetry() {
  // 已经连上 / 还在重试队列里 / 用完次数 → 不再排
  if (autoRetryTimer || autoRetryCount >= AUTO_RETRY_MAX) return;
  const delay = AUTO_RETRY_BASE_MS * Math.pow(2, autoRetryCount);
  autoRetryCount += 1;
  autoRetryTimer = setTimeout(async () => {
    autoRetryTimer = null;
    const ok = await refreshConnection(true);
    if (!ok) scheduleAutoRetry();
  }, delay);
}

/** App 内任意请求失败时调用：安排一次（有上限的）自动重连。 */
function noteConnectionLost() {
  if (autoRetryCount >= AUTO_RETRY_MAX) return;
  scheduleAutoRetry();
}

// 连接进度弹窗：与更新弹窗（updater.js 的 .ut-*）保持同一套视觉语言。
// 因为连接没有可量的百分比，用**不确定进度条**（来回滚动），
// 这比假装显示一个假百分比诚实，也符合"正在重试多个地址"的实际语义。
function showConnProgress(title) {
  ensureModalCss();
  const overlay = document.createElement('div');
  overlay.className = 'ut-overlay';
  overlay.innerHTML =
    '<div class="ut-modal"><h3></h3>' +
    '<div class="ut-pbar indet"><div class="ut-pfill"></div></div>' +
    '<p class="ut-ppct"></p></div>';
  overlay.querySelector('h3').textContent = title;
  overlay.querySelector('.ut-ppct').textContent = i18n('conn.connecting');
  document.body.appendChild(overlay);
  notifyOverlayChange();
  return {
    set(text) {
      const p = overlay.querySelector('.ut-ppct');
      if (p && text) p.textContent = text;
    },
    close() {
      overlay.remove();
      notifyOverlayChange();
    },
  };
}

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
// ⚠️ 并且必须等 DOM 就绪。`type="module"` 虽然默认 defer，但在部分
//    Android WebView / file:// 加载路径下，模块执行时 DOM 仍可能没建完，
//    此时任何 document.getElementById 都返回 null —— 后续第一次
//    showPage 就会因为 null.textContent 抛错中断，导致整个启动序列
//    （包括会话列表刷新和空状态）全部静默失效。
function bootstrap() {
  resetAutoRetry();   // 每次启动都是一轮新的重试配额
  setLang(settings.language);
  applyI18n();
  applyTheme();    // 内部会调 syncNativeBars；此时 splashActive=true → 状态栏=启动页色
  applyFontSize();
  showPage('sessions');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootstrap, { once: true });
} else {
  bootstrap();
}

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
