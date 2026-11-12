"""SAM and optional depth credentials are required only for actual inference."""
import pytest

from swapmein.errors import PublicError
from swapmein.replicate_preparation import preparation_snapshot


@pytest.fixture
def credentials(monkeypatch):
    calls = []
    def replicate():
        calls.append("replicate")
        return "projects/project/secrets/recastfx-replicate-key/versions/3"
    def meta():
        calls.append("meta")
        return "projects/project/secrets/recastfx-meta-api-key/versions/7"
    monkeypatch.setattr("swapmein.replicate_preparation.replicate_credential_version", replicate)
    monkeypatch.setattr("swapmein.meta_credentials.meta_credential_version", meta)
    return calls


@pytest.mark.parametrize("provider,sam,depth,expected", [
    ("replicate", "meta", False, ["meta"]),
    ("replicate", "meta", True, ["replicate", "meta"]),
    ("replicate", "workers", False, []),
    ("replicate", "workers", True, ["replicate"]),
    ("workers", "replicate", False, ["replicate"]),
    ("workers", "meta", True, ["meta"]),
    ("workers", "meta", False, ["meta"]),
    ("replicate", None, False, ["replicate"]),
    ("replicate", None, True, ["replicate"]),
    ("workers", None, False, []),
])
def test_explicit_depth_flag_requires_only_used_credentials(credentials, provider, sam, depth, expected):
    snapshot = preparation_snapshot(provider, sam_provider=sam, depth_guidance=depth)
    assert credentials == expected
    assert ("replicate_credential_version" in snapshot) == ("replicate" in expected)
    assert ("meta_credential_version" in snapshot) == ("meta" in expected)
    assert snapshot["preparation_provider"] == provider
    assert "depth_guidance" not in snapshot


@pytest.mark.parametrize("sam", [None, "workers", "meta"])
def test_omitted_depth_flag_preserves_legacy_coupled_default(credentials, sam):
    preparation_snapshot("replicate", sam_provider=sam)
    assert credentials == ["replicate"] + (["meta"] if sam == "meta" else [])


@pytest.mark.parametrize("provider,sam", [("replicate", "meta"), ("replicate", "replicate"), ("workers", "meta")])
def test_tracking_disabled_loads_no_inference_credentials(credentials, provider, sam):
    snapshot = preparation_snapshot(provider, enhanced_masking=False, sam_provider=sam, depth_guidance=True)
    assert credentials == []
    assert "replicate_credential_version" not in snapshot and "meta_credential_version" not in snapshot


def test_missing_unused_replicate_key_does_not_block_meta_only(monkeypatch):
    monkeypatch.setattr("swapmein.replicate_preparation.replicate_credential_version",
                        lambda: pytest.fail("Unused depth/SAM provider must not load credentials"))
    monkeypatch.setattr("swapmein.meta_credentials.meta_credential_version", lambda: "meta-version")
    assert preparation_snapshot("replicate", sam_provider="meta", depth_guidance=False)["meta_credential_version"] == "meta-version"


@pytest.mark.parametrize("depth", [0, 1, "false", [], {}])
def test_invalid_depth_flag_rejects_before_credentials(credentials, depth):
    with pytest.raises(PublicError):
        preparation_snapshot("replicate", sam_provider="meta", depth_guidance=depth)
    assert not credentials
