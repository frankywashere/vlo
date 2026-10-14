# swapMEin local app

Open `http://127.0.0.1:6332/charswap.html` after running `run.bat`. The page includes
the landing page, email/password registration and sign-in, a seven-step creation
wizard, light/dark themes, and a private video library.

Accounts use scrypt password hashes and seven-day HttpOnly sessions. Account and
library routes enforce ownership; videos are outside Vlo's public `/static`
directory. The database and saved videos live in `backend/runtime/swapmein/`, or
`$VLO_DATA_DIR/runtime/swapmein/` when a data directory is configured. Back up this
directory to preserve accounts and videos. Account data is tied to this server.
Email verification and password recovery are not configured.

The library records live job progress while the wizard is open and copies completed
videos to local storage. If a copy fails, the app shows a retry action. Saved
videos can be played, downloaded, and deleted. Deletion requires
confirmation. Provider keys stay in the current wizard's memory and are never
written to the account database.

Local HTTP previews remain offline for paid generation. The separate live gateway
still owns preparation, review approval, provider submission, and final export.
This change does not deploy the app or connect the local wizard to paid cloud
workers. The SQLite account service is for a persistent local server; deploying
the frontend alone to Firebase Hosting will not supply account routes. A hosted
release needs an account backend with durable storage, HTTPS, and same-origin
account routing.

## H3 providers

All four provider options keep the standard MiniMax H3 model:

| Provider | Model/endpoint | Output | Duration |
| --- | --- | --- | --- |
| reAPI | `minimax-h3` | 768p | 4–15 seconds |
| ToAPIs | `MiniMax-H3` | 768p | 4–15 seconds |
| fal | `minimax/h3/reference-to-video` | 768p | 5–15 seconds |
| OpenRouter | `minimax/hailuo-3` | 2K | 5–15 seconds |

The fal adapter uses the queue API with both image and guide-video references.
Its balance check requires `billing:usage:read` in addition to an inference-capable
key. OpenRouter uses typed image/video references and authenticated video retrieval;
the gateway copies its result to private storage before background export, so it
doesn't persist provider credentials. An unlimited OpenRouter key may not reveal
its balance; the provider enforces available credits during submission. Neither
adapter retries an ambiguous paid submission or switches models.

Contracts were checked against the public [fal H3 schema](https://fal.ai/models/minimax/h3/reference-to-video/api),
[fal billing API](https://fal.ai/docs/platform-apis/v1/account/billing),
[OpenRouter H3 listing](https://openrouter.ai/minimax/hailuo-3), and
[OpenRouter video API](https://openrouter.ai/docs/guides/overview/multimodal/video-generation)
on 14 October 2026. Tests use mock transports; no paid provider request was made.

## Offline sample

Run `backend/.venv/Scripts/python.exe scripts/create-charswap-sample.py` on Windows
(use `.venv/bin/python` on Unix). The script creates an original five-second
cartoon clip and reference images in the ignored `frontend/public/charswap-demo/`
directory. Rebuild after creating it to include it in the local production build.
Sample review issues are simulated and sample generation remains disabled.

## Checks

From `frontend/`, run `npm run test:run -- src/features/charSwap --testTimeout=20000`
and `npm exec -- eslint src/features/charSwap`. From `backend/`, run
`.venv/Scripts/python.exe -m pytest tests/test_swapmein_accounts.py`. Gateway tests
run from `deployment/swapmein/` with its own requirements and Python environment.
From the repository root, run `npm run build` and `npm run check:extension-surface`.

The extension-impact report classifies `backend/main.py` as an adapter because it
hosts extension runtime startup. Its only change is registration of the account
router; extension lifecycle and SDK contracts are unchanged.

The account dropdown consumes the shared host menu renderer (`AppMenu`) with the
`charswap.account` subject. This is a shared host seam; the subject contains only
a signed-in flag, with no email, account ID, session token, or API key. Settings,
appearance, and session actions remain in the account dropdown on every screen.
Settings opens a dedicated page with Generation, Credits, and Account tabs. The
generation key and provider are shared with the wizard, remain in memory, and
clear on sign-out. Account credit balances come from private server storage and
default to zero. The header balance opens the Credits tab. Purchases and credit
spending are unavailable until a payment and billing service is integrated.
Signed-in users open directly into Your library, and the logo returns there.
The library contains generated videos; it has no video-import control.
The landing-page Before/After media is imported as a bundled asset, including in
the separate public build that excludes local demo media.
The How It Works walkthrough cycles every five seconds while visible, pauses
during preview exploration, and provides a Play/Pause control. Reduced-motion
preferences disable automatic cycling until the user explicitly starts it.
