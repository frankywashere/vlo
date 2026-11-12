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


def quality_context_key(job, input_sha):
    """Invalidate CPU approval semantics without invalidating paid receipts."""
    from .mask_gap_tolerance import POLICY_VERSION as GAP_POLICY
    from .mask_quality import POLICY_VERSION as COMPONENT_POLICY
    from .mask_quality import CONTINUITY_CLEANUP_ENABLED
    from .mask_retention import POLICY_VERSION as RETENTION_POLICY
    value = {'identity_context_key': context_key(job, input_sha),
        'component_policy_version': COMPONENT_POLICY, 'gap_policy_version': GAP_POLICY,
        'continuity_cleanup_enabled': CONTINUITY_CLEANUP_ENABLED,
        'retention_policy_version': RETENTION_POLICY,
        'completed_component_review': bool((job.get('targeted_mask_review') or {}).get('decisions')),
        'identity_provenance': job.get('mask_identity_provenance'),
        'identity_matching_completed': job.get('identity_matching_completed'),
        'matching_fallback_used': job.get('matching_fallback_used'),
        # For new records provenance is written only after source validation.
        # The later UI-ready flag must not invalidate that same approval.
        'tracking_reviewed': job.get('tracking_reviewed')
            if job.get('mask_identity_provenance') is None else None,
        'tracking_resolution_pending': job.get('tracking_resolution_pending'),
        'tracking_review_error': job.get('tracking_review_error'),
        'recover_missing_masks': job.get('recover_missing_masks'),
        'mask_recovery_completed': job.get('mask_recovery_completed')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _binding(candidate):
    # Candidate IDs additionally contain the review revision. A saved receipt
    # may be rebound to a later, otherwise identical explicit source review.
    return tuple(candidate.get(field) for field in
        ('target_id', 'label', 'segment_id', 'frame', 'component_sha256'))


def _project_gap_report(report, assessment):
    """Retain omitted-tint guidance; only subtract forgiven approval ranges."""
    blocking = []
    for item in report.get('suppressed_ranges', []):
        intervals = [(item['start_frame'], item['end_frame'])]
        for allowed in assessment.get('tolerated_ranges', []):
            if (allowed['target_id'], allowed['label']) != (item['target_id'], item['label']):
                continue
            following = []
            for start, end in intervals:
                left, right = max(start, allowed['start_frame']), min(end, allowed['end_frame'])
                if left >= right:
                    following.append((start, end))
                else:
                    if start < left:
                        following.append((start, left))
                    if right < end:
                        following.append((right, end))
            intervals = following
        blocking.extend({**item, 'start_frame': start, 'end_frame': end} for start, end in intervals)
    report['gap_tolerance'] = assessment
    report['blocking_suppressed_ranges'] = blocking
    report['review_required'] = bool(blocking)


def recheck_masks(pipeline, job, original, checked, report, root, remaining):
    """A pending/failed request is never replayed; retain it for focused review."""
    revision = job.get('tracking_applied_revision', 0)
    # The current server plan and the immutable archive, never caller-supplied
    # geometry, determine which components may be compared or restored.
    candidates = plan_rechecks(original, checked, job['tracking_metadata'], job['tracking_plan'],
        job['tracking_choices'], job['tracking_assignments'], review_revision=revision)
    planned_candidates = candidates
    key = context_key(job, report['input_sha256'])
    from .mask_gap_tolerance import assess_mask_gaps
    assessment = assess_mask_gaps(job, original, checked, candidates, review_revision=revision)
    forgiven_ids = set(assessment['forgiven_candidate_ids'])
    forgiven_bindings = {_binding(candidate) for candidate in candidates
                         if candidate['candidate_id'] in forgiven_ids}
    candidates = [candidate for candidate in candidates if candidate['candidate_id'] not in forgiven_ids]
    _project_gap_report(report, assessment)
    report['mask_context_key'] = quality_context_key(job, report['input_sha256'])
    supplements = deepcopy(job.get('mask_supplemental_anchors') or [])
    if job.get('mask_supplemental_context_key') != key:
        supplements = []
    # Explicit source taps remain authoritative. Automatic confirmations are
    # unnecessary for omissions this policy leaves untinted, not restored.
    supplements = [item for item in supplements if item.get('confirmation') == 'explicit_source_review'
                   or _binding(item) not in forgiven_bindings]
    for item in supplements:
        item['review_revision'] = revision
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
    current_state = state if state.get('key') == key else {}
    evidence = {item['candidate_id']: item for item in current_state.get('evidence', [])}
    known = {item['candidate_id'] for item in supplements}
    completed = job.get('targeted_mask_review') or {}
    reviewed_ids = {item['candidate_id'] for item in completed.get('decisions', [])}
    unresolved = []
    for candidate in current_state.get('candidates', []):
        if _binding(candidate) in forgiven_bindings:
            continue
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
        assessment = assess_mask_gaps(job, original, checked, planned_candidates, review_revision=revision)
        _project_gap_report(report, assessment)
        report['mask_context_key'] = quality_context_key(job, report['input_sha256'])
    interrupted = current_state.get('status') == 'started'
    report['identity_rechecks'] = {'context_key': key,
        'status': current_state.get('status', 'not_required'),
        'requests': None if interrupted else current_state.get('requests', 0),
        'request_count_known': not interrupted, 'request_limit': len(current_state.get('candidates', [])),
        'usage': current_state.get('usage') if not interrupted else {'cost_status': 'unknown_after_interruption'},
        'confirmed': len(supplements), 'unresolved': len(unresolved),
        'skipped_tolerated_candidates': len(forgiven_ids)}
    # Restored components still require fresh approval; tolerated omissions
    # remain untinted and do not create a new paid-generation authorization.
    report['review_required'] = bool(report.get('review_required') or supplements or unresolved)
    job = {**job, 'mask_supplemental_anchors': supplements, 'mask_supplemental_context_key': key}
    emit('mask_identity_rechecks_completed', job_id=job['id'], status=report['identity_rechecks']['status'],
         request_count=report['identity_rechecks']['requests'], request_count_known=not interrupted,
         confirmed=len(supplements), unresolved=len(unresolved))
    return job, checked, report, unresolved
