import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY in production.")
    SECRET_KEY = "local-development-key"
host_default = "localhost,127.0.0.1,[::1]" if DEBUG else ""
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", host_default).split(",")
    if host.strip()
]
if not DEBUG and (not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS):
    raise ImproperlyConfigured("Set DJANGO_ALLOWED_HOSTS to explicit hosts in production.")
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "repairs",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "repairs.middleware.TrackingPrivacyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "portal.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "portal.wsgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
postgres_db = os.getenv("POSTGRES_DB")
database_url = os.getenv("DATABASE_URL")
if not DEBUG and not (postgres_db or database_url):
    raise ImproperlyConfigured("Set DATABASE_URL or POSTGRES_DB in production.")
if database_url:
    from psycopg import ProgrammingError
    from psycopg.conninfo import conninfo_to_dict

    if not database_url.startswith(("postgresql://", "postgres://")):
        raise ImproperlyConfigured("DATABASE_URL must be a PostgreSQL URL.")
    try:
        options = conninfo_to_dict(database_url)
    except ProgrammingError:
        raise ImproperlyConfigured("DATABASE_URL is not a valid PostgreSQL URL.") from None
    database_name = options.pop("dbname", "")
    if not database_name:
        raise ImproperlyConfigured("DATABASE_URL must include the database name.")
    options.setdefault("sslmode", "verify-full")
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": database_name,
        "HOST": options.pop("host", ""),
        "PORT": options.pop("port", ""),
        "USER": options.pop("user", ""),
        "PASSWORD": options.pop("password", ""),
        "OPTIONS": options,
        "DISABLE_SERVER_SIDE_CURSORS": True,
    }
elif postgres_db:
    if not all(os.getenv(name) for name in ("POSTGRES_USER", "POSTGRES_PASSWORD")):
        raise ImproperlyConfigured("Set POSTGRES_USER and POSTGRES_PASSWORD for PostgreSQL.")
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": postgres_db,
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "OPTIONS": {"sslmode": os.getenv("POSTGRES_SSLMODE", "prefer" if DEBUG else "verify-full")},
    }
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"django.contrib.auth.password_validation.{validator}"}
    for validator in ("UserAttributeSimilarityValidator", "MinimumLengthValidator",
                      "CommonPasswordValidator", "NumericPasswordValidator")
]
TIME_ZONE = os.getenv("SHOP_TIME_ZONE", "Asia/Kolkata")
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}
if not DEBUG:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    if os.getenv("DJANGO_TRUST_HTTPS_PROXY") == "1":
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
