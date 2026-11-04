"""Capacity-only fallback reuses one job/credit receipt through the real API path."""
import copy
import time

from cryptography.fernet import Fernet
import pytest

import swapmein.app as gateway
from swapmein.errors import PublicError
from swapmein.google_fallback import fallback_google_capacity, refund_unsubmitted_fallback
from swapmein.pipeline import Pipeline
from swapmein.pipeline_settings import PATH
from swapmein.self_hosted_jobs import SelfHostedJobs, WorkerIdentity
from test_api import api, authenticated
from test_multi_person_api import upload_options
from test_self_hosted_jobs import prepared


def staged(api, *, credits=True, cipher=None):
    _, records, cloud, provider = api
    job = prepared(route="google_h3")
    original_targets = copy.deepcopy(job["setup"]["targets"])
    job.update(status="generating", source_uri=f"gs://private-app/uploads/{job['id']}/source.bin",
        generation_credits=60, app_credits_charged=60 if credits else 0,
        account_id="c" * 64, access_mode="credits" if credits else "api-key",
        audio_processing=False, depth_guidance=False, face_mesh=False, restore_original_audio=True,
        generation_started_at=time.time()-10, self_hosted_queued_at=time.time()-10,
        tracking_reviewed=True, self_hosted_completed_shots=[],
        normal_generation_snapshot={"version": 1, "provider": "reAPI", "settings": {
            "audio_processing": True, "depth_guidance": True, "face_mesh": True,
            "prompt_variant": "current", "matching_no_review": False, "multi_characters": True,
            "retry_missing_masks_with_sam": True, "restore_original_audio": False,
            "guide_style_version": 2, "depth_identity_colors_version": 1, "depth_overlay_opacity": .2,
            "preparation_provider": "workers"}})
    job["setup"].update(easy_mode=True, enhanced_character_masking=True)
    if cipher:
        job["google_fallback_key_ciphertext"] = cipher
    records.create(f"jobs/{job['id']}.json", job)
    records.data["control/self-hosted/google_h3.json"] = {
        "google_accepting_claims": False, "google_capacity_fallback_jobs": [job["id"]], "queue": []}
    records.data[f"accounts/{'c'*64}/wallet.json"] = {"balance": 140,
        "applied": {"generation:"+job["id"]: -60} if credits else {}, "transactions": []}
    return job, records, cloud, provider, original_targets


def test_capacity_fallback_restores_normal_pipeline_without_reusing_h3_assignments(api):
    job, records, cloud, provider, targets = staged(api)
    for _ in range(2):
        result = fallback_google_capacity(gateway.settings, records, cloud, job["id"])
    assert result["multi_person_generation_route"] == "current" and result["status"] == "queued"
    assert result["audio_processing"] is result["depth_guidance"] is result["face_mesh"] is True
    assert result["restore_original_audio"] is False and result["retry_missing_masks_with_sam"] is True
    assert result["setup"]["targets"] == targets and result["source_uri"] == job["source_uri"]
    assert result["reference_uris"] == job["reference_uris"] and result["generation_credits"] == 60
    assert not {"self_hosted_shots", "tracking_reviewed", "normalized_targets", "generation_started_at"} & result.keys()
    assert result["google_fallback_approved"] is True and result["google_fallback_enqueue_confirmed"] is True
    assert cloud.enqueued == [("admit", job["id"])] and not provider.submissions
    assert gateway.view(result)["generation_fallback"] == {"from": "google_h3", "reason": "capacity_unavailable"}
    assert records.data[f"accounts/{'c'*64}/wallet.json"]["balance"] == 140


