"""Prepared only; requires reviewed pilot marker and stages separate masks."""
from pathlib import Path
import sys,json,time,os,urllib.request,fcntl,shutil
P=Path(__file__).resolve().parent;SITE=Path('/home/paperspace/ai/recastfx_site_worker_20261104_v1');sys.path.insert(0,str(SITE));os.environ['PYTHONPATH']=str(SITE)+os.pathsep+os.environ.get('PYTHONPATH','')
from self_hosted_worker.comfy import ComfyClient,DedicatedRuntime,GraphExecutor
from self_hosted_worker.media import atomic_json
plan=json.loads((P/'plan-full-v3.json').read_text());review=json.loads((P/'pilot-reviewed-for-full.json').read_text());assert review['female_reference_sha256']==plan['references'][2]['sha256'];assert review['quality_accepted'] is True
D=P/'jobs'/plan['job_key'];C=ComfyClient('http://127.0.0.1:8203');PATH=P/'person-mask-journal.json';J=json.loads(PATH.read_text()) if PATH.exists() else {'job_id':plan['job_key'],'tasks':{},'launcher':{},'hard_deadline_epoch':time.time()+12*3600}
def save():atomic_json(PATH,J)
def check():
 if time.time()>J['hard_deadline_epoch']:raise RuntimeError('Person-mask preparation deadline reached')
 if (P/'cancel.request').exists():raise RuntimeError('Operator cancellation requested')
 if (SITE/'jobs/active.json').exists():raise RuntimeError('Website active; keep priority')
 if shutil.disk_usage(P).free<20*2**30:raise RuntimeError('Less than20GiB disk headroom')
 for port in [8192,8199,8200]:
  with urllib.request.urlopen(f'http://127.0.0.1:{port}/queue',timeout=5) as r:q=json.load(r)
  if q.get('queue_running') or q.get('queue_pending'):raise RuntimeError('Shared work active')
def valid(i):
 import numpy as np
 f=D/f'person-masks-{i:03d}.npz';report=D/f'person-mask-report-{i:03d}.json'
 if not f.exists() or not report.exists():return False
 with np.load(f) as data:return data['male'].shape==data['female'].shape==(plan['shots'][i-1]['conditioning_frames'],768,1088)
argv=['/home/paperspace/ai/ComfyUI/.venv/bin/python','-m','self_hosted_worker.launcher','--profile',str(P/'profile-person-masks.json'),'--phase','encode','--port','8203']
with (P/'.offline.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);check()
 with DedicatedRuntime(C,argv,'encode',P/'jobs',D,J,save,check):
  for shot in plan['shots']:
   i=shot['index']
   if valid(i):continue
   g={'sam':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'sam3.1_multiplex_fp16.safetensors'}},'source':{'class_type':'PrettyCutSource','inputs':{'job_key':plan['job_key'],'start_frame':shot['conditioning_start_frame'],'end_frame':shot['conditioning_end_frame'],'conditioning_frames':shot['conditioning_frames']}}}
   for person,text in [('male','foreground man wearing tan suit'),('female','foreground blonde woman wearing black outfit')]:
    g[person+'text']={'class_type':'CLIPTextEncode','inputs':{'clip':['sam',1],'text':text}};g[person]={'class_type':'SAM3_Detect','inputs':{'model':['sam',0],'image':['source',0],'conditioning':[person+'text',0],'threshold':.5,'refine_iterations':2,'individual_masks':False}}
   g['save']={'class_type':'McmaSavePersonMasks','inputs':{'images':['source',0],'male':['male',0],'female':['female',0],'shot_index':i,'delivery_frames':shot['delivery_frames'],'job_key':plan['job_key']}}
   GraphExecutor(C,J,D,save,check).execute(f'person-masks-{i:03d}',g,3600,lambda _,i=i:valid(i))
print('Separate supports saved; visual review still required; no diffusion batch submitted')
