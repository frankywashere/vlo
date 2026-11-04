"""Bounded CPU staging and exact-frame assembly; never submits generation."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from .recipe import WorkerError, geometry

def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def atomic_json(path: Path, data: dict) -> None:
    temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w',encoding='utf-8') as f:
        os.chmod(temporary,0o600);json.dump(data,f,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
    temporary.replace(path)

def command(args: list[str], check=lambda:None, limit: float=120) -> bytes:
    # This process alone is owned by this call; never signal shared services.
    with subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as proc:
        started=time.monotonic()
        try:
            while True:
                check()
                try:
                    out,_err=proc.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic()-started>limit:raise WorkerError('worker_failed','A local media operation exceeded its time limit.')
            if proc.returncode:raise WorkerError('worker_failed','A local media operation failed.')
            return out
        except BaseException:
            if proc.poll() is None:
                proc.terminate()
                try:proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.communicate()
            raise

def probe(path: Path, check=lambda:None) -> dict:
    try:return json.loads(command(['ffprobe','-v','error','-count_frames','-show_streams','-show_format','-of','json',str(path)],check))
    except (ValueError,KeyError):raise WorkerError('worker_failed','The staged media could not be inspected.') from None

def inspect_source(path: Path, maximum_pixels: int=1536*864, check=lambda:None) -> dict:
    m=probe(path,check);v=next((s for s in m.get('streams',[]) if s.get('codec_type')=='video'),None)
    if not v:raise WorkerError('worker_failed','The normalized source contains no video.')
    try:
        rate=v.get('avg_frame_rate','0/1').split('/');fps=int(rate[0])/int(rate[1]);frames=int(v['nb_read_frames'])
    except (ValueError,KeyError,ZeroDivisionError):raise WorkerError('worker_failed','The normalized source frame timing is invalid.') from None
    if fps!=24 or not 1<=frames<=1440:raise WorkerError('worker_failed','This recipe requires a24fps source of at most60seconds.')
    return {**geometry(v['width'],v['height'],maximum_pixels),'frames':frames,'has_audio':any(s.get('codec_type')=='audio' for s in m['streams'])}

def verify_part(path: Path, frames: int, shape: dict, check=lambda:None) -> dict:
    m=probe(path,check);v=next((s for s in m.get('streams',[]) if s.get('codec_type')=='video'),{})
    if (int(v.get('nb_read_frames',0)),v.get('width'),v.get('height'),v.get('avg_frame_rate'))!=(frames,shape['width'],shape['height'],'24/1'):
        raise WorkerError('worker_failed','A completed part does not match its source dimensions or frame count.')
    return {'frames':frames,'bytes':path.stat().st_size,'sha256':digest(path)}

def copy_untouched(source: Path, destination: Path, shot: dict, shape: dict, check=lambda:None) -> None:
    temporary=destination.with_name(destination.stem+'.partial.mp4')
    command(['ffmpeg','-v','error','-y','-threads','4','-i',str(source),'-vf',f"trim=start_frame={shot['start_frame']}:end_frame={shot['end_frame']},setpts=PTS-STARTPTS",'-frames:v',str(shot['delivery_frames']),'-an','-c:v','libx264','-preset','fast','-crf','17','-pix_fmt','yuv420p','-r','24','-movflags','+faststart',str(temporary)],check)
    verify_part(temporary,shot['delivery_frames'],shape,check);os.chmod(temporary,0o600);temporary.replace(destination)

def frame_hashes(path: Path, check=lambda:None) -> list[str]:
    text=command(['ffmpeg','-v','error','-threads','4','-i',str(path),'-map','0:v:0','-an','-f','framemd5','-'],check).decode()
    return [s.rsplit(',',1)[1].strip() for s in text.splitlines() if s and not s.startswith('#')]

def assemble(directory: Path, shots: list[dict], shape: dict, check=lambda:None) -> Path:
    # Relative, numeric filenames avoid concat-list path or quote injection.
    listing=directory/'concat.txt';listing.write_text(''.join(f"file 'shot-{s['index']:03d}.mp4'\n" for s in shots))
    out=directory/'assembled.mp4';temporary=directory/'assembled.partial.mp4'
    if not out.exists():
        command(['ffmpeg','-v','error','-y','-f','concat','-safe','1','-i',str(listing),'-map','0:v:0','-an','-c:v','copy','-movflags','+faststart',str(temporary)],check)
        verify_part(temporary,shape['frames'],shape,check);os.chmod(temporary,0o600);temporary.replace(out)
    verify_part(out,shape['frames'],shape,check)
    expected=[]
    for s in shots:expected.extend(frame_hashes(directory/f"shot-{s['index']:03d}.mp4",check))
    if frame_hashes(out,check)!=expected:raise WorkerError('worker_failed','The assembly does not match the ordered generated parts.')
    # Audio/watermark/comparison are finalized by the existing server finalizer.
    atomic_json(directory/'assembly-verification.json',{'frames':len(expected),'ordered_frames_identical':True,'audio_policy':'server_restore_original'})
    return out
