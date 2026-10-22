"""Scene-bounded Replicate recovery without starting the private GPU worker.

The pinned public wrapper accepts text, not point prompts. Rerun its person
detector on the selected original-RGB scene, select the actual label at the tap,
and retain coordinate fallback if it is still missing. Never invent a mask or
erase an existing selected person's pixels.
"""
from collections import defaultdict
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import tempfile
import time
import uuid
import zipfile

import numpy as np
from PIL import Image

from .diagnostics import emit, timed
from .errors import PublicError
from .mask_geometry import resize_label_archive
from .media import probe, run, sam_input_video
from .replicate_preparation import ReplicatePreparation
from .tracking import _maps


def _validated_groups(requests, metadata):
    if (not isinstance(requests, list) or not 1 <= len(requests) <= 300
            or any(not isinstance(item, dict) for item in requests)):
        raise PublicError("The saved mask-recovery selections are invalid.", 409)
    groups = defaultdict(list)
    work = 0
    for item in requests:
        start, end, frame, label = (item.get(key) for key in ("start_frame", "end_frame", "frame", "label"))
        if (not isinstance(item.get("segment_id"), str) or not 1 <= len(item["segment_id"]) <= 120
                or any(type(value) is not int for value in (start, end, frame, label))
                or not 0 <= start <= frame < end <= metadata["frames"] or not 1 <= label <= 5
                or any(type(item.get(key)) not in (int, float) or not math.isfinite(item[key])
                       or not 0 <= item[key] <= 1 for key in ("x", "y"))):
            raise PublicError("The saved mask-recovery selections are invalid.", 409)
        work += end - start
        groups[start, end].append(item)
    if len({item["segment_id"] for item in requests}) != len(requests):
        raise PublicError("The saved mask-recovery selections are invalid.", 409)
    if work > 1800 or len(groups) > 60:
        raise PublicError("Mask recovery exceeds the supported clip budget.", 409)
    return groups


def _scene_video(source, output, start, end, remaining):
    seconds = math.floor(remaining())
    if seconds < 1:
        raise PublicError("Mask recovery exceeded its bounded runtime.")
    # Frame selection operates on the already prepared 24 fps RGB timeline; seek
    # timestamps can otherwise include one frame from the neighbouring shot.
    run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file,pipe",
         "-i", str(source), "-map", "0:v:0", "-an", "-vf",
         f"trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS",
         "-c:v", "libx264", "-threads", "2", "-crf", "18", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(output)], timeout=min(90, seconds))
    video = next((stream for stream in probe(output).get("streams", []) if stream.get("codec_type") == "video"), None)
    if (not video or video.get("r_frame_rate") != "24/1"
            or int(video.get("nb_frames", 0)) != end - start):
        raise PublicError("The recovery scene changed the prepared frame timeline.", 502)


def _scene_candidates(adapter, root, source, job, metadata, start, end, remaining):
    identity = {"normalized_uri": job["normalized_uri"], "start_frame": start, "end_frame": end,
                "width": metadata["width"], "height": metadata["height"], "fps": 24,
                "prompt": "person", "preview": False, "recovery_method": "scene_text_v1"}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    operation = "sam_recovery_" + digest
    state = adapter._state(operation)
    scene_metadata = {**metadata, "frames": end - start, "duration": (end - start) / 24,
                      "anchor": {"frame": 0}, "scene_cuts": []}
    checked = root / (operation + "-checked.zip")
    if state.get("asset_uri") and state.get("asset_verified") is True:
        # Predict validates the immutable input fingerprint before using cache.
        # For an existing verified asset avoid both a new API create and a GET.
        version = adapter.models["sam"]
        fingerprint = hashlib.sha256(json.dumps([version, identity], sort_keys=True).encode()).hexdigest()
        if state.get("version") != version or state.get("fingerprint") != fingerprint:
            raise PublicError("The saved Replicate recovery does not match its original scene.", 409)
        if adapter.cloud.asset_exists(state["asset_uri"]):
            adapter.cloud.download(state["asset_uri"], checked, 80 * 1024 * 1024)
            with _maps(checked, scene_metadata) as read:
                for frame in range(end - start):
                    read(frame)
            adapter.release_input(operation)
            return checked, scene_metadata
    inputs = {}
    if not state:
        scene = root / (operation + ".mp4")
        _scene_video(source, scene, start, end, remaining)
        inputs = {"video": adapter.upload_video(operation, scene, remaining), "prompt": "person", "preview": False}
    try:
        output = adapter.predict(operation, inputs, identity, remaining, model_operation="sam")
        if not isinstance(output, list) or not 1 <= len(output) <= 2:
            raise PublicError("Replicate did not return individual recovery masks.", 502)
        raw = root / (operation + "-raw.zip")
        adapter.download(output[0], raw, 80 * 1024 * 1024)
        resize_label_archive(raw, checked, scene_metadata)
        uri = adapter.cloud.upload(adapter.cloud.settings.worker_bucket,
            f"inputs/swapmein-{job['id']}/{uuid.uuid4().hex}-{operation}.zip", checked, "application/zip")
        adapter._update(operation, asset_uri=uri, asset_verified=True, recovery_method="scene_text_v1")
        return checked, scene_metadata
    finally:
        adapter.release_input(operation)


