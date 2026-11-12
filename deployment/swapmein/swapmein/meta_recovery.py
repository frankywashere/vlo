"""Recover only reviewed scene/person slots with Meta full-person prompts.

Each source scene has its own immutable seed/video operation.  The existing
durable Meta adapter prevents a lost reply from becoming another paid request.
The aggregate scene+image budget is checked before the first new API request.
"""
from io import BytesIO
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
from .media import sam_input_video
from .meta_seeds import SEED_VERSION, _anchor_selections, build_meta_seeds, check_budget
from .preparation_artifacts import preparation_prefix
# These two utilities only validate selections and trim local RGB frames. They
# never instantiate or call the Replicate adapter.
from .replicate_recovery import _scene_video, _validated_groups
from .tracking import _maps


def _merge_scene(source, candidate, output, metadata, scene_metadata, start, end, requests, unresolved, ambiguous, remaining):
    reports, accepted = [], set()
    width, height = metadata["width"], metadata["height"]
    allowed = {0, *(item["label"] for item in requests)}
    with _maps(source, metadata) as saved, _maps(candidate, scene_metadata) as read:
        for frame in range(end - start):
            if any(int(label) not in allowed for label in np.unique(read(frame))):
                raise PublicError("Meta recovery returned a person outside the saved selections.", 502, code="meta_mask_invalid")
        for item in requests:
            begun = time.monotonic()
            px, py = min(width - 1, int(item["x"] * width)), min(height - 1, int(item["y"] * height))
            outcome = ("conflict" if item["segment_id"] in ambiguous else "coordinate_fallback"
                       if item["segment_id"] in unresolved or int(read(item["frame"] - start)[py, px]) != item["label"]
                       else "recovered")
            if outcome == "recovered":
                for frame in range(start, end):
                    remaining()
                    region, existing = read(frame - start) == item["label"], saved(frame)
                    if np.any(region & (existing != 0) & (existing != item["label"])):
                        outcome = "conflict"
                        break
            if outcome == "recovered":
                accepted.add(item["label"])
            reports.append({"segment_id": item["segment_id"], "outcome": outcome,
                            "seconds": round(time.monotonic() - begun, 3), "method": SEED_VERSION})
        coverage = []
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for frame in range(metadata["frames"]):
                remaining()
                labels = saved(frame).astype(np.uint16, copy=True)
                if start <= frame < end and accepted:
                    detected = read(frame - start)
                    for label in accepted:
                        labels[detected == label] = label
                buffer = BytesIO()
                Image.fromarray(labels).save(buffer, "PNG")
                archive.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
                coverage.append(int(np.count_nonzero(labels)))
    return reports, coverage


def _copy_unresolved(source, output, metadata, requests, ambiguous, remaining):
    """No usable body seeds means no paid video request and no invented pixels."""
    output.write_bytes(source.read_bytes())
    with _maps(output, metadata) as read:
        coverage = []
        for frame in range(metadata["frames"]):
            remaining()
            coverage.append(int(np.count_nonzero(read(frame))))
    return [{"segment_id": item["segment_id"], "outcome": "conflict" if item["segment_id"] in ambiguous else "coordinate_fallback", "seconds": 0,
             "method": SEED_VERSION} for item in requests], coverage


