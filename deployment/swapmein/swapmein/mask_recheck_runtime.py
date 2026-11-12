"""Durable, once-per-input identity rechecks before a guide is rebuilt."""
from copy import deepcopy
import hashlib
import json
import uuid

from .diagnostics import emit
from .errors import PublicError
from .mask_quality import sanitize_reviewed_masks
from .mask_recheck_plan import plan_rechecks


def context_key(job, input_sha):
    from .mask_identity_rechecks import POLICY_VERSION
    value = {'policy_version': POLICY_VERSION, 'input_sha256': input_sha, 'source_uri': job.get('normalized_uri'),
             'tracking_metadata': job.get('tracking_metadata'),
             'plan': job.get('tracking_plan'), 'choices': job.get('tracking_choices'),
             'assignments': job.get('tracking_assignments')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def recheck_masks(pipeline, job, original, checked, report, root, remaining):
    """A pending/failed request is never replayed; retain it for focused review."""
    revision = job.get('tracking_applied_revision', 0)
    # The current server plan and the immutable archive, never caller-supplied
    # geometry, determine which components may be compared or restored.
    candidates = plan_rechecks(original, checked, job['tracking_metadata'], job['tracking_plan'],
        job['tracking_choices'], job['tracking_assignments'], review_revision=revision)
    key = context_key(job, report['input_sha256'])
    report['mask_context_key'] = key
    state = job.get('mask_identity_recheck_state') or {}
    if candidates and state.get('key') != key:
        token = uuid.uuid4().hex
        state = {'key': key, 'claim_token': token, 'status': 'started',
            'candidates': candidates, 'evidence': [], 'requests': 0}
        def claim(current):
            if (current.get('tracking_applied_revision', 0) != revision
                    or context_key(current, report['input_sha256']) != key):
                raise PublicError('The mask review changed. Refresh this clip.', 409, code='tracking_review_invalid')
            old = current.get('mask_identity_recheck_state') or {}
            if old.get('key') == key:
                return current
            # Availability can change without a source/plan revision. Decide
            # from the atomic current record, never a stale worker snapshot.
            blocked = current.get('identity_matching_recheck_blocked') is True or (
                current.get('identity_matching_completed') is False and current.get('identity_matching_error')
                in {'cloudflare_matching_unavailable', 'cloudflare_matching_budget'})
            following = {**state, **({'status': 'blocked_unavailable',
                'reason': current.get('identity_matching_error') or 'matching_service_unavailable',
                'usage': {'input_tokens': 0, 'estimated_cost_usd': 0,
                          'cost_status': 'not_submitted_service_unavailable'}} if blocked else {})}
            return {**current, 'mask_identity_recheck_state': following}
        job = pipeline.records.update(f"jobs/{job['id']}.json", claim)
        state = job['mask_identity_recheck_state']
        if state.get('claim_token') == token and state.get('status') == 'started':
            try:
                from .cloudflare_credentials import server_cloudflare_credentials
                from .cloudflare_budget import MatchingBudget
                from .mask_identity_rechecks import recheck_mask_identities
                credentials = server_cloudflare_credentials()
                source_uri = job.get('normalized_uri')
                if not source_uri:
                    raise PublicError('Original footage is needed for identity rechecking.', code='tracking_invalid')
                source = root / 'identity-original.mp4'
                pipeline.cloud.download(source_uri, source, 500 * 1024 * 1024)
                result = recheck_mask_identities(source, original, job['tracking_metadata'], candidates,
                    credentials=credentials, budget=MatchingBudget(pipeline.records, job['id']),
                    workdir=root, review_revision=revision, input_sha256=report['input_sha256'],
                    deadline_seconds=min(90, remaining()))
                state = {**state, **result, 'status': 'completed'}
            except PublicError as error:
                state = {**state, 'status': 'stopped', 'reason': error.code}
            except (OSError, TimeoutError):
                state = {**state, 'status': 'stopped', 'reason': 'source_unavailable'}
            # Persist the claim even on a service failure or ambiguous result.
            # A process crash before this write leaves 'started', never a rerun.
            def finish(current):
                if ((current.get('mask_identity_recheck_state') or {}).get('key') != key
                        or (current.get('mask_identity_recheck_state') or {}).get('claim_token') != token
                        or context_key(current, report['input_sha256']) != key):
                    raise PublicError('The mask identity review changed.', 409, code='tracking_review_invalid')
                return {**current, 'mask_identity_recheck_state': state}
            job = pipeline.records.update(f"jobs/{job['id']}.json", finish)
    if state.get('key') != key:
        return job, checked, report, None
    evidence = {item['candidate_id']: item for item in state.get('evidence', [])}
    supplements = deepcopy(job.get('mask_supplemental_anchors') or [])
    # Rebinding a completed parent proof to the new review revision is safe only
    # under the exact same archive, source URI, base plan/choices/assignments key.
    if job.get('mask_supplemental_context_key') != key:
        supplements = []
    for item in supplements:
        item['review_revision'] = revision
    known = {item['candidate_id'] for item in supplements}
    completed = job.get('targeted_mask_review') or {}
    reviewed_ids = {item['candidate_id'] for item in completed.get('decisions', [])}
    unresolved = []
    for candidate in state.get('candidates', []):
        if candidate['candidate_id'] in reviewed_ids:
            continue
        decision = evidence.get(candidate['candidate_id'], {})
        if decision.get('outcome') == 'confirmed':
            if candidate['candidate_id'] not in known:
                supplements.append({**candidate, 'review_revision': revision})
                known.add(candidate['candidate_id'])
        elif decision.get('outcome') != 'different':
            unresolved.append({**candidate, 'review_revision': revision})
    if supplements:
        output = root / 'identity-rechecked.zip'
        report = sanitize_reviewed_masks(original, job['tracking_metadata'], job['tracking_plan'],
            job['tracking_choices'], job['tracking_assignments'], output, review_revision=revision,
            supplemental_anchors=supplements)
        checked = output
        report['mask_context_key'] = key
    interrupted = state.get('status') == 'started'
    report['identity_rechecks'] = {'context_key': key, 'status': state.get('status'),
        'requests': None if interrupted else state.get('requests', 0),
        'request_count_known': not interrupted, 'request_limit': len(state.get('candidates', [])),
        'usage': state.get('usage') if not interrupted else {'cost_status': 'unknown_after_interruption'},
        'confirmed': len(supplements), 'unresolved': len(unresolved)}
    # Every changed checked guide gets a fresh approval; neither model receives
    # a paid generation merely because a new identity component was accepted.
    report['review_required'] = bool(report.get('review_required') or supplements or unresolved)
    job = {**job, 'mask_supplemental_anchors': supplements, 'mask_supplemental_context_key': key}
    emit('mask_identity_rechecks_completed', job_id=job['id'], status=state.get('status'),
         request_count=report['identity_rechecks']['requests'], request_count_known=not interrupted,
         confirmed=len(supplements), unresolved=len(unresolved))
    return job, checked, report, unresolved
