#!/usr/bin/env python3
"""把 OutSystems Azure maven 源注入 android/build.gradle 的 allprojects.repositories。

@capacitor/barcode-scanner@1.x 的原生依赖 com.github.outsystems:osbarcode-android
只发布在 OutSystems 的 Azure DevOps 源上；但 Gradle 解析 :app 的 runtime classpath 时
只用根项目 allprojects.repositories（依赖模块自己的 repositories 不被采用），
所以必须在 `cap add android` 之后把该源注入到根 build.gradle。
"""
import sys
from pathlib import Path

FEED = ("https://pkgs.dev.azure.com/OutSystemsRD/"
        "9e79bc5b-69b2-4476-9ca5-d67594972a52/"
        "_packaging/PublicArtifactRepository/maven/v1")

p = Path("android/build.gradle")
s = p.read_text(encoding="utf-8")
if "OutSystemsRD" in s:
    print("azure repo already present, skip")
    sys.exit(0)

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
    print("ERROR: allprojects block not found in android/build.gradle", file=sys.stderr)
    sys.exit(1)
p.write_text(s.replace(old, new, 1), encoding="utf-8")
print("azure repo injected into allprojects.repositories")
