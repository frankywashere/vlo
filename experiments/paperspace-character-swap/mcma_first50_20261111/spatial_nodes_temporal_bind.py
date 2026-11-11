"""Experiment-only node: source-locked target latent for the clip-4 test.

Installed as its OWN custom-node package (runtime/custom_nodes/pretty_latentlock_v2_nodes/__init__.py)
and whitelisted only by profile-latentlock_v2.json, so the main batch never imports it.

What it does: MiniMaxH3ReferenceToVideo returns an EMPTY target latent, so nothing ties
output frames to source frames. This node replaces the video half of that latent with the
VAE latent of the source shot (taken from the reference-video block already inside the
conditioning, so it is bit-identical to what the model sees as <Video 1>) and adds a
standard ComfyUI `noise_mask`:
    1.0  -> token is generated (as before)
    0.0  -> token is held at the source latent for every sampling step
    0..1 -> token ends as  value*generated + (1-value)*source  (a latent-space blend,
            NOT a "start from noised source" init; use BasicScheduler denoise for that)
The H3 model consumes this natively (model.py mask_row_values / per-row timesteps,
model_base.MiniMaxH3.scale_latent_inpaint, samplers.KSamplerX0Inpaint).
"""
import torch
import comfy.nested_tensor

FRAME_PER_TOKEN = (1, 4, 4, 4, 4)  # comfy.ldm.minimax.model.FRAME_PER_TOKEN
ALLOWED_JOBS = {"mcma_temporal_bind_pilot_v1"}


def token_first_frames(latent_t):
    firsts, frame = [], 0
    for k in range(latent_t):
        firsts.append(frame)
        frame += FRAME_PER_TOKEN[k % 5]
    return firsts, frame


class PrettySourceLockedLatent:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "positive": ("CONDITIONING",),
            "latent": ("LATENT",),
            "job_key": ("STRING", {"default": ""}),
            "shot_index": ("INT", {"default": 1}),
            "keep_from_frame": ("INT", {"default": 64, "min": 1, "max": 3600}),
            "feather_tokens": ("INT", {"default": 1, "min": 0, "max": 8}),
            "feather_value": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.01}),
            "generate_value": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
        }}

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "lock"
    CATEGORY = "Pretty/private"

    def lock(self, positive, latent, job_key, keep_from_frame, feather_tokens, feather_value, generate_value, shot_index):
        if job_key not in ALLOWED_JOBS:
            raise ValueError("Isolated latent-lock test only")
        samples = latent["samples"]
        if not getattr(samples, "is_nested", False):
            raise ValueError("Expected a MiniMax H3 AV latent")
        streams = samples.unbind()
        if len(streams) != 2 or streams[0].ndim != 5 or streams[0].shape[1] != 24:
            raise ValueError("Expected a MiniMax H3 AV latent")
        video, audio = streams
        if "noise_mask" in latent:
            raise ValueError("Latent already carries a noise mask")

        refs = positive[0][1].get("minimax_refs") or []
        videos = [r for r in refs if r.get("kind") in ("video", "video_audio")]
        if len(videos) != 1:
            raise ValueError("Expected exactly one reference video in the conditioning")
        source = videos[0]["latent"]
        if tuple(source.shape) != tuple(video.shape):
            raise ValueError("Reference-video latent {} does not match the target latent {}; "
                             "the source must be encoded on the target canvas and length".format(
                                 tuple(source.shape), tuple(video.shape)))

        latent_t = video.shape[2]
        firsts, total_frames = token_first_frames(latent_t)
        firsts.append(total_frames)
        if keep_from_frame not in firsts:
            raise ValueError("keep_from_frame must be the first frame of a latent token: {}".format(firsts))
        keep = firsts.index(keep_from_frame)
        if keep - feather_tokens < 1:
            raise ValueError("Nothing left to generate")

        mask = torch.ones((1, 1, latent_t, video.shape[3], video.shape[4]), dtype=torch.float32)
        mask[:, :, :keep - feather_tokens] = generate_value
        mask[:, :, keep - feather_tokens:keep] = feather_value
        mask[:, :, keep:] = 0.0

        # Spatial edit support is prepared and reviewed separately before generation.
        import numpy as np
        from pathlib import Path
        import torch.nn.functional as F
        shot_path = Path("/home/paperspace/ai/recastfx_mcma_first50_20261111/jobs") / job_key
        # Bind the cached mask to the exact source window length; caller supplies shot_index.
        data = np.load(shot_path / f"edit-mask-{shot_index:03d}.npz")
        spatial = torch.from_numpy(data['mask'].astype(np.float32))
        if spatial.shape[0] != total_frames: raise ValueError('Spatial mask frame count mismatch')
        rows=[]
        for k in range(latent_t):
            a=firsts[k]; z=firsts[k+1]
            pooled=spatial[max(0,a-2):min(total_frames,z+2)].amax(0)[None,None]
            pooled=F.max_pool2d(pooled,65,stride=1,padding=32)
            rows.append(F.adaptive_max_pool2d(pooled,(video.shape[3],video.shape[4]))[0,0])
        mask=torch.stack(rows,dim=0)[None,None]
        print("[MCMA_SPATIAL_EDIT] shot={} shape={} editable_share={:.6f} source_preserved_share={:.6f}".format(shot_index,tuple(mask.shape),float(mask.mean()),float((mask==0).float().mean())),flush=True)
        if mask.max()==0: raise ValueError('No editable foreground found')
        out = dict(latent)
        out["samples"] = comfy.nested_tensor.NestedTensor(
            (source.detach().to(device=video.device, dtype=video.dtype).clone(), audio))
        out["noise_mask"] = mask
        print("[PRETTY_LATENT_LOCK] latent_t={} frames={} generate tokens 0..{} (frames 0..{}) value={} | "
              "feather tokens {}..{} value={} | keep tokens {}..{} (frames {}..{})".format(
                  latent_t, total_frames, keep - feather_tokens - 1, firsts[keep - feather_tokens] - 1,
                  generate_value, keep - feather_tokens, keep - 1, feather_value,
                  keep, latent_t - 1, keep_from_frame, total_frames - 1), flush=True)
        return (out,)


