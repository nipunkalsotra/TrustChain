# Backlog

Deliberately deferred work, tracked here so it doesn't just disappear.

## Cinematic 3D landing page

`frontend/components/landing/` used to hold a full Three.js/React Three
Fiber/Rapier/GSAP/Lenis cinematic landing experience — removed from
`main` (see `docs/implementation-hardening-plan.md` Phase 2) because it
was dormant, unreachable dead code: the real landing route
(`frontend/app/page.tsx`) has rendered the lightweight
`frontend/components/marketing/LandingPage` component instead since
commit `e271dd5`, and the cinematic tree pulled in ~370 KB of dependencies
for a page nothing actually served.

Re-implementing a cinematic 3D landing page is real future work, not
abandoned — just intentionally sequenced after the core product (backend
hardening, auth, evidence publishing) is done, not before. When picked
back up: start from a fresh design pass rather than resurrecting the
deleted code wholesale (it predates several changes — the BRAND-only
`config/content.ts`, the current auth/session model — and would need
re-integrating against both), and land it in its own branch so it can be
reviewed independently of whatever else is in flight at the time.
