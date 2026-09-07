# Playwright smoke suite

End-to-end against real infrastructure, not mocked — matching this
repo's own testing philosophy (see the root `CLAUDE.md`).

## Running locally

1. Start the real backend + Postgres/Redis/Anvil (repo root):
   `./start.sh` (or `docker compose up --build`).
2. From `frontend/`: `npx playwright test`
   (`playwright.config.ts`'s `webServer` starts `npm run dev` for you if
   it isn't already running; it does NOT start the backend).

First run on a machine that's never used Playwright before also needs
the browser binary: `npx playwright install chromium`.

## What's covered

- The public landing page loads.
- An unauthenticated visit to `/dashboard` redirects to `/auth`.
- Signup → lands on `/dashboard` → `localStorage` never holds a token
  (the actual P1 security property — see `lib/auth.ts`) → the real
  `tc_access` session cookie is `HttpOnly` → logout clears local state
  and returns to `/auth`.
- Starting a real run (`POST /run-agent`) returns a `stream_url` with a
  signed token; that URL's stream opens (200); the bare `run_id` with no
  token is rejected (401) — the P1 stream-token requirement's actual
  security property, not just its existence.

## What's NOT covered here

Waiting for a run to actually reach a terminal event (report generated,
trust scores computed) needs real `GROQ_API_KEY`/`TAVILY_API_KEY` and can
take anywhere from seconds to tens of seconds depending on the LLM
provider — outside what a "smoke" suite should block on. The stream test
above only proves the authorisation boundary (can you open the stream at
all), not that a full pipeline run completes successfully end to end —
that's `scripts/e2e_demo.py`'s job (backend-only, no browser), already
covering it.
