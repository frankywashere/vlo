"""Use the matching official JS codec on the already-installed Deno runtime.

The Python parser still owns protocol validation. Only its strict mask codec is
accelerated, with the same canonical checks and binary raster. A child belongs
to one decode request, receives no credentials or network/filesystem authority,
and is always closed. Missing Deno falls back to the original Python codec.
"""
import base64
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import time

from .errors import PublicError

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / "vendor" / "meta_sam_parser"
PROTOCOL = {"protocol": 1, "package": "@meta-sam/parser", "version": "0.1.0"}
MAX_MESSAGE = 8 * 1024 * 1024


def _failure():
    return PublicError("The local Meta mask decoder was interrupted. Saved mask responses are retained; no new segmentation was submitted.",
                       503, code="meta_codec_unavailable")


def _verify_package():
    """Check the small vendored official distribution before executing it."""
    manifest = json.loads((PACKAGE / "UPSTREAM.json").read_text())
    if (manifest.get("name") != PROTOCOL["package"] or manifest.get("version") != PROTOCOL["version"]
            or manifest.get("python_compatibility_version") != "0.2.0"):
        raise ValueError("Incompatible official codec")
    files = manifest.get("files", {})
    actual = {str(path.relative_to(PACKAGE)) for path in PACKAGE.rglob("*")
              if path.is_file() and path.name != "UPSTREAM.json"}
    if not files or actual != set(files):
        raise ValueError("Incomplete official codec")
    for relative, expected in files.items():
        path = PACKAGE / relative
        if (Path(relative).is_absolute() or ".." in Path(relative).parts or path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError("Changed official codec")


class StrictMaskCodec:
    """Synchronous codec bridge suitable for the parser's strict validation seam."""
    def __init__(self, python_codec, remaining):
        self.python_codec, self.remaining = python_codec, remaining
        self.process = None
        self.temporary = None
        self.selector = None
        self.buffer, self.sequence = bytearray(), 0
        self.backend = "python"
        mode = os.environ.get("META_MASK_CODEC", "auto")
        if mode not in {"auto", "python", "javascript"}:
            raise _failure()
        binary = shutil.which("deno") if mode != "python" else None
        if not binary:
            if mode == "javascript":
                raise _failure()
            return
        try:
            _verify_package()
            self.temporary = tempfile.TemporaryDirectory(prefix="meta-codec-")
            self.process = subprocess.Popen([
                binary, "run", "--no-prompt", "--no-config", "--no-remote", "--cached-only", "--no-lock", "--quiet",
                "--deny-net", "--deny-read", "--deny-write", "--deny-env", "--deny-run", "--deny-sys", "--deny-ffi",
                "--v8-flags=--max-old-space-size=128", str(ROOT / "meta_mask_codec.mjs")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
                # No API tokens, cloud credentials, signed URLs or ambient shell environment.
                env={"DENO_DIR": self.temporary.name, "DENO_NO_UPDATE_CHECK": "1", "NO_COLOR": "1"})
            for stream in (self.process.stdin, self.process.stdout):
                os.set_blocking(stream.fileno(), False)
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
            if self._exchange(None) != PROTOCOL:
                raise ValueError("Unexpected decoder handshake")
            self.backend = "javascript"
        except Exception:
            self.close()
            # A present but broken decoder must be visible, rather than silently
            # spending another long preparation using an unexpected runtime.
            raise _failure() from None

    def _exchange(self, request):
        deadline = time.monotonic() + min(10, self.remaining())
        outgoing = (json.dumps(request, separators=(",", ":")) + "\n").encode() if request is not None else b""
        if len(outgoing) > MAX_MESSAGE:
            raise _failure()
        sent = 0
        if outgoing:
            self.selector.register(self.process.stdin, selectors.EVENT_WRITE)
        try:
            while True:
                if b"\n" in self.buffer:
                    line, _, rest = self.buffer.partition(b"\n")
                    self.buffer = bytearray(rest)
                    if sent != len(outgoing):
                        raise _failure()
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise _failure()
                    return value
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _failure()
                for key, event in self.selector.select(remaining):
                    if key.fileobj is self.process.stdin:
                        written = os.write(key.fd, outgoing[sent:sent+65536])
                        sent += written
                        if sent == len(outgoing):
                            self.selector.unregister(self.process.stdin)
                    else:
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            raise _failure()
                        self.buffer.extend(chunk)
                        if len(self.buffer) > MAX_MESSAGE:
                            raise _failure()
        except Exception:
            self.close()
            raise _failure() from None

    def decode(self, mask):
        if self.backend == "python":
            return self.python_codec(mask)
        try:
            self.sequence += 1
            result = self._exchange({"id": self.sequence, "mask": {"encoding": mask.encoding,
                "width": mask.width, "height": mask.height, "payload": mask.payload}})
            if result.get("id") != self.sequence:
                raise _failure()
            if result.get("ok") is False and result.get("kind") == "invalid_mask":
                from meta_sam_parser import InvalidSegmentationMaskError
                raise InvalidSegmentationMaskError("The official Meta codec rejected this mask.")
            if result.get("ok") is not True or not isinstance(result.get("raster"), str):
                raise _failure()
            raster = base64.b64decode(result["raster"], validate=True)
            if len(raster) != mask.width * mask.height or raster.count(0) + raster.count(1) != len(raster):
                raise _failure()
            return raster
        except PublicError:
            raise
        except Exception as error:
            from meta_sam_parser import InvalidSegmentationMaskError
            if isinstance(error, InvalidSegmentationMaskError):
                raise
            self.close()
            raise _failure() from None

    def close(self):
        if self.selector:
            self.selector.close()
            self.selector = None
        if self.process:
            for stream in (self.process.stdin, self.process.stdout):
                if stream:
                    with suppress(OSError):
                        stream.close()
            if self.process.poll() is None:
                with suppress(ProcessLookupError):
                    self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            self.process = None
        if self.temporary:
            self.temporary.cleanup()
            self.temporary = None
