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
    if (!PushNotifications) {
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
                console.warn('Failed to get device info', error);
            }
        }

        try {
            const response = await fetchJson(REGISTER_PATH, {
                token: token,
                platform: capacitor.getPlatform(),
                device_id: deviceInfo && deviceInfo.identifier ? deviceInfo.identifier : '',
                device_name: buildDeviceName(deviceInfo),
                app_version: deviceInfo && deviceInfo.appVersion ? deviceInfo.appVersion : '',
            });
            if (!response.ok) {
                console.warn('Push token registration failed with status', response.status);
            }
        } catch (error) {
            console.warn('Push token registration request failed', error);
        }
    }

    async function unregisterDevice(token) {
        if (!token || !isAuthenticatedPage()) {
            return;
        }

        try {
            await fetchJson(UNREGISTER_PATH, { token: token }, true);
        } catch (error) {
            console.warn('Push token unregister request failed', error);
        }
    }

    function saveToken(token) {
        try {
            window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
        } catch (error) {
            console.warn('Failed to cache push token', error);
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
            console.warn('Unable to determine Firebase status', error);
            return { firebaseConfigured: false };
        }
    }

    async function setupPushNotifications() {
        const cachedToken = loadCachedToken();
        if (cachedToken && isAuthenticatedPage()) {
            registerDevice(cachedToken);
        }

        PushNotifications.addListener('registration', function (tokenResult) {
            const token = tokenResult && tokenResult.value ? tokenResult.value : '';
            if (!token) {
                return;
            }
            saveToken(token);
            registerDevice(token);
        });

        PushNotifications.addListener('registrationError', function (error) {
            console.warn('Push registration error', error);
        });

        PushNotifications.addListener('pushNotificationActionPerformed', function (event) {
            const data = event && event.notification ? event.notification.data : null;
            navigateFromPush(data);
        });

        try {
            const pushSupportStatus = await getPushSupportStatus();
            if (!pushSupportStatus || pushSupportStatus.firebaseConfigured !== true) {
                console.warn('Firebase is not configured for native push notifications on this build.');
                return;
            }

            if (capacitor.getPlatform() === 'android' && typeof PushNotifications.createChannel === 'function') {
                await PushNotifications.createChannel(DEFAULT_CHANNEL);
            }

            const permissionResult = await PushNotifications.requestPermissions();
            if (permissionResult && permissionResult.receive === 'granted') {
                await PushNotifications.register();
            }
        } catch (error) {
            console.warn('Push permission request failed', error);
        }
    }

    attachLogoutHandler();
    setupPushNotifications();
})();
