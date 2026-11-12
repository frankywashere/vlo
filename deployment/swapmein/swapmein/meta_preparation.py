"""Durable Meta SAM 3.1 segmentation using the common private label archive.

One paid POST per operation: immutable stream checkpoints permit offline replay,
while an incomplete/lost stream never causes a replacement paid request. SAM's
strict official codec remains authoritative. Its validated raster is reused
immediately by the exporter through a bounded, request-local ContextVar cache.
"""
import asyncio
import base64
from collections import OrderedDict
from contextvars import ContextVar
import hashlib
from importlib.metadata import version as package_version
from io import BytesIO
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid
import zipfile

import httpx
import numpy as np
from PIL import Image

from .diagnostics import emit
from .errors import PublicError
from .media import probe
from .meta_credentials import meta_credential_version, server_meta_key
from .preparation_artifacts import preparation_prefix

API = "https://api.meta.ai/v1/responses"
MODEL = "sam-3.1"
PARSER_VERSION = "0.2.0"
VIDEO_FRAME_USD = .0002
IMAGE_USD = .0025
MAX_INPUT = 50_000_000
MAX_STREAM = 64 * 1024 * 1024
_cache_context = ContextVar("meta_strict_raster_cache", default=None)
_codec_lock = threading.Lock()
_strict_codec = None


def meta_preparation_snapshot():
    return {"sam_provider": "meta", "meta_model": MODEL,
            "meta_credential_version": meta_credential_version(), "meta_max_api_cost_usd": .16}


class _RasterCache:
    def __init__(self, codec, maximum=16 * 1024 * 1024, max_pixels=854*480):
        self.codec, self.maximum, self.max_pixels = codec, maximum, max_pixels
        self.items, self.bytes, self.decodes, self.hits = OrderedDict(), 0, 0, 0

    def decode(self, mask):
        if (type(mask.width) is not int or type(mask.height) is not int
                or min(mask.width, mask.height) < 1 or mask.width * mask.height > self.max_pixels):
            raise PublicError("Meta's native mask exceeds the source frame bound.", 502, code="meta_mask_invalid")
        key = (mask.encoding, mask.width, mask.height, mask.payload)
        if key in self.items:
            self.hits += 1
            self.items.move_to_end(key)
            return self.items[key]
        # This still performs all original canonical decode/re-encode checks.
        raster = self.codec(mask)
        self.decodes += 1
        size = len(raster) + len(mask.payload)
        if size <= self.maximum:
            while self.items and (self.bytes + size > self.maximum or len(self.items) >= 2048):
                old, value = self.items.popitem(last=False)
                self.bytes -= len(value) + len(old[3])
            self.items[key] = raster
            self.bytes += size
        return raster


def _cached_strict_decode(mask):
    cache = _cache_context.get()
    return cache.decode(mask) if cache is not None else _strict_codec(mask)


def _parser():
    """Pin the private validation seam; never mutate it per request/thread.

    Version 0.2.0 discards the raster after strict validation. A single installed
    dispatcher preserves that validation and gives each async request its own
    bounded cache. No validation, confidence, resolution or model setting changes.
    """
    global _strict_codec
    try:
        if package_version("meta-sam-parser") != PARSER_VERSION:
            raise ValueError
        import meta_sam_parser as parser
        from meta_sam_parser import _segmentation
        with _codec_lock:
            if _strict_codec is None:
                if _segmentation.decode_mask_to_raster is not parser.decode_mask_to_raster:
                    raise ValueError
                _strict_codec = parser.decode_mask_to_raster
                _segmentation.decode_mask_to_raster = _cached_strict_decode
            elif _segmentation.decode_mask_to_raster is not _cached_strict_decode:
                raise ValueError
        return parser
    except Exception:
        raise PublicError("The pinned Meta mask parser is unavailable. No segmentation was submitted.",
                          503, code="meta_parser_unavailable") from None


def _metadata(metadata, video):
    width, height = metadata.get("width"), metadata.get("height")
    frames = metadata.get("frames", 1) if video else 1
    if (any(type(v) is not int for v in (width, height, frames)) or not 1 <= frames <= 720
            or not 1 <= width <= 854 or not 1 <= height <= 480):
        raise PublicError("Meta segmentation needs the unchanged bounded SAM timeline.", code="meta_input_invalid")
    return width, height, frames


