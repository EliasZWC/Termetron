#!/usr/bin/env python3
"""CI 中 cap add android 之后统一修补 android/ 原生工程。

1) 注入 OutSystems Azure maven 源：@capacitor/barcode-scanner@1.x 的原生依赖
   com.github.outsystems:osbarcode-android 只发布在该源上；Gradle 解析 :app 的
   runtime classpath 只查根项目 allprojects.repositories（依赖模块自己的 repos
   不被采用），必须注入到根 build.gradle。
2) minSdkVersion 22 -> 26：osbarcode-android:1.1.x 要求 minSdk >= 26。
3) 深色状态栏/导航栏：Capacitor 默认浅色主题导致手机状态栏灰色（非全屏观感），
   注入 --bg #0a0e14 同色，配合 WebView 背景实现全屏。
4) versionName 从 package.json 读取（与 lib/termetron/README.md **Version:** 同步，随迭代递增）。
"""
import json
import re
import sys
from pathlib import Path

STATUS_BAR_COLOR = "#0a0e14"

FEED = ("https://pkgs.dev.azure.com/OutSystemsRD/"
        "9e79bc5b-69b2-4476-9ca5-d67594972a52/"
        "_packaging/PublicArtifactRepository/maven/v1")


def patch_build_gradle() -> None:
    p = Path("android/build.gradle")
    s = p.read_text(encoding="utf-8")
    if "OutSystemsRD" in s:
        print("  build.gradle: azure repo already present, skip")
        return
    old = """allprojects {
    repositories {
        google()
        mavenCentral()
    }
}"""
    new = f"""allprojects {{
    repositories {{
        google()
        mavenCentral()
        maven {{
            url '{FEED}'
            name 'Azure'
            credentials {{
                username = "optional"
                password = ""
            }}
            content {{
                includeGroup "com.github.outsystems"
            }}
        }}
    }}
}}"""
    if old not in s:
        print("  build.gradle: allprojects block not found", file=sys.stderr)
        sys.exit(1)
    p.write_text(s.replace(old, new, 1), encoding="utf-8")
    print("  build.gradle: azure repo injected")


def patch_variables_gradle() -> None:
    p = Path("android/variables.gradle")
    s = p.read_text(encoding="utf-8")
    if "minSdkVersion = 26" in s:
        print("  variables.gradle: minSdk already 26, skip")
        return
    old = "minSdkVersion = 22"
    new = "minSdkVersion = 26"
    if old not in s:
        print("  variables.gradle: minSdkVersion = 22 not found", file=sys.stderr)
        sys.exit(1)
    p.write_text(s.replace(old, new, 1), encoding="utf-8")
    print("  variables.gradle: minSdkVersion 22 -> 26")


def patch_styles() -> None:
    """深色状态栏/导航栏：给所有 style 注入 statusBarColor 等（浅色主题下状态栏灰）。"""
    p = Path("android/app/src/main/res/values/styles.xml")
    if not p.exists():
        print("  styles.xml not found, skip", file=sys.stderr)
        return
    s = p.read_text(encoding="utf-8")
    if "android:statusBarColor" in s:
        print("  styles.xml: dark status bar already present, skip")
        return
    items = (
        f'\n        <item name="android:statusBarColor">{STATUS_BAR_COLOR}</item>'
        f'\n        <item name="android:navigationBarColor">{STATUS_BAR_COLOR}</item>'
        '\n        <item name="android:windowLightStatusBar">false</item>'
    )
    s = s.replace("</style>", items + "\n    </style>")
    p.write_text(s, encoding="utf-8")
    print(f"  styles.xml: dark status/nav bar -> {STATUS_BAR_COLOR}")


def patch_version_name() -> None:
    """versionName 从 package.json 同步（单一来源：package.json / README **Version:**）。"""
    ver = json.loads(Path("package.json").read_text(encoding="utf-8"))["version"]
    p = Path("android/app/build.gradle")
    s = p.read_text(encoding="utf-8")
    new = f'versionName "{ver}"'
    if new in s:
        print(f"  build.gradle: versionName already {ver}, skip")
        return
    s2, n = re.subn(r'versionName "[^"]*"', new, s, count=1)
    if n == 0:
        print("  build.gradle: versionName not found", file=sys.stderr)
        sys.exit(1)
    p.write_text(s2, encoding="utf-8")
    print(f"  build.gradle: versionName -> {ver}")


APK_INSTALL_PERMISSION = (
    '\n    <uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />'
)

APK_PROVIDER_BLOCK = """        <provider
            android:name="androidx.core.content.FileProvider"
            android:authorities="${applicationId}.fileprovider"
            android:exported="false"
            android:grantUriPermissions="true">
            <meta-data
                android:name="android.support.FILE_PROVIDER_PATHS"
                android:resource="@xml/file_paths" />
        </provider>
"""

FILE_PATHS_XML = """<?xml version="1.0" encoding="utf-8"?>
<paths>
    <cache-path name="apk" path="." />
</paths>
"""

