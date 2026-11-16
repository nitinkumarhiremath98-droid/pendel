"""Runtime configuration. Everything can be overridden with environment variables
(or a .env file in the project root, loaded at start-up)."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv():
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def env(name, default=None, cast=str):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    if cast is bool:
        return v.lower() in ("1", "true", "yes", "on")
    return cast(v)


APP_NAME = env("APP_NAME", "PENDEL")
HOST = env("HOST", "127.0.0.1")
PORT = env("PORT", 8000, int)
PUBLIC_URL = env("PUBLIC_URL", f"http://localhost:{PORT}")
DATABASE_PATH = env("DATABASE_PATH", os.path.join(ROOT, "data", "pendel.db"))
# keep databases created before the PENDEL rebrand working
_legacy_db = os.path.join(ROOT, "data", "refrain.db")
if not os.environ.get("DATABASE_PATH") and not os.path.exists(DATABASE_PATH) and os.path.exists(_legacy_db):
    DATABASE_PATH = _legacy_db
# Persistent database for hosts whose disk is temporary (Render free plan): postgresql://user:pass@host/db?sslmode=require
DATABASE_URL = env("DATABASE_URL", "")
MONGO_URI = env("MONGO_URI", "")  # not supported (see db.py); kept only to print a helpful message
# Render (and similar hosts) wipe the local disk on every restart/redeploy
EPHEMERAL_DISK = env("EPHEMERAL_DISK", bool(os.environ.get("RENDER")), bool)
SECRET_KEY = env("SECRET_KEY", "")  # generated + persisted in data/ if empty
TOKEN_TTL_HOURS = env("TOKEN_TTL_HOURS", 24 * 14, int)
MEDIA_URL_TTL_SECONDS = env("MEDIA_URL_TTL_SECONDS", 6 * 3600, int)
WEB_DIR = os.path.join(ROOT, "web")
MEDIA_DIR = env("MEDIA_DIR", os.path.join(ROOT, "media"))
MAX_UPLOAD_MB = env("MAX_UPLOAD_MB", 60, int)

# Storage: "local" (disk, served by this app) or "s3" (any S3-compatible bucket: AWS S3, Cloudflare R2, Wasabi, MinIO...)
STORAGE_DRIVER = env("STORAGE_DRIVER", "local")
S3_BUCKET = env("S3_BUCKET", "")
S3_REGION = env("S3_REGION", "us-east-1")
S3_ENDPOINT = env("S3_ENDPOINT", "")  # e.g. https://<account>.r2.cloudflarestorage.com ; empty = AWS
S3_ACCESS_KEY = env("S3_ACCESS_KEY", "")
S3_SECRET_KEY = env("S3_SECRET_KEY", "")
# Optional CDN in front of public assets (covers, artist images). Audio always goes through signed URLs.
CDN_BASE_URL = env("CDN_BASE_URL", "")

# Auth
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", "")
BOOTSTRAP_ADMIN_EMAIL = env("ADMIN_EMAIL", "admin@pendel.local")
BOOTSTRAP_ADMIN_PASSWORD = env("ADMIN_PASSWORD", "Admin@12345")
SEED_DEMO = env("SEED_DEMO", True, bool)

# Payments: comma list of enabled providers. "sandbox" simulates checkout (no money moves).
# "razorpay" (UPI, cards, netbanking, wallets) activates only when keys are present.
PAYMENT_PROVIDERS = [p.strip() for p in env("PAYMENT_PROVIDERS", "sandbox").split(",") if p.strip()]
RAZORPAY_KEY_ID = env("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = env("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = env("RAZORPAY_WEBHOOK_SECRET", "")
CURRENCY = env("CURRENCY", "INR")

FFMPEG = env("FFMPEG_PATH", "ffmpeg")

# World catalogue: real songs via Apple's public iTunes Search API (30 s previews) + YouTube for full songs
WORLD_CATALOG = env("WORLD_CATALOG", True, bool)
WORLD_COUNTRY = env("WORLD_COUNTRY", "IN")          # default Apple storefront for searches and charts
WORLD_WARMUP = env("WORLD_WARMUP", True, bool)      # slowly pre-fill the cache in the background
YOUTUBE_API_KEY = env("YOUTUBE_API_KEY", "")         # enables full songs from the world catalogue
