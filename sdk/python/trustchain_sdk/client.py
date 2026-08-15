"""trustchain_sdk.client — sync and async clients for the TrustChain API.

Built for the "SDK-driven third-party agent" use case main.py's own
POST /run-agent docstring calls out explicitly: an API key
(`tc_live_.../tc_test_...`, see POST /api-keys) authenticates without any
human login, in the same `Authorization: Bearer <key>` header a session
JWT would use — see backend/auth.py's get_current_principal.

Both clients share the same method surface; pick whichever fits your
caller (TrustChainClient for scripts/notebooks, AsyncTrustChainClient for
an async agent framework — this backend's own agent pipeline is async
end to end, so most real "agent calling TrustChain" code will want this
one).
"""

import json
from typing import Any, AsyncIterator, Iterator, Optional

import httpx

from trustchain_sdk.exceptions import (
    AuthenticationError,
    AuthorizationError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ServerError,
    StreamTimeoutError,
    TrustChainError,
    ValidationError,
)

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_STREAM_TIMEOUT_SECONDS = 120.0


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return

    try:
        body = response.json()
    except ValueError:
        body = None
    detail = body.get("detail") if isinstance(body, dict) else body
    # error_code (backend/errors.py) is None for responses that predate
    # the typed error taxonomy, or a validation-error body (422's `detail`
    # is FastAPI's own list-of-errors shape, not this API's ApiError at
    # all — Pydantic validation never goes through ApiError).
    error_code = body.get("error_code") if isinstance(body, dict) else None

    status = response.status_code
    message = f"TrustChain API returned {status}: {detail}"

    if status == 400:
        raise BadRequestError(message, status, detail, error_code=error_code)
    if status == 401:
        raise AuthenticationError(message, status, detail, error_code=error_code)
    if status == 403:
        raise AuthorizationError(message, status, detail, error_code=error_code)
    if status == 404:
        raise NotFoundError(message, status, detail, error_code=error_code)
    if status == 409:
        raise ConflictError(message, status, detail, error_code=error_code)
    if status == 422:
        raise ValidationError(message, status, detail, error_code=error_code)
    if status == 429:
        retry_after = response.headers.get("Retry-After")
        raise RateLimitError(
            message, status, detail,
            retry_after_seconds=float(retry_after) if retry_after is not None else None,
            error_code=error_code,
        )
    if status >= 500:
        raise ServerError(message, status, detail, error_code=error_code)
    raise TrustChainError(message, status, detail, error_code=error_code)


def _parse_sse_line(line: str) -> Optional[dict]:
    """SSE lines look like `data: {...}` (see main.py's stream_events) —
    anything else (blank keep-alive lines, comments) is not an event."""
    if not line.startswith("data: "):
        return None
    return json.loads(line[len("data: "):])


class _BaseClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        if not api_key:
            raise ValueError("api_key is required — create one via POST /api-keys (see the README)")
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._timeout = timeout


