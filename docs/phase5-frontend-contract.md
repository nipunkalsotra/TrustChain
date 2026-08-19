# Phase 5 frontend contract

Every endpoint the backend exposes, as of the end of Phase 4 — this is
what a frontend build can rely on existing and working. Endpoints marked
**NEW (Phase 4)** didn't exist before this phase; everything else was
already real and unchanged by it. Every route here is mounted twice —
unprefixed (legacy) and under `/v1` (canonical), pointing at identical
handlers (ADR-0005) — call either; `/v1` is the one to prefer for new
frontend code.

Auth column: `JWT` = human session token only (`Authorization: Bearer
<token>` from signup/login); `JWT or key` = also accepts a project API
key (`tc_live_.../tc_test_...`); `none` = unauthenticated by design.

## Auth & account lifecycle

| Method & path | Auth | Notes |
|---|---|---|
| `POST /auth/signup` | none | Optional `org_name`/`project_name`/`invite_token`. Returns `{token, name, email}`. |
| `POST /auth/login` | none | Credential-stuffing backoff per account+IP. |
| `POST /auth/token-pair` | JWT | Exchanges the primary session JWT for a short-lived access + rotating refresh token pair. |
| `POST /auth/refresh` | none (refresh token in body) | Rotates a refresh token; reuse of an already-rotated one revokes the whole family. |
| `POST /auth/logout` | none (refresh token in body) | Revokes the refresh-token family. |
| `POST /auth/resend-verification` **NEW** | JWT | `{"ok": true, "alreadyVerified": bool}` — a no-op, not an error, once already verified. |
| `POST /auth/verify-email/{token}` **NEW** | none | Token from the verification email. `400 verification_token_invalid` if bad/expired/used. |
| `POST /auth/forgot-password` **NEW** | none | Body `{"email"}`. **Always** `{"ok": true}` — never reveals whether the account exists. |
| `POST /auth/reset-password/{token}` **NEW** | none | Body `{"new_password"}`. Revokes every existing refresh-token session on success. |
| `POST /auth/switch-project` | JWT | Mints a fresh token scoped to a different project the caller already has membership in. |
| `GET /me` | JWT | Every org/role/project the caller belongs to. `user.emailVerified` **NEW** field. |

## API keys (project-scoped machine credentials)

| Method & path | Auth | Notes |
|---|---|---|
| `POST /api-keys` | JWT (admin/owner) | Body `{"scopes": [...], "environment": "live"}`. **Requires `emailVerified: true`** (403 `email_not_verified` otherwise — Phase 4, ADR-0023). `raw_key` shown exactly once. |
| `GET /api-keys` | JWT | Never returns raw keys — `last_four` only. |
| `DELETE /api-keys/{key_id}` | JWT | |

Valid scopes: `logs:write`, `runs:read`, `runs:write`, `agents:register`,
`agents:read`, `alerts:read`.

## Organizations, projects, members, invitations

