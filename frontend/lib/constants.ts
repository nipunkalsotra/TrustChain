// ─── Design Tokens ────────────────────────────────────────────────────────────
export const C = {
    bg0: "#030c0c",
    bg1: "#050f0f",
    bg2: "#060f0f",
    bg3: "#081818",
    border: "#0a2a2a",
    border2: "#0a3a3a",
    dim: "#2a4a4a",
    muted: "#4a6a6a",
    sub: "#667a7a",
    text: "#aacccc",
    bright: "#cce8e8",
    green: "#00ffcc",
    purple: "#bf7fff",
    yellow: "#ffcc00",
    blue: "#00ccff",
    red: "#ff4444",
    orange: "#ff8800",
} as const

// ─── Agent Config ─────────────────────────────────────────────────────────────
export const AGENT_IDS = ["researcher", "validator", "scorer", "reporter"] as const
export type AgentId = typeof AGENT_IDS[number]

export const AGENT_COLORS: Record<string, string> = {
    researcher: C.green,
    validator: C.purple,
    scorer: C.yellow,
    reporter: C.blue,
}

export const AGENT_LABELS: Record<string, string> = {
    researcher: "RESEARCHER",
    validator: "VALIDATOR",
    scorer: "SCORER",
    reporter: "REPORTER",
}

// ─── Mock Data ────────────────────────────────────────────────────────────────
export const MOCK_AUDIT = [
    { entryId: 0, agentId: "researcher", action: "web_search_started", inputHash: "0xab12cd34ef56ab12", outputHash: "0x7890ef12ab345678", timestamp: 1749220863, stepIndex: 0, txHash: "0xc8ff47a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9", runId: "run_20260607_184050" },
    { entryId: 1, agentId: "researcher", action: "web_search_complete", inputHash: "0xcd34ef56ab12cd34", outputHash: "0x1234ab56cd78ef90", timestamp: 1749220867, stepIndex: 1, txHash: "0xd9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0", runId: "run_20260607_184050" },
    { entryId: 2, agentId: "researcher", action: "synthesis_complete", inputHash: "0xef56ab12cd34ef56", outputHash: "0x5678cd90ef12ab34", timestamp: 1749220871, stepIndex: 2, txHash: "0xea1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1", runId: "run_20260607_184050" },
    { entryId: 3, agentId: "validator", action: "validation_started", inputHash: "0xab78cd90ef12ab78", outputHash: "0x9012ef34ab56cd78", timestamp: 1749220875, stepIndex: 3, txHash: "0xfb2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2", runId: "run_20260607_184050" },
    { entryId: 4, agentId: "validator", action: "source_check_complete", inputHash: "0xcd90ef12ab34cd90", outputHash: "0x3456ab78cd90ef12", timestamp: 1749220879, stepIndex: 4, txHash: "0x0c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3", runId: "run_20260607_184050" },
    { entryId: 5, agentId: "validator", action: "validation_complete", inputHash: "0xef12ab34cd56ef12", outputHash: "0x7890cd12ef34ab56", timestamp: 1749220883, stepIndex: 5, txHash: "0x1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4", runId: "run_20260607_184050" },
    { entryId: 6, agentId: "scorer", action: "scoring_started", inputHash: "0xab34cd56ef78ab34", outputHash: "0x1234ef56ab78cd90", timestamp: 1749220887, stepIndex: 6, txHash: "0x2e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5", runId: "run_20260607_184050" },
    { entryId: 7, agentId: "scorer", action: "scores_written", inputHash: "0xcd56ef78ab90cd56", outputHash: "0x5678ab12cd34ef56", timestamp: 1749220891, stepIndex: 7, txHash: "0x3f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6", runId: "run_20260607_184050" },
    { entryId: 8, agentId: "reporter", action: "report_started", inputHash: "0xef78ab90cd12ef78", outputHash: "0x9012cd34ef56ab78", timestamp: 1749220895, stepIndex: 8, txHash: "0x4a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7", runId: "run_20260607_184050" },
    { entryId: 9, agentId: "reporter", action: "report_complete", inputHash: "0xab90cd12ef34ab90", outputHash: "0x3456ef78ab90cd12", timestamp: 1749220899, stepIndex: 9, txHash: "0x5b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8", runId: "run_20260607_184050" },
]

export const MOCK_SCORES: Record<string, number> = {
    researcher: 88,
    validator: 76,
    scorer: 82,
    reporter: 91,
}

export const MOCK_SCORE_HISTORY = [
    { step: 1, researcher: 20, validator: 0, scorer: 0, reporter: 0 },
    { step: 2, researcher: 55, validator: 0, scorer: 0, reporter: 0 },
    { step: 3, researcher: 88, validator: 0, scorer: 0, reporter: 0 },
    { step: 4, researcher: 88, validator: 30, scorer: 0, reporter: 0 },
    { step: 5, researcher: 88, validator: 60, scorer: 0, reporter: 0 },
    { step: 6, researcher: 88, validator: 76, scorer: 0, reporter: 0 },
    { step: 7, researcher: 88, validator: 76, scorer: 40, reporter: 0 },
    { step: 8, researcher: 88, validator: 76, scorer: 82, reporter: 0 },
    { step: 9, researcher: 88, validator: 76, scorer: 82, reporter: 45 },
    { step: 10, researcher: 88, validator: 76, scorer: 82, reporter: 91 },
]

export const MOCK_RUNS = [
    { runId: "run_20260607_184050", task: "Research top 3 blockchain projects built on Monad testnet in 2025", status: "complete", steps: 10, avgScore: 84, ts: 1749220899 },
    { runId: "run_20260607_161230", task: "Analyze DeFi yield strategies on Monad ecosystem", status: "complete", steps: 10, avgScore: 79, ts: 1749211950 },
    { runId: "run_20260607_143500", task: "Compare Monad vs Ethereum transaction throughput benchmarks", status: "complete", steps: 9, avgScore: 91, ts: 1749205700 },
]

// ─── Utility Functions ────────────────────────────────────────────────────────
export const shortHash = (h: string) => h ? `${h.slice(0, 6)}…${h.slice(-4)}` : "—"
// FIX: was "explorer.monad.xyz" — that domain does not exist
export const txLink = (h: string) => `https://testnet.monadexplorer.com/tx/${h}`
export const fmtTime = (ts: number) => {
    const d = new Date(ts * 1000)
    return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })
}
export const fmtDate = (ts: number) => {
    const d = new Date(ts * 1000)
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
}