import { defineConfig } from 'vite';

// 从 package.json 读取版本号（与 lib/qt/README.md **Version:** 保持同步），
// 通过 __APP_VERSION__ 注入前端（About / 连接页底部显示）。
// eslint-disable-next-line @typescript-eslint/no-var-requires
const pkg = require('./package.json');

export default defineConfig({
  base: './',
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  build: { outDir: 'www', emptyOutDir: true }
});
