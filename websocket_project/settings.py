# websocket_project/settings.py
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def get_env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def get_database_settings():
    backend = os.getenv('DB_BACKEND', 'sqlite').strip().lower()
    if backend in {'postgres', 'postgresql', 'pg'}:
        database_config = {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('DB_NAME', 'websocket_chat'),
            'USER': os.getenv('DB_USER', 'postgres'),
            'PASSWORD': os.getenv('DB_PASSWORD', ''),
            'HOST': os.getenv('DB_HOST', '127.0.0.1'),
            'PORT': os.getenv('DB_PORT', '5432'),
        }
        sslmode = os.getenv('DB_SSLMODE', '').strip()
        if sslmode:
            database_config['OPTIONS'] = {
                'sslmode': sslmode,
            }
        return {
            'default': database_config,
        }

    sqlite_name = os.getenv('SQLITE_PATH', '').strip()
    return {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': Path(sqlite_name) if sqlite_name else BASE_DIR / 'db.sqlite3',
        }
    }


def get_channel_layer_settings():
    redis_url = os.getenv('REDIS_URL', '').strip()
    if redis_url:
        return {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [redis_url],
            },
        }

    return {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    }

# 安全密钥（生产环境要修改）
SECRET_KEY = 'django-insecure-your-secret-key-here'

DEBUG = False
ALLOWED_HOSTS = ['*', 'localhost', '127.0.0.1', '.ngrok-free.app', '.ngrok.io', 'chat.6143443.xyz']
GEOCODE_PROVIDER = os.getenv('GEOCODE_PROVIDER', 'auto')
GEOCODE_TIMEOUT = float(os.getenv('GEOCODE_TIMEOUT', '8'))
AMAP_WEB_API_KEY = os.getenv('AMAP_WEB_API_KEY', '')
REVERSE_GEOCODE_URL = os.getenv('REVERSE_GEOCODE_URL', 'https://nominatim.openstreetmap.org/reverse')
BIGDATA_REVERSE_URL = os.getenv('BIGDATA_REVERSE_URL', 'https://api.bigdatacloud.net/data/reverse-geocode-client')
GEOCODE_USER_AGENT = os.getenv('GEOCODE_USER_AGENT', 'websocket-chat/1.0 (location reverse geocoding)')
REDIS_URL = os.getenv('REDIS_URL', '').strip()

DEFAULT_CSRF_TRUSTED_ORIGINS = [
    'https://*.ngrok-free.app',
    'https://*.ngrok.io',
    'http://*.ngrok-free.app',
    'http://*.ngrok.io',
    'https://www.dongwu.eu.cc',
    'https://chat.6143443.xyz',
]
CSRF_TRUSTED_ORIGINS = list(DEFAULT_CSRF_TRUSTED_ORIGINS)

# 如果需要处理跨域
CORS_ALLOW_ALL_ORIGINS = False
DEFAULT_CORS_ALLOWED_ORIGINS = [
    'https://chat.6143443.xyz',
]
# 添加应用
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',  # 添加channels
    'chat',      # 我们的聊天应用
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'chat.origin_middleware.DynamicOriginSettingsMiddleware',
    'chat.origin_middleware.DynamicCorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'chat.middleware.CheckUserSessionMiddleware',
]

ROOT_URLCONF = 'websocket_project.urls'
LOGIN_URL = '/chat/login/'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'chat.context_processors.site_branding',
            ],
        },
    },
]

WSGI_APPLICATION = 'websocket_project.wsgi.application'
# 添加ASGI配置（用于WebSocket）
ASGI_APPLICATION = 'websocket_project.asgi.application'

# 数据库配置
DATABASES = get_database_settings()

# 密码验证
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# 国际化
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

# 静态文件
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
CHAT_ATTACHMENT_MAX_BYTES = int(os.getenv('CHAT_ATTACHMENT_MAX_BYTES', str(50 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv('FILE_UPLOAD_MAX_MEMORY_SIZE', str(CHAT_ATTACHMENT_MAX_BYTES)))
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv('DATA_UPLOAD_MAX_MEMORY_SIZE', str(CHAT_ATTACHMENT_MAX_BYTES)))

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CSRF配置
CSRF_COOKIE_SECURE = False  # 开发环境设为False，生产环境设为True
CSRF_COOKIE_HTTPONLY = False
CSRF_USE_SESSIONS = False
CSRF_COOKIE_AGE = 60 * 60 * 24 * 7  # 7天
CSRF_COOKIE_DOMAIN = None
CSRF_COOKIE_PATH = '/'
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_SAMESITE = 'Lax'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

# Channels配置
CHANNEL_LAYERS = {
    'default': get_channel_layer_settings()
}
