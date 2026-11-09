"""Kling audio orchestration, with local media and fake inference only.

The worker double uses a real audio remux to expose which soundtrack reaches
Kling. SAM, Demucs and matching never call a model or external service here.
"""
import copy
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import numpy as np
from PIL import Image
import pytest

import swapmein.app as gateway
from swapmein.config import Settings
from swapmein.media import Setup, decoded_audio_samples, kling_guide_media, normalize, original_guide, probe, require_audio_duration
from swapmein.multi_tracking import build_selected_masks, selected_tracks
from swapmein.pipeline import Pipeline
from swapmein.tracking import resolve_tracking
from test_api import MemoryRecords, api, authenticated, ready_job
from test_kling_media import audio_packets


@pytest.fixture(scope="module", params=[15, 30])
def kling_media(request, tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required for real guide audio proof")
    duration = request.param
    root = tmp_path_factory.mktemp(f"kling-audio-{duration}")
    source, normalized, processed, masks = [root / name for name in
        ("source.mp4", "normalized.mp4", "vocals_plus3.wav", "masks.zip")]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"color=c=red:s=256x256:r=24:d={duration+2}", "-f", "lavfi", "-i",
        f"sine=frequency=440:duration={duration+2}", "-c:v", "libx264", "-threads", "2",
        "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], check=True)
    setup = Setup.model_validate({"generation_model":"kling-4-0", "trim":[1.25,duration+1.25],
        "anchor":{"x":.5,"y":.5,"time":2.25}, "preparation_version":2})
    metadata = normalize(source, normalized, setup)
    # A distinguishable preprocessed-vocals stand-in at the +3-semitone
    # frequency and the observed worker's 94 ms short tail. Do not require
    # local rubberband or download/run Demucs. Composition must pad that tail.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"sine=frequency={440*1.189207115}:duration={duration-.094}",
        "-ar", "44100", "-ac", "2", str(processed)], check=True)
    labels = np.zeros((256,256), np.uint16)
    labels[64:192,64:192] = 7
    data=BytesIO(); Image.fromarray(labels).save(data,"PNG")
    with zipfile.ZipFile(masks,"w",zipfile.ZIP_DEFLATED) as archive:
        for frame in range(duration*24):
            archive.writestr(f"labels/labels_{frame:05d}.png",data.getvalue())
    return {"duration":duration,"source":source,"normalized":normalized,"processed":processed,
            "masks":masks,"metadata":metadata,"setup":setup}


def dominant_frequency(path):
    raw=subprocess.check_output(["ffmpeg","-v","error","-ss","2","-i",str(path),
        "-map","0:a:0","-t","1","-ac","1","-ar","44100","-f","f32le","-"])
    samples=np.frombuffer(raw,np.float32)
    return int(np.argmax(np.abs(np.fft.rfft(samples)))) * 44100 / len(samples)


def test_cpu_compositor_pads_short_pitch_tail_before_kling_audio_stream_copy(monkeypatch,kling_media,tmp_path):
    patch=Path(__file__).parents[1]/"worker_patch"
    def load(name,filename):
        spec=importlib.util.spec_from_file_location(name,patch/filename)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    monkeypatch.setitem(sys.modules,"common",load("kling_audio_common","common.py"))
    compositor=load("kling_audio_compositor","composite.py")
    prepared,guide=tmp_path/"composed.mp4",tmp_path/"kling.mp4"
    metrics=compositor.compose(kling_media["normalized"],kling_media["masks"],kling_media["processed"],
                               prepared,7,"translucent")
    assert metrics["frames"]==kling_media["duration"]*24
    require_audio_duration(probe(prepared),kling_media["duration"])
    assert decoded_audio_samples(prepared)>=kling_media["duration"]*44100
    kling_guide_media(prepared,guide,kling_media["metadata"])
    assert audio_packets(prepared)==audio_packets(guide)
    assert abs(dominant_frequency(guide)-440*1.189207115)<3


