"""Versioned Replicate SAM/depth adapter for the existing preparation pipeline.

Only the model inference changes. Our normalized timeline, discrete object IDs,
selection/review, audio, compositor, and export remain the common implementation.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import uuid

import httpx

from .diagnostics import emit, timed
from .errors import PublicError
from .mask_geometry import resize_label_archive
from .media import probe
from .preparation_credentials import replicate_credential_version, server_replicate_key

API = "https://api.replicate.com/v1"
# Community wrappers require explicit immutable versions in predictions.create.
MODELS = {
    "sam": "8digit/gener8-sam3:a6f50615da4cfa8b03fe95ec56681bc252a5e60c2ed368938f72bfa4f55dfcd1",
    "depth": "lucataco/depth-anything-video:9143691405afc64c7952499a1e81e3f779535a8916c8da7154a9995f145d5e6d",
}
TERMINAL = {"succeeded", "failed", "canceled", "aborted"}


def preparation_snapshot(provider, enhanced_masking=True):
    """Persist no token: only provider, model versions, and a scoped secret version."""
    if provider not in {"workers", "replicate"}:
        raise PublicError("Choose a supported preparation provider.")
    snapshot = {"preparation_provider": provider}
    if provider == "replicate" and enhanced_masking:
        snapshot.update(replicate_model_versions=dict(MODELS),
                        replicate_credential_version=replicate_credential_version())
    return snapshot


def delivery_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise PublicError("Replicate returned an invalid output asset.", 502)
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        valid = (parsed.scheme == "https" and not parsed.username and not parsed.password
                 and parsed.port in (None, 443)
                 and (host == "replicate.delivery" or host.endswith(".replicate.delivery"))
                 and bool(parsed.path) and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise PublicError("Replicate returned an unexpected output asset host.", 502)
    return value


class ReplicatePreparation:
    def __init__(self, records, cloud, job, *, http=None, sleep=time.sleep):
        self.records, self.cloud, self.job = records, cloud, job
        self.path = f"jobs/{job['id']}.json"
        self.http = http or httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
        self.sleep = sleep
        self.key = server_replicate_key(job.get("replicate_credential_version"))
        if not self.key:
            raise PublicError("Replicate preparation requires an API token. No processing was submitted.", 503)
        self.models = job.get("replicate_model_versions", MODELS)
        if (not isinstance(self.models, dict) or set(self.models) != set(MODELS)
                or any(not isinstance(self.models[k], str) or not re.fullmatch(
                    re.escape(MODELS[k].split(":")[0]) + r":[a-f0-9]{64}", self.models[k]) for k in MODELS)):
            raise PublicError("This job's Replicate model versions are unavailable.", 503)

    def _state(self, operation):
        current, _ = self.records.read(self.path)
        return (current or {}).get("replicate_predictions", {}).get(operation, {})

    def _update(self, operation, **fields):
        def update(current):
            if not current:
                raise PublicError("This preparation job is unavailable.", 404)
            predictions = dict(current.get("replicate_predictions", {}))
            predictions[operation] = {**predictions.get(operation, {}), **fields}
            return {**current, "replicate_predictions": predictions, "updated_at": time.time()}
        self.records.update(self.path, update)

    def _request(self, method, path, *, body=None, cancel_after=None):
        headers = {"Authorization": "Bearer " + self.key}
        if cancel_after is not None:
            headers["Cancel-After"] = f"{cancel_after}s"
        try:
            response = self.http.request(method, API + path, headers=headers, json=body, timeout=30)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
            return result
        except Exception:
            # Never include response bodies, signed input URLs, API tokens, or
            # provider traceback text in our public error or diagnostics.
            raise PublicError("Replicate preparation could not be confirmed. No replacement prediction was submitted.", 502) from None

    def predict(self, operation, inputs, input_identity, remaining):
        version = self.models[operation]
        fingerprint = hashlib.sha256(json.dumps([version, input_identity], sort_keys=True).encode()).hexdigest()
        state = self._state(operation)
        if state and (state.get("fingerprint") != fingerprint or state.get("version") != version):
            raise PublicError("This job's saved Replicate request does not match its prepared input.", 409)
        prediction_id = state.get("id")
        if state and not prediction_id:
            # An API POST has no documented idempotency key. A lost response is
            # ambiguous; a durable intent blocks another paid create on retry.
            raise PublicError("A Replicate submission is unconfirmed. Contact support before retrying this preparation.", 409)
        if prediction_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", prediction_id):
            raise PublicError("This job's Replicate prediction identity is invalid.", 502)
        if not prediction_id:
            seconds = min(600, math.floor(remaining()))
            if seconds < 5:
                raise PublicError("Preparation exceeded its bounded runtime. No new prediction was submitted.")
            attempt = uuid.uuid4().hex
            def reserve(current):
                if not current:
                    raise PublicError("This preparation job is unavailable.", 404)
                predictions = dict(current.get("replicate_predictions", {}))
                if operation in predictions:
                    raise PublicError("This preparation request has already been reserved.", 409)
                predictions[operation] = {"attempt": attempt, "version": version,
                    "fingerprint": fingerprint, "status": "submitting", "submitted_at": time.time()}
                return {**current, "replicate_predictions": predictions}
            self.records.update(self.path, reserve)
            result = self._request("POST", "/predictions", body={"version": version, "input": inputs}, cancel_after=seconds)
            prediction_id = result.get("id")
            if not isinstance(prediction_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", prediction_id):
                raise PublicError("Replicate did not return a recoverable prediction identity.", 502)
            try:
                self._update(operation, id=prediction_id, status=result.get("status", "starting"))
            except Exception:
                # The create succeeded but persisting its identity failed.
                # Bound the orphan immediately as well as by Cancel-After.
                try:
                    self._request("POST", "/predictions/" + prediction_id + "/cancel")
                except PublicError:
                    pass
                raise
            emit("replicate_preparation_submitted", job_id=self.job["id"], operation=operation,
                 prediction_id=prediction_id, model=version.split(":")[0])
        else:
            result = self._request("GET", "/predictions/" + prediction_id)
        try:
            while result.get("status") not in TERMINAL:
                if result.get("status") not in {"starting", "processing"}:
                    raise PublicError("Replicate returned an invalid prediction state.", 502)
                delay = min(3, remaining())
                self.sleep(delay)
                remaining()
                result = self._request("GET", "/predictions/" + prediction_id)
            self._update(operation, status=result["status"],
                predict_seconds=_metric(result, "predict_time"), total_seconds=_metric(result, "total_time"))
            if result["status"] != "succeeded":
                raise PublicError("Replicate preparation did not complete. No video generation was submitted.", 502)
            emit("replicate_preparation_completed", job_id=self.job["id"], operation=operation,
                 prediction_id=prediction_id, predict_seconds=_metric(result, "predict_time"),
                 total_seconds=_metric(result, "total_time"))
            return result.get("output")
        except Exception:
            if result.get("status") not in TERMINAL:
                try:
                    self._request("POST", "/predictions/" + prediction_id + "/cancel")
                    self._update(operation, cancellation_requested=True)
                except PublicError:
                    self._update(operation, cancellation_unconfirmed=True)
            raise

    @timed("replicate_download")
    def download(self, url, path, maximum):
        current = delivery_url(url)
        try:
            # Follow only validated delivery redirects and never forward the
            # bearer token to media hosts (including after a redirect).
            for _ in range(4):
                with self.http.stream("GET", current, timeout=60) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        current = delivery_url(response.headers.get("location", ""))
                        continue
                    response.raise_for_status()
                    declared = response.headers.get("content-length")
                    if declared is not None and (not declared.isdecimal() or not 0 < int(declared) <= maximum):
                        raise ValueError
                    length = 0
                    with path.open("wb") as handle:
                        for chunk in response.iter_bytes(256 * 1024):
                            length += len(chunk)
                            if length > maximum:
                                raise ValueError
                            handle.write(chunk)
                    if not length:
                        raise ValueError
                    return
            raise ValueError
        except Exception:
            path.unlink(missing_ok=True)
            raise PublicError("Replicate's prepared asset could not be saved safely. No new prediction was submitted.", 502) from None

    @timed("replicate_preparation")
    def prepare(self, root, metadata, sam_metadata, source_uri, sam_uri, remaining):
        saved = {}
        operations = [("sam", sam_uri, sam_metadata)]
        if self.job.get("depth_guidance", True):
            operations.append(("depth", source_uri, metadata))
        for operation, uri, working_metadata in operations:
            state = self._state(operation)
            identity = {"uri": uri, "metadata": working_metadata,
                "prompt": "person" if operation == "sam" else None,
                "encoder": "vits" if operation == "depth" else None}
            fingerprint = hashlib.sha256(json.dumps([self.models[operation], identity], sort_keys=True).encode()).hexdigest()
            if state and (state.get("fingerprint") != fingerprint or state.get("version") != self.models[operation]):
                raise PublicError("This job's saved Replicate request does not match its prepared input.", 409)
            asset_uri = state.get("asset_uri")
            if (asset_uri and state.get("asset_verified") is True
                    and state.get("version") == self.models[operation]
                    and self.cloud.asset_exists(asset_uri)):
                saved[operation] = asset_uri
                continue
            inputs = {"video": self.cloud.signed_url(uri, seconds=3600)}
            inputs.update({"prompt": "person", "preview": False} if operation == "sam" else {"encoder": "vits"})
            output = self.predict(operation, inputs, identity, remaining)
            if operation == "sam":
                if not isinstance(output, list) or not 1 <= len(output) <= 2:
                    raise PublicError("Replicate did not return individual tracking masks.", 502)
                archive, checked = root / "replicate-raw-masks.zip", root / "replicate-masks.zip"
                self.download(output[0], archive, 80 * 1024 * 1024)
                # Validate every frame before persisting trusted working labels.
                resize_label_archive(archive, checked, working_metadata)
                local, name, content_type = checked, "replicate-masks.zip", "application/zip"
            else:
                if not isinstance(output, str):
                    raise PublicError("Replicate did not return depth video.", 502)
                local = root / "replicate-depth.mp4"
                self.download(output, local, 80 * 1024 * 1024)
                validate_depth_media(local, working_metadata)
                name, content_type = "replicate-depth.mp4", "video/mp4"
            # Unique names also allow recovery after an upload succeeded but its
            # response was lost, without overwriting immutable source artifacts.
            asset_uri = self.cloud.upload(self.cloud.settings.worker_bucket,
                f"inputs/swapmein-{self.job['id']}/{uuid.uuid4().hex}-{name}", local, content_type)
            self._update(operation, asset_uri=asset_uri, asset_verified=True)
            saved[operation] = asset_uri
        return {"masks_uri": saved["sam"], "depth_uri": saved.get("depth")}


def _metric(result, key):
    value = result.get("metrics", {}).get(key)
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def validate_depth_media(path: Path, metadata):
    checked = probe(path)
    video = next((stream for stream in checked.get("streams", []) if stream.get("codec_type") == "video"), None)
    duration = float(checked.get("format", {}).get("duration", 0))
    if (not video or (video.get("width"), video.get("height")) != (metadata["width"], metadata["height"])
            or int(video.get("nb_frames", 0)) != metadata["frames"] or video.get("r_frame_rate") != "24/1"
            or not math.isfinite(duration) or abs(duration - metadata["duration"]) > .1):
        raise PublicError("Replicate depth changed the prepared frame dimensions or timeline.", 502)
