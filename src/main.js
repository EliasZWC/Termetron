import {
  CapacitorBarcodeScanner,
  CapacitorBarcodeScannerCameraDirection,
  CapacitorBarcodeScannerScanOrientation,
  CapacitorBarcodeScannerTypeHint,
} from '@capacitor/barcode-scanner';
import { App } from '@capacitor/app';

const urlInput = document.getElementById('url');

// 设备返回键：连接页（App 首页）按返回直接退出；进入隧道后由 QT 前端接管
// （会话视窗→目录页，目录页连按两次退出）
App.addListener('backButton', () => { App.exitApp(); });
const connBtn = document.getElementById('connect');

// 版本号由 vite define 注入（__APP_VERSION__ 来自 package.json，与 README **Version:** 同步）
// eslint-disable-next-line no-undef
document.getElementById('ver').textContent =
  'QT v' + (typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.0.1') +
  ' · docs: lib/qt/README.md';

// 记住上次连接地址
urlInput.value = localStorage.getItem('qt_url') || '';

// 隧道 URL 可达性探测：qt remote off 后 trycloudflare URL 失效（Cloudflare 返回 530），
// 直接导航会进入错误页且无法返回。探测 QT 的 /api/remote/status：
//  - 隧道开着：QT 返回 200 且带 CORS 头（Access-Control-Allow-Origin:*）-> fetch 成功
//  - 隧道关闭：Cloudflare 530 页无 CORS 头 -> 跨域被拦 -> fetch reject -> 判定不可达
function probe(url) {
  return new Promise((resolve) => {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 8000);
    fetch(url.replace(/\/$/, '') + '/api/remote/status', { cache: 'no-store', signal: ctl.signal })
      .then((r) => { clearTimeout(t); resolve(r.status === 200); })
      .catch(() => { clearTimeout(t); resolve(false); });
  });
}
function setBusy(b) {
  connBtn.disabled = b;
  connBtn.textContent = b ? 'connecting ...' : 'connect';
}

async function connect(raw) {
  let url = (raw || '').trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
  // 从二维码来的可能是 qt://<url>?t=<token>，转为 https
  url = url.replace(/^qt:\/\//i, 'https://');
  localStorage.setItem('qt_url', url);
  setBusy(true);
  const ok = await probe(url);
  if (!ok) {
    setBusy(false);
    alert('Tunnel unreachable.\nRun "qt remote on" on the computer again, then re-scan or paste the new URL.');
    return;
  }
  window.location.href = url;   // WebView 导航到隧道 URL，QT 前端接管密码/配对认证
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
    alert('scan failed: ' + (e.message || e));
  }
});
