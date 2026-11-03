import os
import sys
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Секреты — в файле .env рядом с manage.py (он в .gitignore); переменные окружения важнее файла
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _key, _sep, _value = _line.strip().partition("=")
        if _sep and not _key.startswith("#"):
            os.environ.setdefault(_key.strip(), _value.strip().strip("\"'"))

# ИИ-ассистент (Groq)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# Локально всё работает как есть; на сервере (Docker) значения приходят из .env — см. .env.example
def _env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-change-me-in-production")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", "*")
# вход в админку Django по https через домен
CSRF_TRUSTED_ORIGINS = _env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
# за nginx: https определяется по заголовку, который ставит nginx
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

INSTALLED_APPS = [
    "daphne",  # первым: runserver работает через ASGI и понимает WebSocket (живой чат)
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "accounts",
    "pharmacies",
    "orders",
    "purchases",
    "assistant",
    "reports",
    "writeoffs",
    "rates",
    "channels",
    "chat",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "frontend" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# WebSocket-чат: REDIS_URL в .env — Redis (нужен, если процессов/серверов несколько); без него — память одного процесса
CHANNEL_LAYERS = (
    {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer", "CONFIG": {"hosts": [os.environ["REDIS_URL"]]}}}
    if os.environ.get("REDIS_URL") and "test" not in sys.argv[1:2]  # тесты не зависят от запущенного Redis
    else {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
)

# SQLite для простого старта. Для PostgreSQL поменяйте только этот блок.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_DB_PATH", BASE_DIR / "db.sqlite3"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "ru"
# Язык ответа API берётся из Accept-Language (фронтенд отправляет выбранный язык)
LANGUAGES = [("ru", "Русский"), ("tg", "Тоҷикӣ"), ("en", "English")]
TIME_ZONE = "Asia/Dushanbe"
USE_I18N = True
USE_TZ = True

# в Docker статика Django (админка) — /django-static/, чтобы не пересекаться со /static/ фронтенда
STATIC_URL = os.environ.get("DJANGO_STATIC_URL", "static/")
STATICFILES_DIRS = [BASE_DIR / "frontend" / "static"]
STATIC_ROOT = os.environ.get("DJANGO_STATIC_ROOT", BASE_DIR / "staticfiles")  # collectstatic — для nginx

# загруженные файлы (фото и видео в чате аптеки, аватарки); на сервере их отдаёт nginx
MEDIA_URL = "media/"
MEDIA_ROOT = os.environ.get("DJANGO_MEDIA_ROOT", BASE_DIR / "media")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Наш пользователь с ролями
AUTH_USER_MODEL = "accounts.User"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Avicenna API",
    "DESCRIPTION": "API для сети аптек: аптеки, лекарства, поставщики, закупки, продажи.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # отдельные схемы для запроса и ответа (read_only поля не мешают в формах)
    "COMPONENT_SPLIT_REQUEST": True,
    "SWAGGER_UI_SETTINGS": {
        "persistAuthorization": True,  # токен не сбрасывается после перезагрузки
        "displayRequestDuration": True,
        "filter": True,
    },
    "TAGS": [
        {"name": "auth", "description": "Логин, обновление токена, текущий пользователь"},
        {"name": "pharmacies", "description": "Аптеки и статистика"},
        {"name": "users", "description": "Сотрудники"},
        {"name": "medicines", "description": "Лекарства"},
        {"name": "suppliers", "description": "Поставщики"},
        {"name": "purchases", "description": "Закупки (приход товара)"},
        {"name": "orders", "description": "Продажи"},
        {"name": "assistant", "description": "ИИ-ассистент (Groq)"},
        {"name": "reports", "description": "Отчёты о продажах и закупках: JSON, Excel, Word, PDF"},
        {"name": "writeoffs", "description": "Списание просроченных лекарств"},
        {"name": "rates", "description": "Курсы валют (Нацбанк и банки Таджикистана)"},
    ],
}