def _seeds(seeds, width, height, frames):
    if not isinstance(seeds, list) or not 1 <= len(seeds) <= 150:
        raise PublicError("Meta needs validated full-person seeds.", code="meta_seed_invalid")
    result, seen = [], set()
    for seed in seeds:
        label, frame, box, points = (seed.get(k) for k in ("label", "frame", "box", "points")) if isinstance(seed, dict) else (None,) * 4
        if (type(label) is not int or not 1 <= label <= 5 or type(frame) is not int or not 0 <= frame < frames
                or not isinstance(box, (list, tuple)) or len(box) != 4 or any(type(v) is not int for v in box)
                or not 0 <= box[0] <= box[2] < width or not 0 <= box[1] <= box[3] < height
                or not isinstance(points, (list, tuple)) or not 1 <= len(points) <= 5 or (frame, label) in seen):
            raise PublicError("A Meta full-person seed is invalid.", code="meta_seed_invalid")
        for point in points:
            if (not isinstance(point, (list, tuple)) or len(point) != 2 or any(type(v) is not int for v in point)
                    or not box[0] <= point[0] <= box[2] or not box[1] <= point[1] <= box[3]):
                raise PublicError("A Meta seed point is outside its full-person box.", code="meta_seed_invalid")
        result.append({"label": label, "frame": frame, "box": list(box), "points": [list(p) for p in points]})
        seen.add((frame, label))
    return sorted(result, key=lambda s: (s["frame"], s["label"]))


def _prompt(seeds, width, height):
    grouped = OrderedDict()
    for seed in seeds:
        x1, y1, x2, y2 = seed["box"]
        text = f"{seed['label']}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={width};h={height}|>"
        text += "".join(f"<|point;x={x};y={y};w={width};h={height}|>" for x, y in seed["points"])
        grouped.setdefault(seed["frame"], []).append(text)
    return "\n".join(f"<{frame}f>" + ",".join(items) for frame, items in grouped.items())


