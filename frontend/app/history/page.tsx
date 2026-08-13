"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { C, fmtTime, fmtDate } from "@/lib/constants"
import { getRuns } from "@/lib/api"
import type { RunRecord } from "@/lib/types"

const STATUS_COLOR: Record<string, string> = {
    complete: C.green,
    running: C.yellow,
    error: C.red,
}

export default function HistoryPage() {
    const [runs, setRuns] = useState<RunRecord[]>([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)

    useEffect(() => {
        getRuns(100)
            .then(data => setRuns(data.runs ?? []))
            .catch(e => setError(e.message))
            .finally(() => setLoading(false))
    }, [])

    return (
        <div className="page-enter" style={{ padding: "24px", maxWidth: 1200, margin: "0 auto" }}>

            {/* Header */}
            <div style={{ marginBottom: 24 }}>
                <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 6 }}>
                    SQLITE · SURVIVES BACKEND RESTARTS
                </div>
                <h2 style={{ fontSize: 18, color: C.bright, fontWeight: 400, letterSpacing: "0.08em" }}>
                    RUN HISTORY
                </h2>
            </div>

            {error && (
                <div style={{
                    marginBottom: 16, padding: "10px 14px",
                    background: `${C.red}15`, border: `1px solid ${C.red}33`,
                    borderRadius: 6, fontSize: 11, color: C.red,
                }}>
                    ✗ Failed to load run history: {error}
                </div>
            )}

            {loading ? (
                <div className="card" style={{ padding: 48, textAlign: "center" }}>
                    <div style={{ fontSize: 24, animation: "spin 1s linear infinite", display: "inline-block", marginBottom: 12 }}>◈</div>
                    <div style={{ fontSize: 11, color: C.text, letterSpacing: "0.1em" }}>LOADING RUN HISTORY…</div>
                </div>
            ) : runs.length === 0 ? (
                <div className="card" style={{ padding: 48, textAlign: "center" }}>
                    <div style={{ fontSize: 32, color: C.dim, marginBottom: 12 }}>◷</div>
                    <div style={{ fontSize: 11, color: C.dim, letterSpacing: "0.1em" }}>
                        NO RUNS YET — START ONE FROM THE DASHBOARD
                    </div>
                </div>
            ) : (
                <div className="card" style={{ overflow: "hidden" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse" }}>
                        <thead>
                            <tr style={{ borderBottom: `1px solid ${C.border}`, background: C.bg3 }}>
                                {["RUN ID", "TASK", "STATUS", "STARTED", "ACTIONS"].map(h => (
                                    <th key={h} style={{ padding: "10px 14px", textAlign: "left", fontSize: 9, color: C.muted, letterSpacing: "0.1em", fontWeight: 400 }}>{h}</th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {runs.map(run => {
                                const color = STATUS_COLOR[run.status] ?? C.dim
                                return (
                                    <tr key={run.runId}
                                        style={{ borderBottom: `1px solid ${C.border}` }}
                                        onMouseEnter={e => (e.currentTarget.style.background = C.bg3)}
                                        onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                                    >
                                        <td style={{ padding: "10px 14px", fontSize: 10, fontFamily: "monospace", color: C.text }}>
                                            {run.runId}
                                        </td>
                                        <td style={{ padding: "10px 14px", fontSize: 11, color: C.text, maxWidth: 340, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                            {run.task ?? "—"}
                                        </td>
                                        <td style={{ padding: "10px 14px" }}>
                                            <span style={{
                                                fontSize: 9, fontWeight: 700, letterSpacing: "0.08em",
                                                color, background: `${color}22`, padding: "2px 8px", borderRadius: 3,
                                            }}>
                                                {run.status.toUpperCase()}
                                            </span>
                                        </td>
                                        <td style={{ padding: "10px 14px", fontSize: 10, color: C.dim }}>
                                            {run.createdAt ? `${fmtDate(run.createdAt)} · ${fmtTime(run.createdAt)}` : "—"}
                                        </td>
                                        <td style={{ padding: "10px 14px" }}>
                                            <div style={{ display: "flex", gap: 6 }}>
                                                {[
                                                    { label: "AUDIT", href: `/audit?run=${run.runId}` },
                                                    { label: "SCORES", href: `/trust-scores?run=${run.runId}` },
                                                    { label: "VERIFY", href: `/verify?run=${run.runId}` },
                                                ].map(a => (
                                                    <Link key={a.label} href={a.href} style={{
                                                        fontSize: 9, letterSpacing: "0.06em", color: C.green,
                                                        border: `1px solid ${C.green}44`, padding: "3px 8px",
                                                        borderRadius: 3, textDecoration: "none",
                                                    }}>
                                                        {a.label}
                                                    </Link>
                                                ))}
                                            </div>
                                        </td>
                                    </tr>
                                )
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    )
}
