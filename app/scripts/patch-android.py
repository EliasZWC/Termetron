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

# ⚠️ 两个色**必须分开**，不能共用：
#   · STATUS_BAR_COLOR 用于状态栏/导航栏，必须等于网页**页面底色** --bg (#1E1E1E)。
#     差一档就会在顶部形成肉眼可见的色阶接缝（用户 v0.8.10 / v0.10.2 两次反馈）。
#   · ICON_BG_COLOR 用于自适应图标底板，是参考应用的 #1B1B1B（比页面底色深一点）。
# 历史 bug：两者共用一个 #1B1B1B 常量，页面底色改成 #1E1E1E 后状态栏就没跟着改。
STATUS_BAR_COLOR = "#1E1E1E"
ICON_BG_COLOR = "#1B1B1B"
ICON_BG_RGB = (0x1B, 0x1B, 0x1B)

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
    """把带 alpha 的前景图合成到图标底板色上，输出不透明 PNG。"""
    w, h, ch, px = _png_read(src)
    out = bytearray(w * h * 3)
    for i in range(w * h):
        if ch == 4:
            r, g, b, a = px[i * 4], px[i * 4 + 1], px[i * 4 + 2], px[i * 4 + 3]
            # alpha 混合到实底
            out[i * 3] = (r * a + ICON_BG_RGB[0] * (255 - a)) // 255
            out[i * 3 + 1] = (g * a + ICON_BG_RGB[1] * (255 - a)) // 255
            out[i * 3 + 2] = (b * a + ICON_BG_RGB[2] * (255 - a)) // 255
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
    """状态栏 / 导航栏色跟随主题（两套资源 + 运行时切换）。

    ⚠️ 颜色必须与网页 **页面底色** 完全一致，不能与顶栏一致。
       用户反馈（v0.8.10）：「状态栏背景的颜色和状态栏下面的颜色不一样，有色差」。
       根因：当时用 --bar #131313（顶栏色），而页面底是 #1B1B1B，
       状态栏与页面之间出现一条明显色阶 —— 界面里最显眼的接缝。
       v0.10.2 又犯一次：页面底改成了 #1E1E1E，状态栏常量还停在 #1B1B1B。
       v0.10.4 第三次：只注入了一个固定深色值，App 内切到**日间主题**后
       页面变白了、状态栏还是夜间黑，顶部一条明显色阶。

    ✅ 现在同时写两套资源：
       · values/styles.xml       → 深色值（#1E1E1E）
       · values-night/styles.xml → 同一份（系统的深色模式兜底）
       真正的切换由 App 里的 StatusBar 插件在运行时做（见 main.js 的
       syncNativeBars），这样 App 内主题一改，状态栏立即跟上，
       不受系统深色模式影响。
    """
    p = Path("android/app/src/main/res/values/styles.xml")
    if not p.exists():
        print("  styles.xml not found, skip", file=sys.stderr)
        return

    # 读现有 style 名（通常只有一个 AppTheme.NoActionBar）
    s = p.read_text(encoding="utf-8")
    style_names = re.findall(r'<style name="([^"]+)"', s)

    def strip_bar_items(xml: str) -> str:
        for tag in ("android:statusBarColor", "android:navigationBarColor",
                    "android:windowLightStatusBar"):
            xml = re.sub(rf'\s*<item name="{re.escape(tag)}">[^<]*</item>', "", xml)
        return xml

    def inject(xml: str, color: str, light: bool) -> str:
        xml = strip_bar_items(xml)
        items = (
            f'\n        <item name="android:statusBarColor">{color}</item>'
            f'\n        <item name="android:navigationBarColor">{color}</item>'
            f'\n        <item name="android:windowLightStatusBar">'
            f'{"true" if light else "false"}</item>'
        )
        # 注入到每个 <style ...> 的结束标签前
        return xml.replace("</style>", items + "\n    </style>")

    # 1) values/styles.xml —— 深色（默认）
    light_xml = inject(s, STATUS_BAR_COLOR, light=False)
    p.write_text(light_xml, encoding="utf-8")
    print(f"  values/styles.xml: status/nav bar -> {STATUS_BAR_COLOR}, light icons")

    # 2) values-light/styles.xml —— 日间。用 -night（深色）以外的限定符：
    #    values-night 是"系统深色模式"时生效，而我们要的是"App 内选日间"。
    #    App 内切主题靠 StatusBar 插件实时改，这里只保证冷启动那一下不闪。
    #    所以再写一份 values-night，让系统深色模式下直接用深色值。
    night_dir = Path("android/app/src/main/res/values-night")
    night_dir.mkdir(parents=True, exist_ok=True)
    night_p = night_dir / "styles.xml"
    night_p.write_text(inject(s, STATUS_BAR_COLOR, light=False), encoding="utf-8")
    print(f"  values-night/styles.xml: status/nav bar -> {STATUS_BAR_COLOR} (system dark)")
    print(f"  styles: {', '.join(style_names) or '(none)'}")


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
            f'    <color name="ic_launcher_background">{ICON_BG_COLOR}</color>\n'
            '</resources>\n',
            encoding="utf-8")
        print(f"  values/ic_launcher_background.xml: {ICON_BG_COLOR}")
    else:
        print("  values/ic_launcher_background.xml not found, skip", file=sys.stderr)

    # 2) drawable/ic_launcher_background.xml（vector 填充）
    #    ⚠️ 不能只 re.sub 改 fillColor！capacitor-assets 的模板是**一张网格图**：
    #       33 条 pathData，除最底那块 `M0,0h108v108h-108z` 外全是
    #       `M0,19L108,19` / `M19,0L19,108` 这类**描边线**（只有 strokeColor）。
    #       改 fillColor 只把底色变深，网格线原样保留 —— 桌面上看到的就是
    #       「深色底 + 一堆浅色格子」，和小尺寸下的「没有背景色」几乎一样。
    #       所以这里**整文件覆写**成纯色矢量，绝不保留模板内容。
    p = Path("android/app/src/main/res/drawable/ic_launcher_background.xml")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!-- Generated by scripts/patch-android.py — do not edit.\n'
        '     纯色深底，与 Scholarius / Livolog 的图标底色一致。 -->\n'
        '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    android:width="108dp"\n'
        '    android:height="108dp"\n'
        '    android:viewportWidth="108"\n'
        '    android:viewportHeight="108">\n'
        '    <path\n'
        f'        android:fillColor="{ICON_BG_COLOR}"\n'
        '        android:pathData="M0,0h108v108h-108z" />\n'
        '</vector>\n',
        encoding="utf-8",
    )
    print(f"  drawable/ic_launcher_background.xml: 覆写为纯色 {ICON_BG_COLOR}")

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
    #
    #    ⚠️ 但 **foreground 必须指向 @mipmap**，不能指向 @drawable：
    #       我们无法在这里安全地造出 res/drawable/ic_launcher_foreground.xml ——
    #       实测手写一个 `<bitmap android:src="@mipmap/...">` 之后，
    #       aapt 在合并资源时会把 `res/drawable/` 下的这个文件丢掉
    #       （APK 里只剩 capacitor-assets 自己生成的 `res/drawable-v24/...`），
    #       于是 adaptive-icon 引用一个不存在的资源 -> **图标整个空白**。
    #       这是 v0.8.10~v0.8.14「桌面图标依旧是空的」的真因。
    #       capacitor-assets 已经把前景放在 mipmap-*/ic_launcher_foreground.png，
    #       直接引用它即可。
    adaptive = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@drawable/ic_launcher_background" />\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
        '    <monochrome android:drawable="@mipmap/ic_launcher_foreground" />\n'
        '</adaptive-icon>\n'
    )
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        p = Path("android/app/src/main/res/mipmap-anydpi-v26") / name
        if p.exists():
            p.write_text(adaptive, encoding="utf-8")
            print(f"  mipmap-anydpi-v26/{name}: no inset, background=@drawable, "
                  f"foreground=@mipmap")

    # 4) 删掉可能遗留的 drawable*/ic_launcher_foreground.xml
    #    ⚠️ 不要在这里造这个文件：aapt 合并资源时会把它丢掉（见上面第 3 段），
    #       而 adaptive-icon 一旦引用它就会变成空白图标。
    #    ⚠️ **必须连 drawable-v24 一起删**：capacitor-assets 每次都会重新生成
    #       `res/drawable-v24/ic_launcher_foreground.xml`，那里面是它的**默认模板
    #       图标**（一个几何折角图形），不是我们的 `>_`。API 24+ 解析
    #       `@drawable/ic_launcher_foreground` 时会优先选中 v24 版本，
    #       于是桌面上会出现一个和品牌完全无关的图形 —— 这正是
    #       「桌面图标依旧没有 / 图标不对」的根因。只删 drawable/ 是不够的。
    removed = []
    for res_dir in Path("android/app/src/main/res").glob("drawable*"):
        stale = res_dir / "ic_launcher_foreground.xml"
        if stale.exists():
            stale.unlink()
            removed.append(res_dir.name)
    if removed:
        print("  " + ", ".join(f"{d}/ic_launcher_foreground.xml" for d in removed)
              + ": 已删除（改用 @mipmap）")

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