def prepare_audio_job(monkeypatch, fixture, *, manual, enabled, silent):
    job_id="e"*32
    records=MemoryRecords()
    options=fixture["setup"].model_copy(update={"easy_mode":not manual,"auto_generate":False,
                                               "submit_without_audio":silent})
    records.data[f"jobs/{job_id}.json"]={"id":job_id,"status":"queued","source_uri":"gs://app/upload.bin",
        "setup":options.model_dump(),"generation_model":"kling-4-0","audio_processing":enabled,
        "restore_original_audio":True,"face_mesh":False,"depth_guidance":False,"guide_style_version":2,
        "prompt_variant":"cloudflare_identity_v1","matching_no_review":False,
        "preparation_provider":"replicate" if fixture["duration"]>15 else "workers"}
    records.data["control/preparation.json"]={"active":{"job_id":job_id,"until":time.time()+1800}}
    class Cloud:
        shutdown_verified=True
        def __init__(self):
            self.calls=[];self.enqueued=[]
            self.objects={"gs://app/upload.bin":fixture["source"].read_bytes(),
                          "gs://worker/sam/masks.zip":fixture["masks"].read_bytes(),
                          "gs://worker/sam/sam_preview.mp4":fixture["normalized"].read_bytes(),
                          "gs://worker/audio/vocals_plus3.wav":fixture["processed"].read_bytes()}
        def check_cap(self):pass
        def enqueue(self,kind,queued_id,**kwargs):self.enqueued.append((kind,queued_id))
        def download(self,uri,destination,*_):destination.write_bytes(self.objects[uri])
        def upload(self,bucket,key,source,_content_type):
            uri=f"gs://{bucket}/{key}";self.objects[uri]=source.read_bytes();return uri
        def copy(self,uri,bucket,key):
            result=f"gs://{bucket}/{key}";self.objects[result]=self.objects[uri];return result
        def worker_output(self,result,_run,filename):
            return result[filename]
        @contextmanager
        def worker(self,kind):
            def invoke(endpoint,payload,_timeout):
                self.calls.append((kind,endpoint,copy.deepcopy(payload)))
                if kind=="gpu":
                    assert payload["operation"]=="sam" and fixture["duration"]<=15
                    return {"masks.zip":"gs://worker/sam/masks.zip","sam_preview.mp4":"gs://worker/sam/sam_preview.mp4"}
                if endpoint=="/benchmark":
                    return {"vocals_plus3.wav":"gs://worker/audio/vocals_plus3.wav"}
                assert endpoint=="/compose"
                # Real remux preserves the selected soundtrack. Pixel/tint and
                # frame projection are covered by the compositor's own tests.
                with tempfile.TemporaryDirectory() as temporary:
                    root=Path(temporary); source=root/"source.mp4";audio=root/"audio.bin";guide=root/"guide.mp4"
                    self.download(payload["input_uri"],source)
                    self.download(payload["guide_audio_uri"],audio)
                    original_guide(source,audio,guide,fixture["metadata"])
                    uri=self.upload("worker",f"{payload['run_id']}/prepared_guide.mp4",guide,"video/mp4")
                return {"prepared_guide.mp4":uri}
            yield invoke
    cloud=Cloud()
    class HostedPreparation:
        def __init__(self,*_):pass
        def prepare(self,_root,metadata,sam_metadata,*_):
            assert metadata["frames"]==sam_metadata["frames"]==fixture["duration"]*24
            return {"depth_uri":None,"masks_uri":"gs://worker/sam/masks.zip"}
    monkeypatch.setattr("swapmein.pipeline.ReplicatePreparation",HostedPreparation)
    def match(self,job,source,masks,metadata,targets,root,remaining):
        destination=root/"matched.zip"
        coverage=build_selected_masks(masks,metadata,selected_tracks(masks,metadata,targets),destination)
        return {"masks_path":destination,"coverage":coverage,"targets":targets,"assignments":[],"diagnostics":{"calls":0}}
    monkeypatch.setattr(Pipeline,"match_cloudflare_identities",match)
    monkeypatch.setattr(Pipeline,"save_scene_thumbnails",lambda *_:None)
    monkeypatch.setattr("swapmein.pipeline.create_selection_preview",lambda source,masks,output,metadata:shutil.copyfile(source,output))
    pipeline=Pipeline(replace(Settings(),app_bucket="app",worker_bucket="worker"),records,cloud)
    pipeline.prepare(job_id)
    return pipeline,records,cloud,records.data[f"jobs/{job_id}.json"]


