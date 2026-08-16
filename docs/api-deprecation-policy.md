# API deprecation policy

How a route (or a specific method/field on one) moves from "supported"
to "gone" in TrustChain's HTTP API, and how callers find out before it
happens.

## This is not about the legacy/`/v1` split

[ADR-0005](adr/0005-api-versioning-via-dual-mounted-router.md) mounts
every route twice — unprefixed (legacy) and under `/v1` (canonical) —
pointing at identical handlers, and is explicit that the unprefixed
form has **no deprecation date**; it's a permanent alias, not a
transitional shim. This policy governs deprecating an actual route, a
method on one, or a field within one — not that dual-mount itself.

## Mechanism

`backend/deprecation.py` defines `DEPRECATED_ROUTES`, a list of
`DeprecatedRoute(path, sunset, successor=None, methods=None)` entries.
A middleware in `main.py` (`_deprecation_headers_middleware`, right
after the existing metrics middleware) checks every response's
resolved route template against that list and, on a match, adds:

- `Deprecation: true` — [draft-ietf-httpapi-deprecation-header](https://www.ietf.org/archive/id/draft-ietf-httpapi-deprecation-header-latest.html).
  A bare `true` rather than a second "deprecated since" date next to
  `Sunset`'s date, since most clients that check this header at all
  just check for its presence.
- `Sunset: <IMF-fixdate>` — [RFC 8594](https://www.rfc-editor.org/rfc/rfc8594) —
  the date the route may actually stop working, not the date it was
  announced.
- `Link: <url>; rel="successor-version"` — only when `successor` is
  set, pointing at the direct replacement (per RFC 8594 §6.1's example
  usage). Omitted for a capability removed outright with no 1:1
  replacement.

`DEPRECATED_ROUTES` is **empty today** — nothing in this API is
currently deprecated. The list and this doc's "Active deprecations"
table below are meant to be updated together by hand; each references
the other so they can't silently drift apart.

## Process for deprecating something

1. Add a `DeprecatedRoute` entry to `backend/deprecation.py` with a
   real `sunset` date.
2. Add a matching row to the "Active deprecations" table below:
   route, reason, sunset date, successor (if any), migration notes.
3. Bump `CHANGELOG.md` (or the equivalent release notes for that
   version) noting the deprecation, per
   [`docs/release-process.md`](release-process.md)'s existing
   semver/Conventional-Commits flow.
4. Minimum notice: **90 days** between the `Sunset` date taking effect
   in responses and the route actually being removed or changed in a
   breaking way. This is a policy intention, not something enforced by
   tooling yet — there's no automated check today that a route
   actually stops responding only after its `Sunset` date, or that 90
   days actually elapsed. Being honest about that gap here rather than
   implying more rigor than exists (same spirit as
   [`docs/slo.md`](slo.md)'s "Honest starting point").
5. On the sunset date (or after), remove the route/field and delete
   its `DeprecatedRoute` entry and table row in the same change that
   removes the functionality.

## Automated enforcement: the API compat check

`.github/workflows/test.yml`'s `api-compat-check` job runs on every
push/PR: it generates the current commit's OpenAPI schema
(`backend/scripts/generate_openapi_schema.py`, a pure in-process
introspection — no live server needed) and diffs it against the
previous release's schema using [`oasdiff breaking`](https://github.com/oasdiff/oasdiff)
— specifically the `breaking`-changes check, not a full diff, so
additive changes (a new optional field, a new route) don't fail the
build; only changes that would break an existing caller do (a removed
route/field, a new required request field, a tightened response type,
etc.).

The baseline it diffs against is whatever `openapi.json` is attached to
the immediately-preceding git tag as a release asset —
`.github/workflows/release.yml`'s `publish-openapi-snapshot` job
produces that asset on every tagged release going forward. Two
bootstrap gaps, both handled as a clean skip (not a failure) rather
than silently passing or hard-failing on infrastructure that doesn't
exist yet:

- **No tag exists at all** (true today — this project hasn't cut its
  first release; see `docs/release-process.md`) — the job logs why and
  exits cleanly. It starts actually comparing once the first tag lands.
- **A tag exists but predates this feature**, so it has no `openapi.json`
  asset — same clean-skip handling; only releases from here on carry
  the snapshot.

This job compares against the single most recent tag, not specifically
"the previous minor version" (distinguishing minor-vs-patch bumps would
add real complexity for little benefit at this project's pre-1.0
stabilization stage, where most tags are expected to be patches anyway)
— documented here as a deliberate simplification, in the same spirit as
this doc's other honesty notes.

## How callers see this today

The headers are real and present on affected responses, but **neither
SDK (`sdk/python/`, `sdk/typescript/`) nor the CLI currently reads or
surfaces them** — a caller has to notice the raw `Deprecation`/`Sunset`
response headers themselves (e.g. via browser devtools, curl, or their
own HTTP client's header inspection). Both SDKs already have a single
choke point per language that inspects response headers on every call
(`_raise_for_status()` in `sdk/python/trustchain_sdk/client.py`,
its equivalent in `sdk/typescript/src/client.ts` — both already check
`Retry-After` for 429s), which would be the natural place to add a
`Deprecation`/`Sunset` warning log line. Tracked as a known gap, not
implemented as part of adding the headers themselves.

## Active deprecations

None currently.

| Route | Method(s) | Reason | Sunset date | Successor | Migration notes |
|---|---|---|---|---|---|
| — | — | — | — | — | — |
