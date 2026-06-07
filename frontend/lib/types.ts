// lib/types.ts — single source of truth for all types
// Matches EXACTLY what backend sends in SSE events

// ── SSE event shape — matches backend pipeline.py output ─────────────────────
export interface SSEEvent {
    // Step events (the main ones)
    agentId: string
    action: string
    txHash: string
    step: number        // backend sends "step" not "stepIndex"
    inputHash: string
    outputHash: string
    trustScore: number
    runId: string
    timestamp: number

    // Control events
    type?: "run_started" | "run_complete" | "error"
    task?: string
    report?: string
    score?: number
    txCount?: number
    txHashes?: string[]
    message?: string
}

// ── Audit entry — matches entries[] returned by GET /audit-log ───────────────
export interface AuditEntry {
    agentId: string
    action: string
    txHash: string
    stepIndex: number   // audit-log uses "stepIndex"; SSE uses "step"
    inputHash: string
    outputHash: string
    trustScore: number
    runId: string
    timestamp: number
}

// ── Chain status — matches GET /chain-status response ────────────────────────
export interface ChainStatus {
    connected: boolean
    chainId: number
    blockNumber: number
    rpcUrl: string
    contractsDeployed: number
}

// ── Trust score — matches GET /trust-scores response ─────────────────────────
export interface TrustScore {
    agentId: string
    runId: string
    score: number
}

// ── Verify result — matches POST /verify response ────────────────────────────
export interface VerifyResult {
    agentId: string
    matches: boolean
    exists: boolean
    verified: boolean
}

// ── Helpers ───────────────────────────────────────────────────────────────────
// FIX: was "explorer.monad.xyz" — that domain does not exist
export const txLink = (hash: string) =>
    `https://testnet.monadexplorer.com/tx/${hash}`

export const shortHash = (hash: string) =>
    hash ? `${hash.slice(0, 8)}...${hash.slice(-6)}` : ""

export const formatTimestamp = (ts: number) => {
    const d = new Date(ts * 1000)
    return d.toLocaleTimeString("en-US", { hour12: false })
}

// ── Step event type guard ─────────────────────────────────────────────────────
// Returns true only for real agent step events (have agentId + txHash)
// Filters out run_started, run_complete, error control events
export const isStepEvent = (e: SSEEvent): boolean =>
    Boolean(e.agentId && e.txHash && !e.type)