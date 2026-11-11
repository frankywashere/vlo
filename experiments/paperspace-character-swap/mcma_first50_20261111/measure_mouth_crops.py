"""Diagnostic only: visible mouth-aperture timelines, not creative acceptance."""
from pathlib import Path
import json,cv2,numpy as np,mediapipe as mp
P=Path(__file__).resolve().parent
options=mp.tasks.vision.FaceLandmarkerOptions(base_options=mp.tasks.BaseOptions(model_asset_path='/Users/frank/Desktop/Coding/CharSwap/benchmark/models/face_landmarker.task'),running_mode=mp.tasks.vision.RunningMode.IMAGE,num_faces=4,min_face_detection_confidence=.35,min_face_presence_confidence=.35)
paths={'source':P/'source-working.mp4','silent_pilot':P/'pilot/shot-001.mp4'}
if (P/'audio-pilot/shot-001.mp4').exists():paths['audio_pilot']=P/'audio-pilot/shot-001.mp4'
result={}
with mp.tasks.vision.FaceLandmarker.create_from_options(options) as detector:
 for name,path in paths.items():
  cap=cv2.VideoCapture(str(path));rows=[]
  for i in range(49):
   ok,bgr=cap.read()
   if not ok:break
   row={'frame':i}
   for label,(x0,y0,x1,y1) in {'woman':(110,0,490,360),'man':(610,0,1030,390)}.items():
    crop=cv2.resize(bgr[y0:y1,x0:x1],None,fx=2,fy=2)
    rgb=cv2.cvtColor(crop,cv2.COLOR_BGR2RGB);found=detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=rgb));candidates=[]
    for face in found.face_landmarks:
     xy=np.array([[q.x,q.y] for q in face]);lo=xy.min(0);hi=xy.max(0);area=np.prod(hi-lo)
     if area<.01:continue
     candidates.append({'area':float(area),'mouth_aperture_ratio':float(np.linalg.norm(xy[13]-xy[14])/max(np.linalg.norm(xy[61]-xy[291]),1e-6))})
    if candidates:row[label]=max(candidates,key=lambda c:c['area'])
   rows.append(row)
  cap.release();summary={}
  for label in ['woman','man']:
   values=[r[label]['mouth_aperture_ratio'] for r in rows if label in r]
   summary[label]={'detected_frames':len(values),'min':min(values) if values else None,'max':max(values) if values else None,'std':float(np.std(values)) if values else None}
  result[name]={'rows':rows,'summary':summary}
(P/'mouth-crop-diagnostic.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v['summary'] for k,v in result.items()},indent=2))
