#!/usr/bin/env python3
"""
check_mypy_baseline.py — blocking mypy against a committed baseline, not
the full pre-existing backlog. Same shape and reasoning as
check_ruff_format_baseline.py — see that script's docstring for the full
"why a baseline instead of either fixing everything or staying non-
blocking forever" argument; this applies it to mypy's 82 pre-existing
(file, message) pairs (103 raw error occurrences — several messages
repeat at different line numbers within the same file, collapsed here;
see below) instead of ruff format's 117 files.

Keyed on (file, message) WITHOUT the line number deliberately: an
unrelated edit earlier in a file shifts every error below it to a new
line number, which would otherwise make this script cry "new violation"
for an error that's actually unchanged, just relocated — pure noise
divorced from whether anything got worse. The real risk this key
accepts: if the exact same error message legitimately recurs at a new
line in a file that already has it once, this won't catch that as
"new" (it's already an ok entry). Weighed against the alternative
(constant false-positive CI failures from routine nearby edits), that's
the right tradeoff for a baseline whose whole point is "did something
NEW and DIFFERENT break," not exact occurrence-counting.

Usage: python3 scripts/check_mypy_baseline.py
Exit 0: no new violations. Exit 1: prints exactly which new (file,
message) pair(s) appeared.
"""

import pathlib
import re
import subprocess
import sys

BASELINE_PATH = pathlib.Path(__file__).parent.parent / ".mypy-baseline.txt"
_ERROR_LINE_RE = re.compile(r"^([a-zA-Z0-9_/.]+\.py):(\d+): (error: .+)$")


def _current_violations() -> set[str]:
    result = subprocess.run(
        ["mypy", "--ignore-missing-imports", "--explicit-package-bases", "--exclude", "tests/", "."],
        cwd=BASELINE_PATH.parent,
        capture_output=True,
        text=True,
    )
    violations = set()
    for line in result.stdout.splitlines():
        m = _ERROR_LINE_RE.match(line)
        if m:
            file, _line_no, message = m.groups()
            violations.add(f"{file}: {message}")
    return violations


def main() -> int:
    baseline = {line.strip() for line in BASELINE_PATH.read_text().splitlines() if line.strip()}
    current = _current_violations()
    new_violations = sorted(current - baseline)

    if new_violations:
        print("mypy: new violation(s) not in the baseline:", file=sys.stderr)
        for entry in new_violations:
            print(f"  {entry}", file=sys.stderr)
        print(
            f"\nFix the underlying type issue, or (if this is a genuinely "
            f"pre-existing finding this change just happened to surface — "
            f"e.g. touching a file mypy already couldn't fully type) add it "
            f"to {BASELINE_PATH.name}.",
            file=sys.stderr,
        )
        return 1

    fixed = sorted(baseline - current)
    if fixed:
        print(
            f"Note: {len(fixed)} baseline finding(s) no longer reproduce and could be "
            f"removed from {BASELINE_PATH.name}: {', '.join(fixed)}"
        )

    print(f"mypy: no new violations ({len(current)}/{len(baseline)} baseline findings still present)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
