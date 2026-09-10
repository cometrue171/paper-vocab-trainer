# Science English — Android shell

A tiny [Capacitor](https://capacitorjs.com) WebView shell. **It ships without any server
address**: on first launch it asks for the URL of *your own* Science English instance
(local `http://<lan-ip>:5010` or your self-hosted `https://…/english/`) and remembers it.

## Build your own APK

```bash
cd mobile
npm install
npx cap add android          # scaffold the native project (first time only)
npx cap sync android
cd android && ./gradlew assembleRelease
# → app/build/outputs/apk/release/app-release.apk
```

Signing: create your own keystore and point `android/app/keystore/keystore.properties`
at it (see the `signingConfigs` block in `android/app/build.gradle`). **Never commit
keystores or `keystore.properties`.**

Requirements: JDK 21, Android SDK (platform 36, build-tools 36), Node 18+.
