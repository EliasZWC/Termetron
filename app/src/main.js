import {
  CapacitorBarcodeScanner,
  CapacitorBarcodeScannerCameraDirection,
  CapacitorBarcodeScannerScanOrientation,
  CapacitorBarcodeScannerTypeHint,
} from '@capacitor/barcode-scanner';
import { App } from '@capacitor/app';
import { checkForUpdate } from './updater';

// 轻量提示（底部 toast，短暂显示，不阻塞）
const TMT_TOAST_CSS = '.tmt-toast{position:fixed;left:50%;bottom:48px;transform:translateX(-50%);background:rgba(13,19,27,.95);border:1px solid #1b2733;color:#c9d1d9;padding:10px 18px;border-radius:8px;font-size:13px;z-index:10000;pointer-events:none;font-family:system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.5)}';
function tmtToast(msg) {
  if (!document.getElementById('tmt-toast-css')) {
    const s = document.createElement('style'); s.id = 'tmt-toast-css'; s.textContent = TMT_TOAST_CSS; document.head.appendChild(s);
  }
  const el = document.createElement('div'); el.className = 'tmt-toast'; el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 1800);
}

const urlInput = document.getElementById('url');

// Termetron 风格提示框（替代系统默认 alert）：深色 #0d131b + 紫色 #a78bfa，与 updater 弹窗一致
const TMT_CSS = `
.tmt-overlay{position:fixed;inset:0;background:rgba(0,0,0,.62);display:flex;align-items:center;justify-content:center;z-index:9999;font-family:system-ui,sans-serif}
.tmt-modal{background:#0d131b;border:1px solid #1b2733;border-radius:12px;padding:22px 20px;width:min(320px,88vw);color:#c9d1d9;box-shadow:0 10px 32px rgba(0,0,0,.55);text-align:left}
.tmt-modal p{margin:0 0 18px;font-size:13px;line-height:1.6;white-space:pre-line;word-break:break-word}
.tmt-actions{display:flex;gap:10px;justify-content:flex-end}
.tmt-btn{padding:9px 16px;border-radius:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;background:#a78bfa;color:#0a0e14;-webkit-tap-highlight-color:transparent}
.tmt-btn:focus{outline:none;box-shadow:0 0 0 2px rgba(167,139,250,.3)}
`;
function tmtAlert(message) {
  if (!document.getElementById('tmt-css')) {
    const s = document.createElement('style');
    s.id = 'tmt-css';
    s.textContent = TMT_CSS;
    document.head.appendChild(s);
  }
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'tmt-overlay';
    overlay.innerHTML =
      '<div class="tmt-modal"><p></p><div class="tmt-actions"><button class="tmt-btn"></button></div></div>';
    overlay.querySelector('p').textContent = message;
    const ok = overlay.querySelector('button');
    ok.textContent = 'OK';
    document.body.appendChild(overlay);
    const done = () => { overlay.remove(); resolve(); };
    ok.onclick = done;
    ok.focus();
  });
}

// 连接页（App 首页）返回键：连按两次退出（与隧道页返回层级一致，防误退）
let lastBack = 0;
App.addListener('backButton', () => {
  const now = Date.now();
  if (now - lastBack < 2000) { App.exitApp(); }
  else { lastBack = now; tmtToast('Press back again to exit'); }
});
const connBtn = document.getElementById('connect');

