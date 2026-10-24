import json,time,tempfile,subprocess
from pathlib import Path
from google.cloud import storage
started=time.time(); client=storage.Client(); bucket=client.bucket('charswap-franky-20261013-app')
job=json.loads(bucket.blob('jobs/9ebd35094e7f490fb459f0196303bc73.json').download_as_text())
report={'started_at':started,'cpu':2,'memory_gib':2,'duration':15,'results':[]}
root=Path(tempfile.mkdtemp());before=root/'before.mp4';after=root/'after.mp4'
def download(uri,path):
 t=time.monotonic(); b,k=uri[5:].split('/',1);client.bucket(b).blob(k).download_to_filename(str(path));return round(time.monotonic()-t,3)
report['download_before_seconds']=download(job.get('before_uri') or job['normalized_uri'],before)
report['download_after_seconds']=download(job['output_uri'],after)
base=['ffmpeg','-v','error','-nostdin','-y','-i',str(before),'-i',str(after)]
encode=['-c:v','libx264','-threads','2','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-t','15','-movflags','+faststart']
def run(name,args,outputs):
 t=time.monotonic();subprocess.run(args,check=True,timeout=90,capture_output=True);r={'name':name,'seconds':round(time.monotonic()-t,3),'bytes':{p.name:p.stat().st_size for p in outputs}};report['results'].append(r);print(json.dumps(r),flush=True)
normal='fps=24,trim=duration=15,setpts=PTS-STARTPTS,setsar=1'
full=root/'full.mp4';comp=root/'comparison.mp4';dual=root/'dual.mp4';dualcomp=root/'dualcomparison.mp4';portrait=root/'portrait.mp4'
run('finish_baseline',base+['-map','1:v:0','-map','1:a:0?', '-vf',normal]+encode+[str(full)],[full])
stack=f'[0:v]{normal},scale=1024:768:force_original_aspect_ratio=decrease,pad=1024:768:(ow-iw)/2:(oh-ih)/2[b];[1:v]{normal},scale=1024:768,split[a][f];[b][a]vstack=inputs=2[c]'
run('comparison_separate',base+['-filter_complex',stack.replace(',split[a][f]','[a]'),'-map','[c]','-map','1:a:0?']+encode+[str(comp)],[comp])
run('finish_and_comparison_one_process',base+['-filter_complex',stack,'-map','[f]','-map','1:a:0?']+encode+[str(dual)]+['-map','[c]','-map','1:a:0?']+encode+[str(dualcomp)],[dual,dualcomp])
# Center crops are only a throughput test for two 432x768 portrait panels.
pstack=f'[0:v]{normal},scale=-2:768,crop=432:768,setsar=1[b];[1:v]{normal},scale=-2:768,crop=432:768,setsar=1[a];[b][a]hstack=inputs=2[c]'
run('portrait_side_by_side_864x768',base+['-filter_complex',pstack,'-map','[c]','-map','1:a:0?']+encode+[str(portrait)],[portrait])
t=time.monotonic();bucket.blob('experiments/comparison-export-benchmark/landscape.mp4').upload_from_filename(str(comp),content_type='video/mp4');report['upload_comparison_seconds']=round(time.monotonic()-t,3)
report['total_worker_seconds']=round(time.time()-started,3)
report['validation']=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(comp)]))
bucket.blob('experiments/comparison-export-benchmark/report.json').upload_from_string(json.dumps(report),content_type='application/json');print(json.dumps({'total_worker_seconds':report['total_worker_seconds'],'report':'experiments/comparison-export-benchmark/report.json'}),flush=True)
