"""Настройки корпоративного портала «ЭКРОСХИМ».

Все секреты и параметры интеграции с Active Directory вынесены в переменные
окружения (файл .env), чтобы один и тот же артефакт разворачивался в разных
контурах без правки кода.
"""
from __future__ import annotations

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .env import env, env_bool, env_int, env_list, load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Базовые параметры
# ---------------------------------------------------------------------------
DEBUG = env_bool("DJANGO_DEBUG", True)

SECRET_KEY = env("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-secret-key-ecros-portal-change-me"
    else:
        raise ImproperlyConfigured(
            "Задайте DJANGO_SECRET_KEY — в боевом режиме без него работать нельзя."
        )

ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    default=("localhost", "127.0.0.1", "[::1]") if DEBUG else (),
)
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

PORTAL_SITE_NAME = env("PORTAL_SITE_NAME", "Портал ЭКРОСХИМ")
PORTAL_COMPANY_NAME = env("PORTAL_COMPANY_NAME", "ЭКРОСХИМ")
PORTAL_SUPPORT_EMAIL = env("PORTAL_SUPPORT_EMAIL", "portal@example.local")

# ---------------------------------------------------------------------------
# Приложения
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.accounts",
    "apps.vacations",
    "apps.portal",
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
    "apps.accounts.middleware.RequireLoginMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.portal.context_processors.portal_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# База данных
# ---------------------------------------------------------------------------
if env("DJANGO_DB_ENGINE", "sqlite").lower() in {"postgres", "postgresql", "pg"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DJANGO_DB_NAME", "ecros_portal"),
            "USER": env("DJANGO_DB_USER", "ecros"),
            "PASSWORD": env("DJANGO_DB_PASSWORD", ""),
            "HOST": env("DJANGO_DB_HOST", "127.0.0.1"),
            "PORT": env("DJANGO_DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
            "OPTIONS": {"connect_timeout": 5},
        }
    }
else:
    (BASE_DIR / "var").mkdir(exist_ok=True)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": env("DJANGO_DB_PATH", str(BASE_DIR / "var" / "db.sqlite3")),
            "OPTIONS": {"timeout": 20},
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Аутентификация и роли
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.Employee"

AUTHENTICATION_BACKENDS = [
    # 1. Пароль проверяется напрямую в Active Directory (SSO-подобный вход).
    "apps.accounts.auth.ActiveDirectoryBackend",
    # 2. Резерв: локальный пароль — только для служебных и аварийных учёток.
    "apps.accounts.auth.LocalPasswordBackend",
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "portal:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SESSION_COOKIE_AGE = env_int("DJANGO_SESSION_COOKIE_AGE", 60 * 60 * 10)
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# Защита от перебора паролей при входе
LOGIN_MAX_ATTEMPTS = env_int("PORTAL_LOGIN_MAX_ATTEMPTS", 8)
LOGIN_LOCKOUT_SECONDS = env_int("PORTAL_LOGIN_LOCKOUT_SECONDS", 900)

# Аварийный вход по локальному паролю (если AD недоступен)
ALLOW_LOCAL_PASSWORD_LOGIN = env_bool("PORTAL_ALLOW_LOCAL_PASSWORD", True)

# ---------------------------------------------------------------------------
# Active Directory (on-prem, LDAP/LDAPS)
# ---------------------------------------------------------------------------
AD_ENABLED = env_bool("AD_ENABLED", True)
# Если контроллер домена не задан, по умолчанию используем демонстрационный каталог.
AD_PROVIDER = env("AD_PROVIDER", "ldap" if env("AD_SERVER") else "mock").lower()  # ldap | mock
AD_USE_SSL = env_bool("AD_USE_SSL", False)
AD_PORT = env_int("AD_PORT", 636 if AD_USE_SSL else 389)
AD_VERIFY_TLS = env_bool("AD_VERIFY_TLS", False)
AD_START_TLS = env_bool("AD_START_TLS", False)

AD = {
    "ENABLED": AD_ENABLED,
    "PROVIDER": AD_PROVIDER,
    "SERVER": env("AD_SERVER", ""),
    "PORT": AD_PORT,
    "USE_SSL": AD_USE_SSL,
    "START_TLS": AD_START_TLS,
    "VERIFY_TLS": AD_VERIFY_TLS,
    "CA_CERT_FILE": env("AD_CA_CERT_FILE", ""),
    "BASE_DN": env("AD_BASE_DN", ""),
    "BIND_DN": env("AD_BIND_DN", ""),
    "BIND_PASSWORD": env("AD_BIND_PASSWORD", ""),
    "DOMAIN_NETBIOS": env("AD_DOMAIN_NETBIOS", ""),
    "USER_SEARCH_BASE": env("AD_USER_SEARCH_BASE", "") or env("AD_BASE_DN", ""),
    "USER_FILTER": env(
        "AD_USER_FILTER",
        "(&(objectCategory=person)(objectClass=user)(!(isCriticalSystemObject=TRUE)))",
    ),
    "GROUP_SEARCH_BASE": env("AD_GROUP_SEARCH_BASE", "") or env("AD_BASE_DN", ""),
    "TIMEOUT": env_int("AD_TIMEOUT", 12),
    "PAGE_SIZE": env_int("AD_PAGE_SIZE", 500),
    "SYNC_INTERVAL_MINUTES": env_int("AD_SYNC_INTERVAL_MINUTES", 60),
}

# ---------------------------------------------------------------------------
# Отпуска
# ---------------------------------------------------------------------------
VACATION_DEFAULT_DAYS = env_int("VACATION_DEFAULT_DAYS", 28)
VACATION_MAX_DAYS_PER_REQUEST = env_int("VACATION_MAX_DAYS_PER_REQUEST", 28)
VACATION_MIN_NOTICE_DAYS = env_int("VACATION_MIN_NOTICE_DAYS", 3)
VACATION_MAX_OVERLAP_PERCENT = env_int("VACATION_MAX_OVERLAP_PERCENT", 40)

# ---------------------------------------------------------------------------
# Прочее
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = env("DJANGO_TIME_ZONE", "Europe/Moscow")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "var" / "media"

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

LANGUAGES = [("ru", "Русский")]

# Email-уведомления: по умолчанию пишутся в консоль/лог.
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("DJANGO_EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("DJANGO_EMAIL_PORT", 25)
EMAIL_HOST_USER = env("DJANGO_EMAIL_USER", "")
EMAIL_HOST_PASSWORD = env("DJANGO_EMAIL_PASSWORD", "")
EMAIL_USE_TLS = env_bool("DJANGO_EMAIL_USE_TLS", False)
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", "portal@example.local")
PORTAL_NOTIFY_BY_EMAIL = env_bool("PORTAL_NOTIFY_BY_EMAIL", False)

# Безопасность в боевом режиме
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", False)
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", False)
SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 0)
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(BASE_DIR / "var" / "portal.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "standard",
            "encoding": "utf-8",
        },
    },
    "root": {"handlers": ["console", "file"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {
            "handlers": ["console", "file"],
            "level": "WARNING",
            "propagate": False,
        },
        "apps": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
        "ldap3": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
