"""Bounded private-asset signing caches; authorization remains with each route."""
import base64
from collections import OrderedDict
from concurrent.futures import Future
import threading
import time

import google.auth
from google.auth.credentials import Signing
from google.auth.transport.requests import Request

from .errors import PublicError


class BoundedAuthRequest(Request):
    def __call__(self, *args, timeout=120, **kwargs):
        return super().__call__(*args, timeout=min(10, timeout) if timeout is not None else 10, **kwargs)


class AccessTokens:
    """Refresh one server credential only when Google's expiry guard requires it."""
    def __init__(self, factory=None):
        self.factory = factory
        self.lock = threading.Lock()
        self.credentials = None
        self.retry_after = 0.

    def get(self):
        with self.lock:
            if time.monotonic() < self.retry_after:
                raise PublicError("Google authentication is temporarily unavailable. Try again shortly.",
                                  503, code="google_auth_unavailable")
            try:
                if self.credentials is None:
                    factory = self.factory or google.auth.default
                    self.credentials, _ = factory(scopes=["https://www.googleapis.com/auth/cloud-platform"],
                                                   request=BoundedAuthRequest())
                if not self.credentials.valid:
                    self.credentials.refresh(BoundedAuthRequest())
                return self.credentials.token
            except Exception:
                # Waiting URL signers must share an authentication failure,
                # rather than each performing another slow refresh in turn.
                self.retry_after = time.monotonic() + 5
                raise PublicError("Google authentication is temporarily unavailable. Try again shortly.",
                                  503, code="google_auth_unavailable") from None


class SignedUrls:
    """LRU with single-flight misses, isolated by exact asset and URL lifetime.

    Five-minute reuse leaves at least 55 minutes on a one-hour link, safely
    longer than the browser's subsequent 30-minute media reuse. Short-lived
    provider links are never reused with less than half their lifetime left.
    """
    def __init__(self, maximum=1024, clock=time.monotonic):
        self.maximum, self.clock = maximum, clock
        self.lock = threading.Lock()
        self.entries, self.pending = OrderedDict(), {}
        self.requests = self.hits = self.misses = 0

    def get(self, key, seconds, build):
        now = self.clock()
        with self.lock:
            self.requests += 1
            value = self.entries.get(key)
            if value and value[0] > now:
                self.hits += 1
                self.entries.move_to_end(key)
                return value[1]
            self.entries.pop(key, None)
            future = self.pending.get(key)
            owner = future is None
            if owner:
                future = Future()
                self.pending[key] = future
                self.misses += 1
            else:
                self.hits += 1
        if not owner:
            return future.result(timeout=20)
        try:
            value = build()
            # Measure from before signing, not afterward: a slow signer must
            # not extend the original signed URL's usable cache lifetime.
            until = now + min(300, max(0, seconds / 2))
            with self.lock:
                self.entries[key] = (until, value)
                self.entries.move_to_end(key)
                while len(self.entries) > self.maximum:
                    self.entries.popitem(last=False)
            future.set_result(value)
            return value
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with self.lock:
                self.pending.pop(key, None)

    def stats(self):
        with self.lock:
            return {"url_requests": self.requests, "url_cache_hits": self.hits, "unique_urls": self.misses}


class IamSigner(Signing):
    """Keep Storage's V4 canonicalization, with a bounded IAM transport call."""
    def __init__(self, email, http, token):
        self.email, self.http, self.token = email, http, token

    @property
    def signer_email(self):
        return self.email

    @property
    def signer(self):
        return self

    def sign_bytes(self, message):
        try:
            response = self.http.post(
                f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{self.email}:signBlob",
                headers={"Authorization": "Bearer " + self.token(), "Content-Type": "application/json"},
                json={"payload": base64.b64encode(message).decode("ascii")}, timeout=10)
            response.raise_for_status()
            signature = base64.b64decode(response.json()["signedBlob"], validate=True)
            if not signature or len(signature) > 8192:
                raise ValueError()
            return signature
        except Exception:
            raise PublicError("Your Library links are temporarily unavailable. Tap Refresh to try again.",
                              503, code="library_signing_unavailable") from None
