"""Resize discrete SAM tracking labels onto the prepared guide without inventing IDs."""
from io import BytesIO
import json
from pathlib import Path
import zipfile

import numpy as np
from PIL import Image

from .diagnostics import timed
from .errors import PublicError


@timed("mask_resize")
def resize_label_archive(archive: Path, output: Path, metadata: dict):
    width, height, frames = (metadata.get(key) for key in ("width", "height", "frames"))
    if (any(type(value) is not int for value in (width, height, frames))
            or not 1 <= frames <= 360 or not 2 <= min(width, height) <= 768
            or max(width, height) > 1920 or width * height > 1_474_560):
        raise PublicError("Invalid prepared mask dimensions or frame count.")
    if archive.resolve() == output.resolve():
        raise PublicError("Tracking masks need a separate output archive.")
    source_size = None
    try:
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as destination:
            infos = source.infolist()
            names = [info.filename for info in infos]
            expected = [f"labels/labels_{index:05d}.png" for index in range(frames)]
            labels = sorted(name for name in names if name.startswith("labels/") and name.endswith(".png"))
            if labels != expected or len(names) != len(set(names)) or len(infos) > frames + 16:
                raise PublicError("Tracking did not return one mask for every prepared frame.")
            for name in labels:
                info = source.getinfo(name)
                if info.file_size > 2_000_000:
                    raise PublicError("Oversized tracking mask.")
                with Image.open(BytesIO(source.read(name))) as image:
                    if image.width > 854 or image.height > 480 or image.width < 1 or image.height < 1:
                        raise PublicError("Tracking mask exceeds the SAM working dimensions.")
                    if source_size is None:
                        source_size = image.size
                        if abs((image.width / image.height) / (width / height) - 1) > .03:
                            raise PublicError("Tracking masks do not match the prepared frame shape.")
                    if image.size != source_size:
                        raise PublicError("Tracking mask dimensions changed between frames.")
                    array = np.array(image)
                    if array.ndim != 2 or not np.issubdtype(array.dtype, np.integer) or np.any(array < 0) or np.any(array > 65535):
                        raise PublicError("Invalid tracking label values.")
                    # Labels are categorical IDs. Linear interpolation would
                    # create other identities at boundaries between people.
                    resized = Image.fromarray(array.astype(np.uint16)).resize((width, height), Image.Resampling.NEAREST)
                    buffer = BytesIO()
                    resized.save(buffer, "PNG")
                    destination.writestr(name, buffer.getvalue())
            if "objects.json" in names:
                if source.getinfo("objects.json").file_size > 1_000_000:
                    raise PublicError("Oversized tracking metadata.")
                objects = json.loads(source.read("objects.json"))
                if not isinstance(objects, dict):
                    raise PublicError("Invalid tracking metadata.")
                objects.update(width=width, height=height, frames=frames, fps=24)
                # Object geometry is expressed in image pixels by the worker.
                scale_x, scale_y = width / source_size[0], height / source_size[1]
                for item in objects.get("objects", []):
                    if "best_box" in item:
                        box = item["best_box"]
                        if not isinstance(box, list) or len(box) != 4 or any(type(value) not in (int, float) for value in box):
                            raise PublicError("Invalid tracking object bounds.")
                        item["best_box"] = [box[0] * scale_x, box[1] * scale_y, box[2] * scale_x, box[3] * scale_y]
                    if type(item.get("best_area")) is int:
                        item["best_area"] = round(item["best_area"] * scale_x * scale_y)
                destination.writestr("objects.json", json.dumps(objects, allow_nan=False))
        return {"width": width, "height": height, "frames": frames,
                "source_width": source_size[0], "source_height": source_size[1]}
    except PublicError:
        output.unlink(missing_ok=True)
        raise
    except Exception:
        output.unlink(missing_ok=True)
        raise PublicError("The tracking masks could not be resized safely.") from None
