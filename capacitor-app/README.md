# Capacitor App Shell

这个目录用于把现有 Django 聊天站点封装成 iOS / Android App。

## 设计思路

- Django 站点继续提供实际聊天页面
- Capacitor 负责原生容器、打包、推送、相机和文件能力
- 默认站点地址是 `https://chat.6143443.xyz/chat/login/`
- `CAP_SERVER_URL` 可以临时覆盖 App 启动时加载的聊天地址

## 常用命令

```bash
cd capacitor-app
npm install
npm run sync:prod
npm run add:android
npm run add:ios
```

本地联调时：

- Android 模拟器通常用 `http://10.0.2.2:8000/chat/login/`
- 可直接执行 `npm run sync:local-android`
- 真机建议直接用已部署的 HTTPS 域名
- iOS 模拟器本地联调可执行 `npm run sync:local-ios`

## 当前配套后端能力

仓库根目录 Django 后端已经提供：

- 设备 token 注册接口：`/chat/mobile/devices/register/`
- 设备 token 注销接口：`/chat/mobile/devices/unregister/`
- FCM 推送服务骨架
- Redis channel layer 配置

## 说明

`static/mobile-bridge.js` 会在 Django 页面中自动注入。
当页面运行在 Capacitor 原生容器里时，它会尝试：

- 请求通知权限
- 注册原生 push token
- 把 token 回传到 Django
- 点击通知时跳转到对应聊天页

## Firebase / 推送落地

Android：

- 把 Firebase 控制台下载的 `google-services.json` 放到 `capacitor-app/android/app/`
- 然后执行 `npm run sync:prod`
- 再用 `npm run open:android` 打开 Android Studio 构建 `apk` / `aab`
- 详细说明见 `capacitor-app/android/README.md`

iOS：

- 先在 Apple Developer 和 Firebase 里配置 APNs
- 再用 `npm run open:ios` 打开 Xcode
- 在 Xcode 中开启 Push Notifications 和 Background Modes

目前仓库已经具备后端 token 注册和 FCM 推送发送骨架，但 Firebase 项目本身的密钥文件还需要你放进去。
