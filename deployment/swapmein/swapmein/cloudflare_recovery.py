"""Recover initial taps without discarding the other raw SAM person tracks."""
from io import BytesIO
from pathlib import Path
import uuid
import zipfile

import numpy as np
from PIL import Image

from .diagnostics import emit, timed
from .errors import PublicError
from .mask_recovery import run_mask_recovery
from .multi_tracking import selected_tracks
from .tracking import _maps, _metadata, _pixel_label


def _failure():
    return PublicError("A missing initial person mask could not be recovered safely. Check scene matches.",
                       409, code="cloudflare_anchor_recovery")


@timed("identity_anchor_recovery", job_arg="job")
def recover_identity_anchors(pipeline, job, masks, metadata, targets, root, remaining):
    """Return a separate repaired raw archive, or None when no retry is needed.

    The existing point-recovery worker accepts only selected labels 1..5. Give
    it an empty archive with those temporary slots, then merge its additions
    into the original all-person SAM archive under globally unused labels.
    """
    permissive = {**metadata, "allow_unmatched_anchor": True, "review_all_scenes": True}
    tracks = selected_tracks(masks, permissive, targets, allow_unmatched=True)
    missing = [target for target in tracks if target["selected_label"] == 0]
    if not missing or job.get("retry_missing_masks_with_sam") is not True:
        return None
    width, height, frames, _, cuts = _metadata(metadata)
    used = set()
    with _maps(masks, metadata) as read:
        for frame in range(frames):
            used.update(int(label) for label in np.unique(read(frame)))
    unused = (label for label in range(1, 65536) if label not in used)
    requests, raw_labels = [], {}
    boundaries = [0, *cuts, frames]
    for slot, target in enumerate(missing, 1):
        anchor = target["anchor"]
        frame = _metadata({**metadata, "anchor": anchor})[3]
        start, end = next((start, end) for start, end in zip(boundaries, boundaries[1:]) if start <= frame < end)
        label = next(unused, None)
        if label is None:
            raise _failure()
        raw_labels[slot] = label
        requests.append({"segment_id": "identity-initial-" + target["id"], "start_frame": start,
            "end_frame": end, "frame": frame, "x": anchor["x"], "y": anchor["y"], "label": slot})
    root = Path(root)
    empty = root / ("identity-recovery-empty-" + uuid.uuid4().hex + ".zip")
    buffer = BytesIO()
    Image.fromarray(np.zeros((height, width), dtype=np.uint16)).save(buffer, "PNG")
    with zipfile.ZipFile(empty, "w", zipfile.ZIP_DEFLATED) as archive:
        for frame in range(frames):
            archive.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
    prefix = f"inputs/swapmein-{job['id']}/identity-recovery-{uuid.uuid4().hex}/"
    empty_uri = pipeline.cloud.upload(pipeline.settings.worker_bucket, prefix + "empty-masks.zip", empty, "application/zip")
    saved, _ = pipeline.records.read(f"jobs/{job['id']}.json")
    recovery_job = {**job, **(saved or {}), "reviewed_masks_uri": empty_uri,
                    "tracking_metadata": metadata, "recovery_entry_point": "automatic_identity_anchor"}
    recovered_uri, _, report = run_mask_recovery(pipeline, recovery_job, requests, remaining)
    if (len(report) != len(requests) or {item.get("segment_id") for item in report}
            != {item["segment_id"] for item in requests}
            or any(item.get("outcome") != "recovered" for item in report)):
        raise _failure()
    recovered = root / ("identity-recovery-selected-" + uuid.uuid4().hex + ".zip")
    pipeline.cloud.download(recovered_uri, recovered, 80 * 1024 * 1024)
    output = root / ("identity-anchor-repaired-" + uuid.uuid4().hex + ".zip")
    partial = output.with_name(output.name + ".partial")
    try:
        with _maps(masks, metadata) as original, _maps(recovered, metadata) as repaired, zipfile.ZipFile(
                partial, "w", zipfile.ZIP_DEFLATED) as destination:
            for frame in range(frames):
                source, additions = original(frame), repaired(frame)
                if any(int(label) not in {0, *raw_labels} for label in np.unique(additions)):
                    raise _failure()
                if np.any((additions != 0) & (source != 0)):
                    raise _failure()  # Preserve every original SAM pixel and person ID.
                mapped = source.astype(np.uint16, copy=True)
                for request in requests:
                    region = additions == request["label"]
                    if not request["start_frame"] <= frame < request["end_frame"] and np.any(region):
                        raise _failure()
                    if frame == request["frame"]:
                        x, y = min(width - 1, int(request["x"] * width)), min(height - 1, int(request["y"] * height))
                        if not region[y, x]:
                            raise _failure()
                    mapped[region] = raw_labels[request["label"]]
                buffer = BytesIO()
                Image.fromarray(mapped).save(buffer, "PNG")
                destination.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
        partial.replace(output)
        # Verify the actual taps after remapping, rather than trusting the report.
        with _maps(output, metadata) as read:
            for request in requests:
                if _pixel_label(read, request["frame"], request, width, height) != raw_labels[request["label"]]:
                    raise _failure()
        uri = pipeline.cloud.upload(pipeline.settings.worker_bucket, prefix + "raw-repaired-masks.zip", output, "application/zip")
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        partial.unlink(missing_ok=True)
    emit("cloudflare_initial_masks_recovered", job_id=job["id"], recovered_targets=len(requests),
         raw_labels_preserved=True)
    return output, uri, report