APK_INSTALLER_JAVA = '''package dev.qt.terminal;

import android.content.Intent;
import android.net.Uri;

import androidx.core.content.FileProvider;

import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.File;

/** App 内更新：把已下载的 APK 通过 FileProvider 交给系统安装器安装。 */
@CapacitorPlugin(name = "ApkInstaller")
public class ApkInstallerPlugin extends Plugin {

    @PluginMethod
    public void install(PluginCall call) {
        String path = call.getString("path");
        if (path == null || path.isEmpty()) {
            call.reject("path required");
            return;
        }
        try {
            File file = new File(path);
            String auth = getContext().getPackageName() + ".fileprovider";
            Uri apkUri = FileProvider.getUriForFile(getContext(), auth, file);
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(apkUri, "application/vnd.android.package-archive");
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getActivity().startActivity(intent);
            call.resolve();
        } catch (Exception e) {
            call.reject(e.getMessage());
        }
    }
}
'''

MAIN_ACTIVITY_JAVA = '''package dev.qt.terminal;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(ApkInstallerPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
'''


def patch_apk_install() -> None:
    """App 内更新：APK 安装能力。
    - REQUEST_INSTALL_PACKAGES 权限 + FileProvider（Android 8+ 安装 APK 必需）
    - ApkInstallerPlugin.java（Capacitor 插件：FileProvider URI -> 系统安装器）
    - MainActivity 注册插件；下载由 @capacitor/filesystem 写 cache，file_paths 共享。
    """
    manifest = Path("android/app/src/main/AndroidManifest.xml")
    s = manifest.read_text(encoding="utf-8")

    if "REQUEST_INSTALL_PACKAGES" not in s:
        if "</manifest>" not in s:
            print("  manifest: </manifest> not found", file=sys.stderr)
            sys.exit(1)
        s = s.replace("</manifest>", APK_INSTALL_PERMISSION + "\n</manifest>", 1)

    if "fileprovider" not in s:
        if "</application>" not in s:
            print("  manifest: </application> not found", file=sys.stderr)
            sys.exit(1)
        s = s.replace("</application>", APK_PROVIDER_BLOCK + "\n    </application>", 1)
    manifest.write_text(s, encoding="utf-8")
    print("  AndroidManifest: REQUEST_INSTALL_PACKAGES + FileProvider")

    xml = Path("android/app/src/main/res/xml/file_paths.xml")
    if not xml.exists():
        xml.parent.mkdir(parents=True, exist_ok=True)
        xml.write_text(FILE_PATHS_XML, encoding="utf-8")
        print("  file_paths.xml: created (cache-path)")
    else:
        print("  file_paths.xml: already present, skip")

    plugin = Path("android/app/src/main/java/dev/qt/terminal/ApkInstallerPlugin.java")
    if not plugin.exists():
        plugin.parent.mkdir(parents=True, exist_ok=True)
        plugin.write_text(APK_INSTALLER_JAVA, encoding="utf-8")
        print("  ApkInstallerPlugin.java: created")
    else:
        print("  ApkInstallerPlugin.java: already present, skip")

    act = Path("android/app/src/main/java/dev/qt/terminal/MainActivity.java")
    a = act.read_text(encoding="utf-8")
    if "registerPlugin" in a:
        print("  MainActivity: plugin already registered, skip")
    else:
        act.write_text(MAIN_ACTIVITY_JAVA, encoding="utf-8")
        print("  MainActivity: registerPlugin(ApkInstallerPlugin.class)")


SPLASH_BG = "#0b0f14"

# Android 12+ 系统 SplashScreen：深色背景 + 品牌 launcher 图标（values-v31 优先于 values）
SPLASH_V31 = """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <style name="AppTheme.NoActionBarLaunch" parent="Theme.SplashScreen">
        <item name="android:windowSplashScreenBackground">#0b0f14</item>
        <item name="android:windowSplashScreenAnimatedIcon">@mipmap/ic_launcher</item>
        <item name="android:windowSplashScreenIconBackgroundColor">#0b0f14</item>
    </style>
</resources>
"""


