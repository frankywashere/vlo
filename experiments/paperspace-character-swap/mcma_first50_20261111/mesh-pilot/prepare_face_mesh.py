"""Two target-specific face crops; track source geometry through brief detection gaps."""
from pathlib import Path
import json,cv2,numpy as np,mediapipe as mp,subprocess
from PIL import Image,ImageDraw
P=Path(__file__).resolve().parent;ROOT=P.parent
cap=cv2.VideoCapture(str(ROOT/'source-working.mp4'));frames=[]
for _ in range(49):
 ok,b=cap.read();assert ok;frames.append(b)
cap.release();gray=[cv2.cvtColor(b,cv2.COLOR_BGR2GRAY) for b in frames]
options=mp.tasks.vision.FaceLandmarkerOptions(base_options=mp.tasks.BaseOptions(model_asset_path='/Users/frank/Desktop/Coding/CharSwap/benchmark/models/face_landmarker.task'),running_mode=mp.tasks.vision.RunningMode.IMAGE,num_faces=4,min_face_detection_confidence=.2,min_face_presence_confidence=.2)
mask=np.load(P/'edit-mask-001.npz')['mask']; dilated=np.array([cv2.dilate(x,np.ones((21,21),np.uint8)) for x in mask])
tracks=np.full((49,2,478,2),np.nan,np.float32);methods=[['missing']*49 for _ in range(2)];reports={}
with mp.tasks.vision.FaceLandmarker.create_from_options(options) as detector:
 for person,(name,box) in enumerate([('woman',(110,50,550,350)),('man',(500,60,940,350))]):
  x0,y0,x1,y1=box
  for i,b in enumerate(frames):
   rgb=cv2.cvtColor(cv2.resize(b[y0:y1,x0:x1],None,fx=2,fy=2),cv2.COLOR_BGR2RGB);result=detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=rgb));candidates=[]
   for face in result.face_landmarks:
    xy=np.array([[v.x,v.y] for v in face],np.float32);area=float(np.prod(xy.max(0)-xy.min(0)))
    if area<.01 or not np.isfinite(xy).all():continue
    xy=xy*np.array([x1-x0,y1-y0],np.float32)+np.array([x0,y0],np.float32)
    if xy[:,1].mean()>310:continue
    ix=np.clip(np.round(xy[:,0]+7).astype(int),0,1087);iy=np.clip(np.round(xy[:,1]).astype(int),0,767)
    if dilated[i,iy,ix].mean()<.65:continue
    candidates.append((area,xy))
   if candidates:tracks[i,person]=max(candidates,key=lambda x:x[0])[1];methods[person][i]='detected'
  detected=[i for i in range(49) if methods[person][i]=='detected'];assert detected, name+' never detected'
  # Optical flow uses actual consecutive source frames, never generated expressions.
  for i in range(49):
   if methods[person][i]=='detected':continue
   anchor=min(detected,key=lambda j:abs(j-i));xy=tracks[anchor,person].copy();step=1 if i>anchor else -1
   for a in range(anchor,i,step):
    nxt=a+step;forward,status,error=cv2.calcOpticalFlowPyrLK(gray[a],gray[nxt],xy[:,None,:],None,winSize=(19,19),maxLevel=3,criteria=(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT,30,.01))
    back,backstatus,_=cv2.calcOpticalFlowPyrLK(gray[nxt],gray[a],forward,None,winSize=(19,19),maxLevel=3)
    f=forward[:,0];good=(status[:,0]>0)&(backstatus[:,0]>0)&(np.linalg.norm(back[:,0]-xy,axis=1)<2.5)&(error[:,0]<35)
    assert good.sum()>80,(name,i,a,'insufficient reliable optical flow')
    affine,inliers=cv2.estimateAffinePartial2D(xy[good],f[good],method=cv2.RANSAC,ransacReprojThreshold=2.5)
    assert affine is not None
    global_move=np.c_[xy,np.ones(len(xy))]@affine.T;xy=np.where(good[:,None],f,global_move).astype(np.float32)
   tracks[i,person]=xy;methods[person][i]='source_optical_flow'
  reports[name]={'direct_detection_frames':len(detected),'optical_flow_frames':49-len(detected),'methods':methods[person]}
assert np.isfinite(tracks).all()
mask=np.load(P/'edit-mask-001.npz')['mask'];connections=mp.tasks.vision.FaceLandmarksConnections
lines=[(connections.FACE_LANDMARKS_TESSELATION,(50,170,205),1),(connections.FACE_LANDMARKS_LIPS,(255,135,100),2),(connections.FACE_LANDMARKS_LEFT_EYE,(130,255,150),2),(connections.FACE_LANDMARKS_RIGHT_EYE,(130,255,150),2)]
np.savez_compressed(P/'source-face-landmarks.npz',points=tracks)
# Lossless, untimed source-sized mesh guide for visual inspection; padding is added in Comfy.
writer=subprocess.Popen(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s','1074x768','-r','24','-i','pipe:0','-an','-c:v','libx264','-threads','2','-preset','fast','-crf','0','-pix_fmt','yuv420p',str(P/'mesh-guide.mp4')],stdin=subprocess.PIPE)
for i,b in enumerate(frames):
 rgb=cv2.cvtColor(b,cv2.COLOR_BGR2RGB);layer=Image.new('RGB',(1074,768));draw=ImageDraw.Draw(layer)
 for person in range(2):
  xy=tracks[i,person];assert ((xy[:,0]>=0)&(xy[:,0]<1074)&(xy[:,1]>=0)&(xy[:,1]<768)).mean()>.95
  # SAM union plus manually bounded target crops excludes all audience faces.
  ix=np.clip(np.round(xy[:,0]+7).astype(int),0,1087);iy=np.clip(np.round(xy[:,1]).astype(int),0,767)
  assert dilated[i,iy,ix].mean()>.65,(i,person,'face does not match foreground edit mask')
  for edges,color,width in lines:
   for edge in edges:draw.line([tuple(xy[edge.start]),tuple(xy[edge.end])],fill=color,width=width)
 overlay=np.array(layer);use=np.any(overlay!=0,axis=2)&(mask[i,:,7:1081]>0);rgb[use]=overlay[use];writer.stdin.write(rgb.tobytes())
 if i in [0,8,16,24,32,40,48]:Image.fromarray(rgb).save(P/f'guide-frame-{i:03d}.jpg')
writer.stdin.close();assert writer.wait()==0
(P/'face-mesh-report.json').write_text(json.dumps({'frames':49,'faces_per_frame':2,'targets':reports,'no_audience_mesh':True,'source_based_geometry':True,'note':'Flow-filled frames are source-image tracking estimates, not direct landmark detections. Inspect profile fits before render.'},indent=2))
print(json.dumps({k:{a:b for a,b in v.items() if a!='methods'} for k,v in reports.items()},indent=2))
