// 测试用 mock：模拟 @capacitor/core 的 Capacitor 全局
// 记录 ApkInstaller.install 的调用参数到 window.__installCalls 供断言
export const Capacitor = {
  getPlatform: () => 'android',
  Plugins: {
    ApkInstaller: {
      install: async (opts) => {
        window.__installCalls = window.__installCalls || [];
        window.__installCalls.push({ ...opts });
      },
    },
  },
};
