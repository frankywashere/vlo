from pathlib import Path
from PIL import Image,ImageDraw
p=Path(__file__).resolve().parent/'jobs/mcma_first50_v1';out=p.parent.parent/'mask-boards';out.mkdir(exist_ok=True)
reports=sorted(p.glob('mask-report-*.json'))
for offset in range(0,len(reports),5):
 group=reports[offset:offset+5];board=Image.new('RGB',(1088,410*len(group)),(20,20,20));draw=ImageDraw.Draw(board)
 for row,report in enumerate(group):
  i=int(report.stem.split('-')[-1]);files=sorted(p.glob(f'mask-review-{i:03d}-*.jpg'));selected=[files[len(files)//2],files[-1]]
  draw.text((8,row*410+5),f'Shot {i}: middle / end',fill='white')
  for col,file in enumerate(selected):board.paste(Image.open(file),(col*544,row*410+26))
 board.save(out/f'board-{offset//5+1:02d}.jpg')
print(len(reports),'shots represented')
