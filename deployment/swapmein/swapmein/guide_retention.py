"""Private, exact submitted guides retained for one day after completion."""
import hashlib
import math
from pathlib import Path
import re
import tempfile
import time
import uuid

from google.api_core.exceptions import NotFound, PreconditionFailed

from .diagnostics import emit
from .errors import PublicError

SECONDS = 86400
PREFIX = 'retained-guides/'
EXPIRY_PREFIX = 'guide-expiry/'


def _identity(job):
    identifier, account = job.get('id'), job.get('account_id')
    if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
        return None
    if not isinstance(account, str) or not re.fullmatch('[a-f0-9]{64}', account):
        return None
    return identifier, account


def _expiry_path(job_id, expiry):
    return f'{EXPIRY_PREFIX}{math.ceil(expiry):010d}-{job_id}.json'


def retain_guide(settings, records, cloud, job):
    identity = _identity(job)
    terminal_guide = job.get('status') == 'completed' or (job.get('preparation_only') and job.get('status') == 'ready')
    if not identity or not terminal_guide or not job.get('guide_uri'):
        return job
    identifier, account = identity
    expected = f'gs://{settings.app_bucket}/{PREFIX}{account}/{identifier}/guide.mp4'
    retained = job.get('retained_guide')
    if retained:
        if retained.get('uri') != expected:
            raise PublicError('The saved guide identity is invalid.', 409)
    else:
        # Older completed jobs have already lost their scratch media. Never
        # recreate a guide, call SAM or extend retention for those old runs.
        if job.get('temporary_media_cleanup_started_at'):
            return job
        guide = job['guide_uri']
        silent = job.get('silent_submission') or {}
        if (job.get('setup') or {}).get('submit_without_audio'):
            if job.get('preparation_only') and (silent.get('guide_uri') != guide or not silent.get('uri')):
                # Production strips audio immediately before submission. The
                # guide-only site must expose that same input without rendering.
                from .media import strip_audio
                with tempfile.TemporaryDirectory() as directory:
                    source, output = Path(directory) / 'guide.mp4', Path(directory) / 'silent.mp4'
                    cloud.download(guide, source, 100 * 1024 * 1024)
                    strip_audio(source, output)
                    uri = cloud.upload(settings.app_bucket,
                        f'outputs/{identifier}/silent-preview-{uuid.uuid4().hex}.mp4', output, 'video/mp4')
                silent = {'guide_uri': guide, 'uri': uri}
                def bind(current):
                    if not current or current.get('guide_uri') != guide or current.get('status') != 'ready':
                        raise PublicError('The prepared guide changed before audio removal.', 409)
                    return {**current, 'silent_submission': silent}
                job = records.update(f'jobs/{identifier}.json', bind)
            if silent.get('guide_uri') != guide or not silent.get('uri'):
                raise PublicError('The exact silent submission is unavailable for guide retention.', 503)
            guide = silent['uri']
        if not guide.startswith(f'gs://{settings.app_bucket}/outputs/{identifier}/') or not guide.endswith('.mp4') or '..' in guide:
            raise PublicError('The submitted guide is outside this job.', 409)
        created = time.time()
        completed = job.get('completed_at') or created
        if type(completed) not in (int, float) or not math.isfinite(completed):
            raise PublicError('The guide retention timestamp is invalid.', 409)
        expiry = completed + SECONDS
        if expiry <= created:
            return job
        with tempfile.TemporaryDirectory() as directory:
            original, saved = Path(directory) / 'source.mp4', Path(directory) / 'saved.mp4'
            cloud.download(guide, original, 100 * 1024 * 1024)
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            try:
                cloud.copy(guide, settings.app_bucket, f'{PREFIX}{account}/{identifier}/guide.mp4')
            except PreconditionFailed:
                pass  # A copy may have succeeded before its record was saved.
            cloud.download(expected, saved, 100 * 1024 * 1024)
            if hashlib.sha256(saved.read_bytes()).hexdigest() != digest:
                raise PublicError('The saved guide differs from the submitted video.', 503)
        blob = cloud.blob(settings.app_bucket, f'{PREFIX}{account}/{identifier}/guide.mp4')
        blob.reload(timeout=20, retry=None)
        sources = []
        for uri in dict.fromkeys([job['guide_uri'], guide]):
            key = uri[len(f'gs://{settings.app_bucket}/'):]
            source_blob = cloud.blob(settings.app_bucket, key)
            source_blob.reload(timeout=20, retry=None)
            sources.append({'uri': uri, 'generation': int(source_blob.generation)})
        retained = dict(uri=expected, source_uri=guide, sha256=digest,
            bytes=blob.size, generation=int(blob.generation), saved_at=created, expires_at=expiry,
            expiring_sources=sources)
        def save(current):
            if not current or current.get('status') != job['status'] or current.get('guide_uri') != job['guide_uri']:
                raise PublicError('The completed guide changed before retention.', 409)
            previous = current.get('retained_guide')
            if previous and previous != retained:
                if previous.get('sha256') != digest or previous.get('uri') != expected:
                    raise PublicError('A different guide is already retained.', 409)
                return current
            return {**current, 'retained_guide': retained}
        job = records.update(f'jobs/{identifier}.json', save)
        retained = job['retained_guide']
    marker = {'job_id': identifier, 'account_id': account, **retained}
    def reserve(current):
        if current and current != marker:
            raise PublicError('The guide expiry reservation changed.', 409)
        return current or marker
    records.update(_expiry_path(identifier, retained['expires_at']), reserve)
    emit('submitted_guide_retained', job_id=identifier, sha256=retained['sha256'],
         bytes=retained['bytes'], expires_at=retained['expires_at'])
    return job


