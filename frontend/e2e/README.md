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
