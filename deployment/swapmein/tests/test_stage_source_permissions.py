"""Private build contexts still produce code readable by the non-root image user."""
import os
import stat

import pytest

import deploy


def stage_with_umask(destination, mask):
    previous = os.umask(mask)
    try:
        return deploy.stage_source(destination)
    finally:
        os.umask(previous)


@pytest.mark.parametrize('mask',[0o077,0o027])
def test_restrictive_release_umask_keeps_context_private_and_inner_code_readable(tmp_path,mask):
    destination=tmp_path/'private-context'
    destination.mkdir(mode=0o700)
    stage_with_umask(destination,mask)
    assert stat.S_IMODE(destination.stat().st_mode)==0o700
    for path in destination.rglob('*'):
        assert stat.S_IMODE(path.stat().st_mode)==(0o755 if path.is_dir() else 0o644),path
    package=destination/'swapmein/vendor/meta_sam_parser'
    assert (package/'LICENSE').is_file() and (package/'dist/index.js').is_file()


def test_permission_hardening_preserves_source_hash_and_all_staged_bytes(tmp_path):
    first,second=tmp_path/'normal',tmp_path/'private'
    first.mkdir();second.mkdir(mode=0o700)
    first_hash=stage_with_umask(first,0o022)
    second_hash=stage_with_umask(second,0o077)
    assert first_hash==second_hash
    a={str(p.relative_to(first)):p.read_bytes() for p in first.rglob('*') if p.is_file()}
    b={str(p.relative_to(second)):p.read_bytes() for p in second.rglob('*') if p.is_file()}
    assert a==b
    assert stat.S_IMODE(second.stat().st_mode)==0o700
