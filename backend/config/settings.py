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

# Round 2: the 60 labeled samples scored by `manage.py evaluate_samples`, and where its report is written.
# The report is a generated file (like the Chroma index), not database rows: GET /api/inspections/metrics/ reads it.
INSPECTION_SAMPLES_DIR = Path(os.environ.get("INSPECTION_SAMPLES_DIR", "").strip()
                              or BASE_DIR / "challenge-pack-r2" / "Samples")
INSPECTION_METRICS_FILE = Path(os.environ.get("INSPECTION_METRICS_FILE", "").strip()
                               or MEDIA_ROOT / "metrics" / "inspection_metrics.json")

# --- Celery (batch inspections) ------------------------------------------------------------------
# One task per batch item; the broker is Redis. No result backend: results are written to the database.
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "").strip() or "redis://localhost:6379/0"
CELERY_TASK_IGNORE_RESULT = True
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
# A task is acknowledged only after it finishes, so a worker that stops mid-task gets it redelivered;
# the task is idempotent (services.record_prediction never overwrites a final result).
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS = True
CELERY_TASK_SOFT_TIME_LIMIT = 60  # seconds; raises inside the model call -> the item fails readably
CELERY_TASK_TIME_LIMIT = 90
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True  # the worker waits for Redis instead of exiting
# Fail fast when Redis is down so an upload request isn't held for long: the items are marked failed.
CELERY_BROKER_CONNECTION_TIMEOUT = 1
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 600, "socket_connect_timeout": 1, "socket_timeout": 5}
CELERY_TASK_PUBLISH_RETRY_POLICY = {"max_retries": 1, "interval_start": 0, "interval_step": 0.2, "interval_max": 0.2}

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
