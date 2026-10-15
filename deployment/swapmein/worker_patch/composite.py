"""CPU-only, bounded compositing of reviewed SAM label maps."""
import json, subprocess, zipfile
from pathlib import Path
import numpy as np
from common import probe
from collections import deque


def clean_border_fragments(mask):
    # Any removable component must fit entirely inside the outer 8%-width
    # strip. A component touching the inner strip boundary is preserved.
    # Eight-connected components match the existing OpenCV local proof.
    height,width=mask.shape; limit=int(width*.08); result=mask.copy()
    for band in [result[:,:limit+1],result[:,::-1][:,:limit+1]]:
        visited=np.zeros(band.shape,dtype=bool)
        for seed in np.flatnonzero(band[:,0]):
            if visited[seed,0]: continue
            queue=deque([(int(seed),0)]);visited[seed,0]=True;component=[];touches_inner=False
            while queue:
                y,x=queue.popleft();component.append((y,x));touches_inner |= x==limit
                for yy in range(max(0,y-1),min(height,y+2)):
                    for xx in range(max(0,x-1),min(limit+1,x+2)):
                        if band[yy,xx] and not visited[yy,xx]:
                            visited[yy,xx]=True;queue.append((yy,xx))
            if not touches_inner and len(component)<height*width*.03:
                yy,xx=zip(*component);band[yy,xx]=False
    return result

def compose(source, archive, vocals, output, selected_label, fill_mode='gray', depth_source=None):
    if fill_mode not in ['gray','original','color','depth']:raise ValueError('Invalid fill mode')
    meta = probe(source)
    video = next(s for s in meta['streams'] if s['codec_type'] == 'video')
    width, height = video['width'], video['height']
    depth_decoder = None
    if fill_mode == 'depth':
        if depth_source is None:raise ValueError('Depth source required')
        depth_meta=probe(depth_source)
        depth_video=next(s for s in depth_meta['streams'] if s['codec_type']=='video')
        if (depth_video['width'],depth_video['height'])!=(width,height):raise ValueError('Depth dimensions differ from source')
    masks = output.parent/'labels'; masks.mkdir()
    with zipfile.ZipFile(archive) as z:
        entries = sorted(i for i in z.namelist() if i.startswith('labels/labels_') and i.endswith('.png'))
        if not 1 <= len(entries) <= 360:
            raise ValueError('Expected 1–360 mask frames')
        for i, name in enumerate(entries):
            if name != f'labels/labels_{i:05d}.png' or z.getinfo(name).file_size > 2_000_000:
                raise ValueError('Invalid or oversized mask frame')
            (masks/f'labels_{i:05d}.png').write_bytes(z.read(name))
    duration = len(entries)/24
    audio = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(vocals)]))
    if abs(float(audio['format']['duration'])-duration) > .1:
        raise ValueError('Guide audio and masks do not have matching duration')
    depth_decoder=subprocess.Popen(['ffmpeg','-v','error','-i',str(depth_source),'-map','0:v:0','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],stdout=subprocess.PIPE) if fill_mode=='depth' else None
    decoded = subprocess.Popen(['ffmpeg','-v','error','-i',str(source),'-map','0:v:0','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],stdout=subprocess.PIPE)
    labels = subprocess.Popen(['ffmpeg','-v','error','-framerate','24','-i',str(masks/'labels_%05d.png'),'-f','rawvideo','-pix_fmt','gray16le','pipe:1'],stdout=subprocess.PIPE)
    encoded = subprocess.Popen(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{width}x{height}','-r','24','-i','pipe:0','-i',str(vocals),'-map','0:v:0','-map','1:a:0','-c:v','libx264','-threads','4','-crf','18','-pix_fmt','yuv420p','-af','apad','-c:a','aac','-b:a','192k','-t',str(duration),'-movflags','+faststart',str(output)],stdin=subprocess.PIPE)
    coverage=[]
    try:
        for i in range(len(entries)):
            raw = decoded.stdout.read(width*height*3)
            mask = labels.stdout.read(width*height*2)
            if len(raw)!=width*height*3 or len(mask)!=width*height*2:
                raise ValueError('Source and mask frame dimensions/count do not match')
            frame = np.frombuffer(raw,dtype=np.uint8).reshape(height,width,3).copy()
            target = clean_border_fragments(np.frombuffer(mask,dtype='<u2').reshape(height,width)==selected_label)
            area=int(target.sum())
            if not area and fill_mode not in ['color','depth']: raise ValueError(f'Selected person absent in frame {i}; review required')
            coverage.append(area)
            if fill_mode=='gray':frame[target]=128
            elif fill_mode=='color':frame[target]=np.rint(frame[target].astype(np.float32)*.5 + np.array([0,220,255],dtype=np.float32)*.5).astype(np.uint8)
            if fill_mode=='depth':
                depth_raw=depth_decoder.stdout.read(width*height*3)
                if len(depth_raw)!=width*height*3:raise ValueError('Depth frame count mismatch')
                depth_frame=np.frombuffer(depth_raw,dtype=np.uint8).reshape(height,width,3)
                frame[target]=depth_frame[target]
            encoded.stdin.write(frame.tobytes())
        if decoded.stdout.read(1) or labels.stdout.read(1):
            raise ValueError('Unexpected extra source or mask frames')
        if depth_decoder and depth_decoder.stdout.read(1):raise ValueError('Unexpected extra depth frames')
        encoded.stdin.close()
        if any(p.wait()!=0 for p in [decoded, labels, encoded]+([depth_decoder] if depth_decoder else [])):
            raise RuntimeError('Video decode/encode failed')
    finally:
        for p in [decoded, labels, encoded]+([depth_decoder] if depth_decoder else []):
            if p.poll() is None: p.kill();p.wait()
    return {'frames':len(entries),'fps':24,'duration_seconds':duration,'width':width,'height':height,'selected_label':selected_label,'mask_pixels_per_frame':coverage,'fill_mode':fill_mode,'fill_rgb':[128,128,128] if fill_mode=='gray' else None,'overlay_rgb':[0,220,255] if fill_mode=='color' else None,'overlay_opacity':.5 if fill_mode=='color' else None,'device':'cpu','depth':fill_mode=='depth','face_mesh':False}
