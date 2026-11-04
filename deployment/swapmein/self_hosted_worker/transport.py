"""Private outbound protocol; secrets and signed URLs never enter log messages."""
from __future__ import annotations
import http.client
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from .recipe import WorkerError

class ControlRejected(WorkerError):
    pass

class SignedURLExpired(WorkerError):
    def __init__(self):super().__init__('worker_failed','A temporary media authorization needs refreshing.')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*_args,**_kwargs):return None

OPENER=urllib.request.build_opener(NoRedirect())

def validate_url(url: str, allow_local: bool=False, control: bool=False) -> urllib.parse.SplitResult:
    try:p=urllib.parse.urlsplit(url)
    except ValueError:raise WorkerError('worker_failed','A service address is invalid.') from None
    if p.username or p.password or p.fragment or not p.hostname:
        raise WorkerError('worker_failed','A service address is invalid.')
    local=p.hostname in {'localhost','127.0.0.1','::1'}
    if p.scheme!='https' and not (allow_local and local and p.scheme=='http'):
        raise WorkerError('worker_failed','Remote worker services require HTTPS.')
    if not control and not (allow_local and local) and not (p.hostname=='storage.googleapis.com' or p.hostname.endswith('.storage.googleapis.com')):
        raise WorkerError('worker_failed','Media must use the configured private Google storage endpoint.')
    return p

class ControlClient:
    def __init__(self,base_url: str,route: str,token: str,worker_id: str,allow_local=False):
        validate_url(base_url,allow_local,control=True)
        route={'google':'google_h3','paperspace':'paperspace_h3'}.get(route,route)
        if route not in {'google_h3','paperspace_h3'}:raise WorkerError('worker_failed','Invalid worker route.')
        if not token:raise WorkerError('worker_failed','A worker credential is required.')
        self.base=base_url.rstrip('/')+'/worker/self-hosted/'+route
        self.token=token;self.worker_id=worker_id
    def call(self,action: str,body: dict) -> dict:
        req=urllib.request.Request(self.base+'/'+action,data=json.dumps({'worker_id':self.worker_id,**body},allow_nan=False).encode(),headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'},method='POST')
        try:
            with OPENER.open(req,timeout=25) as r:
                if int(r.headers.get('Content-Length','0'))>2*1024*1024:raise WorkerError('worker_failed','The worker service returned an oversized response.')
                data=json.loads(r.read(2*1024*1024+1))
                if not isinstance(data,dict):raise ValueError()
                return data
        except urllib.error.HTTPError as e:
            if e.code in {401,403,409,410}:raise ControlRejected('worker_failed','Worker authorization or job ownership is no longer valid.') from None
            raise WorkerError('worker_failed','The worker service is temporarily unavailable.') from None
        except (urllib.error.URLError,ValueError,TimeoutError,OSError):raise WorkerError('worker_failed','The worker service could not be reached safely.') from None

def download(url: str,destination: Path,maximum: int,allow_local=False,check=lambda:None) -> None:
    validate_url(url,allow_local)
    temporary=destination.with_suffix(destination.suffix+'.partial')
    try:
        with OPENER.open(urllib.request.Request(url),timeout=30) as response,temporary.open('wb') as f:
            import os
            os.chmod(temporary,0o600)
            if int(response.headers.get('Content-Length','0'))>maximum:raise WorkerError('worker_failed','A staged input exceeds the worker size limit.')
            size=0
            while True:
                check();b=response.read(1024*1024)
                if not b:break
                size+=len(b)
                if size>maximum:raise WorkerError('worker_failed','A staged input exceeds the worker size limit.')
                f.write(b)
            if not size:raise WorkerError('worker_failed','A staged input is empty.')
        temporary.replace(destination)
    except urllib.error.HTTPError as e:
        if e.code in {401,403}:raise SignedURLExpired() from None
        raise WorkerError('worker_failed','A private input could not be downloaded.') from None
    except (urllib.error.URLError,TimeoutError,OSError):raise WorkerError('worker_failed','A private input transfer failed.') from None
    finally:
        temporary.unlink(missing_ok=True)

def upload(descriptor: dict,source: Path,allow_local=False,check=lambda:None) -> None:
    p=validate_url(descriptor.get('url',''),allow_local)
    headers=descriptor.get('headers') or {}
    if not isinstance(headers,dict) or any(not isinstance(k,str) or not isinstance(v,str) or '\r' in k+v or '\n' in k+v or k.lower() in {'authorization','proxy-authorization','host','transfer-encoding','content-length'} for k,v in headers.items()):
        raise WorkerError('worker_failed','The output upload authorization is invalid.')
    cls=http.client.HTTPSConnection if p.scheme=='https' else http.client.HTTPConnection
    connection=cls(p.hostname,p.port,timeout=30)
    try:
        connection.putrequest('PUT',urllib.parse.urlunsplit(('', '',p.path,p.query,'')))
        for k,v in headers.items():connection.putheader(k,v)
        connection.putheader('Content-Length',str(source.stat().st_size));connection.endheaders()
        with source.open('rb') as f:
            while True:
                check();b=f.read(1024*1024)
                if not b:break
                connection.send(b)
        response=connection.getresponse();response.read(1024)
        if response.status in {401,403}:raise SignedURLExpired()
        if not 200<=response.status<300:raise WorkerError('worker_failed','The private output upload was rejected.')
    except (OSError,TimeoutError,http.client.HTTPException):raise WorkerError('worker_failed','The private output transfer failed.') from None
    finally:connection.close()
