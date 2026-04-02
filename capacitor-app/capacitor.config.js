const defaultServerUrl = 'https://chat.6143443.xyz/chat/login/';
const serverUrl = (process.env.CAP_SERVER_URL || defaultServerUrl).trim();
const allowNavigation = [];

if (serverUrl) {
    try {
        const parsed = new URL(serverUrl);
        if (parsed.hostname) {
            allowNavigation.push(parsed.hostname);
        }
    } catch (error) {
        console.warn('Invalid CAP_SERVER_URL:', serverUrl);
    }
}

module.exports = {
    appId: 'com.animalchat.mobile',
    appName: 'Animal Chat',
    webDir: 'web',
    bundledWebRuntime: false,
    server: serverUrl
        ? {
            url: serverUrl,
            cleartext: serverUrl.startsWith('http://'),
            allowNavigation: allowNavigation,
        }
        : undefined,
};
