"""Search provenance must survive a synthesis model that omits citations."""
import asyncio
from types import SimpleNamespace

from agents import researcher


def test_search_urls_are_carried_independently_of_synthesis(monkeypatch):
    async def search(_):
        return {"results": [
            {"url": "https://actual-source.example/proof", "content": "Evidence"},
            {"url": "https://actual-source.example/proof", "content": "Duplicate"},
            {"url": "javascript:alert(1)", "content": "Invalid source"},
        ]}

    async def synthesize(_):
        return SimpleNamespace(content="A summary with no URLs.")

    async def record(**kwargs):
        return "pending:1", {"action": kwargs["action"]}

    monkeypatch.setattr(researcher, "get_llm", lambda: SimpleNamespace(ainvoke=synthesize))
    monkeypatch.setattr(researcher, "log_step", record)
    monkeypatch.setattr(researcher, "track_token_usage", lambda *args: 0)
    result = asyncio.run(researcher.researcher_node(
        {"task": "Research proofs", "run_id": "source-regression"},
        tools=[SimpleNamespace(name="search_web", ainvoke=search)],
    ))
    assert result["research"] == "A summary with no URLs."
    assert result["source_urls"] == ["https://actual-source.example/proof"]
