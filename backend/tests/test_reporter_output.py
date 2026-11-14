"""A terminal report must contain model output before completion is recorded."""

import asyncio
from types import SimpleNamespace

import pytest

from agents import reporter


def exercise(monkeypatch, content):
    actions = []

    async def record(**kwargs):
        actions.append(kwargs["action"])
        return "pending:1", {"action": kwargs["action"]}

    async def invoke(_):
        return SimpleNamespace(content=content)

    monkeypatch.setattr(reporter, "get_llm", lambda: SimpleNamespace(ainvoke=invoke))
    monkeypatch.setattr(reporter, "log_step", record)
    monkeypatch.setattr(reporter, "track_token_usage", lambda *args: 0)
    state = {
        "task": "q",
        "research": "research",
        "validation": "checked",
        "run_id": "report-test",
        "score": 80,
    }
    return actions, lambda: asyncio.run(reporter.reporter_node(state))


def test_empty_report_never_records_completion(monkeypatch):
    actions, run = exercise(monkeypatch, "  ")
    with pytest.raises(ValueError, match="empty report"):
        run()
    assert actions == ["report_started"]


def test_report_output_is_preserved_before_completion(monkeypatch):
    actions, run = exercise(monkeypatch, "A real model report.")
    result = run()["report"]
    assert result.startswith("A real model report.")
    assert "queued for asynchronous Merkle anchoring" in result
    assert actions == ["report_started", "report_complete"]


def test_sources_survive_model_omission_and_network_claim_is_replaced():
    result = reporter.finalize_report(
        "## Summary\nFindings.\n\n## Blockchain Audit\nAll steps confirmed on Monad.",
        "Research [source](https://example.org/proof). Also https://example.org/proof",
    )
    assert result.count("https://example.org/proof") == 1
    assert "All steps confirmed on Monad" not in result
    assert "a recorded step is not yet a confirmed on-chain proof" in result


def test_no_source_urls_are_invented_and_later_sections_are_preserved():
    result = reporter.finalize_report(
        "## Summary\nResult.\n\n## Sources\nhttps://invented.invalid\n\n## Trust Score\n80/100",
        "Research without URLs.",
    )
    assert "https://invented.invalid" not in result
    assert "## Trust Score\n80/100" in result
    assert "## Sources" not in result


def test_tool_sources_survive_synthesis_omission_and_override_model_urls():
    result = reporter.finalize_report(
        "## Summary\nA summary with no citations.",
        "Model research mentions https://invented.invalid",
        ["https://actual-source.example/research"],
    )
    assert "https://actual-source.example/research" in result
    assert "https://invented.invalid" not in result


def test_failed_search_does_not_turn_model_urls_into_sources():
    result = reporter.finalize_report("Summary.", "https://invented.invalid", [])
    assert "## Sources" not in result
