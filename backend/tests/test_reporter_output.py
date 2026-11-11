"""A terminal report must contain model output before completion is recorded."""
import asyncio
from types import SimpleNamespace

import pytest

from agents import reporter


def exercise(monkeypatch, content):
    actions = []

    async def record(**kwargs):
        actions.append(kwargs['action'])
        return 'pending:1', {'action': kwargs['action']}

    async def invoke(_):
        return SimpleNamespace(content=content)

    monkeypatch.setattr(reporter, 'get_llm', lambda: SimpleNamespace(ainvoke=invoke))
    monkeypatch.setattr(reporter, 'log_step', record)
    monkeypatch.setattr(reporter, 'track_token_usage', lambda *args: 0)
    state = {'task': 'q', 'research': 'research', 'validation': 'checked', 'run_id': 'report-test', 'score': 80}
    return actions, lambda: asyncio.run(reporter.reporter_node(state))


def test_empty_report_never_records_completion(monkeypatch):
    actions, run = exercise(monkeypatch, '  ')
    with pytest.raises(ValueError, match='empty report'):
        run()
    assert actions == ['report_started']


def test_report_output_is_preserved_before_completion(monkeypatch):
    actions, run = exercise(monkeypatch, 'A real model report.')
    assert run()['report'] == 'A real model report.'
    assert actions == ['report_started', 'report_complete']
