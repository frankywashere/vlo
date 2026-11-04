"""Private job-scoped source, output and lossless H3 conditioning nodes.

Install into the dedicated runtime's custom_nodes only. Model/kernel code is not
vendored here. The outbound driver supplies RECASTFX_WORKER_SPOOL to its child.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import numpy as np
import torch
from PIL import Image,ImageOps
from safetensors.torch import load_file,save_file
import comfy.nested_tensor

SAFE=re.compile(r'^[A-Za-z0-9_-]{1,96}$')
def directory(job_key):
    if not isinstance(job_key,str) or not SAFE.fullmatch(job_key):raise ValueError('Invalid private job identity')
    configured=os.environ.get('RECASTFX_WORKER_SPOOL')
    if not configured or not Path(configured).is_absolute():raise ValueError('Private worker spool is not configured')
    root=Path(configured).resolve();path=(root/job_key).resolve()
    if path.parent!=root or not path.is_dir():raise ValueError('Private job is not staged')
    return path

def manifest(job_key):
    p=directory(job_key);return p,json.loads((p/'job.json').read_text())

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def shot_inputs():
    return {'job_key':('STRING',{'default':''}),'start_frame':('INT',{'default':0,'min':0}),'end_frame':('INT',{'default':1,'min':1}),'conditioning_frames':('INT',{'default':56,'min':56,'max':362})}

def source_bounds(data,start,end,conditioning):
    n=end-start;expected=max(56,((max(0,n-5)+16)//17)*17+5)
    if not 0<=start<end<=data['geometry']['frames'] or conditioning!=expected or n>360:raise ValueError('Source bounds do not match the frozen shot recipe')
    if not any(s['start_frame']==start and s['end_frame']==end and s['conditioning_frames']==conditioning for s in data['shots']):raise ValueError('Shot is not in the frozen private plan')

class RecastFXWorkerSource:
    @classmethod
    def INPUT_TYPES(cls):return {'required':shot_inputs()}
    RETURN_TYPES=('IMAGE',);FUNCTION='load';CATEGORY='RecastFX/private worker'
    def load(self,job_key,start_frame,end_frame,conditioning_frames):
        p,m=manifest(job_key);source_bounds(m,start_frame,end_frame,conditioning_frames);g=m['geometry'];n=end_frame-start_frame
        vf=f"trim=start_frame={start_frame}:end_frame={end_frame},setpts=PTS-STARTPTS,pad={g['canvas_width']}:{g['canvas_height']}:{g['pad_left']}:{g['pad_top']},tpad=stop_mode=clone:stop={conditioning_frames-n}"
        raw=subprocess.check_output(['ffmpeg','-v','error','-threads','4','-i',str(p/'source.mp4'),'-vf',vf,'-frames:v',str(conditioning_frames),'-f','rawvideo','-pix_fmt','rgb24','pipe:1'],timeout=180)
        a=np.frombuffer(raw,dtype=np.uint8).reshape(conditioning_frames,g['canvas_height'],g['canvas_width'],3).copy()
        return (torch.from_numpy(a).float().div_(255),)

class RecastFXWorkerAudio:
    @classmethod
    def INPUT_TYPES(cls):return {'required':shot_inputs()}
    RETURN_TYPES=('AUDIO',);FUNCTION='load';CATEGORY='RecastFX/private worker'
    def load(self,job_key,start_frame,end_frame,conditioning_frames):
        p,m=manifest(job_key);source_bounds(m,start_frame,end_frame,conditioning_frames);duration=conditioning_frames/24
        if m['geometry']['has_audio']:
            af=f'atrim=start={start_frame/24:.9f}:end={end_frame/24:.9f},asetpts=PTS-STARTPTS,apad=whole_dur={duration:.9f}'
            pcm=subprocess.check_output(['ffmpeg','-v','error','-i',str(p/'source.mp4'),'-map','0:a:0','-af',af,'-t',str(duration),'-ar','32000','-ac','2','-f','f32le','-'],timeout=120)
            a=np.frombuffer(pcm,dtype=np.float32).copy().reshape(-1,2).T
        else:a=np.zeros((2,round(duration*32000)),dtype=np.float32)
        if abs(a.shape[-1]-round(duration*32000))>1:raise ValueError('Conditioning audio has the wrong duration')
        return ({'waveform':torch.from_numpy(a).unsqueeze(0),'sample_rate':32000},)

class RecastFXWorkerReference:
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'job_key':('STRING',{'default':''}),'reference_index':('INT',{'default':1,'min':0,'max':99})}}
    RETURN_TYPES=('IMAGE',);FUNCTION='load';CATEGORY='RecastFX/private worker'
    def load(self,job_key,reference_index):
        p,m=manifest(job_key)
        if reference_index not in m['reference_indexes']:raise ValueError('Reference is not in the private plan')
        with Image.open(p/f'reference-{reference_index}.image') as img:
            img=ImageOps.exif_transpose(img)
            if img.width*img.height>16_777_216:raise ValueError('Reference image exceeds the pixel limit')
            a=np.asarray(img.convert('RGB'),dtype=np.float32)/255.0
        return (torch.from_numpy(a.copy()).unsqueeze(0),)

class RecastFXWorkerSaveExact:
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'images':('IMAGE',),'job_key':('STRING',{'default':''}),'shot_index':('INT',{'default':1,'min':1,'max':100}),'delivery_frames':('INT',{'default':1,'min':1,'max':360})}}
    RETURN_TYPES=();OUTPUT_NODE=True;FUNCTION='save';CATEGORY='RecastFX/private worker'
    def save(self,images,job_key,shot_index,delivery_frames):
        p,m=manifest(job_key);g=m['geometry'];s=next((s for s in m['shots'] if s['index']==shot_index),None)
        if not s or s['delivery_frames']!=delivery_frames or images.shape[0]<delivery_frames or tuple(images.shape[1:3])!=(g['canvas_height'],g['canvas_width']):raise ValueError('Generated frame layout does not match the frozen shot')
        path=p/f'shot-{shot_index:03d}.mp4'
        if path.exists():raise FileExistsError('Preserve the existing generated shot')
        temporary=p/f'shot-{shot_index:03d}.partial.mp4';temporary.unlink(missing_ok=True)
        args=['ffmpeg','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s',f"{g['width']}x{g['height']}",'-r','24','-i','pipe:0','-frames:v',str(delivery_frames),'-an','-c:v','libx264','-preset','fast','-crf','17','-pix_fmt','yuv420p','-movflags','+faststart',str(temporary)]
        proc=subprocess.Popen(args,stdin=subprocess.PIPE)
        try:
            for f in images[:delivery_frames,g['pad_top']:g['pad_top']+g['height'],g['pad_left']:g['pad_left']+g['width'],:]:proc.stdin.write((f.clamp(0,1)*255).to(torch.uint8).cpu().numpy().tobytes())
            proc.stdin.close()
            if proc.wait(timeout=90):raise RuntimeError('Output encoding failed')
            os.chmod(temporary,0o600);temporary.replace(path)
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
        return {'ui':{'text':[path.name]}}

def pack(value,tensors):
    if isinstance(value,torch.Tensor):
        name=f'tensor_{len(tensors)}';tensors[name]=value.detach().cpu().clone().contiguous();return {'kind':'tensor','key':name}
    if isinstance(value,comfy.nested_tensor.NestedTensor):return {'kind':'nested','items':[pack(x,tensors) for x in value.unbind()]}
    if isinstance(value,dict):
        if not all(isinstance(k,str) for k in value):raise TypeError('Bundle keys must be strings')
        return {'kind':'dict','items':{k:pack(v,tensors) for k,v in value.items()}}
    if isinstance(value,(list,tuple)):return {'kind':'tuple' if isinstance(value,tuple) else 'list','items':[pack(x,tensors) for x in value]}
    if value is None or isinstance(value,(str,int,float,bool)):return {'kind':'scalar','value':value}
    raise TypeError('Unsupported conditioning bundle value')

def unpack(value,tensors):
    kind=value['kind']
    if kind=='tensor':return tensors[value['key']]
    if kind=='nested':return comfy.nested_tensor.NestedTensor([unpack(x,tensors) for x in value['items']])
    if kind=='dict':return {k:unpack(v,tensors) for k,v in value['items'].items()}
    if kind in {'list','tuple'}:
        xs=[unpack(x,tensors) for x in value['items']];return tuple(xs) if kind=='tuple' else xs
    if kind=='scalar':return value['value']
    raise ValueError('Unsupported conditioning bundle tag')

def bundle_path(job_key,shot_index):
    p,m=manifest(job_key)
    if not any(s['index']==shot_index and s['reference_indexes'] for s in m['shots']):raise ValueError('Shot does not require a conditioning bundle')
    return p/'bundles'/f'shot-{shot_index:03d}'

class RecastFXWorkerSaveBundle:
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'conditioning':('CONDITIONING',),'latent':('LATENT',),'job_key':('STRING',{'default':''}),'shot_index':('INT',{'default':1,'min':1,'max':100})}}
    RETURN_TYPES=();FUNCTION='save';OUTPUT_NODE=True;CATEGORY='RecastFX/private worker'
    def save(self,conditioning,latent,job_key,shot_index):
        path=bundle_path(job_key,shot_index)
        if path.exists():raise FileExistsError('Preserve the existing conditioning bundle')
        path.parent.mkdir(exist_ok=True);temporary=path.with_name(path.name+'.partial')
        if temporary.exists():raise FileExistsError('An incomplete conditioning bundle requires operator review')
        temporary.mkdir(mode=0o700);tensors={};tree=pack({'conditioning':conditioning,'latent':latent},tensors)
        save_file(tensors,str(temporary/'tensors.safetensors'));os.chmod(temporary/'tensors.safetensors',0o600)
        data={'tree':tree,'sha256':sha(temporary/'tensors.safetensors'),'tensors':{k:{'shape':list(v.shape),'dtype':str(v.dtype)} for k,v in tensors.items()}}
        with (temporary/'manifest.json').open('x') as f:os.chmod(f.name,0o600);json.dump(data,f,allow_nan=False)
        temporary.replace(path);return {}

class RecastFXWorkerLoadBundle:
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'job_key':('STRING',{'default':''}),'shot_index':('INT',{'default':1,'min':1,'max':100})}}
    RETURN_TYPES=('CONDITIONING','LATENT');FUNCTION='load';CATEGORY='RecastFX/private worker'
    def load(self,job_key,shot_index):
        path=bundle_path(job_key,shot_index);m=json.loads((path/'manifest.json').read_text());tensor_path=path/'tensors.safetensors'
        if sha(tensor_path)!=m['sha256']:raise ValueError('Conditioning bundle changed')
        tensors=load_file(str(tensor_path),device='cpu')
        for name,description in m['tensors'].items():
            if list(tensors[name].shape)!=description['shape'] or str(tensors[name].dtype)!=description['dtype']:raise ValueError('Conditioning tensor layout changed')
        result=unpack(m['tree'],tensors);return result['conditioning'],result['latent']

NODE_CLASS_MAPPINGS={x.__name__:x for x in [RecastFXWorkerSource,RecastFXWorkerAudio,RecastFXWorkerReference,RecastFXWorkerSaveExact,RecastFXWorkerSaveBundle,RecastFXWorkerLoadBundle]}
