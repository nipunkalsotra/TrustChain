"""
trustchain_sdk.instrumentation — the actual "any third-party agent can be
audited through a published SDK" surface (plan §13.1/§13.2), distinct
from trustchain_sdk.client.TrustChainClient (a REST wrapper around
TrustChain's OWN 4-agent pipeline, POST /run-agent etc.).

Design principles this module is built around (plan §13.1), each with a
concrete mechanism:
  - One line to adopt        -> TrustChain(api_key=...) + tc.log(...)
  - Never break the host app -> every call fails open (logs a warning,
                                 never raises) unless on_error="raise"
  - Non-blocking by default  -> tc.log() enqueues onto a background
                                 worker thread and returns immediately;
                                 tc.log(..., wait=True) or tc.log_and_wait
                                 makes the real call inline when a caller
                                 genuinely needs the server-assigned
                                 step_id right away (e.g. to fetch a proof)
  - Framework-native          -> trustchain_sdk.integrations.langchain
  - Honest about state        -> a queued StepReceipt reports
                                 anchor_status=None, not a guess
"""

from __future__ import annotations

import functools
import json
import logging
import queue
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Optional

from trustchain_sdk.client import DEFAULT_BASE_URL, TrustChainClient
from trustchain_sdk.exceptions import TrustChainError
from trustchain_sdk.merkle import hex_to_bytes, verify_proof as _verify_proof_locally

logger = logging.getLogger("trustchain_sdk")


def _code_hash(agent_id: str, model: str, version: str, system_prompt: str) -> str:
    """keccak256(json.dumps({...}, sort_keys=True, separators=(",", ":")))
    — MUST match backend/blockchain/hashing_utils.py's compute_hash
    exactly (same key names, same dict, same serialisation), or every
    hash computed here will silently mismatch what's registered on-chain
    despite being "the same" config. system_prompt is hashed HERE,
    client-side — the raw prompt is never sent to the API (see
    RegisterAgentRequest in the backend, which only ever accepts a
    pre-computed hash)."""
    from eth_utils import keccak

    config = {"agentId": agent_id, "model": model, "version": version, "systemPrompt": system_prompt}
    serialised = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return "0x" + keccak(text=serialised).hex()


@dataclass
class StepReceipt:
    local_id: str
    status: str = "queued"
    step_id: Optional[int] = None
    anchor_status: Optional[str] = None
    error: Optional[str] = None


@dataclass
class VerifyResult:
    agent_id: str
    verified: bool
    is_active: bool
    hash_matches: bool
    stored_hash: str
    provided_hash: str


@dataclass
class VerifyContentResult:
    """Typed result of TrustChain.verify_content — see backend/main.py's
    POST /integrity/verify-content docstring for the full reasoning.
    `matches_original` is None (not False) when there's no edit history
    to compare against at all — distinct from "compared, and it didn't
    match"."""
    step_id: int
    field: str
    computed_hash: str
    matches_current: bool
    matches_original: Optional[bool]


@dataclass
class MerkleProof:
    step_id: int
    run_id: str
    leaf: str
    proof: list[str]
    root: str
    tx_hash: Optional[str]
    anchor_status: str
    anchor_id: Optional[int] = None
    # Phase 3 §6.2/§9.7 — which leaf preimage produced `leaf`, and (for
    # schema 2) the identity fingerprint bound into it. 1 for every step
    # anchored before Phase 3 identity binding shipped, or logged by an
    # SDK that predates it.
    leaf_schema_version: int = 1
    agent_code_hash: Optional[str] = None


