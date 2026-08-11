// 测试用 mock：模拟 @capacitor/filesystem
// 通过 window.__downloadCfg 控制行为：
//   { fail: true, failMsg }  -> downloadFile 抛错
//   否则模拟 progress 事件（30/70/100）并返回 { path: <cache>/<name> }
export const Directory = { Cache: 'CACHE', Data: 'DATA', Documents: 'DOCUMENTS' };

export const Filesystem = {
  downloadFile: async (opts) => {
    const cfg = window.__downloadCfg || {};
    if (cfg.fail) throw new Error(cfg.failMsg || 'mock download failed');
    const cb = window.__progressCb;
    const step = (p) => new Promise((r) => setTimeout(() => { if (cb) cb({ percent: p }); r(); }, 300));
    await step(30);
    await step(70);
    await step(100);
    // DownloadFileResult 字段是 path（与真实 API 一致，便于验证 updater 用 saved.path）
    return { path: '/data/user/0/dev.qt.terminal/cache/' + opts.path };
  },
  addListener: async (eventName, cb) => {
    if (eventName === 'progress') window.__progressCb = cb;
    return { remove: () => { if (eventName === 'progress') window.__progressCb = null; } };
  },
};
