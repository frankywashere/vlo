import sys,json,tempfile,subprocess,zipfile
from pathlib import Path
from io import BytesIO
import numpy as np
from PIL import Image
sys.path.insert(0,'/app')
from app import ComposeRequest,health
from composite import compose
from face_mesh import FaceMeshPainter
request=ComposeRequest(input_uri='gs://test/source',masks_uri='gs://test/masks',guide_audio_uri='gs://test/audio',run_id='test',selected_label=1,fill_mode='translucent',face_mesh=True,target_styles=[{'label':1,'color':'#00dcff'},{'label':2,'color':'#00dcff'}])
assert health()['version']=='audio-cyan-guide-v8'
p=FaceMeshPainter();p.apply(np.zeros((64,64,3),np.uint8),np.zeros((64,64,3),np.uint8),np.ones((64,64),np.uint16),np.ones((64,64),bool));p.close()
with tempfile.TemporaryDirectory() as temp:
 root=Path(temp);source=root/'source.mp4';audio=root/'audio.wav';masks=root/'masks.zip';output=root/'out.mp4'
 subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=red:s=96x64:r=24:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
 subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=duration=1',str(audio)],check=True)
 labels=np.zeros((64,96),np.uint16);labels[10:50,10:35]=1;labels[10:50,60:85]=2;buffer=BytesIO();Image.fromarray(labels).save(buffer,'PNG')
 with zipfile.ZipFile(masks,'w') as z:
  for i in range(24):z.writestr(f'labels/labels_{i:05d}.png',buffer.getvalue())
 metrics=compose(source,masks,audio,output,1,'translucent',None,[{'label':1,'color':'#00dcff'},{'label':2,'color':'#00dcff'}])
 raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-']);frame=np.frombuffer(raw,np.uint8).reshape(64,96,3)
 assert metrics['overlay_opacity']==.2
 assert np.max(abs(frame[25,20].astype(int)-np.array([203,44,51])))<10
 assert np.max(abs(frame[25,70].astype(int)-frame[25,20].astype(int)))<10
 assert frame[25,48,0]>240 and frame[25,48,1]<10
 print(json.dumps({'worker_interface':'translucent accepted','shared_overlay_opacity':.2,'both_labels_same_cyan':True,'unselected_rgb_preserved':True,'face_mesh_cpu_runtime':'passed'}))
