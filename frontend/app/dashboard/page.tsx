"use client"

import { useState, useEffect, useRef } from "react"
import { C, AGENT_IDS, AGENT_COLORS, AGENT_LABELS, shortHash, txLink, fmtTime } from "@/lib/constants"
import { GaugeCircle, TxLink } from "@/components/ui/TrustChainUI"
import { useAgentStream } from "@/hooks/useAgentStream"
import { useTrustScores } from "@/hooks/useTrustScores"

export default function DashboardPage() {
    const [task, setTask] = useState("")
    const feedRef = useRef<HTMLDivElement>(null)

    // ── Real backend hooks ─────────────────────────────────────────────────
    // useAgentStream: connects to POST /run-agent + GET /stream/{id}
    // returns steps (SSE events), status, runId, error, report
    const { steps, status, runId, error, report, startRun, reset } = useAgentStream()

    // useTrustScores: polls GET /trust-scores?run_id=...
    // returns scores: { agentId, score }[]
    const { scores } = useTrustScores(runId, status)

    // ── Auto-scroll feed ───────────────────────────────────────────────────
    useEffect(() => {
        if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight
    }, [steps])

    // ── Derived values ─────────────────────────────────────────────────────
    const scoreFor = (id: string) => scores.find(s => s.agentId === id)?.score ?? 0

    const nonZeroScores = scores.filter(s => s.score > 0)
    const avgScore = nonZeroScores.length > 0
        ? Math.round(nonZeroScores.reduce((a, s) => a + s.score, 0) / nonZeroScores.length)
        : 0

    // Steps that have a confirmed tx hash
    const confirmedSteps = steps.filter(s => s.txHash)

    // Which agents have appeared in the feed
    const activeAgents = new Set(steps.map(s => s.agentId).filter(Boolean))

    const handleExecute = () => {
        if (!task.trim() || status === "running") return
        startRun(task.trim())
    }

    // ── Render ─────────────────────────────────────────────────────────────
    return (
        <div className="page-enter" style={{ padding: "24px", maxWidth: 1400, margin: "0 auto" }}>

            {/* Mission Input */}
            <div style={{ marginBottom: 24 }}>
                <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 10 }}>
                    MISSION INPUT
                </div>
                <div style={{ display: "flex", gap: 12 }}>
                    <textarea
                        value={task}
                        onChange={e => setTask(e.target.value)}
                        onKeyDown={e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleExecute() }}
                        placeholder="Enter research task for the agent pipeline..."
                        rows={3}
                        style={{
                            flex: 1, background: C.bg3, border: `1px solid ${C.border2}`,
                            borderRadius: 6, color: C.bright, fontSize: 13,
                            padding: "12px 16px", resize: "none",
                        }}
                        onFocus={e => (e.target.style.borderColor = `${C.green}66`)}
                        onBlur={e => (e.target.style.borderColor = C.border2)}
                    />
                    <button
                        onClick={status === "running" ? reset : handleExecute}
                        disabled={!task.trim() && status !== "running"}
                        style={{
                            padding: "0 28px", minWidth: 130,
                            background: status === "running" ? "#1a0808" : "#081a18",
                            border: `1px solid ${status === "running" ? C.red : C.green}`,
                            borderRadius: 6,
                            color: status === "running" ? C.red : C.green,
                            fontSize: 12, letterSpacing: "0.12em", fontWeight: 700,
                            boxShadow: status === "running" ? `0 0 16px ${C.red}33` : `0 0 16px ${C.green}22`,
                        }}
                    >
                        {status === "running" ? "■ ABORT" : "▶ EXECUTE"}
                    </button>
                </div>

                {/* Error banner */}
                {error && (
                    <div style={{
                        marginTop: 8, padding: "8px 12px",
                        background: "#1a0808", border: "1px solid #ff444433",
                        borderRadius: 4, fontSize: 12, color: "#ff6666",
                    }}>
                        ✗ {error}
                    </div>
                )}
            </div>

            {/* Main grid — feed left, scores right */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: 20 }}>

                {/* ── Live Agent Feed ────────────────────────────────────── */}
                <div>
                    <div style={{
                        fontSize: 9, letterSpacing: "0.2em", color: C.dim,
                        marginBottom: 10, display: "flex", justifyContent: "space-between",
                    }}>
                        <span>LIVE AGENT FEED</span>
                        {runId && <span style={{ color: C.muted }}>{runId}</span>}
                    </div>

                    <div ref={feedRef} className="card" style={{ height: 460, overflowY: "auto", padding: 14 }}>
                        {steps.length === 0 ? (
                            <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12 }}>
                                <div style={{ width: 48, height: 48, border: `2px solid ${C.border}`, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20 }}>◈</div>
                                <span style={{ fontSize: 10, color: C.dim, letterSpacing: "0.1em" }}>
                                    {status === "running" ? "INITIALISING PIPELINE..." : "PIPELINE IDLE"}
                                </span>
                                {status === "running" && (
                                    <span style={{ fontSize: 10, color: C.green, animation: "pulse 1.2s ease-in-out infinite" }}>
                                        AWAITING FIRST AGENT STEP
                                    </span>
                                )}
                            </div>
                        ) : (
                            steps.map((evt, i) => {
                                const color = AGENT_COLORS[evt.agentId] ?? C.green
                                return (
                                    <div key={`${evt.agentId}-${evt.step}-${i}`} style={{
                                        borderLeft: `2px solid ${color}`,
                                        background: "rgba(0,20,20,0.6)",
                                        padding: "10px 14px", marginBottom: 8,
                                        borderRadius: "0 6px 6px 0",
                                        animation: "fadeSlideIn 0.3s ease",
                                    }}>
                                        {/* Row header */}
                                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
                                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                                <span style={{
                                                    fontSize: 9, fontWeight: 700, color,
                                                    background: `${color}22`, padding: "2px 7px",
                                                    borderRadius: 3, letterSpacing: "0.08em",
                                                }}>
                                                    {AGENT_LABELS[evt.agentId] ?? evt.agentId?.toUpperCase()}
                                                </span>
                                                <span style={{ fontSize: 10, color: C.muted }}>
                                                    STEP {(evt.step ?? 0) + 1}
                                                </span>
                                                {evt.trustScore > 0 && (
                                                    <span style={{ fontSize: 9, color: `${color}88` }}>
                                                        score={evt.trustScore}
                                                    </span>
                                                )}
                                            </div>
                                            <span style={{ fontSize: 9, color: C.muted }}>
                                                {evt.timestamp ? fmtTime(evt.timestamp) : ""}
                                            </span>
                                        </div>

                                        {/* Action label */}
                                        <div style={{ fontSize: 11, color: C.text, marginBottom: 6 }}>
                                            {evt.action}
                                        </div>

                                        {/* TX hash link */}
                                        <TxLink hash={evt.txHash} />
                                    </div>
                                )
                            })
                        )}

                        {/* Pipeline complete banner */}
                        {status === "complete" && steps.length > 0 && (
                            <div style={{
                                textAlign: "center", padding: "12px 0",
                                fontSize: 10, color: C.green, letterSpacing: "0.1em",
                                borderTop: `1px solid ${C.border}`, marginTop: 8,
                            }}>
                                ✓ PIPELINE COMPLETE — {confirmedSteps.length} STEPS LOGGED ON-CHAIN
                            </div>
                        )}
                    </div>

                    {/* Agent progress tabs */}
                    {steps.length > 0 && (
                        <div style={{
                            display: "flex", gap: 2, marginTop: 8,
                            borderBottom: `1px solid ${C.border}`, paddingBottom: 2,
                        }}>
                            {AGENT_IDS.map(id => {
                                const done = activeAgents.has(id)
                                const color = AGENT_COLORS[id]
                                return (
                                    <div key={id} style={{
                                        fontSize: 9, padding: "3px 10px", letterSpacing: "0.06em",
                                        color: done ? color : C.dim,
                                        borderBottom: done ? `1px solid ${color}` : "none",
                                    }}>
                                        {AGENT_LABELS[id]}
                                    </div>
                                )
                            })}
                            {status === "complete" && (
                                <div style={{ fontSize: 9, padding: "3px 10px", letterSpacing: "0.06em", color: C.green, marginLeft: "auto" }}>
                                    COMPLETE ✓
                                </div>
                            )}
                        </div>
                    )}

                    {/* Final report — appears when run_complete fires */}
                    {report && (
                        <div style={{ marginTop: 20 }}>
                            <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 10 }}>
                                FINAL REPORT
                            </div>
                            <div className="card" style={{
                                padding: 20, fontSize: 13, color: C.text,
                                lineHeight: 1.8, whiteSpace: "pre-wrap", maxHeight: 400, overflowY: "auto",
                            }}>
                                {report}
                            </div>
                        </div>
                    )}
                </div>

                {/* ── Right panel: scores + metrics ─────────────────────── */}
                <div>
                    {/* Trust score gauges — reads from TrustScoreRegistry on Monad */}
                    <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 10 }}>
                        AGENT TRUST SCORES
                    </div>
                    <div className="card" style={{ padding: 20 }}>
                        <div style={{ fontSize: 8, color: C.dim, letterSpacing: "0.08em", marginBottom: 14, textAlign: "center" }}>
                            TRUSTSCOREREGISTRY · MONAD TESTNET
                        </div>
                        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, justifyItems: "center" }}>
                            {AGENT_IDS.map(id => (
                                <GaugeCircle key={id} agentId={id} score={scoreFor(id)} />
                            ))}
                        </div>

                        {/* Pipeline score summary */}
                        {runId && (
                            <div style={{
                                marginTop: 20, paddingTop: 14,
                                borderTop: `1px solid ${C.border}`, textAlign: "center",
                            }}>
                                <div style={{ fontSize: 9, color: C.dim, letterSpacing: "0.08em" }}>PIPELINE SCORE</div>
                                <div style={{
                                    fontFamily: "'Share Tech Mono',monospace",
                                    fontSize: 28, color: C.green,
                                    textShadow: `0 0 20px ${C.green}44`, marginTop: 4,
                                }}>
                                    {avgScore}
                                </div>
                                <div style={{ fontSize: 9, color: C.dim, marginTop: 2 }}>OUT OF 100</div>
                            </div>
                        )}
                    </div>

                    {/* Chain metrics */}
                    <div style={{ marginTop: 14 }}>
                        <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 10 }}>
                            CHAIN METRICS
                        </div>
                        <div className="card" style={{ padding: 16, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
                            {[
                                { label: "TOTAL TX", value: confirmedSteps.length, sub: "ON MONAD" },
                                { label: "PIPELINE SCORE", value: avgScore, sub: "OUT OF 100" },
                                { label: "RUNS", value: runId ? 1 : 0, sub: "THIS SESSION" },
                                { label: "STATUS", value: status.toUpperCase(), sub: status === "running" ? "LIVE" : "" },
                            ].map(({ label, value, sub }) => (
                                <div key={label}>
                                    <div style={{ fontSize: 8, color: C.muted, marginBottom: 4, letterSpacing: "0.1em" }}>
                                        {label}
                                    </div>
                                    <div style={{
                                        fontSize: 18, fontWeight: 700,
                                        color: status === "running" && label === "STATUS" ? C.green : C.bright,
                                        animation: status === "running" && label === "STATUS" ? "pulse 1.2s ease-in-out infinite" : "none",
                                    }}>
                                        {value}
                                    </div>
                                    <div style={{ fontSize: 8, color: C.dim, marginTop: 2 }}>{sub}</div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </div>

            {/* ── On-chain audit log table ───────────────────────────────── */}
            {confirmedSteps.length > 0 && (
                <div style={{ marginTop: 24 }}>
                    <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 10 }}>
                        ON-CHAIN AUDIT LOG
                    </div>
                    <div className="card" style={{ overflow: "hidden" }}>
                        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                            <thead>
                                <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                                    {["AGENT", "ACTION", "STEP", "TX HASH", "MONAD"].map(h => (
                                        <th key={h} style={{
                                            padding: "8px 14px", textAlign: "left",
                                            fontSize: 9, color: C.muted,
                                            letterSpacing: "0.08em", fontWeight: 400,
                                        }}>{h}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {confirmedSteps.map((evt, i) => (
                                    <tr
                                        key={i}
                                        style={{ borderBottom: "1px solid #0a1a1a", transition: "background 0.15s" }}
                                        onMouseEnter={e => (e.currentTarget.style.background = C.bg3)}
                                        onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                                    >
                                        <td style={{ padding: "8px 14px" }}>
                                            <span style={{
                                                color: AGENT_COLORS[evt.agentId] ?? C.bright,
                                                fontWeight: 600, fontSize: 10,
                                            }}>
                                                {AGENT_LABELS[evt.agentId] ?? evt.agentId?.toUpperCase()}
                                            </span>
                                        </td>
                                        <td style={{ padding: "8px 14px", color: C.sub }}>{evt.action}</td>
                                        <td style={{ padding: "8px 14px", color: C.muted }}>{evt.step}</td>
                                        <td style={{ padding: "8px 14px", color: "#4a8a8a", fontSize: 10, fontFamily: "monospace" }}>
                                            {shortHash(evt.txHash)}
                                        </td>
                                        <td style={{ padding: "8px 14px" }}>
                                            <TxLink hash={evt.txHash} />
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    )
}