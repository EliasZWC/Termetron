# Termetron Mobile App (Capacitor)

The Android client for **Termetron** — a standalone app wrapping the web
terminal in a **Capacitor** WebView:

- Connection page (paste the tunnel URL or **scan the QR code**)
- After connecting, enters the Termetron terminal (auth / pairing is handled
  by the Termetron front end)
- Own icon and window — feels like a native app (but zero native code)

## Prerequisites

- **Node.js** (>=18) + npm
- **Android Studio** for local builds; iOS requires a Mac + Xcode + Apple
  developer account

## Build option A: cloud build (recommended, zero local setup)

The repo ships a GitHub Actions workflow (`.github/workflows/build.yml`):

1. Push this directory to a GitHub repository. Make it **public** so the
   in-app auto-update can fetch releases without authentication.
2. Trigger `build-apk` manually in Actions (or it runs on every push).
3. Download the built APK from the Actions **Artifacts**.

The cloud handles Node/JDK/Android SDK install + gradle build automatically —
no local toolchain needed.

## Build option B: local build

```bash
cd lib/termetron/app
npm install                     # install deps (@capacitor/*, scanner, splash)
npm run build                   # vite build -> www/
npx cap add android             # first time only (generates android/)
npx cap sync android            # sync web output + native plugins
npx cap open android            # open in Android Studio
# Android Studio: Build -> Build APK(s); output in android/app/build/outputs/apk/
```

The APK installs directly on an Android phone (no store needed).

## Usage

1. On the computer: `termetron remote on` → shows a QR code (URL) plus the
   one-time password (shown separately).
2. On the phone: open Termetron → scan the QR (or paste the URL).
3. Type the one-time password → a device key appears.
4. On the computer: `termetron allow <key>` (or approve the popup).
5. The phone enters the Termetron terminal. It stays connected across
   screen-off / re-open until `termetron remote off`.

## In-app auto-update

The app checks the public GitHub releases of `EliasZWC/Termetron` every time it
is entered (cold start / return to foreground) and offers to download + install
a new APK in-app. Bump the version in `package.json` and `../README.md`
(`**Version:**`) on every release.

## Notes

- **Connection address**: the tunnel URL changes on every `termetron remote
  on` (trycloudflare random subdomain). The app remembers the last address and
  supports QR scanning.
- **iOS**: requires a Mac + Xcode + Apple developer account; `npx cap add ios`
  then handle as needed (not built on this Windows setup).
- **Security**: the auth model lives on the Termetron side (one-time password +
  device key + computer approval). The app itself stores no credentials — it
  only navigates to the tunnel URL.
