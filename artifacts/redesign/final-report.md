# TrustChain frontend redesign — final report

**14 November 2026 · Local production build · Espresso / warm white / green**

The landing page, account screens, and complete multi-page workspace now share the new visual system. The latest applicable executions pass all **30 distinct Playwright scenarios**, **20 frontend unit tests**, and **7 focused backend regressions**. The production build, TypeScript compilation, ESLint, and whitespace checks pass.

## What changed

- Rebuilt the landing page around “Intelligence, with a proof”: editorial typography, an animated dimensional evidence object, orbital paths, scrolling reveals, interactive pipeline stages, a working SHA-256 integrity demonstration, developer integration content, and clear account/workspace actions.
- Redesigned the entire workspace: overview, agent runs and run details, agent registry, trust scores, audit trail, verification studio, on-chain anchors, alerts, team, API keys, settings, and help.
- Applied the same palette to sign-in, signup, password recovery, email verification, invitation acceptance, pricing, coming-soon, and not-found screens.
- Added espresso surfaces, warm white text, green actions and indicators, new typography, form focus treatments, dialog entrances, animated charts, page transitions, and responsive layouts.
- Made dark mode the default while preserving a saved light-mode preference. The complementary light theme uses warm white, brown, and green.
- Added a persistent motion control, system reduced-motion support, keyboard-operable landing tabs, and a named workspace search control. Mobile tables scroll inside their panels.
- Improved final reports with readable headings, emphasis, and safe clickable HTTP(S) source links. React escapes raw HTML; no arbitrary HTML is inserted.

## Functional verification

Tests ran in Chromium against the production frontend on `localhost:3000`. Real-service checks used FastAPI, PostgreSQL, Redis, both MCP servers, the configured model/search providers, Anvil chain 31337, the anchor worker, indexer, and integrity watchdog. A fresh database separated this work from previous audit data. Console email supplied real single-use verification, recovery, and invitation tokens.

| Area | Verified behavior |
| --- | --- |
| Landing and public pages | CTAs, all four pipeline stages, keyboard tab switching, SHA-256 alteration/restoration, motion persistence, mobile menu, pricing/coming-soon/404 return navigation |
| Authentication | Signup, login, logout, HttpOnly session cookies, stale-cache rejection, email verification links, reset links, password mismatch and visibility, token reuse rejection, invalid tokens, automatic access-cookie refresh |
| Organizations and projects | Create and rename, project/environment selection, workspace switching, persistence after reload |
| Agent workflows | Launch from the dashboard, live/persisted activity, report, score, source links, confirmed evidence, reopened report, failed/unknown-run states, unsigned-stream rejection |
| Agents and SDKs | UI registration, TypeScript and Python SDK logging, registry search, fingerprint match/mismatch, four concurrent registrations |
| Evidence | Audit search and anchor-status filtering, pagination, JSON exports, step proof retrieval/export, run integrity checks, matching and altered content checks, active chain identity |
| Alerts | Actual identity drift, severity filtering, evidence inspection, acknowledgement, resolution, reopening, investigation notes |
| Team and access | Invitations, acceptance, resend/revoke, role changes, viewer restrictions, member removal and immediate loss of access, team search |
| API keys | Scope selection, empty-scope prevention, one-time display, clipboard, SDK use, revocation and HTTP 401 rejection |
| Preferences | All notification switches persist; account appearance toggle, saved theme, recovery link |
| Navigation and layouts | All workspace headings, command search and keyboard shortcut, dialog Escape, mobile navigation, five legacy redirects; repeatable fixture layouts at 320, 390, 768, and 1600 pixels, plus real-data mobile checks |
| Smaller controls | Refresh actions, empty run validation, cancellation, score downloads, empty-search results |

| Check | Latest result |
| --- | --- |
| Production build and TypeScript | Passed |
| ESLint | Passed |
| Frontend unit tests | 20 passed |
| Playwright UI / design scenarios | 12 passed, including the supplemental controls check |
| Playwright real-service audit | 13 passed |
| Playwright live smoke scenarios | 4 passed |
| Playwright complete pipeline | 1 passed |
| Source/report regressions | 7 passed |
| `git diff --check` | Passed |

**How the count is calculated:** the consolidated production browser run passed 28/29 scenarios. Its source-link failure was corrected and the affected complete pipeline passed a focused rerun. The supplemental controls scenario also passed. This gives 30 unique passing scenarios on their latest applicable executions; earlier diagnostic attempts are not counted again. See [structured verification results](verification-results.json).

## Defects found and corrected

The model could omit source URLs when synthesizing research and generating the final report. The original search tool URLs now travel separately through the pipeline state, so the final report retains actual source links even when model prose omits them. The report also no longer hardcodes a claim that all steps are already confirmed on Monad: it accurately distinguishes durable recording, queued anchoring, and confirmed chain receipts. These changes happen before the final report is recorded and hashed.

The local test chain was restarted from a fresh state, so the tracked local bootstrap addresses were refreshed from Foundry's actual deployment receipt. No production chain or production database was modified.

Test-harness fixes included separate output directories, a selector scoped to application error messages, assertions updated for the intentional default dark theme, and a reopened-report assertion adapted to rendered formatting.

## Real pipeline evidence

- Run: `run_20261114_120724_cc77ccd2`
- Final score: 85/100; report length: 2,128 characters.
- All 10 recorded steps confirmed on local chain 31337.
- Run integrity verification passed and the persisted report reopened.
- Proof retrieved for step 94 at block 96, transaction `0xb33702d2a5b35619d87e7bc0bdff7ce59a1b336c874b6e457bd57444fcbfb15d`.

[Completed run](screenshots/live-completed-run.png) · [Run verification](screenshots/live-run-verification.png) · [Confirmed proof](screenshots/live-step-proof.png)

## Silent cinematic demo

[TrustChain — Cinematic Product Tour](TrustChain-Cinematic-Product-Tour.mp4)

**5:26 · 1920×1080 · 30 fps · H.264 MP4 · zero audio tracks.** The file has streaming-friendly fast-start metadata; its SHA-256 digest and chapter timing are in the accompanying metadata.

The film covers the landing page and interactive demonstration, project creation, a real research run, report and scores, audit filtering/export, chain receipts, Merkle proof retrieval, run verification, scoped API keys, real SDK integration, fingerprint checks, content verification, alert investigation, team invitations, preferences, and key revocation.

Presentation overlays add chapter labels, an eased cursor, click ripples, animated field-note cards, and reading/progress indicators. Application results are real. The disposable integration key is masked and revoked. The original supplied reference video is preserved.

[Chapter list](cinematic-tour-chapters.md) · [Video metadata](cinematic-tour-metadata.json) · [Reproduction instructions](README.md)

## Scope and limits

This is a local integration result, not a production deployment certification. Chromium was tested; Firefox and WebKit were not. External email delivery, public RPC reliability, production TLS/cookie settings, and optional external evidence publishing were not exercised. Pricing remains explicitly unpublished rather than inventing commercial terms. A confirmed cryptographic record proves correspondence to recorded evidence; it does not establish the factual correctness of a model's answer. The browser scenarios cover the implemented user workflows and representative failure states, not every possible input or concurrency schedule.

Fixture screenshots are labeled by filename in the README; the live evidence screenshots and film use real services. Raw console logs and tokens remain under `/tmp/trustchain-design` and are not part of the deliverables.
