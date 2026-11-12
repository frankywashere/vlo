"""Offline differential tests for the two matching official Meta codecs."""
import hashlib
import random
import shutil
import subprocess

import pytest
from meta_sam_parser import InvalidSegmentationMaskError, SegmentationMask, decode_mask_to_raster
from meta_sam_parser._mask_codec import _encode_raster

from swapmein.errors import PublicError
from swapmein import meta_mask_codec as module
from swapmein.meta_mask_codec import StrictMaskCodec

HAS_DENO = bool(shutil.which("deno"))
SAMPLE = SegmentationMask(encoding="one_bit", width=5, height=5, payload="!!!!!(QO(0lu8?")


@pytest.fixture
def codec(monkeypatch):
    if not HAS_DENO:
        pytest.skip("Deno runtime is checked during the container build")
    monkeypatch.setenv("META_MASK_CODEC", "javascript")
    instance = StrictMaskCodec(decode_mask_to_raster, lambda: 10)
    try:
        assert instance.backend == "javascript"
        yield instance
    finally:
        instance.close()


def test_vendored_official_distribution_is_complete_and_unchanged():
    module._verify_package()
    assert (module.PACKAGE / "LICENSE").is_file()


@pytest.mark.parametrize("shape", [(1, 1), (1, 17), (19, 1), (5, 5), (64, 48), (129, 73)])
def test_real_official_encodings_match_pixel_for_pixel(codec, shape):
    width, height = shape
    random_source = random.Random(width * 1000 + height)
    rasters = [bytes(width * height), bytes([1]) * (width * height),
        bytes((x + y) % 2 for y in range(height) for x in range(width)),
        bytes(random_source.randrange(2) for _ in range(width * height))]
    for raster in rasters:
        mask = SegmentationMask(encoding="one_bit", width=width, height=height,
                                payload=_encode_raster(raster, width, height))
        assert codec.decode(mask) == decode_mask_to_raster(mask) == raster


@pytest.mark.parametrize("payload", ["", "!", "~bad", "!!!!!(QO(0lu8?x", "!!!!!(QO(0lu8", "!!!!!(QO(0lu8\"", "!!!!!(QO(0lu8😀"])
def test_truncated_noncanonical_and_invalid_alphabet_rejected_by_both(codec, payload):
    mask = SegmentationMask(encoding="one_bit", width=5, height=5, payload=payload)
    with pytest.raises(InvalidSegmentationMaskError):
        decode_mask_to_raster(mask)
    with pytest.raises(InvalidSegmentationMaskError):
        codec.decode(mask)
    # A malformed mask does not corrupt the next independent request.
    assert codec.decode(SAMPLE) == decode_mask_to_raster(SAMPLE)


def test_official_lossless_threshold_and_accepted_finalization_variants_match(codec):
    # Meta's public compact-api-image-lossless conformance vector, SAM License.
    from meta_sam_parser import _mask_codec as original
    payload = "~!!!!.!0^zlTde]:)]`W"
    packed = original._unpack(payload[1:])
    alternate = bytearray(packed)
    alternate[-4] = 0
    for value in (payload, "~" + original._pack(packed + bytes([0])), "~" + original._pack(alternate)):
        mask = SegmentationMask(encoding="lossless", width=3, height=2, payload=value)
        assert codec.decode(mask) == decode_mask_to_raster(mask) == bytes([0, 0, 1, 1, 0, 1])


def test_missing_deno_uses_original_python_without_starting_a_process(monkeypatch):
    monkeypatch.setenv("META_MASK_CODEC", "auto")
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: pytest.fail("No child"))
    codec = StrictMaskCodec(decode_mask_to_raster, lambda: 10)
    assert codec.backend == "python" and codec.decode(SAMPLE) == decode_mask_to_raster(SAMPLE)
    codec.close()


def test_explicit_python_mode_preserves_fallback_even_with_deno(monkeypatch):
    monkeypatch.setenv("META_MASK_CODEC", "python")
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: pytest.fail("No child"))
    codec = StrictMaskCodec(decode_mask_to_raster, lambda: 10)
    assert codec.backend == "python" and codec.decode(SAMPLE) == decode_mask_to_raster(SAMPLE)
    codec.close()


