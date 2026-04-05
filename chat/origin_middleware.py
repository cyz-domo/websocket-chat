from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpResponse

from .models import SiteConfiguration


def _merge_unique(*groups):
    merged = []
    for group in groups:
        for item in group:
            if item and item not in merged:
                merged.append(item)
    return merged


class DynamicOriginSettingsMiddleware:
    """在请求进入 CSRF 中间件前，动态加载受信任来源配置。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        allowed_hosts = list(getattr(settings, 'DEFAULT_ALLOWED_HOSTS', []))
        trusted_origins = list(getattr(settings, 'DEFAULT_CSRF_TRUSTED_ORIGINS', []))
        cors_allowed_origins = list(getattr(settings, 'DEFAULT_CORS_ALLOWED_ORIGINS', []))
        allow_all_cors = False

        try:
            config = SiteConfiguration.get_solo()
        except (OperationalError, ProgrammingError):
            config = None

        if config:
            allowed_hosts = _merge_unique(
                allowed_hosts,
                SiteConfiguration.parse_origin_lines(config.allowed_hosts),
            )
            trusted_origins = _merge_unique(
                trusted_origins,
                SiteConfiguration.parse_origin_lines(config.trusted_origins),
            )
            cors_allowed_origins = _merge_unique(
                cors_allowed_origins,
                SiteConfiguration.parse_origin_lines(config.cors_allowed_origins),
            )
            allow_all_cors = config.allow_all_cors

        settings.ALLOWED_HOSTS = allowed_hosts
        settings.CSRF_TRUSTED_ORIGINS = trusted_origins
        request._dynamic_cors_allowed_origins = cors_allowed_origins
        request._dynamic_allow_all_cors = allow_all_cors

        return self.get_response(request)


class DynamicCorsMiddleware:
    """简单 CORS 处理中间件，支持后台动态配置。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        origin = request.headers.get('Origin', '')
        allowed_origins = getattr(request, '_dynamic_cors_allowed_origins', [])
        allow_all_cors = getattr(request, '_dynamic_allow_all_cors', False)
        origin_allowed = bool(origin and (allow_all_cors or origin in allowed_origins))

        if request.method == 'OPTIONS' and origin_allowed:
            response = HttpResponse(status=200)
        else:
            response = self.get_response(request)

        if origin_allowed:
            response['Access-Control-Allow-Origin'] = origin
            response['Access-Control-Allow-Credentials'] = 'true'
            response['Access-Control-Allow-Headers'] = request.headers.get(
                'Access-Control-Request-Headers',
                'Content-Type, X-CSRFToken, X-Requested-With',
            )
            response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
            response['Vary'] = 'Origin'

        return response
