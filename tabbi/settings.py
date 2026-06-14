import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

_PRODUCTION = os.environ.get("DJANGO_SECRET_KEY") is not None

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-tabbi-dev-key-change-in-production",
)

DEBUG = not _PRODUCTION

ALLOWED_HOSTS = (
    [os.environ.get("ALLOWED_HOST", "tab.bi"), "www.tab.bi"]
    if _PRODUCTION
    else ["*"]
)

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "django.contrib.sessions",
    "plans",
    "concierge",
    "passport",
    "world66_content",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 365

ROOT_URLCONF = "tabbi.urls"

# World66 content — set WORLD66_DIR in .env to a cloned copy of the world66 repo
WORLD66_DIR = Path(os.environ.get("WORLD66_DIR", str(BASE_DIR / "world66")))
WORLD66_CONTENT_DIR = WORLD66_DIR / "content"
WORLD66_SITE_URL = os.environ.get("WORLD66_SITE_URL", "https://world66.ai")

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "plans.context_processors.world66_url",
            ],
        },
    },
]

WSGI_APPLICATION = "tabbi.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "tabbi.db",
    }
}

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

EMAIL_BACKEND = (
    "django.core.mail.backends.console.EmailBackend"
    if not _PRODUCTION and not os.environ.get("EMAIL_HOST_USER")
    else "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Tabbi Concierge <concierge@tab.bi>")

SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
