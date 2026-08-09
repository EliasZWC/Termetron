import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'dev.qt.terminal',
  appName: 'QT',
  webDir: 'www',
  android: {
    allowMixedContent: true   // 允许 http(s) 混合内容（隧道为 https，一般不需；保险起见）
  },
  server: {
    androidScheme: 'https'
  }
};

export default config;
