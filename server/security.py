"""Password hashing, signed tokens, signed media URLs and rate limiting."""
import base64, hashlib, hmac, json, os, secrets, threading, time

from . import config

_SECRET = None


def secret():
    global _SECRET
    if _SECRET:
        return _SECRET
    if config.SECRET_KEY:
        _SECRET = config.SECRET_KEY.encode()
        return _SECRET
    path = os.path.join(os.path.dirname(config.DATABASE_PATH), ".secret_key")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        _SECRET = f.read().strip().encode()
    return _SECRET


def hash_password(pw):
    salt = secrets.token_bytes(16)
    it = 200_000
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, it)
    return f"pbkdf2_sha256${it}${salt.hex()}${dk.hex()}"


def verify_password(pw, stored):
    try:
        algo, it, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(user_id, token_version):
    payload = {"uid": user_id, "tv": token_version, "exp": int(time.time()) + config.TOKEN_TTL_HOURS * 3600}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token):
    try:
        body, sig = token.split(".")
        good = _b64(hmac.new(secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        payload = json.loads(_unb64(body))
        if payload["exp"] < time.time():
            return None
        return payload
    except Exception:
        return None


def sign_media(key, ttl=None):
    exp = int(time.time()) + (ttl or config.MEDIA_URL_TTL_SECONDS)
    sig = _b64(hmac.new(secret(), f"{key}|{exp}".encode(), hashlib.sha256).digest()[:18])
    return exp, sig


def verify_media(key, exp, sig):
    try:
        if int(exp) < time.time():
            return False
    except (TypeError, ValueError):
        return False
    good = _b64(hmac.new(secret(), f"{key}|{exp}".encode(), hashlib.sha256).digest()[:18])
    return hmac.compare_digest(good, sig or "")


class RateLimiter:
    def __init__(self, limit, window):
        self.limit, self.window, self.hits, self.lock = limit, window, {}, threading.Lock()

    def allow(self, key):
        now = time.time()
        with self.lock:
            q = [t for t in self.hits.get(key, []) if now - t < self.window]
            if len(q) >= self.limit:
                self.hits[key] = q
                return False
            q.append(now)
            self.hits[key] = q
            return True


login_limiter = RateLimiter(10, 300)
register_limiter = RateLimiter(5, 3600)
