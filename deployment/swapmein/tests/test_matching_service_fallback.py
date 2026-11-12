"""A failed matching service is not called again during manual mask review."""
from copy import deepcopy

import pytest

from test_mask_recheck_runtime import example, assert_no_old_approval, assert_other_person_unchanged


def forbid_extra_services(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Manual fallback cannot make another paid service request")
    monkeypatch.setattr("swapmein.cloudflare_credentials.server_cloudflare_credentials", forbidden)
    monkeypatch.setattr("swapmein.mask_identity_rechecks.recheck_mask_identities", forbidden)
    monkeypatch.setattr("swapmein.mask_recovery.run_mask_recovery", forbidden)
    monkeypatch.setattr("swapmein.initial_mask_recovery.recover_initial_selection", forbidden)
    monkeypatch.setattr("swapmein.meta_recovery.run_meta_mask_recovery", forbidden)


@pytest.mark.parametrize("model", ["minimax-h3", "kling-4-0"])
@pytest.mark.parametrize("provider", ["workers", "replicate", "meta"])
@pytest.mark.parametrize("mask_style", ["identity_overlay_multi_20", "none"])
@pytest.mark.parametrize("failure", ["cloudflare_matching_unavailable", "cloudflare_matching_budget"])
def test_failed_service_preserves_confirmations_and_opens_only_focused_manual_review(
        example, monkeypatch, model, provider, mask_style, failure):
    job = deepcopy(example.job)
    job.update(generation_model=model, sam_provider=provider, mask_style=mask_style,
        preparation_provider="replicate" if provider == "replicate" else "workers",
        prompt_variant="cloudflare_identity_v1", matching_no_review=True,
        identity_matching_completed=False, identity_matching_error=failure,
        identity_matching_recheck_blocked=True, tracking_reviewed=True)
    job["setup"].update(generation_model=model, enhanced_character_masking=mask_style != "none")
    example.records.data = deepcopy(job)
    forbid_extra_services(monkeypatch)
    original_bytes = example.cloud.objects["original"]
    result = example.pipeline.check_reviewed_mask_quality(job)
    assert result["status"] == "tracking_review" and result["tracking_plan"]["version"] == 4
    assert result["prompt_variant"] == "cloudflare_identity_v1"
    assert not result.get("matching_fallback_used")
    receipt = result["mask_identity_recheck_state"]
    assert receipt["status"] == "blocked_unavailable" and receipt["requests"] == 0
    assert receipt["reason"] == failure and receipt["evidence"] == []
    assert receipt["usage"]["cost_status"] == "not_submitted_service_unavailable"
    saved = result["targeted_mask_review"]
    for field in ("tracking_plan", "tracking_choices", "tracking_assignments", "tracking_metadata"):
        assert saved["base_" + field] == job[field]
    # Quality annotations may describe withheld tint; identity choices,
    # reference assignments and explicit presence decisions remain authoritative.
    for original, target in zip(job["normalized_targets"], saved["base_normalized_targets"]):
        for field in ("id", "reference_index", "anchor"):
            assert target[field] == original[field]
        for source_scene, checked_scene in zip(original["scenes"], target["scenes"]):
            for field, value in source_scene.items():
                assert checked_scene[field] == value
    assert saved["base_normalized_targets"][1] == job["normalized_targets"][1]
    assert [target["id"] for target in result["tracking_plan"]["targets"]] == ["one"]
    assert len(saved["checks"]) == 1 and saved["checks"][0]["target_id"] == "one"
    assert example.cloud.objects["original"] == original_bytes
    assert_other_person_unchanged(example, result)
    assert_no_old_approval(result)
    assert not example.calls and "source" not in example.cloud.downloads
    assert not example.cloud.compositions and not example.cloud.enqueued


@pytest.mark.parametrize("failure", ["cloudflare_matching_unavailable", "cloudflare_matching_budget"])
def test_legacy_failed_job_without_new_flag_also_blocks_repeat_matching(example, monkeypatch, failure):
    job = deepcopy(example.job)
    job.update(identity_matching_completed=False, identity_matching_error=failure)
    assert "identity_matching_recheck_blocked" not in job
    example.records.data = deepcopy(job)
    forbid_extra_services(monkeypatch)
    result = example.pipeline.check_reviewed_mask_quality(job)
    assert result["mask_identity_recheck_state"]["status"] == "blocked_unavailable"
    assert result["status"] == "tracking_review"
    assert not example.calls and "source" not in example.cloud.downloads
    assert_no_old_approval(result)


def test_stale_caller_cannot_ignore_newly_durable_service_failure(example, monkeypatch):
    stale = deepcopy(example.job)
    example.records.data.update(identity_matching_completed=False,
        identity_matching_error="cloudflare_matching_unavailable", identity_matching_recheck_blocked=True)
    forbid_extra_services(monkeypatch)
    result = example.pipeline.check_reviewed_mask_quality(stale)
    assert result["mask_identity_recheck_state"]["status"] == "blocked_unavailable"
    assert result["status"] == "tracking_review"
    assert not example.calls and "source" not in example.cloud.downloads


@pytest.mark.parametrize('failure', ['cloudflare_identity_uncertain', 'cloudflare_anchor_recovery'])
def test_uncertain_identity_or_missing_anchor_is_not_mislabeled_as_service_outage(example, failure):
    job=deepcopy(example.job)
    job.update(identity_matching_completed=False, identity_matching_error=failure,
        identity_matching_recheck_blocked=False)
    example.records.data=deepcopy(job)
    result=example.pipeline.check_reviewed_mask_quality(job)
    assert len(example.calls)==1
    assert result['mask_identity_recheck_state']['status']=='completed'
    assert result['reviewed_mask_quality']['identity_rechecks']['confirmed']==1


def test_existing_confirmed_evidence_survives_later_service_outage(example, monkeypatch):
    confirmed=example.pipeline.check_reviewed_mask_quality(deepcopy(example.job))
    assert len(example.calls)==1 and confirmed['mask_supplemental_anchors']
    # Force an actual geometry recheck; an already-confirmed proof may be reused
    # even when new API calls would now be blocked.
    confirmed['reviewed_mask_quality']['policy_version']='old-cache'
    confirmed.update(identity_matching_completed=False,
        identity_matching_error='cloudflare_matching_unavailable', identity_matching_recheck_blocked=True)
    example.records.data=deepcopy(confirmed)
    forbid_extra_services(monkeypatch)
    result=example.pipeline.check_reviewed_mask_quality(confirmed)
    assert len(example.calls)==1
    assert result['mask_identity_recheck_state']['status']=='completed'
    assert result['mask_supplemental_anchors']==confirmed['mask_supplemental_anchors']
    assert result['reviewed_mask_quality']['identity_rechecks']['confirmed']==1
    assert not result.get('targeted_mask_review')
    assert_other_person_unchanged(example,result)
