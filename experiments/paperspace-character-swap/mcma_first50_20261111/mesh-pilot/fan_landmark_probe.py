from pathlib import Path
import os,sys,json
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/'landmark_packages'));os.environ['TORCH_HOME']=str(P/'landmark_model_cache')
import cv2,numpy as np,torch,face_alignment
from PIL import Image,ImageDraw
torch.set_num_threads(8)
model=face_alignment.FaceAlignment(face_alignment.LandmarksType.TWO_HALF_D,device='cpu',flip_input=True,face_detector='folder',compile=False)
anchors=[0,8,16,24,32,40,48]
women=[[247,120,325,215],[280,140,350,235],[310,155,388,250],[349,170,420,258],[375,170,447,270],[397,165,478,268],[409,165,490,270]]
men=[[751,130,836,240],[730,145,815,255],[700,150,795,270],[676,161,765,270],[650,145,745,270],[625,150,730,270],[600,165,715,280]]
cap=cv2.VideoCapture(str(P/'jobs/mcma_first50_v1/source.mp4'));out=P/'fan-probe';out.mkdir(exist_ok=True);allpoints=[];scores=[]
selected=[0,24,48] if '--probe' in sys.argv else list(range(49))
for frame in selected:
 cap.set(cv2.CAP_PROP_POS_FRAMES,frame);ok,b=cap.read();assert ok;rgb=cv2.cvtColor(b,cv2.COLOR_BGR2RGB)
 boxes=[np.array([np.interp(frame,anchors,np.array(person)[:,i]) for i in range(4)]) for person in [women,men]]
 points,confidence,_=model.get_landmarks_from_image(rgb,detected_faces=boxes,return_landmark_score=True)
 allpoints.append(np.array(points));scores.append([float(np.mean(x)) for x in confidence]);im=Image.fromarray(rgb);draw=ImageDraw.Draw(im)
 for person in points:
  for a,z in [(0,17),(17,22),(22,27),(27,31),(31,36),(36,42),(42,48),(48,60),(60,68)]:
   path=[tuple(x[:2]) for x in person[a:z]]
   if a in [36,42,48,60]:path.append(path[0])
   draw.line(path,fill=(255,135,100) if a>=48 else (50,170,205),width=2)
 im.save(out/f'frame-{frame:03d}.jpg')
np.savez_compressed(out/'landmarks.npz',points=np.array(allpoints),frames=np.array(selected));(out/'scores.json').write_text(json.dumps(scores));print('Landmarked',len(selected),'frames')
