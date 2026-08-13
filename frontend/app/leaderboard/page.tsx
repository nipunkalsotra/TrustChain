"use client"

import { useState, useEffect } from "react"
import { C, AGENT_COLORS, AGENT_LABELS } from "@/lib/constants"
import { getLeaderboard } from "@/lib/api"
import type { LeaderboardEntry } from "@/lib/types"

const RANK_ICON = ["◆", "◈", "◉"]

export default function LeaderboardPage() {
    const [agents, setAgents] = useState<LeaderboardEntry[]>([])
    const [totalRuns, setTotalRuns] = useState(0)
    const [runsConsidered, setRunsConsidered] = useState(0)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)

    useEffect(() => {
        getLeaderboard(50)
            .then(data => {
                setAgents(data.agents ?? [])
                setTotalRuns(data.totalRuns ?? 0)
                setRunsConsidered(data.runsConsidered ?? 0)
            })
            .catch(e => setError(e.message))
            .finally(() => setLoading(false))
    }, [])

    const isCapped = totalRuns > runsConsidered

    return (
        <div className="page-enter" style={{ padding: "24px", maxWidth: 900, margin: "0 auto" }}>

            {/* Header */}
            <div style={{ marginBottom: 24 }}>
                <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 6 }}>
                    TRUSTSCOREREGISTRY · AGGREGATED ACROSS ALL RUNS
                </div>
                <h2 style={{ fontSize: 18, color: C.bright, fontWeight: 400, letterSpacing: "0.08em" }}>
                    AGENT LEADERBOARD
                </h2>
            </div>

            {error && (
                <div style={{
                    marginBottom: 16, padding: "10px 14px",
                    background: `${C.red}15`, border: `1px solid ${C.red}33`,
                    borderRadius: 6, fontSize: 11, color: C.red,
                }}>
                    ✗ Failed to load leaderboard: {error}
                </div>
            )}

            {isCapped && !loading && (
                <div style={{
                    marginBottom: 16, padding: "8px 14px",
                    background: "#0a1a0a", border: `1px solid ${C.dim}`,
                    borderRadius: 6, fontSize: 11, color: C.muted,
                }}>
                    <span style={{ color: C.yellow }}>◎</span> Showing the {runsConsidered} most recent of {totalRuns} total runs.
                </div>
            )}

            {loading ? (
                <div className="card" style={{ padding: 48, textAlign: "center" }}>
                    <div style={{ fontSize: 24, animation: "spin 1s linear infinite", display: "inline-block", marginBottom: 12 }}>◈</div>
                    <div style={{ fontSize: 11, color: C.text, letterSpacing: "0.1em" }}>AGGREGATING ON-CHAIN SCORES…</div>
                </div>
            ) : agents.length === 0 ? (
                <div className="card" style={{ padding: 48, textAlign: "center" }}>
                    <div style={{ fontSize: 32, color: C.dim, marginBottom: 12 }}>▲</div>
                    <div style={{ fontSize: 11, color: C.dim, letterSpacing: "0.1em" }}>
                        NO RUNS SCORED YET
                    </div>
                </div>
            ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {agents.map((agent, rank) => {
                        const color = AGENT_COLORS[agent.agentId] ?? C.green
                        return (
                            <div key={agent.agentId} className="card" style={{
                                padding: "18px 24px", display: "flex", alignItems: "center", gap: 20,
                                borderLeft: `3px solid ${color}`,
                            }}>
                                <div style={{ fontSize: 22, color: rank === 0 ? C.yellow : C.dim, width: 36, textAlign: "center" }}>
                                    {RANK_ICON[rank] ?? "·"}
                                </div>
                                <div style={{ width: 40, fontSize: 14, color: C.muted, fontFamily: "'Share Tech Mono',monospace" }}>
                                    #{rank + 1}
                                </div>
                                <div style={{ flex: 1 }}>
                                    <span style={{
                                        fontSize: 12, fontWeight: 700, color,
                                        background: `${color}22`, padding: "3px 10px",
                                        borderRadius: 4, letterSpacing: "0.08em",
                                    }}>
                                        {AGENT_LABELS[agent.agentId] ?? agent.agentId.toUpperCase()}
                                    </span>
                                </div>
                                <div style={{ textAlign: "center", minWidth: 90 }}>
                                    <div style={{ fontFamily: "'Share Tech Mono',monospace", fontSize: 26, color, textShadow: `0 0 16px ${color}44` }}>
                                        {agent.avgScore}
                                    </div>
                                    <div style={{ fontSize: 8, color: C.dim, letterSpacing: "0.08em" }}>AVG SCORE</div>
                                </div>
                                <div style={{ textAlign: "center", minWidth: 70 }}>
                                    <div style={{ fontSize: 16, color: C.bright }}>{agent.bestScore}</div>
                                    <div style={{ fontSize: 8, color: C.dim, letterSpacing: "0.08em" }}>BEST</div>
                                </div>
                                <div style={{ textAlign: "center", minWidth: 70 }}>
                                    <div style={{ fontSize: 16, color: C.bright }}>{agent.runsCount}</div>
                                    <div style={{ fontSize: 8, color: C.dim, letterSpacing: "0.08em" }}>RUNS</div>
                                </div>
                            </div>
                        )
                    })}
                </div>
            )}
        </div>
    )
}
