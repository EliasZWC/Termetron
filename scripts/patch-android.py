#!/usr/bin/env python3
"""CI 中 cap add android 之后统一修补 android/ 原生工程。

1) 注入 OutSystems Azure maven 源：@capacitor/barcode-scanner@1.x 的原生依赖
   com.github.outsystems:osbarcode-android 只发布在该源上；Gradle 解析 :app 的
   runtime classpath 只查根项目 allprojects.repositories（依赖模块自己的 repos
   不被采用），必须注入到根 build.gradle。
2) minSdkVersion 22 -> 26：osbarcode-android:1.1.x 要求 minSdk >= 26。
3) 深色状态栏/导航栏：Capacitor 默认浅色主题导致手机状态栏灰色（非全屏观感），
   注入 --bg #0a0e14 同色，配合 WebView 背景实现全屏。
4) versionName 从 package.json 读取（与 lib/qt/README.md **Version:** 同步，随迭代递增）。
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


if __name__ == "__main__":
    print("patching android/ ...")
    patch_build_gradle()
    patch_variables_gradle()
    patch_styles()
    patch_version_name()
    patch_apk_install()
    print("done")
