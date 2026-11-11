# TrustChain fixes and final validation — November 11, 2026

The defects identified in the earlier audit have been corrected. The latest applicable results are green: 24 distinct Playwright scenarios, 411 backend tests, 49 Python SDK/CLI tests, 31 TypeScript SDK tests, and 20 frontend unit tests passed. Three existing backend tests remain explicitly skipped; none is counted as a pass. The frontend dependency audit reports zero vulnerabilities.

## Changes delivered

- Verification and reset emails now link to the implemented auth routes. Alert and notification-preference email links now open dashboard routes, and the preference query opens the Notifications tab.
- A PostgreSQL transaction advisory lock, keyed by chain and signer address, coordinates nonce allocation through confirmation across identity writes, score writes, and anchor submissions. The existing worker leadership lock remains separate. Four concurrent SDK registrations now all return confirmed receipts.
- MCP results are normalized from structured mappings, JSON strings, or text content blocks before research and fact-check code accesses them. Malformed payloads still fail explicitly. Added parser regressions and verified the real pipeline without the previous list/mapping exception.
- The chain-status endpoint reads the V2 evidence connection. The anchors page now reports the working local chain and fits a 390-pixel viewport with real evidence.
- Updated Next.js to 16.4.6, React to 19.3.1, and matching lint dependencies. Removed the unused shadcn scaffolding CLI from runtime dependencies, applied compatible security updates, and pinned a patched minimatch range within the TypeScript lint dependency tree. Installed frontend dependency findings fell from 28 to zero.
- The TypeScript test command now reports individual test cases under the local Node runner. Ambiguous browser selectors are scoped to the intended heading. Live SDK tests allow five minutes for external-service work while retaining their terminal-event assertions. Model calls use a 2,048-token budget and two retries, with low reasoning effort for GPT-OSS models. An empty final report now fails explicitly instead of marking the run complete.

## Final validation

| Check | Latest result |
| --- | --- |
| Playwright | 24 distinct scenarios passed, 0 failed |
| Full backend suite | 411 passed, 3 skipped, 0 failed |
| Python SDK and CLI | 49 passed, 0 skipped, 0 failed |
| TypeScript SDK | 31 passed, 0 skipped, 0 failed |
| Frontend unit tests | 20 passed |
| Production build, TypeScript, ESLint | Passed |
| Changed Python modules, Ruff | Passed |
| Solidity contract suite | Passed |
| Frontend npm audit | 0 vulnerabilities at every severity |
| Git whitespace checks | Passed |

Browser results include the complete real pipeline, email-link navigation, verification/reset, organizations/projects, invitations and roles, session refresh, SDK-to-browser activity, confirmed proofs, content verification, downloads, clipboard, search, pagination, notification persistence, real integrity alerts, concurrent registration, and mobile pages.

The first full browser rerun passed 22 of 23 scenarios and exposed an ambiguous assertion for two legitimate New run buttons. That assertion was scoped correctly and its complete invitation workflow passed on rerun. The separate full-pipeline scenario also passed. Thus the latest applicable outcomes are 24/24, assembled from the full run and targeted correction; this is not presented as one unbroken 24-test invocation. Earlier failed and interrupted diagnostic runs remain in the temporary audit logs.

The three backend skips cover an externally managed database outage scenario, a pre-existing root-mismatch integration placeholder, and the unavailable missing-boto3 branch when boto3 is installed.

A configurable GROQ_MODEL now keeps the selected provider model and agent identity hashing aligned. The default remains openai/gpt-oss-120b, used for the verification suites. Those suites exhausted that model’s daily token allowance before recording; the demonstration uses the same Groq account’s available openai/gpt-oss-20b model with real requests. The failed first recording was discarded.

## Video and evidence

The replacement **5 minute 10 second silent product tour** is [available as an MP4](../artifacts/demo/TrustChain-Silent-Product-Tour.mp4). It follows the supplied ACTL reference with the native dark application, a visible moving cursor, click indicators, and small fading instructional callouts. The export is 1920×1080 H.264 at 30 fps, with **no audio stream**. The 1600×900 browser recording is scaled to the export resolution; application actions are not sped up.

The walkthrough starts in a verified disposable workspace and demonstrates project creation, a real model/search run and report, trust scores, audit filtering/export, ten confirmed pipeline steps, Merkle proofs, run integrity checks, real TypeScript SDK registration/logging, a deliberate identity mismatch, content verification, alert investigation/resolution, a viewer invitation, notification preferences, and key revocation. The same revoked credential returns HTTP 401. Blockchain activity uses local Anvil chain 31337; invitations use console email.

During the replacement recording, a real GPT-OSS response consumed its previous output allowance without returning report text. The budget/reasoning adjustment and explicit empty-report guard above address this case. The reporter and MCP regression checks passed **4/4**, and the replacement walkthrough then completed with a 1,325-character report and ten confirmed steps. This targeted verification was performed after the earlier full-suite results; the full suite was not rerun for this recording adjustment.

- [Silent tour chapters](../artifacts/demo/silent-tour-chapters.md)
- [Media metadata and verified recording events](../artifacts/demo/silent-tour-metadata.json)
- [Silent recording script](../scripts/record_product_demo.mjs) and [silent encoding script](../scripts/render_product_demo.py)

- [Final structured results](audits/2026-11-11/final/results.json)
- [Final npm audit](audits/2026-11-11/final/npm-audit.json)
- [Backend XML results](audits/2026-11-11/final/fixed-backend.xml)
- [Python SDK/CLI XML results](audits/2026-11-11/final/final-python-sdk.xml)
- [Original audit and historical failures](e2e-audit-2026-11-11.md)
- [Browser regressions](../frontend/e2e/audit.spec.ts)

## Scope and remaining deployment work

These results validate the local stack, using real Groq/Tavily requests, PostgreSQL, Redis, and V2 contracts on Anvil chain 31337. Email is captured by the console backend. They do not certify production TLS/cookies, external email delivery, public-chain availability, optional IPFS publishing, or other browser engines. The three skips remain visible. Provider quota still limits throughput; the local fixes do not increase the account's external allowance. Generated research and trust scores are not guarantees of factual accuracy.

No public deployment, repository push, or Drive upload is included. The MP4 is prepared locally for the requested Drive upload.
