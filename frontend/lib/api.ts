const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

// ── POST /run-agent ───────────────────────────────────────────────────────────
export async function startRun(task: string): Promise<{ run_id: string; stream_url: string }> {
    const res = await fetch(`${API}/run-agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task }),
    })
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

// ── SSE stream URL ────────────────────────────────────────────────────────────
export const streamUrl = (runId: string) => `${API}/stream/${runId}`