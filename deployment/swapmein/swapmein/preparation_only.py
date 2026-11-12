"""Fail-closed rendering boundary for the isolated guide inspection site."""
import os

from .errors import PublicError


def preparation_only():
    return os.getenv('PREPARATION_ONLY', 'false').lower() == 'true'


def isolated_test_site(settings=None):
    if settings is None:
        from .config import Settings
        settings = Settings.from_env()
    return (getattr(settings, 'test_site', False)
            and settings.app_bucket == settings.project + '-long-test'
            and settings.worker_bucket == settings.app_bucket)


def require_rendering(settings=None):
    if preparation_only() or getattr(settings, 'preparation_only', False):
        raise PublicError('This test site prepares guides only. Video generation is disabled.',
                          409, code='generation_disabled')
