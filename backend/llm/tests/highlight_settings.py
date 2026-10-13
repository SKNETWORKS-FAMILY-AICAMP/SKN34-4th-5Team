"""Highlight ORM regression only: disposable loopback PostgreSQL, no dotenv/live DB."""
import os

from baseball.tests.unit_settings import *

INSTALLED_APPS = [
    "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.postgres",
    "baseball", "accounts", "llm",
    "travel", "community", "ads", "tving", "fantasy",
]
AUTH_USER_MODEL = "accounts.CustomUser"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": "127.0.0.1",
        "PORT": os.environ.get("HIGHLIGHT_TEST_DB_PORT", "5432"),
        "NAME": "highlight_ci",
        "USER": "highlight_ci",
        "PASSWORD": "highlight_ci",
        "TEST": {"NAME": "test_highlight_ci"},
    },
}
DATABASE_ROUTERS = []
