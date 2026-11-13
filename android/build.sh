#!/usr/bin/env bash
# Builds the Android app with the SDK's own tools, no Gradle: it is plain Java without libraries.
#
#   android/build.sh            builds android/build/neomixer.apk
#   android/build.sh install    also installs it on the phone adb is connected to
#
# Needs a JDK (17 or newer) and an Android SDK with a platform and build-tools, in $ANDROID_HOME
# or ~/Android/Sdk:
#   sdkmanager "platforms;android-36" "build-tools;36.0.0"
#
# The APK is signed with the key in $NEOMIXER_KEYSTORE (password in $NEOMIXER_KEYSTORE_PASS),
# by default one made on the first build in ~/.config/neomixer. Android only installs an update
# over an app signed with the same key, so keep it.
set -euo pipefail
cd "$(dirname "$0")"

SDK=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}
BUILD_TOOLS=$(ls -d "$SDK"/build-tools/* 2>/dev/null | sort -V | tail -1)
PLATFORM=$(ls -d "$SDK"/platforms/android-* 2>/dev/null | sort -V | tail -1)
if [ -z "$BUILD_TOOLS" ] || [ -z "$PLATFORM" ]; then
    echo "No Android SDK with build-tools and a platform in $SDK" >&2
    exit 1
fi
JAR=$PLATFORM/android.jar

MIN_SDK=26
TARGET_SDK=${PLATFORM##*-}
VERSION=$(sed -n "s/^VERSION = '\(.*\)'/\1/p" ../src/neomixer/settings.py)
# 1.2.3 -> 10203
VERSION_CODE=$(echo "$VERSION" | awk -F. '{ print $1 * 10000 + $2 * 100 + $3 }')

KEYSTORE=${NEOMIXER_KEYSTORE:-${XDG_CONFIG_HOME:-$HOME/.config}/neomixer/android.keystore}
KEYSTORE_PASS=${NEOMIXER_KEYSTORE_PASS:-neomixer}
if [ ! -f "$KEYSTORE" ]; then
    mkdir -p "$(dirname "$KEYSTORE")"
    keytool -genkeypair -keystore "$KEYSTORE" -storepass "$KEYSTORE_PASS" -keypass "$KEYSTORE_PASS" \
        -alias neomixer -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=NeoMixer" >/dev/null 2>&1
    echo "Made a signing key: $KEYSTORE"
fi

rm -rf build
mkdir -p build/gen build/classes build/dex

"$BUILD_TOOLS/aapt2" compile --dir res -o build/res.zip
"$BUILD_TOOLS/aapt2" link -o build/unaligned.apk -I "$JAR" --manifest AndroidManifest.xml --java build/gen \
    --min-sdk-version $MIN_SDK --target-sdk-version "$TARGET_SDK" \
    --version-code "$VERSION_CODE" --version-name "$VERSION" build/res.zip

javac --release 17 -Xlint:all,-options -Werror -classpath "$JAR" -d build/classes \
    $(find src build/gen -name '*.java')

"$BUILD_TOOLS/d8" --release --lib "$JAR" --min-api $MIN_SDK --output build/dex $(find build/classes -name '*.class')
(cd build/dex && zip -q ../unaligned.apk classes.dex)

"$BUILD_TOOLS/zipalign" -f -p 4 build/unaligned.apk build/aligned.apk
"$BUILD_TOOLS/apksigner" sign --ks "$KEYSTORE" --ks-pass "pass:$KEYSTORE_PASS" --ks-key-alias neomixer \
    --out build/neomixer.apk build/aligned.apk
rm -f build/unaligned.apk build/aligned.apk build/neomixer.apk.idsig

echo "Built android/build/neomixer.apk ($VERSION)"

if [ "${1:-}" = install ]; then
    adb ${ADB_SERIAL:+-s "$ADB_SERIAL"} install -r build/neomixer.apk
fi
