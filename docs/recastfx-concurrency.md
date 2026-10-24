# RecastFX processing capacity

Implementation branch: `optimization`, based on production `main`.

## Admin behavior

Admin contains a **Processing capacity** section beside Service controls.
Staff with `service_controls` can save revision-checked integer limits:

| Setting | Default after migration | Bounds | Counts |
| --- | --- | --- | --- |
| Simultaneous processing jobs | 8 | 1–256 | Preparation and finishing tasks sharing the heavy worker pool |
| Simultaneous reAPI renders | 8 | 1–256 | Reservations, accepted generations and unresolved submissions |

The panel loads on expansion, shows active counts and has explicit Save and
Refresh actions. It is read-only until the required worker routes, CPU lifecycle
configuration and capacity migration are ready. Lowering a limit leaves existing
work running; new jobs wait until active usage is below the saved limit.

These are application admission limits within fixed, bounded infrastructure
ceilings. Editing Admin does not grant cloud permissions or deploy a new service.
A different ceiling needs a deliberate infrastructure change. Request-based CPU
instances retain minimum zero, so raising the ceiling does not keep idle workers
running.

The reAPI limit applies to this production backend's tracked work, not to jobs
submitted directly through another script/provider dashboard or the isolated
minute-test backend. Provider-enforced limits still apply. Their live
[pricing page](https://reapi.ai/pricing), checked October 24, lists 30 concurrent
media tasks per account and offers account limit increases.
The API overview and pricing page also list different request-rate defaults
(20 versus 50 per second), so neither is treated as verification of this
account's exact allowance. Idempotency headers do not prevent another billable
POST. Start at eight, observe provider responses, and keep Admin within the
account's approved limit. Multiple keys on one account share those limits; no
key pool or automatic cross-key resubmission is introduced. A higher account
allowance can use the same existing key. Saving a higher Admin value within
1–256 needs no deployment once the infrastructure is installed.

## Runtime routes

| Work | Queue / worker | Deployment ceiling |
| --- | --- | --- |
| Admission, guarded provider submission, status checks | `recastfx-control` / `recastfx-control-worker` | 128 concurrent deliveries; 16 instances, eight requests each; 16 dispatches/second |
| Preparation, reviewed guide rebuild, finishing/comparison export | `recastfx-work` / `recastfx-worker` | 256 concurrent deliveries; 256 instances, one request each; 32 dispatches/second |
| CPU guide/face mesh/audio work | isolated private `recastfx-audio` | Automatic min zero/max 256, one request per instance |
| Local SAM/depth GPU route | existing private `proof-gpu` | Existing single-worker lifecycle and exclusive preparation lease |

The application heavy permit is checked before claiming a delivery execution.
Busy tasks remain retryable without consuming a paid model attempt. Receipt
lane and queue are saved durably: old heavy deliveries remain drainable and a
watchdog cannot accidentally recover a task to the other lane. Each lane uses
its exact private URL as its JWT audience. The website API keeps its original
account/session authentication and original instance ceiling.

A successful admission task enqueues preparation and returns; it does not run
all media processing inside the quick worker. Preparation reservations support
multiple owners while preserving daily/monthly admission counts. Only one
local-worker GPU preparation can hold a lease; Replicate preparations may run
alongside it within the overall heavy limit. Cleanup releases only its own
reservation, and uncertain GPU shutdown retains a blocked recovery lease.

CPU requests in the new mode verify the exact private configured CPU service identity,
automatic bounded scaling, request billing, concurrency and readiness. They
never manually scale that shared service or schedule its shutdown. Legacy CPU
and GPU behavior remain available when the new configuration flags are off.

## Provider admission and recovery

The generation limit is acquired before the wallet debit and provider POST.
If full, the job retains explicit approval and appears queued in Library.
API-key continuations retain only the existing short-lived encrypted key; no
plaintext key is added to task bodies or capacity records. Each continuation has
a current semantic token, so stale duplicates cannot submit the render.

The original atomic ready-to-submitting claim still guards the paid POST.
Two accepted jobs can remain in flight while another waits. Known terminal
provider results release a slot before finishing starts. Unknown submissions
retain their reservation until reconciled; they do not expire automatically.
Unpaid stale reservations may be recovered only with a matching reservation
version, preventing a watchdog from freeing a newly renewed submission slot.

A failed outbox write clears the queued display flag and exposes an attention
message while retaining approval and inputs. A delayed, unstarted preparation
can release its own expired reservation, rotate its semantic token and return to
admission. A blocked GPU shutdown reservation is never treated as unused.

## Controlled rollout

No deployment is performed merely by building or saving this branch. The default
configuration keeps the existing single-worker route until explicitly enabled.
The minute-test runtime/UI are not part of this branch and its single-worker
preview deployment path remains unchanged.

Before a production migration:

1. Establish an actual maintenance boundary for new generation/upload/review
   submissions and pause old background admission/submission deliveries. Drain
   already-running API/worker requests. The deployment tool's
   `--submissions-paused` flag is an assertion of this completed operation; it
   does **not** itself pause the old API. Do not pass it while requests can still
   race the adoption scan.
2. Confirm no legacy preparation or retained blocked shutdown lease remains.
   Let old `shutdown-audio-*` backup tasks drain; do not delete an unknown backup
   merely to satisfy the check. Those tasks can otherwise force the shared CPU
   service back into manual zero while another request is using it.
3. Build the tested production image and run `deploy_task_workers.py` with the
   production target, immutable image and verified pause assertion. It validates
   the CPU image and spend configuration, adopts current
   submitting/generating/unknown reAPI jobs, and refuses a migration scan above
   10,000 job records. An already initialized capacity ledger is retained.
4. The tool provisions the two queues/private workers and an isolated
   `recastfx-audio` CPU service with bounded automatic scaling, resolves each exact worker URL, then
   activates `PROCESSING_CAPACITY_ENABLED`, `REQUEST_SCALED_AUDIO` and the control
   queue routing. Secrets stay as existing Secret Manager references under the
   existing service account. GPU capacity and provider keys do not change.
5. Verify queue settings, private IAM, CPU billing/scaling, both worker audiences,
   health, watchdog success and Admin readiness; then deliberately resume the
   paused admission path. Begin at eight/eight and measure queue wait, stage timing,
   provider failures and spend before raising the limits.

For rollback, first stop new admissions and drain active CPU requests. Returning
to manual CPU shutdown while autoscaled requests remain active is unsafe.
Preserve the capacity ledger, saved provider task IDs, wallet records and
outboxes; do not free unknown paid reservations just to unblock the queue.

## Validation

Tests use in-memory atomic records and mocked provider transports, with no paid
model submissions. Coverage includes two jobs accepted/third queued, admission
handoff, no debit while waiting, one debit/POST after a slot opens, stale tokens,
unknown submissions, lowering limits, read/write staff permissions, revision
conflicts, invalid bounds/configuration, owned multi-job preparation leases,
local GPU exclusivity, CPU contexts that overlap without shutting down one
another, queue-lane authentication, watchdog routing, bounded migration and
reconciliation/submission races. This is technical validation, not a live
multi-user throughput or provider-capacity benchmark.

Extension surface classification: no supported editor/extension SDK surface is
changed. This is the separate RecastFX application backend and Admin UI; the
repository extension-impact check is still run.

Validation on October 24, 2026: the full gateway suite passed 2,377 tests before
the final narrow recovery/configuration refinements; the final focused
API/storage/pipeline/CPU/worker/deployment integration run passed 455 tests.
The separate two-user admission/charge suite passed eight tests after its last
fixture update. All 598 CharSwap frontend tests passed with two test workers;
TypeScript, the browser-cleanup asset check and the production frontend build
passed. The extension-impact and whitespace checks were clean. Nothing was
submitted to a paid model or deployed during this implementation.

An earlier follow-up raised the preconfigured ceiling from four/eight to 32 for both
controls and changes new-install defaults from two to eight. Existing saved
limits are retained. CPU/worker minima stay zero and billing stays request based;
these ceilings do not reserve paid idle instances. Local GPU exclusivity and
minute-preview isolation are unchanged.

That earlier 32-ceiling/eight-default follow-up passed 458 focused backend integration
checks, 56 Admin/panel tests, a subsequent 54-test capacity/API boundary rerun,
TypeScript/build and extension-impact/diff checks. Tests admit eight jobs and
queue the ninth by default, permit all 32 after an explicit Admin change, reject
33, and retain any previously saved limits. No provider key was added, and no
live deployment or paid model request was made.

## Expanded headroom (October 24, 2026)

The current preconfigured ceiling is 256 processing jobs and 256 active reAPI
renders, with both defaults still eight. The control service can scale to 16
instances with eight requests each, while queue dispatch rates remain bounded
at 16/second for control work and 32/second for heavy work. Shared backend
constants live in `swapmein/capacity_bounds.py` to keep deployment and runtime
validation aligned. Admin fields follow the server-advertised ceiling.

Live us-east4 quotas were rechecked: 3,000 vCPUs and 6,000 GiB memory. At all
configured worker ceilings, heavy workers (256×2 vCPU/4 GiB), guide workers
(256×4 vCPU/8 GiB) and control workers (16×2 vCPU/4 GiB) total 1,568 vCPUs and
3,136 GiB before the existing small API/preview services. This fits the checked
CPU/RAM allowance with headroom; it is arithmetic, not a measured throughput
claim or a reservation of that capacity. Local GPU exclusivity is unchanged.

All minima remain zero and CPU billing remains request based. The saved Admin
value, rather than this ceiling, controls expensive stage admission. Excess
queued tasks and retries can still incur small request/startup overhead. Actual
provider allowances, existing daily admission/spend controls and regional
availability remain independent constraints. No Google quota was increased.

The expanded ceiling passed 458 backend integration checks, 56 Admin/panel tests,
TypeScript/build and extension-impact/diff checks. Boundary tests accept 256 and
reject 257; deployment tests preserve min zero/private IAM, keep preview/GPU
limits unchanged and verify dispatch rates do not grow with the instance ceiling.
No live deployment or paid provider request was performed.

The production deployment clones the installed CPU image into `recastfx-audio`,
leaving the older site’s `proof-audio` lifecycle unchanged. Guarded preparation
pools maintain a legacy-readable busy lease while any parallel preparation runs.
This prevents an old-version request from overwriting the pool or sharing its GPU.
