"""Old site leases cannot overlap or overwrite the new preparation pool."""
import pytest
from swapmein.storage import (reserve_preparation, preparation_leases, release_preparation,
    set_preparation_lease, claim_queued_slot, check_preparation_capacity)
from swapmein.errors import PublicError


def test_guard_is_visible_to_old_code_and_not_counted_as_a_real_job():
    current = reserve_preparation({}, 'a', 1000, maximum=8, legacy_guard=True)
    current = reserve_preparation(current, 'b', 1001, maximum=8, legacy_guard=True)
    assert set(preparation_leases(current)) == {'a', 'b'}
    assert current['active'] == {'job_id': 'capacity-pool', 'until': 2801, 'blocked': False}
    # The original worker only reads active and rejects any existing job.
    assert current['active']['job_id'] not in {'a', 'b'}
    current = release_preparation(current, 'a')
    assert current['active']['job_id'] == 'capacity-pool'
    current = release_preparation(current, 'b')
    assert not current.get('active') and not current.get('pool_version')


def test_guard_waits_for_real_legacy_work_even_when_capacity_is_available():
    old = {'active': {'job_id': 'old-job', 'until': 2000}}
    for operation in (lambda: reserve_preparation(old, 'new', 1000, maximum=8, legacy_guard=True),
                      lambda: claim_queued_slot(old, 'new', 1000, maximum=8, legacy_guard=True)):
        with pytest.raises(PublicError) as caught:
            operation()
        assert caught.value.code == 'preparation_busy'
    assert old['active']['job_id'] == 'old-job'


def test_guard_survives_renewal_blocking_and_lowering_limit_to_one():
    current = claim_queued_slot({}, 'a', 1000, maximum=1, legacy_guard=True)
    current = set_preparation_lease(current, 'a', {'job_id': 'a', 'until': 3000, 'blocked': True})
    assert current['active']['until'] == 3000 and current['active']['blocked']
    assert set(preparation_leases(current)) == {'a'}
    with pytest.raises(PublicError) as caught:
        check_preparation_capacity(current, 1001, maximum=8, legacy_guard=True)
    assert caught.value.code == 'preparation_recovery'


def test_old_queued_claim_with_stale_pool_marker_is_still_a_real_lease():
    current = {'pool_version': 1, 'active_jobs': {}, 'active': {'job_id': 'old', 'until': 2000}}
    assert set(preparation_leases(current)) == {'old'}
    with pytest.raises(PublicError):
        claim_queued_slot(current, 'new', 1000, maximum=8, legacy_guard=True)