@pytest.mark.parametrize("manual",[False,True])
@pytest.mark.parametrize("enabled,silent",[(True,False),(False,False),(True,True)])
def test_kling_initial_and_reviewed_guides_use_the_pinned_audio_for_15_and_30_seconds(
        monkeypatch,kling_media,tmp_path,manual,enabled,silent):
    pipeline,records,cloud,job=prepare_audio_job(monkeypatch,kling_media,manual=manual,enabled=enabled,silent=silent)
    processing=enabled and not silent
    assert job["status"]==("tracking_review" if manual else "ready"),job.get("message")
    audio_calls=[payload for kind,endpoint,payload in cloud.calls if kind=="audio" and endpoint=="/benchmark"]
    assert len(audio_calls)==int(processing)
    if audio_calls:
        assert audio_calls[0]["input_uri"]==job["normalized_uri"]
    if manual:
        assert not any(endpoint=="/compose" for _,endpoint,_ in cloud.calls)
        archive,reviewed=tmp_path/"raw.zip",tmp_path/"reviewed.zip"
        archive.write_bytes(cloud.objects[job["tracking_masks_uri"]])
        choices=[{"segment_id":section["id"],"absent":False,"anchor":{
            "x":.5,"y":.5,"time":section["seek_time"]}}
            for section in job["tracking_plan"]["segments"] if section["needs_review"]]
        resolved=resolve_tracking(archive,job["tracking_metadata"],job["tracking_plan"],choices,reviewed)
        uri=cloud.upload("worker","reviewed.zip",reviewed,"application/zip")
        job.update(status="preparing",reviewed_masks_uri=uri,reviewed_coverage=resolved["coverage"],
                   tracking_applied_revision=1,generation_after_review_revision=None,tracking_task_token="b"*32)
        records.data[f"jobs/{job['id']}.json"]=copy.deepcopy(job)
        pipeline.rebuild_reviewed_guide(job,lambda:600)
        job=records.data[f"jobs/{job['id']}.json"]
        assert job["status"]=="ready" and job["tracking_reviewed"] is True
    composed=[payload for _,endpoint,payload in cloud.calls if endpoint=="/compose"]
    assert len(composed)==1
    audio_uri=composed[0]["guide_audio_uri"]
    assert cloud.objects[audio_uri]==(kling_media["processed"].read_bytes() if processing
                                       else cloud.objects[job["normalized_uri"]])
    assert len([1 for kind,endpoint,_ in cloud.calls if kind=="audio" and endpoint=="/benchmark"])==int(processing)
    guide=tmp_path/"kling.mp4";guide.write_bytes(cloud.objects[job["guide_uri"]])
    require_audio_duration(probe(guide),kling_media["duration"])
    assert decoded_audio_samples(guide)>=kling_media["duration"]*44100
    if processing:
        assert abs(decoded_audio_samples(kling_media["processed"])/44100-kling_media["duration"]+.094)<.001
    video=next(s for s in probe(guide)["streams"] if s["codec_type"]=="video")
    assert int(video["nb_frames"])==kling_media["duration"]*24 and video["height"]>=700
    assert abs(dominant_frequency(guide)-(440*1.189207115 if processing else 440))<3
    prepared_uri=next(uri for uri in cloud.objects if uri.endswith("/prepared_guide.mp4"))
    prepared=tmp_path/"prepared.mp4";prepared.write_bytes(cloud.objects[prepared_uri])
    assert audio_packets(prepared)==audio_packets(guide)


