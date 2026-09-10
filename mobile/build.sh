#!/usr/bin/env bash
# Build the Science English Android shell (no server address baked in).
set -e
export JAVA_HOME="${JAVA_HOME:-$HOME/AndroidBuild/jdk21/Contents/Home}"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/AndroidBuild/android-sdk}"
cd "$(dirname "$0")"
[ -d node_modules ] || npm install
npx cap sync android
cd android
./gradlew --no-daemon assembleRelease --console=plain
echo "APK: $(pwd)/app/build/outputs/apk/release/app-release.apk"
