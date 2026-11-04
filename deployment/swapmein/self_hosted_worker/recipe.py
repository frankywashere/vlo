"""Frozen CharacterSwap recipe and validated source/subject mapping."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path

RECIPE_VERSION = 'characterswap_v1'
TEMPLATES = Path(__file__).parent / 'templates'

class WorkerError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

def conditioning_frames(delivery_frames: int) -> int:
    if not isinstance(delivery_frames, int) or not 0 < delivery_frames <= 360:
        raise WorkerError('mapping_required', 'A shot must contain between one and360 frames.')
    return max(56, ((max(0, delivery_frames - 5) + 16) // 17) * 17 + 5)

def shot_references(shot: dict) -> list[int]:
    indexes = shot.get('reference_indexes')
    if indexes is None:
        indexes = [] if shot.get('reference_index') is None else [shot['reference_index']]
    if not isinstance(indexes, list) or any(type(i) is not int or i < 0 or i > 99 for i in indexes) or len(indexes)>5:
        raise WorkerError('mapping_required', 'Shot reference assignments are invalid.')
    people = shot.get('person_ids')
    if people is not None and (not isinstance(people, list) or len(people) != len(indexes) or len(set(map(str, people))) != len(people)):
        raise WorkerError('mapping_required', 'Every present person needs one distinct reference assignment.')
    targets=shot.get('targets')
    if targets is not None:
        if not isinstance(targets,list) or any(not isinstance(t,dict) for t in targets) or [t.get('reference_index') for t in targets]!=indexes or people is not None and [t.get('id') for t in targets]!=people:
            raise WorkerError('mapping_required','Target identities and model reference slots disagree.')
    return indexes

def validate_plan(job: dict, frames: int, references: set[int]) -> list[dict]:
    shots = job.get('shots')
    if not isinstance(shots, list) or not shots or len(shots) > 100:
        raise WorkerError('mapping_required', 'An ordered shot plan is required.')
    cursor = 0
    checked = []
    for index, raw in enumerate(shots, 1):
        s = copy.deepcopy(raw)
        if type(s.get('index')) is not int or s.get('index') != index or type(s.get('start_frame')) is not int or type(s.get('end_frame')) is not int or s['start_frame'] != cursor:
            raise WorkerError('mapping_required', 'Shot boundaries must cover the source once in order.')
        length = s['end_frame'] - s['start_frame']
        expected = conditioning_frames(length)
        if s.get('conditioning_frames', expected) != expected or s['end_frame'] > frames:
            raise WorkerError('mapping_required', 'Shot conditioning length or boundary is invalid.')
        indexes = shot_references(s)
        if not set(indexes) <= references:
            raise WorkerError('mapping_required', 'A selected person has no staged reference.')
        if indexes and (not isinstance(s.get('prompt'), str) or not s['prompt'].strip() or len(s['prompt']) > 16000):
            raise WorkerError('mapping_required', 'A mapped shot prompt is required.')
        s.update(delivery_frames=length, conditioning_frames=expected, reference_indexes=indexes)
        checked.append(s)
        cursor = s['end_frame']
    if cursor != frames:
        raise WorkerError('mapping_required', 'The shot plan does not cover the complete source.')
    recipe = job.get('recipe') or {}
    for key, expected in {'seed':904239, 'steps':8, 'cfg':1, 'shift_video':12, 'shift_audio':3, 'video_shift':12, 'audio_shift':3, 'version':RECIPE_VERSION, 'fps':24, 'model':'ref2va_bf16', 'character_swap_strength':1, 'turbo_strength':1, 'sampler':'er_sde', 'scheduler':'normal', 'sam':False, 'face_mesh':False, 'depth':False, 'restore_original_audio':True}.items():
        if key in recipe and recipe[key] != expected:
            raise WorkerError('worker_failed', 'The requested recipe does not match this validated worker.')
    return checked

def geometry(width: int, height: int, maximum_pixels: int = 1536 * 864) -> dict:
    if width < 64 or height < 64 or width % 2 or height % 2:
        raise WorkerError('worker_failed', 'The normalized source geometry is unsupported.')
    cw, ch = math.ceil(width / 32) * 32, math.ceil(height / 32) * 32
    if cw * ch > maximum_pixels or max(cw, ch) > 2048:
        raise WorkerError('worker_failed', 'The normalized source exceeds this worker canvas limit.')
    return {'width':width, 'height':height, 'canvas_width':cw, 'canvas_height':ch, 'pad_left':(cw-width)//2, 'pad_top':(ch-height)//2, 'fps':24}

def build_graph(kind: str, route: str, job_key: str, shot: dict, shape: dict, aliases: dict | None = None) -> dict:
    g = json.loads((TEMPLATES / f'{kind}.json').read_text())
    aliases = aliases or {}
    route={'google_h3':'google','paperspace_h3':'paperspace'}.get(route,route)
    if route == 'google':
        if kind == 'encode':
            g['text'] = {'class_type':'CLIPLoader', 'inputs':{'clip_name':'qwen3vl_32b_minimax_h3_bf16.safetensors', 'type':'minimax', 'device':'default'}}
            for key in ['vae', 'audio_vae']:
                g[key] = {'class_type':'VAELoader', 'inputs':{'vae_name':g[key]['inputs']['vae_name']}}
        else:
            g['model'] = {'class_type':'UNETLoader', 'inputs':{'unet_name':'minimax_h3_ref2va_pruned_bf16.safetensors','weight_dtype':'default'}}
            g['vae'] = {'class_type':'VAELoader', 'inputs':{'vae_name':g['vae']['inputs']['vae_name']}}
    elif route != 'paperspace':
        raise WorkerError('worker_failed', 'Unknown self-hosted route.')
    for node, field in [('model','unet_name'),('text','clip_name'),('vae','vae_name'),('audio_vae','vae_name'),('turbo','lora_name'),('character','lora_name')]:
        if node in g and node in aliases:
            g[node]['inputs'][field] = aliases[node]
    if kind == 'encode':
        common = dict(job_key=job_key, start_frame=shot['start_frame'], end_frame=shot['end_frame'], conditioning_frames=shot['conditioning_frames'])
        g['source'] = {'class_type':'RecastFXWorkerSource','inputs':common}
        g['paired_audio'] = {'class_type':'RecastFXWorkerAudio','inputs':common}
        condition = {k:v for k,v in g['condition']['inputs'].items() if not k.startswith('ref_images.')}
        condition.update(prompt=shot['prompt'], width=shape['canvas_width'], height=shape['canvas_height'], length=shot['conditioning_frames'])
        for slot, reference in enumerate(shot['reference_indexes'], 1):
            node = f'reference_{slot}'
            g[node] = {'class_type':'RecastFXWorkerReference','inputs':{'job_key':job_key,'reference_index':reference}}
            condition[f'ref_images.ref_image_{slot}'] = [node,0]
        g['condition']['inputs'] = condition
        g['persist']['class_type'] = 'RecastFXWorkerSaveBundle'
        g['persist']['inputs'].update(job_key=job_key, shot_index=shot['index'])
    else:
        g['bundle'] = {'class_type':'RecastFXWorkerLoadBundle','inputs':{'job_key':job_key,'shot_index':shot['index']}}
        g['save'] = {'class_type':'RecastFXWorkerSaveExact','inputs':{'images':['decode',0], 'job_key':job_key,'shot_index':shot['index'],'delivery_frames':shot['delivery_frames']}}
    return g