@pytest.mark.parametrize("change", ["reason", "claims_open", "still_queued", "active", "started", "lease", "output", "unknown"])
def test_fallback_requires_confirmed_detached_capacity_failure_and_no_possible_gpu_work(api, change):
    job, records, cloud, provider, _ = staged(api)
    current = records.data[f"jobs/{job['id']}.json"]
    control = records.data["control/self-hosted/google_h3.json"]
    if change == "claims_open": control["google_accepting_claims"] = True
    if change == "still_queued": control["queue"] = [job["id"]]
    if change == "active": control["active"] = {"job_id": job["id"]}
    if change == "started": current["worker_started_at"] = time.time()
    if change == "lease": current["self_hosted_lease"] = {"token": "existing"}
    if change == "output": current["self_hosted_output_uri"] = "saved-result"
    if change == "unknown": current["status"] = "submission_unknown"
    before = copy.deepcopy(records.data)
    with pytest.raises(PublicError):
        fallback_google_capacity(gateway.settings, records, cloud, job["id"],
                                 reason="boot_timeout" if change == "reason" else "capacity_unavailable")
    assert records.data == before and not cloud.enqueued and not provider.submissions


def test_worker_claim_racing_fallback_cannot_be_reassigned(api, monkeypatch):
    job, records, cloud, _, _ = staged(api)
    original = records.update
    def race(path, mutation):
        if path == f"jobs/{job['id']}.json":
            records.data[path]["worker_started_at"] = time.time()
            records.data[path]["self_hosted_lease"] = {"token": "claimed"}
        return original(path, mutation)
    monkeypatch.setattr(records, "update", race)
    with pytest.raises(PublicError): fallback_google_capacity(gateway.settings, records, cloud, job["id"])
    assert records.data[f"jobs/{job['id']}.json"]["multi_person_generation_route"] == "google_h3"
    assert not cloud.enqueued


@pytest.mark.parametrize("missing", ["snapshot", "source", "references", "malformed_snapshot"])
def test_unsupported_fallback_stops_actionably_and_refunds_known_unused_reservation(api,missing):
    job,records,cloud,provider,_=staged(api)
    current=records.data[f"jobs/{job['id']}.json"]
    current.pop({"snapshot":"normal_generation_snapshot","source":"source_uri","references":"reference_uris"}.get(missing,"missing"),None)
    if missing=="malformed_snapshot":current["normal_generation_snapshot"]=["invalid"]
    result=fallback_google_capacity(gateway.settings,records,cloud,job["id"])
    assert result["status"]=="failed" and result["google_fallback_enqueue_confirmed"] is True
    assert result["google_fallback_error"]=="normal_preparation_unavailable"
    assert result["google_fallback_credit_state"]=="refunded"
    assert records.data[f"accounts/{'c'*64}/wallet.json"]["balance"]==200
    assert not cloud.enqueued and not provider.submissions


def test_fallback_queue_ack_loss_reuses_original_semantic_token(api, monkeypatch):
    job, records, cloud, _, _ = staged(api)
    tokens=[]
    def lose_ack(kind, job_id, *, task_token=None, delay_seconds=0):
        tokens.append((kind, job_id, task_token))
        if len(tokens)==1: raise TimeoutError("Queue outcome unknown")
    monkeypatch.setattr(cloud, "enqueue", lose_ack)
    result=fallback_google_capacity(gateway.settings, records, cloud, job["id"])
    assert result["google_fallback_enqueue_confirmed"] is False
    result=fallback_google_capacity(gateway.settings, records, cloud, job["id"])
    assert result["google_fallback_enqueue_confirmed"] is True and tokens[0]==tokens[1]
    result["status"]="preparing";records.data[f"jobs/{job['id']}.json"]=result
    fallback_google_capacity(gateway.settings, records, cloud, job["id"])
    assert len(tokens)==2


@pytest.mark.parametrize("cipher", [None, "already-encrypted"])
def test_api_key_fallback_never_borrows_the_server_key(api, cipher):
    job, records, cloud, _, _ = staged(api, credits=False, cipher=cipher)
    result=fallback_google_capacity(gateway.settings, records, cloud, job["id"])
    assert result["auto_key_ciphertext"] == cipher
    assert result["google_fallback_requires_key"] is (cipher is None)
    assert result["google_fallback_approved"] is (cipher is not None)
    assert records.data[f"accounts/{'c'*64}/wallet.json"]["applied"] == {}


