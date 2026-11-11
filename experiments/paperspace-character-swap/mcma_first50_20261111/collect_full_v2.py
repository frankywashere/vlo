"""Collect immutable verified parts; assemble only when all 1200 frames exist."""
from pathlib import Path
import json,sys,time,subprocess,hashlib,os,fcntl
P=Path(__file__).resolve().parent
sys.path.insert(0,'/Users/frank/Desktop/Coding/vlo/deployment/swapmein')
sys.path.insert(0,str(P.with_name('pretty_full_music_video_20261109_v3')))
from self_hosted_worker.media import verify_part
from assemble_exact import assemble
HOST='paperspace@100.110.98.79';REMOTE='/home/paperspace/ai/recastfx_mcma_first50_20261111';KEY='mcma_first50_v2'
plan=json.loads((P/'plan-full-v2.json').read_text());shape={'width':1074,'height':768,'fps':24,'frames':1200};parts=P/'parts-v2';parts.mkdir(exist_ok=True)
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
def write(d):
 t=P/'collection-v2-status.tmp';t.write_text(json.dumps(d,indent=2));t.replace(P/'collection-v2-status.json')
def snapshot():
 code="from pathlib import Path; import json,hashlib; p=Path("+repr(REMOTE)+"); d=p/'jobs'/"+repr(KEY)+"; print(json.dumps({'status':json.loads((p/'status-full-v2.json').read_text()),'files':{f.name:{'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()} for f in d.glob('shot-*.mp4')}}))"
 import shlex
 r=subprocess.run(['ssh',HOST,'/home/paperspace/ai/ComfyUI/.venv/bin/python -c '+shlex.quote(code)],capture_output=True,text=True,timeout=45,check=True);return json.loads(r.stdout)
with (P/'.collector-v2.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 deadline=time.time()+96*3600
 while time.time()<deadline:
  try:
   snap=snapshot();done=[]
   for s in plan['shots']:
    name=f"shot-{s['index']:03d}.mp4";f=parts/name;record=snap['files'].get(name)
    if not record:continue
    if not f.exists():
     temp=parts/(name+'.partial.mp4');subprocess.run(['scp',f'{HOST}:{REMOTE}/jobs/{KEY}/{name}',str(temp)],check=True,timeout=180)
     assert temp.stat().st_size==record['bytes'] and sha(temp)==record['sha256']
     verify_part(temp,s['delivery_frames'],shape);temp.replace(f)
    assert sha(f)==record['sha256'],'Do not replace an existing local part silently'
    verify_part(f,s['delivery_frames'],shape);done.append(s['index'])
   write({'phase':'collecting','completed_shots':done,'total_shots':len(plan['shots']),'remote_status':snap['status'],'at':time.time()})
   if len(done)==len(plan['shots']):break
  except Exception as error:write({'phase':'retrying_read','error':str(error),'at':time.time()})
  time.sleep(60)
 else:raise RuntimeError('Collection deadline reached; preserve files')
 master=assemble(parts,plan['shots'],shape)
 final=Path('/Users/frank/Desktop/mcma-first50-recast-paperspace.mp4');assert not final.exists(),'Preserve existing delivery'
 temp=final.with_name(final.stem+'.partial.mp4')
 subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-i',str(master),'-i',plan['source'],'-map','0:v:0','-map','1:a:0','-t','50','-c:v','libx264','-threads','2','-preset','fast','-crf','17','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-movflags','+faststart',str(temp)],check=True)
 verify_part(temp,1200,shape);temp.replace(final)
 write({'phase':'ready_for_creative_review','completed_shots':list(range(1,len(plan['shots'])+1)),'delivery':str(final),'frames':1200,'seconds':50,'at':time.time()})
 print(final)
