# Browser checks

Run from `frontend/`. Install Chromium with `npx playwright install chromium` if necessary.

- `npx playwright test e2e/product.spec.ts`: browser UI checks with explicit API fixtures. No live backend required.
- `npx playwright test e2e/smoke.spec.ts`: real-backend integration at `http://localhost:8000`; requires PostgreSQL, Redis, migrations, and cookie/CORS configuration for `http://localhost:3000`.

Playwright starts or reuses the frontend on port 3000. Run the suites sequentially, or give separate invocations distinct `--output` directories so their trace files do not overwrite one another. Integration tests create unique test accounts and projects. They verify run launch and stream authorization; external model and chain services govern pipeline completion.

Screenshots from product tests are written to `/tmp/trustchain-*.png`. Failure traces are stored in `test-results/`.

For a complete workflow check with real model/search calls and chain writes:

```sh
TRUSTCHAIN_FULL_PIPELINE=1 npx playwright test e2e/pipeline.spec.ts
```

This opt-in test signs up through the UI, launches a task from the dashboard, waits for the final report and score, checks that every recorded step is confirmed, exercises run verification and proof retrieval, and reopens the completed run. Without the environment flag, it is skipped. It requires the API, both MCP servers, deployed V2 contracts, anchor worker, and indexer, in addition to the standard database/Redis services.

The expanded real-service audit is also opt-in:

```sh
TRUSTCHAIN_AUDIT=1 TRUSTCHAIN_AUDIT_EMAIL_LOG=/path/to/console-api.log \
  npx playwright test e2e/audit.spec.ts --workers=1
```

Use an isolated local database and chain with `EMAIL_BACKEND=console`,
`FRONTEND_URL=http://localhost:3000`, and both SDKs installed/built. The audit
reads the console email log to follow real verification, reset, and invitation
tokens. It creates test accounts and scoped keys, exercises both SDKs against
the browser workspace, and tests organizations, agents, alerts, permissions,
downloads, clipboard, pagination, preferences, and concurrent registration.
The final mobile workspace check requires a completed account from
`pipeline.spec.ts` in the same API log. Run the pipeline separately from SDK
suites to avoid consuming the model provider's quota concurrently. Run the
production frontend (`npm run build` then `npm run start`) for release checks.

The audit deliberately asserts correct behavior for known defects; failures
must remain visible until the underlying functionality is corrected. Results
and scope from the November 11, 2026 audit are in
[`docs/e2e-audit-2026-11-11.md`](../../docs/e2e-audit-2026-11-11.md).

The espresso/white/green redesign is covered by `redesign.spec.ts`. It exercises the landing pipeline and live browser SHA-256 example, motion preferences, public routes, keyboard interactions, all workspace layouts at 320/768/1600 pixels, account appearance, scope validation, searches, refreshes, cancellation, and score export. It uses the explicit API fixture in `e2e/fixtures/workspace.ts`; live workflows remain in the audit, smoke, and pipeline suites.

The November 14 redesign report and silent cinematic walkthrough are in [`artifacts/redesign/`](../../artifacts/redesign/README.md). The report preserves the test execution history and distinguishes repeatable fixture screenshots from live-service evidence.
