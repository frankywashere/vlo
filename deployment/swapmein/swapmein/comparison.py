"""Bounded CPU comparison exports and private 30-day Library companions."""
from pathlib import Path
import tempfile
import uuid

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .media import decoded_audio_samples, probe, require_audio_duration, run
from .watermark_artwork import create_watermark


def build_comparison(original: Path, recast: Path, output: Path, duration: int):
    video = next(stream for stream in probe(recast)["streams"] if stream["codec_type"] == "video")
    width = max(320, min(720, video["width"])) // 2 * 2
    pad = round(width * .03)
    panel_width = width - pad * 2
    panel_height = round(min(panel_width * video["height"] / video["width"], width)) // 2 * 2
    label, gap, footer = [round(width * fraction) for fraction in (.05, .025, .11)]
    before_y = pad + label
    after_label = before_y + panel_height + gap
    after_y = after_label + label
    height = (after_y + panel_height + footer + pad + 1) // 2 * 2
    artwork = Image.new("RGB", (width, height), "#faf8f7")
    draw = ImageDraw.Draw(artwork)
    font = ImageFont.load_default(size=max(12, round(width * .025)))
    small = ImageFont.load_default(size=max(10, round(width * .018)))
    draw.text((pad, before_y - label / 2), "Original", font=font, anchor="lm", fill="#252326")
    draw.text((pad, after_label + label / 2), "Recast", font=font, anchor="lm", fill="#cc3f37")
    footer_y = after_y + panel_height + footer / 2
    draw.text((pad, footer_y), "Any clip. Any face.", font=small, anchor="lm", fill="#716b73")
    logo, _ = create_watermark(width * 2, width)
    artwork.paste(logo, (width - pad - logo.width, round(footer_y - logo.height / 2)), logo)
    background = output.with_suffix(".comparison.png")
    artwork.save(background)
    def panel(index, name):
        return (f"[{index}:v]fps=24,trim=end_frame={duration * 24},setpts=PTS-STARTPTS,"
                f"scale={panel_width}:{panel_height}:force_original_aspect_ratio=decrease,"
                f"pad={panel_width}:{panel_height}:(ow-iw)/2:(oh-ih)/2:color=0x171418,setsar=1[{name}]")
    filters = (panel(0, "before") + ";" + panel(1, "after")
               + f";[2:v]trim=end_frame={duration * 24},setpts=PTS-STARTPTS[background]"
               + f";[background][before]overlay={pad}:{before_y}[top]"
               + f";[top][after]overlay={pad}:{after_y}[comparison]")
    run(["ffmpeg", "-v", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", str(original),
         "-protocol_whitelist", "file,pipe", "-i", str(recast), "-loop", "1", "-framerate", "24", "-i", str(background),
         "-filter_complex", filters, "-map", "[comparison]", "-map", "1:a:0", "-t", str(duration),
         "-c:v", "libx264", "-threads", "2", "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "copy",
         "-map_metadata", "-1", "-movflags", "+faststart", str(output)], timeout=120)
    checked = probe(output)
    exported = next(stream for stream in checked["streams"] if stream["codec_type"] == "video")
    if (exported["width"], exported["height"], int(exported.get("nb_frames", 0))) != (width, height, duration * 24):
        raise ValueError("Comparison frames could not be verified")
    require_audio_duration(checked, duration)
    if decoded_audio_samples(output) < (duration - .05) * 44100:
        raise ValueError("Comparison soundtrack could not be verified")


def preserve_library_comparison(settings, cloud, job):
    if not job.get("account_id") or job.get("status") != "completed":
        return {}
    if job.get("comparison_uri") and job.get("comparison_for_output_uri") == job.get("output_uri"):
        return {}
    source = job.get("library_source_uri") or (job.get("normalized_uri") if not job.get("temporary_media_cleanup_started_at") else None)
    if not source:
        return {}  # Older deliveries may already have removed their original.
    prefix = f"library/{job['account_id']}/{job['id']}/"
    attempt = uuid.uuid4().hex
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        original, recast, comparison = [root / name for name in ("original.mp4", "recast.mp4", "comparison.mp4")]
        cloud.download(source, original)
        cloud.download(job["output_uri"], recast)
        references = job.get("library_reference_uris") or job.get("reference_uris") or ([job["reference_uri"]] if job.get("reference_uri") else [])
        faces, saved_faces = [], []
        for index, uri in enumerate(references[:5]):
            raw, portrait = root / f"reference-{index}", root / f"character-{index}.jpg"
            cloud.download(uri, raw, 20 * 1024 * 1024)
            with Image.open(raw) as image:
                thumbnail = ImageOps.fit(ImageOps.exif_transpose(image).convert("RGBA"), (160, 160), centering=(.5, .35))
                backing = Image.new("RGBA", thumbnail.size, "white")
                Image.alpha_composite(backing, thumbnail).convert("RGB").save(portrait, quality=90)
            faces.append(portrait)
        build_comparison(original, recast, comparison, job["duration"])
        source_uri = job.get("library_source_uri") or cloud.upload(settings.app_bucket, prefix + f"original-{attempt}.mp4", original, "video/mp4")
        for index, portrait in enumerate(faces):
            saved_faces.append(cloud.upload(settings.app_bucket, prefix + f"character-{index}-{attempt}.jpg", portrait, "image/jpeg"))
        uri = cloud.upload(settings.app_bucket, prefix + f"comparison-{attempt}.mp4", comparison, "video/mp4")
    return {"library_source_uri": source_uri, "library_reference_uris": saved_faces,
            "comparison_uri": uri, "comparison_for_output_uri": job["output_uri"]}
