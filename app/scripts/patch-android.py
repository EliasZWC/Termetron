#!/usr/bin/env python3
"""CI 中 cap add android 之后统一修补 android/ 原生工程。

1) 注入 OutSystems Azure maven 源：@capacitor/barcode-scanner@1.x 的原生依赖
   com.github.outsystems:osbarcode-android 只发布在该源上；Gradle 解析 :app 的
   runtime classpath 只查根项目 allprojects.repositories（依赖模块自己的 repos
   不被采用），必须注入到根 build.gradle。
2) minSdkVersion 22 -> 26：osbarcode-android:1.1.x 要求 minSdk >= 26。
3) 深色状态栏/导航栏：Capacitor 默认浅色主题导致手机状态栏灰色（非全屏观感），
   注入与**页面底色**同色的 statusBarColor（#1B1B1B），配合 WebView 背景实现无缝全屏。
4) versionName 从 package.json 读取（与 lib/termetron/README.md **Version:** 同步，随迭代递增）。
"""
import json
import re
import struct
import sys
import zlib
from pathlib import Path

# ⚠️ 必须等于网页**页面底色**（--bg #1B1B1B），不是顶栏色（--bar #131313）。
# 状态栏比页面深一档会在顶部形成一条明显色阶接缝（用户 v0.8.10 反馈的那个色差）。
STATUS_BAR_COLOR = "#1B1B1B"
BG_RGB = (0x1B, 0x1B, 0x1B)

FEED = ("https://pkgs.dev.azure.com/OutSystemsRD/"
        "9e79bc5b-69b2-4476-9ca5-d67594972a52/"
        "_packaging/PublicArtifactRepository/maven/v1")


# ──────────────────────────────────────────────────────────────
#  纯 stdlib 的 PNG 读 / 写（CI 不装 Pillow，不能引入新依赖）
# ──────────────────────────────────────────────────────────────
def _png_read(path: Path):
    """读 8-bit RGB/RGBA PNG -> (w, h, channels, bytearray 像素数据)。"""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} 不是 PNG")
    pos = 8
    w = h = bitdepth = colortype = None
    idat = bytearray()
    while pos < len(data):
        (ln,) = struct.unpack(">I", data[pos:pos + 4])
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            w, h, bitdepth, colortype = struct.unpack(">IIBB", chunk[:10])
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
    if bitdepth != 8 or colortype not in (2, 6):
        raise ValueError(f"{path}: 只支持 8-bit RGB/RGBA（实测 {bitdepth}/{colortype}）")
    ch = 3 if colortype == 2 else 4
    raw = zlib.decompress(bytes(idat))
    stride = w * ch
    out = bytearray(h * stride)
    prev = bytearray(stride)
    src = 0
    for y in range(h):
        f = raw[src]
        src += 1
        line = bytearray(raw[src:src + stride])
        src += stride
        if f == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 0xFF
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, ch, out


