"""Pluggable media storage.

LocalStorage keeps files under MEDIA_DIR and serves them through /media with
HTTP Range support and HMAC-signed, expiring URLs for audio.

S3Storage works with any S3-compatible object store (AWS S3, Cloudflare R2,
Wasabi, MinIO, DigitalOcean Spaces). Uploads use SigV4-signed PUTs and playback
uses short-lived presigned GET URLs, so audio never needs to be public. Put a CDN
(CloudFront, Cloudflare) in front of the bucket and set CDN_BASE_URL for public
artwork.
"""
import datetime, hashlib, hmac, os, shutil, urllib.parse, urllib.request

from . import config, security

AUDIO_PREFIXES = ("audio/", "uploads/audio/")


def is_protected(key):
    return key.startswith(AUDIO_PREFIXES)


class LocalStorage:
    name = "local"

    def __init__(self, root):
        self.root = os.path.abspath(root)

    def path(self, key):
        p = os.path.abspath(os.path.join(self.root, key))
        if not p.startswith(self.root + os.sep):
            raise ValueError("invalid storage key")
        return p

    def put(self, key, data: bytes, content_type="application/octet-stream"):
        p = self.path(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        return key

    def put_file(self, key, src_path, content_type="application/octet-stream"):
        p = self.path(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copyfile(src_path, p)
        return key

    def local_path(self, key):
        """Path on disk for processing (ffmpeg). For local storage it is the file itself."""
        return self.path(key)

    def delete(self, key):
        try:
            os.remove(self.path(key))
        except FileNotFoundError:
            pass

    def exists(self, key):
        return os.path.exists(self.path(key))

    def url(self, key):
        if not key:
            return None
        if key.startswith(("http://", "https://")):
            return key
        if is_protected(key):
            exp, sig = security.sign_media(key)
            return f"/media/{urllib.parse.quote(key)}?exp={exp}&sig={sig}"
        if config.CDN_BASE_URL:
            return f"{config.CDN_BASE_URL.rstrip('/')}/{urllib.parse.quote(key)}"
        return f"/media/{urllib.parse.quote(key)}"


class S3Storage:
    name = "s3"

    def __init__(self, bucket=None, access_key=None, secret_key=None, endpoint=None, region=None, public_url=None):
        self.bucket = bucket or config.S3_BUCKET
        self.access_key, self.secret_key = access_key or config.S3_ACCESS_KEY, secret_key or config.S3_SECRET_KEY
        if not (self.bucket and self.access_key and self.secret_key):
            raise RuntimeError("STORAGE_DRIVER=s3 needs S3_BUCKET, S3_ACCESS_KEY and S3_SECRET_KEY")
        self.region = region or config.S3_REGION
        self.public_url = (public_url or "").rstrip("/")  # a public bucket domain (R2: https://pub-….r2.dev or your own)
        ep = endpoint or config.S3_ENDPOINT or f"https://s3.{self.region}.amazonaws.com"
        u = urllib.parse.urlparse(ep)
        self.scheme, self.host = u.scheme, u.netloc
        self.cache_dir = os.path.join(os.path.dirname(config.DATABASE_PATH), "s3cache")

    # --- SigV4 helpers (path-style addressing works for AWS and every S3-compatible store) ---
    def _sign(self, key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    def _signing_key(self, date):
        k = self._sign(("AWS4" + self.secret_key).encode(), date)
        k = self._sign(k, self.region)
        k = self._sign(k, "s3")
        return self._sign(k, "aws4_request")

    def _canonical_path(self, key):
        return "/" + self.bucket + "/" + urllib.parse.quote(key, safe="/~")

    def presign(self, key, expires=None, method="GET", now=None):
        expires = expires or config.MEDIA_URL_TTL_SECONDS
        t = now or datetime.datetime.now(datetime.timezone.utc)
        amz, date = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
        scope = f"{date}/{self.region}/s3/aws4_request"
        qs = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{self.access_key}/{scope}", "X-Amz-Date": amz,
              "X-Amz-Expires": str(min(expires, 604800)), "X-Amz-SignedHeaders": "host"}
        cq = "&".join(f"{urllib.parse.quote(k, safe='~')}={urllib.parse.quote(v, safe='~')}" for k, v in sorted(qs.items()))
        creq = "\n".join([method, self._canonical_path(key), cq, f"host:{self.host}\n", "host", "UNSIGNED-PAYLOAD"])
        sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(creq.encode()).hexdigest()])
        sig = hmac.new(self._signing_key(date), sts.encode(), hashlib.sha256).hexdigest()
        return f"{self.scheme}://{self.host}{self._canonical_path(key)}?{cq}&X-Amz-Signature={sig}"

    def _request(self, method, key, data=b"", content_type=None, extra_headers=None, now=None, send=True):
        t = now or datetime.datetime.now(datetime.timezone.utc)
        amz, date = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(data).hexdigest()
        headers = {"host": self.host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz}
        if content_type:
            headers["content-type"] = content_type
        headers.update({k.lower(): v for k, v in (extra_headers or {}).items()})
        signed = ";".join(sorted(headers))
        canon_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
        creq = "\n".join([method, self._canonical_path(key), "", canon_headers, signed, payload_hash])
        scope = f"{date}/{self.region}/s3/aws4_request"
        sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(creq.encode()).hexdigest()])
        sig = hmac.new(self._signing_key(date), sts.encode(), hashlib.sha256).hexdigest()
        headers["Authorization"] = f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"
        if not send:  # for tests: the signature without a network call
            return headers
        req = urllib.request.Request(f"{self.scheme}://{self.host}{self._canonical_path(key)}", data=data or None, method=method,
                                     headers={k: v for k, v in headers.items() if k != "host"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()

    def put(self, key, data, content_type="application/octet-stream", cache_control=None):
        self._request("PUT", key, data, content_type, {"cache-control": cache_control} if cache_control else None)
        return key

    def public(self, key):
        """Permanent public link (bucket served through its public domain), for files meant to be streamed by anyone."""
        return f"{self.public_url}/{urllib.parse.quote(key, safe='/~')}" if self.public_url else None

    def put_file(self, key, src_path, content_type="application/octet-stream"):
        with open(src_path, "rb") as f:
            return self.put(key, f.read(), content_type)

    def local_path(self, key):
        p = os.path.join(self.cache_dir, key)
        if not os.path.exists(p):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with urllib.request.urlopen(self.presign(key, 600), timeout=120) as r, open(p, "wb") as f:
                shutil.copyfileobj(r, f)
        return p

    def delete(self, key):
        self._request("DELETE", key)

    def exists(self, key):
        try:
            self._request("HEAD", key)
            return True
        except Exception:
            return False

    def url(self, key):
        if not key:
            return None
        if key.startswith(("http://", "https://")):
            return key
        if is_protected(key):
            return self.presign(key)
        if config.CDN_BASE_URL:
            return f"{config.CDN_BASE_URL.rstrip('/')}/{urllib.parse.quote(key)}"
        return self.presign(key, 7 * 24 * 3600)


def _make():
    if config.STORAGE_DRIVER == "s3":
        return S3Storage()
    return LocalStorage(config.MEDIA_DIR)


storage = _make()


def r2():
    """Cloudflare R2 bucket for direct-stream uploads (S3-compatible API, signed here with SigV4: no boto3 needed).
    None when the R2_* settings are missing."""
    if not (config.R2_ENDPOINT_URL and config.R2_ACCESS_KEY_ID and config.R2_SECRET_ACCESS_KEY and config.R2_BUCKET_NAME and config.R2_PUBLIC_URL):
        return None
    return S3Storage(bucket=config.R2_BUCKET_NAME, access_key=config.R2_ACCESS_KEY_ID, secret_key=config.R2_SECRET_ACCESS_KEY,
                     endpoint=config.R2_ENDPOINT_URL, region="auto", public_url=config.R2_PUBLIC_URL)
