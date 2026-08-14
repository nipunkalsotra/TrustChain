# 0010 — JWT issuer/audience claims

**Status:** Accepted

## Context

Session JWTs (`auth.py::create_token`) and short-lived access tokens
(`refresh.py::_short_lived_access_token`) were both signed with
`JWT_SECRET` and validated on decode only by signature and expiry — no
`iss` (issuer) or `aud` (audience) claim was set or checked. A token
minted for a different TrustChain deployment or environment that
happened to share the same `JWT_SECRET` (e.g. a staging secret reused
by accident, or a secret rotated in one place but not another) would be
accepted as valid here with nothing catching the mismatch.

## Decision

Both token-issuing paths set `iss`/`aud` (`Settings.jwt_issuer`/
`jwt_audience`, defaulting to `"trustchain-api"`/`"trustchain-clients"`);
`auth.decode_token` — the single decode path both `get_current_user` and
`get_current_principal` funnel through — validates both on every decode
via PyJWT's own `issuer=`/`audience=` parameters.

## Alternatives considered

- **Leave `iss`/`aud` unset.** Rejected — closing this specific,
  explicitly-flagged gap was the point; the marginal cost (two extra
  config fields, two extra claims) is low relative to what it catches.
- **Make `jwt_issuer`/`jwt_audience` required, no default**, matching
  `jwt_secret`'s own "fail closed if unset" pattern. Rejected: unlike
  `jwt_secret` (where a random per-process fallback is actively
  dangerous — see `auth.py`'s module docstring), a stable, sensible
  default for `iss`/`aud` doesn't have an equivalent failure mode
  worth forcing every existing deployment to explicitly configure.

## Consequences

- Every existing token issued before this change fails validation
  after it ships (no `iss`/`aud` claim present) — every active session
  is invalidated, forcing re-login. Acceptable for this project's
  current stage (no real user base with sessions worth preserving
  across the change); a production rollout with real sessions would
  need either a transition window (accept tokens with or without the
  claims for some period) or an explicit "log everyone out" migration
  note, neither of which exists here.
- `refresh.py`'s short-lived access token and `auth.py`'s primary
  session token both had to be updated in lockstep — they're two
  separate `jwt.encode` call sites signing with the same secret; a
  future third token-issuing path would need to remember the same
  claims or `decode_token` would reject it.
