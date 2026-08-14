"""
agents/llm_provider.py — LLM provider abstraction (F17, Phase 2 plan's
fix list).

Groq is configured as one implementation behind build_chat_model(), not a
hard dependency baked into every agent node — swapping providers (or
adding failover to a second one) is a change in ONE place. Every agent
node already talks to whatever this returns purely through LangChain's
common BaseChatModel interface (.ainvoke, .with_structured_output), never
a Groq-specific API, so nothing downstream needs to change when a second
provider is added.

MODEL_NAME/MODEL_VERSION are the single source of truth for "which model
is actually running" — imported by BOTH this module (to construct the
live LLM) and blockchain/hashing_utils.py (to compute the identity hash
that gets registered on-chain for tamper detection). Before this, those
were two separately hardcoded string literals that could silently drift
apart — which would make the tamper-detection demo report a false
mismatch (or worse, mask a real one) for reasons that have nothing to do
with an actual substitution attack.
"""

from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from config import get_settings

# The ONLY place these are allowed to be hardcoded — agents/base.py::get_llm
# and blockchain/hashing_utils.py::AGENTS both import from here instead of
# each carrying their own copy.
MODEL_NAME = "llama-3.3-70b-versatile"
MODEL_VERSION = "groq-v1"


def build_chat_model(provider: Optional[str] = None) -> BaseChatModel:
    """Constructs the configured LLM provider's chat model. `provider`
    defaults to config.llm_provider (currently only "groq" is
    implemented) — adding a second provider means adding one more branch
    here."""
    provider = provider or get_settings().llm_provider

    if provider == "groq":
        from langchain_groq import ChatGroq

        api_key = get_settings().groq_api_key
        if not api_key:
            raise ValueError("GROQ_API_KEY not set in .env")
        return ChatGroq(model=MODEL_NAME, temperature=0.3, max_tokens=2048, api_key=api_key)

    raise ValueError(f"unknown LLM provider: {provider!r}")
