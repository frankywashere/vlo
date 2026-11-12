"""Shared Meta SAM routing reaches both hosted models without starting SAM GPU work."""
import pytest

from test_pipeline_v2 import harness, media  # noqa: F401 - shared real-media fixture


@pytest.mark.parametrize("model", ["minimax-h3", "kling-4-0"])
@pytest.mark.parametrize("depth_provider", ["workers", "replicate"])
@pytest.mark.parametrize("selection", ["single", "multi", "all"])
@pytest.mark.parametrize("depth", [False, True])
def test_meta_masks_share_model_review_composition_and_independent_depth(
        monkeypatch, media, model, depth_provider, selection, depth):
    job, cloud, sam_inputs, previews, _ = harness(monkeypatch, media,
        easy=False, enhanced=True, selection=selection, audio_processing=False,
        preparation_provider=depth_provider, sam_provider="meta", depth_guidance=depth,
        generation_model=model)
    assert job["status"] == ("ready" if selection == "all" else "tracking_review"), job["message"]
    assert len(cloud.meta_calls) == 1
    assert sam_inputs == ["normalized.mp4"]
    gpu = [payload for kind, endpoint, payload in cloud.calls if kind == "gpu"]
    assert all(item["operation"] == "depth" for item in gpu)
    assert len(gpu) == int(depth and depth_provider == "workers")
    assert len(cloud.replicate_calls) == int(depth and depth_provider == "replicate")
    assert previews  # Manual mode offers a preview even for the all-people union.
    assert not cloud.enqueued  # no paid video generation in these prep proofs
    if selection != "all":
        assert job["tracking_masks_uri"] in cloud.objects
        assert job["tracking_uri"] in cloud.objects


def test_meta_masking_off_does_not_make_any_sam_request(monkeypatch, media):
    job, cloud, sam_inputs, _, _ = harness(monkeypatch, media, easy=True,
        enhanced=False, selection="single", audio_processing=False,
        preparation_provider="workers", sam_provider="meta", depth_guidance=False)
    assert job["status"] == "ready"
    assert not cloud.meta_calls and not sam_inputs
    assert not [call for call in cloud.calls if call[0] == "gpu"]


@pytest.mark.parametrize("code", ["meta_seed_not_found", "meta_seed_ambiguous"])
def test_uncertain_meta_initial_tap_has_editable_source_review(monkeypatch, media, code):
    job, cloud, sam_inputs, previews, _ = harness(monkeypatch, media, easy=True,
        enhanced=True, selection="multi", audio_processing=False,
        preparation_provider="workers", sam_provider="meta", depth_guidance=False,
        meta_seed_error=code)
    assert job["status"] == "tracking_review" and job["meta_seed_review"]
    assert job["tracking_plan"]["version"] == 3
    assert len(job["tracking_plan"]["segments"]) == 2
    assert job["tracking_metadata"]["scene_cuts"] == []
    assert job["guide_uri"] is None
    assert job["tracking_uri"] in cloud.objects
    assert not previews and not cloud.enqueued and not cloud.calls
    assert job["meta_seed_error"] == code


def test_meta_initial_anchor_miss_ignores_no_review_fallback_and_stops_extra_paid_batch(monkeypatch, media):
    from swapmein import cloudflare_recovery
    from swapmein.pipeline import Pipeline
    original = cloudflare_recovery.recover_identity_anchors
    def enabled_retry(pipeline, job, *args):
        return original(pipeline, {**job, "retry_missing_masks_with_sam": True}, *args)
    monkeypatch.setattr(cloudflare_recovery, "recover_identity_anchors", enabled_retry)
    monkeypatch.setattr(cloudflare_recovery, "run_mask_recovery",
                        lambda *args: pytest.fail("No extra automatic paid Meta recovery"))
    monkeypatch.setattr(Pipeline, "match_cloudflare_identities",
                        lambda *args: pytest.fail("Missing initial anchors need review before identity calls"))
    job, cloud, _, previews, _ = harness(monkeypatch, media, easy=True, enhanced=True,
        selection="multi", audio_processing=False, background_tap=True, sam_provider="meta",
        preparation_provider="workers", depth_guidance=False, prompt_variant="cloudflare_identity_v1",
        matching_no_review=True)
    assert job["status"] == "tracking_review", job.get("message")
    assert job["identity_matching_error"] == "meta_initial_anchor_review"
    assert not job.get("matching_fallback_used")
    assert len(cloud.meta_calls) == 1  # Only the already-approved primary preparation.
    assert previews and not cloud.enqueued
    assert not cloud.calls


@pytest.mark.parametrize("selection", ["single", "multi"])
def test_meta_option5_initial_miss_returns_source_review_before_extra_paid_recovery(monkeypatch, media, selection):
    monkeypatch.setattr("swapmein.initial_mask_recovery.recover_initial_selection",
                        lambda *args: pytest.fail("Initial Meta miss must not enter another paid recovery batch"))
    job, cloud, _, previews, _ = harness(monkeypatch, media, easy=True, enhanced=True,
        selection=selection, audio_processing=False, background_tap=True, sam_provider="meta",
        preparation_provider="workers", depth_guidance=False, prompt_variant="identity_tint_tracking_v1")
    assert job["status"] == "tracking_review" and job["meta_seed_review"], job.get("message")
    assert job["meta_seed_error"] == "meta_initial_anchor_review"
    assert job["guide_uri"] is None and job["tracking_uri"] in cloud.objects
    assert job["tracking_plan"]["version"] == 3
    assert len(cloud.meta_calls) == 1
    assert not cloud.enqueued and not cloud.calls and not previews
