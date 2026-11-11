from pathlib import Path
import os,sys,cv2,numpy as np,torch
from PIL import Image,ImageDraw
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/'landmark_packages'));os.environ['TORCH_HOME']=str(P/'landmark_model_cache');import face_alignment
torch.set_num_threads(8);model=face_alignment.FaceAlignment(face_alignment.LandmarksType.TWO_HALF_D,device='cpu',flip_input=False,face_detector='folder',compile=False)
c=cv2.VideoCapture(str(P/'jobs/mcma_first50_v1/source.mp4'));ok,b=c.read();rgb=cv2.cvtColor(b,cv2.COLOR_BGR2RGB);board=Image.new('RGB',(1200,720),(20,20,20));draw=ImageDraw.Draw(board)
boxes=[[290,135,333,200],[282,125,338,210],[295,140,331,195]]
for row,flip in enumerate([False,True]):
 model.flip_input=flip
 for col,box in enumerate(boxes):
  points=model.get_landmarks_from_image(rgb,detected_faces=[np.array(box)])[0];im=Image.fromarray(rgb);d=ImageDraw.Draw(im)
  for a,z in [(0,17),(17,22),(22,27),(27,31),(31,36),(36,42),(42,48),(48,60),(60,68)]:
   path=[tuple(x[:2]) for x in points[a:z]]
   if a in [36,42,48,60]:path.append(path[0])
   d.line(path,fill=(255,135,100) if a>=48 else (50,170,205),width=1)
  board.paste(im.crop((210,90,350,230)).resize((360,360)),(col*400,row*360));draw.text((col*400,row*360),f'flip{flip} box{box}',fill='white')
board.save(P/'fan-probe/profile-box-comparison-v2.jpg')