def _merge_scene(source, candidate, output, metadata, scene_metadata, start, end, requests, remaining):
    reports, accepted = [], {}
    width, height = metadata["width"], metadata["height"]
    with _maps(source, metadata) as saved, _maps(candidate, scene_metadata) as read:
        for item in requests:
            before = time.monotonic()
            px, py = min(width - 1, math.floor(item["x"] * width)), min(height - 1, math.floor(item["y"] * height))
            selected = int(read(item["frame"] - start)[py, px])
            outcome = "coordinate_fallback" if selected == 0 else "recovered"
            if outcome == "recovered":
                if selected in accepted and accepted[selected] != item["label"]:
                    outcome = "conflict"
                else:
                    for frame in range(start, end):
                        remaining()
                        mask, existing = read(frame - start) == selected, saved(frame)
                        if np.any(mask & (existing != 0) & (existing != item["label"])):
                            outcome = "conflict"
                            break
            if outcome == "recovered":
                accepted[selected] = item["label"]
            reports.append({"segment_id": item["segment_id"], "outcome": outcome,
                            "seconds": round(time.monotonic() - before, 3), "method": "scene_text_v1"})
        coverage = []
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for frame in range(metadata["frames"]):
                remaining()
                labels = saved(frame).astype(np.uint16)
                if start <= frame < end and accepted:
                    detected = read(frame - start)
                    for raw_label, target_label in accepted.items():
                        labels[detected == raw_label] = target_label
                buffer = BytesIO()
                Image.fromarray(labels).save(buffer, "PNG")
                archive.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
                coverage.append(int(np.count_nonzero(labels)))
    return reports, coverage


@timed("replicate_mask_recovery", job_arg="job")
def run_replicate_mask_recovery(pipeline, job, requests, remaining):
    metadata, cloud = job["tracking_metadata"], pipeline.cloud
    groups = _validated_groups(requests, metadata)
    cloud.check_cap()
    pipeline.update_job(job["id"], message="Recovering missing person masks from your saved selections.")
    emit("mask_recovery_started", job_id=job["id"], section_count=len(requests),
         revision=job.get("tracking_applied_revision"), entry_point=job.get("recovery_entry_point", "scene_review"),
         provider="replicate", recovery_method="scene_text_v1")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source, bounded, selected = root / "original.mp4", root / "sam.mp4", root / "selected.zip"
        cloud.download(job["reviewed_masks_uri"], selected, 80 * 1024 * 1024)
        with _maps(selected, metadata) as read:
            for frame in range(metadata["frames"]):
                remaining()
                if np.any(read(frame) > 5):
                    raise PublicError("Recovery needs the saved selected-person masks.", 409)
        cloud.download(job["normalized_uri"], source, 100 * 1024 * 1024)
        sam_input_video(source, bounded)
        remaining()
        adapter = ReplicatePreparation(pipeline.records, cloud, job)
        reports = []
        try:
            for index, ((start, end), items) in enumerate(groups.items()):
                remaining()
                candidate, scene_metadata = _scene_candidates(adapter, root, bounded, job, metadata, start, end, remaining)
                merged = root / f"merged-{index}.zip"
                items_report, coverage = _merge_scene(selected, candidate, merged, metadata,
                                                       scene_metadata, start, end, items, remaining)
                reports.extend(items_report)
                selected = merged
            uri = cloud.upload(pipeline.settings.worker_bucket,
                f"inputs/swapmein-{job['id']}/recovery-{uuid.uuid4().hex}/reviewed-masks.zip", selected, "application/zip")
        finally:
            adapter.http.close()
    ordered = {item["segment_id"]: item for item in reports}
    return uri, coverage, [ordered[item["segment_id"]] for item in requests]
