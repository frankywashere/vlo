"""Create an original, offline cartoon sample for the local swapMEin wizard.

Run with backend/.venv/Scripts/python.exe on Windows (bin/python on Unix).
Requires the installed Pillow/numpy dependencies and ffmpeg on PATH.
All generated assets live in the existing ignored demo folder.
"""
import json
import math
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image, ImageDraw


def main():
    root = Path(__file__).resolve().parents[1] / "frontend/public/charswap-demo"
    root.mkdir(parents=True, exist_ok=True)
    width, height, fps = 854, 480, 24
    process = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pixel_format", "rgb24",
        "-video_size", f"{width}x{height}", "-framerate", str(fps), "-i", "pipe:0", "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(root / "source.mp4")], stdin=subprocess.PIPE)
    people = [{"id": f"person-{i + 1}", "name": f"Person {i + 1}", "color": color,
        "thumbnail": f"/charswap-demo/person-{i + 1}.jpg", "boxes": []}
        for i, color in enumerate(["#dc302f", "#9575cd"])]
    for frame in range(fps * 5):
        image = Image.new("RGB", (width, height), "#efe5db")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 340, width, height), fill="#d1c6b9")
        draw.ellipse((600, 35, 740, 175), fill="#f9f4ec")
        draw.rounded_rectangle((45, 70, 190, 300), radius=70, fill="#e4d4c5")
        draw.line((0, 340, width, 340), fill="#b4a694", width=2)
        for i, person in enumerate(people):
            x = (290 if i == 0 else 560) + round(math.sin(frame / 22 + i) * 16)
            y = 0
            color = person["color"]
            draw.ellipse((x - 62, 411, x + 65, 435), fill="#b9ae9f")
            draw.line((x - 18, 310, x - 35, 420), fill="#32353b", width=28)
            draw.line((x + 18, 310, x + 35, 420), fill="#32353b", width=28)
            draw.rounded_rectangle((x - 47, 206, x + 47, 327), radius=20, fill=color)
            draw.line((x - 46, 217, x - 75, 299 + round(math.sin(frame / 12) * 18)), fill=color, width=20)
            draw.line((x + 46, 217, x + 75, 299 - round(math.sin(frame / 12) * 18)), fill=color, width=20)
            draw.rectangle((x - 15, 181, x + 15, 218), fill="#c99875")
            draw.ellipse((x - 37, 116, x + 37, 197), fill="#d7ac8b")
            draw.pieslice((x - 40, 102, x + 40, 166), 180, 360, fill="#413933")
            draw.ellipse((x - 18, 153, x - 12, 159), fill="#413933")
            draw.ellipse((x + 12, 153, x + 18, 159), fill="#413933")
            draw.arc((x - 14, 161, x + 14, 183), 0, 180, fill="#986449", width=3)
            if frame % 6 == 0:
                person["boxes"].append({"time": frame / fps, "x": (x - 80) / width, "y": 102 / height,
                    "width": 160 / width, "height": 330 / height})
            if frame == 12:
                image.crop((x - 80, 95, x + 80, 430)).save(root / f"person-{i + 1}.jpg")
        if frame == 12:
            image.save(root / "poster.jpg")
        process.stdin.write(np.asarray(image).tobytes())
    process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError("Unable to encode the sample video.")
    bear = Image.new("RGB", (320, 400), "#f8eee5")
    draw = ImageDraw.Draw(bear)
    for box in [(78, 72, 128, 122), (192, 72, 242, 122), (78, 212, 242, 365), (89, 91, 232, 240)]:
        draw.ellipse(box, fill="#bc8255")
    draw.ellipse((114, 154, 207, 221), fill="#e0b58d")
    for x in [124, 188]:
        draw.ellipse((x, 142, x + 12, 156), fill="#342b29")
    draw.ellipse((149, 168, 172, 184), fill="#342b29")
    draw.rounded_rectangle((115, 251, 208, 320), radius=12, fill="#dc302f")
    bear.save(root / "teddy.png")
    manifest = {"clip": {"id": "studio-demo", "name": "Studio sample.mp4", "url": "/charswap-demo/source.mp4",
        "duration": 5, "width": width, "height": height, "mode": "demo"}, "poster": "/charswap-demo/poster.jpg",
        "people": people, "reference": {"id": "teddy-demo", "name": "Teddy bear", "url": "/charswap-demo/teddy.png"}}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Created an offline sample in {root}")


if __name__ == "__main__":
    main()