@timed("meta_mask_recovery", job_arg="job")
def run_meta_mask_recovery(pipeline, job, requests, remaining):
    from .meta_preparation import MetaPreparation
    metadata, cloud = job["tracking_metadata"], pipeline.cloud
    groups = _validated_groups(requests, metadata)
    for items in groups.values():
        if len(items) > 5 or len({item["label"] for item in items}) != len(items):
            raise PublicError("Review each selected person once per recovery scene.", 409, code="meta_seed_invalid")
    image_count = sum(len({item["frame"] for item in items}) for items in groups.values())
    expected = check_budget(image_count, [end - start for start, end in groups])
    cloud.check_cap()
    pipeline.update_job(job["id"], message="Recovering the full-person masks from your saved selections.",
                        meta_recovery_expected_api_cost_usd=float(expected))
    emit("mask_recovery_started", job_id=job["id"], section_count=len(requests), provider="meta",
         recovery_method=SEED_VERSION, revision=job.get("tracking_applied_revision"),
         entry_point=job.get("recovery_entry_point", "scene_review"))
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source, bounded, selected = root / "original.mp4", root / "sam.mp4", root / "selected.zip"
        cloud.download(job["reviewed_masks_uri"], selected, 80 * 1024 * 1024)
        # Check the entire saved selected archive before any paid bootstrap.
        with _maps(selected, metadata) as read:
            for frame in range(metadata["frames"]):
                remaining()
                if np.any(read(frame) > 5):
                    raise PublicError("Recovery needs the saved selected-person masks.", 409, code="meta_mask_invalid")
        cloud.download(job["normalized_uri"], source, 100 * 1024 * 1024)
        sam_metadata = sam_input_video(source, bounded)
        if sam_metadata["frames"] != metadata["frames"]:
            raise PublicError("The recovery input changed the saved frame timeline.", 502, code="meta_mask_invalid")
        adapter = MetaPreparation(pipeline.records, cloud, job)
        reports, manifests = [], []
        try:
            for index, ((start, end), items) in enumerate(groups.items()):
                remaining()
                work = root / f"scene-{start}-{end}"
                work.mkdir()
                scene = work / "rgb.mp4"
                _scene_video(bounded, scene, start, end, remaining)
                small_metadata = {**sam_metadata, "frames": end - start, "duration": (end - start) / 24,
                                  "anchor": {"frame": 0}, "scene_cuts": []}
                scene_metadata = {**metadata, "frames": end - start, "duration": (end - start) / 24,
                                  "anchor": {"frame": 0}, "scene_cuts": []}
                targets = [{"id": item["segment_id"], "label": item["label"], "anchor": {
                    "x": item["x"], "y": item["y"], "frame": item["frame"] - start,
                    "time": (item["frame"] - start) / 24}} for item in items]
                logical_context = {"kind": "recovery", "start_frame": start, "end_frame": end,
                    "review_revision": job.get("tracking_applied_revision", job.get("tracking_revision", 0))}
                seeds, manifest = build_meta_seeds(adapter, work, scene, small_metadata, targets, remaining,
                    operation_prefix="sam_recovery_seed", allow_unmatched=True, logical_context=logical_context)
                manifest.update(start_frame=start, end_frame=end)
                manifests.append(manifest)
                merged = root / f"merged-{index}.zip"
                if not seeds:
                    items_report, coverage = _copy_unresolved(selected, merged, metadata, items,
                        set(manifest["ambiguous_target_ids"]), remaining)
                else:
                    # Stable saved selections identify the paid request. Exact
                    # scene bytes and derived mask prompts remain protected by
                    # the adapter fingerprint, not a new paid operation name.
                    import hashlib
                    import json
                    identity = {"version": SEED_VERSION, "source_identity": manifest["source_identity"],
                                "selected_anchors": _anchor_selections(small_metadata, targets)}
                    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
                    result = adapter.segment_video("sam_recovery_" + digest, scene, small_metadata, seeds, remaining,
                                                   propagation_direction="both")
                    manifest["video_usage"] = result.get("usage", {})
                    raw, checked = work / "raw.zip", work / "checked.zip"
                    local = result.get("local_masks")
                    if local and Path(local).is_file():
                        raw.write_bytes(Path(local).read_bytes())
                    else:
                        cloud.download(result["masks_uri"], raw, 80 * 1024 * 1024)
                    resize_label_archive(raw, checked, scene_metadata)
                    items_report, coverage = _merge_scene(selected, checked, merged, metadata, scene_metadata,
                        start, end, items, set(manifest["unresolved_target_ids"]),
                        set(manifest["ambiguous_target_ids"]), remaining)
                reports.extend(items_report)
                selected = merged
            uri = cloud.upload(pipeline.settings.worker_bucket, preparation_prefix(job, "worker")
                + f"meta-recovery-{uuid.uuid4().hex}/reviewed-masks.zip", selected, "application/zip")
            pipeline.update_job(job["id"], meta_recovery_seed_manifests=manifests)
        finally:
            adapter.http.close()
    ordered = {item["segment_id"]: item for item in reports}
    return uri, coverage, [ordered[item["segment_id"]] for item in requests]