NODE_CLASS_MAPPINGS = {"PrettySourceLockedLatent": PrettySourceLockedLatent}


class PrettyCheckpointSampledLatent:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"latent": ("LATENT",), "job_key": ("STRING", {"default": ""}), "shot_index": ("INT", {"default": 1})}}
    RETURN_TYPES = ("LATENT",)
    FUNCTION = "checkpoint"
    CATEGORY = "Pretty/private"
    def checkpoint(self, latent, job_key, shot_index):
        from pathlib import Path
        from safetensors.torch import save_file
        import os
        if job_key not in ALLOWED_JOBS: raise ValueError("Isolated v2 only")
        video, audio = latent["samples"].unbind()
        destination = Path("/home/paperspace/ai/recastfx_mcma_first50_20261111/jobs") / job_key / f"sampled-latent-{shot_index:03d}.safetensors"
        if destination.exists(): raise FileExistsError("Preserve existing sampled latent")
        temporary = destination.with_suffix(".partial")
        save_file({"video": video.detach().cpu().contiguous(), "audio": audio.detach().cpu().contiguous()}, str(temporary), metadata={"job_key": job_key, "stage": "after_sampling_before_split"})
        os.chmod(temporary, 0o600)
        with temporary.open("rb") as handle: os.fsync(handle.fileno())
        temporary.replace(destination)
        out = dict(latent)
        out.pop("noise_mask", None)
        print("[PRETTY_SAMPLE_CHECKPOINT] saved=" + str(destination) + " removed_post_sampling_mask=True", flush=True)
        return (out,)

NODE_CLASS_MAPPINGS["PrettyCheckpointSampledLatent"] = PrettyCheckpointSampledLatent


class McmaSaveEditMask:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'images':('IMAGE',),'male':('MASK',),'female':('MASK',),'shot_index':('INT',{'default':1})}}
    RETURN_TYPES=()
    OUTPUT_NODE=True
    FUNCTION='save'
    CATEGORY='Mcma/private'
    def save(self,images,male,female,shot_index):
        import numpy as np, subprocess, json
        from pathlib import Path
        root=Path('/home/paperspace/ai/recastfx_mcma_first50_20261111/jobs/mcma_first50_v1')
        assert len(images)==len(male)==len(female)
        mask=((male.cpu().numpy()>.5)|(female.cpu().numpy()>.5)).astype(np.uint8)
        np.savez_compressed(root/f'edit-mask-{shot_index:03d}.npz',mask=mask)
        picks=sorted(set([0,len(images)//4,len(images)//2,3*len(images)//4,len(images)-1]))
        from PIL import Image
        for frame in picks:
            rgb=images[frame].cpu().numpy().copy()
            rgb[mask[frame]>0]=rgb[mask[frame]>0]*.5+np.array([0,1,1])*.5
            Image.fromarray((rgb*255).clip(0,255).astype(np.uint8)).resize((544,384)).save(root/f'mask-review-{shot_index:03d}-{frame:03d}.jpg')
        (root/f'mask-report-{shot_index:03d}.json').write_text(json.dumps({'frames':len(images),'coverage':[int(x.sum()) for x in mask]}))
        return {}
NODE_CLASS_MAPPINGS['McmaSaveEditMask']=McmaSaveEditMask

class McmaPersonBindingMasks:
    @classmethod
    def INPUT_TYPES(cls):return {"required":{"job_key":("STRING",{"default":""})}}
    RETURN_TYPES=("MASK","MASK")
    FUNCTION="load"
    CATEGORY="Mcma/private"
    def load(self,job_key):
        if job_key not in ALLOWED_JOBS:raise ValueError("Isolated opening only")
        import numpy as np
        from pathlib import Path
        p=Path("/home/paperspace/ai/recastfx_mcma_first50_20261111/jobs")/job_key
        a=np.load(p/"edit-mask-001.npz")["mask"].astype(np.float32)
        assert a.shape==(56,768,1088)
        female=a.copy();female[:,:,557:]=0
        male=a.copy();male[:,:,:557]=0
        assert np.array_equal(male+female,a)
        return torch.from_numpy(male),torch.from_numpy(female)
NODE_CLASS_MAPPINGS["McmaPersonBindingMasks"]=McmaPersonBindingMasks
