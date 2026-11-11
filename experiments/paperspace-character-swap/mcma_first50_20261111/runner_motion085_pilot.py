"""Owned offline CharacterSwap job. No provider, cloud or website requests."""
from pathlib import Path
import json,sys,time,os,shutil,hashlib,subprocess,urllib.request,fcntl,traceback
P=Path(__file__).resolve().parent
SITE=Path('/home/paperspace/ai/recastfx_site_worker_20261104_v1')
sys.path.insert(0,str(SITE))
os.environ["PYTHONPATH"]=str(SITE)+os.pathsep+os.environ.get("PYTHONPATH","")
from self_hosted_worker.comfy import ComfyClient,DedicatedRuntime,GraphExecutor,stop_owned_record
from self_hosted_worker.recipe import build_graph
from self_hosted_worker.media import atomic_json,verify_part,copy_untouched,digest
MODE='motion085_pilot'
PLAN=json.loads((P/'plan-motion085-pilot.json').read_text());KEY=PLAN['job_key'];D=P/'jobs'/KEY
BY_ID={s['index']:s for s in PLAN['shots']}
LL=PLAN['latent_lock'];LOCK={k:LL[k] for k in ['keep_from_frame','feather_tokens','feather_value','generate_value']};DENOISE=float(LL.get('render_denoise',1.0))
SHAPE={'width': 1074, 'height': 768, 'canvas_width': 1088, 'canvas_height': 768, 'pad_left': 7, 'pad_top': 0, 'fps': 24, 'frames': 1200, 'has_audio': True}
PROFILE=P/'profile-motion085-pilot.json';PY='/home/paperspace/ai/ComfyUI/.venv/bin/python'
CLIENT=ComfyClient('http://127.0.0.1:8203')
PATH=P/'journal-motion085-pilot.json';J=json.loads(PATH.read_text()) if PATH.exists() else {'job_id':KEY,'tasks':{},'completed_shots':[],'started_at':time.time(),'hard_deadline_epoch':time.time()+96*3600,'launcher':{}}
last_guard=0
class SharedResourceBusy(RuntimeError):pass

def save(**fields):
 J.update(fields);atomic_json(PATH,J)
 status={k:J.get(k) for k in ['status','phase','current_shot','completed_shots','started_at','error','hard_deadline_epoch']}
 status.update(mode=MODE,total_shots=1,elapsed_seconds=round(time.time()-J['started_at'],1))
 atomic_json(P/'status-motion085-pilot.json',status)

def guard(startup=False):
 global last_guard
 if (P/'cancel.request').exists():raise RuntimeError('Operator cancellation requested')
 if time.time()>=J['hard_deadline_epoch']:raise RuntimeError('96-hour batch deadline reached')
 if time.monotonic()-last_guard<10 and not startup:return
 last_guard=time.monotonic()
 if (SITE/'jobs/active.json').exists():raise SharedResourceBusy('Shared website worker has work; preserve its priority')
 for port in [8192,8199,8200]:
  try:
   with urllib.request.urlopen(f'http://127.0.0.1:{port}/queue',timeout=5) as r:q=json.load(r)
  except Exception as e:raise SharedResourceBusy(f'Shared endpoint {port} cannot be verified idle') from e
  if q.get('queue_running') or q.get('queue_pending'):raise SharedResourceBusy(f'Shared endpoint {port} became busy')
 free=shutil.disk_usage(P).free
 if free<20*2**30:raise RuntimeError('Disk headroom below 20GiB; no files outside this job will be deleted')
 available=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024
 if available<(48 if startup else 12)*2**30:raise SharedResourceBusy('Available host RAM below safety limit')
 save(last_heartbeat_at=time.time())

def output_ok(index):
 try:verify_part(D/f'shot-{index:03d}.mp4',BY_ID[index]['delivery_frames'],SHAPE);return True
 except Exception:return False

def bundle_ok(index):
 b=D/'bundles'/f'shot-{index:03d}'
 try:return digest(b/'tensors.safetensors')==json.loads((b/'manifest.json').read_text())['sha256']
 except Exception:return False

def argv(phase):return [PY,'-m','self_hosted_worker.launcher','--profile',str(PROFILE),'--phase',phase,'--port','8203']

def make_graph(kind,s):
 g=build_graph(kind,'paperspace',KEY,s,SHAPE)
 if kind=='encode':
  g['paired_audio']['class_type']='PrettyCutAudio'
  g['paired_audio']['inputs'].update(start_frame=s['conditioning_start_frame'],end_frame=s['conditioning_end_frame'])
  for node,cls in [('source','PrettyCutSource')]:
   g[node]['class_type']=cls
   g[node]['inputs'].update(start_frame=s['conditioning_start_frame'],end_frame=s['conditioning_end_frame'])
  if s.get('guide'):
   g['continuity_image']={'class_type':'PrettyContinuityGuide','inputs':{'job_key':KEY,'shot_index':s['index']}}
   g['continuity']={'class_type':'MiniMaxH3AddGuide','inputs':{'positive':['condition',0],'latent':['condition',1],'vae':['vae',0],'image':['continuity_image',0],'frame_idx':s['guide']['frame_idx']}}
   g['persist']['inputs']['conditioning']=['continuity',0]
  # Source-locked target latent + noise mask (the only change against the no-audio baseline).
  g['lock']={'class_type':'PrettySourceLockedLatent','inputs':dict(positive=g['persist']['inputs']['conditioning'],latent=['condition',1],job_key=KEY,shot_index=s['index'],keep_from_frame=s['conditioning_frames'],feather_tokens=0,feather_value=0.5,generate_value=1.0)}
  g['persist']['inputs']['latent']=['lock',0]
 else:
  g['checkpoint']={'class_type':'PrettyCheckpointSampledLatent','inputs':{'latent':['sample',0],'job_key':KEY,'shot_index':s['index']}}
  g['separate']['inputs']['av_latent']=['checkpoint',0]
  g['save']['class_type']='PrettyCutSaveExact';g['save']['inputs']['trim_start_frames']=s['trim_start_frames']
  if DENOISE!=1.0:g['schedule']['inputs']['denoise']=DENOISE
  if s['conditioning_frames']>=300:
   g['model']['inputs']['expert_mode_allocations']='cuda:2,4g;cpu,*'
 return g

