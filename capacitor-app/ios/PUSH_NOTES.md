# iOS Push Setup Notes

1. Open `capacitor-app/ios/App/App.xcodeproj` in Xcode.
2. Select the `App` target.
3. Enable:
   - `Push Notifications`
   - `Background Modes`
   - `Remote notifications`
4. Configure APNs in Apple Developer.
5. Link the same APNs credentials inside your Firebase project.
6. Build and run on a real iPhone for push verification.

The Django backend already exposes token registration endpoints and can send FCM notifications once Firebase credentials are configured server-side.
