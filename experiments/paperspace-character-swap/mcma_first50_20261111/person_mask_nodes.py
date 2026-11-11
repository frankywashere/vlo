"""Private MCMA full50 preparation. Saves raw separate supports for review."""
from pathlib import Path
import json,os,hashlib
import numpy as np
from PIL import Image
ROOT=Path('/home/paperspace/ai/recastfx_mcma_first50_20261111/jobs')
class McmaSavePersonMasks:
 @classmethod
 def INPUT_TYPES(cls):return {'required':{'images':('IMAGE',),'male':('MASK',),'female':('MASK',),'shot_index':('INT',{'default':1}),'delivery_frames':('INT',{'default':1}),'job_key':('STRING',{'default':''})}}
 RETURN_TYPES=();OUTPUT_NODE=True;FUNCTION='save';CATEGORY='Mcma/private'
 def save(self,images,male,female,shot_index,delivery_frames,job_key):
  if job_key!='mcma_first50_v3':raise ValueError('Isolated full50 preparation only')
  root=ROOT/job_key;manifest=json.loads((root/'job.json').read_text());shot=next(s for s in manifest['shots'] if s['index']==shot_index)
  if shot['delivery_frames']!=delivery_frames or len(images)!=shot['conditioning_frames']:raise ValueError('Frozen shot geometry mismatch')
  a=(male.detach().cpu().numpy()>.5).astype(np.uint8);b=(female.detach().cpu().numpy()>.5).astype(np.uint8)
  if a.shape!=b.shape or a.shape!=tuple(images.shape[:3]):raise ValueError('Person-mask dimensions differ from source')
  path=root/f'person-masks-{shot_index:03d}.npz'
  if path.exists():raise FileExistsError('Preserve saved person masks')
  picks=sorted({0,delivery_frames//4,delivery_frames//2,3*delivery_frames//4,delivery_frames-1});overlap=(a>0)&(b>0)
  for i in picks:
   rgb=images[i].detach().cpu().numpy().copy();ma=a[i]>0;fe=b[i]>0
   rgb[ma]=rgb[ma]*.5+np.array([0,.8,1])*.5;rgb[fe]=rgb[fe]*.5+np.array([1,.1,.6])*.5;rgb[overlap[i]]=[1,1,0]
   Image.fromarray((rgb*255).clip(0,255).astype(np.uint8)).resize((544,384)).save(root/f'person-review-{shot_index:03d}-{i:03d}.jpg')
  temporary=path.with_suffix('.partial')
  with temporary.open('wb') as f:np.savez_compressed(f,male=a,female=b);f.flush();os.fsync(f.fileno())
  temporary.replace(path)
  report={'shot':shot_index,'delivery_frames':delivery_frames,'conditioning_frames':len(images),'sampled_actual_frames':picks,'male_pixels_per_frame':a.reshape(len(a),-1).sum(1).tolist(),'female_pixels_per_frame':b.reshape(len(b),-1).sum(1).tolist(),'overlap_pixels_per_frame':overlap.reshape(len(a),-1).sum(1).tolist(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'review_status':'unreviewed','source_images_modified':False}
  (root/f'person-mask-report-{shot_index:03d}.json').write_text(json.dumps(report,indent=2));return {}
NODE_CLASS_MAPPINGS={'McmaSavePersonMasks':McmaSavePersonMasks}