def retained_guide_view(job, cloud, *, now=None):
    now = time.time() if now is None else now
    retained = job.get('retained_guide') or {}
    identity = _identity(job)
    if not identity or retained.get('expires_at', 0) <= now or retained.get('deleted_at'):
        return {}
    identifier, account = identity
    bucket = cloud.settings.app_bucket
    if retained.get('uri') != f'gs://{bucket}/{PREFIX}{account}/{identifier}/guide.mp4':
        return {}
    return {'guide_url': cloud.signed_url(retained['uri'],
                seconds=max(1, min(3600, math.floor(retained['expires_at'] - now)))),
            'guide_expires_at': retained['expires_at'], 'guide_sha256': retained['sha256']}


def delete_expired_guides(settings, records, cloud, *, now=None, limit=25):
    now = time.time() if now is None else now
    counts = {'deleted': 0, 'errors': 0}
    deadline = time.monotonic() + 15
    for path in records.list_paths(EXPIRY_PREFIX, maximum=min(25, limit)):
        if time.monotonic() > deadline:
            break
        match = re.fullmatch(r'guide-expiry/([0-9]{10})-([a-f0-9]{32})\.json', path)
        if not match:
            continue
        if int(match[1]) > now:
            break  # GCS lists expiry timestamps in lexical/time order.
        try:
            marker, _ = records.read(path)
            if not marker:
                records.delete(path)
                continue
            identifier, account = marker.get('job_id'), marker.get('account_id')
            if (_identity({'id': identifier, 'account_id': account}) is None or identifier != match[2]
                    or _expiry_path(identifier, marker['expires_at']) != path):
                raise ValueError('Invalid guide expiry identity')
            key = f'{PREFIX}{account}/{identifier}/guide.mp4'
            if marker['uri'] != f'gs://{settings.app_bucket}/{key}' or marker['expires_at'] > now:
                raise ValueError('Invalid guide expiry path')
            objects = [(key, marker['generation'])]
            for source in marker.get('expiring_sources', []):
                prefix = f'gs://{settings.app_bucket}/outputs/{identifier}/'
                uri = source['uri']
                if not uri.startswith(prefix) or not uri.endswith('.mp4') or '..' in uri:
                    raise ValueError('Invalid scratch guide expiry path')
                objects.append((uri[len(f'gs://{settings.app_bucket}/'):], source['generation']))
            for key, generation in objects:
                try:
                    cloud.blob(settings.app_bucket, key).delete(
                        if_generation_match=generation, timeout=10, retry=None)
                except NotFound:
                    pass  # Lifecycle cleanup or a prior interrupted sweep won.
            records.delete(path)
            counts['deleted'] += 1
        except Exception as error:
            counts['errors'] += 1
            emit('guide_expiry_delayed', error=error)
    emit('guide_expiry_sweep_finished', **counts)
    return counts
