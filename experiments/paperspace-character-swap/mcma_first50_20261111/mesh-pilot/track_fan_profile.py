from pathlib import Path
import cv2,numpy as np
from PIL import Image,ImageDraw
P=Path(__file__).resolve().parent
p=np.load(P/'fan-probe/landmarks.npz')['points'][...,:2].copy();assert p.shape==(49,2,68,2)
c=cv2.VideoCapture(str(P.parent/'source-working.mp4'));frames=[]
for _ in range(49):ok,b=c.read();assert ok;frames.append(b)
gray=[cv2.cvtColor(b,cv2.COLOR_BGR2GRAY) for b in frames]
# Track the visible woman's facial features backward from a reviewed source fit.
for i in range(15,-1,-1):
 xy=p[i+1,0].astype(np.float32);f,s,e=cv2.calcOpticalFlowPyrLK(gray[i+1],gray[i],xy[:,None],None,winSize=(15,15),maxLevel=3)
 back,bs,_=cv2.calcOpticalFlowPyrLK(gray[i],gray[i+1],f,None,winSize=(15,15),maxLevel=3)
 good=(s[:,0]>0)&(bs[:,0]>0)&(np.linalg.norm(back[:,0]-xy,axis=1)<2.0)&(e[:,0]<40)
 assert good.sum()>25,(i,int(good.sum()))
 a,_=cv2.estimateAffinePartial2D(xy[good],f[:,0][good],method=cv2.RANSAC,ransacReprojThreshold=2)
 assert a is not None
 p[i,0]=np.where(good[:,None],f[:,0],np.c_[xy,np.ones(68)]@a.T)
np.savez_compressed(P/'source-face-landmarks-fan-tracked.npz',points=p)
for i in [0,8,16,24,32,40,48]:
 im=Image.fromarray(cv2.cvtColor(frames[i],cv2.COLOR_BGR2RGB));d=ImageDraw.Draw(im)
 for person in p[i]:
  for a,z in [(0,17),(17,22),(22,27),(27,31),(31,36),(36,42),(42,48),(48,60),(60,68)]:
   path=[tuple(x) for x in person[a:z]]
   if a in [36,42,48,60]:path.append(path[0])
   d.line(path,fill=(255,135,100) if a>=48 else (50,170,205),width=1)
 im.save(P/f'fan-tracked-{i:03d}.jpg')