def _png_write(path: Path, w: int, h: int, rgb: bytearray) -> None:
    """写 8-bit RGB PNG（filter=0）。"""
    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]

    def chunk(typ: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + typ + payload
                + struct.pack(">I", zlib.crc32(typ + payload) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


def _composite_on_bg(src: Path, dst: Path) -> None:
    """把带 alpha 的前景图合成到 #1B1B1B 实底上，输出不透明 PNG。"""
    w, h, ch, px = _png_read(src)
    out = bytearray(w * h * 3)
    for i in range(w * h):
        if ch == 4:
            r, g, b, a = px[i * 4], px[i * 4 + 1], px[i * 4 + 2], px[i * 4 + 3]
            # alpha 混合到实底
            out[i * 3] = (r * a + BG_RGB[0] * (255 - a)) // 255
            out[i * 3 + 1] = (g * a + BG_RGB[1] * (255 - a)) // 255
            out[i * 3 + 2] = (b * a + BG_RGB[2] * (255 - a)) // 255
        else:
            out[i * 3], out[i * 3 + 1], out[i * 3 + 2] = px[i * 3], px[i * 3 + 1], px[i * 3 + 2]
    _png_write(dst, w, h, out)


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
    """深色状态栏/导航栏：给所有 style 注入 statusBarColor 等（浅色主题下状态栏灰）。

    ⚠️ 颜色必须与网页 **页面底色** 完全一致，不能与顶栏一致。
       用户反馈（v0.8.10）：「状态栏背景的颜色和状态栏下面的颜色不一样，有色差」。
       根因：当时用 --bar #131313（顶栏色），而页面底是 #1B1B1B，
       状态栏与页面之间出现一条明显色阶 —— 界面里最显眼的接缝。
       判据：状态栏 = 页面底色（#1B1B1B），与内容区无缝；
             顶栏/标签栏比它更深（#131313），靠亮度差自然分层。
    """
    p = Path("android/app/src/main/res/values/styles.xml")
    if not p.exists():
        print("  styles.xml not found, skip", file=sys.stderr)
        return
    s = p.read_text(encoding="utf-8")

    # 已存在就改写颜色值（历史构建里可能残留旧色），不重复注入
    if "android:statusBarColor" in s:
        s = re.sub(r'(<item name="android:statusBarColor">)[^<]*(</item>)',
                   rf'\g<1>{STATUS_BAR_COLOR}\g<2>', s)
        s = re.sub(r'(<item name="android:navigationBarColor">)[^<]*(</item>)',
                   rf'\g<1>{STATUS_BAR_COLOR}\g<2>', s)
        p.write_text(s, encoding="utf-8")
        print(f"  styles.xml: status/nav bar colour -> {STATUS_BAR_COLOR} (matches page bg)")
        return

    items = (
        f'\n        <item name="android:statusBarColor">{STATUS_BAR_COLOR}</item>'
        f'\n        <item name="android:navigationBarColor">{STATUS_BAR_COLOR}</item>'
        '\n        <item name="android:windowLightStatusBar">false</item>'
    )
    s = s.replace("</style>", items + "\n    </style>")
    p.write_text(s, encoding="utf-8")
    print(f"  styles.xml: status/nav bar -> {STATUS_BAR_COLOR} (matches page bg)")


def patch_launcher_background() -> None:
    """重写自适应图标配置，使其与 Scholarius / Livolog 一致。

    ⚠️ 三处都会被 `capacitor-assets generate --android` 改坏，
       所以每次构建后都必须重写（这就是 v0.8.10 / v0.8.11
       「桌面图标没改 / 背景色不一样」的真因）：

       1. `values/ic_launcher_background.xml` 被写成 **#FFFFFF（纯白）**
       2. `mipmap-anydpi-v26/ic_launcher.xml` 给 background 加了
          **inset="16.7%"** —— 背景四边各缩进 1/6，外面那圈没有填充，
          露出系统底色，看起来就是「图标外围有一圈不同色的边」
       3. `ic_launcher.png` 只有约 2% 不透明像素（96% 是纯透明），
          Android 7 及以下会显示成一个几乎空白的图标
     """
    # 1) values/ic_launcher_background.xml（color 资源）
    p = Path("android/app/src/main/res/values/ic_launcher_background.xml")
    if p.exists():
        p.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<resources>\n'
            f'    <color name="ic_launcher_background">{STATUS_BAR_COLOR}</color>\n'
            '</resources>\n',
            encoding="utf-8")
        print(f"  values/ic_launcher_background.xml: {STATUS_BAR_COLOR}")
    else:
        print("  values/ic_launcher_background.xml not found, skip", file=sys.stderr)

    # 2) drawable/ic_launcher_background.xml（vector 填充）
    p = Path("android/app/src/main/res/drawable/ic_launcher_background.xml")
    if p.exists():
        s = p.read_text(encoding="utf-8")
        s2 = re.sub(r'(android:fillColor=")[^"]*(")', rf'\g<1>{STATUS_BAR_COLOR}\g<2>', s)
        if s2 != s:
            p.write_text(s2, encoding="utf-8")
            print(f"  drawable/ic_launcher_background.xml: {STATUS_BAR_COLOR}")
        else:
            # 值已正确（重复运行时会走到这里），不是错误
            m = re.search(r'android:fillColor="([^"]+)"', s)
            print(f"  drawable/ic_launcher_background.xml: 已是 {m.group(1) if m else '?'}")
    else:
        print("  drawable/ic_launcher_background.xml not found, skip", file=sys.stderr)

    # 3) mipmap-anydpi-v26/ic_launcher{,_round}.xml
    #    ⚠️ 这是「背景色不一样」的真因：
    #       capacitor-assets 生成的是
    #           <background><inset android:drawable="@mipmap/ic_launcher_background"
    #                              android:inset="16.7%" /></background>
    #       背景被 inset 16.7% —— 四边各缩进 1/6，外面那圈没有任何填充，
    #       露出的就是系统底色。所以图标外围有一圈与 #1B1B1B 不同的边。
    #       参考两款应用都没有 inset、也不引用 mipmap：
    #           <background android:drawable="@drawable/ic_launcher_background" />
    #           <foreground android:drawable="@drawable/ic_launcher_foreground" />
    #           <monochrome android:drawable="@drawable/ic_launcher_foreground" />
    adaptive = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@drawable/ic_launcher_background" />\n'
        '    <foreground android:drawable="@drawable/ic_launcher_foreground" />\n'
        '    <monochrome android:drawable="@drawable/ic_launcher_foreground" />\n'
        '</adaptive-icon>\n'
    )
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        p = Path("android/app/src/main/res/mipmap-anydpi-v26") / name
        if p.exists():
            p.write_text(adaptive, encoding="utf-8")
            print(f"  mipmap-anydpi-v26/{name}: no inset, @drawable refs (+monochrome)")

    # 4) drawable/ic_launcher_foreground.xml —— adaptive-icon 需要 drawable，
    #    capacitor-assets 只产出 mipmap-*/ic_launcher_foreground.png，这里补个 bitmap 引用。
    p = Path("android/app/src/main/res/drawable/ic_launcher_foreground.xml")
    src = Path("android/app/src/main/res/mipmap-xxxhdpi/ic_launcher_foreground.png")
    if src.exists():
        p.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<bitmap xmlns:android="http://schemas.android.com/apk/res/android"\n'
            '    android:src="@mipmap/ic_launcher_foreground" />\n',
            encoding="utf-8")
        print("  drawable/ic_launcher_foreground.xml: -> @mipmap/ic_launcher_foreground")

    # 5) ic_launcher.png / ic_launcher_round.png —— 补上实底
    #    ⚠️ 这是「桌面图标是空的」的真因：
    #       capacitor-assets 产出的这两个 PNG **只有符号、没有底板**，
    #       不透明像素仅约 3%。Android 8+ 走 adaptive-icon（有 background 层）
    #       所以看着正常；但 Android 7 及以下、以及部分启动器/文件管理器
    #       直接显示这两个 PNG —— 深色桌面上就是一个空白格子。
    #       参考两款应用的图标资源里根本没有这两个文件（纯 adaptive），
    #       我们要兼容旧系统，就得自己把底色合上去。
    for name in ("ic_launcher", "ic_launcher_round"):
        for d in ("ldpi", "mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi"):
            fg = Path(f"android/app/src/main/res/mipmap-{d}/ic_launcher_foreground.png")
            out = Path(f"android/app/src/main/res/mipmap-{d}/{name}.png")
            if fg.exists():
                try:
                    _composite_on_bg(fg, out)
                except Exception as e:                     # noqa: BLE001
                    print(f"  {out.name} ({d}) 合成失败: {e}", file=sys.stderr)
        print(f"  mipmap-*/{name}.png: 已合成 #1B1B1B 实底")


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