class TrustChainClient(_BaseClient):
    """Synchronous client. Each method issues one request and returns the
    parsed JSON body, or raises a trustchain_sdk.exceptions.TrustChainError
    subclass — never a raw httpx exception for an API-level error."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        super().__init__(api_key, base_url, timeout)
        self._client = httpx.Client(base_url=self._base_url, headers=self._headers, timeout=self._timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TrustChainClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        response = self._client.request(method, path, **kwargs)
        _raise_for_status(response)
        return response.json()

    def run_agent(self, task: str, run_id: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict:
        """Starts a pipeline run. Returns immediately with {run_id, task,
        status, stream_url} — the pipeline itself runs in the background;
        use stream() or poll get_run() to follow it. Requires the
        `runs:write` scope on this API key.

        `idempotency_key`, if given, makes a retried call with the exact
        same key + task safe to repeat (returns the original response
        instead of starting a second run) — see backend/db/idempotency.py.
        A retried call with the SAME key but a DIFFERENT task raises
        ConflictError (409)."""
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        body = {"task": task}
        if run_id:
            body["run_id"] = run_id
        return self._request("POST", "/run-agent", json=body, headers=headers)

    def get_run(self, run_id: str) -> dict:
        return self._request("GET", f"/runs/{run_id}")

    def list_runs(self, limit: int = 50) -> dict:
        return self._request("GET", "/runs", params={"limit": limit})

    def trust_scores(self, run_id: str) -> dict:
        return self._request("GET", "/trust-scores", params={"run_id": run_id})

    def trust_score_history(self, run_id: str) -> dict:
        return self._request("GET", "/trust-scores/history", params={"run_id": run_id})

    def leaderboard(self, max_runs: int = 50) -> dict:
        return self._request("GET", "/leaderboard", params={"max_runs": max_runs})

    def audit_log(self, run_id: Optional[str] = None) -> dict:
        params = {"run_id": run_id} if run_id else {}
        return self._request("GET", "/audit-log", params=params)

    def register_agent(self, agent_id: str, code_hash: str, model: str, version: str) -> dict:
        """Low-level — takes an already-computed code_hash. Most callers
        want trustchain_sdk.TrustChain.register_agent (which hashes
        {agentId, model, version, systemPrompt} client-side first, never
        sending the raw prompt) rather than this directly."""
        return self._request(
            "POST", "/agents",
            json={"agent_id": agent_id, "code_hash": code_hash, "model": model, "version": version},
        )

    def verify_agent(self, agent_id: str, code_hash: str) -> dict:
        return self._request("GET", f"/agents/{agent_id}/verify", params={"code_hash": code_hash})

    def log_step(
        self, run_id: str, agent_id: str, action: str, input: str, output: str,  # noqa: A002
        trust_score: int = 0, idempotency_key: Optional[str] = None,
    ) -> dict:
        """Low-level, synchronous SDK-ingest call (POST /steps) — most
        callers want trustchain_sdk.TrustChain.log (non-blocking by
        default) rather than this directly."""
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        return self._request(
            "POST", "/steps",
            json={
                "run_id": run_id, "agent_id": agent_id, "action": action,
                "input": input, "output": output, "trust_score": trust_score,
            },
            headers=headers,
        )

    def get_step_proof(self, step_id: int) -> dict:
        return self._request("GET", f"/steps/{step_id}/proof")

    def platform_stats(self) -> dict:
        """GET /stats — public, no auth required (this call still sends
        the configured API key/JWT like every other method here, which
        the endpoint simply ignores)."""
        return self._request("GET", "/stats")

    def stream(self, run_id: str, timeout: float = DEFAULT_STREAM_TIMEOUT_SECONDS) -> Iterator[dict]:
        """Yields parsed SSE events for a run as they arrive. Deliberately
        unauthenticated on the server side (see main.py's stream_events
        docstring — browser EventSource can't send an Authorization
        header), so this doesn't send the API key either; it only needs
        the run_id, same as the browser frontend.

        Consumes the stream to its NATURAL end (connection close) rather
        than returning as soon as it sees a `type: "run_complete"` or
        `type: "error"` event — main.py's stream_events always sends ONE
        MORE synthetic `run_complete` wrapper event after the pipeline's
        own last event (see that function's docstring), and critically,
        that wrapper is only sent AFTER the run's terminal status has
        already been committed to Postgres (db.complete_run/fail_run —
        see main.py's _run_pipeline_background: both are awaited before
        run_events.publish_terminal(), which is what makes read_events()
        return, which is what makes this wrapper get sent). Stopping
        early on the pipeline's own "error"/"run_complete" event — an
        earlier version of this method did exactly that — races that DB
        write: a caller who saw "error" and immediately called get_run()
        could get a 404 "not yet complete" even though the stream had
        already told them the run was done. Caught via this SDK's own
        integration tests (test_get_run_reflects_a_real_run).

        Raises StreamTimeoutError if the stream goes quiet for longer
        than `timeout` without the connection closing."""
        with httpx.Client(base_url=self._base_url, timeout=httpx.Timeout(timeout, connect=10.0)) as stream_client:
            try:
                with stream_client.stream("GET", f"/stream/{run_id}") as response:
                    _raise_for_status(response)
                    for line in response.iter_lines():
                        event = _parse_sse_line(line)
                        if event is not None:
                            yield event
            except httpx.ReadTimeout as e:
                raise StreamTimeoutError(
                    f"stream for run {run_id} timed out after {timeout}s with no terminal event"
                ) from e

    def run_and_wait(self, task: str, timeout: float = DEFAULT_STREAM_TIMEOUT_SECONDS) -> dict:
        """Convenience wrapper: start a run and block until it reaches a
        terminal event, returning that event. Most agent integrations
        want this rather than manually wiring run_agent()+stream()."""
        started = self.run_agent(task)
        final_event = None
        for event in self.stream(started["run_id"], timeout=timeout):
            final_event = event
        return final_event


class AsyncTrustChainClient(_BaseClient):
    """Async counterpart to TrustChainClient — identical method surface,
    `await`ed. Prefer this inside an async agent framework (this
    backend's own pipeline is async end-to-end via LangGraph)."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        super().__init__(api_key, base_url, timeout)
        self._client = httpx.AsyncClient(base_url=self._base_url, headers=self._headers, timeout=self._timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncTrustChainClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        response = await self._client.request(method, path, **kwargs)
        _raise_for_status(response)
        return response.json()

    async def run_agent(self, task: str, run_id: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        body = {"task": task}
        if run_id:
            body["run_id"] = run_id
        return await self._request("POST", "/run-agent", json=body, headers=headers)

    async def get_run(self, run_id: str) -> dict:
        return await self._request("GET", f"/runs/{run_id}")

    async def list_runs(self, limit: int = 50) -> dict:
        return await self._request("GET", "/runs", params={"limit": limit})

    async def trust_scores(self, run_id: str) -> dict:
        return await self._request("GET", "/trust-scores", params={"run_id": run_id})

    async def trust_score_history(self, run_id: str) -> dict:
        return await self._request("GET", "/trust-scores/history", params={"run_id": run_id})

    async def leaderboard(self, max_runs: int = 50) -> dict:
        return await self._request("GET", "/leaderboard", params={"max_runs": max_runs})

    async def audit_log(self, run_id: Optional[str] = None) -> dict:
        params = {"run_id": run_id} if run_id else {}
        return await self._request("GET", "/audit-log", params=params)

    async def stream(self, run_id: str, timeout: float = DEFAULT_STREAM_TIMEOUT_SECONDS) -> AsyncIterator[dict]:
        """See TrustChainClient.stream's docstring — same "consume to the
        stream's natural end, don't stop early on the pipeline's own
        error/run_complete event" reasoning applies here identically."""
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=httpx.Timeout(timeout, connect=10.0)
        ) as stream_client:
            try:
                async with stream_client.stream("GET", f"/stream/{run_id}") as response:
                    _raise_for_status(response)
                    async for line in response.aiter_lines():
                        event = _parse_sse_line(line)
                        if event is not None:
                            yield event
            except httpx.ReadTimeout as e:
                raise StreamTimeoutError(
                    f"stream for run {run_id} timed out after {timeout}s with no terminal event"
                ) from e

    async def run_and_wait(self, task: str, timeout: float = DEFAULT_STREAM_TIMEOUT_SECONDS) -> dict:
        started = await self.run_agent(task)
        final_event = None
        async for event in self.stream(started["run_id"], timeout=timeout):
            final_event = event
        return final_event
