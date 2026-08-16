"""
scripts/check_anchor_payload_pii.py — static CI check for backend/'s own
anchor-payload builders (every `log_step(...)` call site — see
agents/base.py's module docstring: it's the sole chokepoint every step
funnels through before its input_text/output_text gets hashed for
on-chain anchoring).

Scope, deliberately narrow: this catches a FUTURE developer mistake —
someone adding a new log_step() call that passes an attribute/variable
whose name obviously suggests raw PII (user.email, a `password`
variable, etc.) as input_text/output_text. It does NOT and CANNOT catch
PII a caller freely types into a run's task text or a POST /steps body
— that's arbitrary user-supplied free text, not something static
analysis over this repo's own source can see. That risk is handled
separately, at runtime, by pii_patterns.py's find_likely_pii (wired
into log_step itself) — detection-only there too, for the same
verification-breaking reason explained in that module's docstring:
this script and that runtime check are deliberately NOT the same
mechanism, because a static check can safely be blocking (it only ever
fires on code a human just wrote and can fix before merging) while a
runtime check on arbitrary user data cannot (it would either mutate an
anchored hash out from under its own verification story, or reject
legitimate requests on false positives).

Run from backend/:
    python3 scripts/check_anchor_payload_pii.py

Exits 1 (and prints every finding) if any log_step() call's
input_text/output_text argument looks like it's built from a
PII-suggestively-named attribute or variable; 0 otherwise. Wired into
.github/workflows/test.yml's backend job, same blocking style as the
Bandit step there.
"""

import ast
import sys
from pathlib import Path
from typing import NamedTuple, Optional

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Directories under backend/ that never define a real log_step call
# site worth checking — tests exercise log_step with deliberately fake
# data (including strings LOOKING like PII, to test detection itself),
# alembic/versions is generated migration boilerplate, and .venv is
# third-party code this repo doesn't own.
_EXCLUDED_DIR_NAMES = {"tests", ".venv", "__pycache__", "alembic"}

# Substrings that suggest a name refers to real PII rather than, say,
# a step's own agent_id/action/run_id (all fine to anchor — see
# docs/architecture.md's data model section). Matched case-insensitively
# against the final attribute/variable name only (not the whole
# expression), so `user.email` and `EMAIL_ADDRESS` both match. A name
# merely containing one of these substrings is still worth a human's
# eyes on that log_step() call site even on a false positive — the
# cost of reviewing a rare false positive is far lower than the cost of
# an actual PII string reaching an immutable anchor.
_SUSPICIOUS_NAME_SUBSTRINGS = (
    "email", "password", "passwd", "ssn", "social_security",
    "phone", "credit_card", "creditcard", "cvv",
    "date_of_birth", "dob", "home_address", "street_address",
)

# log_step(bridge, agent_id, action, input_text, output_text, step_index, run_id, trust_score=0)
_TARGET_POSITIONAL_INDICES = {3: "input_text", 4: "output_text"}
_TARGET_KEYWORD_NAMES = {"input_text", "output_text"}


class Finding(NamedTuple):
    file: str
    line: int
    param: str
    name: str


def _leaf_name(node: ast.AST) -> Optional[str]:
    """The final identifier a value expression resolves to, for the
    purposes of this check — `user.email` -> "email", a bare `password`
    Name -> "password", an f-string embedding `{user.email}` -> "email"
    (checked via the JoinedStr branch below). Returns None for anything
    else (a literal, a function call with no suspicious leaf, etc.) —
    this check flags NAMES, not arbitrary expressions, to stay a fast,
    low-noise lint rather than a full data-flow analysis."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.JoinedStr):  # f-string
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                leaf = _leaf_name(value.value)
                if leaf is not None:
                    return leaf
    return None


def _is_suspicious(name: str) -> bool:
    lowered = name.lower()
    return any(substr in lowered for substr in _SUSPICIOUS_NAME_SUBSTRINGS)


def _check_call(call: ast.Call, file_rel: str) -> list[Finding]:
    findings = []
    for index, arg in enumerate(call.args):
        param = _TARGET_POSITIONAL_INDICES.get(index)
        if param is None:
            continue
        leaf = _leaf_name(arg)
        if leaf and _is_suspicious(leaf):
            findings.append(Finding(file_rel, call.lineno, param, leaf))
    for kw in call.keywords:
        if kw.arg not in _TARGET_KEYWORD_NAMES:
            continue
        leaf = _leaf_name(kw.value)
        if leaf and _is_suspicious(leaf):
            findings.append(Finding(file_rel, call.lineno, kw.arg, leaf))
    return findings


def check_file(path: Path) -> list[Finding]:
    source = path.read_text()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    file_rel = str(path.relative_to(BACKEND_ROOT))
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "log_step":
            findings.extend(_check_call(node, file_rel))
    return findings


def check_all() -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        if any(part in _EXCLUDED_DIR_NAMES for part in path.relative_to(BACKEND_ROOT).parts[:-1]):
            continue
        findings.extend(check_file(path))
    return findings


def main() -> int:
    findings = check_all()
    if not findings:
        print("check_anchor_payload_pii: no suspicious log_step() arguments found")
        return 0
    print(f"check_anchor_payload_pii: {len(findings)} suspicious log_step() argument(s) found:")
    for f in findings:
        print(f"  {f.file}:{f.line} — {f.param}= looks like it's built from `{f.name}`")
    print(
        "\nIf this is a real PII field flowing into an anchor payload, redesign what "
        "gets anchored instead of passing it through — see agents/base.py's log_step "
        "docstring and pii_patterns.py's module docstring for why this can't be fixed "
        "by redacting/truncating it after the fact. If this is a false positive (the "
        "name just happens to contain a flagged substring), rename the variable or add "
        "it as an explicit exception in this script's _SUSPICIOUS_NAME_SUBSTRINGS."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
