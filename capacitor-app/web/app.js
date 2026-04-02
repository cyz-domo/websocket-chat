window.addEventListener('DOMContentLoaded', function () {
    if (window.Capacitor && typeof window.Capacitor.getPlatform === 'function') {
        document.body.dataset.platform = window.Capacitor.getPlatform();
    }
});
