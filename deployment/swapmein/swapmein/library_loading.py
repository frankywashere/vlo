"""Bounded read-only Library fan-out; preserve order and fail explicitly."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import threading
import time

from .accounts import index_path
from .errors import PublicError
from .security import ID_PATTERN

MAX_PARALLEL = 4


def parallel(values, build, *, deadline=None, maximum=MAX_PARALLEL):
    values = list(values)
    if not values:
        return []
    def remaining():
        seconds = None if deadline is None else deadline - time.monotonic()
        if seconds is not None and seconds <= 0:
            raise PublicError("Your Library took too long to respond. Tap Refresh to try again.",
                              503, code="library_read_timeout")
        return seconds
    remaining()
    pool = ThreadPoolExecutor(max_workers=min(maximum, len(values)))
    pending, following, results, completed = {}, 0, [None] * len(values), False
    try:
        for _ in range(min(maximum, len(values))):
            pending[pool.submit(build, values[following])] = following
            following += 1
        while pending:
            done, _ = wait(pending, timeout=remaining(), return_when=FIRST_COMPLETED)
            if not done:
                raise PublicError("Your Library took too long to respond. Tap Refresh to try again.",
                                  503, code="library_read_timeout")
            for future in done:
                results[pending.pop(future)] = future.result()
            remaining()
            for _ in done:
                if following < len(values):
                    pending[pool.submit(build, values[following])] = following
                    following += 1
        completed = True
        return results
    finally:
        # A failure must not leave all 50 records queued. Running read-only
        # calls finish under their own I/O deadlines; no more are submitted.
        pool.shutdown(wait=completed, cancel_futures=True)


def reader(records):
    # Real GCS records provide a list-specific deadline. Small in-memory stores
    # keep the same interface without needing network timeout configuration.
    return getattr(records, "read_library", records.read)


def load_entries(records, account_id, job_ids, *, deadline=None):
    read = reader(records)
    lock, count = threading.Lock(), 0

    def counted(path):
        nonlocal count
        with lock:
            count += 1
        return read(path)[0]

    def load(job_id):
        item = counted(index_path(account_id, job_id))
        if not item or item.get("hidden"):
            return None
        return job_id, item, counted(f"jobs/{job_id}.json")

    # The account's private index remains the authority, including its ordering
    # and 50-entry bound. Corrupt duplicate IDs cannot create duplicate cards or
    # repeated work. A single failed required read aborts; it never means empty.
    valid = list(dict.fromkeys(item for item in job_ids[:50]
                              if isinstance(item, str) and ID_PATTERN.fullmatch(item)))
    return [entry for entry in parallel(valid, load, deadline=deadline) if entry is not None], count
