"""Deployment documents are pure, pinned and cannot provision extra resources."""
import copy
import json

import pytest

from google_h3_config import build_google_config
from swapmein.config import Settings
from swapmein.google_lifecycle import ENV, configuration


def manifest():
    return {"release_sha256": "a" * 64, "config_sha256": "b" * 64, "startup_sha256": "c" * 64,
            "api_url": "https://recastfx-api.example.run.app", "worker_id": "google-character-swap-1"}


@pytest.mark.parametrize("maximum", [1800, 14400])
def test_config_uses_only_retained_belgium_disk_and_one_bounded_google_gpu(monkeypatch, maximum):
    source = manifest()
    unchanged = copy.deepcopy(source)
    result = build_google_config(source, maximum)
    assert source == unchanged
    assert result == build_google_config(source, maximum)
    assert (result["project"], result["zone"], result["instance"], result["disk"]) == (
        "charswap-franky-20261013", "europe-west1-b", "recastfx-h3-google-worker", "recastfx-h3-be-models-20261101")
    body = result["instance_body"]
    assert body["machineType"].endswith("/zones/europe-west1-b/machineTypes/g4-standard-48")
    assert len(body["disks"]) == 1
    disk = body["disks"][0]
    assert disk["boot"] is True and disk["autoDelete"] is False
    assert disk["source"].endswith("/zones/europe-west1-b/disks/recastfx-h3-be-models-20261101")
    assert "initializeParams" not in disk and "guestAccelerators" not in body
    assert body["scheduling"] == {"onHostMaintenance": "TERMINATE", "automaticRestart": False,
        "provisioningModel": "STANDARD", "maxRunDuration": {"seconds": str(maximum)},
        "instanceTerminationAction": "STOP"}
    monkeypatch.setenv(ENV, json.dumps(result))
    assert configuration(Settings()) == result


def test_metadata_pins_artifacts_api_and_secret_resource_without_secret_contents():
    source = manifest()
    source["private_token"] = "must-not-appear-in-output"
    result = build_google_config(source)
    items = {item["key"]: item["value"] for item in result["instance_body"]["metadata"]["items"]}
    prefix = "gs://charswap-franky-20261013-proof/self-hosted-worker/releases/" + "a" * 64 + "/"
    assert items["startup-script-url"] == prefix + "startup.sh"
    assert items["recastfx-worker-release"] == prefix + "worker.tar.gz"
    assert items["recastfx-worker-config"] == prefix + "worker.json"
    assert items["recastfx-worker-release-sha256"] == "a" * 64
    assert items["recastfx-worker-config-sha256"] == "b" * 64
    assert items["recastfx-worker-startup-sha256"] == "c" * 64
    assert items["recastfx-api-url"] == source["api_url"]
    assert items["recastfx-worker-token-secret"] == "projects/charswap-franky-20261013/secrets/recastfx-google-h3-worker-token/versions/1"
    assert items["enable-oslogin"] == "true" and items["block-project-ssh-keys"] == "true"
    assert "must-not-appear-in-output" not in json.dumps(result)


def test_service_account_and_network_remain_fixed_without_reserved_external_ip():
    body = build_google_config(manifest())["instance_body"]
    assert body["labels"] == {"recastfx-role": "characterswap"}
    assert body["serviceAccounts"] == [{
        "email": "recastfx-google-h3-worker@charswap-franky-20261013.iam.gserviceaccount.com",
        "scopes": ["https://www.googleapis.com/auth/cloud-platform"]}]
    assert len(body["networkInterfaces"]) == 1
    network = body["networkInterfaces"][0]
    assert network["network"] == "projects/charswap-franky-20261013/global/networks/default"
    assert network["subnetwork"] == "projects/charswap-franky-20261013/regions/europe-west1/subnetworks/default"
    assert network["nicType"] == "GVNIC"
    assert network["accessConfigs"] == [{"name": "External NAT", "type": "ONE_TO_ONE_NAT", "networkTier": "PREMIUM"}]


@pytest.mark.parametrize("maximum", [True, 0, 1799, 14401, 1800.0, "1800", None])
def test_rejects_invalid_uptime_instead_of_relaxing_the_cost_cap(maximum):
    with pytest.raises(ValueError):
        build_google_config(manifest(), maximum)


@pytest.mark.parametrize("pin", [None, [], "", "a" * 63, "A" * 64, "a" * 65, "../mutable"])
@pytest.mark.parametrize("field", ["release_sha256", "config_sha256", "startup_sha256"])
def test_all_artifact_pins_must_be_canonical_sha256(field, pin):
    source = manifest()
    source[field] = pin
    with pytest.raises(ValueError):
        build_google_config(source)


@pytest.mark.parametrize("url", [None, "http://example.run.app", "https://user:password@example.run.app",
    "https://example.run.app/path", "https://example.run.app?token=secret", "https://example.run.app#fragment",
    "https://example.run.app:8080", "https://example.run.app:bad", " https://example.run.app",
    "https://example.run.app/\n"])
def test_api_url_rejects_credentials_non_https_and_unexpected_paths(url):
    source = manifest()
    source["api_url"] = url
    with pytest.raises(ValueError):
        build_google_config(source)


def test_mutating_a_built_document_does_not_change_later_builds():
    source = manifest()
    result = build_google_config(source)
    result["instance_body"]["disks"][0]["autoDelete"] = True
    assert build_google_config(source)["instance_body"]["disks"][0]["autoDelete"] is False
