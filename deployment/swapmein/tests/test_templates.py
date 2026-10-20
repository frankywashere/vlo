from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import pytest
import swapmein.app as gateway
import swapmein.template_api as api_module
import swapmein.templates as domain
from swapmein.errors import PublicError
from swapmein.template_local import LocalTemplateStorage
from swapmein.template_tracking import follow_faces, prepare_template, track_template
from swapmein.templates import TemplateCatalog
from test_api import api, MemoryRecords

OWNER = {"email": "owner@example.com", "account_id": "a" * 64}
ANCHOR = {"x": .5, "y": .4, "time": 0}


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    media = LocalTemplateStorage(tmp_path)
    catalog = TemplateCatalog(MemoryRecords(), media, "local")
    def prepare(source, video, poster):
        video.write_bytes(b"video")
        poster.write_bytes(b"poster")
        return {"duration": 5, "width": 640, "height": 480}
    monkeypatch.setattr(domain, "prepare_template", prepare)
    monkeypatch.setattr(domain, "track_template", lambda *args: {"anchor": ANCHOR, "tracking": "tracked", "face_track": [{"time": moment, "x": .4, "y": .3, "width": .2, "height": .2} for moment in [0, .25]]})
    return catalog


def upload(catalog, tmp_path, template_id="b" * 32):
    source = tmp_path / "original.mp4"
    source.write_bytes(b"source")
    return catalog.upload(source, "A scene", "cinematic", template_id, OWNER)


def test_publishing_requires_review_and_has_no_private_record_fields(catalog, tmp_path):
    item = upload(catalog, tmp_path)
    assert item["status"] == "draft" and not catalog.view()["templates"]
    with pytest.raises(PublicError):
        catalog.item(item["id"], public=True)
    with pytest.raises(PublicError, match="Review and confirm"):
        catalog.update(item["id"], 0, "A scene", "cinematic", "published", ANCHOR, False, OWNER)
    catalog.update(item["id"], 0, "A scene", "cinematic", "published", ANCHOR, True, OWNER)
    public = catalog.view()["templates"][0]
    assert public["anchor"] == ANCHOR and public["tracking"] == "tracked"
    assert "video_uri" not in public and "updated_by" not in public
    with pytest.raises(PublicError, match="changed"):
        catalog.update(item["id"], 0, "stale edit", "cinematic", "draft", None, False, OWNER)
    catalog.update(item["id"], 1, "A scene", "cinematic", "draft", None, False, OWNER)
    assert not catalog.view()["templates"]
    assert catalog.item(item["id"])["face_track"] == []


def test_template_upload_is_idempotent_and_categories_are_versioned(catalog, tmp_path):
    item = upload(catalog, tmp_path)
    upload(catalog, tmp_path)
    assert len(catalog.read()["templates"]) == 1
    with pytest.raises(PublicError, match="changed"):
        catalog.category(revision=0, name="Action")
    changed = catalog.category(revision=1, name="Action")
    category = next(row for row in changed["categories"] if row["name"] == "Action")
    catalog.category(revision=2, category_id=category["id"], name="Adventure")
    with pytest.raises(PublicError, match="Move the templates"):
        catalog.category(revision=3, category_id="cinematic", remove=True)
    catalog.update(item["id"], 0, "A scene", category["id"], "published", None, False, OWNER)
    assert catalog.view()["templates"][0]["category"] == "Adventure"


def test_local_media_links_are_signed_expiring_and_scoped(tmp_path):
    storage = LocalTemplateStorage(tmp_path / "storage")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    uri = storage.upload("local", "template-media/" + "b" * 32 + "/video.mp4", source, "video/mp4")
    url = urlsplit(storage.signed_url(uri))
    query = parse_qs(url.query)
    assert storage.verify_asset("b" * 32, "video.mp4", int(query["expires"][0]), query["signature"][0]).read_bytes() == b"video"
    for filename, expires, signature in [("../catalog.json", 1, "invalid"), ("video.mp4", 1, "invalid"), ("video.mp4", int(query["expires"][0]), "invalid")]:
        with pytest.raises(PublicError):
            storage.verify_asset("b" * 32, filename, expires, signature)


