"""
trustchain_sdk.integrations.langchain — TrustChainCallback: audits every
LLM and tool call a LangChain agent makes, without the agent's own code
needing a single tc.log() call (plan §13.1's "framework-native" principle
— "a LangChain callback handler beats a manual API").

    from trustchain_sdk.integrations.langchain import TrustChainCallback

    agent = create_agent(llm, tools,
                          callbacks=[TrustChainCallback(tc, agent_id="support-bot")])

`langchain_core` is NOT a dependency of trustchain-sdk itself (most
callers don't use LangChain) — imported lazily, only when this specific
integration module is actually imported, so installing the base SDK
never requires it.
"""

from typing import Any
from uuid import UUID

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError as e:
    raise ImportError(
        "trustchain_sdk.integrations.langchain requires langchain-core — pip install langchain-core"
    ) from e

from trustchain_sdk.instrumentation import TrustChain


class TrustChainCallback(BaseCallbackHandler):
    """Logs one step per LLM call and per tool call. Failures in TrustChain
    itself never interrupt the agent run — `tc.log()` already fails open
    (see TrustChain's on_error setting); this handler additionally never
    lets a bug in ITS OWN bookkeeping (e.g. an unexpected callback
    ordering) propagate into LangChain's own execution, since a broken
    callback handler could otherwise take down an otherwise-healthy agent
    run — exactly the "never break the host application" principle this
    SDK is built around."""

    def __init__(self, tc: TrustChain, agent_id: str):
        self._tc = tc
        self._agent_id = agent_id
        self._pending_prompts: dict[UUID, str] = {}
        self._pending_tool_inputs: dict[UUID, str] = {}

    def _safe_log(self, action: str, input_text: str, output_text: str) -> None:
        try:
            self._tc.log(agent_id=self._agent_id, action=action, input=input_text, output=output_text)
        except Exception:  # noqa: BLE001 — see class docstring
            pass

    def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: UUID, **kwargs: Any) -> None:
        self._pending_prompts[run_id] = "\n---\n".join(prompts)

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        prompt = self._pending_prompts.pop(run_id, "")
        try:
            output = response.generations[0][0].text
        except (AttributeError, IndexError):
            output = str(response)
        self._safe_log("llm_call", prompt, output)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        prompt = self._pending_prompts.pop(run_id, "")
        self._safe_log("llm_call_error", prompt, str(error))

    def on_tool_start(
        self, serialized: dict, input_str: str, *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._pending_tool_inputs[run_id] = input_str

    def on_tool_end(self, output: str, *, run_id: UUID, **kwargs: Any) -> None:
        tool_input = self._pending_tool_inputs.pop(run_id, "")
        self._safe_log("tool_call", tool_input, str(output))

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        tool_input = self._pending_tool_inputs.pop(run_id, "")
        self._safe_log("tool_call_error", tool_input, str(error))