def execute_shot(s):
 i=s['index'];save(phase='preparing_shot',current_shot=i)
 if output_ok(i):
  if i not in J['completed_shots']:J['completed_shots'].append(i)
  J.setdefault('outputs',{})[str(i)]={'sha256':digest(D/f'shot-{i:03d}.mp4'),'bytes':(D/f'shot-{i:03d}.mp4').stat().st_size,'frames':s['delivery_frames']}
  save()
  return
 if not s['reference_indexes']:
  copy_untouched(D/'source.mp4',D/f'shot-{i:03d}.mp4',s,SHAPE,guard)
 else:
  executor=GraphExecutor(CLIENT,J,D,save,guard,poll_seconds=5)
  if not bundle_ok(i):
   guard(True);save(phase='encoding')
   with DedicatedRuntime(CLIENT,argv('encode'),'encode',P/'jobs',D,J,save,guard):
    executor.execute(f'encode-{i:03d}',make_graph('encode',s),3600,lambda _key:bundle_ok(i))
  guard(True);save(phase='rendering')
  with DedicatedRuntime(CLIENT,argv('render'),'render',P/'jobs',D,J,save,guard):
   executor.execute(f'render-{i:03d}',make_graph('render',s),7200 if s['conditioning_frames']<=107 else 14400 if s['conditioning_frames']<=209 else 21600,lambda _key:output_ok(i))
 assert output_ok(i)
 if i not in J['completed_shots']:J['completed_shots'].append(i)
 J.setdefault('outputs',{})[str(i)]={'sha256':digest(D/f'shot-{i:03d}.mp4'),'bytes':(D/f'shot-{i:03d}.mp4').stat().st_size,'frames':s['delivery_frames']}
 # Only the just-finished scratch bundle is expendable; input/model files stay.
 b=D/'bundles'/f'shot-{i:03d}'
 if b.is_dir():shutil.rmtree(b)
 save(phase='shot_saved')

def run_when_capacity_is_free(shot):
 while True:
  try:
   guard(True)
   execute_shot(shot)
   return
  except SharedResourceBusy as error:
   # DedicatedRuntime has closed its owned process before control reaches here.
   # This job only calls loopback Comfy; no paid/provider calls can be repeated.
   if CLIENT.available():raise RuntimeError('Owned endpoint remains available; require inspection before resuming')
   attempt={'at':time.time(),'shot':shot['index'],'reason':str(error),'tasks':{}}
   i=shot['index']
   for kind,valid in [('encode',bundle_ok(i)),('render',output_ok(i))]:
    key=f'{kind}-{i:03d}';task=J['tasks'].get(key)
    if not task or task.get('status')=='success':continue
    if valid:task['status']='success'
    else:
     attempt['tasks'][key]=J['tasks'].pop(key)
     # An interrupted atomic bundle can only be incomplete scratch for this shot.
     partial=D/'bundles'/f'shot-{i:03d}.partial'
     if kind=='encode' and partial.is_dir():shutil.rmtree(partial)
   J.setdefault('interrupted_local_attempts',[]).append(attempt)
   save(status='waiting',phase='waiting_for_shared_resources',current_shot=i,waiting_reason=str(error))
   idle=0
   while idle<2:
    time.sleep(30)
    try:guard(True);idle+=1
    except SharedResourceBusy:idle=0
   save(status='running',phase='resuming_saved_shot',waiting_reason=None)

with (P/'.offline.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 try:
  assert MODE=='motion085_pilot'
  if MODE=='full':assert (P/'tests-reviewed.json').exists(),'Keyframe tests must be reviewed before the long batch'
  assert len(BY_ID)==len(PLAN['shots']) and all(0<s['delivery_frames']<=360 and s['delivery_frames']==s['end_frame']-s['start_frame'] for s in PLAN['shots'])
  assert sum(s['delivery_frames'] for s in PLAN['shots'])==1200
  assert all(s['end_frame']==PLAN['shots'][i+1]['start_frame'] for i,s in enumerate(PLAN['shots'][:-1]))
  save(status='running',phase='preflight',error=None)
  GraphExecutor(CLIENT,J,D,save,guard).recover_existing(lambda key: bundle_ok(int(key.split('-')[1])) if key.startswith('encode-') else output_ok(int(key.split('-')[1])))
  ids=[s['index'] for s in PLAN['shots']][:1]  # bounded spatial-preservation pilot first
  for i in ids:run_when_capacity_is_free(BY_ID[i])
  save(status='all_shots_ready',phase='idle',current_shot=None)
 except BaseException as error:
  (P/'error-motion085-pilot.txt').write_text(traceback.format_exc());save(status='stopped',error=str(error))
  raise
 finally:
  stop_owned_record(J.get('launcher') or {});J['launcher']={};save()
