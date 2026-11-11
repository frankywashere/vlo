from pathlib import Path
import json,time,subprocess,fcntl
P=Path(__file__).resolve().parent;remote='/home/paperspace/ai/recastfx_mcma_first50_20261111';host='paperspace@100.110.98.79';out=P/'mesh-pilot';out.mkdir(exist_ok=True)
with (P/'.mesh-collector.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 deadline=time.time()+3*3600
 while time.time()<deadline:
  r=subprocess.run(['ssh',host,f'cat {remote}/status-mesh-pilot.json'],capture_output=True,text=True,timeout=30)
  if r.returncode==0:
   status=json.loads(r.stdout);(out/'remote-status.json').write_text(json.dumps(status,indent=2))
   if status['status']=='stopped':raise RuntimeError(status.get('error'))
   if status['completed_shots']==[1]:break
  time.sleep(30)
 else:raise RuntimeError('Audio collection deadline expired')
 subprocess.run(['scp',f'{host}:{remote}/jobs/mcma_mesh_pilot_v1/shot-001.mp4',str(out/'shot-001.mp4')],check=True,timeout=180)
 subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-i',str(out/'shot-001.mp4'),'-i',str(P/'source-working.mp4'),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-t',str(49/24),'-movflags','+faststart',str(out/'shot-001-with-audio.mp4')],check=True)
 import cv2,numpy as np
 from PIL import Image,ImageDraw
 paths=[P/'source-working.mp4',P/'audio-pilot/shot-001.mp4',out/'shot-001.mp4'];caps=[cv2.VideoCapture(str(x)) for x in paths];board=Image.new('RGB',(1611,410*6),(20,20,20));draw=ImageDraw.Draw(board)
 for row,i in enumerate([0,8,16,24,32,40]):
  for col,cap in enumerate(caps):
   cap.set(cv2.CAP_PROP_POS_FRAMES,i);ok,bgr=cap.read();assert ok
   rgb=cv2.cvtColor(cv2.resize(bgr,(537,384)),cv2.COLOR_BGR2RGB);board.paste(Image.fromarray(rgb),(537*col,410*row+26))
   draw.text((537*col+8,410*row+5),['Source','Audio only','Audio + partial face mesh'][col]+f' frame{i}',fill='white')
 for cap in caps:cap.release()
 board.save(out/'render-comparison.jpg');print(out/'shot-001-with-audio.mp4')
