# QT 手机端 App（Capacitor）

QT 的手机客户端——用 **Capacitor**（WebView 套壳）包装的独立 App：
- 打开后显示连接页（输入隧道 URL 或**扫二维码**）
- 连接后进入 QT 终端（认证/配对在 QT 前端完成）
- 有独立图标、独立窗口，像原生 App（但零原生代码）

## 前置要求

- **Node.js**（>=18）+ npm
- **Android Studio**（本地构建 apk 用；iOS 需 Mac + Xcode + Apple 开发者账号）

## 构建方式 A：云构建（推荐，零本地安装）

仓库已带 GitHub Actions workflow（`.github/workflows/build.yml`）：

1. 把本目录推到一个 GitHub 仓库（私有即可）
2. Actions 里手动触发 `build-apk`（或推送自动触发）
3. 构建完成后在 Actions 的 **Artifacts** 里下载 `qt-apk`

云端自动完成 Node/JDK/Android SDK 安装 + gradle 构建，无需本地任何工具链。

## 构建方式 B：本地构建

```bash
cd lib/termetron/app
npm install                     # 安装依赖（含 @capacitor/* 与扫码插件）
npm run build                   # vite 构建 -> www/
npx cap add android             # 首次生成 android/ 工程（之后不需要）
npx cap sync android            # 同步 web 产物 + 原生插件
npx cap open android            # 在 Android Studio 打开
# 在 Android Studio 里 Build -> Build APK(s)，产物在 android/app/build/outputs/apk/
```

构建出的 apk 可直接安装到 Android 手机（无需商店）。分发给同学：直接发 apk 文件。

## 使用

1. 电脑上 QT：`qt remote on` → 屏幕显示二维码（含 URL + 一次性密码）
2. 手机打开 QT App → 扫电脑二维码（或手动粘贴 URL）
3. 输入一次性密码 → 显示设备密钥
4. 电脑上 `qt allow <密钥>` 放行
5. 手机进入 QT 终端（息屏/重开保持连接，`qt remote off` 才断开）

## 说明

- **连接地址**：每次 `qt remote on` 的隧道 URL 会变（trycloudflare 随机子域），App 会记住上次地址，也支持扫码免输入
- **iOS**：构建需 Mac + Xcode + Apple 开发者账号；`npx cap add ios` 后按需处理（当前 Windows 环境不构建 iOS）
- **安全**：认证模型在 QT 侧（一次性密码 + 设备密钥 + 电脑放行），App 本身不存凭据，仅导航到隧道 URL