// 版本号由 vite define 注入（__APP_VERSION__ 来自 package.json，与 README **Version:** 同步）
// eslint-disable-next-line no-undef
document.getElementById('ver').textContent =
  'Termetron v' + (typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.0.1');

// Web 层品牌启动页（100% 生效，不依赖原生 splash）：由 CSS animation（ut-splash 2.6s）驱动显示+淡出
// —— animation 从元素首次渲染时刻开始计时（不受 JS/module 执行延迟影响），比 JS 定时器更可靠；
// animationend 后移除 DOM，setTimeout 兜底（极端情况下 animation 未触发）。
// 不再调用原生 SplashScreen.show()：原生 splash 与 Web splash 时序竞争会让 Web splash 看起来“没生效”。
const splashEl = document.getElementById('splash');
if (splashEl) {
  // 从隧道返回连接页（?t=back）时跳过 splash，避免“重启应用”的观感（仅冷启动才显示品牌 splash）
  const isBack = new URLSearchParams(location.search).get('t') === 'back';
  if (isBack) {
    splashEl.remove();
  } else {
    splashEl.addEventListener('animationend', () => splashEl.remove(), { once: true });
    setTimeout(() => splashEl.remove(), 3500);
  }
}

// 记住上次连接地址
urlInput.value = localStorage.getItem('qt_url') || '';

// 隧道 URL 可达性探测：termetron remote off 后 trycloudflare URL 失效（Cloudflare 返回 530），
// 直接导航会进入错误页且无法返回。探测 Termetron 的 /api/remote/status：
//  - 隧道开着：Termetron 返回 200 且带 CORS 头（Access-Control-Allow-Origin:*）-> fetch 成功
//  - 隧道关闭：Cloudflare 530 页无 CORS 头 -> 跨域被拦 -> fetch reject -> 判定不可达
function probe(url, tries = 3) {
  // 不要用 cache:'no-store'：它会被 WebView 变成 Cache-Control 请求头（非 CORS
  // safelisted），触发 OPTIONS 预检——服务器现已支持预检，但简化请求更稳。
  // 超时 15s + 失败重试 3 次，容忍手机网络/CF 链路的慢与抖动。
  return new Promise((resolve) => {
    const attempt = (n) => {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 15000);
      fetch(url.replace(/\/$/, '') + '/api/remote/status', { signal: ctl.signal })
        .then((r) => { clearTimeout(t); resolve(r.status === 200); })
        .catch(() => {
          clearTimeout(t);
          if (n < tries) setTimeout(() => attempt(n + 1), 1200);
          else resolve(false);
        });
    };
    attempt(1);
  });
}
function setBusy(b) {
  connBtn.disabled = b;
  connBtn.textContent = b ? 'Connecting ...' : 'Connect';
}

async function connect(raw) {
  let url = (raw || '').trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
  // 从二维码来的可能是 termetron://<url>?t=<token>（或旧版 qt://），转为 https
  url = url.replace(/^termetron:\/\//i, 'https://').replace(/^qt:\/\//i, 'https://');
  localStorage.setItem('qt_url', url);
  setBusy(true);
  const ok = await probe(url);
  if (!ok) {
    setBusy(false);
    await tmtAlert('Tunnel is closed or unreachable.\nRun "termetron remote on" on the computer again, then re-scan or paste the new URL.');
    return;
  }
  window.location.href = url;   // WebView 导航到隧道 URL，Termetron 前端接管密码/配对认证
}

document.getElementById('connect').addEventListener('click', () => connect(urlInput.value));
urlInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') connect(urlInput.value); });

document.getElementById('scan').addEventListener('click', async () => {
  try {
    const result = await CapacitorBarcodeScanner.scanBarcode({
      hint: CapacitorBarcodeScannerTypeHint.QR_CODE,
      scanInstructions: '把二维码对准取景框',
      scanButton: true,
      scanText: '完成',
      cameraDirection: CapacitorBarcodeScannerCameraDirection.BACK,
      scanOrientation: CapacitorBarcodeScannerScanOrientation.ADAPTIVE,
    });
    if (result && result.ScanResult) connect(result.ScanResult);
  } catch (e) {
    await tmtAlert('Scan failed: ' + (e.message || e));
  }
});

// 自动更新：每次“进入 App”检查一次——冷启动 + 后台切回前台（appStateChange isActive）
setTimeout(() => { checkForUpdate().catch(() => {}); }, 1500);
App.addListener('appStateChange', ({ isActive }) => {
  if (isActive) checkForUpdate().catch(() => {});
});
