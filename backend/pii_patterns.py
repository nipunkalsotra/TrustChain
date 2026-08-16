"""
pii_patterns.py — conservative, low-false-positive detection of
PII-shaped content in text about to be hashed into an anchor payload
(agents/base.py::log_step) or reviewed by static analysis
(scripts/check_anchor_payload_pii.py, run in CI's backend job).

Deliberately detection-only, never redaction or rejection: log_step
hashes exactly what a caller submitted (the run's task text, a
POST /steps body, LLM output) — mutating or blocking that content
before hashing would mean the stored hash no longer matches what the
caller (or anyone independently verifying a proof — see
docs/architecture.md's hash/content-separation note, and
sdk/*/README.md's verify_proof) can recompute from their own copy of
the original text. That breaks this project's entire "verify without
trusting our database" premise for every step, not just the ones that
happened to match a PII pattern — a correctness regression far worse
than the privacy exposure this module exists to surface. Detection
gives operators real visibility (a metric + a structured log line, see
agents/base.py::log_step) into something that's actually fixable —
redesigning which agent prompts/outputs get anchored — without
silently corrupting verification or rejecting a legitimate task just
because it mentions an email address in passing ("look up
alice@example.com's public GitHub profile" is not a privacy incident).

Scoped to email addresses only, not phone numbers/SSNs/credit cards —
those patterns are far more prone to matching ordinary numeric content
(a step_index, a trust score, a dollar amount) than genuine PII, and a
noisy detector that operators learn to ignore is worse than a narrower
one that's actually trustworthy when it fires.
"""

import re
from typing import NamedTuple


class PiiMatch(NamedTuple):
    kind: str
    match: str


_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def find_likely_pii(text: str) -> list[PiiMatch]:
    """Returns every email-shaped substring found in `text`, empty if
    none. Cheap enough to run on every log_step call (a single regex
    pass over strings that are already capped at 100_000 chars by
    LogStepRequest's own field validation, main.py's LogStepRequest)."""
    if not text:
        return []
    return [PiiMatch("email", m.group(0)) for m in _EMAIL_RE.finditer(text)]
