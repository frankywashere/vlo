import importlib.util
from pathlib import Path
import pytest


@pytest.fixture
def configure(monkeypatch):
    directory = Path(__file__).parents[1]
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location('email_auth_configuration', directory/'configure_email_auth.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_enable_is_narrow_idempotent_and_preserves_other_auth_settings(configure, monkeypatch):
    before = {'authorizedDomains': ['recastfx.com'], 'emailPrivacyConfig': {'enableImprovedEmailPrivacy': True},
              'signIn': {'allowDuplicateEmails': False}}
    after = {**before, 'signIn': {**before['signIn'], 'email': {'enabled': True, 'passwordRequired': True}}}
    calls = []
    states = iter([before, after])
    monkeypatch.setattr(configure.deploy, 'google_get', lambda url: next(states))
    monkeypatch.setattr(configure.deploy, 'google_patch', lambda url, body: calls.append((url, body)))
    result = configure.configure(enable=True)
    assert calls == [(configure.CONFIG_URL+'?updateMask=signIn.email.enabled,signIn.email.passwordRequired',
                      {'signIn': {'email': {'enabled': True, 'passwordRequired': True}}})]
    assert result['email_enabled'] and result['password_required'] and result['changed']
    monkeypatch.setattr(configure.deploy, 'google_get', lambda url: after)
    configure.configure(enable=True)
    assert len(calls) == 1


def test_status_is_read_only(configure, monkeypatch):
    monkeypatch.setattr(configure.deploy, 'google_get', lambda url: {})
    monkeypatch.setattr(configure.deploy, 'google_patch', lambda *args: pytest.fail('No write without explicit enable'))
    assert configure.configure()['changed'] is False


def test_unrelated_configuration_drift_is_rejected(configure, monkeypatch):
    states = iter([{'authorizedDomains': ['recastfx.com']},
                   {'authorizedDomains': ['other.example'], 'signIn': {'email': {'enabled': True, 'passwordRequired': True}}}])
    monkeypatch.setattr(configure.deploy, 'google_get', lambda url: next(states))
    monkeypatch.setattr(configure.deploy, 'google_patch', lambda *args: None)
    with pytest.raises(configure.deploy.SetupError, match='authorizedDomains'):
        configure.configure(enable=True)
