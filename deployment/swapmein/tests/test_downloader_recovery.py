import asyncio
import json
from pathlib import Path
import pytest
from swapmein import downloader_runtime as runtime, video_downloader as dl, downloader_stream as wire
from swapmein.errors import PublicError
from test_api import api
import swapmein.app as app


def test_recovery_retries_once_and_keeps_cookie(monkeypatch, tmp_path):
    cookie = tmp_path / '.youtube-session.txt';cookie.write_text('private')
    (tmp_path / 'video.part').write_text('partial')
    candidate = ('2026.10.26', Path('/isolated/runtime'))
    monkeypatch.setattr(runtime, 'current', lambda: ('2026.8.19', None))
    stages=[];calls=[];accepted=[]
    def update(previous, progress, cancel):
        progress('checking');progress('updating');return candidate
    monkeypatch.setattr(runtime,'newer_runtime',update)
    monkeypatch.setattr(runtime,'accepted',accepted.append)
    def download(url, directory, maximum, cookies, **kwargs):
        calls.append(kwargs)
        if len(calls)==1:raise PublicError('Unavailable',422,code='source_unavailable')
        assert cookie.read_text()=='private' and not (tmp_path/'video.part').exists()
        assert kwargs['runtime']==candidate[1]
        return directory/'video.mp4'
    monkeypatch.setattr(dl,'download',download)
    assert dl.download_recovering('https://youtu.be/id',tmp_path,500,cookie,stages.append)==tmp_path/'video.mp4'
    assert len(calls)==2 and accepted==[candidate] and stages==['checking','updating','retrying']


@pytest.mark.parametrize('code',['source_too_large','source_login_required','source_duration_or_live_limit','download_timeout','download_cancelled'])
def test_ineligible_failure_never_checks_update(monkeypatch,tmp_path,code):
    monkeypatch.setattr(dl,'download',lambda *a,**k: (_ for _ in ()).throw(PublicError('Failed',code=code)))
    monkeypatch.setattr(runtime,'newer_runtime',lambda *a: pytest.fail('must not update'))
    with pytest.raises(PublicError):dl.download_recovering('https://youtu.be/id',tmp_path,500,None)


def test_retry_failure_is_terminal_and_candidate_rejected(monkeypatch,tmp_path):
    calls=[];rejected=[];candidate=('2026.10.26',tmp_path/'runtime')
    def download(*a,**k):calls.append(1);raise PublicError('Blocked',code='source_http_403')
    monkeypatch.setattr(dl,'download',download)
    monkeypatch.setattr(runtime,'newer_runtime',lambda *a:candidate)
    monkeypatch.setattr(runtime,'rejected',rejected.append)
    with pytest.raises(PublicError):dl.download_recovering('https://youtu.be/id',tmp_path,500,None)
    assert len(calls)==2 and rejected==[candidate]


@pytest.mark.parametrize('candidate',[None,ValueError('unreachable')])
def test_unavailable_update_retains_original_error(monkeypatch,tmp_path,candidate):
    original=PublicError('Original failure',code='source_unavailable');calls=[]
    def download(*a,**k):calls.append(1);raise original
    def update(*a):
        if isinstance(candidate,Exception):raise candidate
        return candidate
    monkeypatch.setattr(dl,'download',download);monkeypatch.setattr(runtime,'newer_runtime',update)
    with pytest.raises(PublicError) as caught:dl.download_recovering('https://youtu.be/id',tmp_path,500,None)
    assert len(calls)==1
    if candidate is None: assert caught.value is original
    else: assert caught.value.code=='downloader_update_unavailable' and 'Original failure' in str(caught.value)


@pytest.mark.parametrize('bad',['2026.10.26rc1','latest','2026.10.26;curl','https://evil/2026.10.26'])
def test_runtime_only_accepts_stable_date_versions(bad):
    with pytest.raises(ValueError):runtime.release_number(bad)


def test_stream_phases_then_exact_binary_and_cleanup(monkeypatch,tmp_path):
    output=tmp_path/'video.mp4';data=b'\x00\xff\n{"event":"error"}';output.write_bytes(data);cleaned=[]
    def prepare(*a,progress,cancel):
        for stage in ['checking','updating','retrying']:progress(stage)
        return output,tmp_path
    monkeypatch.setattr(dl,'prepare',prepare);monkeypatch.setattr(dl,'cleanup',cleaned.append)
    chunks=list(wire.stream(None,None,'https://youtu.be/id'))
    assert [json.loads(c)['stage'] for c in chunks[:4]]==['preparing','checking','updating','retrying']
    assert json.loads(chunks[4])=={'event':'ready','bytes':len(data)}
    assert b''.join(chunks[5:])==data and cleaned==[tmp_path]


