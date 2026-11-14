"""New submissions are limited to 15s; saved provider jobs retain their timeline."""
import json
from io import BytesIO

import pytest
from PIL import Image

from swapmein.pipeline_settings import PATH
from test_api import api, authenticated  # noqa: F401
from test_kling_model_policy import options


@pytest.mark.parametrize("duration", [16, 20, 30])
@pytest.mark.parametrize("direct", [False, True])
def test_long_kling_rejected_before_upload_preparation_or_generation(api, duration, direct):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    records.data[PATH] = {"kling4_enabled": True}
    requested = options(duration)
    if direct:
        response = client.post("/api/uploads", headers=headers, json=requested)
    else:
        photo = BytesIO()
        Image.new("RGB", (256, 256), "blue").save(photo, "PNG")
        response = client.post("/api/jobs", headers=headers, files={
            "clip": ("clip.mp4", b"clip", "video/mp4"),
            "reference": ("photo.png", photo.getvalue(), "image/png")},
            data={"setup": json.dumps(requested["setup"])})
    assert response.status_code == 409
    assert response.json()["code"] == "kling_duration_limit"
    assert not cloud.upload_sizes and not cloud.enqueued and not provider.submissions
    assert not any(p.startswith(("jobs/", "uploads/")) for p in records.data)


def test_enabled_kling_config_advertises_fifteen_seconds(api):
    client, records, _, _ = api
    records.data[PATH] = {"kling4_enabled": True}
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json()["kling4_enabled"] is True
    assert response.json()["max_duration_seconds"] == 15


def test_previously_accepted_thirty_second_upload_retains_original_trim(api):
    client, records, _, provider = api
    headers, _ = authenticated(api)
    records.data[PATH] = {"kling4_enabled": True}
    response = client.post("/api/uploads", headers=headers, json=options(15))
    assert response.status_code == 201
    identifier = response.json()["id"]
    # Reconstruct an immutable ticket accepted before the new intake limit.
    manifest = records.data[f"uploads/{identifier}/manifest.json"]
    manifest["setup"]["trim"] = [0, 30]
    completed = client.post(f"/api/uploads/{identifier}/complete", headers=headers)
    assert completed.status_code == 202, completed.text
    assert records.data[f"jobs/{identifier}.json"]["setup"]["trim"] == [0, 30]
    assert not provider.submissions
