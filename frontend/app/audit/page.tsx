"use client"

import { Fragment, useState, useEffect } from "react"
import { C, AGENT_IDS, AGENT_COLORS, AGENT_LABELS, shortHash, fmtTime, MOCK_AUDIT } from "@/lib/constants"
import { getAuditLog } from "@/lib/api"
import type { AuditEntry } from "@/lib/types"

// ─── Correct Monad testnet explorer URL ──────────────────────────────────────
const MONAD_TX = (hash: string) => `https://testnet.monadexplorer.com/tx/${hash}`

export default function AuditPage() {
    const [entries, setEntries] = useState<AuditEntry[]>([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)
    const [filter, setFilter] = useState("all")
    const [search, setSearch] = useState("")
    const [runFilter, setRunFilter] = useState("all")
    const [expanded, setExpanded] = useState<number | null>(null)
    const [usingMock, setUsingMock] = useState(false)

    // ── Fetch all audit entries on mount ──────────────────────────────────
    useEffect(() => {
        setLoading(true)
        getAuditLog()
            .then(data => {
                // API returns { entries: AuditEntry[], total: number }
                const arr: AuditEntry[] = Array.isArray(data)
                    ? data
                    : Array.isArray(data?.entries)
                        ? data.entries
                        : []

                if (arr.length === 0) {
                    // Backend returned nothing — show mock data so the UI isn't blank
                    setEntries(MOCK_AUDIT as unknown as AuditEntry[])
                    setUsingMock(true)
                } else {
                    setEntries(arr)
                    setUsingMock(false)
                }
                setError(null)
            })
            .catch(e => {
                // API unreachable — fall back to mock data
                setEntries(MOCK_AUDIT as unknown as AuditEntry[])
                setUsingMock(true)
                setError(e.message)
            })
            .finally(() => setLoading(false))
    }, [])

    // ── Derive unique run IDs from entries for the run selector ───────────
    const allRunIds = Array.from(
        new Set(entries.map(e => e.runId).filter(Boolean))
    ).sort().reverse()

    // ── Apply filters ─────────────────────────────────────────────────────
    const filtered = entries
        .filter(e => runFilter === "all" || e.runId === runFilter)
        .filter(e => filter === "all" || e.agentId === filter)
        .filter(e =>
            !search ||
            e.action.toLowerCase().includes(search.toLowerCase()) ||
            e.agentId.toLowerCase().includes(search.toLowerCase()) ||
            e.txHash.toLowerCase().includes(search.toLowerCase()) ||
            e.runId?.toLowerCase().includes(search.toLowerCase())
        )

    // ── Per-agent counts (scoped to current run filter) ───────────────────
    const scopedEntries = runFilter === "all"
        ? entries
        : entries.filter(e => e.runId === runFilter)

    return (
        <div className="page-enter" style={{ padding: "24px", maxWidth: 1200, margin: "0 auto" }}>

            {/* Header */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
                <div>
                    <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 6 }}>
                        AGENTAUDITLOG.SOL · MONAD TESTNET
                    </div>
                    <h2 style={{ fontSize: 18, color: C.bright, fontWeight: 400, letterSpacing: "0.08em" }}>
                        ON-CHAIN AUDIT LOG
                    </h2>
                </div>
                <div style={{ textAlign: "right" }}>
                    <div style={{ fontSize: 20, color: C.green, fontFamily: "'Share Tech Mono',monospace" }}>
                        {filtered.length}
                        <span style={{ fontSize: 10, color: C.muted, marginLeft: 6 }}>ENTRIES</span>
                    </div>
                    {runFilter !== "all" && (
                        <div style={{ fontSize: 9, color: C.dim, marginTop: 2 }}>
                            RUN: {runFilter}
                        </div>
                    )}
                </div>
            </div>

            {/* Mock data notice */}
            {usingMock && (
                <div style={{
                    marginBottom: 16, padding: "8px 14px",
                    background: "#0a1a0a", border: `1px solid ${C.dim}`,
                    borderRadius: 6, fontSize: 11, color: C.muted,
                    display: "flex", alignItems: "center", gap: 8,
                }}>
                    <span style={{ color: C.yellow }}>◎</span>
                    PREVIEW MODE — showing mock data. Connect backend to see live chain entries.
                </div>
            )}

            {/* RUN SELECTOR */}
            <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 9, letterSpacing: "0.15em", color: C.dim, marginBottom: 8 }}>
                    FILTER BY RUN
                </div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <button
                        onClick={() => setRunFilter("all")}
                        style={{
                            background: runFilter === "all" ? `${C.green}22` : "transparent",
                            border: `1px solid ${runFilter === "all" ? C.green : C.border}`,
                            color: runFilter === "all" ? C.green : C.sub,
                            fontSize: 10,
                            letterSpacing: "0.08em",
                            padding: "4px 12px",
                            borderRadius: 4,
                            fontFamily: "inherit",
                            cursor: "pointer",
                            transition: "all 0.15s",
                        }}
                    >
                        ALL RUNS
                    </button>
                    {allRunIds.map(runId => (
                        <button
                            key={runId}
                            onClick={() => setRunFilter(runId)}
                            style={{
                                background: runFilter === runId ? `${C.green}22` : "transparent",
                                border: `1px solid ${runFilter === runId ? C.green : C.border}`,
                                color: runFilter === runId ? C.green : C.sub,
                                fontSize: 10,
                                letterSpacing: "0.06em",
                                padding: "4px 12px",
                                borderRadius: 4,
                                fontFamily: "monospace",
                                cursor: "pointer",
                                transition: "all 0.15s",
                            }}
                        >
                            {runId}
                        </button>
                    ))}
                    {loading && (
                        <span style={{ fontSize: 10, color: C.dim, alignSelf: "center", marginLeft: 4 }}>
                            loading runs…
                        </span>
                    )}
                </div>
            </div>

            {/* Agent filter + Search */}
            <div style={{ display: "flex", gap: 8, marginBottom: 20, flexWrap: "wrap" }}>
                {["all", ...AGENT_IDS].map(f => {
                    const active = filter === f
                    const color = f === "all" ? C.green : AGENT_COLORS[f]
                    return (
                        <button
                            key={f}
                            onClick={() => setFilter(f)}
                            style={{
                                background: active ? `${color}22` : "transparent",
                                border: `1px solid ${active ? color : C.border}`,
                                color: active ? color : C.sub,
                                fontSize: 10,
                                letterSpacing: "0.1em",
                                padding: "4px 14px",
                                borderRadius: 4,
                                fontFamily: "inherit",
                                cursor: "pointer",
                                transition: "all 0.15s",
                            }}
                        >
                            {f === "all" ? "ALL AGENTS" : AGENT_LABELS[f]}
                        </button>
                    )
                })}
                <input
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    placeholder="Search action, agent, tx hash, run id..."
                    style={{
                        marginLeft: "auto",
                        background: C.bg3,
                        border: `1px solid ${C.border2}`,
                        borderRadius: 4,
                        color: C.bright,
                        fontSize: 11,
                        padding: "4px 12px",
                        width: 260,
                        outline: "none",
                    }}
                />
            </div>

            {/* Per-agent step counts */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12, marginBottom: 24 }}>
                {AGENT_IDS.map(id => {
                    const count = scopedEntries.filter(e => e.agentId === id).length
                    const color = AGENT_COLORS[id]
                    return (
                        <div key={id} className="card" style={{ padding: "14px 16px", display: "flex", alignItems: "center", gap: 12 }}>
                            <div style={{
                                width: 8, height: 8, borderRadius: "50%",
                                background: color, boxShadow: `0 0 6px ${color}`, flexShrink: 0,
                            }} />
                            <div>
                                <div style={{ fontSize: 9, letterSpacing: "0.1em", color, marginBottom: 2 }}>
                                    {AGENT_LABELS[id]}
                                </div>
                                <div style={{ fontSize: 20, color: C.bright, fontWeight: 700, lineHeight: 1 }}>
                                    {count}
                                </div>
                                <div style={{ fontSize: 8, color: C.dim }}>STEPS LOGGED</div>
                            </div>
                        </div>
                    )
                })}
            </div>

            {/* Error state — shown alongside mock data, not instead of it */}
            {error && !usingMock && (
                <div style={{
                    marginBottom: 16, padding: "10px 14px",
                    background: "#1a0808", border: "1px solid #ff444433",
                    borderRadius: 6, fontSize: 12, color: "#ff6666",
                }}>
                    ✗ Failed to load audit log: {error}
                </div>
            )}

            {/* Table */}
            <div className="card" style={{ overflow: "hidden" }}>
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                    <thead>
                        <tr style={{ borderBottom: `1px solid ${C.border}`, background: C.bg3 }}>
                            {["#", "RUN", "AGENT", "ACTION", "INPUT HASH", "OUTPUT HASH", "TIME", "TX"].map(h => (
                                <th key={h} style={{
                                    padding: "10px 14px",
                                    textAlign: "left",
                                    fontSize: 9,
                                    color: C.muted,
                                    letterSpacing: "0.1em",
                                    fontWeight: 400,
                                }}>{h}</th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {loading ? (
                            <tr>
                                <td colSpan={8} style={{ padding: "40px 0", textAlign: "center", fontSize: 11, color: C.dim }}>
                                    LOADING CHAIN DATA…
                                </td>
                            </tr>
                        ) : filtered.length === 0 ? (
                            <tr>
                                <td colSpan={8} style={{ padding: "40px 0", textAlign: "center", fontSize: 11, color: C.dim, letterSpacing: "0.1em" }}>
                                    NO ENTRIES MATCH FILTER
                                </td>
                            </tr>
                        ) : (
                            filtered.map((e, idx) => {
                                const color = AGENT_COLORS[e.agentId] ?? C.green
                                const isExp = expanded === idx

                                return (
                                    <Fragment key={`${e.runId}-${e.stepIndex}-${idx}`}>
                                        <tr
                                            style={{
                                                borderBottom: `1px solid ${C.border}`,
                                                cursor: "pointer",
                                                transition: "background 0.1s",
                                                background: isExp ? C.bg3 : "transparent",
                                            }}
                                            onClick={() => setExpanded(isExp ? null : idx)}
                                            onMouseEnter={ev => {
                                                if (!isExp) (ev.currentTarget as HTMLElement).style.background = C.bg3
                                            }}
                                            onMouseLeave={ev => {
                                                if (!isExp) (ev.currentTarget as HTMLElement).style.background = "transparent"
                                            }}
                                        >
                                            <td style={{ padding: "10px 14px", fontSize: 10, color: C.dim }}>
                                                {e.stepIndex ?? idx}
                                            </td>
                                            <td style={{ padding: "10px 14px" }}>
                                                <span
                                                    onClick={ev => { ev.stopPropagation(); setRunFilter(e.runId) }}
                                                    style={{
                                                        fontSize: 9,
                                                        color: C.dim,
                                                        fontFamily: "monospace",
                                                        cursor: "pointer",
                                                        padding: "2px 6px",
                                                        borderRadius: 3,
                                                        border: `1px solid ${C.border}`,
                                                        transition: "all 0.15s",
                                                    }}
                                                    onMouseEnter={ev => (ev.currentTarget.style.color = C.green)}
                                                    onMouseLeave={ev => (ev.currentTarget.style.color = C.dim)}
                                                    title="Click to filter to this run"
                                                >
                                                    {e.runId ?? "—"}
                                                </span>
                                            </td>
                                            <td style={{ padding: "10px 14px" }}>
                                                <span style={{
                                                    fontSize: 9,
                                                    fontWeight: 700,
                                                    color,
                                                    background: `${color}22`,
                                                    padding: "2px 7px",
                                                    borderRadius: 3,
                                                    letterSpacing: "0.08em",
                                                }}>
                                                    {AGENT_LABELS[e.agentId] ?? e.agentId?.toUpperCase()}
                                                </span>
                                            </td>
                                            <td style={{ padding: "10px 14px", fontSize: 11, color: C.text }}>
                                                {e.action}
                                            </td>
                                            <td style={{ padding: "10px 14px", fontSize: 10, color: C.muted, fontFamily: "monospace" }}>
                                                {shortHash(e.inputHash)}
                                            </td>
                                            <td style={{ padding: "10px 14px", fontSize: 10, color: C.muted, fontFamily: "monospace" }}>
                                                {shortHash(e.outputHash)}
                                            </td>
                                            <td style={{ padding: "10px 14px", fontSize: 10, color: C.dim }}>
                                                {fmtTime(e.timestamp)}
                                            </td>
                                            <td style={{ padding: "10px 14px" }}>
                                                <a
                                                    href={MONAD_TX(e.txHash)}
                                                    target="_blank"
                                                    rel="noopener noreferrer"
                                                    onClick={ev => ev.stopPropagation()}
                                                    style={{
                                                        color: C.green,
                                                        fontSize: 10,
                                                        textDecoration: "none",
                                                        opacity: 0.6,
                                                        transition: "opacity 0.15s",
                                                    }}
                                                    onMouseEnter={ev => (ev.currentTarget.style.opacity = "1")}
                                                    onMouseLeave={ev => (ev.currentTarget.style.opacity = "0.6")}
                                                >
                                                    {shortHash(e.txHash)} ↗
                                                </a>
                                            </td>
                                        </tr>

                                        {/* Expanded detail row */}
                                        {isExp && (
                                            <tr style={{ background: "#030c0c" }}>
                                                <td colSpan={8} style={{ padding: "16px 20px" }}>
                                                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16, marginBottom: 12 }}>
                                                        {[
                                                            { label: "FULL INPUT HASH", value: e.inputHash },
                                                            { label: "FULL OUTPUT HASH", value: e.outputHash },
                                                            { label: "FULL TX HASH", value: e.txHash },
                                                        ].map(({ label, value }) => (
                                                            <div key={label}>
                                                                <div style={{ fontSize: 9, color: C.muted, letterSpacing: "0.1em", marginBottom: 4 }}>
                                                                    {label}
                                                                </div>
                                                                <div style={{
                                                                    fontSize: 10,
                                                                    color: C.text,
                                                                    fontFamily: "monospace",
                                                                    wordBreak: "break-all",
                                                                    background: C.bg3,
                                                                    padding: "8px 10px",
                                                                    borderRadius: 4,
                                                                    border: `1px solid ${C.border}`,
                                                                }}>
                                                                    {value}
                                                                </div>
                                                            </div>
                                                        ))}
                                                    </div>
                                                    <a
                                                        href={MONAD_TX(e.txHash)}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        style={{
                                                            display: "inline-flex",
                                                            alignItems: "center",
                                                            gap: 6,
                                                            fontSize: 11,
                                                            color: C.green,
                                                            textDecoration: "none",
                                                            border: `1px solid ${C.green}44`,
                                                            padding: "5px 12px",
                                                            borderRadius: 4,
                                                            transition: "all 0.15s",
                                                        }}
                                                        onMouseEnter={ev => (ev.currentTarget.style.background = `${C.green}11`)}
                                                        onMouseLeave={ev => (ev.currentTarget.style.background = "transparent")}
                                                    >
                                                        ⛓ VIEW ON MONAD EXPLORER ↗
                                                    </a>
                                                </td>
                                            </tr>
                                        )}
                                    </Fragment>
                                )
                            })
                        )}
                    </tbody>
                </table>
            </div>
        </div>
    )
}