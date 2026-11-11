from pathlib import Path
import cv2,numpy as np,mediapipe as mp
from PIL import Image,ImageDraw
P=Path(__file__).resolve().parent;c=cv2.VideoCapture(str(P.parent/'source-working.mp4'));ok,b=c.read();assert ok
opt=mp.tasks.vision.FaceLandmarkerOptions(base_options=mp.tasks.BaseOptions(model_asset_path='/Users/frank/Desktop/Coding/CharSwap/benchmark/models/face_landmarker.task'),num_faces=1,min_face_detection_confidence=.1,min_face_presence_confidence=.1)
boxes=[(220,95,360,245),(260,110,350,220),(275,125,345,210),(200,60,400,290)]
board=Image.new('RGB',(1440,720))
with mp.tasks.vision.FaceLandmarker.create_from_options(opt) as d:
 for row,flip in enumerate([False,True]):
  for col,(x,y,z,w) in enumerate(boxes):
   rgb=cv2.cvtColor(cv2.resize(b[y:w,x:z],(512,512)),cv2.COLOR_BGR2RGB)
   if flip:rgb=rgb[:,::-1].copy()
   r=d.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=rgb));im=Image.fromarray(cv2.cvtColor(b,cv2.COLOR_BGR2RGB));draw=ImageDraw.Draw(im)
   for face in r.face_landmarks:
    points=np.array([[(1-v.x if flip else v.x)*(z-x)+x,v.y*(w-y)+y] for v in face])
    for edges,color in [(mp.tasks.vision.FaceLandmarksConnections.FACE_LANDMARKS_FACE_OVAL,'cyan'),(mp.tasks.vision.FaceLandmarksConnections.FACE_LANDMARKS_LIPS,'orange')]:
     for edge in edges:draw.line([tuple(points[edge.start]),tuple(points[edge.end])],fill=color,width=1)
   board.paste(im.crop((210,90,350,230)).resize((360,360)),(col*360,row*360));ImageDraw.Draw(board).text((col*360,row*360),f'{flip} {x,y,z,w} found{len(r.face_landmarks)}',fill='white')
board.save(P/'mediapipe-profile-probe.jpg')
