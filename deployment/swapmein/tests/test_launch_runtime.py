"""A UI deployment must preserve the existing owner account configuration."""
import importlib
from pathlib import Path


def test_deployment_preserves_all_configured_owners_and_secret_bindings(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    launch = importlib.import_module("deploy_launch")
    owners = "owner@example.com,second-owner@example.com"
    current = {"spec": {"template": {"spec": {"containers": [{"env": [
        {"name": "COMMERCE_OWNER_EMAILS", "value": owners},
        {"name": "STRIPE_MODE", "value": "live"},
        {"name": "CLOUDFLARE_SECRET_NAME", "value": "projects/example/secrets/recastfx-cloudflare-key"},
        {"name": "STRIPE_SECRET_KEY", "valueFrom": {"secretKeyRef": {"name": "stripe-key", "key": "latest"}}},
    ]}]}}}}
    values, bindings = launch.runtime_settings(current)
    assert values["COMMERCE_OWNER_EMAILS"] == owners
    assert values["STRIPE_MODE"] == "live"
    assert values["CLOUDFLARE_SECRET_NAME"] == "projects/example/secrets/recastfx-cloudflare-key"
    assert "STRIPE_SECRET_KEY=stripe-key:latest" in bindings
    assert "RECASTFX_JOB_KEY=recastfx-job-key:latest" in bindings


def test_deployment_provisions_scoped_cloudflare_storage_without_copying_a_token(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    launch = importlib.import_module("deploy_launch")
    calls = []
    monkeypatch.setattr(launch.deploy, "gcloud", lambda *args, **kwargs: calls.append((args, kwargs)) or None)
    launch.ensure_private_provider_secret()
    cloudflare = [(args, kwargs) for args, kwargs in calls if "recastfx-cloudflare-key" in args]
    assert any(args[:2] == ("secrets", "create") for args, _ in cloudflare)
    assert {argument for args, _ in cloudflare for argument in args if argument.startswith("--role=")} == {
        "--role=roles/secretmanager.secretAccessor", "--role=roles/secretmanager.secretVersionAdder"}
    assert not any("versions" in args for args, _ in calls)