def test_required_js_without_deno_and_invalid_mode_fail_closed(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    for mode in ("javascript", "unknown"):
        monkeypatch.setenv("META_MASK_CODEC", mode)
        with pytest.raises(PublicError) as error:
            StrictMaskCodec(decode_mask_to_raster, lambda: 10)
        assert error.value.code == "meta_codec_unavailable"


def test_changed_bundle_fails_before_subprocess(monkeypatch):
    monkeypatch.setenv("META_MASK_CODEC", "javascript")
    monkeypatch.setattr(module.shutil, "which", lambda _: "/verified/deno")
    monkeypatch.setattr(module, "_verify_package", lambda: (_ for _ in ()).throw(ValueError("changed bytes")))
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: pytest.fail("No unverified program"))
    with pytest.raises(PublicError):
        StrictMaskCodec(decode_mask_to_raster, lambda: 10)


def test_child_cannot_inherit_credentials_or_network_authority(monkeypatch):
    if not HAS_DENO:
        pytest.skip("Deno unavailable")
    monkeypatch.setenv("META_MASK_CODEC", "javascript")
    monkeypatch.setenv("META_API_KEY", "NEVER_INHERIT_THIS")
    actual = subprocess.Popen
    captured = []
    def start(command, **kwargs):
        captured.append((command, kwargs))
        return actual(command, **kwargs)
    monkeypatch.setattr(module.subprocess, "Popen", start)
    codec = StrictMaskCodec(decode_mask_to_raster, lambda: 10)
    process = codec.process
    try:
        assert codec.decode(SAMPLE) == decode_mask_to_raster(SAMPLE)
        command, kwargs = captured[0]
        assert "--deny-net" in command and "--deny-read" in command and "--deny-env" in command
        assert "--deny-run" in command and "--deny-write" in command
        assert "--no-config" in command and "--no-remote" in command
        assert set(kwargs["env"]) == {"DENO_DIR", "DENO_NO_UPDATE_CHECK", "NO_COLOR"}
        assert "NEVER_INHERIT_THIS" not in str(kwargs)
    finally:
        codec.close()
    assert process.poll() is not None
    codec.close()  # Cleanup is idempotent.


def test_dead_child_fails_boundedly_without_silent_redecode(codec):
    codec.process.kill()
    codec.process.wait(timeout=2)
    with pytest.raises(PublicError) as error:
        codec.decode(SAMPLE)
    assert error.value.code == "meta_codec_unavailable" and codec.process is None


def test_staged_runtime_includes_pinned_codec_and_license(tmp_path):
    import deploy
    staged = tmp_path / "source"
    staged.mkdir()
    deploy.stage_source(staged)
    package = staged / "swapmein" / "vendor" / "meta_sam_parser"
    assert (staged / "swapmein" / "meta_mask_codec.mjs").read_bytes() == (module.ROOT / "meta_mask_codec.mjs").read_bytes()
    assert (package / "LICENSE").read_bytes() == (module.PACKAGE / "LICENSE").read_bytes()
    for source in (module.PACKAGE / "dist").glob("*.js"):
        assert hashlib.sha256((package / "dist" / source.name).read_bytes()).hexdigest() == hashlib.sha256(source.read_bytes()).hexdigest()


def test_required_image_decode_smoke_runs_as_nonroot_gateway():
    import deploy
    lines = [line.strip() for line in (deploy.SOURCE / "Dockerfile").read_text().splitlines()]
    user = lines.index("USER gateway")
    smoke = next(i for i, line in enumerate(lines)
                 if line.startswith("RUN META_MASK_CODEC=javascript "))
    assert user < smoke, "The required codec smoke must exercise the runtime user's permissions"
    assert not any(line.startswith("USER ") for line in lines[user+1:smoke])
    # Actual decoding, not only import/version checking, must run in the build.
    assert 'c.backend=="javascript"' in lines[smoke]
    assert 'c.decode(m)==decode_mask_to_raster(m)' in lines[smoke]
