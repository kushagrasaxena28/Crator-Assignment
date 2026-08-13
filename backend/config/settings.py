"""Django settings for the external-services permissions layer.

One SQLite database, one app (`external_services`), DRF for the REST layer. Configuration that
differs between machines (Django's secret key, the JWT signing key) is read from the environment
via a `.env` file; see `.env.example`. User and agent credentials are not configuration — they
live in the database as hashes.
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
# Defaults to False: a missing or misspelled DJANGO_DEBUG should fail towards the safe setting,
# never quietly serve tracebacks and accept any Host header.
DEBUG = _env_bool("DJANGO_DEBUG", default=False)
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
        # SQLite needs configuring before it will honour the row lock the approval endpoint takes.
        # By default Django opens a DEFERRED transaction, so concurrent writers each acquire a read
        # lock first and then deadlock trying to upgrade it — SQLite gives up immediately with
        # "database is locked" rather than waiting, which surfaced as a 500 for every loser of the
        # race instead of a clean 409.
        "OPTIONS": {
            # Take the write lock when the transaction opens rather than on first write, so
            # concurrent writers queue instead of deadlocking on an upgrade.
            "transaction_mode": "IMMEDIATE",
            # ...and having queued, actually wait for their turn instead of erroring instantly.
            "timeout": 20,
            # WAL lets readers proceed while one writer holds the lock, so polling the audit log
            # or a ticket's status doesn't contend with a resolution in flight.
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        },
    }
}

# Our own user model, so the API's human identity and Django's admin/superuser account are the
# same thing rather than two parallel notions of "user". It subclasses AbstractUser and only
# overrides the primary key, so everything below actually applies to it.
AUTH_USER_MODEL = "external_services.User"

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

# Most endpoints are agent-facing, so the agent token is the default. The human-only controls
# (resolving a ticket, setting permissions) and the audit log override this per-view, and token
# issuance disables authentication entirely — it *is* the authentication step.
#
# `IsAuthenticated` is defence in depth rather than the primary control: the auth classes already
# raise on failure, so an unauthenticated request never reaches a view. But that made security rest
# entirely on every future auth class remembering to raise instead of returning None, which is a
# thin thing to depend on. Both our User and Agent models expose `is_authenticated` so this class
# understands them.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "external_services.authentication.AgentJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "EXCEPTION_HANDLER": "external_services.exceptions.api_exception_handler",
    "UNAUTHENTICATED_USER": None,
}

# Unexpected API errors are reported as one clean line by external_services.exceptions, so
# `django.request` is set to a plain handler at WARNING rather than being silenced: routing it to a
# NullHandler suppressed every 4xx/5xx the custom handler doesn't cover too, which made a genuine
# 500 nearly impossible to diagnose.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "[{levelname}] {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