def patch_splash() -> None:
    """品牌启动页（双保险，不依赖 capacitor-assets）：
    1) 把 assets/splash.png 复制为 android drawable-nodpi/splash.png（保证 @drawable/splash 存在）；
    2) values-v31/styles.xml 强制 Android 12+ 系统 splash = Theme.SplashScreen + 深色背景 + launcher 图标；
    3) values/styles.xml 的 AppTheme.NoActionBarLaunch android:background -> @drawable/splash（Android <12 完整 splash.png）。
    """
    # 1) splash 图片资源
    src = Path("assets/splash.png")
    dst = Path("android/app/src/main/res/drawable-nodpi/splash.png")
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            import shutil
            shutil.copyfile(src, dst)
            print("  drawable-nodpi/splash.png: copied from assets")
        else:
            print("  drawable-nodpi/splash.png: already present, skip")
    else:
        print("  assets/splash.png not found, skip splash image", file=sys.stderr)
    # 2) Android 12+ 系统 splash（values-v31 覆盖，强制品牌）
    v31 = Path("android/app/src/main/res/values-v31/styles.xml")
    v31.parent.mkdir(parents=True, exist_ok=True)
    v31.write_text(SPLASH_V31, encoding="utf-8")
    print("  values-v31/styles.xml: Android 12+ splash (dark bg + launcher icon)")
    # 3) Android <12 完整 splash.png（@drawable/splash）
    vals = Path("android/app/src/main/res/values/styles.xml")
    if vals.exists():
        s = vals.read_text(encoding="utf-8")
        mark = '<style name="AppTheme.NoActionBarLaunch"'
        if mark in s:
            i0 = s.index(mark)
            i1 = s.index("</style>", i0)
            block = s[i0:i1]
            if "android:background" not in block:
                inject = '\n        <item name="android:background">@drawable/splash</item>'
                s = s[:i1] + inject + s[i1:]
                vals.write_text(s, encoding="utf-8")
                print("  values/styles.xml: launch background -> @drawable/splash")
            else:
                print("  values/styles.xml: launch background already set, skip")
        else:
            print("  values/styles.xml: AppTheme.NoActionBarLaunch not found, skip", file=sys.stderr)
    else:
        print("  values/styles.xml not found, skip", file=sys.stderr)



def patch_splash_dep() -> None:
    """确保 androidx.core:core-splashscreen 依赖（Theme.SplashScreen parent 需要；
    @capacitor/splash-screen 通常会带，这里兜底）。"""
    p = Path("android/app/build.gradle")
    if not p.exists():
        print("  app/build.gradle not found, skip splash dep", file=sys.stderr)
        return
    s = p.read_text(encoding="utf-8")
    if "core-splashscreen" in s:
        print("  build.gradle: core-splashscreen already present, skip")
        return
    if "dependencies {" not in s:
        print("  build.gradle: dependencies block not found", file=sys.stderr)
        return
    idx = s.index("dependencies {") + len("dependencies {")
    s = s[:idx] + '\n    implementation "androidx.core:core-splashscreen:1.2.0"' + s[idx:]
    p.write_text(s, encoding="utf-8")
    print("  build.gradle: core-splashscreen:1.2.0 added")


SIGNING_BLOCK = """
    signingConfigs {
        debug {
            storeFile file('android-debug.p12')
            storePassword 'android'
            keyAlias 'androiddebugkey'
            keyPassword 'android'
            storeType 'PKCS12'
        }
    }"""


def patch_signing() -> None:
    """固定 APK 签名：使用仓库内的 android-debug.p12（PKCS12，入库，确定性签名）。

    背景：GitHub Actions 每次在全新环境构建，若依赖默认 debug keystore，每次随机生成
    → 各版本 APK 签名不同 → 覆盖安装报「签名冲突」。把固定 keystore 提交进仓库并显式
    配置 signingConfigs.debug，所有构建用同一密钥 → 签名 100% 一致（AGP 默认 debug
    buildType 即使用 signingConfigs.debug）。
    """
    src = Path("android-debug.p12")
    if not src.exists():
        print("  android-debug.p12 not found in repo root, skip signing", file=sys.stderr)
        return
    import shutil
    dst = Path("android/app/android-debug.p12")
    shutil.copyfile(src, dst)
    print("  android/app/android-debug.p12: copied from repo root")

    p = Path("android/app/build.gradle")
    s = p.read_text(encoding="utf-8")
    if "android-debug.p12" in s:
        print("  build.gradle: fixed signing already present, skip")
        return
    marker = "android {"
    if marker not in s:
        print("  build.gradle: 'android {' not found", file=sys.stderr)
        sys.exit(1)
    # 兜底：若 buildTypes.debug 已存在但未引用 signingConfig，先补一行
    # （必须在注入 signingConfigs 之前调用，否则正则会误匹配 signingConfigs.debug 块）
    s = _ensure_debug_signing(s)
    s = s.replace(marker, marker + SIGNING_BLOCK, 1)
    p.write_text(s, encoding="utf-8")
    print("  build.gradle: fixed debug signingConfig (android-debug.p12)")


def _ensure_debug_signing(s: str) -> str:
    """若 buildTypes 里存在 debug 块且未写 signingConfig，则在块内补一行引用。"""
    import re

    def repl(m):
        block = m.group(0)
        if "signingConfig" in block:
            return block
        r = block.rstrip()
        if r.endswith("}"):
            idx = r.rfind("}")
            return r[:idx] + "            signingConfig signingConfigs.debug\n" + r[idx:]
        return block

    return re.sub(r"debug\s*\{[^}]*\}", repl, s, count=1)


if __name__ == "__main__":
    print("patching android/ ...")
    patch_build_gradle()
    patch_variables_gradle()
    patch_styles()
    patch_version_name()
    patch_apk_install()
    patch_splash()
    patch_splash_dep()
    patch_signing()
    print("done")
