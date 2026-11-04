"""Build public, pinned GCE lifecycle configuration without cloud or file access."""
from collections.abc import Mapping
import re
from urllib.parse import urlsplit


PROJECT = "charswap-franky-20261013"
ZONE = "europe-west1-b"
REGION = "europe-west1"
INSTANCE = "recastfx-h3-google-worker"
DISK = "recastfx-h3-be-models-20261101"
BUCKET = PROJECT + "-proof"
WORKER_ACCOUNT = f"recastfx-google-h3-worker@{PROJECT}.iam.gserviceaccount.com"
TOKEN_SECRET = f"projects/{PROJECT}/secrets/recastfx-google-h3-worker-token/versions/1"


def build_google_config(manifest: Mapping[str, object], maximum_uptime_seconds: int = 14400) -> dict:
    """Return the one retained-disk worker's reproducible lifecycle document.

    The caller reads and verifies artifact bytes, publishes each content-addressed
    object without overwrite, and installs this JSON. This function only builds
    configuration; it neither publishes artifacts nor starts or modifies a VM.
    """
    if not isinstance(manifest, Mapping):
        raise ValueError("A worker artifact manifest is required")
    pins = {}
    for key in ("release_sha256", "config_sha256", "startup_sha256"):
        value = manifest.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Every worker artifact must have a lowercase SHA256 pin")
        pins[key] = value
    if type(maximum_uptime_seconds) is not int or not 1800 <= maximum_uptime_seconds <= 14400:
        raise ValueError("The worker uptime limit must be 1800 to 14400 whole seconds")
    api_url = manifest.get("api_url")
    if not isinstance(api_url, str) or api_url != api_url.strip():
        raise ValueError("A canonical HTTPS API base URL is required")
    try:
        parsed = urlsplit(api_url)
        valid_api = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                     and not parsed.password and parsed.port in (None, 443)
                     and parsed.path in ("", "/") and not parsed.query and not parsed.fragment
                     and not any(character.isspace() for character in api_url))
    except ValueError:
        valid_api = False
    if not valid_api:
        raise ValueError("A canonical HTTPS API base URL is required")
    prefix = f"gs://{BUCKET}/self-hosted-worker/releases/{pins['release_sha256']}/"
    base = f"projects/{PROJECT}/zones/{ZONE}"
    metadata = {
        "startup-script-url": prefix + "startup.sh",
        "recastfx-worker-release": prefix + "worker.tar.gz",
        "recastfx-worker-release-sha256": pins["release_sha256"],
        "recastfx-worker-config": prefix + "worker.json",
        "recastfx-worker-config-sha256": pins["config_sha256"],
        "recastfx-worker-startup-sha256": pins["startup_sha256"],
        "recastfx-api-url": api_url.rstrip("/"),
        "recastfx-worker-token-secret": TOKEN_SECRET,
        "enable-oslogin": "true",
        "block-project-ssh-keys": "true",
    }
    return {
        "project": PROJECT,
        "zone": ZONE,
        "instance": INSTANCE,
        "disk": DISK,
        "idle_seconds": 120,
        "boot_seconds": 900,
        "maximum_uptime_seconds": maximum_uptime_seconds,
        "instance_body": {
            "name": INSTANCE,
            "machineType": base + "/machineTypes/g4-standard-48",
            "labels": {"recastfx-role": "characterswap"},
            "disks": [{"source": base + "/disks/" + DISK, "boot": True,
                       "autoDelete": False, "mode": "READ_WRITE", "type": "PERSISTENT",
                       "interface": "NVME"}],
            "serviceAccounts": [{"email": WORKER_ACCOUNT,
                                 "scopes": ["https://www.googleapis.com/auth/cloud-platform"]}],
            "networkInterfaces": [{
                "network": f"projects/{PROJECT}/global/networks/default",
                "subnetwork": f"projects/{PROJECT}/regions/{REGION}/subnetworks/default",
                "nicType": "GVNIC",
                "accessConfigs": [{"name": "External NAT", "type": "ONE_TO_ONE_NAT",
                                   "networkTier": "PREMIUM"}],
            }],
            "metadata": {"items": [{"key": key, "value": value} for key, value in metadata.items()]},
            "scheduling": {
                "onHostMaintenance": "TERMINATE",
                "automaticRestart": False,
                "provisioningModel": "STANDARD",
                "maxRunDuration": {"seconds": str(maximum_uptime_seconds)},
                "instanceTerminationAction": "STOP",
            },
        },
    }
