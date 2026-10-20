"""Versioned template catalog shared by production GCS and local file storage.

Host-domain service: staff policy stays at the HTTP adapter, not in the catalog.
"""
from pathlib import Path
import copy
import tempfile
import time
import uuid

from .errors import PublicError
from .template_tracking import prepare_template, track_template

CATALOG_PATH = "control/templates.json"
DEFAULT_CATEGORIES = [{"id": name.lower(), "name": name} for name in ("Cinematic", "Social", "Characters", "Comedy")]


class TemplateCatalog:
    def __init__(self, records, media, bucket):
        self.records, self.media, self.bucket = records, media, bucket

    def read(self):
        current, _ = self.records.read(CATALOG_PATH)
        return current or {"revision": 0, "categories": copy.deepcopy(DEFAULT_CATEGORIES), "templates": []}

    def mutate(self, change):
        def update(current):
            current = current or {"revision": 0, "categories": copy.deepcopy(DEFAULT_CATEGORIES), "templates": []}
            change(current)
            return {**current, "revision": current["revision"] + 1}
        return self.records.update(CATALOG_PATH, update)

    def item(self, template_id, *, public=False):
        found = next((item for item in self.read()["templates"] if item["id"] == template_id), None)
        if not found or found["status"] == "archived" or public and found["status"] != "published":
            raise PublicError("This template is no longer available.", 404)
        return found

    def present(self, item, categories):
        return {**{key: item[key] for key in ("id", "title", "category_id", "duration", "width", "height", "revision", "status", "anchor", "face_track", "tracking")},
                "category": next((category["name"] for category in categories if category["id"] == item["category_id"]), "Other"),
                "video": self.media.signed_url(item["video_uri"]), "poster": self.media.signed_url(item["poster_uri"])}

    def view(self, *, admin=False):
        catalog = self.read()
        return {"revision": catalog["revision"], "categories": catalog["categories"], "templates": [self.present(item, catalog["categories"])
            for item in catalog["templates"] if item["status"] != "archived" and (admin or item["status"] == "published")]}

    def category(self, *, revision, name=None, category_id=None, remove=False):
        def change(catalog):
            if catalog["revision"] != revision:
                raise PublicError("Templates changed in another session. Refresh before saving.", 409)
            if remove:
                if not category_id:
                    raise PublicError("Choose a category to remove.")
                if any(item["category_id"] == category_id and item["status"] != "archived" for item in catalog["templates"]):
                    raise PublicError("Move the templates out of this category first.", 409)
                catalog["categories"] = [item for item in catalog["categories"] if item["id"] != category_id]
                return
            clean = (name or "").strip()
            if not clean or len(clean) > 40 or clean.casefold() == "all":
                raise PublicError("Use a category name between 1 and 40 characters, other than All.")
            if any(item["name"].casefold() == clean.casefold() and item["id"] != category_id for item in catalog["categories"]):
                raise PublicError("That category already exists.", 409)
            if category_id:
                item = next((item for item in catalog["categories"] if item["id"] == category_id), None)
                if not item:
                    raise PublicError("Category not found.", 404)
                item["name"] = clean
            else:
                if len(catalog["categories"]) >= 25:
                    raise PublicError("Use up to 25 categories.", 409)
                catalog["categories"].append({"id": uuid.uuid4().hex, "name": clean})
        self.mutate(change)
        return self.view(admin=True)

    def upload(self, source, title, category_id, template_id, actor):
        catalog = self.read()
        existing = next((item for item in catalog["templates"] if item["id"] == template_id), None)
        if existing:
            return self.present(self.item(template_id), catalog["categories"])
        if len(catalog["templates"]) >= 200:
            raise PublicError("The template catalog is full. Contact support to archive older media.", 409)
        if category_id not in {item["id"] for item in catalog["categories"]}:
            raise PublicError("Choose an existing category.")
        with tempfile.TemporaryDirectory() as directory:
            video, poster = Path(directory) / "video.mp4", Path(directory) / "poster.jpg"
            metadata = prepare_template(source, video, poster)
            tracking = track_template(video, metadata["duration"])
            # Each attempt has immutable media, so a retry after a partial cloud
            # upload cannot overwrite footage referenced by a published template.
            prefix = f"template-media/{uuid.uuid4().hex}/"
            video_uri = self.media.upload(self.bucket, prefix + "video.mp4", video, "video/mp4")
            poster_uri = self.media.upload(self.bucket, prefix + "poster.jpg", poster, "image/jpeg")
        item = {"id": template_id, "title": title.strip(), "category_id": category_id, "status": "draft", "revision": 0,
                "video_uri": video_uri, "poster_uri": poster_uri, **metadata, **tracking,
                "rights_confirmed": True, "created_at": time.time(), "updated_by": actor["email"]}
        def change(current):
            if any(row["id"] == template_id for row in current["templates"]):
                return
            if len(current["templates"]) >= 200 or category_id not in {item["id"] for item in current["categories"]}:
                raise PublicError("The catalog changed during upload. Refresh before retrying.", 409)
            current["templates"].append(item)
        self.mutate(change)
        return self.present(self.item(template_id), self.read()["categories"])

    def update(self, template_id, revision, title, category_id, status, anchor, reviewed, actor):
        def change(catalog):
            item = next((item for item in catalog["templates"] if item["id"] == template_id), None)
            if not item or item["status"] == "archived":
                raise PublicError("Template not found.", 404)
            if item["revision"] != revision:
                raise PublicError("This template changed. Refresh before saving.", 409)
            if category_id not in {category["id"] for category in catalog["categories"]}:
                raise PublicError("Choose an existing category.")
            if anchor and (not 0 <= anchor["time"] < item["duration"]):
                raise PublicError("Select the person inside the clip.")
            if status == "published" and anchor and not reviewed:
                raise PublicError("Review and confirm the person selection before publishing.")
            if item["anchor"] != anchor:
                item.update(face_track=[], tracking="selected" if anchor else "none")
            item.update(title=title.strip(), category_id=category_id, status=status, anchor=anchor,
                        revision=revision + 1, updated_by=actor["email"], updated_at=time.time())
        self.mutate(change)
        return self.view(admin=True)

    def track(self, template_id, revision, anchor):
        item = self.item(template_id)
        if item["revision"] != revision:
            raise PublicError("This template changed. Refresh before tracking.", 409)
        if not 0 <= anchor["time"] < item["duration"]:
            raise PublicError("Select the person inside the clip.")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "video.mp4"
            self.media.download(item["video_uri"], source)
            tracking = track_template(source, item["duration"], anchor)
        def change(catalog):
            current = next((row for row in catalog["templates"] if row["id"] == template_id), None)
            if not current or current["revision"] != revision or current["status"] != "draft":
                raise PublicError("Unpublish this template and refresh before tracking it.", 409)
            current.update(**tracking, revision=revision + 1)
        self.mutate(change)
        return self.present(self.item(template_id), self.read()["categories"])