def test_face_trajectory_stops_at_ambiguity_and_never_chooses_another_person():
    boxes = [(0, [(.4, .3, .2, .2), (.05, .3, .1, .1)]), (.25, [(.41, .3, .2, .2)]), (.5, [(.1, .3, .2, .2)])]
    tracked = follow_faces(boxes, ANCHOR)
    assert [frame["time"] for frame in tracked] == [0, .25]
    assert follow_faces(boxes, {"x": .9, "y": .9, "time": 0}) == []
    assert len(follow_faces([(0, [(.4, .3, .2, .2)]), (.25, [(.41, .3, .2, .2), (.42, .3, .2, .2)])])) == 1


def test_template_routes_enforce_staff_permissions_and_never_start_a_generation(api, catalog, monkeypatch):
    client, records, cloud, provider = api
    monkeypatch.setattr(api_module, "catalog_service", lambda: catalog)
    assert client.get("/api/commerce/admin/templates").status_code == 401
    assert client.post("/api/commerce/admin/templates", files={"clip": ("a.mp4", b"video")}, data={"title": "A", "category_id": "cinematic", "request_id": "b" * 32, "rights_confirmed": "true"}).status_code == 401
    monkeypatch.setattr(api_module, "verify_account", lambda *_: OWNER)
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", OWNER["email"])
    result = client.post("/api/commerce/admin/templates", files={"clip": ("a.mp4", b"video")}, data={"title": "A", "category_id": "cinematic", "request_id": "b" * 32, "rights_confirmed": "true"})
    assert result.status_code == 200 and result.json()["status"] == "draft"
    assert not provider.submissions and not cloud.enqueued
    assert client.get("/api/templates").json()["templates"] == []
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", "someone-else@example.com")
    assert client.post("/api/commerce/admin/templates/categories", json={"revision": 1, "name": "Action"}).status_code == 403
    assert client.get("/api/templates/" + "b" * 32).status_code == 404


def test_upload_requires_publication_permission_and_rejects_empty_media(api, catalog, monkeypatch):
    client, *_ = api
    monkeypatch.setattr(api_module, "catalog_service", lambda: catalog)
    monkeypatch.setattr(api_module, "verify_account", lambda *_: OWNER)
    monkeypatch.setenv("COMMERCE_OWNER_EMAILS", OWNER["email"])
    for content, allowed in [(b"video", "false"), (b"", "true")]:
        result = client.post("/api/commerce/admin/templates", files={"clip": ("a.mp4", content)}, data={"title": "A", "category_id": "cinematic", "request_id": "b" * 32, "rights_confirmed": allowed})
        assert result.status_code == 400


def test_local_templates_verify_identity_and_existing_staff_access(api, tmp_path, monkeypatch):
    import httpx
    monkeypatch.setenv("TEMPLATE_LOCAL_DIRECTORY", str(tmp_path))
    identities = []
    monkeypatch.setattr(api_module, "verify_account", lambda token, project: identities.append(token) or OWNER)
    access = {"role": "viewer", "permissions": []}
    def staff_response(url, **options):
        assert url == "https://recastfx.com/api/commerce/admin"
        assert options["headers"] == {"X-Firebase-Token": "verified-token"}
        assert options["follow_redirects"] is False
        return httpx.Response(200, json=access, request=httpx.Request("GET", url))
    monkeypatch.setattr(api_module.httpx, "get", staff_response)
    with pytest.raises(PublicError) as missing:
        api_module.authorize(None, write=True)
    assert missing.value.status_code == 401
    assert api_module.authorize("verified-token") == OWNER
    with pytest.raises(PublicError) as denied:
        api_module.authorize("verified-token", write=True)
    assert denied.value.status_code == 403
    access["permissions"] = ["manage_templates"]
    assert api_module.authorize("verified-token", write=True) == OWNER
    access.update(role="owner", permissions=[])
    assert api_module.authorize("verified-token", write=True) == OWNER
    assert identities == ["verified-token"] * 4


def test_real_template_preparation_is_browser_playable_and_no_face_is_not_fabricated(tmp_path):
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is needed for template preparation")
    from swapmein.media import run
    source, video, poster = [tmp_path / name for name in ("source.mp4", "video.mp4", "poster.jpg")]
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x240:r=24:d=4", "-c:v", "libx264", "-threads", "2", str(source)])
    metadata = prepare_template(source, video, poster)
    assert metadata == {"duration": 4, "width": 320, "height": 240}
    assert video.is_file() and poster.is_file()
    assert track_template(video, 4) == {"anchor": None, "tracking": "none", "face_track": []}
