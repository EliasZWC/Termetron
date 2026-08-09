import {
  CapacitorBarcodeScanner,
  CapacitorBarcodeScannerCameraDirection,
  CapacitorBarcodeScannerScanOrientation,
  CapacitorBarcodeScannerTypeHint,
} from '@capacitor/barcode-scanner';

const urlInput = document.getElementById('url');

// 记住上次连接地址
urlInput.value = localStorage.getItem('qt_url') || '';

function connect(raw) {
  let url = (raw || '').trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
  // 从二维码来的可能是 qt://<url>?t=<token>，转为 https
  url = url.replace(/^qt:\/\//i, 'https://');
  localStorage.setItem('qt_url', url);
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