class AlertRecord(Mapping):
    """One alert as returned by TrustChain.alerts() (Phase 4 G4). Behaves
    exactly like the raw dict GET /alerts already returned — `alert["id"]`,
    `alert.get("severity")`, `dict(alert)`, `**alert` all still work
    unchanged, via collections.abc.Mapping — so this is purely additive;
    no existing caller doing plain dict access breaks. On top of that, it
    exposes named accessors for the forensic-evidence fields
    integrity_watchdog/main.py's `_forensic_evidence` nests inside the
    raw `evidence` sub-dict, so a caller doesn't need to know that
    nesting or those exact camelCase key names to act on tamper
    attribution.

    Every accessor returns None when the field isn't present — most
    alert_types carry no forensic evidence at all (it's specific to
    step_row_tampered), and even for that type it's only populated for
    edits made after the steps_audit_trigger migrations
    (b9a8a1970b3c/010d34f64a31) existed. A missing field is a normal,
    expected case to handle, not an error."""

    def __init__(self, raw: dict):
        self._raw = raw

    def __getitem__(self, key):
        return self._raw[key]

    def __iter__(self):
        return iter(self._raw)

    def __len__(self):
        return len(self._raw)

    def __repr__(self) -> str:
        return f"AlertRecord({self._raw!r})"

    @property
    def evidence(self) -> dict:
        """The full raw evidence blob — always available even for a
        forensic field this class doesn't have a named accessor for yet
        (a new evidence key added on the backend shows up here
        immediately, without needing an SDK release first)."""
        return self._raw.get("evidence") or {}

    @property
    def is_deletion(self) -> bool:
        """True if this evidence describes a DELETEd step row rather than
        an edited one — integrity_watchdog's deletion-sentinel case has a
        different evidence shape (no old/new hash pairs to diff against,
        since the row is simply gone; "whatHappened" is the marker key
        that case sets and the edit case never does)."""
        return "whatHappened" in self.evidence

    @property
    def edited_by_operator(self) -> Optional[str]:
        """The human operator's display name, if the edit/delete was made
        through an individually-issued db_operator credential rather than
        the shared superuser role — see db_operator.py / ADR-0020."""
        return self.evidence.get("editedByOperator")

    @property
    def edited_by_db_role(self) -> Optional[str]:
        return self.evidence.get("editedByDbRole")

    @property
    def old_output_hash(self) -> Optional[str]:
        return self.evidence.get("oldOutputHash")

    @property
    def new_output_hash(self) -> Optional[str]:
        return self.evidence.get("newOutputHash")

    @property
    def old_input_hash(self) -> Optional[str]:
        return self.evidence.get("oldInputHash")

    @property
    def new_input_hash(self) -> Optional[str]:
        return self.evidence.get("newInputHash")


@dataclass
class _QueuedLog:
    kwargs: dict
    receipt: StepReceipt


