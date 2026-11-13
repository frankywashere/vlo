# Plan

## Goals

Provisional: nobody has said what to build yet. This plan is drafted from the repository only and will be rewritten once the team answers in General.

vlo is a free, local, open-source, extensible video editor with AI features (frame-accurate timeline, ComfyUI bridge, rotoscoping masks, SAM-Audio, colour grading, an extension SDK). Recent commits show a "charSwap" / RecastFX line of work (character swap, Kling recipes, reference photos, YouTube downloads). The first goal is a healthy baseline, so any new feature can be built and verified safely.

## Approach

- Stack: React/TypeScript frontend (`frontend/src/features/*`, Zustand, MUI), Python/FastAPI backend (`backend/`), extension SDK (`extensions/`, `packages/`).
- Follow `AGENTS.md`: feature-based layout, named exports, strict typing, colocated `__tests__`, and the extension-surface classification.
- Verify with `npm run test`, `npm run test:backend` and `npm run check:extension-surface` before each handoff.
- Keep changes small and reviewable. Do not start feature work until a direction is chosen (Phase 2).

## Phase 1: Baseline and orientation

1. Install dependencies, then run the frontend and backend test suites on `main`. Record failures and flaky tests.
2. Run `npm run check:extension-surface` and note the current report.
3. Fill in `.ehgi/rules.md` from `AGENTS.md` (the key conventions, one rule per line) so reviews can check against it.
4. Write a short architecture note in `.ehgi/decisions.md`: the feature map, the backend routers/services, and how the extension surface is classified.

## Phase 2: Choose the direction (blocked on the team)

1. Collect the team's answer to "what do we want to build?" in General.
2. Rewrite Goals and the phases below this one around that answer.
3. Break the chosen scope into tasks with acceptance criteria and tests.

## Phase 3: Candidate follow-ups (confirm before starting)

1. Review the charSwap and RecastFX docs in `docs/` and list the known gaps in that workflow.
2. Check that the Windows install and run scripts still work (`install.bat`, `run.bat`, `update.bat`), since the changelog shows past Windows-specific bugs.
3. Extend test coverage in the least-tested feature areas found in Phase 1.
