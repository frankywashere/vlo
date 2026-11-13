# Rules

Every agent working on this project follows these rules, and agents' reviews check each change against them. One rule per line.

- Organize code by feature under `frontend/src/features/<feature>/`, with `components/`, `hooks/`, `utils/`, `__tests__/`, `constants.ts`, and a top-level primary component, hook, or store.
- Each feature exposes only its public API through `index.ts` (barrel file); keep internal components and helpers private.
- Use named exports only; never `export default`.
- Name React component files in `PascalCase`, hooks in `camelCase` with a `use` prefix, and utilities in `camelCase`.
- Name types and interfaces in `PascalCase` and constants in `UPPER_SNAKE_CASE`; the root component matches the feature name.
- Declare components as `export function ComponentName(...)`; do not use `React.FC`.
- Define a `<ComponentName>Props` interface for component props.
- Keep components focused on UI; extract complex logic (drag-and-drop, collision math) into custom hooks.
- Use `React.memo` for list items and frequently rendered components.
- Use Zustand for state, with separate stores per domain; stores own actions and update logic.
- Always select Zustand state with granular selectors or `useShallow`; never select the whole state object.
- Style with MUI; use `Box` with `sx` for general layout.
- For components rendered in large loops (clips, ticks, list items), use `styled()` instead of `sx`.
- Never use `any` without an explicit comment justifying it; prefer `unknown` or specific types.
- Use `import type { ... }` when importing types and interfaces.
- Put complex calculations in pure, stateless functions in `utils/`; put stateful singletons managing external systems in `services/`.
- Comments explain why, not what; use numbered comments for complex algorithmic steps.
- Before adding a reusable service, registry, command path, long-running job, renderer contribution, or host option list, read `extensions/extension-development/references/host-contracts.md` and classify it as exact API consumer, shared host seam, or conformance-only.
- Keep owner binding, activation disposal, SDK shapes, finite-JSON checks, limits, and error translation in the extension adapter; keep reusable mechanics in the owning host domain; core code must not import an owner-bound extension adapter.
- Where a native analogue exists for an extension contribution, add paired tests showing native and SDK entry points share the same validation, arbitration, and lifecycle rules.
- State the extension surface classification in the pull request description, and classify new adapter call sites in `.gitattributes`.
- Colocate tests in `__tests__` directories inside `components`, `hooks`, or `utils`, and name them `*.test.ts` or `*.test.tsx`.
- Use `globalThis`, never `global`, when mocking or accessing global objects in tests.
- Run backend tests with the venv interpreter (`backend/.venv/bin/python -m pytest`), not a bare `python3.x` path.
- Run `npm run check:extension-surface` before handoff or commit and review every reported category; add new files that dispatch, persist, or present extension-owned behaviour to `.gitattributes`.
- Run the relevant tests before committing (`npm run test` for frontend, `npm run test:backend` for backend) and fix failures first.
