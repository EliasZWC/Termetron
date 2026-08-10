// Termetron in-app auto-update: check GitHub Release -> Termetron-styled prompt ->
// download APK (timeout + retry) -> system installer.
// Triggers on every app entry (cold start / return to foreground via
// appStateChange isActive); 30s in-memory debounce.
// Prerequisite: public repo EliasZWC/Termetron; CI publishes each build as a
// GitHub Release (tag=v<version>, asset termetron-v<version>.apk).
import { Capacitor } from '@capacitor/core';
import { Directory, Filesystem } from '@capacitor/filesystem';

const REPO = 'EliasZWC/Termetron';
const DEBOUNCE_MS = 30000;   // 30s in-memory debounce
const CHECK_TIMEOUT = 15000; // release check timeout (ms)

let _lastCheck = 0;

function semverLte(a, b) {
  const pa = String(a).split('.').map(Number);
  const pb = String(b).split('.').map(Number);
  for (let i = 0; i < 3; i++) {
    const x = pa[i] || 0;
    const y = pb[i] || 0;
    if (x !== y) return x < y;
  }
  return true; // 相等也算“无需更新”
}

// ---- Termetron-styled modal (replaces browser-default confirm/alert) ----
const MODAL_CSS = `
.ut-overlay{position:fixed;inset:0;background:rgba(0,0,0,.62);display:flex;align-items:center;justify-content:center;z-index:9999;font-family:system-ui,sans-serif}
.ut-modal{background:#0d131b;border:1px solid #1b2733;border-radius:12px;padding:22px 20px;width:min(320px,88vw);color:#c9d1d9;box-shadow:0 10px 32px rgba(0,0,0,.55);text-align:left}
.ut-modal h3{color:#a78bfa;margin:0 0 10px;font-size:15px;font-weight:700}
.ut-modal p{margin:0 0 18px;font-size:13px;line-height:1.6;white-space:pre-line;word-break:break-word}
.ut-actions{display:flex;gap:10px;justify-content:flex-end}
.ut-btn{padding:9px 16px;border-radius:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;-webkit-tap-highlight-color:transparent}
.ut-btn.cancel{background:#171e2b;color:#c9d1d9;border:1px solid #1b2733}
.ut-btn.ok{background:#a78bfa;color:#0a0e14}
.ut-btn:focus{outline:none;box-shadow:0 0 0 2px rgba(167,139,250,.3)}
.ut-pbar{height:6px;background:#171e2b;border-radius:3px;overflow:hidden;margin:2px 0 10px}
.ut-pfill{height:100%;width:0;background:#a78bfa;transition:width .2s ease}
.ut-ppct{color:#5a6a7a;font-size:12px;text-align:center;margin:0}
`;

function ensureModalCss() {
  if (document.getElementById('ut-modal-css')) return;
  const s = document.createElement('style');
  s.id = 'ut-modal-css';
  s.textContent = MODAL_CSS;
  document.head.appendChild(s);
}

function showModal({ title, message, okText = 'OK', cancelText = null }) {
  ensureModalCss();
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'ut-overlay';
    overlay.innerHTML =
      '<div class="ut-modal"><h3></h3><p></p><div class="ut-actions">' +
      (cancelText ? '<button class="ut-btn cancel"></button>' : '') +
      '<button class="ut-btn ok"></button></div></div>';
    const h = overlay.querySelector('h3');
    const p = overlay.querySelector('p');
    const ok = overlay.querySelector('.ut-btn.ok');
    const cancel = overlay.querySelector('.ut-btn.cancel');
    h.textContent = title;
    p.textContent = message;
    ok.textContent = okText;
    if (cancel) cancel.textContent = cancelText;
    document.body.appendChild(overlay);
    const done = (val) => { overlay.remove(); document.removeEventListener('keydown', kd); resolve(val); };
    const kd = (e) => {
      if (e.key === 'Escape' && cancel) { e.preventDefault(); done(false); }
      else if (e.key === 'Enter') { e.preventDefault(); done(true); }
    };
    document.addEventListener('keydown', kd);
    ok.onclick = () => done(true);
    if (cancel) cancel.onclick = () => done(false);
    ok.focus();
  });
}

