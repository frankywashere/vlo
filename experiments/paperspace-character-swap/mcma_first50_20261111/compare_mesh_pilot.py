from pathlib import Path
import subprocess,json,hashlib
P=Path(__file__).resolve().parent;out=P/'mesh-pilot';inputs=[P/'source-working.mp4',P/'audio-pilot/shot-001-with-audio.mp4',out/'shot-001-with-audio.mp4'];assert all(x.exists() for x in inputs)
args=['ffmpeg','-v','error','-nostdin','-n']
for path in inputs:args+=['-i',str(path)]
filt=';'.join(f'[{i}:v]trim=end_frame=49,setpts=PTS-STARTPTS,scale=536:384[v{i}]' for i in range(3))+';[v0][v1][v2]hstack=inputs=3[v]'
dest=out/'source-audio-mesh-comparison.mp4'
subprocess.run(args+['-filter_complex',filt,'-map','[v]','-map','0:a:0','-t',str(49/24),'-c:v','libx264','-crf','17','-preset','fast','-c:a','aac','-movflags','+faststart',str(dest)],check=True)
probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(dest)]));(out/'comparison-media-report.json').write_text(json.dumps(probe,indent=2));print(dest)