def test_normal_provider_submission_reuses_one_debit_and_one_admission(api, monkeypatch):
    job, records, cloud, provider, targets=staged(api)
    result=fallback_google_capacity(gateway.settings, records, cloud, job["id"])
    result.update(status="ready", guide_uri="gs://private-app/masked-normal-guide.mp4",
                  normalized_targets=targets, mask_style="depth_multi")
    records.data[f"jobs/{job['id']}.json"]=result
    admissions=[]
    class Capacity:
        def __init__(self,*_):pass
        def enabled(self):return True
        def claim_provider(self,job_id):admissions.append(job_id);return True
    monkeypatch.setattr(gateway,"ProcessingCapacity",Capacity)
    for _ in range(2):
        current=records.read(f"jobs/{job['id']}.json")[0]
        gateway.submit_generation(job["id"],current,{"access_mode":"credits"},"normal-server-key")
    assert admissions==[job["id"]] and len(provider.submissions)==1
    wallet=records.data[f"accounts/{'c'*64}/wallet.json"]
    assert wallet["balance"]==140 and wallet["applied"]=={"generation:"+job["id"]:-60}


def test_refunded_reservation_can_never_be_used_for_a_free_hosted_render(api):
    job,records,cloud,provider,targets=staged(api)
    result=fallback_google_capacity(gateway.settings,records,cloud,job["id"])
    result.update(status="ready",guide_uri="gs://private-app/masked.mp4",normalized_targets=targets,mask_style="depth_multi")
    records.data[f"jobs/{job['id']}.json"]=result
    wallet=records.data[f"accounts/{'c'*64}/wallet.json"]
    wallet.update(balance=200,applied={"generation:"+job["id"]:-60,"refund:"+job["id"]:60})
    with pytest.raises(PublicError) as error:
        gateway.submit_generation(job["id"],result,{"access_mode":"credits"},"normal-server-key")
    assert error.value.status_code==409 and not provider.submissions
    assert records.data[f"accounts/{'c'*64}/wallet.json"]["balance"]==200


def test_cold_google_is_launch_available_without_claimable_heartbeat_and_lifecycle_errors_block_it(api,monkeypatch):
    from swapmein.self_hosted_jobs import RECIPE_VERSION, worker_views, require_worker_ready
    from swapmein.commerce import feature_view
    _,records,_,_=api
    monkeypatch.setenv("SELF_HOSTED_GOOGLE_H3_WORKER_TOKEN","safe-google-test-token-"+"a"*32)
    monkeypatch.setattr("swapmein.google_lifecycle.on_demand_available",lambda *_:True)
    monkeypatch.setattr("swapmein.commerce.provider_key_ready",lambda:False)
    records.data["commerce/features.json"]={"credit_generation":True}
    records.data[PATH]={"multi_person_generation_route":"google_h3"}
    view=worker_views(gateway.settings,records)["google_h3"]
    assert view["ready"] is False and view["on_demand_available"] is True
    require_worker_ready(gateway.settings,records,"google_h3")
    assert feature_view(records,generation_route="google_h3")["credit_generation"] is True
    assert feature_view(records,generation_route="current")["credit_generation"] is False
    records.data["control/self-hosted/google_h3.json"]={"recipe_version":RECIPE_VERSION,
        "heartbeat_at":time.time(),"google_lifecycle_error":"start_outcome_unknown"}
    view=worker_views(gateway.settings,records)["google_h3"]
    assert view["ready"] is view["on_demand_available"] is False
    with pytest.raises(PublicError):require_worker_ready(gateway.settings,records,"google_h3")
    assert feature_view(records,generation_route="google_h3")["credit_generation"] is False


