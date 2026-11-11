from pathlib import Path
import sys,json,time,os,urllib.request
P=Path(__file__).resolve().parent;sys.path.insert(0,'/home/paperspace/ai/recastfx_site_worker_20261104_v1')
from self_hosted_worker.comfy import ComfyClient,DedicatedRuntime,GraphExecutor
from self_hosted_worker.media import atomic_json
D=P/'jobs/mcma_first50_v1';C=ComfyClient('http://127.0.0.1:8203');plan=json.loads((P/'plan.json').read_text());J={'job_id':plan['job_key'],'tasks':{},'launcher':{},'hard_deadline_epoch':time.time()+6*3600}
def save():atomic_json(P/'mask-journal.json',J)
def check():
 if Path('/home/paperspace/ai/recastfx_site_worker_20261104_v1/jobs/active.json').exists():raise RuntimeError('Website active')
 for port in [8192,8199,8200]:
  q=json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/queue',timeout=5))
  if q.get('queue_running') or q.get('queue_pending'):raise RuntimeError('Shared queue active')
argv=['/home/paperspace/ai/ComfyUI/.venv/bin/python','-m','self_hosted_worker.launcher','--profile',str(P/'profile-mask.json'),'--phase','encode','--port','8203']
with DedicatedRuntime(C,argv,'encode',P/'jobs',D,J,save,check):
 for shot in plan['shots']:
  i=shot['index']
  if (D/f'edit-mask-{i:03d}.npz').exists():continue
  g={'sam':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'sam3.1_multiplex_fp16.safetensors'}},'source':{'class_type':'PrettyCutSource','inputs':{'job_key':plan['job_key'],'start_frame':shot['start_frame'],'end_frame':shot['end_frame'],'conditioning_frames':shot['conditioning_frames']}}}
  for target,text in [('male','foreground man wearing tan suit'),('female','foreground blonde woman wearing black outfit')]:
   g[target+'text']={'class_type':'CLIPTextEncode','inputs':{'clip':['sam',1],'text':text}}
   g[target]={'class_type':'SAM3_Detect','inputs':{'model':['sam',0],'image':['source',0],'conditioning':[target+'text',0],'threshold':.5,'refine_iterations':2,'individual_masks':False}}
  g['save']={'class_type':'McmaSaveEditMask','inputs':{'images':['source',0],'male':['male',0],'female':['female',0],'shot_index':i}}
  GraphExecutor(C,J,D,save,check).execute(f'mask-{i:03d}',g,3600,lambda _: (D/f'edit-mask-{i:03d}.npz').exists())
print('All shot masks ready; no additional generation submitted')
