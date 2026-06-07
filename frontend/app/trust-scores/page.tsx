"use client"

import { useState, useEffect } from "react"
import { C, AGENT_IDS, AGENT_COLORS, AGENT_LABELS, MOCK_SCORE_HISTORY, fmtTime } from "@/lib/constants"
import { GaugeCircle, ScoreChart, TxLink } from "@/components/ui/TrustChainUI"
import { getTrustScores, getAuditLog } from "@/lib/api"
import type { TrustScore, AuditEntry } from "@/lib/types"

export default function TrustScoresPage() {
    const [runId, setRunId] = useState("")
    const [inputRunId, setInputRunId] = useState("")
    const [scores, setScores] = useState<TrustScore[]>([])
    const [entries, setEntries] = useState<AuditEntry[]>([])
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)

    const scoreMap: Record<string, number> = Object.fromEntries(
        scores.map(s => [s.agentId, s.score])
    )
    const avgScore = scores.length > 0
        ? Math.round(scores.reduce((a, s) => a + s.score, 0) / scores.length)
        : 0
    const leaderboard = AGENT_IDS
        .map(id => ({ id, score: scoreMap[id] ?? 0, color: AGENT_COLORS[id] }))
        .sort((a, b) => b.score - a.score)

    const fetchData = async (rid: string) => {
        if (!rid.trim()) return
        setLoading(true)
        setError(null)
        try {
            const [scoreData, auditData] = await Promise.all([
                getTrustScores(rid),
                getAuditLog(rid),
            ])
            setScores(scoreData.scores ?? [])
            setEntries(auditData.entries ?? [])
            setRunId(rid)
        } catch (e: any) {
            setError(e.message ?? "Failed to fetch data")
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="page-enter" style={{ padding: "24px", maxWidth: 1200, margin: "0 auto" }}>

            {/* Header + Run ID input */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
                <div>
                    <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 6 }}>
                        TRUSTSCOREREGISTRY · MONAD TESTNET
                    </div>
                    <h2 style={{ fontSize: 18, color: C.bright, fontWeight: 400, letterSpacing: "0.08em" }}>
                        AGENT TRUST SCORES
                    </h2>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <input
                        value={inputRunId}
                        onChange={e => setInputRunId(e.target.value)}
                        onKeyDown={e => e.key === "Enter" && fetchData(inputRunId)}
                        placeholder="Enter run ID..."
                        style={{
                            background: C.bg3, border: `1px solid ${C.border2}`,
                            color: C.bright, fontSize: 11, padding: "6px 12px",
                            borderRadius: 4, width: 240,
                        }}
                        onFocus={e => (e.target.style.borderColor = `${C.green}66`)}
                        onBlur={e => (e.target.style.borderColor = C.border2)}
                    />
                    <button
                        onClick={() => fetchData(inputRunId)}
                        disabled={!inputRunId.trim() || loading}
                        style={{
                            background: `${C.green}15`, border: `1px solid ${C.green}`,
                            color: C.green, fontSize: 10, letterSpacing: "0.1em",
                            padding: "6px 16px", borderRadius: 4, fontWeight: 600,
                            opacity: !inputRunId.trim() ? 0.4 : 1,
                        }}
                    >
                        {loading ? "LOADING…" : "LOAD"}
                    </button>
                </div>
            </div>

            {/* Error */}
            {error && (
                <div style={{
                    marginBottom: 20, padding: "10px 14px",
                    background: `${C.red}15`, border: `1px solid ${C.red}33`,
                    borderRadius: 6, fontSize: 11, color: C.red,
                }}>
                    ✗ {error}
                </div>
            )}

            {/* Empty state */}
            {!runId && !loading && (
                <div className="card" style={{ padding: 48, textAlign: "center" }}>
                    <div style={{ fontSize: 32, color: C.dim, marginBottom: 12 }}>◎</div>
                    <div style={{ fontSize: 11, color: C.dim, letterSpacing: "0.1em" }}>
                        ENTER A RUN ID TO LOAD TRUST SCORES FROM CHAIN
                    </div>
                    <div style={{ fontSize: 10, color: C.muted, marginTop: 8 }}>
                        Run IDs look like: run_20260607_184050
                    </div>
                </div>
            )}

            {/* Loading */}
            {loading && (
                <div className="card" style={{ padding: 48, textAlign: "center" }}>
                    <div style={{ fontSize: 24, animation: "spin 1s linear infinite", display: "inline-block", marginBottom: 12 }}>◈</div>
                    <div style={{ fontSize: 11, color: C.text, letterSpacing: "0.1em" }}>
                        FETCHING FROM MONAD TESTNET…
                    </div>
                </div>
            )}

            {/* Loaded data */}
            {runId && !loading && (
                <>
                    {/* Pipeline score banner */}
                    <div className="card" style={{ padding: "24px 32px", marginBottom: 20, display: "flex", alignItems: "center", gap: 32 }}>
                        <div style={{ textAlign: "center", minWidth: 120 }}>
                            <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 4 }}>PIPELINE SCORE</div>
                            <div style={{ fontFamily: "'Share Tech Mono',monospace", fontSize: 52, color: C.green, textShadow: `0 0 30px ${C.green}55`, lineHeight: 1 }}>
                                {avgScore}
                            </div>
                            <div style={{ fontSize: 9, color: C.dim, marginTop: 4 }}>OUT OF 100</div>
                            <div style={{ fontSize: 8, color: C.muted, marginTop: 6, letterSpacing: "0.05em" }}>{runId}</div>
                        </div>
                        <div style={{ flex: 1, borderLeft: `1px solid ${C.border}`, paddingLeft: 32 }}>
                            {leaderboard.map(({ id, score, color }) => (
                                <div key={id} style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
                                    <span style={{ fontSize: 9, letterSpacing: "0.1em", color, width: 90, flexShrink: 0 }}>{AGENT_LABELS[id]}</span>
                                    <div style={{ flex: 1, background: C.bg3, borderRadius: 2, height: 6, overflow: "hidden" }}>
                                        <div style={{ width: `${score}%`, height: "100%", background: color, borderRadius: 2, transition: "width 0.8s ease", boxShadow: `0 0 6px ${color}66` }} />
                                    </div>
                                    <span style={{ fontSize: 11, color, fontWeight: 700, minWidth: 32, textAlign: "right" }}>{score}</span>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* 4 Gauge cards with sparklines */}
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 16, marginBottom: 24 }}>
                        {AGENT_IDS.map(id => {
                            const agentEntries = entries.filter(e => e.agentId === id)
                            const lastEntry = agentEntries.at(-1)
                            return (
                                <div key={id} className="card" style={{ padding: 24, display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>
                                    <GaugeCircle agentId={id} score={scoreMap[id] ?? 0} size={120} fontSize={24} />
                                    <div style={{ width: "100%", borderTop: `1px solid ${C.border}`, paddingTop: 14 }}>
                                        <div style={{ fontSize: 9, color: C.dim, marginBottom: 6, letterSpacing: "0.08em" }}>SCORE OVER TIME</div>
                                        <ScoreChart history={MOCK_SCORE_HISTORY} agentId={id} />
                                    </div>
                                    <div style={{ fontSize: 9, color: C.dim, letterSpacing: "0.06em", textAlign: "center" }}>
                                        {agentEntries.length} STEPS
                                        {lastEntry && <span style={{ color: C.muted }}> · {fmtTime(lastEntry.timestamp)}</span>}
                                    </div>
                                </div>
                            )
                        })}
                    </div>

                    {/* Leaderboard table */}
                    <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 12 }}>LEADERBOARD · {runId}</div>
                    <div className="card" style={{ overflow: "hidden" }}>
                        <table style={{ width: "100%", borderCollapse: "collapse" }}>
                            <thead>
                                <tr style={{ borderBottom: `1px solid ${C.border}`, background: C.bg3 }}>
                                    {["RANK", "AGENT", "SCORE", "STEPS", "LAST UPDATED", "LAST TX"].map(h => (
                                        <th key={h} style={{ padding: "10px 16px", textAlign: "left", fontSize: 9, color: C.muted, letterSpacing: "0.1em", fontWeight: 400 }}>{h}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {leaderboard.map(({ id, score, color }, rank) => {
                                    const agentEntries = entries.filter(e => e.agentId === id)
                                    const lastEntry = agentEntries.at(-1)
                                    return (
                                        <tr key={id}
                                            style={{ borderBottom: `1px solid ${C.border}`, transition: "background 0.1s" }}
                                            onMouseEnter={e => (e.currentTarget.style.background = C.bg3)}
                                            onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                                        >
                                            <td style={{ padding: "12px 16px", fontSize: 16, color: rank === 0 ? C.yellow : C.dim, fontWeight: 700 }}>
                                                {rank === 0 ? "◆" : rank === 1 ? "◈" : rank === 2 ? "◉" : "·"} {rank + 1}
                                            </td>
                                            <td style={{ padding: "12px 16px" }}>
                                                <span style={{ fontSize: 10, fontWeight: 700, color, background: `${color}22`, padding: "3px 10px", borderRadius: 3, letterSpacing: "0.08em" }}>
                                                    {AGENT_LABELS[id]}
                                                </span>
                                            </td>
                                            <td style={{ padding: "12px 16px" }}>
                                                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                                    <span style={{ fontFamily: "'Share Tech Mono',monospace", fontSize: 20, color, fontWeight: 700 }}>{score}</span>
                                                    <span style={{ fontSize: 9, color: C.dim }}>/100</span>
                                                </div>
                                            </td>
                                            <td style={{ padding: "12px 16px", fontSize: 11, color: C.text }}>{agentEntries.length}</td>
                                            <td style={{ padding: "12px 16px", fontSize: 10, color: C.dim }}>
                                                {lastEntry ? fmtTime(lastEntry.timestamp) : "—"}
                                            </td>
                                            <td style={{ padding: "12px 16px" }}>
                                                <TxLink hash={lastEntry?.txHash} />
                                            </td>
                                        </tr>
                                    )
                                })}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
        </div>
    )
}