import { authHeader } from "@/lib/auth"

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

// ── POST /auth/signup, POST /auth/login ───────────────────────────────────────
export async function signup(name: string, email: string, password: string) {
    const res = await fetch(`${API}/auth/signup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, email, password }),
    })
    if (!res.ok) {
        const detail = await res.json().catch(() => null)
        throw new Error(detail?.detail ?? `signup failed: ${res.status}`)
    }
    return res.json()  // { token, name, email }
}

export async function login(email: string, password: string) {
    const res = await fetch(`${API}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
    })
    if (!res.ok) {
        const detail = await res.json().catch(() => null)
        throw new Error(detail?.detail ?? `login failed: ${res.status}`)
    }
    return res.json()  // { token, name, email }
}

// ── POST /run-agent ───────────────────────────────────────────────────────────
export async function startRun(task: string): Promise<{ run_id: string; stream_url: string }> {
    const res = await fetch(`${API}/run-agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeader() },
        body: JSON.stringify({ task }),
    })
    if (res.status === 401) throw new Error("Please log in again — your session expired.")
    if (!res.ok) throw new Error(`start run failed: ${res.status}`)
    return res.json()
}

// ── GET /chain-status ─────────────────────────────────────────────────────────
export async function getChainStatus() {
    const res = await fetch(`${API}/chain-status`)
    if (!res.ok) throw new Error("chain status failed")
    return res.json()
}

// ── GET /trust-scores ─────────────────────────────────────────────────────────
export async function getTrustScores(runId: string) {
    const res = await fetch(`${API}/trust-scores?run_id=${runId}`)
    if (!res.ok) throw new Error("trust scores failed")
    return res.json()  // { runId, scores: TrustScore[] }
}

// ── GET /trust-scores/history ─────────────────────────────────────────────────
export async function getTrustScoreHistory(runId: string) {
    const res = await fetch(`${API}/trust-scores/history?run_id=${runId}`)
    if (!res.ok) throw new Error("trust score history failed")
    return res.json()  // { runId, history: Record<agentId, ScoreHistoryPoint[]> }
}

// ── GET /audit-log ────────────────────────────────────────────────────────────
export async function getAuditLog(runId?: string) {
    const url = runId ? `${API}/audit-log?run_id=${runId}` : `${API}/audit-log`
    const res = await fetch(url)
    if (!res.ok) throw new Error("audit log failed")
    return res.json()  // { entries, total }
}

// ── POST /verify — check all 4 agent code hashes ─────────────────────────────
// OLD: verifyIntegrity(agentId, codeHashHex)  ← WRONG, backend expects { runId }
// NEW: verifyRun(runId) sends { runId } matching backend VerifyRequest model
export async function verifyRun(runId: string) {
    const res = await fetch(`${API}/verify`, {
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
    const res = await fetch(`${API}/verify/tamper-demo?agent_id=${agentId}`)
    if (!res.ok) throw new Error(`tamper demo failed: ${res.status}`)
    return res.json()
    // { agentId, real: {matches,exists,verified,hash,simulatedModel}, tampered: {...} }
}

// ── GET /verify-audit — check all audit entries for a run ────────────────────
export async function verifyAudit(runId: string) {
    const res = await fetch(`${API}/verify-audit?run_id=${runId}`)
    if (!res.ok) throw new Error(`verify audit failed: ${res.status}`)
    return res.json()
    // { runId, allMatch, entries: [{ entryId, agentId, action, actionMatch, inputMatch, outputMatch, txHash }] }
}

// ── GET /runs/{runId} ─────────────────────────────────────────────────────────
export async function getRun(runId: string) {
    const res = await fetch(`${API}/runs/${runId}`)
    if (!res.ok) throw new Error("get run failed")
    return res.json()
}

// ── GET /runs — run history, persisted in SQLite (survives restarts) ─────────
export async function getRuns(limit = 50) {
    const res = await fetch(`${API}/runs?limit=${limit}`)
    if (!res.ok) throw new Error("get runs failed")
    return res.json()  // { runs: RunRecord[], total }
}

// ── GET /leaderboard ──────────────────────────────────────────────────────────
export async function getLeaderboard(maxRuns = 50) {
    const res = await fetch(`${API}/leaderboard?max_runs=${maxRuns}`)
    if (!res.ok) throw new Error("leaderboard failed")
    return res.json()  // { agents: [{agentId, avgScore, bestScore, runsCount}], totalRuns, runsConsidered }
}

// ── SSE stream URL ────────────────────────────────────────────────────────────
export const streamUrl = (runId: string) => `${API}/stream/${runId}`