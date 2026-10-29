import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'dev.qt.terminal',
  appName: 'Termetron',
  webDir: 'www',
  android: {
    // Tailscale 直连用的是明文 http（tailnet 内部已加密），必须允许混合内容
    allowMixedContent: true,
  },
  plugins: {
    SplashScreen: {
      // 原生 splash 极短 —— 品牌 splash 由 Web 层 CSS animation 接管
      // （Android 12+ 系统 splash 不受此项控制）
      launchShowDuration: 0,
      launchAutoHide: true,
      backgroundColor: '#1B1B1B',
      androidScaleType: 'CENTER_CROP',
      showSpinner: false,
      splashFullScreen: true,
      splashImmersive: true,
    },
  },
  server: {
    androidScheme: 'https',
    // ⚠️ allowNavigation 只影响**顶层页面跳转**，不限制 fetch/XHR。
    //    本应用连电脑走的是 fetch（WebView 内 AJAX），所以不需要放行
    //    任意 Tailscale IP —— 而且 Capacitor 也不支持通配符 IP 段。
    //    保留 localhost 仅为将来调试方便。
    allowNavigation: ['localhost'],
  },
};

export default config;
