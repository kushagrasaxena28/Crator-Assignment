"""Django settings for the external-services permissions layer.

One SQLite database, one app (`external_services`), DRF for the REST layer. Configuration that
differs between machines (secrets, the JWT signing key, admin credentials, Notion keys) is read
from the environment via a `.env` file; see `.env.example`.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env before reading any environment values below.
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-key-change-in-production")
DEBUG = _env_bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = ["*"] if DEBUG else os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "external_services",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
        "DIRS": [],
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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# App-specific configuration — every value that differs between machines lives here, read once
# from the environment, rather than each module calling os.environ.get() independently.
JWT_SECRET = os.environ.get("JWT_SECRET")
JWT_EXPIRES_IN_MINUTES = int(os.environ.get("JWT_EXPIRES_IN_MINUTES", "20"))
ADMIN_USER = os.environ.get("ADMIN_USER")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID")

# Agent endpoints authenticate with a short-lived JWT; the auth class raises on any failure, so
# a missing/invalid token is a clean 401 without needing a permission class. The two exceptions
# (token issuance, admin resolve/) set their own authentication per-view.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "external_services.authentication.AgentJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [],
    "EXCEPTION_HANDLER": "external_services.exceptions.api_exception_handler",
    "UNAUTHENTICATED_USER": None,
}

# We report unexpected API errors ourselves as one clean line (see external_services.exceptions);
# this silences Django's default duplicate traceback dump. Per-request status lines are unaffected.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "loggers": {
        "django.request": {"handlers": ["null"], "propagate": False},
    },
}