def patch_cleartext() -> None:
    """允许 WebView 用明文 HTTP 访问 Tailscale IP。

    ⚠️ 这是「填了正确的 IP 和端口，测试连接仍然失败」的真正原因（v0.10.6）：
       Android 9（API 28）起**默认禁止明文 HTTP**。而 Termetron 的手机端
       通过 fetch 访问 `http://100.x.y.z:8899` —— 走系统网络栈，
       不是 WebView 内部的混合内容，所以 capacitor.config.ts 里的
       `allowMixedContent: true` 管不到它（那个只管 https 页面里加载
       http 资源时的渲染策略）。
       缺少 `android:usesCleartextTraffic="true"` 时，请求会被系统直接拒绝，
       Promise 抛错，界面上就显示「测试连接失败」。

    Tailscale 的 tailnet 本身是 WireGuard 加密的，明文 HTTP 只暴露在
    已加密的覆盖网络内，不经过公网 —— 这个取舍是安全的。
    """
    manifest = Path("android/app/src/main/AndroidManifest.xml")
    if not manifest.exists():
        print("  AndroidManifest not found, skip", file=sys.stderr)
        return
    s = manifest.read_text(encoding="utf-8")

    if 'android:usesCleartextTraffic="true"' in s:
        print("  AndroidManifest: usesCleartextTraffic already true, skip")
        return
    if "android:usesCleartextTraffic" in s:
        # 历史构建里可能被写成 false，强制改成 true
        s = re.sub(r'android:usesCleartextTraffic="[^"]*"',
                   'android:usesCleartextTraffic="true"', s)
        manifest.write_text(s, encoding="utf-8")
        print("  AndroidManifest: usesCleartextTraffic -> true")
        return

    # 注入到 <application 标签的属性里
    if "<application" not in s:
        print("  AndroidManifest: <application> not found", file=sys.stderr)
        sys.exit(1)
    s = s.replace("<application",
                  '<application\n        android:usesCleartextTraffic="true"', 1)
    manifest.write_text(s, encoding="utf-8")
    print("  AndroidManifest: usesCleartextTraffic=true (plain HTTP to Tailscale IP)")


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
    patch_cleartext()
    patch_apk_install()
    patch_splash()
    patch_splash_dep()
    patch_signing()
    print("done")
