"""Wait for our mask preparation to exit, then run exactly one audio pilot."""
from pathlib import Path
import json,time,subprocess,os,fcntl
P=Path(__file__).resolve().parent
with (P/'.audio-controller.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 deadline=time.time()+3*3600
 while True:
  try:cmd=Path('/proc/3823374/cmdline').read_bytes()
  except FileNotFoundError:break
  if b'prepare_masks.py' not in cmd:break
  if time.time()>deadline:raise RuntimeError('Preparation wait expired; no render started')
  time.sleep(15)
 reports=list((P/'jobs/mcma_first50_v1').glob('mask-report-*.json'))
 if len(reports)!=20:raise RuntimeError('Mask preparation incomplete; inspect before audio pilot')
 env=dict(os.environ,PYTHONPATH='/home/paperspace/ai/recastfx_site_worker_20261104_v1',OMP_NUM_THREADS='8')
 with (P/'audio-pilot.log').open('ab',buffering=0) as out:
  result=subprocess.run(['/home/paperspace/ai/ComfyUI/.venv/bin/python','-u',str(P/'runner_audio_pilot.py')],cwd=P,env=env,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT)
 (P/'audio-controller-result.json').write_text(json.dumps({'exit_code':result.returncode,'at':time.time(),'full_batch_started':False}))
