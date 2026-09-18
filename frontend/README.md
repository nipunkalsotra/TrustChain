# TrustChain frontend

Next.js 16 / React 19 product UI. The public landing page is preserved in `components/marketing`; the workspace lives in `components/product` and `components/dashboard`.

## Run locally

```sh
cd frontend
npm install
cp .env.example .env.local  # only if you do not already have one
npm run dev
```

Open http://localhost:3000. Set `NEXT_PUBLIC_API_URL` to the backend origin (defaults to http://localhost:8000). The backend must allow the frontend origin in CORS and use the appropriate cookie settings. Use the same hostname for both services in local development (`localhost`, not a mix of `localhost` and `127.0.0.1`).

Start backend dependencies and apply the repository's database migrations before testing real accounts. See the root `start.sh` and `docker-compose.yml` for the complete stack. Blockchain registration, anchoring, and pipeline execution additionally require configured contracts, workers, signing keys, and model/search providers. The UI reports unavailable services and failed runs; it does not substitute demo data.

## Product routes

- `/auth`: sign in, or `/auth?mode=signup` to create an account.
- `/auth/forgot-password`, `/auth/reset-password/[token]`, `/auth/verify-email/[token]`: account recovery and verification.
- `/invite/[token]`: invitation preview, signup, and acceptance.
- `/dashboard`: overview and recent activity.
- `/dashboard/runs` and `/dashboard/runs/[runId]`: launch workflows, inspect activity, and read reports.
- `/dashboard/agents`: register configuration hashes and verify agent identities.
- `/dashboard/audit`, `/dashboard/proofs`, `/dashboard/anchors`: inspect audit entries, verify runs/content, and export Merkle proofs.
- `/dashboard/alerts`: organization findings, acknowledgment, resolution, and reopening.
- `/dashboard/trust-scores`: project agent score comparison.
- `/dashboard/team`, `/dashboard/keys`, `/dashboard/settings`: memberships, scoped API keys, projects, organizations, and preferences.
- `/dashboard/help`: built-in onboarding guide.

Only the public Docs and Pricing links remain coming-soon destinations. Legacy product paths redirect to the corresponding workspace pages.

Authentication uses the backend's HttpOnly session cookies, CSRF protection, and a shared refresh request for concurrent expired requests. `GET /me` gates workspace rendering; local storage holds display information and theme only. Role and email-verification restrictions are reflected in the UI and enforced by the backend.

## Checks

```sh
npm run lint
npm run test
npm run build
npx playwright test e2e/product.spec.ts
npx playwright test e2e/smoke.spec.ts
```

`product.spec.ts` uses explicit API fixtures to exercise populated views, error states, actions, responsive navigation, and both themes. `smoke.spec.ts` uses the real backend for account creation, cookie authentication, workspace reads, project creation/switching, persisted preferences, logout/login, and signed run launch. It creates uniquely named test accounts and a project. Pipeline completion depends on configured external providers.

The production build uses Next.js's webpack builder because Turbopack's CSS worker failed to bind its subprocess port in the development environment. The development server continues to use Turbopack.
