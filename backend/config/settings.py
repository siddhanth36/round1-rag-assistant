"""Django settings for INDUSTRIX AI. Every environment-specific value comes from the environment / backend/.env."""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]  # repository root (manage.py, rag/, backend/)
load_dotenv(BASE_DIR / "backend" / ".env")  # optional file; real environment variables win


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"Environment variable {name} is required (see backend/.env.example).")
    return value


def env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = required("SECRET_KEY")
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]

# Supabase PostgreSQL through the session pooler. PostgreSQL only: there is deliberately no SQLite fallback.
# Tests use a separate database Django creates (test_<NAME>, i.e. test_postgres), never the real one.
DATABASES = {
    "default": dj_database_url.parse(
        required("DATABASE_URL"), conn_max_age=60, conn_health_checks=True, ssl_require=True
    )
}

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "backend.accounts",
    "backend.machines",
    "backend.assistant",
    "backend.inspections",
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

ROOT_URLCONF = "backend.config.urls"
WSGI_APPLICATION = "backend.config.wsgi.application"

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

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

TIME_ZONE = "UTC"  # USE_TZ is on by default: timestamps stored as UTC

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "frontend" / "static"]
# ponytail: no STATIC_ROOT/collectstatic: the demo runs on runserver with DEBUG=True. Add both for a real deployment.
LOGIN_URL = "/login/"

# Uploaded manuals. Never routed under a public URL: PDFs are served only by an authenticated view.
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "").strip() or BASE_DIR / "media")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))

# Round 2 pack folder holding the supplied, unmodified Model/inference.py + classifier.npz.
INSPECTION_MODEL_DIR = Path(os.environ.get("INSPECTION_MODEL_DIR", "").strip() or BASE_DIR / "challenge-pack-r2" / "Model")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["backend.accounts.authentication.SessionAuthentication401"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]
    + (["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
}

# --- security -----------------------------------------------------------------------------------
# CSRF middleware is on; cookies are SameSite=Lax (Django default). csrftoken stays readable by JS
# (default) so the frontend can send X-CSRFToken. X_FRAME_OPTIONS stays DENY globally; the PDF file
# view will opt in with @xframe_options_sameorigin (embedded by the same-origin viewer page).
# Defaults are restated below on purpose: these are the audited security settings.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = env_bool("SECURE_COOKIES", not DEBUG)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
# ponytail: no HSTS / SECURE_SSL_REDIRECT. The demo runs on local HTTP; set both (env-driven) when served over HTTPS.
