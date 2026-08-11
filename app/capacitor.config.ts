import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'dev.qt.terminal',
  appName: 'Termetron',
  webDir: 'www',
  android: {
    allowMixedContent: true   // 允许 http(s) 混合内容（隧道为 https，一般不需；保险起见）
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 0,      // 原生 splash 极短——品牌 splash 由 Web 层 CSS animation 接管（Android 12+ 系统 splash 不受此项控制）
      launchAutoHide: true,
      backgroundColor: '#0b0f14', // 与 Web splash 深色底一致
      androidScaleType: 'CENTER_CROP',
      showSpinner: false,
      splashFullScreen: true,
      splashImmersive: true,
    },
  },
  server: {
    androidScheme: 'https',
    // 允许在 WebView 内导航：隧道域（trycloudflare）+ 本地服务器（localhost，供认证失败后返回连接登录页）。
    // 缺 localhost 时，从隧道域导航回 https://localhost/ 会被当外部链接交给系统浏览器 → Back 无效。
    allowNavigation: ['*.trycloudflare.com', 'localhost']
  }
};

export default config;
