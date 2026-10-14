"""Copy existing approved proof assets into an ignored, local-only UI demo.

Usage: python3 scripts/prepare-charswap-demo.py /path/to/CharSwap
Requires ffmpeg, Pillow, and numpy. Does not call cloud services or copy secrets.
"""
import argparse
import io
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import numpy as np
from PIL import Image

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("proof_root", type=Path)
args = parser.parse_args()
proof = args.proof_root / "experiments/h3_rooftop_segmentation_only_5s_v1"
destination = Path(__file__).resolve().parents[1] / "frontend/public/charswap-demo"
destination.mkdir(parents=True, exist_ok=True)
shutil.copyfile(proof / "source.mp4", destination / "source.mp4")
shutil.copyfile(args.proof_root / "experiments/plush_4s_480p_v1/plush_reference.png", destination / "teddy.png")
subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0.5", "-i", str(proof / "source.mp4"), "-frames:v", "1", str(destination / "poster.jpg")], check=True)
image = Image.open(destination / "poster.jpg")
people = []
with zipfile.ZipFile(proof / "masks.zip") as archive:
    for label, name, color in [(1, "Person 1", "#75baff"), (2, "Person 2", "#d3a3ff")]:
        boxes = []
        for frame in range(0, 120, 6):
            mask = np.asarray(Image.open(io.BytesIO(archive.read(f"labels/labels_{frame:05d}.png"))))
            rows, cols = np.where(mask == label)
            if not len(rows):
                continue
            x, y, right, bottom = int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())
            boxes.append({"time": frame / 24, "x": x / 854, "y": y / 480, "width": (right - x + 1) / 854, "height": (bottom - y + 1) / 480})
        if not boxes:
            continue
        box = min(boxes, key=lambda b: abs(b["time"] - .5))
        crop = image.crop((round(box["x"] * 854), round(box["y"] * 480), round((box["x"] + box["width"]) * 854), round((box["y"] + box["height"]) * 480)))
        crop.thumbnail((300, 300))
        crop.save(destination / f"person-{label}.jpg")
        people.append({"id": f"person-{label}", "name": name, "color": color, "thumbnail": f"/charswap-demo/person-{label}.jpg", "boxes": boxes})
manifest = {"clip": {"id": "rooftop-demo", "name": "Rooftop sample.mp4", "url": "/charswap-demo/source.mp4", "duration": 5, "width": 854, "height": 480, "mode": "demo"}, "poster": "/charswap-demo/poster.jpg", "people": people, "reference": {"id": "teddy-demo", "name": "Teddy bear", "url": "/charswap-demo/teddy.png"}}
(destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(f"Prepared {len(people)} sample people in ignored directory: {destination}")
