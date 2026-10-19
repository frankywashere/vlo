"""CPU-only, bounded compositing of reviewed SAM label maps."""
import json, re, subprocess, zipfile
from pathlib import Path
import numpy as np
from PIL import Image
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

def depth_blend(depth, opacity):
    # Depth is the base, with a light shared magenta tint. Original RGB is
    # retained only outside the person masks, not blended into their depth.
    magenta = np.array([255,79,216],dtype=np.float32)
    return np.rint(depth.astype(np.float32)*(1-opacity)+magenta*opacity).astype(np.uint8)


def compose(source, archive, vocals, output, selected_label, fill_mode='gray', depth_source=None, target_styles=None, face_mesh=False, mesh_all_people=False, depth_opacity=1.0):
    if not 0 <= depth_opacity <= 1:raise ValueError('Invalid depth opacity')
    if fill_mode not in ['gray','original','color','depth','solid','translucent']:raise ValueError('Invalid fill mode')
    styles=[]
    if target_styles is not None:
        if fill_mode not in ['depth','solid','original','translucent']:raise ValueError('Target styles require a depth or solid guide')
        if not isinstance(target_styles,list) or not 1<=len(target_styles)<=5:raise ValueError('Expected 1–5 target styles')
        for style in target_styles:
            if (not isinstance(style,dict) or set(style)!={'label','color'} or type(style['label']) is not int
                    or not 1<=style['label']<=65535 or not isinstance(style['color'],str)
                    or not re.fullmatch(r'#[0-9A-Fa-f]{6}',style['color'])):raise ValueError('Invalid target style')
            rgb=np.array([int(style['color'][i:i+2],16) for i in (1,3,5)],dtype=np.float32)
            if not rgb.max():raise ValueError('Target color must be visible')
            styles.append((style['label'],rgb))
        if len({label for label,_ in styles})!=len(styles):raise ValueError('Target labels must be unique')
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
            if name != f'labels/labels_{i:05d}.png' or z.getinfo(name).file_size > 8_000_000:
                raise ValueError('Invalid or oversized mask frame')
            (masks/f'labels_{i:05d}.png').write_bytes(z.read(name))
            with Image.open(masks/f'labels_{i:05d}.png') as image:
                if image.size!=(width,height):raise ValueError('Mask dimensions differ from source')
                values=np.array(image)
                if values.ndim!=2 or not np.issubdtype(values.dtype,np.integer) or np.any(values<0) or np.any(values>65535):
                    raise ValueError('Invalid tracking labels')
    duration = len(entries)/24
    audio = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(vocals)]))
    if abs(float(audio['format']['duration'])-duration) > .1:
        raise ValueError('Guide audio and masks do not have matching duration')
    painter = None
    if face_mesh:
        from face_mesh import FaceMeshPainter
        painter = FaceMeshPainter(multiple_per_label=mesh_all_people)
    depth_decoder=subprocess.Popen(['ffmpeg','-v','error','-i',str(depth_source),'-map','0:v:0','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],stdout=subprocess.PIPE) if fill_mode=='depth' else None
    decoded = subprocess.Popen(['ffmpeg','-v','error','-i',str(source),'-map','0:v:0','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],stdout=subprocess.PIPE)
    labels = subprocess.Popen(['ffmpeg','-v','error','-framerate','24','-i',str(masks/'labels_%05d.png'),'-f','rawvideo','-pix_fmt','gray16le','pipe:1'],stdout=subprocess.PIPE)
    encoded = subprocess.Popen(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{width}x{height}','-r','24','-i','pipe:0','-i',str(vocals),'-map','0:v:0','-map','1:a:0','-c:v','libx264','-threads','4','-crf','18','-pix_fmt','yuv420p','-af','apad','-c:a','aac','-b:a','192k','-t',str(duration),'-movflags','+faststart',str(output)],stdin=subprocess.PIPE)
    coverage=[]; target_coverage={str(label):[] for label,_ in styles}
    try:
        for i in range(len(entries)):
            raw = decoded.stdout.read(width*height*3)
            mask = labels.stdout.read(width*height*2)
            if len(raw)!=width*height*3 or len(mask)!=width*height*2:
                raise ValueError('Source and mask frame dimensions/count do not match')
            frame = np.frombuffer(raw,dtype=np.uint8).reshape(height,width,3).copy()
            original = frame.copy() if painter else None
            label_frame=np.frombuffer(mask,dtype='<u2').reshape(height,width)
            target = clean_border_fragments(label_frame==selected_label) if not styles else np.zeros((height,width),dtype=bool)
            styled_masks=[]
            for label,rgb in styles:
                styled=clean_border_fragments(label_frame==label)
                styled_masks.append((styled,rgb));target|=styled
                target_coverage[str(label)].append(int(styled.sum()))
            area=int(target.sum())
            if not area and not face_mesh and fill_mode not in ['color','depth','solid','translucent']: raise ValueError(f'Selected person absent in frame {i}; review required')
            coverage.append(area)
            if fill_mode=='solid':
                if styles:
                    for styled,rgb in styled_masks:frame[styled]=rgb.astype(np.uint8)
                else:frame[target]=128
            if fill_mode=='translucent':
                if styles:
                    for styled,rgb in styled_masks:
                        frame[styled]=np.rint(frame[styled].astype(np.float32)*.8+rgb*.2).astype(np.uint8)
                else:frame[target]=np.rint(frame[target].astype(np.float32)*.8+np.array([0,220,255],dtype=np.float32)*.2).astype(np.uint8)
            if fill_mode=='gray':frame[target]=128
            elif fill_mode=='color':frame[target]=np.rint(frame[target].astype(np.float32)*.5 + np.array([0,220,255],dtype=np.float32)*.5).astype(np.uint8)
            if fill_mode=='depth':
                depth_raw=depth_decoder.stdout.read(width*height*3)
                if len(depth_raw)!=width*height*3:raise ValueError('Depth frame count mismatch')
                depth_frame=np.frombuffer(depth_raw,dtype=np.uint8).reshape(height,width,3)
                if styles:
                    # Keep the depth signal's value/brightness variation while
                    # giving each selected identity a distinct, stable hue.
                    brightness=depth_frame.max(axis=2)
                    for styled,rgb in styled_masks:
                        frame[styled]=(depth_blend(depth_frame[styled],depth_opacity)
                            if depth_opacity < 1 else np.rint(brightness[styled,None]*(rgb/rgb.max())).astype(np.uint8))
                else:frame[target]=depth_blend(depth_frame[target],depth_opacity) if depth_opacity < 1 else depth_frame[target]
            if painter:
                painter.apply(original, frame, label_frame, target)
            encoded.stdin.write(frame.tobytes())
        if decoded.stdout.read(1) or (labels and labels.stdout.read(1)):
            raise ValueError('Unexpected extra source or mask frames')
        if depth_decoder and depth_decoder.stdout.read(1):raise ValueError('Unexpected extra depth frames')
        encoded.stdin.close()
        if any(p.wait()!=0 for p in [p for p in (decoded, labels, encoded, depth_decoder) if p is not None]):
            raise RuntimeError('Video decode/encode failed')
    finally:
        if painter:painter.close()
        for p in [p for p in (decoded, labels, encoded, depth_decoder) if p is not None]:
            if p.poll() is None: p.kill();p.wait()
    result={'frames':len(entries),'fps':24,'duration_seconds':duration,'width':width,'height':height,'selected_label':selected_label,'mask_pixels_per_frame':coverage,'fill_mode':fill_mode,'fill_rgb':[128,128,128] if fill_mode=='gray' else None,'overlay_rgb':[0,220,255] if fill_mode in ['color','translucent'] else None,'overlay_opacity':.2 if fill_mode=='translucent' else .5 if fill_mode=='color' else None,'device':'cpu','depth':fill_mode=='depth','face_mesh':face_mesh}
    if fill_mode=='depth':result['depth_opacity']=depth_opacity
    if painter:result['face_mesh_metrics']=painter.metrics
    if styles:result.update(target_styles=target_styles,target_coverage=target_coverage)
    return result
