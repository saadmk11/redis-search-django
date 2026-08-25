from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-secret"

DEBUG = True

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "redis_search_django",
    "redis_search_django.debug",
    "tests",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

ROOT_URLCONF = "tests.urls"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REDIS_SEARCH = {
    "URL": "redis://localhost:6379/0",
    "PREFIX": "rsd",
    "AUTO_INDEX": True,
    "SIGNAL_ERRORS": "raise",
}

USE_TZ = True

MEDIA_ROOT = BASE_DIR / "tests" / "media"
MEDIA_URL = "/media/"