def _project(raster, mask, bounds, width, height):
    values = [bounds.left, bounds.top, bounds.right, bounds.bottom]
    if (any(not math.isfinite(v) or not float(v).is_integer() for v in values)
            or not 0 <= values[0] < values[2] <= width or not 0 <= values[1] < values[3] <= height):
        raise PublicError("Meta returned mask bounds outside the source frame.", 502, code="meta_mask_invalid")
    left, top, right, bottom = map(int, values)
    crop = np.frombuffer(raster, dtype=np.uint8).reshape(mask.height, mask.width)
    xs = np.minimum(mask.width - 1, ((2 * np.arange(right-left) + 1) * mask.width) // (2 * (right-left)))
    ys = np.minimum(mask.height - 1, ((2 * np.arange(bottom-top) + 1) * mask.height) // (2 * (bottom-top)))
    full = np.zeros((height, width), dtype=np.uint8)
    full[top:bottom, left:right] = crop[ys[:, None], xs[None, :]]
    return full


def _archive_content_digest(path):
    """Compare immutable members independently of ZIP timestamps/compression."""
    digest = hashlib.sha256()
    with zipfile.ZipFile(path) as archive:
        names = sorted(archive.namelist())
        if len(names) != len(set(names)):
            raise PublicError("Meta returned duplicate mask archive members.", 502, code="meta_mask_invalid")
        for name in names:
            data = archive.read(name)
            digest.update(name.encode())
            digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


class MetaPreparation:
    def __init__(self, records, cloud, job, http=None):
        self.records, self.cloud, self.job = records, cloud, job
        self.path = f"jobs/{job['id']}.json"
        self.http = http or httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
        if job.get("meta_model", MODEL) != MODEL:
            raise PublicError("This job's Meta model is unavailable.", 503)

    def _state(self, operation):
        current, _ = self.records.read(self.path)
        return (current or {}).get("meta_preparation", {}).get(operation, {})

    def _update(self, operation, **fields):
        def update(current):
            if not current:
                raise PublicError("This preparation job is unavailable.", 404)
            states = dict(current.get("meta_preparation", {}))
            states[operation] = {**states.get(operation, {}), **fields}
            return {**current, "meta_preparation": states, "updated_at": time.time()}
        self.records.update(self.path, update)

    def _usage(self, state, frames, video):
        usage = state.get("usage", {})
        billed = usage.get("video_frames_processed") if video else usage.get("images_processed")
        billed = billed if type(billed) is int and 0 <= billed <= 1_000_000 else None
        count = max(50, billed if billed is not None else frames) if video else billed if billed is not None else 1
        return {"provider": "meta", "model": MODEL, "expected_frames": frames,
            "billed_frames": billed if video else None, "billed_images": billed if not video else None,
            "estimated_cost_usd": round(count * (VIDEO_FRAME_USD if video else IMAGE_USD), 8),
            "cost_status": "reported_frames_estimate" if billed is not None else "published_estimate",
            "currency": "USD", "response_id": state.get("response_id"),
            "stream_seconds": state.get("stream_seconds"), "decode_seconds": state.get("decode_seconds")}

    def _record_cost(self, operation, state):
        from .finance import record_expense
        usage = self._usage(state, state["expected_frames"], state["mode"] == "video")
        confirmed = state.get("stream_complete") is True
        record_expense(self.records, "Meta", state["finance_reference"],
            at=state.get("submitted_at"), cost=usage["estimated_cost_usd"] if confirmed else None,
            status="estimate" if confirmed else "unavailable", job_id=self.job["id"],
            detail=f"SAM 3.1 {state['mode']} segmentation ({operation}); "
                + ("reported frames × published rate" if usage.get("billed_frames") is not None else "published per-request/frame estimate"))

    def _checkpoint(self, operation, state, root, pending):
        if not pending:
            return
        index = len(state["chunks"])
        path = root / f"events-{index:05d}.ndjson"
        data = "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in pending).encode()
        path.write_bytes(data)
        uri = self.cloud.upload(self.cloud.settings.worker_bucket,
            state["prefix"] + path.name, path, "application/x-ndjson")
        state["chunks"].append({"uri": uri, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        self._update(operation, chunks=list(state["chunks"]), response_id=state.get("response_id"),
                     received_events=state["received_events"])
        pending.clear()

    def _capture(self, operation, path, metadata, payload, identity, remaining, root):
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        state = self._state(operation)
        if state:
            if state.get("fingerprint") != fingerprint or state.get("model") != MODEL:
                raise PublicError("The saved Meta request differs from this prepared input.", 409, code="meta_request_mismatch")
            if state.get("stream_complete") is not True:
                raise PublicError("The previous Meta stream is incomplete or unconfirmed. No new paid request was sent.",
                                  409, code="meta_submission_unknown")
            return state
        _parser()  # Never pay when the pinned local decoder cannot run.
        seconds = min(600, math.floor(remaining()))
        if seconds < 10:
            raise PublicError("There is not enough preparation time for a new Meta request.", code="meta_timeout")
        credential = self.job.get("meta_credential_version")
        if credential is None:
            credential = meta_credential_version()
        key = server_meta_key(credential)
        if not key:
            raise PublicError("Meta preparation requires an API key.", 503)
        attempt = uuid.uuid4().hex
        prefix = preparation_prefix(self.job, "worker") + f"meta/{operation}/{attempt}/"
        initial = {"attempt": attempt, "fingerprint": fingerprint, "model": MODEL,
            "credential_version": credential, "status": "submitting", "submitted_at": time.time(),
            "prefix": prefix, "chunks": [], "received_events": 0,
            "source_sha256": identity["sha256"], "expected_frames": metadata["frames"],
            # Keep the exact generation guidance even when the paid stream or
            # local decoder fails. Never persist the data URL, input bytes or key.
            "request_parameters": {"model": MODEL, "prompt": identity["prompt"],
                "metadata": dict(payload["metadata"]), "width": metadata["width"],
                "height": metadata["height"], "frames": metadata["frames"],
                "stream": True, "store": False, "adapter_version": identity["adapter_version"]},
            "mode": identity["mode"],
            "estimated_cost_usd": round(max(50, metadata["frames"]) * VIDEO_FRAME_USD
                if identity["mode"] == "video" else IMAGE_USD, 8),
            "budget_family": "primary" if operation.startswith(("sam_seed_", "sam_primary_")) else "other"}
        initial["finance_reference"] = f"sam:{self.job['id']}:{operation}:{attempt}"
        def reserve(current):
            if not current:
                raise PublicError("This preparation job is unavailable.", 404)
            states = dict(current.get("meta_preparation", {}))
            if operation in states:
                raise PublicError("This Meta operation has already been reserved.", 409, code="meta_submission_unknown")
            if initial["budget_family"] == "primary":
                costs = [item.get("estimated_cost_usd") for item in states.values()
                         if isinstance(item, dict) and item.get("budget_family") == "primary"]
                if any(type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0 for cost in costs):
                    raise PublicError("The previous Meta usage reservation needs operator review.", code="meta_budget_exceeded")
                spent = sum(costs)
                limit = self.job.get("meta_max_api_cost_usd", .16)
                if (type(limit) not in (int, float) or not math.isfinite(limit) or not 0 < limit <= .16
                        or spent + initial["estimated_cost_usd"] > limit + 1e-10):
                    raise PublicError("This preparation has reached its Meta API usage budget.", code="meta_budget_exceeded")
            states[operation] = initial
            return {**current, "meta_preparation": states}
        self.records.update(self.path, reserve)
        state, pending = {**initial, "chunks": []}, []
        self._record_cost(operation, state)
        started, last_flush, total = time.monotonic(), time.monotonic(), 0
        terminal = None
        emit("meta_segmentation_submitting", job_id=self.job["id"], operation=operation,
             model=MODEL, expected_frames=metadata["frames"], mode=identity["mode"])
        try:
            with self.http.stream("POST", API, json=payload,
                headers={"Authorization": "Bearer " + key, "Accept": "text/event-stream"},
                timeout=seconds) as response:
                if response.status_code != 200 or "text/event-stream" not in response.headers.get("content-type", "").lower():
                    self._update(operation, status="http_error", http_status=response.status_code)
                    raise PublicError("Meta did not accept the segmentation request. No automatic retry was submitted.",
                                      502, code="meta_preparation_failed")
                lines, event_bytes = [], 0
                for line in response.iter_lines():
                    remaining()
                    total += len(line.encode()) + 1
                    event_bytes += len(line.encode()) + 1
                    if total > MAX_STREAM or event_bytes > 8 * 1024 * 1024:
                        raise ValueError("Stream exceeds bounded size")
                    if line.startswith("data:"):
                        lines.append(line[5:].lstrip(" "))
                    if line or not lines:
                        continue
                    raw, lines, event_bytes = "\n".join(lines), [], 0
                    if raw == "[DONE]":
                        continue
                    event = json.loads(raw)
                    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                        raise ValueError("Invalid SSE event")
                    pending.append(event)
                    state["received_events"] += 1
                    body = event.get("response")
                    if isinstance(body, dict):
                        response_id = body.get("id")
                        if isinstance(response_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", response_id):
                            state["response_id"] = response_id
                        usage = body.get("usage")
                        if isinstance(usage, dict):
                            state["usage"] = {k: v for k, v in usage.items()
                                if k in {"video_frames_processed", "images_processed"} and type(v) is int and 0 <= v <= 1_000_000}
                    kind = event["type"]
                    if kind in {"response.completed", "response.failed", "response.incomplete", "error"}:
                        terminal = kind
                    if len(pending) >= 32 or time.monotonic() - last_flush >= 5 or terminal:
                        self._checkpoint(operation, state, root, pending)
                        last_flush = time.monotonic()
                    if terminal:
                        break
            self._checkpoint(operation, state, root, pending)
            elapsed = round(time.monotonic() - started, 6)
            self._update(operation, status="stream_saved" if terminal == "response.completed" else "stream_incomplete",
                stream_complete=terminal == "response.completed", terminal_event=terminal,
                usage=state.get("usage", {}), stream_seconds=elapsed, response_id=state.get("response_id"), stream_bytes=total)
            self._record_cost(operation, self._state(operation))
            emit("meta_segmentation_stream_saved", job_id=self.job["id"], operation=operation,
                 expected_frames=metadata["frames"], events=state["received_events"], elapsed_seconds=elapsed,
                 terminal_event=terminal, **{k: v for k, v in self._usage(state, metadata["frames"], identity["mode"] == "video").items()
                    if k in {"estimated_cost_usd", "cost_status", "billed_frames", "billed_images"}})
            if terminal != "response.completed":
                raise PublicError("Meta segmentation did not complete. Saved stream data is retained; no new paid request was sent.",
                                  502, code="meta_stream_incomplete")
            return self._state(operation)
        except PublicError:
            raise
        except Exception as error:
            # A lost POST/stream is ambiguous. Keep the intent forever; no replacement POST.
            try:
                self._checkpoint(operation, state, root, pending)
                self._update(operation, status="submission_unknown", error_type=type(error).__name__)
            except Exception:
                pass
            emit("meta_segmentation_unconfirmed", job_id=self.job["id"], operation=operation, error_type=type(error).__name__)
            raise PublicError("The Meta stream could not be confirmed. Your inputs are saved; no duplicate request was sent.",
                              502, code="meta_submission_unknown") from None

    async def _decode(self, operation, state, root, metadata, seeds, remaining, video):
        parser = _parser()
        width, height, frames = _metadata(metadata, video)
        cache = _RasterCache(_strict_codec, max_pixels=width*height)
        token = _cache_context.set(cache)
        selected = {s["label"] for s in seeds} if seeds is not None else None
        remap, images, seen, emitted, revisions = {}, [], 0, 0, {}
        decoded = root / "decoded"
        decoded.mkdir(exist_ok=True)
        async def events():
            chunks = state.get("chunks")
            if not isinstance(chunks, list) or not 1 <= len(chunks) <= 10000:
                raise PublicError("The saved Meta stream is unavailable.", 502, code="meta_stream_invalid")
            for index, chunk in enumerate(chunks):
                remaining()
                path = root / f"replay-{index:05d}.ndjson"
                if not isinstance(chunk, dict) or not str(chunk.get("uri", "")).startswith(
                        f"gs://{self.cloud.settings.worker_bucket}/{state['prefix']}"):
                    raise PublicError("The saved Meta stream is outside this preparation.", 502, code="meta_stream_invalid")
                self.cloud.download(chunk["uri"], path, 8 * 1024 * 1024)
                data = path.read_bytes()
                if len(data) != chunk.get("bytes") or hashlib.sha256(data).hexdigest() != chunk.get("sha256"):
                    raise PublicError("A saved Meta stream chunk changed.", 502, code="meta_stream_invalid")
                for line in data.splitlines():
                    if line:
                        yield json.loads(line)
        parsed = parser.parse_responses_stream(events(), parser.video_segmentation_format() if video else parser.image_segmentation_format())
        started = time.monotonic()
        try:
            async for snapshot in parsed:
                remaining()
                for record in snapshot.records[seen:]:
                    if record.kind != "mask":
                        continue
                    frame = record.frame.frame_index if record.frame is not None else 0
                    if not 0 <= frame < frames or (video and record.frame is None):
                        raise PublicError("Meta returned a mask outside the source timeline.", 502, code="meta_mask_invalid")
                    object_id = record.object_id
                    if not isinstance(object_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", object_id):
                        raise PublicError("Meta returned an invalid person identity.", 502, code="meta_mask_invalid")
                    if selected is not None:
                        if object_id not in {str(label) for label in selected}:
                            raise PublicError("Meta returned an unselected person identity.", 502, code="meta_mask_conflict")
                        label = int(object_id)
                    else:
                        label = remap.setdefault(object_id, len(remap) + 1)
                        if label > (65535 if video else 512):
                            raise PublicError("Meta returned too many person masks.", 502, code="meta_mask_invalid")
                    full = _project(cache.decode(record.mask), record.mask, record.bounds, width, height)
                    box = [int(record.bounds.left), int(record.bounds.top), int(record.bounds.right)-1, int(record.bounds.bottom)-1]
                    if video:
                        folder = decoded / str(label)
                        folder.mkdir(exist_ok=True)
                        Image.fromarray(full).save(folder / f"{frame:05d}.png")
                        revisions[(frame, label)] = record.revision
                    else:
                        images.append({"id": object_id, "box": box, "mask": full.astype(bool)})
                    emitted += 1
                seen = len(snapshot.records)
                if emitted and emitted % 48 == 0:
                    emit("meta_mask_decode_progress", job_id=self.job["id"], operation=operation, masks=emitted,
                         elapsed_seconds=round(time.monotonic()-started, 3))
            result = await parsed.final_result()
            if result.outcome.status != "completed" or any(d.severity == "error" for d in result.diagnostics):
                raise PublicError("Meta's mask stream failed strict validation.", 502, code="meta_mask_invalid")
            seconds = round(time.monotonic() - started, 6)
            self._update(operation, decode_seconds=seconds, decoded_masks=emitted, codec_decodes=cache.decodes,
                         codec_cache_hits=cache.hits, parser_version=PARSER_VERSION)
            emit("meta_masks_decoded", job_id=self.job["id"], operation=operation, masks=emitted,
                 elapsed_seconds=seconds, codec_decodes=cache.decodes, codec_cache_hits=cache.hits)
            if not video:
                return images
            labels = sorted(selected if selected is not None else remap.values())
            for seed in seeds or []:
                path = decoded / str(seed["label"]) / f"{seed['frame']:05d}.png"
                if not path.exists():
                    raise PublicError("Meta did not return a mask for the selected seed frame.", code="meta_seed_coverage")
                mask = np.asarray(Image.open(path)) > 0
                if not all(mask[y, x] for x, y in seed["points"]):
                    raise PublicError("Meta's mask missed the selected person on the seed frame.", code="meta_seed_coverage")
            output = root / "masks.zip"
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for frame in range(frames):
                    remaining()
                    mapped = np.zeros((height, width), dtype=np.uint16)
                    for label in sorted(label for index, label in revisions if index == frame):
                        path = decoded / str(label) / f"{frame:05d}.png"
                        if not path.exists():
                            continue  # An explicitly absent person has a zero mask for this frame.
                        mask = np.asarray(Image.open(path)) > 0
                        if selected is not None and np.any(mask & (mapped != 0)):
                            raise PublicError("Meta's selected person masks overlap. Review the scene before rendering.",
                                              code="meta_mask_conflict")
                        # Explicit replace-all concept mode has one replacement
                        # for every instance. Preserve the exact union; the
                        # arbitrary label on an overlap has no identity meaning.
                        mapped[mask & (mapped == 0)] = label
                    buffer = BytesIO()
                    Image.fromarray(mapped).save(buffer, "PNG")
                    archive.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
                archive.writestr("objects.json", json.dumps({"width": width, "height": height, "frames": frames,
                    "fps": 24, "objects": [{"label": label, "meta_id": next((k for k,v in remap.items() if v == label), str(label))} for label in labels]}))
            return output
        except PublicError:
            raise
        except Exception as error:
            # The official adapter wraps source exceptions; preserve our safe
            # storage/deadline error without exposing provider payloads.
            cause = error
            for _ in range(8):
                if isinstance(cause, PublicError):
                    raise cause from None
                cause = getattr(cause, "__cause__", None)
                if cause is None:
                    break
            emit("meta_mask_decode_failed", job_id=self.job["id"], operation=operation, error_type=type(error).__name__)
            raise PublicError("Meta's mask stream could not be decoded safely. Saved responses are retained for recovery.",
                              502, code="meta_mask_invalid") from None
        finally:
            try:
                await parsed.aclose()
            except Exception:
                pass
            _cache_context.reset(token)

    def _segment(self, operation, path, metadata, remaining, *, video, seeds=None, propagation_direction="both", concept=None):
        if not isinstance(operation, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", operation):
            raise PublicError("Invalid Meta operation identity.")
        width, height, frames = _metadata(metadata, video)
        path = Path(path)
        if not path.is_file() or not 0 < path.stat().st_size < MAX_INPUT:
            raise PublicError("The prepared Meta input exceeds its size bound.", code="meta_input_invalid")
        if video:
            checked = probe(path)
            stream = next((s for s in checked.get("streams", []) if s.get("codec_type") == "video"), {})
            try:
                valid = (stream.get("width") == width and stream.get("height") == height
                    and int(stream.get("nb_frames", 0)) == frames and stream.get("r_frame_rate") == "24/1"
                    and abs(float(checked["format"]["duration"]) - frames/24) <= .1)
            except (ValueError, TypeError, KeyError):
                valid = False
            if not valid:
                raise PublicError("Meta input differs from the prepared source timeline.", code="meta_input_invalid")
            if seeds:
                seeds = _seeds(seeds, width, height, frames)
                if concept is not None:
                    raise PublicError("Selected Meta seeds cannot be combined with concept detection.", code="meta_seed_invalid")
                prompt = _prompt(seeds, width, height)
            else:
                replace_all = self.job.get("replace_all_people") is True or (self.job.get("setup") or {}).get("replace_all_people") is True
                if not replace_all or concept not in (None, "person"):
                    raise PublicError("Only replace-all mode can request all-person Meta detection.", code="meta_seed_invalid")
                seeds, prompt = None, "person"
            if propagation_direction not in {"forward", "backward", "both"}:
                raise PublicError("Invalid Meta propagation direction.")
            mime, media_type = "video/mp4", "input_video"
            limit = self.job.get("meta_max_api_cost_usd", .16)
            if (type(limit) not in (int, float) or not math.isfinite(limit) or not 0 < limit <= .16
                    or max(50, frames) * VIDEO_FRAME_USD > limit + 1e-10):
                raise PublicError("Meta segmentation exceeds this job's API usage limit.", code="meta_budget_exceeded")
        else:
            try:
                with Image.open(path) as image:
                    if image.size != (width, height) or image.format not in {"PNG", "JPEG"}:
                        raise ValueError
                    image.verify()
                    mime = "image/png" if image.format == "PNG" else "image/jpeg"
            except Exception:
                raise PublicError("Meta seed image geometry changed or its bytes are invalid.", code="meta_input_invalid") from None
            prompt, media_type = "person", "input_image"
        raw = path.read_bytes()
        identity = {"sha256": hashlib.sha256(raw).hexdigest(), "width": width, "height": height,
            "frames": frames, "prompt": prompt, "model": MODEL, "mode": "video" if video else "image",
            "propagation_direction": propagation_direction if video else None, "mask_encoding": "one_bit",
            "adapter_version": "meta-preparation-v1"}
        metadata = {**metadata, "width": width, "height": height, "frames": frames}
        payload = {"model": MODEL, "stream": True, "store": False,
            "metadata": {"mask_encoding": "one_bit", "include_confidence": "true"},
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": media_type, "video_url" if video else "image_url": "data:"+mime+";base64,"+base64.b64encode(raw).decode()}]}]}
        if video:
            payload["metadata"]["propagation_direction"] = propagation_direction
        root = path.parent / ("meta-" + operation + "-" + identity["sha256"][:12])
        root.mkdir(exist_ok=True)
        state = self._capture(operation, path, metadata, payload, identity, remaining, root)
        self._record_cost(operation, state)
        if video and state.get("asset_verified") is True and state.get("asset_sha256"):
            from .mask_geometry import resize_label_archive
            expected_uri = f"gs://{self.cloud.settings.worker_bucket}/{state['prefix']}masks.zip"
            if state.get("asset_uri") != expected_uri:
                raise PublicError("The saved Meta masks are outside this operation.", 502, code="meta_mask_invalid")
            if self.cloud.asset_exists(expected_uri):
                cached = root / "masks.zip"
                self.cloud.download(expected_uri, cached, 80 * 1024 * 1024)
                if hashlib.sha256(cached.read_bytes()).hexdigest() != state["asset_sha256"]:
                    raise PublicError("The saved Meta mask archive changed.", 502, code="meta_mask_invalid")
                checked = root / "cached-validated.zip"
                resize_label_archive(cached, checked, metadata)
                checked.unlink()
                return {"masks_uri": expected_uri, "local_masks": cached, "usage": self._usage(state, frames, True)}
        masks = asyncio.run(self._decode(operation, state, root, metadata, seeds, remaining, video))
        if not video:
            return {"masks": masks, "usage": self._usage(self._state(operation), frames, False)}
        state = self._state(operation)
        uri = f"gs://{self.cloud.settings.worker_bucket}/{state['prefix']}masks.zip"
        if self.cloud.asset_exists(uri):
            # An upload can succeed while its acknowledgement is lost. Never
            # overwrite it, and do not compare nondeterministic ZIP timestamps.
            saved = root / "previously-uploaded-masks.zip"
            self.cloud.download(uri, saved, 80 * 1024 * 1024)
            if _archive_content_digest(saved) != _archive_content_digest(masks):
                raise PublicError("The existing Meta mask artifact differs from its saved stream.",
                                  502, code="meta_mask_invalid")
            masks.write_bytes(saved.read_bytes())
        else:
            uri = self.cloud.upload(self.cloud.settings.worker_bucket, state["prefix"] + "masks.zip", masks, "application/zip")
        self._update(operation, status="completed", asset_uri=uri, asset_verified=True,
                     asset_sha256=hashlib.sha256(masks.read_bytes()).hexdigest(), asset_bytes=masks.stat().st_size)
        return {"masks_uri": uri, "local_masks": masks, "usage": self._usage(self._state(operation), frames, True)}

    def segment_image(self, operation, image_path, metadata, remaining):
        return self._segment(operation, image_path, metadata, remaining, video=False)

    def segment_video(self, operation, video_path, metadata, seeds, remaining, *, propagation_direction="both", concept=None):
        return self._segment(operation, video_path, metadata, remaining, video=True, seeds=seeds,
                             propagation_direction=propagation_direction, concept=concept)
