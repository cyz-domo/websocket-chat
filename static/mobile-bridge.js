(function () {
    const capacitor = window.Capacitor;
    if (!capacitor || typeof capacitor.getPlatform !== 'function') {
        return;
    }

    const isNative = typeof capacitor.isNativePlatform === 'function'
        ? capacitor.isNativePlatform()
        : ['android', 'ios'].includes(capacitor.getPlatform());
    if (!isNative) {
        return;
    }

    const plugins = capacitor.Plugins || {};
    const PushNotifications = plugins.PushNotifications;
    const PushSupport = plugins.PushSupport;
    const Device = plugins.Device;
    const LocalNotifications = plugins.LocalNotifications;
    if (!PushNotifications) {
        console.warn('[mobile-bridge] PushNotifications plugin is unavailable');
        return;
    }

    const TOKEN_STORAGE_KEY = 'animal_chat_push_token';
    const REGISTER_PATH = '/chat/mobile/devices/register/';
    const UNREGISTER_PATH = '/chat/mobile/devices/unregister/';
    const AUTH_FREE_PATHS = new Set(['/chat/login/', '/chat/register/']);
    const DEFAULT_CHANNEL = {
        id: 'chat_messages',
        name: 'Chat messages',
        description: 'Animal Chat message notifications',
        importance: 5,
        visibility: 1,
        sound: 'default',
    };
    const FOREGROUND_NOTIFICATION_CHANNEL = {
        id: 'chat_messages_foreground',
        name: 'Chat messages (foreground)',
        description: 'Animal Chat foreground notifications',
        importance: 5,
        visibility: 1,
        sound: 'default',
    };

    function logInfo(message, details) {
        if (details === undefined) {
            console.log('[mobile-bridge]', message);
            return;
        }
        console.log('[mobile-bridge]', message, details);
    }

    function logWarn(message, details) {
        if (details === undefined) {
            console.warn('[mobile-bridge]', message);
            return;
        }
        console.warn('[mobile-bridge]', message, details);
    }

    function getCsrfToken() {
        const matches = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
        return matches ? decodeURIComponent(matches[1]) : '';
    }

    function isAuthenticatedPage() {
        return !AUTH_FREE_PATHS.has(window.location.pathname);
    }

    function buildDeviceName(info) {
        const parts = [];
        if (info && info.manufacturer) {
            parts.push(info.manufacturer);
        }
        if (info && info.model) {
            parts.push(info.model);
        }
        if (!parts.length) {
            parts.push(navigator.userAgent.slice(0, 120));
        }
        return parts.join(' ').trim().slice(0, 120);
    }

    function fetchJson(url, payload, useKeepalive) {
        const headers = {
            'Content-Type': 'application/json',
        };
        const csrfToken = getCsrfToken();
        if (csrfToken) {
            headers['X-CSRFToken'] = csrfToken;
        }
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: headers,
            body: JSON.stringify(payload),
            keepalive: !!useKeepalive,
        });
    }

    async function registerDevice(token) {
        if (!token || !isAuthenticatedPage()) {
            return;
        }

        let deviceInfo = null;
        if (Device && typeof Device.getInfo === 'function') {
            try {
                deviceInfo = await Device.getInfo();
            } catch (error) {
                logWarn('Failed to get device info', error);
            }
        }

        try {
            logInfo('Registering mobile device token with backend');
            const response = await fetchJson(REGISTER_PATH, {
                token: token,
                platform: capacitor.getPlatform(),
                device_id: deviceInfo && deviceInfo.identifier ? deviceInfo.identifier : '',
                device_name: buildDeviceName(deviceInfo),
                app_version: deviceInfo && deviceInfo.appVersion ? deviceInfo.appVersion : '',
            });
            const contentType = response.headers.get('content-type') || '';
            let payload = null;
            if (contentType.includes('application/json')) {
                payload = await response.json();
            } else {
                const bodyText = await response.text();
                logWarn('Push token registration returned a non-JSON response', {
                    status: response.status,
                    redirected: response.redirected,
                    bodyPreview: bodyText.slice(0, 160),
                });
                return;
            }

            if (!response.ok || !payload || payload.ok !== true) {
                logWarn('Push token registration failed', {
                    status: response.status,
                    redirected: response.redirected,
                    payload: payload,
                });
                return;
            }
            logInfo('Push token registered successfully', payload);
        } catch (error) {
            logWarn('Push token registration request failed', error);
        }
    }

    async function unregisterDevice(token) {
        if (!token || !isAuthenticatedPage()) {
            return;
        }

        try {
            await fetchJson(UNREGISTER_PATH, { token: token }, true);
        } catch (error) {
            logWarn('Push token unregister request failed', error);
        }
    }

    function saveToken(token) {
        try {
            window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
        } catch (error) {
            logWarn('Failed to cache push token', error);
        }
    }

    function loadCachedToken() {
        try {
            return window.localStorage.getItem(TOKEN_STORAGE_KEY) || '';
        } catch (error) {
            return '';
        }
    }

    function navigateFromPush(data) {
        if (!data || typeof data !== 'object') {
            return;
        }

        if (data.kind === 'direct' && data.public_id) {
            window.location.href = '/chat/dm/id/' + encodeURIComponent(data.public_id) + '/';
            return;
        }

        if (data.kind === 'room' && data.room_name) {
            window.location.href = '/chat/' + encodeURIComponent(data.room_name) + '/';
        }
    }

    function attachLogoutHandler() {
        document.addEventListener('click', function (event) {
            const link = event.target.closest('a[href]');
            if (!link) {
                return;
            }

            let targetUrl;
            try {
                targetUrl = new URL(link.href, window.location.origin);
            } catch (error) {
                return;
            }

            if (targetUrl.pathname !== '/chat/logout/') {
                return;
            }

            const token = loadCachedToken();
            if (token) {
                unregisterDevice(token);
            }
        });
    }

    async function getPushSupportStatus() {
        if (!capacitor || capacitor.getPlatform() !== 'android' || !PushSupport || typeof PushSupport.getStatus !== 'function') {
            return { firebaseConfigured: true };
        }

        try {
            return await PushSupport.getStatus();
        } catch (error) {
            logWarn('Unable to determine Firebase status', error);
            return { firebaseConfigured: false };
        }
    }

    async function ensureLocalNotificationSupport() {
        if (!LocalNotifications) {
            logWarn('LocalNotifications plugin is unavailable');
            return;
        }

        try {
            const permissionResult = await LocalNotifications.requestPermissions();
            logInfo('Local notification permission result', permissionResult);
        } catch (error) {
            logWarn('Local notification permission request failed', error);
        }

        if (capacitor.getPlatform() === 'android' && typeof LocalNotifications.createChannel === 'function') {
            try {
                await LocalNotifications.createChannel(FOREGROUND_NOTIFICATION_CHANNEL);
            } catch (error) {
                logWarn('Failed to create foreground local notification channel', error);
            }
        }
    }

    function getNotificationId(notification) {
        const source = notification && notification.id ? String(notification.id) : String(Date.now());
        let hash = 0;
        for (let index = 0; index < source.length; index += 1) {
            hash = ((hash << 5) - hash) + source.charCodeAt(index);
            hash |= 0;
        }
        return Math.abs(hash) || Date.now();
    }

    async function showForegroundNotification(notification) {
        if (!LocalNotifications || typeof LocalNotifications.schedule !== 'function') {
            return;
        }

        try {
            await LocalNotifications.schedule({
                notifications: [{
                    id: getNotificationId(notification),
                    title: (notification && notification.title) || 'Animal Chat',
                    body: (notification && notification.body) || '你收到一条新消息',
                    extra: notification && notification.data ? notification.data : {},
                    channelId: FOREGROUND_NOTIFICATION_CHANNEL.id,
                    smallIcon: 'ic_stat_chat',
                }],
            });
            logInfo('Displayed foreground local notification');
        } catch (error) {
            logWarn('Failed to display foreground local notification', error);
        }
    }

    async function setupPushNotifications() {
        logInfo('Initializing push notifications', {
            platform: capacitor.getPlatform(),
            path: window.location.pathname,
        });

        const cachedToken = loadCachedToken();
        if (cachedToken && isAuthenticatedPage()) {
            logInfo('Found cached push token, syncing with backend');
            registerDevice(cachedToken);
        }

        PushNotifications.addListener('registration', function (tokenResult) {
            const token = tokenResult && tokenResult.value ? tokenResult.value : '';
            if (!token) {
                logWarn('Push registration returned an empty token');
                return;
            }
            logInfo('Received push registration token', token);
            saveToken(token);
            registerDevice(token);
        });

        PushNotifications.addListener('registrationError', function (error) {
            logWarn('Push registration error', error);
        });

        PushNotifications.addListener('pushNotificationReceived', function (notification) {
            logInfo('Push notification received', notification);
            if (document.visibilityState === 'visible') {
                showForegroundNotification(notification);
            }
        });

        PushNotifications.addListener('pushNotificationActionPerformed', function (event) {
            const data = event && event.notification ? event.notification.data : null;
            logInfo('Push notification action performed', data);
            navigateFromPush(data);
        });

        try {
            const pushSupportStatus = await getPushSupportStatus();
            if (!pushSupportStatus || pushSupportStatus.firebaseConfigured !== true) {
                logWarn('Firebase is not configured for native push notifications on this build.', pushSupportStatus);
                return;
            }

            if (capacitor.getPlatform() === 'android' && typeof PushNotifications.createChannel === 'function') {
                await PushNotifications.createChannel(DEFAULT_CHANNEL);
            }

            await ensureLocalNotificationSupport();
            const permissionResult = await PushNotifications.requestPermissions();
            logInfo('Push permission result', permissionResult);
            if (permissionResult && permissionResult.receive === 'granted') {
                await PushNotifications.register();
                logInfo('Triggered native push registration');
            } else {
                logWarn('Push permission was not granted', permissionResult);
            }
        } catch (error) {
            logWarn('Push permission request failed', error);
        }
    }

    attachLogoutHandler();
    setupPushNotifications();
})();
