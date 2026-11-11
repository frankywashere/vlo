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
ALLOWED_JOBS = {"pretty_shot004_latentlock_v2"}


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
            "keep_from_frame": ("INT", {"default": 64, "min": 1, "max": 3600}),
            "feather_tokens": ("INT", {"default": 1, "min": 0, "max": 8}),
            "feather_value": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.01}),
            "generate_value": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
        }}

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "lock"
    CATEGORY = "Pretty/private"

    def lock(self, positive, latent, job_key, keep_from_frame, feather_tokens, feather_value, generate_value):
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
        if keep_from_frame not in firsts:
            raise ValueError("keep_from_frame must be the first frame of a latent token: {}".format(firsts))
        keep = firsts.index(keep_from_frame)
        if keep - feather_tokens < 1:
            raise ValueError("Nothing left to generate")

        mask = torch.ones((1, 1, latent_t, video.shape[3], video.shape[4]), dtype=torch.float32)
        mask[:, :, :keep - feather_tokens] = generate_value
        mask[:, :, keep - feather_tokens:keep] = feather_value
        mask[:, :, keep:] = 0.0

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
        return {"required": {"latent": ("LATENT",), "job_key": ("STRING", {"default": ""})}}
    RETURN_TYPES = ("LATENT",)
    FUNCTION = "checkpoint"
    CATEGORY = "Pretty/private"
    def checkpoint(self, latent, job_key):
        from pathlib import Path
        from safetensors.torch import save_file
        import os
        if job_key != "pretty_shot004_latentlock_v2": raise ValueError("Isolated v2 only")
        video, audio = latent["samples"].unbind()
        destination = Path("/home/paperspace/ai/recastfx_pretty_full_music_20261109_v3/jobs") / job_key / "sampled-latent.safetensors"
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