/** App 内更新：把已下载的 APK 通过 FileProvider 交给系统安装器安装。
 *  用 startActivityForResult 等待安装流程结束，结束后把用户带回本 App
 *  （避免安装完成直接停在桌面）。若进程在替换安装时被系统终止，系统安装器
 *  的“打开”按钮同样可回到新版 App。 */
@CapacitorPlugin(name = "ApkInstaller")
public class ApkInstallerPlugin extends Plugin {

    private static final int REQ_INSTALL = 7001;

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
            // 不用 FLAG_ACTIVITY_NEW_TASK：用 startActivityForResult 等安装流程结束再拉回 App
            getActivity().startActivityForResult(intent, REQ_INSTALL);
            call.resolve();
        } catch (Exception e) {
            call.reject(e.getMessage());
        }
    }

    @Override
    public void handleOnActivityResult(int requestCode, int resultCode, Intent data) {
        super.handleOnActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_INSTALL) {
            // 安装流程结束（用户点了“完成”/取消，或安装失败返回），把用户带回 App
            returnToApp();
        }
    }

    private void returnToApp() {
        try {
            Intent i = new Intent(getContext(), MainActivity.class);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            getContext().startActivity(i);
        } catch (Exception ignored) {
        }
    }
}
'''

MAIN_ACTIVITY_JAVA = '''package dev.qt.terminal;