def test_stream_error_is_safe_and_has_no_binary(monkeypatch):
    def prepare(*a,**k):raise RuntimeError('COOKIE=secret')
    monkeypatch.setattr(dl,'prepare',prepare)
    chunks=b''.join(wire.stream(None,None,'https://youtu.be/id'))
    assert b'secret' not in chunks and b'"event": "error"' in chunks and b'"ready"' not in chunks


def test_stream_response_disconnect_cancels_and_cleans(monkeypatch,tmp_path):
    output=tmp_path/'video.mp4';output.write_bytes(b'test');cleaned=[];cancelled=[]
    def prepare(*a,progress,cancel):cancelled.append(cancel);return output,tmp_path
    monkeypatch.setattr(dl,'prepare',prepare);monkeypatch.setattr(dl,'cleanup',cleaned.append)
    async def send(message):
        if message['type']=='http.response.body' and b'"ready"' in message.get('body',b''):raise OSError('disconnect')
    async def receive():return {'type':'http.disconnect'}
    from starlette.requests import ClientDisconnect
    with pytest.raises(ClientDisconnect):asyncio.run(wire.DownloadStreamResponse(None,None,'https://youtu.be/id')({'type':'http','method':'POST','headers':[],'asgi':{'spec_version':'2.4'}},receive,send))
    assert cleaned==[tmp_path] and cancelled[0].is_set()


def test_api_stream_checks_auth_and_access_before_starting(api,monkeypatch):
    client,records,*_=api
    monkeypatch.setattr(app,'verify_account',lambda *_:{'email':'user@example.com','account_id':'b'*64})
    monkeypatch.setattr(dl,'prepare',lambda *a,**k:pytest.fail('not authorized'))
    response=client.post('/api/video-downloader',json={'url':'https://youtu.be/id'},headers={'Accept':wire.MEDIA_TYPE})
    assert response.status_code==403


def test_update_only_installs_newer_release_in_isolated_target(monkeypatch,tmp_path):
    from io import BytesIO
    stages=[];commands=[]
    monkeypatch.setattr(runtime.tempfile,'mkdtemp',lambda **k:str(tmp_path))
    class Response:
        content=b'{}'
        def raise_for_status(self):pass
        def json(self):return {'info':{'version':'2026.10.26'}}
    monkeypatch.setattr(runtime.httpx,'get',lambda *a,**k:Response())
    class Process:
        returncode=0
        def poll(self):return 0
    def launch(command,**kwargs):
        commands.append(command);(tmp_path/'yt_dlp').mkdir();(tmp_path/'yt_dlp/__init__.py').write_text('')
        assert kwargs['stdout']==runtime.subprocess.DEVNULL and kwargs['stderr']==runtime.subprocess.DEVNULL
        return Process()
    monkeypatch.setattr(runtime.subprocess,'Popen',launch)
    monkeypatch.setattr(runtime,'_rejected',{})
    assert runtime.newer_runtime('2026.8.19',stages.append,None)==('2026.10.26',tmp_path)
    command=commands[0]
    assert '--isolated' in command and '--only-binary=:all:' in command and '--target' in command and '--index-url' in command
    assert command[-1]=='yt-dlp[default]==2026.10.26'
    assert stages==['checking','updating']
    assert runtime.newer_runtime('2026.10.26',stages.append,None) is None and len(commands)==1


def test_cancel_during_install_kills_child_and_removes_candidate(monkeypatch,tmp_path):
    from threading import Event
    cancel=Event();cancelled=[]
    monkeypatch.setattr(runtime.tempfile,'mkdtemp',lambda **k:str(tmp_path/'runtime'))
    (tmp_path/'runtime').mkdir()
    class Response:
        content=b'{}'
        def raise_for_status(self):pass
        def json(self):return {'info':{'version':'2026.10.26'}}
    monkeypatch.setattr(runtime.httpx,'get',lambda *a,**k:Response())
    class Process:
        pid=999;returncode=None
        def poll(self):return None
        def wait(self):return 0
    def launch(*a,**k):cancel.set();return Process()
    monkeypatch.setattr(runtime.subprocess,'Popen',launch)
    monkeypatch.setattr(runtime.os,'killpg',lambda *a:cancelled.append(a))
    monkeypatch.setattr(runtime,'_rejected',{})
    with pytest.raises(PublicError):runtime.newer_runtime('2026.8.19',lambda stage:None,cancel)
    assert cancelled and not (tmp_path/'runtime').exists()
