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
        {"name": "STRIPE_SECRET_KEY", "valueFrom": {"secretKeyRef": {"name": "stripe-key", "key": "latest"}}},
    ]}]}}}}
    values, bindings = launch.runtime_settings(current)
    assert values["COMMERCE_OWNER_EMAILS"] == owners
    assert values["STRIPE_MODE"] == "live"
    assert "STRIPE_SECRET_KEY=stripe-key:latest" in bindings
    assert "RECASTFX_JOB_KEY=recastfx-job-key:latest" in bindings