import android.os.Bundle;
import android.webkit.WebView;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(ApkInstallerPlugin.class);
        super.onCreate(savedInstanceState);
    }

    @Override
    public void onBackPressed() {
        // 设备返回键：隧道（远程）页面交给页面 JS 按层级返回（设置页→目录页→连接页），
        // 隧道页无 Capacitor bridge 无法用 JS backButton 捕获，故由原生注入调用 window.__handleBack。
        WebView wv = getBridge() != null ? getBridge().getWebView() : null;
        String url = wv != null ? wv.getUrl() : null;
        if (url != null && !url.startsWith("https://localhost") && !url.startsWith("capacitor://localhost")) {
            wv.evaluateJavascript("window.__handleBack && window.__handleBack();", null);
        } else {
            super.onBackPressed();
        }
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


SPLASH_BG = "#1B1B1B"

# Android 12+ 系统 SplashScreen：深色背景 + 透明图标（原生 splash 视觉上“取消”，
# 由 Web 层 splash 无缝接管；Android 12+ 系统 splash 无法禁用，只能极简化）
SPLASH_V31 = """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <style name="AppTheme.NoActionBarLaunch" parent="Theme.SplashScreen">
        <item name="android:windowSplashScreenBackground">#1B1B1B</item>
        <item name="android:windowSplashScreenAnimatedIcon">@drawable/splash_transparent</item>
        <item name="android:windowSplashScreenIconBackgroundColor">@android:color/transparent</item>
    </style>
</resources>
"""

SPLASH_TRANSPARENT_XML = """<?xml version="1.0" encoding="utf-8"?>
<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="rectangle">
    <solid android:color="#00000000" />
</shape>
"""


def patch_splash() -> None:
    """原生 splash 极简化（视觉上“取消原生 splash”，品牌画面全部交给 Web 层 CSS animation）：
    1) 写透明图标 drawable（Android 12+ 系统 splash 需要 drawable，透明=不显示图标）；
    2) values-v31/styles.xml 强制 Android 12+ 系统 splash = Theme.SplashScreen + 深色背景 + 透明图标；
    3) values/styles.xml 的 AppTheme.NoActionBarLaunch android:background -> #1B1B1B 纯深色（Android <12）。
    不再使用 splash.png / @drawable/splash（避免原生品牌画面与 Web splash 叠加重复）。
    """
    # 1) 透明图标 drawable
    td = Path("android/app/src/main/res/drawable/splash_transparent.xml")
    td.parent.mkdir(parents=True, exist_ok=True)
    td.write_text(SPLASH_TRANSPARENT_XML, encoding="utf-8")
    print("  drawable/splash_transparent.xml: transparent splash icon")
    # 2) Android 12+ 系统 splash（深色背景 + 透明图标）
    v31 = Path("android/app/src/main/res/values-v31/styles.xml")
    v31.parent.mkdir(parents=True, exist_ok=True)
    v31.write_text(SPLASH_V31, encoding="utf-8")
    print("  values-v31/styles.xml: Android 12+ splash (dark bg + transparent icon)")
    # 3) Android <12：launch 背景纯深色（若已有 @drawable/splash 则替换为纯色）
    vals = Path("android/app/src/main/res/values/styles.xml")
    if vals.exists():
        s = vals.read_text(encoding="utf-8")
        mark = '<style name="AppTheme.NoActionBarLaunch"'
        if mark in s:
            i0 = s.index(mark)
            i1 = s.index("</style>", i0)
            block = s[i0:i1]
            import re
            if "android:background" in block:
                # Capacitor styles.xml 用子元素形式：<item name="android:background">value</item>
                s = s[:i0] + re.sub(
                    r'(<item name="android:background">)[^<]*(</item>)',
                    r'\g<1>#1B1B1B\g<2>', block, count=1) + s[i1:]
            else:
                inject = '\n        <item name="android:background">#1B1B1B</item>'
                s = s[:i1] + inject + s[i1:]
            vals.write_text(s, encoding="utf-8")
            print("  values/styles.xml: launch background -> #1B1B1B (solid)")
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
    patch_launcher_background()
    patch_version_name()
    patch_apk_install()
    patch_splash()
    patch_splash_dep()
    patch_signing()
    print("done")
