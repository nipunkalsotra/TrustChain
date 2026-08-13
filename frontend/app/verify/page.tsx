"use client"

import { useState, useEffect } from "react"
import { useSearchParams } from "next/navigation"
import { C, AGENT_IDS, AGENT_COLORS, AGENT_LABELS } from "@/lib/constants"
import { TxLink } from "@/components/ui/TrustChainUI"
import { verifyRun, verifyAudit, tamperDemo } from "@/lib/api"
import { errorMessage } from "@/lib/utils"
import type { IdentityVerifyResult, AuditVerifyResult, TamperDemoResult } from "@/lib/types"

export default function VerifyPage() {
    const searchParams = useSearchParams()
    const runParam = searchParams.get("run")

    const [runId, setRunId] = useState(runParam ?? "")
    const [status, setStatus] = useState<"idle" | "loading" | "success" | "failed">("idle")
    const [result, setResult] = useState<IdentityVerifyResult | null>(null)
    const [auditResult, setAuditResult] = useState<AuditVerifyResult | null>(null)
    const [error, setError] = useState<string | null>(null)

    // ── Live tamper demo — read-only, no runId or gas needed ───────────────
    const [tamperAgent, setTamperAgent] = useState<string | null>(null)
    const [tamperLoading, setTamperLoading] = useState(false)
    const [tamperResult, setTamperResult] = useState<TamperDemoResult | null>(null)
    const [tamperError, setTamperError] = useState<string | null>(null)

    const runTamperDemo = async (agentId: string) => {
        setTamperAgent(agentId)
        setTamperLoading(true)
        setTamperResult(null)
        setTamperError(null)
        try {
            setTamperResult(await tamperDemo(agentId))
        } catch (e: unknown) {
            setTamperError(errorMessage(e, "Tamper demo failed — is the backend running?"))
        } finally {
            setTamperLoading(false)
        }
    }

    const handleVerify = async (overrideRunId?: string) => {
        const rid = overrideRunId ?? runId
        if (!rid.trim()) return
        setStatus("loading")
        setResult(null)
        setAuditResult(null)
        setError(null)

        try {
            // Run both checks in parallel — independent endpoints
            const [identityData, auditData] = await Promise.all([
                verifyRun(rid.trim()),
                verifyAudit(rid.trim()),
            ])

            setResult(identityData)
            setAuditResult(auditData)
            setStatus(identityData.allMatch && auditData.allMatch ? "success" : "failed")
        } catch (e: unknown) {
            setError(errorMessage(e, "Verification failed — is the backend running?"))
            setStatus("failed")
        }
    }

    // Auto-verify when arriving from a link like /verify?run=...
    // (runId itself already initializes from runParam above — no need to
    // set it again here.) Genuine data-fetching effect (network I/O +
    // loading/error state), so unlike a prop-to-state mirror this can't be
    // moved to render time without firing the fetch during render.
    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        if (runParam) handleVerify(runParam)
    }, [runParam])

    return (
        <div className="page-enter" style={{ padding: "24px", maxWidth: 960, margin: "0 auto" }}>

            {/* Header */}
            <div style={{ marginBottom: 32 }}>
                <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 6 }}>
                    AGENTIDENTITYREGISTRY + AGENTAUDITLOG · MONAD TESTNET
                </div>
                <h2 style={{ fontSize: 18, color: C.bright, fontWeight: 400, letterSpacing: "0.08em" }}>
                    INTEGRITY VERIFICATION
                </h2>
                <p style={{ fontSize: 12, color: C.text, marginTop: 10, lineHeight: 1.7 }}>
                    Two independent on-chain checks: agent code hashes against{" "}
                    <span style={{ color: C.purple }}>AgentIdentityRegistry</span> and every audit
                    entry against <span style={{ color: C.green }}>AgentAuditLog</span>.
                    Any mismatch proves tampering or an unauthorized agent version.
                </p>
            </div>

            {/* Input card */}
            <div className="card" style={{ padding: 24, marginBottom: 24 }}>
                <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 12 }}>RUN ID</div>
                <div style={{ display: "flex", gap: 12 }}>
                    <input
                        value={runId}
                        onChange={e => setRunId(e.target.value)}
                        onKeyDown={e => e.key === "Enter" && handleVerify()}
                        placeholder="e.g. run_20260607_184050"
                        style={{
                            flex: 1, background: C.bg3, border: `1px solid ${C.border2}`,
                            borderRadius: 6, color: C.bright, fontSize: 13, padding: "10px 14px",
                        }}
                        onFocus={e => (e.target.style.borderColor = `${C.green}66`)}
                        onBlur={e => (e.target.style.borderColor = C.border2)}
                    />
                    <button
                        onClick={() => handleVerify()}
                        disabled={!runId.trim() || status === "loading"}
                        style={{
                            background: status === "loading" ? C.bg3 : `${C.green}15`,
                            border: `1px solid ${C.green}`, color: C.green,
                            fontSize: 12, letterSpacing: "0.1em", padding: "0 24px",
                            borderRadius: 6, fontWeight: 600,
                            opacity: !runId.trim() ? 0.4 : 1,
                        }}
                    >
                        {status === "loading" ? "VERIFYING…" : "◆ VERIFY"}
                    </button>
                </div>
            </div>

            {/* Network error */}
            {error && status === "failed" && !result && (
                <div style={{
                    marginBottom: 20, padding: "12px 16px",
                    background: `${C.red}15`, border: `1px solid ${C.red}33`,
                    borderRadius: 8, fontSize: 11, color: C.red,
                }}>
                    ✗ {error}
                </div>
            )}

            {/* Loading */}
            {status === "loading" && (
                <div className="card" style={{ padding: 40, textAlign: "center" }}>
                    <div style={{ fontSize: 24, animation: "spin 1s linear infinite", display: "inline-block", marginBottom: 16 }}>◈</div>
                    <div style={{ fontSize: 11, color: C.text, letterSpacing: "0.1em" }}>VERIFYING ON-CHAIN…</div>
                    <div style={{ fontSize: 10, color: C.dim, marginTop: 8, animation: "pulse 1.5s ease-in-out infinite" }}>
                        CHECKING IDENTITY REGISTRY + AUDIT LOG · MONAD TESTNET
                    </div>
                </div>
            )}

            {/* ─────────────────────────────────────────────────────────────
                SECTION 1 — AGENT IDENTITY
            ───────────────────────────────────────────────────────────── */}
            {result && status !== "loading" && (
                <div style={{ animation: "fadeIn 0.4s ease", marginBottom: 32 }}>
                    <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 12 }}>
                        SECTION 1 · AGENT IDENTITY — AGENTIDENTITYREGISTRY.SOL
                    </div>

                    {/* Banner */}
                    <div style={{
                        background: result.allMatch ? `${C.green}15` : `${C.red}15`,
                        border: `1px solid ${result.allMatch ? C.green : C.red}`,
                        borderRadius: 8, padding: "20px 24px", marginBottom: 16,
                        display: "flex", alignItems: "center", gap: 16,
                    }}>
                        <span style={{ fontSize: 32, filter: `drop-shadow(0 0 10px ${result.allMatch ? C.green : C.red})` }}>
                            {result.allMatch ? "◆" : "✗"}
                        </span>
                        <div>
                            <div style={{ fontSize: 14, fontWeight: 700, letterSpacing: "0.1em", color: result.allMatch ? C.green : C.red, marginBottom: 4 }}>
                                {result.allMatch ? "ALL AGENTS VERIFIED" : "AGENT IDENTITY MISMATCH"}
                            </div>
                            <div style={{ fontSize: 11, color: C.text }}>
                                {result.allMatch
                                    ? `All 4 agents in run ${result.runId} match their registered on-chain code hashes.`
                                    : `One or more agents in run ${result.runId} do not match their registered hashes.`
                                }
                            </div>
                        </div>
                    </div>

                    {/* Per-agent cards */}
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                        {result.agents?.map((agent) => {
                            const pass = agent.verified ?? agent.matches
                            const color = AGENT_COLORS[agent.agentId]
                            return (
                                <div key={agent.agentId} className="card" style={{ padding: 20, borderLeft: `3px solid ${pass ? C.green : C.red}` }}>
                                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
                                        <span style={{ fontSize: 10, fontWeight: 700, color, letterSpacing: "0.1em" }}>
                                            {AGENT_LABELS[agent.agentId] ?? agent.agentId?.toUpperCase()}
                                        </span>
                                        <span style={{
                                            fontSize: 9, padding: "2px 8px", borderRadius: 3, letterSpacing: "0.1em", fontWeight: 700,
                                            background: pass ? `${C.green}22` : `${C.red}22`,
                                            color: pass ? C.green : C.red,
                                            border: `1px solid ${pass ? C.green : C.red}44`,
                                        }}>
                                            {pass ? "✓ VERIFIED" : "✗ MISMATCH"}
                                        </span>
                                    </div>
                                    <div style={{ display: "grid", gap: 8 }}>
                                        {[
                                            { label: "EXISTS ON-CHAIN", ok: agent.exists },
                                            { label: "CODE HASH MATCH", ok: agent.matches },
                                            { label: "FULLY VERIFIED", ok: agent.verified },
                                        ].map(({ label, ok }) => (
                                            <div key={label} style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                                                <span style={{ fontSize: 9, letterSpacing: "0.08em", color: C.muted }}>{label}</span>
                                                <span style={{ fontSize: 9, fontWeight: 700, color: ok ? C.green : C.red }}>
                                                    {ok ? "✓ PASS" : "✗ FAIL"}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                    {agent.registeredHash && (
                                        <div style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${C.border}` }}>
                                            <div style={{ fontSize: 8, color: C.dim, letterSpacing: "0.08em", marginBottom: 4 }}>
                                                REGISTERED CODE HASH
                                            </div>
                                            <div style={{ fontSize: 9, color: C.text, fontFamily: "monospace", wordBreak: "break-all" }}>
                                                {agent.registeredHash.slice(0, 10)}…{agent.registeredHash.slice(-8)}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )
                        })}
                    </div>
                </div>
            )}

            {/* ─────────────────────────────────────────────────────────────
                SECTION 2 — AUDIT ENTRY INTEGRITY
            ───────────────────────────────────────────────────────────── */}
            {auditResult && status !== "loading" && (
                <div style={{ animation: "fadeIn 0.5s ease" }}>
                    <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 12 }}>
                        SECTION 2 · ACTION INTEGRITY — AGENTAUDITLOG.SOL
                    </div>

                    {/* Banner */}
                    <div style={{
                        background: auditResult.allMatch ? `${C.green}15` : `${C.red}15`,
                        border: `1px solid ${auditResult.allMatch ? C.green : C.red}`,
                        borderRadius: 8, padding: "16px 24px", marginBottom: 16,
                        display: "flex", alignItems: "center", gap: 12,
                    }}>
                        <span style={{ fontSize: 24, color: auditResult.allMatch ? C.green : C.red }}>
                            {auditResult.allMatch ? "◆" : "✗"}
                        </span>
                        <div>
                            <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: "0.1em", color: auditResult.allMatch ? C.green : C.red, marginBottom: 4 }}>
                                {auditResult.allMatch ? "ALL AUDIT ENTRIES VERIFIED" : "AUDIT TAMPERING DETECTED"}
                            </div>
                            <div style={{ fontSize: 11, color: C.text }}>
                                {auditResult.entries?.length ?? 0} entries read twice from AgentAuditLog on Monad — both reads must agree.
                            </div>
                        </div>
                    </div>

                    {/* Per-entry table */}
                    <div className="card" style={{ overflow: "hidden", marginBottom: 12 }}>
                        <table style={{ width: "100%", borderCollapse: "collapse" }}>
                            <thead>
                                <tr style={{ borderBottom: `1px solid ${C.border}`, background: C.bg3 }}>
                                    {["AGENT", "ACTION", "ACTION ✓", "INPUT HASH ✓", "OUTPUT HASH ✓", "TX"].map(h => (
                                        <th key={h} style={{ padding: "8px 14px", textAlign: "left", fontSize: 9, color: C.muted, letterSpacing: "0.1em", fontWeight: 400 }}>{h}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {auditResult.entries?.map((e, i) => {
                                    const color = AGENT_COLORS[e.agentId]
                                    return (
                                        <tr key={i}
                                            style={{ borderBottom: `1px solid ${C.border}` }}
                                            onMouseEnter={ev => (ev.currentTarget.style.background = C.bg3)}
                                            onMouseLeave={ev => (ev.currentTarget.style.background = "transparent")}
                                        >
                                            <td style={{ padding: "8px 14px" }}>
                                                <span style={{ fontSize: 9, color, background: `${color}22`, padding: "2px 7px", borderRadius: 3, letterSpacing: "0.08em", fontWeight: 700 }}>
                                                    {AGENT_LABELS[e.agentId] ?? e.agentId?.toUpperCase()}
                                                </span>
                                            </td>
                                            <td style={{ padding: "8px 14px", fontSize: 10, color: C.text }}>{e.action}</td>
                                            <td style={{ padding: "8px 14px" }}>
                                                <span style={{ fontSize: 10, fontWeight: 700, color: e.actionMatch ? C.green : C.red }}>
                                                    {e.actionMatch ? "✓ MATCH" : "✗ FAIL"}
                                                </span>
                                            </td>
                                            <td style={{ padding: "8px 14px" }}>
                                                <span style={{ fontSize: 10, fontWeight: 700, color: e.inputMatch ? C.green : C.red }}>
                                                    {e.inputMatch ? "✓ MATCH" : "✗ FAIL"}
                                                </span>
                                            </td>
                                            <td style={{ padding: "8px 14px" }}>
                                                <span style={{ fontSize: 10, fontWeight: 700, color: e.outputMatch ? C.green : C.red }}>
                                                    {e.outputMatch ? "✓ MATCH" : "✗ FAIL"}
                                                </span>
                                            </td>
                                            <td style={{ padding: "8px 14px" }}>
                                                <TxLink hash={e.txHash ?? undefined} />
                                            </td>
                                        </tr>
                                    )
                                })}
                            </tbody>
                        </table>
                    </div>

                    {/* Summary counts */}
                    <div style={{ display: "flex", gap: 24, fontSize: 10, color: C.muted }}>
                        {[
                            { label: "TOTAL ENTRIES", value: auditResult.entries?.length ?? 0, vc: C.bright },
                            { label: "ACTION CHECKS", value: auditResult.entries?.filter((e) => e.actionMatch).length ?? 0, vc: C.green },
                            { label: "INPUT CHECKS", value: auditResult.entries?.filter((e) => e.inputMatch).length ?? 0, vc: C.green },
                            { label: "OUTPUT CHECKS", value: auditResult.entries?.filter((e) => e.outputMatch).length ?? 0, vc: C.green },
                        ].map(({ label, value, vc }) => (
                            <div key={label}>
                                <div style={{ fontSize: 8, letterSpacing: "0.1em", color: C.dim, marginBottom: 2 }}>{label}</div>
                                <div style={{ fontSize: 16, fontWeight: 700, color: vc }}>{value}</div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* ─────────────────────────────────────────────────────────────
                SECTION 3 — LIVE TAMPER DEMO
            ───────────────────────────────────────────────────────────── */}
            <div style={{ animation: "fadeIn 0.4s ease", marginTop: 32 }}>
                <div style={{ fontSize: 9, letterSpacing: "0.2em", color: C.dim, marginBottom: 12 }}>
                    SECTION 3 · LIVE TAMPER DEMO — READ-ONLY, NO GAS
                </div>
                <div className="card" style={{ padding: 24 }}>
                    <p style={{ fontSize: 12, color: C.text, marginBottom: 16, lineHeight: 1.7 }}>
                        Proves the substitution detector actually works: picks an agent, hashes its{" "}
                        <span style={{ color: C.green }}>real</span> config (should PASS), then hashes a deliberately{" "}
                        <span style={{ color: C.red }}>tampered</span> config simulating a silently swapped model
                        (should FAIL) — both checked against the same on-chain record. No transactions are sent.
                    </p>
                    <div style={{ display: "flex", gap: 8, marginBottom: tamperResult || tamperError ? 20 : 0, flexWrap: "wrap" }}>
                        {AGENT_IDS.map(id => {
                            const color = AGENT_COLORS[id]
                            const active = tamperAgent === id
                            return (
                                <button
                                    key={id}
                                    onClick={() => runTamperDemo(id)}
                                    disabled={tamperLoading}
                                    style={{
                                        background: active ? `${color}22` : "transparent",
                                        border: `1px solid ${active ? color : C.border2}`,
                                        color: active ? color : C.sub,
                                        fontSize: 10, letterSpacing: "0.1em", padding: "8px 16px",
                                        borderRadius: 6, fontFamily: "inherit", cursor: "pointer",
                                        opacity: tamperLoading && !active ? 0.5 : 1,
                                    }}
                                >
                                    {tamperLoading && active ? "SIMULATING…" : `SIMULATE TAMPER: ${AGENT_LABELS[id]}`}
                                </button>
                            )
                        })}
                    </div>

                    {tamperError && (
                        <div style={{
                            padding: "10px 14px", background: `${C.red}15`,
                            border: `1px solid ${C.red}33`, borderRadius: 6, fontSize: 11, color: C.red,
                        }}>
                            ✗ {tamperError}
                        </div>
                    )}

                    {tamperResult && (
                        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                            {(
                                [
                                    { key: "real", label: "REAL CONFIG", expect: "SHOULD PASS" },
                                    { key: "tampered", label: "TAMPERED CONFIG", expect: "SHOULD FAIL" },
                                ] as const
                            ).map(({ key, label, expect }) => {
                                const r = tamperResult[key]
                                const pass = r?.verified
                                return (
                                    <div key={key} style={{
                                        padding: 16, borderRadius: 6,
                                        border: `1px solid ${pass ? C.green : C.red}44`,
                                        background: pass ? `${C.green}0a` : `${C.red}0a`,
                                    }}>
                                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                                            <span style={{ fontSize: 10, letterSpacing: "0.1em", color: C.muted }}>{label}</span>
                                            <span style={{
                                                fontSize: 9, fontWeight: 700, letterSpacing: "0.08em",
                                                color: pass ? C.green : C.red,
                                                background: pass ? `${C.green}22` : `${C.red}22`,
                                                padding: "2px 8px", borderRadius: 3,
                                            }}>
                                                {pass ? "✓ VERIFIED" : "✗ MISMATCH"}
                                            </span>
                                        </div>
                                        <div style={{ fontSize: 9, color: C.dim, marginBottom: 4 }}>
                                            simulated model: <span style={{ color: C.text }}>{r?.simulatedModel}</span>
                                        </div>
                                        <div style={{ fontSize: 9, color: C.dim, fontFamily: "monospace", wordBreak: "break-all" }}>
                                            {r?.hash?.slice(0, 18)}…{r?.hash?.slice(-8)}
                                        </div>
                                        <div style={{ fontSize: 8, color: C.muted, marginTop: 8, letterSpacing: "0.08em" }}>
                                            {expect}
                                        </div>
                                    </div>
                                )
                            })}
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}