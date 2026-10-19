"""Stage only CPU mesh source and a hash-verified official model, never user media."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

MODEL_SHA256 = '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff'
MODEL_URL = 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task'


def stage(output, model=None):
    output=Path(output)
    if output.exists() and any(output.iterdir()):raise ValueError('Use an empty staging directory')
    data=Path(model).read_bytes() if model else urllib.request.urlopen(MODEL_URL,timeout=60).read(5_000_001)
    if hashlib.sha256(data).hexdigest()!=MODEL_SHA256:raise ValueError('Face model hash mismatch')
    output.mkdir(parents=True,exist_ok=True)
    directory=Path(__file__).parent
    for name in ('app.py','composite.py','face_mesh.py'):
        shutil.copyfile(directory/name,output/name)
    shutil.copyfile(directory/'Dockerfile.face-mesh',output/'Dockerfile')
    (output/'face_landmarker.task').write_bytes(data)
    image='us-east4-docker.pkg.dev/charswap-franky-20261013/proof/audio:face-mesh-v1'
    config={'steps':[{'name':'gcr.io/cloud-builders/docker','args':['build','-t',image,'.']}],
            'images':[image],'timeout':'1200s','options':{'logging':'GCS_ONLY'},
            'logsBucket':'gs://charswap-franky-20261013-proof/build-logs'}
    (output/'cloudbuild.json').write_text(json.dumps(config,indent=2))
    return {'model_sha256':MODEL_SHA256,'image':image,'sources':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.iterdir() if p.suffix=='.py'}}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--model')
    args=parser.parse_args();print(json.dumps(stage(args.output,args.model),indent=2))