function showProgressModal(title) {
  ensureModalCss();
  const overlay = document.createElement('div');
  overlay.className = 'ut-overlay';
  overlay.innerHTML =
    '<div class="ut-modal"><h3></h3><div class="ut-pbar"><div class="ut-pfill"></div></div>' +
    '<p class="ut-ppct"></p></div>';
  overlay.querySelector('h3').textContent = title;
  document.body.appendChild(overlay);
  const pfill = overlay.querySelector('.ut-pfill');
  const ppct = overlay.querySelector('.ut-ppct');
  return {
    set(percent) {
      const p = Math.max(0, Math.min(100, Math.round(percent)));
      pfill.style.width = p + '%';
      ppct.textContent = p + '%';
    },
    close() { overlay.remove(); },
  };
}

function fetchWithTimeout(url, timeout) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeout);
  return fetch(url, { cache: 'no-store', signal: ctl.signal }).finally(() => clearTimeout(t));
}

export async function checkForUpdate() {
  if (Capacitor.getPlatform() !== 'android') return; // App only (skip on web)
  const now = Date.now();
  if (now - _lastCheck < DEBOUNCE_MS) return; // debounce: once per entry
  _lastCheck = now;

  let rel;
  try {
    const res = await fetchWithTimeout(`https://api.github.com/repos/${REPO}/releases/latest`, CHECK_TIMEOUT);
    if (!res.ok) return; // repo not public / no release / network issue: silent
    rel = await res.json();
  } catch (e) {
    return;
  }

  const tag = String(rel.tag_name || '').replace(/^v/, '');
  // eslint-disable-next-line no-undef
  const cur = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.0.1';
  if (!tag || semverLte(tag, cur)) return; // already up to date

  const apk = (rel.assets || []).find((a) => /\.apk$/i.test(a.name));
  if (!apk) return;

  const go = await showModal({
    title: 'Update available',
    message: `Termetron v${tag} is available.\nCurrent version: v${cur}.\n\nDownload and install now?`,
    okText: 'Update',
    cancelText: 'Later',
  });
  if (!go) return;
  await downloadAndInstall(apk.browser_download_url, `termetron-v${tag}.apk`);
}

async function downloadAndInstall(url, name) {
  // 循环：失败弹 Try again / Cancel，直到成功或用户取消
  for (;;) {
    const outcome = await tryDownload(url, name);
    if (outcome === 'installed' || outcome === 'no-installer') return;
    const retry = await showModal({
      title: 'Update failed',
      message: `Could not download the update (${outcome && outcome.error ? outcome.error.message : 'network error'}).\nCheck your connection and try again.`,
      okText: 'Try again',
      cancelText: 'Cancel',
    });
    if (!retry) return;
  }
}

async function tryDownload(url, name) {
  // 原生下载（@capacitor/filesystem downloadFile）——绕过 WebView fetch 的 CORS 限制：
  // GitHub release 资产（release-assets.githubusercontent.com）不带 Access-Control-Allow-Origin，
  // WebView fetch 跨域会被拦（TypeError: Failed to fetch），原生 HTTP 不受 CORS 约束。
  const prog = showProgressModal('Downloading update...');
  let listener = null;
  try {
    listener = await Filesystem.addListener('progress', (info) => {
      if (info && typeof info.percent === 'number') prog.set(info.percent);
    });
    const saved = await Filesystem.downloadFile({
      url,
      path: name,
      directory: Directory.Cache,
    });
    if (listener) listener.remove();
    prog.close();
    const plugin = Capacitor.Plugins.ApkInstaller;
    if (!plugin) {
      await showModal({
        title: 'Downloaded',
        message: 'The APK was downloaded, but the installer is unavailable.\nPlease install it manually from the GitHub release.',
        okText: 'OK',
      });
      return 'no-installer';
    }
    // DownloadFileResult 字段是 path（不是 uri）
    const filePath = String(saved && saved.path ? saved.path : '').replace(/^file:\/\//, '');
    await plugin.install({ path: filePath });
    return 'installed';
  } catch (e) {
    if (listener) listener.remove();
    prog.close();
    return { error: e };
  }
}
