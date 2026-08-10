// App 内自动更新：检测 GitHub Release -> 提示 -> 下载 APK -> 系统安装器。
// 触发时机 = 每次“进入 App”（冷启动 / 后台切回前台 appStateChange isActive），
// 一直在用则不检查；另加 30s 内存防抖，避免同一次进入 / 快速切前后台重复请求。
// 前置条件：App 托管在公开仓库 EliasZWC/Termetron；CI 每次构建把 APK 发成 GitHub
// Release（tag=v<version>，附件 termetron-v<version>.apk）。
import { Capacitor } from '@capacitor/core';
import { Directory, Filesystem } from '@capacitor/filesystem';

const REPO = 'EliasZWC/Termetron';
const DEBOUNCE_MS = 30000; // 30s 内存防抖（非持久化）

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

function arrayBufferToBase64(buffer) {
  let binary = '';
  const bytes = new Uint8Array(buffer);
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

export async function checkForUpdate() {
  if (Capacitor.getPlatform() !== 'android') return; // 仅 App 内（Web 调试跳过）
  const now = Date.now();
  if (now - _lastCheck < DEBOUNCE_MS) return; // 防抖：同一次进入只查一次
  _lastCheck = now;

  let rel;
  try {
    const res = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, { cache: 'no-store' });
    if (!res.ok) return; // 仓库未公开 / 无 release / 网络异常：静默
    rel = await res.json();
  } catch (e) {
    return;
  }

  const tag = String(rel.tag_name || '').replace(/^v/, '');
  // eslint-disable-next-line no-undef
  const cur = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.0.1';
  if (!tag || semverLte(tag, cur)) return; // 已是最新

  const apk = (rel.assets || []).find((a) => /\.apk$/i.test(a.name));
  if (!apk) return;

  // eslint-disable-next-line no-alert
  if (!confirm(`发现新版本 v${tag}（当前 v${cur}）\n\n下载并安装？`)) return;
  await downloadAndInstall(apk.browser_download_url, `termetron-v${tag}.apk`);
}

async function downloadAndInstall(url, name) {
  try {
    const resp = await fetch(url, { cache: 'no-store' });
    if (!resp.ok) throw new Error('download failed: ' + resp.status);
    const buf = await resp.arrayBuffer();
    const saved = await Filesystem.writeFile({
      path: name,
      data: arrayBufferToBase64(buf),
      directory: Directory.Cache,
      recursive: true,
    });
    const plugin = Capacitor.Plugins.ApkInstaller;
    if (!plugin) {
      // eslint-disable-next-line no-alert
      alert('更新下载完成，但安装器不可用：请从 GitHub Release 手动安装。');
      return;
    }
    await plugin.install({ path: saved.uri.replace(/^file:\/\//, '') });
  } catch (e) {
    // eslint-disable-next-line no-alert
    alert('更新失败：' + (e.message || e));
  }
}