@pytest.mark.parametrize("duration",[15,30])
def test_kling_silent_submission_strips_only_the_submitted_copy_once(api,monkeypatch,duration):
    client,records,cloud,provider=api
    headers,owner=authenticated(api)
    job_id=ready_job(records,owner);job=records.data[f"jobs/{job_id}.json"]
    job.update(generation_model="kling-4-0",duration=duration,face_mesh=False,audio_processing=False,
               restore_original_audio=True,mask_style="identity_overlay_20",source_uri="gs://private-app/source.bin")
    job["setup"].update(generation_model="kling-4-0",trim=[0,duration],submit_without_audio=True,
                        enhanced_character_masking=True)
    downloads=[];stripped=[]
    monkeypatch.setattr(provider,"balance",lambda _:100000)
    monkeypatch.setattr(cloud,"asset_exists",lambda uri:True,raising=False)
    def download(uri,destination):downloads.append(uri);destination.write_bytes(b"audible-preview")
    def strip(source,destination):
        assert source.read_bytes()==b"audible-preview";stripped.append(True);destination.write_bytes(b"silent-guide")
    monkeypatch.setattr(cloud,"download",download)
    monkeypatch.setattr(gateway,"strip_audio",strip)
    for _ in range(2):
        response=client.post(f"/api/jobs/{job_id}/generate",headers=headers,json={"approved":True})
        assert response.status_code==200,response.text
    assert len(provider.submissions)==1 and len(stripped)==1
    assert "silent-" in provider.submissions[0]["video_url"]
    assert downloads==["gs://private-app/guide.mp4"]
    assert job["guide_uri"]=="gs://private-app/guide.mp4"
    assert "silent" in provider.submissions[0]["prompt"].lower()
    assert "pitched" not in provider.submissions[0]["prompt"].lower()


@pytest.mark.parametrize("duration",[15,30])
@pytest.mark.parametrize("enabled",[False,True])
def test_kling_export_restores_original_upload_trim_even_if_provider_audio_is_pitched_or_missing(monkeypatch,duration,enabled):
    job_id="d"*32;path=f"jobs/{job_id}.json";records=MemoryRecords()
    records.data[path]={"id":job_id,"status":"finalizing","duration":duration,"generation_model":"kling-4-0",
        "audio_processing":enabled,"restore_original_audio":True,"provider_output":"https://cdn.reapi.ai/output.mp4",
        "source_uri":"gs://app/upload.bin","normalized_uri":"gs://app/normalized.mp4",
        "setup":{"trim":[1.25,duration+1.25]}}
    class Cloud:
        def download_provider(self,_url,destination):destination.write_bytes(b"video-with-untrusted-audio")
        def download(self,uri,destination,*_):
            destination.write_bytes(b"original-full-upload" if uri.endswith("upload.bin") else b"trimmed-rgb")
        def upload(self,bucket,key,source,_type):
            assert source.read_bytes()==b"finished-original-audio";return f"gs://{bucket}/{key}"
    def finish(raw,original,output,seconds,*,audio_source,audio_start,restore_audio=True):
        assert raw.read_bytes()==b"video-with-untrusted-audio" and original.read_bytes()==b"trimmed-rgb"
        assert audio_source.read_bytes()==b"original-full-upload"
        assert audio_start==1.25 and seconds==duration and restore_audio is True
        output.write_bytes(b"finished-original-audio");return {"width":1280,"height":720}
    monkeypatch.setattr("swapmein.pipeline.finish",finish)
    monkeypatch.setattr(Pipeline,"ensure_library_poster",lambda self,job,*_:job)
    monkeypatch.setattr(Pipeline,"complete_library_delivery",lambda self,job:None)
    Pipeline(replace(Settings(),app_bucket="app"),records,Cloud()).finalize(job_id)
    assert records.data[path]["status"]=="completed",records.data[path].get("message")
