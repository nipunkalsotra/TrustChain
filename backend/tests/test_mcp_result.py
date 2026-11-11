import pytest
from agents.mcp_result import tool_payload


def test_mapping_and_mcp_text_blocks_have_identical_payloads():
    payload = {"verdict": "supported", "confidence": 0.9, "evidence": []}
    assert tool_payload(payload) == payload
    assert tool_payload([{"type": "text", "text": '{"verdict":"supported","confidence":0.9,"evidence":[]}'}]) == payload


def test_invalid_tool_payload_cannot_be_treated_as_success():
    with pytest.raises(ValueError):
        tool_payload([{"type": "text", "text": "upstream failed"}])
