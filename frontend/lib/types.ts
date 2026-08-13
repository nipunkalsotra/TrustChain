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

// ── Run record — matches GET /runs and GET /runs/{runId} response ────────────
export interface RunRecord {
    runId:       string
    task:        string | null
    userEmail:   string | null
    status:      "running" | "complete" | "error"
    result:      Record<string, unknown> | null
    createdAt:   number | null
    completedAt: number | null
}

// ── Leaderboard entry — matches GET /leaderboard response ────────────────────
export interface LeaderboardEntry {
    agentId:   string
    avgScore:  number
    bestScore: number
    runsCount: number
}

// ── Score history point — matches GET /trust-scores/history response ────────
export interface ScoreHistoryPoint {
    score: number
    timestamp: number
    reason: string
}

// ── Verify result — matches POST /verify response ────────────────────────────
export interface VerifyResult {
    agentId: string
    matches: boolean
    exists: boolean
    verified: boolean
}

// ── Identity verify — matches POST /verify response (the full payload) ───────
export interface IdentityAgentVerify {
    agentId:        string
    exists:         boolean
    matches:        boolean
    verified:       boolean
    registeredHash: string
}

export interface IdentityVerifyResult {
    runId:    string
    allMatch: boolean
    agents:   IdentityAgentVerify[]
}

// ── Audit verify — matches GET /verify-audit response ─────────────────────────
export interface AuditVerifyEntry {
    entryId:      number
    agentId:      string
    action:       string
    actionMatch:  boolean
    inputMatch:   boolean
    outputMatch:  boolean
    txHash:       string | null
}

export interface AuditVerifyResult {
    runId:    string
    allMatch: boolean
    entries:  AuditVerifyEntry[]
}

// ── Tamper demo — matches GET /verify/tamper-demo response ───────────────────
export interface TamperCheck {
    matches:        boolean
    exists:         boolean
    verified:       boolean
    hash:           string
    simulatedModel: string
}

export interface TamperDemoResult {
    agentId:  string
    real:     TamperCheck
    tampered: TamperCheck
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