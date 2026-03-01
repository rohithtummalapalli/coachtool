from __future__ import annotations

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent
load_dotenv(ROOT_DIR / ".env")


def env(key: str, default: str | None = None) -> str | None:
    return os.getenv(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(key: str, default: list[str] | None = None) -> list[str]:
    value = os.getenv(key)
    if value is None:
        return default or []
    return [v.strip() for v in value.split(",") if v.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY", "unsafe-dev-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", default=["127.0.0.1", "localhost"])

INSTALLED_APPS = [
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
    "accounts",
    "rag_admin",
    "mcp_admin",
]

JAZZMIN_SETTINGS = {
    "site_title": "AI Admin Panel",
    "site_header": "AI Control Center",
    "site_brand": "My AI Platform",
    "welcome_sign": "Welcome to AI Control Center",
    "copyright": "My AI Platform",
    "show_sidebar": True,
    "navigation_expanded": True,
    "related_modal_active": True,
    "icons": {
        "auth.user": "fas fa-user",
        "auth.group": "fas fa-users-cog",
        "rag_admin.document": "fas fa-file-lines",
        "rag_admin.ragsettings": "fas fa-sliders",
        "mcp_admin.mcpserver": "fas fa-server",
        "mcp_admin.mcptool": "fas fa-screwdriver-wrench",
    },
    "order_with_respect_to": [
        "auth",
        "accounts",
        "rag_admin",
        "mcp_admin",
        "admin",
    ],
    "custom_links": {
        "rag_admin": [
            {"name": "Documents", "url": "admin:rag_admin_document_changelist", "icon": "fas fa-file-lines"},
            {"name": "RAG Settings", "url": "admin:rag_admin_ragsettings_changelist", "icon": "fas fa-sliders"},
        ],
        "accounts": [
            {"name": "Users", "url": "admin:accounts_user_changelist", "icon": "fas fa-user"},
        ],
    },
    "topmenu_links": [
        {"name": "Home", "url": "admin:index", "permissions": ["auth.view_user"]},
        {"model": "rag_admin.Document"},
        {"model": "rag_admin.RagSettings"},
        {"model": "accounts.User"},
    ],
}

JAZZMIN_UI_TWEAKS = {
    "theme": "flatly",
    "dark_mode_theme": "darkly",
    "navbar_small_text": False,
    "body_small_text": False,
    "sidebar_nav_small_text": False,
    "sidebar_disable_expand": False,
}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
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
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

database_url = env("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
if database_url and database_url.startswith("sqlite:///") and not database_url.startswith("sqlite:////"):
    # Normalize relative sqlite path so cwd doesn't change DB target.
    relative_sqlite_path = database_url.replace("sqlite:///", "", 1)
    database_url = f"sqlite:///{(BASE_DIR / relative_sqlite_path).resolve().as_posix()}"
is_postgres = database_url.startswith("postgres://") or database_url.startswith("postgresql://")

db_parse_kwargs = {
    "conn_max_age": 600,
}
if is_postgres:
    db_parse_kwargs["ssl_require"] = env_bool("DATABASE_SSL_REQUIRE", default=not DEBUG)

DATABASES = {
    "default": dj_database_url.parse(database_url, **db_parse_kwargs)
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "RAG Tool Admin API",
    "DESCRIPTION": "Admin and user management API",
    "VERSION": "1.0.0",
}

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", default=[])
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", default=[])

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=not DEBUG)
if DEBUG:
    # Always keep local dev on HTTP to avoid admin/login redirect loops.
    SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        }
    },
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
}
