from pathlib import Path
import shutil,json,os
P=Path(__file__).resolve().parent;src=P/'jobs/mcma_temporal_pilot_v1';d=P/'jobs/mcma_temporal_bind_pilot_v1';d.mkdir(exist_ok=True)
for name in ['source.mp4','reference-0.image','reference-1.image','reference-2.image','edit-mask-001.npz']:
 if not (d/name).exists():os.link(src/name,d/name)
m=json.loads((src/'job.json').read_text());m['job_id']=d.name;(d/'job.json').write_text(json.dumps(m,indent=2))
n=P/'runtime/custom_nodes/mcma_temporal_bind_nodes'
if not n.exists():shutil.copytree(P/'runtime/custom_nodes/mcma_temporal_nodes',n)
shutil.copy2(P/'spatial_nodes_temporal_bind.py',n/'spatial.py');shutil.copy2(P/'temporal_bind_plugin_init.py',n/'__init__.py')
profile=json.loads((P/'profile-temporal-pilot.json').read_text());profile['whitelist']=['mcma_temporal_bind_nodes' if x=='mcma_temporal_nodes' else x for x in profile['whitelist']];(P/'profile-temporal-bind-pilot.json').write_text(json.dumps(profile,indent=2))
# Review the source-space image-attention supports; these colors are never rendered.
import numpy as np,cv2
from PIL import Image
mask=np.load(d/'edit-mask-001.npz')['mask'];c=cv2.VideoCapture(str(d/'source.mp4'))
for i in [0,16,32,48]:
 c.set(cv2.CAP_PROP_POS_FRAMES,i);ok,b=c.read();assert ok;rgb=cv2.cvtColor(b,cv2.COLOR_BGR2RGB).astype(np.float32);a=mask[i,:,7:1081];left=a.copy();left[:,550:]=0;right=a-left
 rgb[left>0]=rgb[left>0]*.5+np.array([255,60,180])*.5;rgb[right>0]=rgb[right>0]*.5+np.array([20,200,255])*.5
 Image.fromarray(rgb.clip(0,255).astype(np.uint8)).resize((537,384)).save(d/f'picture-support-{i:03d}.jpg')
print('new binding pilot staged; isolated package; reference-support reviews written')