def test_failed_normal_readmission_refunds_reservation_without_requiring_user_refresh(api,monkeypatch):
    job,records,cloud,provider,_=staged(api)
    result=fallback_google_capacity(gateway.settings,records,cloud,job["id"])
    monkeypatch.setattr(gateway,"internal_pipeline",lambda *_:None)
    def unavailable(*_):raise PublicError("Preparation needs operator recovery",503,code="preparation_recovery")
    monkeypatch.setattr(gateway,"preparation_limits",unavailable)
    gateway.admit_preparation(gateway.InternalTask(job_id=job["id"],task_token=result["admission_token"]),None)
    assert records.data[f"jobs/{job['id']}.json"]["status"]=="failed"
    assert records.data[f"accounts/{'c'*64}/wallet.json"]["balance"]==200
    assert not provider.submissions


def test_fallback_reenters_sam_preparation_and_failed_prep_refunds_exactly_once(api, monkeypatch):
    job,records,cloud,provider,targets=staged(api)
    job["normal_generation_snapshot"]["settings"]["preparation_provider"]="replicate"
    records.data[f"jobs/{job['id']}.json"]=job
    result=fallback_google_capacity(gateway.settings,records,cloud,job["id"])
    records.data["control/preparation.json"]={"active":{"job_id":job["id"],"until":time.time()+1800}}
    cloud.shutdown_verified=True
    monkeypatch.setattr(cloud,"download",lambda _uri,path,_maximum:path.write_bytes(b"source"))
    monkeypatch.setattr(cloud,"check_cap",lambda:None,raising=False)
    metadata={"duration":5,"width":1280,"height":720,"frames":120,
        "anchor":{"x":.2,"y":.5,"frame":12,"time":.5},"scene_cuts":[],"targets":targets}
    def normalize(_source,path,_options):path.write_bytes(b"normal RGB");return copy.deepcopy(metadata)
    monkeypatch.setattr("swapmein.pipeline.normalize",normalize)
    calls=[]
    def normal_sam(records_,cloud_,job_):
        calls.append(job_["preparation_provider"])
        raise PublicError("Controlled normal-SAM preparation failure",503)
    monkeypatch.setattr("swapmein.pipeline.ReplicatePreparation",normal_sam)
    Pipeline(gateway.settings,records,cloud).prepare(job["id"],result["tracking_task_token"])
    assert calls==["replicate"] and not provider.submissions
    current=records.read(f"jobs/{job['id']}.json")[0]
    assert current["status"]=="failed"
    for _ in range(2):refund_unsubmitted_fallback(records,current)
    wallet=records.data[f"accounts/{'c'*64}/wallet.json"]
    assert wallet["balance"]==200 and wallet["applied"]=={
        "generation:"+job["id"]:-60,"refund:"+job["id"]:60}


def test_google_upload_pins_normal_settings_and_encrypted_optional_key_before_admin_changes(api,monkeypatch):
    client,records,_,_=api
    headers,_=authenticated(api)
    monkeypatch.setenv("RECASTFX_JOB_KEY",Fernet.generate_key().decode())
    records.data[PATH]={"multi_person_generation_route":"google_h3","audio_processing":True,
                        "face_mesh_enabled":True,"depth_guidance":True,"prompt_variant":"favorite_v2"}
    ticket=client.post("/api/uploads",headers=headers,json=upload_options())
    assert ticket.status_code==201,ticket.text
    job_id=ticket.json()["id"];manifest=records.data[f"uploads/{job_id}/manifest.json"]
    pinned=copy.deepcopy(manifest["normal_generation_snapshot"])
    assert pinned["settings"]["audio_processing"] is pinned["settings"]["depth_guidance"] is True
    assert manifest.get("google_fallback_key_ciphertext") and "valid-example-key" not in str(manifest)
    records.data[PATH]={"multi_person_generation_route":"current","audio_processing":False,"depth_guidance":False}
    response=client.post(f"/api/uploads/{job_id}/complete",headers=headers)
    assert response.status_code==202,response.text
    current=records.data[f"jobs/{job_id}.json"]
    assert current["normal_generation_snapshot"]==pinned
    assert current["audio_processing"] is current["depth_guidance"] is False
