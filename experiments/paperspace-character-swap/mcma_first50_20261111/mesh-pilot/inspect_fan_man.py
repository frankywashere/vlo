from pathlib import Path
import cv2,numpy as np
from PIL import Image,ImageDraw
P=Path(__file__).resolve().parent;p=np.load(P/'fan-probe/landmarks.npz')['points'][...,:2];c=cv2.VideoCapture(str(P.parent/'source-working.mp4'));board=Image.new('RGB',(1120,1120))
for i in range(49):
 ok,b=c.read();im=Image.fromarray(cv2.cvtColor(b,cv2.COLOR_BGR2RGB));d=ImageDraw.Draw(im);xy=p[i,1]
 for a,z in [(0,17),(36,42),(42,48),(48,60),(60,68)]:
  line=[tuple(x) for x in xy[a:z]]
  if a>0:line.append(line[0])
  d.line(line,fill='orange' if a>=48 else 'cyan',width=1)
 cx=793-(i/48)*140;cy=190+(i/48)*32
 crop=im.crop((int(cx-65),int(cy-65),int(cx+65),int(cy+65))).resize((160,160));ImageDraw.Draw(crop).text((0,0),str(i),fill='white');board.paste(crop,((i%7)*160,(i//7)*160))
board.save(P/'fan-all-man.jpg')
