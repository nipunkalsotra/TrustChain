import { csrfHeader } from "@/lib/auth"

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"])
// Endpoints excluded from the silent-refresh-and-retry loop below: a failed
// login/signup IS the real answer, not an expired session to recover from,
// and retrying /auth/refresh or /auth/logout against themselves after their
// own 401 would either recurse or make no sense.
const NO_REFRESH_RETRY = new Set(["/auth/login", "/auth/signup", "/auth/refresh", "/auth/logout"])

// Every request now goes through here — `credentials: "include"` sends
// the HttpOnly tc_access session cookie (and receives Set-Cookie back),
// an unsafe method gets the CSRF header matched against tc_csrf (see
// backend/main.py's _csrf_protection_middleware), and a 401 gets exactly
// one silent POST /auth/refresh + retry before giving up — the tc_access
// cookie is only 15 minutes, so this is the normal path for any session
// that's been open a while, not an edge case.
async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
    const method = (options.method ?? "GET").toUpperCase()
    const headers = { ...options.headers, ...(UNSAFE_METHODS.has(method) ? csrfHeader() : {}) }
    const doFetch = () => fetch(`${API}${path}`, { ...options, headers, credentials: "include" })

    let res = await doFetch()
    if (res.status === 401 && !NO_REFRESH_RETRY.has(path)) {
        const refreshed = await fetch(`${API}/auth/refresh`, {
            method: "POST", credentials: "include", headers: csrfHeader(),
        })
        if (refreshed.ok) res = await doFetch()
    }
    return res
}

// ── POST /auth/signup, POST /auth/login ───────────────────────────────────────
// Both still return {token, name, email} in the body (SDK/CLI compatibility
// — see backend/main.py's own comment on that route) but the frontend now
// ignores `token` entirely; credentials:"include" (via apiFetch) is what
// actually establishes the session, through the Set-Cookie headers riding
// alongside that body.
export async function signup(name: string, email: string, password: string) {
    const res = await apiFetch("/auth/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, email, password }),
    })
    if (!res.ok) {
        const detail = await res.json().catch(() => null)
        throw new Error(detail?.detail ?? `signup failed: ${res.status}`)
    }
    return res.json()
}

export async function login(email: string, password: string) {
    const res = await apiFetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
    })
    if (!res.ok) {
        const detail = await res.json().catch(() => null)
        throw new Error(detail?.detail ?? `login failed: ${res.status}`)
    }
    return res.json()
}

// ── POST /auth/logout ─────────────────────────────────────────────────────────
export async function logout() {
    await apiFetch("/auth/logout", { method: "POST" })
}

// ── POST /run-agent ───────────────────────────────────────────────────────────
export async function startRun(task: string): Promise<{ run_id: string; stream_url: string }> {
    const res = await apiFetch("/run-agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task }),
    })
    if (res.status === 401) throw new Error("Please log in again — your session expired.")
    if (!res.ok) throw new Error(`start run failed: ${res.status}`)
    return res.json()
}

// ── GET /chain-status ─────────────────────────────────────────────────────────
export async function getChainStatus() {
    const res = await apiFetch("/chain-status")
    if (!res.ok) throw new Error("chain status failed")
    return res.json()
}

// ── GET /trust-scores ─────────────────────────────────────────────────────────
export async function getTrustScores(runId: string) {
    const res = await apiFetch(`/trust-scores?run_id=${runId}`)
    if (!res.ok) throw new Error("trust scores failed")
    return res.json()  // { runId, scores: TrustScore[] }
}

// ── GET /trust-scores/history ─────────────────────────────────────────────────
export async function getTrustScoreHistory(runId: string) {
    const res = await apiFetch(`/trust-scores/history?run_id=${runId}`)
    if (!res.ok) throw new Error("trust score history failed")
    return res.json()  // { runId, history: Record<agentId, ScoreHistoryPoint[]> }
}

// ── GET /audit-log ────────────────────────────────────────────────────────────
export async function getAuditLog(runId?: string) {
    const url = runId ? `/audit-log?run_id=${runId}` : "/audit-log"
    const res = await apiFetch(url)
    if (!res.ok) throw new Error("audit log failed")
    return res.json()  // { entries, total }
}

// ── POST /verify — check all 4 agent code hashes ─────────────────────────────
// OLD: verifyIntegrity(agentId, codeHashHex)  ← WRONG, backend expects { runId }
// NEW: verifyRun(runId) sends { runId } matching backend VerifyRequest model
export async function verifyRun(runId: string) {
    const res = await apiFetch("/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ runId }),
    })
    if (!res.ok) throw new Error(`verify failed: ${res.status}`)
    return res.json()
    // { runId, allMatch, agents: [{ agentId, exists, matches, verified, registeredHash }] }
}

// ── GET /verify/tamper-demo — read-only, no gas ───────────────────────────────
export async function tamperDemo(agentId: string) {
    const res = await apiFetch(`/verify/tamper-demo?agent_id=${agentId}`)
    if (!res.ok) throw new Error(`tamper demo failed: ${res.status}`)
    return res.json()
    // { agentId, real: {matches,exists,verified,hash,simulatedModel}, tampered: {...} }
}

// ── GET /verify-audit — check all audit entries for a run ────────────────────
export async function verifyAudit(runId: string) {
    const res = await apiFetch(`/verify-audit?run_id=${runId}`)
    if (!res.ok) throw new Error(`verify audit failed: ${res.status}`)
    return res.json()
    // { runId, allMatch, entries: [{ entryId, agentId, action, actionMatch, inputMatch, outputMatch, txHash }] }
}

// ── GET /runs/{runId} ─────────────────────────────────────────────────────────
export async function getRun(runId: string) {
    const res = await apiFetch(`/runs/${runId}`)
    if (!res.ok) throw new Error("get run failed")
    return res.json()
}

// ── GET /runs — run history, persisted in SQLite (survives restarts) ─────────
export async function getRuns(limit = 50) {
    const res = await apiFetch(`/runs?limit=${limit}`)
    if (!res.ok) throw new Error("get runs failed")
    return res.json()  // { runs: RunRecord[], total }
}

// ── GET /leaderboard ──────────────────────────────────────────────────────────
export async function getLeaderboard(maxRuns = 50) {
    const res = await apiFetch(`/leaderboard?max_runs=${maxRuns}`)
    if (!res.ok) throw new Error("leaderboard failed")
    return res.json()  // { agents: [{agentId, avgScore, bestScore, runsCount}], totalRuns, runsConsidered }
}

// ── SSE stream URL ────────────────────────────────────────────────────────────
// GET /stream/{run_id} requires a short-lived signed token (browser
// EventSource can't set an Authorization header — see backend/main.py's
// comment on that endpoint). There is no way to build a valid stream URL
// from just a run_id anymore — the only source of one is startRun()'s own
// `stream_url` field (POST /run-agent) or POST /runs/{runId}/stream-token
// (reconnecting after the original token expires) — this just resolves
// whichever relative URL one of those returned against the API host.
export const resolveStreamUrl = (relativeStreamUrl: string) => `${API}${relativeStreamUrl}`

// ── POST /runs/{runId}/stream-token — reconnect after the stream token in
// startRun()'s original stream_url has expired (5 minutes) ─────────────────
export async function refreshStreamToken(runId: string): Promise<{ stream_url: string }> {
    const res = await apiFetch(`/runs/${runId}/stream-token`, { method: "POST" })
    if (!res.ok) throw new Error(`refresh stream token failed: ${res.status}`)
    return res.json()
}
