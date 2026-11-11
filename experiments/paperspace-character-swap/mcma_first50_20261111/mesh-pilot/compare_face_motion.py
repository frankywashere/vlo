from pathlib import Path
import cv2,numpy as np,subprocess,json
from PIL import Image,ImageDraw
P=Path(__file__).resolve().parent;R=P.parent
paths=[R/'source-working.mp4',R/'audio-pilot/shot-001.mp4',P/'shot-001.mp4'];caps=[cv2.VideoCapture(str(x)) for x in paths]
writer=subprocess.Popen(['ffmpeg','-v','error','-nostdin','-n','-f','rawvideo','-pix_fmt','rgb24','-s','960x640','-r','24','-i','pipe:0','-an','-c:v','libx264','-crf','17','-movflags','+faststart',str(P/'face-motion-silent.mp4')],stdin=subprocess.PIPE)
board=Image.new('RGB',(960,2560));samples=[0,6,12,18,24,30,36,42]
for i in range(49):
 ims=[]
 for c in caps:
  ok,b=c.read();assert ok;ims.append(Image.fromarray(cv2.cvtColor(b,cv2.COLOR_BGR2RGB)))
 frame=Image.new('RGB',(960,640));draw=ImageDraw.Draw(frame)
 for person in range(2):
  cx=(793-140*i/48) if person==0 else (287+162*i/48);cy=(190+32*i/48) if person==0 else (172+45*i/48)
  for col,im in enumerate(ims):
   crop=im.crop((int(cx-80),int(cy-70),int(cx+80),int(cy+90))).resize((320,320));frame.paste(crop,(col*320,person*320));draw.text((col*320+3,person*320+3),['SOURCE','AUDIO ONLY','AUDIO + MESH'][col]+f' f{i}',fill='white')
 writer.stdin.write(np.array(frame).tobytes())
 if i in samples:
  # Both faces at160px per panel, sequential sample blocks.
  board.paste(frame.resize((960,320)),(0,samples.index(i)*320))
writer.stdin.close();assert writer.wait()==0
for c in caps:c.release()
board.save(P/'face-motion-contact.jpg')
subprocess.run(['ffmpeg','-v','error','-nostdin','-n','-i',str(P/'face-motion-silent.mp4'),'-i',str(R/'source-working.mp4'),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-t',str(49/24),'-movflags','+faststart',str(P/'face-motion-with-audio.mp4')],check=True)
print(P/'face-motion-with-audio.mp4')
