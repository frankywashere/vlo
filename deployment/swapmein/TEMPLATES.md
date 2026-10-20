# Reusable templates

The Templates page opens ordinary clips at **Clip**. Published templates with a
staff-reviewed person selection open at **Character**, filling in the clip,
whole-second duration and selected person. Changing the trim clears that selection.
Neither path starts a generation, skips billing validation, or treats a face
preview as an already prepared SAM guide.

Admin → Templates supports upload, categories, title editing, face selection,
CPU face tracking, draft/publish/unpublish and archive. Uploads must be 4–60 seconds
and at most 100 MiB. The stored MP4 uses the first 15 seconds at most, whole seconds,
H.264/AAC and a maximum 1280px side without upscaling. Original uploads are temporary.
Publishing requires permission to distribute/reuse the footage and a review of
any selected person. Templates without a selected person can also be published.

Face preview uses OpenCV's bundled frontal-face cascade at 4Hz (maximum 60 samples).
It follows spatially adjacent boxes and stops at ambiguous matches or large jumps.
It does not identify people. Occlusion, profile faces and scene cuts may produce
partial tracks; staff can choose another frame or keep a manually selected anchor.
The normal generation pipeline still performs its own person/mask preparation.

`manage_templates` is a separate staff permission. Owners always have it. Read-only
staff can inspect the catalog, while upload/category/publish/track mutations enforce
the permission on the server. Upload authorization happens before multipart media
is read. Processing is limited to one template per gateway process; it never calls
paid providers or GPU services. Catalog and per-template revisions prevent stale edits.

Production uses private GCS records at `control/templates.json` and immutable media
under `template-media/`. Public responses include only published templates and
short-lived media URLs. Draft URLs are available only to authorized staff. Archive
retracts a template but keeps media for existing signed links and operator recovery.
The catalog is capped at 200 records/25 categories. Archived media and orphaned
attempts require operator storage maintenance; they do not use the 30-day generation
library retention rule. Published footage is deliberately reusable by other users.

For local development:

1. Install `deployment/swapmein/requirements.txt` into its `.venv`.
2. Run `npm run dev:templates` from the repository root.
3. Add `RECASTFX_TEMPLATE_API=http://127.0.0.1:8081` to `frontend/.env.local` and
   restart the Vite dev/preview server. This server-only variable is not bundled.

Local storage stays in ignored `backend/runtime/template-library/`; media URLs are
signed with a persistent private local key. Use one local server worker. Local staff
authorization verifies Firebase and checks the existing secure gateway's Admin
permissions. It does not need a Google Cloud CLI login, create fake admin accounts,
grant local privileges, or write to production storage. New delegated staff permission
management requires deploying the updated production gateway; existing owners can
manage local templates immediately. Without the override, API routing uses the hosted
gateway. Deploy the updated gateway and frontend for production template publishing.

Bundled sample clips remain usable when the live catalog is empty. Once staff publish
templates, those published choices replace the sample collection. The original upload
copyright confirmation remains required when users add their own footage.

This is an app-owned catalog/storage service; no SDK or extension lifecycle changes.
The account dropdown continues to use the existing shared shell menu contract, with
native and contributed action coverage. Face detection reference:
[OpenCV cascade classifiers](https://docs.opencv.org/4.x/db/d28/tutorial_cascade_classifier.html).
