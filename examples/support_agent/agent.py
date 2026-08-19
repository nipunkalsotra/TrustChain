"""
examples/support_agent/agent.py — a small, genuinely plausible
customer-support agent instrumented with the TrustChain SDK (Phase 4
plan §3 step 9).

Deliberately NOT a loop that logs "test" ten times: this simulates
Acme's refund-support assistant answering real-shaped customer queries
with real-shaped answers (order numbers, dollar amounts, dates) — the
content itself matters here, not just that *some* content got logged,
because scripts/e2e_demo.py's later tamper stage needs a believable
original message to confirm against via POST /integrity/verify-content.
There's no real LLM call behind `_KNOWLEDGE_BASE` (a small canned
lookup) — the point of this example is demonstrating the SDK's
instrumentation surface faithfully, not building a real support bot;
swap `SupportAgent.answer`'s body for a real LLM call and everything
else here (registration, logging, proofs) works completely unchanged.

Two SDK integration styles are exercised on purpose (Phase 4 plan's own
callout: "Exercise at least one of these — it is how real integrations
will actually look"):
  - `answer()` uses `tc.log_and_wait` directly — the blocking form, used
    here because the caller (this module, and later scripts/e2e_demo.py)
    needs the real step_id right away to fetch a Merkle proof.
  - `classify_intent()` uses the `@tc.audited(...)` decorator — zero
    lines in the hot path, non-blocking, for a helper call where nobody
    downstream needs the step_id synchronously.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from trustchain_sdk import StepReceipt, TrustChain

AGENT_ID = "support-bot"
AGENT_MODEL = "llama-3.3-70b"
AGENT_VERSION = "2026-08"

SYSTEM_PROMPT = (
    "You are Acme's refund and order-status support assistant. Answer "
    "questions about orders, refunds, shipping, and returns using only "
    "the information in the customer's account. Be concise, state "
    "dollar amounts and dates exactly, and never invent an order or "
    "refund that isn't in the record."
)

# A small, realistic knowledge base standing in for what a real
# integration would look up from an orders/billing system. Keyed loosely
# by keyword so `answer()` can pick a plausible response for a range of
# example queries — not a real NLU pipeline, just enough to make the
# logged content look like something a real customer actually asked.
# Keyword tuples, not single strings — a real customer query about a
# refund on a specific order ("Where is my refund for order 4471?")
# contains "order 4471" as a literal substring too, so a single-keyword
# match would pick the ORDER-STATUS entry instead of the REFUND one
# whenever both a topic word and an order number appear together (caught
# by a real end-to-end run: scripts/e2e_demo.py's stage 7 verify-content
# check failed because the "true original" it asserted against was never
# actually what got logged). Every keyword in a tuple must appear for
# that entry to match, and _lookup checks entries in this exact
# (most-specific-first) order — "refund" is checked before "order 4471"
# specifically so a refund query mentioning an order number still hits
# the refund answer, not the shipping one.
_KNOWLEDGE_BASE: dict[tuple[str, ...], str] = {
    ("refund", "4471"): "Your refund of $50 was issued on 12 August and should arrive by Friday.",
    ("order", "4471"): "Order #4471 shipped on 10 August via standard delivery, tracking TC-88213-US.",
    ("return", "policy"): "Items can be returned within 30 days of delivery for a full refund, "
                           "provided the original packaging is intact.",
    ("cancel", "account"): "Your account cancellation request has been received. Any active "
                            "subscriptions will end at the close of the current billing period, "
                            "and no further charges will be made.",
    ("shipping", "delay"): "Your order #5820 is delayed due to a carrier backlog and is now expected "
                            "to arrive by 22 August, three days later than originally quoted.",
}

_DEFAULT_RESPONSE = (
    "I couldn't find a matching order or refund record for that request — "
    "could you confirm the order number so I can look into it further?"
)


@dataclass
class SupportAgent:
    """Wraps one TrustChain client + one registered agent identity.
    Construct with `SupportAgent.connect(...)`, not the constructor
    directly, so the agent is always registered before anything logs a
    step under its agent_id (an unregistered agent_id still logs fine —
    Phase 2 behavior — but skips the identity-binding drift check Phase 3
    added, which defeats the point of a demo meant to exercise the real
    integration surface)."""

    tc: TrustChain

    @classmethod
    def connect(cls, api_key: Optional[str] = None, base_url: Optional[str] = None) -> "SupportAgent":
        api_key = api_key or os.environ.get("TRUSTCHAIN_API_KEY")
        if not api_key:
            raise RuntimeError(
                "no API key — pass api_key= or set TRUSTCHAIN_API_KEY "
                "(see docs/e2e-walkthrough.md for how to mint one via POST /api-keys)"
            )
        tc = TrustChain(
            api_key=api_key, base_url=base_url or os.environ.get("TRUSTCHAIN_BASE_URL", "http://localhost:8000"),
            on_error="raise",  # fail loudly in a demo — a silently-swallowed error defeats the point
        )
        tc.register_agent(agent_id=AGENT_ID, model=AGENT_MODEL, version=AGENT_VERSION, system_prompt=SYSTEM_PROMPT)
        return cls(tc=tc)

    def answer(self, query: str) -> StepReceipt:
        """Blocking (log_and_wait) — the caller gets a real step_id back
        immediately, needed to fetch a Merkle proof or, later, to point a
        tamper demonstration at this exact row."""
        output = self._lookup(query)
        return self.tc.log_and_wait(
            agent_id=AGENT_ID, action="answer_query", input=query, output=output, trust_score=92,
        )

    def classify_intent(self, query: str) -> str:
        """Non-blocking (@tc.audited) — a helper call nobody needs a
        step_id back from synchronously, logged fire-and-forget."""
        return self._classify(query)

    def _lookup(self, query: str) -> str:
        q = query.lower()
        for keywords, response in _KNOWLEDGE_BASE.items():
            if all(kw in q for kw in keywords):
                return response
        return _DEFAULT_RESPONSE

    def _classify(self, query: str) -> str:
        q = query.lower()
        if "refund" in q:
            return "refund_status"
        if "order" in q or "shipping" in q or "delay" in q:
            return "order_status"
        if "return" in q:
            return "return_policy"
        if "cancel" in q:
            return "account_cancellation"
        return "general_inquiry"

    def close(self) -> None:
        self.tc.close()  # flushes the background queue — do not skip


def _make_classify_intent_audited(agent: SupportAgent):
    """`@tc.audited` needs a bound `tc` instance at decoration time, so
    this wires it up after `SupportAgent.connect()` has one — used by
    scripts/e2e_demo.py to demonstrate the decorator form (Phase 4 plan's
    "Also try the decorator form" callout) without every SupportAgent
    instance paying for a second, always-decorated code path it may not
    need."""

    @agent.tc.audited(agent_id=AGENT_ID, action="classify_intent")
    def classify_intent(query: str) -> str:
        return agent._classify(query)

    return classify_intent


if __name__ == "__main__":
    # Standalone smoke run — python3 -m examples.support_agent.agent,
    # from repo root, with TRUSTCHAIN_API_KEY set and a live backend at
    # TRUSTCHAIN_BASE_URL (default http://localhost:8000). Prints exactly
    # the same shape of output as the Phase 4 plan's own Stage 4 example.
    agent = SupportAgent.connect()
    try:
        receipt = agent.answer("Where is my refund for order 4471?")
        print("step_id:", receipt.step_id)

        proof = agent.tc.get_proof(receipt.step_id)
        print("proof verifies:", agent.tc.verify_proof(proof) if proof else None)

        classify_intent = _make_classify_intent_audited(agent)
        intent = classify_intent("What's your return policy?")
        print("classified intent (logged via @tc.audited):", intent)
    finally:
        agent.close()
