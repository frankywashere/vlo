# Decisions

Short notes of what was decided in chat.

## Architecture note

**Frontend feature map** (`frontend/src/features/`, each feature behind an `index.ts` barrel; shared infrastructure in `frontend/src/core/`):
- Editing: `timeline`, `timelineSelection`, `player`, `renderer`, `transitions`, `transformations`, `text`, `masks`, `tracking`, `composite`, `colorGrading`, `scopes`, `charSwap`, `miniEditor`.
- Project and media: `project`, `userAssets`, `libraryBrowser`, `editorFocus`, `panelUI`.
- AI and backend-facing: `generation`, `modelWork`, `samAudio`, `runtimeCapabilities`.
- Extensions: `extensions` (host implementation).
- `core/` holds cross-feature building blocks: `shell` (menu, command, keybinding and region registries), `playback`, `project`, `persistence`, `media`, `export`, `extract`, `color`, `pixi`, `time`, `liveParams`, `backendJobs`.

**Backend** (`backend/`, FastAPI; `main.py` wires routers):
- Routers (`backend/routers/`): `app_lifecycle`, `app_settings`, `beats`, `comfyui`, `comfyui_compat`, `downloads`, `extensions`, `generation_delivery`, `runtime_capabilities`, `sam2`, `sam_audio`.
- Services (`backend/services/`) mostly mirror the routers: `app_lifecycle`, `beats`, `comfyui`, `extensions`, `generation_delivery`, `sam2`, `sam_audio`, plus `jobs` (shared long-running job kernel), `gen_pipeline`, `model_work`, `ai_models`, `workflow_rules`, and single-file services such as `download_service`, `model_registry`, `runtime_settings`, `hardware`.

**Extension surface** (see AGENTS.md section 8): a capability is classified as an exact API consumer, a shared host seam, or conformance-only. The classification is recorded in the PR description. Files are catalogued in `.gitattributes` via `extension-surface=` with these values:
- `host`: implementation spine (`features/extensions`, `core/shell`, `backend/services/extensions`, `backend/services/jobs`, `routers/extensions.py`).
- `adapter`: translates public extension names onto shared host code (e.g. `services/extensions/jobs.py`, `backend/main.py`, `core/color/gradeSchema.ts`).
- `public`: portable V1 contracts (`packages/extension-sdk`, `backend/services/extensions/__init__.py`).
- `authoring`: `extension-template`, `extensions/extension-development`.
- `fixture`: out-of-tree conformance fixtures and their test suites.
Run `npm run check:extension-surface` before handoff; reusable mechanics stay in the owning host domain, while owner binding, disposal and error translation stay in the adapter.
