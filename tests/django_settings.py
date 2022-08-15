from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-secret"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "redis_search_django",
    "tests",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

REDIS_SEARCH_AUTO_INDEX = True
REDIS_OM_URL = "redis://localhost:6379/0"
