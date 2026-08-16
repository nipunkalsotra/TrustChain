"""
Tests for pii_patterns.py (runtime email detection, wired into
agents/base.py::log_step) and scripts/check_anchor_payload_pii.py (the
static CI check over log_step() call sites) — see each module's
docstring for why these are two deliberately separate mechanisms.
"""

from pathlib import Path

from pii_patterns import find_likely_pii
from scripts.check_anchor_payload_pii import BACKEND_ROOT, check_all, check_file


def test_find_likely_pii_detects_a_real_email():
    matches = find_likely_pii("please contact alice.researcher@example.com about this")
    assert len(matches) == 1
    assert matches[0].kind == "email"
    assert matches[0].match == "alice.researcher@example.com"


def test_find_likely_pii_detects_multiple_emails():
    matches = find_likely_pii("cc bob@example.org and carol@example.net")
    assert {m.match for m in matches} == {"bob@example.org", "carol@example.net"}


def test_find_likely_pii_finds_nothing_in_ordinary_text():
    assert find_likely_pii("Summarize the top 3 AI startups in India") == []


def test_find_likely_pii_handles_empty_string():
    assert find_likely_pii("") == []


# ── Static CI check over log_step() call sites ─────────────────────────────

def test_check_all_finds_nothing_in_the_real_current_codebase():
    """The load-bearing regression test: every real log_step() call site
    in this repo today passes clean (non-PII-named) arguments. A future
    call site that passes something like `user.email` as input_text/
    output_text should make THIS test fail, exactly like it would fail
    CI (see check_anchor_payload_pii.py's own __main__, wired into
    .github/workflows/test.yml's backend job)."""
    findings = check_all()
    assert findings == [], f"unexpected suspicious log_step() arguments: {findings}"


def test_check_file_detects_a_suspicious_keyword_argument():
    # BACKEND_ROOT-relative, not an arbitrary tmp path — check_file()
    # computes each finding's file path relative to BACKEND_ROOT (real
    # call sites always live under backend/), so the fixture has to live
    # there too for this test to exercise the real path the CI check
    # actually runs.
    fixture = BACKEND_ROOT / "_test_pii_fixture_kw.py"
    fixture.write_text(
        "async def bad_node(state, bridge=None):\n"
        "    await log_step(\n"
        "        bridge=bridge, agent_id='x', action='y',\n"
        "        input_text=state['user'].email, output_text='fine',\n"
        "        step_index=0, run_id='r', trust_score=0,\n"
        "    )\n"
    )
    try:
        findings = check_file(fixture)
        assert len(findings) == 1
        assert findings[0].param == "input_text"
        assert findings[0].name == "email"
    finally:
        fixture.unlink()


def test_check_file_detects_a_suspicious_positional_argument():
    fixture = BACKEND_ROOT / "_test_pii_fixture_pos.py"
    fixture.write_text(
        "async def bad_node(state, bridge=None):\n"
        "    await log_step(bridge, 'x', 'y', state['user'].email, 'fine', 1, 'r')\n"
    )
    try:
        findings = check_file(fixture)
        assert len(findings) == 1
        assert findings[0].param == "input_text"
    finally:
        fixture.unlink()


def test_check_file_ignores_non_suspicious_arguments():
    fixture = BACKEND_ROOT / "_test_pii_fixture_clean.py"
    fixture.write_text(
        "async def ok_node(state, bridge=None):\n"
        "    await log_step(\n"
        "        bridge=bridge, agent_id='x', action='y',\n"
        "        input_text=state['research'], output_text=state['report'],\n"
        "        step_index=0, run_id='r', trust_score=0,\n"
        "    )\n"
    )
    try:
        assert check_file(fixture) == []
    finally:
        fixture.unlink()


def test_check_file_ignores_files_that_dont_call_log_step():
    assert check_file(Path(__file__)) == []
