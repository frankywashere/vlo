"""Wait for an atomic completed clip, then insert one isolated correction.
No currently rendering clip is cancelled. All prior outputs are retained.
"""
from pathlib import Path
import json,time,os,signal,subprocess,fcntl,hashlib,shutil,urllib.request,sys
P=Path(__file__).resolve().parent;D=P/'jobs/pretty_full_music_v3';PY='/home/paperspace/ai/ComfyUI/.venv/bin/python'
OWNER=int(sys.argv[1]);STATE=P/'shot004-latentlock_v2-queue.json';ID='shot004-latentlock_v2-v1'  # OWNER = live PID of `offline_runner_repair.py full`, passed on the command line
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(value):
 temp=STATE.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2));temp.replace(STATE)
def alive():
 try:return b'offline_runner_repair.py\x00full' in Path(f'/proc/{OWNER}/cmdline').read_bytes()
 except FileNotFoundError:return False
def process_running():
 try:return Path(f'/proc/{OWNER}/stat').read_text().split(') ',1)[1][0]!='Z'
 except FileNotFoundError:return False
with (P/'.latentlock_v2-controller.lock').open('a') as control:
 fcntl.flock(control,fcntl.LOCK_EX|fcntl.LOCK_NB)
 assert not STATE.exists(),'One repair queue only'
 old=json.loads((P/'plan.json').read_text())
 test_plan=json.loads((P/'plan-shot004-latentlock_v2.json').read_text());assert test_plan['shots'][0]['index']==4;assert (P/'profile-latentlock_v2.json').exists() and (P/'runtime/custom_nodes/pretty_latentlock_v2_nodes/__init__.py').exists(),'Run stage_latentlock_v2.py first'
 assert not (P/'cancel.request').exists() and alive()
 j=json.loads((P/'journal.json').read_text());after=j['current_shot'];assert after!=4 and not (D/f'shot-{after:03d}.mp4').exists()
 record={'repair_id':ID,'status':'waiting_for_completed_clip','after_shot':after,'queued_at':time.time(),'current_render_preserved':True,'paid_api_calls':0}
 write(record);frozen=False
 try:
  deadline=time.time()+12*3600
  while True:
   if not alive():raise RuntimeError('Original runner exited before handoff; preserve state for inspection')
   if time.time()>deadline:raise RuntimeError('Boundary wait expired without changes')
   if not (D/f'shot-{after:03d}.mp4').exists():time.sleep(.1);continue
   os.kill(OWNER,signal.SIGSTOP);frozen=True
   j=json.loads((P/'journal.json').read_text());current=j.get('current_shot')
   # If another clip really started before the observer, let it finish too.
   if current and current>after and not (D/f'shot-{current:03d}.mp4').exists():
    os.kill(OWNER,signal.SIGCONT);frozen=False;after=current;record['after_shot']=after;write(record);continue
   s=old['shots'][after-1]
   probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=width,height,avg_frame_rate,nb_read_frames','-of','json',str(D/f'shot-{after:03d}.mp4')]))['streams'][0]
   assert (probe['width'],probe['height'],probe['avg_frame_rate'],int(probe['nb_read_frames']))==(1102,768,'24/1',s['delivery_frames'])
   record.update(status='completed_clip_handoff',boundary_verified_at=time.time());write(record)
   (P/'cancel.request').write_text(ID+' completed-clip handoff only')
   os.kill(OWNER,signal.SIGCONT);frozen=False
   stop=time.time()+120
   while process_running() and time.time()<stop:time.sleep(.25)
   assert not process_running(),'Runner did not release ownership; do not start another'
   break
  with (P/'.offline.lock').open('a') as ownership:
   fcntl.flock(ownership,fcntl.LOCK_EX|fcntl.LOCK_NB)
   j=json.loads((P/'journal.json').read_text());assert not j.get('launcher')
   try:urllib.request.urlopen('http://127.0.0.1:8203/queue',timeout=2)
   except OSError:pass
   else:raise RuntimeError('Private runtime still available')
   s=old['shots'][after-1];file=D/f'shot-{after:03d}.mp4'
   j.setdefault('outputs',{})[str(after)]={'sha256':sha(file),'bytes':file.stat().st_size,'frames':s['delivery_frames']}
   if after not in j['completed_shots']:j['completed_shots'].append(after)
   if f'render-{after:03d}' in j['tasks']:j['tasks'][f'render-{after:03d}']['status']='success'
   j.update(status='waiting',phase='isolated_latentlock_v2_test',error=None)
   temp=P/'journal.audio-test.tmp';temp.write_text(json.dumps(j,indent=2));temp.replace(P/'journal.json')
   status=json.loads((P/'status.json').read_text());status.update(status='waiting',phase='isolated_latentlock_v2_test',completed_shots=j['completed_shots'],error=None);(P/'status.json').write_text(json.dumps(status,indent=2))
   trial=P/'jobs'/test_plan['job_key'];trial.mkdir(exist_ok=False)
   for name in ['source.mp4','reference-0.image','reference-1.image']:os.link(D/name,trial/name)
   manifest=json.loads((D/'job.json').read_text());manifest['shots']=test_plan['shots']
   (trial/'job.json').write_text(json.dumps(manifest,indent=2))
   shutil.copytree(P/'jobs/pretty_shot004_latentlock_v1/bundles',trial/'bundles')
   assert (P/'cancel.request').read_text().startswith(ID);(P/'cancel.request').unlink()
  env=dict(os.environ,PYTHONPATH='/home/paperspace/ai/recastfx_site_worker_20261104_v1',OMP_NUM_THREADS='8')
  record.update(status='isolated_latentlock_v2_test_running',handoff_completed_at=time.time());write(record)
  try:
   with (P/'latentlock_v2-test.log').open('ab',buffering=0) as log:
    result=subprocess.run([PY,'-u',str(P/'offline_runner_latentlock_v2.py')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,env=env,cwd=P)
   record.update(test_exit_code=result.returncode,test_finished_at=time.time())
   output=trial/'shot-004.mp4'
   if result.returncode==0 and output.exists():record.update(test_status='completed',test_output_sha256=sha(output))
   else:record.update(test_status='failed_no_automatic_retry')
  finally:
   record.update(status='test_finished_main_batch_paused',main_batch_paused=True,main_outputs_unchanged=True)
   write(record)

 except BaseException as error:
  if frozen:os.kill(OWNER,signal.SIGCONT)
  record.update(status='needs_inspection',error=str(error),at=time.time());write(record);raise
