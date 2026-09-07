"""Settings for ClaimVoice — the AI first-responder for insurance claims intake.

One deployable serves three things: the voice client judges talk to, the
webhook AssemblyAI posts extracted claims to, and the dispatcher dashboard.
"""

from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Credentials live in .env next to manage.py. Real environment variables win,
# so a hosting platform's config overrides the file.
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-for-production-9f2a1c")
DEBUG = env_bool("DEBUG", True)

# Hosting platforms hand out a hostname at deploy time; accept it plus anything
# listed explicitly. In DEBUG everything is allowed so ngrok/tunnels just work.
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
for var in ("RENDER_EXTERNAL_HOSTNAME", "RAILWAY_PUBLIC_DOMAIN"):
    if os.environ.get(var):
        ALLOWED_HOSTS.append(os.environ[var])
if DEBUG or not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["*"]

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]
for var in ("RENDER_EXTERNAL_HOSTNAME", "RAILWAY_PUBLIC_DOMAIN"):
    if os.environ.get(var):
        CSRF_TRUSTED_ORIGINS.append(f"https://{os.environ[var]}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "claims",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "claimvoice.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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

WSGI_APPLICATION = "claimvoice.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Postgres on Render/Railway when DATABASE_URL is present; SQLite otherwise.
if os.environ.get("DATABASE_URL"):
    import dj_database_url

    DATABASES["default"] = dj_database_url.config(
        conn_max_age=600, conn_health_checks=True
    )

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

# Call recordings. On a platform with an ephemeral disk (Render free) these
# survive until the next deploy; point MEDIA_ROOT at a mounted disk or object
# store to keep them.
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if not DEBUG:
    # Render and Railway terminate TLS at their edge and forward over http, so
    # Django has to be told what the browser actually used.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    # The tool webhooks are machine-to-machine and answer POST only; the
    # redirect would turn them into a GET and lose the body.
    SECURE_REDIRECT_EXEMPT = [r"^api/", r"^healthz/"]

# --- ClaimVoice ------------------------------------------------------------

ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY", "")
# Set by `manage.py publish_agent`; the voice page connects to this agent.
ASSEMBLYAI_AGENT_ID = os.environ.get("AGENT_ID", "")
ASSEMBLYAI_AGENTS_API = os.environ.get(
    "AGENTS_API_BASE", "https://agents.assemblyai.com/v1"
)
# Public https origin of this deployment. AssemblyAI posts the tool webhooks
# here, and it stores HTTP tools only — so without this the agent is published
# with no tools at all and Ivy can talk but never file anything.
#
# Hosting platforms know their own hostname, so on Render or Railway this
# configures itself and there is nothing to paste after the first deploy.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
if not PUBLIC_BASE_URL:
    for _var in ("RENDER_EXTERNAL_HOSTNAME", "RAILWAY_PUBLIC_DOMAIN"):
        if os.environ.get(_var):
            PUBLIC_BASE_URL = f"https://{os.environ[_var]}"
            break
# Optional shared secret required on the webhook when set.
CLAIM_WEBHOOK_SECRET = os.environ.get("CLAIM_WEBHOOK_SECRET", "")

# A recording is roughly 100 KB per second of call at 24 kHz stereo, so a long
# call is a big upload. This caps it rather than letting one fill the disk.
MAX_RECORDING_BYTES = int(os.environ.get("MAX_RECORDING_BYTES", 40 * 1024 * 1024))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "claims": {"handlers": ["console"], "level": "INFO"},
    },
}
