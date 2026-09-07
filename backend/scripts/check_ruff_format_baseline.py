#!/usr/bin/env python3
"""
check_ruff_format_baseline.py — blocking `ruff format --check` against a
committed baseline, not the full 117-file backlog.

This repo predates ruff/black; reformatting all 117 currently-non-
compliant files in one shot is a real, one-time, whole-repo formatting
commit CLAUDE.md's own convention deliberately keeps SEPARATE from
routine work (a mass mechanical diff bundled into an unrelated change
makes that change much harder to review, even though ruff format is
behavior-preserving). Until that commit happens, THIS script is the
actual CI gate (see .github/workflows/test.yml's "ruff format" step):
any file already in .ruff-format-baseline.txt is allowed to still be
non-compliant; any file NOT in that list must be. A new file, or an
edit to an existing compliant file that de-formats it, fails CI
immediately — the backlog can only shrink (by editing the baseline file
by hand after actually reformatting something) or stay flat, never grow
silently.

Usage: python3 scripts/check_ruff_format_baseline.py
Exit 0: no new violations. Exit 1: prints exactly which file(s) are new.
"""

import pathlib
import subprocess
import sys

BASELINE_PATH = pathlib.Path(__file__).parent.parent / ".ruff-format-baseline.txt"


def _current_violations() -> set[str]:
    result = subprocess.run(
        ["ruff", "format", "--check", "--diff", "."],
        cwd=BASELINE_PATH.parent,
        capture_output=True,
        text=True,
    )
    return {line.removeprefix("--- ") for line in result.stdout.splitlines() if line.startswith("--- ")}


def main() -> int:
    baseline = {line.strip() for line in BASELINE_PATH.read_text().splitlines() if line.strip()}
    current = _current_violations()
    new_violations = sorted(current - baseline)

    if new_violations:
        print("ruff format: new violation(s) not in the baseline:", file=sys.stderr)
        for path in new_violations:
            print(f"  {path}", file=sys.stderr)
        print(
            f"\nRun `ruff format {' '.join(new_violations)}` to fix, or "
            f"(if this file is genuinely a pre-existing one you're only touching "
            f"incidentally) add it to {BASELINE_PATH.name}.",
            file=sys.stderr,
        )
        return 1

    fixed = sorted(baseline - current)
    if fixed:
        print(
            f"Note: {len(fixed)} baseline file(s) are now compliant and could be "
            f"removed from {BASELINE_PATH.name}: {', '.join(fixed)}"
        )

    print(f"ruff format: no new violations ({len(current)}/{len(baseline)} baseline files still non-compliant)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
