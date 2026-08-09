#!/usr/bin/env python3
"""CI 中 cap add android 之后统一修补 android/ 原生工程。

1) 注入 OutSystems Azure maven 源：@capacitor/barcode-scanner@1.x 的原生依赖
   com.github.outsystems:osbarcode-android 只发布在该源上；Gradle 解析 :app 的
   runtime classpath 只查根项目 allprojects.repositories（依赖模块自己的 repos
   不被采用），必须注入到根 build.gradle。
2) minSdkVersion 22 -> 26：osbarcode-android:1.1.x 要求 minSdk >= 26。
"""
import sys
from pathlib import Path

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


if __name__ == "__main__":
    print("patching android/ ...")
    patch_build_gradle()
    patch_variables_gradle()
    print("done")