class TrustChain:
    """
    from trustchain_sdk import TrustChain
    tc = TrustChain(api_key="tc_live_...")

    tc.register_agent(agent_id="support-bot", model="gpt-4o", version="2025-11",
                       system_prompt=SUPPORT_PROMPT)
    tc.log(agent_id="support-bot", action="answer_query", input=query, output=result)

    @tc.audited(agent_id="support-bot", action="answer_query")
    def answer_query(query: str) -> str: ...

    tc.verify_agent("support-bot", model=..., version=..., system_prompt=...)
    proof = tc.get_proof(step_id)
    tc.verify_proof(proof)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        on_error: str = "warn",
        queue_max_size: int = 10_000,
    ):
        """`on_error`: "warn" (default — log via the `trustchain_sdk`
        logger, swallow, never raise into caller code — audit logging
        must never be able to break the host application) or "raise"
        (surface TrustChainError to the caller; useful in tests/CI where
        a silently-dropped step should fail loudly instead)."""
        if on_error not in ("warn", "raise"):
            raise ValueError('on_error must be "warn" or "raise"')
        self._client = TrustChainClient(api_key, base_url=base_url)
        self._on_error = on_error
        self._queue: queue.Queue[Optional[_QueuedLog]] = queue.Queue(maxsize=queue_max_size)
        self._run_ids: dict[str, str] = {}
        self._run_ids_lock = threading.Lock()
        # Phase 3 §6.2/§10.1: agent_id -> code_hash, populated by
        # register_agent()/declare_agent(). log() attaches the cached hash
        # to every step for that agent_id, which is what lets the backend
        # run its synchronous identity-drift check (agents/base.py::
        # _check_identity_drift) with zero extra round trips. An agent_id
        # never registered through THIS instance has no cached hash and
        # simply gets no drift check on its steps — the same Phase 2
        # behavior as before this existed.
        self._agent_hashes: dict[str, str] = {}
        self._agent_hashes_lock = threading.Lock()
        self._worker = threading.Thread(target=self._run_worker, daemon=True, name="trustchain-sdk-log-worker")
        self._worker.start()

    def close(self) -> None:
        """Stops accepting new background work and blocks until the
        queue drains — call before process exit so buffered log() calls
        aren't lost. Does NOT need to be called for log(..., wait=True)/
        log_and_wait(), which never touch the queue."""
        self._queue.put(None)  # sentinel — worker exits after draining what precedes it
        self._worker.join(timeout=30)
        self._client.close()

    def __enter__(self) -> "TrustChain":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def flush(self, timeout: float = 10.0) -> bool:
        """Blocks until every log() call queued so far has been sent (or
        `timeout` elapses). Returns False on timeout — callers that care
        about that should treat it as "some steps may not be durably
        queued server-side yet", not as data loss (the queue itself
        isn't cleared, draining continues in the background)."""
        deadline = time.monotonic() + timeout
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.05)
        return self._queue.empty()

    # ── Background worker ────────────────────────────────────────────

    def _run_worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            self._send_log(item)

    def _send_log(self, item: _QueuedLog) -> None:
        try:
            response = self._client.log_step(**item.kwargs)
            item.receipt.step_id = response["step_id"]
            item.receipt.status = response["status"]
            item.receipt.anchor_status = response["anchor_status"]
        except Exception as e:  # noqa: BLE001 — deliberately broad, see class docstring
            item.receipt.error = str(e)
            logger.warning("trustchain_sdk: failed to log step (agent_id=%s): %s", item.kwargs.get("agent_id"), e)

    # ── Registration / verification ──────────────────────────────────

    def register_agent(self, agent_id: str, model: str, version: str, system_prompt: str) -> str:
        """Synchronous — registration is rare (once per agent config, not
        once per call), so there's no non-blocking mode for it. Returns
        the on-chain tx hash. Fails open per `on_error` like everything
        else here.

        Caches code_hash for agent_id on success (Phase 3 §6.2) — every
        subsequent log()/audited() call for this agent_id attaches it
        automatically. Cached even on a FAILED call is deliberately NOT
        done: a cached hash the backend never actually saw registered
        would make every step's drift check compare against a real
        on-chain hash while a WRONG hash sits in this cache, guaranteeing
        false-positive drift alerts rather than the true "not registered,
        no check happens" state."""
        code_hash = _code_hash(agent_id, model, version, system_prompt)
        try:
            response = self._client.register_agent(agent_id, code_hash, model, version)
        except Exception as e:
            return self._handle_error(e, default="")
        with self._agent_hashes_lock:
            self._agent_hashes[agent_id] = code_hash
        return response["tx_hash"]

    def declare_agent(self, agent_id: str, model: str, version: str, system_prompt: str) -> str:
        """Idempotent registration (Phase 3 §10.1) — registers ONLY if
        this exact {agent_id, model, version, system_prompt} combination
        isn't already what's cached, otherwise no-ops and returns "".
        Meant for a startup call that runs on every boot: an app that
        calls this every time it starts (vs. register_agent(), meant for
        an explicit, deliberate registration step) won't spam
        AgentUpdated events — and therefore won't spam
        agent_identity_changed alerts — just because it restarted with an
        unchanged config. NOTE: this only checks the LOCAL cache, not the
        server's registered hash — call verify_agent() first if you need
        to know whether the server's state already matches before
        deciding whether to call this at all."""
        code_hash = _code_hash(agent_id, model, version, system_prompt)
        with self._agent_hashes_lock:
            if self._agent_hashes.get(agent_id) == code_hash:
                return ""
        return self.register_agent(agent_id, model, version, system_prompt)

    def verify_agent(self, agent_id: str, model: str, version: str, system_prompt: str) -> Optional[VerifyResult]:
        code_hash = _code_hash(agent_id, model, version, system_prompt)
        try:
            response = self._client.verify_agent(agent_id, code_hash)
            return VerifyResult(
                agent_id=agent_id,
                verified=response["isValid"],
                is_active=response["isActive"],
                hash_matches=response["hashMatches"],
                stored_hash=response["storedHash"],
                provided_hash=response["providedHash"],
            )
        except Exception as e:
            return self._handle_error(e, default=None)

    # ── Logging ──────────────────────────────────────────────────────

    def log(
        self,
        agent_id: str,
        action: str,
        input: str,  # noqa: A002 — matches the plan's documented kwarg name
        output: str,
        trust_score: int = 0,
        wait: bool = False,
    ) -> StepReceipt:
        """Non-blocking by default (queues onto the background worker,
        returns immediately with step_id=None — the server hasn't
        assigned one yet). Pass wait=True for the synchronous version
        (blocks for the real HTTP round trip and returns the real
        step_id/anchor_status) — needed before get_proof(), since a proof
        can't be fetched for a step_id that doesn't exist yet."""
        import uuid

        receipt = StepReceipt(local_id=uuid.uuid4().hex)
        with self._agent_hashes_lock:
            agent_code_hash = self._agent_hashes.get(agent_id)
        kwargs = {
            "run_id": self._current_run_id(agent_id),
            "agent_id": agent_id, "action": action, "input": input, "output": output,
            "trust_score": trust_score, "agent_code_hash": agent_code_hash,
        }

        if wait:
            self._send_log(_QueuedLog(kwargs=kwargs, receipt=receipt))
            if receipt.error is not None:
                return self._handle_error(TrustChainError(receipt.error), default=receipt)
            return receipt

        try:
            self._queue.put_nowait(_QueuedLog(kwargs=kwargs, receipt=receipt))
        except queue.Full:
            receipt.error = "trustchain_sdk: local queue full, step dropped"
            logger.warning(receipt.error)
        return receipt

    def log_and_wait(self, *args, **kwargs) -> StepReceipt:
        return self.log(*args, wait=True, **kwargs)

    def _current_run_id(self, agent_id: str) -> str:
        """One run_id per agent_id per TrustChain instance, generated
        once and reused for every log() call on that agent — matches the
        plan's model of "one run groups one agent invocation's steps".
        Call `new_run(agent_id)` to start a fresh one (e.g. between
        distinct conversations/requests the SDK can't infer boundaries
        for on its own)."""
        with self._run_ids_lock:
            if agent_id not in self._run_ids:
                import uuid
                self._run_ids[agent_id] = f"sdk_{agent_id}_{uuid.uuid4().hex}"
            return self._run_ids[agent_id]

    def new_run(self, agent_id: str) -> str:
        import uuid

        with self._run_ids_lock:
            self._run_ids[agent_id] = f"sdk_{agent_id}_{uuid.uuid4().hex}"
            return self._run_ids[agent_id]

    def audited(self, agent_id: str, action: str) -> Callable:
        """Decorator form — zero lines in the hot path. Logs the
        function's own repr'd args/return value as input/output; wrap
        your own call site directly (see `log()`) if you need more
        control over what gets recorded."""
        def decorator(fn: Callable) -> Callable:
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                result = fn(*args, **kwargs)
                self.log(
                    agent_id=agent_id, action=action,
                    input=repr({"args": args, "kwargs": kwargs}),
                    output=repr(result),
                )
                return result
            return wrapper
        return decorator

    # ── Proofs ───────────────────────────────────────────────────────

    def get_proof(self, step_id: int) -> Optional[MerkleProof]:
        try:
            response = self._client.get_step_proof(step_id)
            return MerkleProof(
                step_id=response["stepId"], run_id=response["runId"], leaf=response["leaf"],
                proof=response["proof"], root=response["root"], tx_hash=response.get("txHash"),
                anchor_status=response["anchorStatus"], anchor_id=response.get("anchorId"),
                leaf_schema_version=response.get("leafSchemaVersion", 1),
                agent_code_hash=response.get("agentCodeHash"),
            )
        except Exception as e:
            return self._handle_error(e, default=None)

    # ── Alerts ───────────────────────────────────────────────────────

    def alerts(self, status: Optional[str] = None, severity: Optional[str] = None, limit: int = 50) -> list[AlertRecord]:
        """Phase 3 §10.1 — read access to your org's alerts, so a team
        can pipe TrustChain findings into their own on-call tooling.
        Needs an API key with the `alerts:read` scope (see POST
        /api-keys) — a key without it gets a TrustChainError (or, with
        on_error="warn", an empty list) via the same fail-open path as
        everything else here.

        Returns AlertRecord objects (Phase 4 G4), not plain dicts — each
        one is still fully dict-like (subscriptable, .get(), iterable),
        so existing code doing `alert["id"]` keeps working unchanged; it
        additionally exposes typed accessors (.edited_by_operator,
        .old_output_hash, .is_deletion, ...) for the tamper-attribution
        fields GET /alerts's evidence blob carries — see AlertRecord's
        own docstring."""
        try:
            response = self._client.list_alerts(status=status, severity=severity, limit=limit)
            return [AlertRecord(a) for a in response["alerts"]]
        except Exception as e:
            return self._handle_error(e, default=[])

    # ── Content verification (Phase 4 G3) ───────────────────────────────

    def verify_content(self, step_id: int, field: str, candidate_text: str) -> Optional[VerifyContentResult]:
        """Confirms or refutes a candidate piece of text against a step's
        stored hash — the SDK-reachable form of what was previously only
        available via a hand-written call to POST /integrity/verify-content
        (Phase 4 G3). TrustChain never stores or returns your agent's
        actual input/output text; `candidate_text` must come from your own
        systems (application logs, an observability tool, wherever your
        agent framework actually logs transcripts).

        field must be "input" or "output". Returns None (not an exception,
        unless on_error="raise") on any failure, same fail-open contract as
        get_proof."""
        try:
            response = self._client.verify_content(step_id, field, candidate_text)
            return VerifyContentResult(
                step_id=response["stepId"], field=response["field"], computed_hash=response["computedHash"],
                matches_current=response["matchesCurrent"], matches_original=response["matchesOriginal"],
            )
        except Exception as e:
            return self._handle_error(e, default=None)

    def verify_proof(self, proof: MerkleProof) -> bool:
        """LOCAL verification only — recomputes the root from leaf+proof
        and compares to `proof.root` as returned by the API. This proves
        internal consistency (tampering with the leaf or any sibling
        breaks the fold) but does NOT independently confirm `proof.root`
        is what's actually anchored on-chain; for that, see
        verify_proof_onchain, which reads the root from the real
        contract instead of trusting this field."""
        leaf = hex_to_bytes(proof.leaf)
        siblings = [hex_to_bytes(p) for p in proof.proof]
        root = hex_to_bytes(proof.root)
        return _verify_proof_locally(leaf, siblings, root)

    def verify_proof_onchain(self, proof: MerkleProof, rpc_url: str, audit_log_address: str) -> bool:
        """Strongest form: reads AgentAuditLogV2.verifyProof(anchorId,
        leaf, proof) directly from the chain at `rpc_url` — the same
        call backend/tests verify against. Requires knowing which chain/
        contract to check (there's no way for the SDK to infer this
        safely — a wrong RPC/address would silently "verify" against the
        wrong deployment), so this is opt-in, not the default. Returns
        False (never raises, even on an RPC error) — a chain read failing
        is a normal "couldn't confirm" outcome to handle, not a crash.
        """
        if proof.anchor_id is None:
            return False
        try:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(rpc_url))
            abi = [{
                "inputs": [
                    {"name": "anchorId", "type": "uint256"},
                    {"name": "leaf", "type": "bytes32"},
                    {"name": "proof", "type": "bytes32[]"},
                ],
                "name": "verifyProof",
                "outputs": [{"name": "", "type": "bool"}],
                "stateMutability": "view",
                "type": "function",
            }]
            contract = w3.eth.contract(address=Web3.to_checksum_address(audit_log_address), abi=abi)
            return contract.functions.verifyProof(
                proof.anchor_id, hex_to_bytes(proof.leaf), [hex_to_bytes(p) for p in proof.proof],
            ).call()
        except Exception as e:
            logger.warning("trustchain_sdk: on-chain proof verification failed: %s", e)
            return False

    # ── Error handling ───────────────────────────────────────────────

    def _handle_error(self, e: Exception, default: Any) -> Any:
        if self._on_error == "raise":
            raise e if isinstance(e, TrustChainError) else TrustChainError(str(e))
        logger.warning("trustchain_sdk: %s", e)
        return default
