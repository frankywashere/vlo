"""Point-prompted, scene-bounded SAM recovery; never overwrite another selection."""
from io import BytesIO
import gc
import json
import math
from pathlib import Path
import tempfile
import time
import zipfile

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RecoverySelection(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    segment_id: str = Field(min_length=1, max_length=120)
    start_frame: int = Field(ge=0, le=359, strict=True)
    end_frame: int = Field(ge=1, le=360, strict=True)
    frame: int = Field(ge=0, le=359, strict=True)
    label: int = Field(ge=1, le=5, strict=True)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def frame_inside_scene(self):
        if not self.start_frame <= self.frame < self.end_frame:
            raise ValueError("The tap must be inside its saved scene")
        return self


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_uri: str
    masks_uri: str
    run_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    selections: list[RecoverySelection] = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def bounded_work(self):
        if (len({item.segment_id for item in self.selections}) != len(self.selections)
                or sum(item.end_frame - item.start_frame for item in self.selections) > 1800):
            raise ValueError("Recovery exceeds the five-person clip budget")
        return self


def recover_archive(source, output, frames, selections, track):
    """Validate each recovery before applying it; retain all existing target labels."""
    count = len(frames)
    reports = []
    with zipfile.ZipFile(source) as archive:
        names = [f"labels/labels_{i:05d}.png" for i in range(count)]
        if sorted(archive.namelist()) != sorted(names):
            raise ValueError("Recovery needs exactly the saved selected masks")
        maps = []
        size = None
        for name in names:
            if archive.getinfo(name).file_size > 2_000_000:
                raise ValueError("Oversized selected mask")
            with Image.open(BytesIO(archive.read(name))) as image:
                if (max(image.size) > 1920 or min(image.size) > 768
                        or image.width * image.height > 1_474_560):
                    raise ValueError("Selected mask exceeds prepared dimensions")
                if size is None:
                    size = image.size
                if image.size != size:
                    raise ValueError("Selected masks changed dimensions")
                labels = np.array(image)
                if labels.ndim != 2 or not np.issubdtype(labels.dtype, np.integer) or np.any(labels < 0) or np.any(labels > 5):
                    raise ValueError("Invalid selected mask labels")
                maps.append(labels.astype(np.uint16))
        for selection in selections:
            item = selection.model_dump() if isinstance(selection, RecoverySelection) else selection
            if item["end_frame"] > count:
                raise ValueError("Recovery scene exceeds the clip")
            start, end, tap = item["start_frame"], item["end_frame"], item["frame"]
            before = time.monotonic()
            predicted = track(frames[start:end], tap - start, item["x"], item["y"])
            expected = set(range(end - start))
            if set(predicted) != expected:
                raise ValueError("SAM recovery returned an incomplete frame timeline")
            recovered = []
            for index in range(end - start):
                binary = np.asarray(predicted[index])
                if binary.shape != frames[start + index].shape[:2] or binary.dtype != np.bool_:
                    raise ValueError("SAM recovery returned invalid mask geometry")
                recovered.append(np.array(Image.fromarray(binary).resize(size, Image.Resampling.NEAREST), dtype=bool))
            px = min(size[0] - 1, math.floor(item["x"] * size[0]))
            py = min(size[1] - 1, math.floor(item["y"] * size[1]))
            outcome = "recovered" if recovered[tap - start][py, px] else "coordinate_fallback"
            # Match the existing strict duplicate-selection rule. Never erase an
            # already assigned person's pixels to make a recovery look successful.
            if outcome == "recovered" and any(np.any(mask & (maps[start+i] != 0) & (maps[start+i] != item["label"]))
                    for i, mask in enumerate(recovered)):
                outcome = "conflict"
            if outcome == "recovered":
                for i, mask in enumerate(recovered):
                    maps[start + i][mask] = item["label"]
            reports.append({"segment_id": item["segment_id"], "outcome": outcome,
                "seconds": round(time.monotonic() - before, 3)})
    coverage = []
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, labels in zip(names, maps, strict=True):
            buffer = BytesIO()
            Image.fromarray(labels).save(buffer, "PNG")
            archive.writestr(name, buffer.getvalue())
            coverage.append(int(np.count_nonzero(labels)))
    return {"selections": reports, "coverage": coverage}


class PointTracker:
    """Load only retained local SAM weights; one model, fresh state per scene."""
    def __init__(self, checkpoint):
        import torch
        from transformers import Sam3TrackerVideoModel, Sam3TrackerVideoProcessor
        self.torch = torch
        self.processor = Sam3TrackerVideoProcessor.from_pretrained(checkpoint, local_files_only=True)
        self.model, information = Sam3TrackerVideoModel.from_pretrained(checkpoint, local_files_only=True,
            dtype=torch.bfloat16, output_loading_info=True)
        # Reject an incompatible bundled checkpoint instead of using random weights.
        if information.get("missing_keys") or information.get("mismatched_keys") or information.get("error_msgs"):
            raise ValueError("Bundled SAM checkpoint is incomplete for point tracking")
        self.model.to("cuda").eval()

    def __call__(self, frames, tap_frame, x, y):
        processor, model, torch = self.processor, self.model, self.torch
        height, width = frames[0].shape[:2]
        session = processor.init_video_session(video=[Image.fromarray(frame) for frame in frames],
            inference_device="cuda", processing_device="cpu", video_storage_device="cpu", dtype=torch.bfloat16)
        processor.add_inputs_to_inference_session(inference_session=session, frame_idx=tap_frame,
            obj_ids=1, input_points=[[[[min(width-1, x*width), min(height-1, y*height)]]]], input_labels=[[[1]]])
        result = {}
        with torch.inference_mode():
            model(inference_session=session, frame_idx=tap_frame)
            for reverse in (False, True):
                for prediction in model.propagate_in_video_iterator(inference_session=session,
                        start_frame_idx=tap_frame, max_frame_num_to_track=len(frames), reverse=reverse):
                    index = int(prediction.frame_idx)
                    mask = processor.post_process_masks([prediction.pred_masks],
                        original_sizes=[[height, width]], binarize=False)[0]
                    # One tracked object and one selected mask, at source resolution.
                    result[index] = (mask[0, 0] > 0).cpu().numpy().astype(bool)
        del session
        return result


def install(worker):
    """Attach a private endpoint using the existing worker lock and storage policy."""
    @worker.app.post("/recover-masks")
    def recover_masks(request: RecoveryRequest):
        if not worker.lock.acquire(blocking=False):
            raise worker.HTTPException(409, "Worker busy")
        tracker = None
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, archive, output = root / "source.mp4", root / "selected.zip", root / "outputs"
                output.mkdir()
                worker.download_input(request.input_uri, str(source))
                worker.probe(source)
                worker.download_input(request.masks_uri, str(archive))
                frames = worker.read_frames(source)
                if not 1 <= len(frames) <= 360 or frames[0].shape[0] > 480 or frames[0].shape[1] > 854:
                    raise ValueError("Point recovery requires bounded original RGB frames")
                tracker = PointTracker(worker.SAM_PATH)
                report = recover_archive(archive, output / "masks.zip", frames, request.selections, tracker)
                report["seconds"] = round(time.monotonic() - started, 3)
                (output / "recovery.json").write_text(json.dumps(report, allow_nan=False))
                return {"metrics": report, "files": worker.upload_tree(output, request.run_id)}
        finally:
            del tracker
            gc.collect()
            worker.torch.cuda.empty_cache()
            worker.lock.release()
