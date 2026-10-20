"""Local template storage uses the same catalog and validation as the cloud adapter."""
from pathlib import Path
import hashlib
import hmac
import json
import os
import re
import shutil
import threading
import time

from .errors import PublicError
from .templates import CATALOG_PATH

LOCAL_STORAGE_LOCK = threading.RLock()


class LocalTemplateStorage:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = LOCAL_STORAGE_LOCK
        secret = self.root / "media-signing.key"
        with self.lock:
            try:
                descriptor = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as file:
                    file.write(os.urandom(32))
            except FileExistsError:
                pass
            self.secret = secret.read_bytes()

    def read(self, path):
        if path != CATALOG_PATH:
            raise PublicError("Invalid template record.", 500)
        with self.lock:
            file = self.root / "catalog.json"
            return (json.loads(file.read_text("utf-8")) if file.exists() else None), 1

    def update(self, path, mutation):
        with self.lock:
            current, _ = self.read(path)
            updated = mutation(current)
            temporary = self.root / "catalog.partial"
            temporary.write_text(json.dumps(updated), "utf-8")
            temporary.replace(self.root / "catalog.json")
            return updated

    def asset(self, key):
        if not re.fullmatch(r"template-media/[a-f0-9]{32}/(?:video\.mp4|poster\.jpg)", key):
            raise PublicError("Template asset not found.", 404)
        target = self.root.joinpath(*key.split("/")).resolve()
        if not target.is_relative_to(self.root):
            raise PublicError("Template asset not found.", 404)
        return target

    def upload(self, _bucket, key, source, _content_type):
        target = self.asset(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # A retried upload has immutable media under its original request ID.
            return "gs://templates-local/" + key
        with target.open("xb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
        return "gs://templates-local/" + key

    def download(self, uri, target):
        key = uri.removeprefix("gs://templates-local/")
        shutil.copyfile(self.asset(key), target)

    def signed_url(self, uri):
        key = uri.removeprefix("gs://templates-local/")
        self.asset(key)
        expires = int(time.time()) + 900
        signature = hmac.new(self.secret, f"{key}:{expires}".encode(), hashlib.sha256).hexdigest()
        return "/api/templates/assets/" + key.removeprefix("template-media/") + f"?expires={expires}&signature={signature}"

    def verify_asset(self, template_id, filename, expires, signature):
        key = f"template-media/{template_id}/{filename}"
        target = self.asset(key)
        expected = hmac.new(self.secret, f"{key}:{expires}".encode(), hashlib.sha256).hexdigest()
        if not time.time() < expires <= time.time() + 3600 or not hmac.compare_digest(expected, signature) or not target.is_file():
            raise PublicError("This template link expired. Refresh the templates page.", 403)
        return target
