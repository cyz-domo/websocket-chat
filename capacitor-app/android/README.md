# Android Build Notes

## 1. Firebase file

Put the real Firebase config file here:

```text
capacitor-app/android/app/google-services.json
```

The repo only includes:

```text
capacitor-app/android/app/google-services.json.example
```

## 2. Sync the project

For production domain:

```bash
cd capacitor-app
npm run sync:prod
```

For local Android emulator:

```bash
cd capacitor-app
npm run sync:local-android
```

## 3. Open Android Studio

```bash
cd capacitor-app
npm run open:android
```

Then in Android Studio:

1. Wait for Gradle sync to finish
2. Confirm `google-services.json` is detected
3. Build `debug` first
4. Then generate `apk` or `aab`

## 4. Notification behavior

This project already includes:

- `POST_NOTIFICATIONS` permission
- default notification channel id: `chat_messages`
- default notification icon metadata
- runtime push permission request through `static/mobile-bridge.js`

## 5. If push does not arrive

Check these in order:

1. The device token is registered in Django admin under mobile devices
2. Django server has `FIREBASE_CREDENTIALS_FILE` configured
3. `google-services.json` belongs to package `com.animalchat.mobile`
4. The app was installed from the latest Android Studio build
