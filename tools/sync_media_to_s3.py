"""Copy everything under media/ to the configured S3-compatible bucket.

Use this once when switching STORAGE_DRIVER from local to s3:
    python tools/sync_media_to_s3.py
"""
import mimetypes, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["STORAGE_DRIVER"] = "s3"
from server import config  # noqa: E402
from server.storage import S3Storage  # noqa: E402

s3 = S3Storage()
n = 0
for root, _, files in os.walk(config.MEDIA_DIR):
    for name in files:
        path = os.path.join(root, name)
        key = os.path.relpath(path, config.MEDIA_DIR).replace(os.sep, "/")
        s3.put_file(key, path, mimetypes.guess_type(name)[0] or "application/octet-stream")
        n += 1
        print("uploaded", key)
print(f"done: {n} files")