| Method & path | Auth | Notes |
|---|---|---|
| `GET /orgs` | JWT | Every org the caller belongs to. |
| `POST /orgs` | JWT | Bootstraps a new org; caller becomes owner unconditionally. |
| `GET /orgs/{org_id}` | JWT | |
| `PATCH /orgs/{org_id}` | JWT (admin+) | |
| `DELETE /orgs/{org_id}` | JWT (owner) | Soft delete; blocked if it's the caller's only org. |
| `POST /orgs/{org_id}/transfer-ownership` | JWT (owner) | Atomic promote-target/demote-actor. |
| `GET /orgs/{org_id}/audit-events` | JWT (admin+) | Platform audit log for admin actions. |
| `GET /orgs/{org_id}/projects` | JWT | |
| `POST /orgs/{org_id}/projects` | JWT (admin+) | |
| `GET /projects/{project_id}` | JWT | |
| `PATCH /projects/{project_id}` | JWT (admin+) | |
| `DELETE /projects/{project_id}` | JWT (owner) | Blocked if it's the org's only project. |
| `GET /orgs/{org_id}/members` | JWT | |
| `PATCH /orgs/{org_id}/members/{user_id}` | JWT (admin+) | Change role; rank-limited (can't touch a peer/higher rank). |
| `DELETE /orgs/{org_id}/members/me` | JWT | Self-leave. |
| `DELETE /orgs/{org_id}/members/{user_id}` | JWT (admin+) | Blocked on the last owner. |
| `POST /orgs/{org_id}/invitations` | JWT (admin+) | **Requires `emailVerified: true`** (Phase 4, ADR-0023). Never returns the raw token — it exists only in the sent email. |
| `GET /orgs/{org_id}/invitations` | JWT (admin+) | `?status=pending\|accepted\|revoked\|expired`. |
| `DELETE /orgs/{org_id}/invitations/{invitation_id}` | JWT (admin+) | Revoke. |
| `POST /orgs/{org_id}/invitations/{invitation_id}/resend` | JWT (admin+) | Revoke-then-recreate. |
| `GET /invitations/{token}` | none | Public preview (org name, role, inviter) — one identical 404 for invalid/expired/revoked/accepted, so a scanner learns nothing. |
| `POST /invitations/{token}/accept` | JWT | The already-registered-user accept path (new users use `invite_token` at signup instead). |

## Agent pipeline: runs, streaming, agents, steps

| Method & path | Auth | Notes |
|---|---|---|
| `POST /run-agent` (`POST /v1/runs`) | JWT or key (`runs:write`) | Starts TrustChain's own 4-agent pipeline. Returns immediately; poll or stream. |
| `GET /stream/{run_id}` (`GET /v1/runs/{id}/stream`) | none | SSE — deliberately unauthenticated (browser `EventSource` can't send a header). |
| `GET /runs` | JWT or key (`runs:read`) | Project-scoped run history. |
| `GET /runs/{run_id}` | JWT or key (`runs:read`) | |
| `POST /agents` | JWT or key (`agents:register`) | On-chain identity registration. |
| `GET /agents` | JWT or key (`agents:read`) | `?include_revoked=false`. Read-model, not raw chain events. |
| `GET /agents/{agent_id}/verify` | JWT or key (`agents:read`) | `?code_hash=0x...` |
| `GET /agents/{agent_id}/integrity` | JWT or key (`agents:read`) | |
| `POST /steps` | JWT or key (**`logs:write`** — not `steps:write`) | Third-party SDK ingest. Real HTTP call underneath every `TrustChain.log()`. |
| `GET /steps/{step_id}/proof` | JWT or key (`runs:read`) | 404 until the anchor-worker has actually batched+anchored the step — poll, don't assume immediate. |

## Statistics

| Method & path | Auth | Notes |
|---|---|---|
| `GET /stats` | none | Public platform-wide counts. |
| `GET /audit-log` | JWT or key (`runs:read`) | `?run_id=...` |
| `GET /trust-scores` | JWT or key (`runs:read`) | **Requires `run_id` query param** — 422 without it. |
| `GET /trust-scores/history` | JWT or key (`runs:read`) | `?run_id=...` |
| `GET /leaderboard` | JWT or key (`runs:read`) | `?max_runs=50` |
| `GET /gas-spend` | JWT or key (`runs:read`) | |

## Alerts & notification preferences

| Method & path | Auth | Notes |
|---|---|---|
| `GET /alerts` | JWT or key (`alerts:read`) | `?status=&severity=&alert_type=&project_id=&before_id=&limit=`. Each alert's `evidence` object carries forensic detail — see "Alert evidence shapes" below. |
| `GET /alerts/summary` | JWT or key | Counts by severity/status. |
| `GET /alerts/stream` | JWT or key | SSE — live push (Redis-backed, from-now-only; call `GET /alerts` on load for full history first). |
| `GET /alerts/{alert_id}` | JWT or key | Includes `deliveries` (per-recipient send status) in addition to `evidence`. |
| `POST /alerts/{alert_id}/acknowledge` | JWT (admin+) | Body `{}`. |
| `POST /alerts/{alert_id}/resolve` | JWT (admin+) | Body `{"resolution_note"}`. |
| `POST /alerts/{alert_id}/reopen` | JWT (admin+) | |
| `GET /me/notification-preferences` | JWT | Per-org preferences (own only). |
| `PUT /me/notification-preferences` | JWT | |

### Alert evidence shapes

Every `alert.evidence` is a free-form object whose shape depends on
`alert.alertType` — the frontend should render it defensively (unknown
keys ignored, not an error). The two Phase 4 shapes worth knowing about
specifically:

- **`step_row_tampered`** (an edited row): `editedByOperator`,
  `editedByDbRole`, `changedColumns` (array), `oldOutputHash`/
  `newOutputHash` (and/or `oldInputHash`/`newInputHash`,
  `oldLeafHash`/`newLeafHash` — only present for columns that actually
  changed).
- **`step_missing`** (a deleted row, still referenced by an anchored
  batch): `missingStepIds` (array), `expectedCount`, `foundCount`, and
  **`deletionForensics`** — an object keyed by step id (as a string) to
  that step's own `{editedByOperator, editedByDbRole, whatHappened,
  deletedAtUnix, editedFromClientAddr}` — a genuinely different shape
  from the edit case, not just a different `alertType` string.

## Integrity & verification

| Method & path | Auth | Notes |
|---|---|---|
| `GET /integrity/status` | JWT or key | Coverage/health of the active project's continuous verification. |
| `POST /integrity/verify-run/{run_id}` | JWT or key (`runs:read`) | Synchronous, on-demand — runs every detector against one run outside the sweep loop. |
| `POST /integrity/verify-content` **surfaced via SDK/CLI in Phase 4** | JWT or key (`runs:read`) | Body `{"stepId", "field": "input"\|"output", "candidateText"}`. Never sends/stores real content — hashes the candidate and compares. `matchesOriginal` is `null` (not `false`) when there's no edit history to compare against at all. Rate-limited (6/min). |

## Legacy V1 (read-only demo surface, do not build new UI against)

| Method & path | Auth | Notes |
|---|---|---|
| `POST /verify` | none | |
| `GET /verify/tamper-demo` | none | |
| `GET /verify-audit` | none | |
| `GET /chain-status` | none | |
| `GET /health` | none | V1 bridge check. **Phase 4:** `{"status": "not_configured"}` (200) when `PRIVATE_KEY` is simply unset — not the same as a real failure (still a genuine 503 `bridge_unavailable`). Use `/ready` for actual readiness gating, not this. |

## Operational (not for UI)

| Method & path | Auth |
|---|---|
| `GET /ready` | none |
| `GET /metrics` | none (Prometheus scrape format) |

## Error response shape

Every `ApiError`-raised failure (most 4xx/5xx from this API) has this
JSON body, always:

```json
{"detail": "human-readable message", "error_code": "machine_readable_snake_case_code"}
```

`error_code` is the stable field to branch UI logic on — `detail`'s
exact wording isn't a contract. A small number of pre-`ApiError`
routes/framework-level errors (a 422 from Pydantic validation, a 404
from an unmatched route) have no `error_code` field at all — treat its
absence as "generic/unclassified failure," not a bug.
