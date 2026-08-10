import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'dev.qt.terminal',
  appName: 'QT',
  webDir: 'www',
  android: {
    allowMixedContent: true   // 允许 http(s) 混合内容（隧道为 https，一般不需；保险起见）
  },
  server: {
    androidScheme: 'https',
    // 允许在 WebView 内导航到隧道域（否则 Capacitor 默认把未白名单 http(s)
    // 外链交给外部浏览器，扫码后不会回到 App 内）
    allowNavigation: ['*.trycloudflare.com']
  }
};

export default config;
