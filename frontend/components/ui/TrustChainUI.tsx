"use client"

import { C, AGENT_COLORS, AGENT_LABELS, shortHash, txLink } from "@/lib/constants"

// ─── Status Dot ───────────────────────────────────────────────────────────────
export function Dot({ active, color }: { active: boolean; color?: string }) {
    const c = color || (active ? C.green : C.red)
    return (
        <span style={{
            display: "inline-block", width: 7, height: 7, borderRadius: "50%",
            background: c, boxShadow: `0 0 6px ${c}`, marginRight: 6, flexShrink: 0,
            animation: active ? "glow-pulse 2s ease-in-out infinite" : "none",
        }} />
    )
}

// ─── TX Hash Link ─────────────────────────────────────────────────────────────
export function TxLink({ hash }: { hash?: string }) {
    if (!hash) return (
        <span style={{ fontSize: 10, color: C.dim, animation: "pulse 1.2s ease-in-out infinite" }}>
            ⏳ confirming…
        </span>
    )
    return (
        <a href={txLink(hash)} target="_blank" rel="noopener noreferrer" style={{
            display: "inline-flex", alignItems: "center", gap: 4,
            fontSize: 10, color: C.green, opacity: 0.65, border: `1px solid ${C.green}33`,
            padding: "2px 8px", borderRadius: 3, transition: "opacity 0.15s",
        }}
            onMouseEnter={e => (e.currentTarget.style.opacity = "1")}
            onMouseLeave={e => (e.currentTarget.style.opacity = "0.65")}
        >
            ⛓ {shortHash(hash)} ↗
        </a>
    )
}

// ─── Gauge Circle ─────────────────────────────────────────────────────────────
export function GaugeCircle({
    score, agentId, size = 92, fontSize = 18,
}: {
    score: number; agentId: string; size?: number; fontSize?: number
}) {
    const color = AGENT_COLORS[agentId] ?? C.green
    const r = size / 2 - 8
    const circ = 2 * Math.PI * r
    const filled = (score / 100) * circ
    return (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
            <div style={{ position: "relative", width: size, height: size }}>
                <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
                    <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#1a2a2a" strokeWidth={7} />
                    <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={7}
                        strokeDasharray={`${filled} ${circ}`} strokeLinecap="round"
                        style={{ transition: "stroke-dasharray 0.8s ease", filter: `drop-shadow(0 0 4px ${color})` }}
                    />
                </svg>
                <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
                    <span style={{ fontFamily: "'Share Tech Mono',monospace", fontSize, fontWeight: 700, color, lineHeight: 1 }}>
                        {score}
                    </span>
                    <span style={{ fontSize: 9, color: C.muted, marginTop: 1 }}>/100</span>
                </div>
            </div>
            <span style={{ fontSize: 10, letterSpacing: "0.1em", color, opacity: 0.85 }}>
                {AGENT_LABELS[agentId] ?? agentId?.toUpperCase()}
            </span>
        </div>
    )
}

// ─── Score Sparkline Chart ────────────────────────────────────────────────────
export function ScoreChart({ history, agentId }: { history: Record<string, number>[]; agentId: string }) {
    const color = AGENT_COLORS[agentId] ?? C.green
    const W = 300, H = 80
    const data = history.map(h => h[agentId] as number)
    const pts = data.map((v, i) => `${(i / (data.length - 1)) * W},${H - (v / 100) * H}`)
    const path = `M ${pts.join(" L ")}`
    const area = `M 0,${H} L ${pts.join(" L ")} L ${W},${H} Z`
    return (
        <svg width="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ display: "block" }}>
            <defs>
                <linearGradient id={`g-${agentId}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={color} stopOpacity="0.25" />
                    <stop offset="100%" stopColor={color} stopOpacity="0" />
                </linearGradient>
            </defs>
            <path d={area} fill={`url(#g-${agentId})`} />
            <path d={path} fill="none" stroke={color} strokeWidth="1.5"
                style={{ filter: `drop-shadow(0 0 3px ${color})` }} />
            {data.map((v, i) => (
                <circle key={i}
                    cx={(i / (data.length - 1)) * W}
                    cy={H - (v / 100) * H}
                    r={2.5} fill={color}
                    style={{ filter: `drop-shadow(0 0 2px ${color})` }}
                />
            ))}
        </svg>
    )
}

// ─── Ticker ───────────────────────────────────────────────────────────────────
export function Ticker() {
    const items = [
        "◈ TRUSTCHAIN ONLINE",
        "MONAD TESTNET · CHAIN 10143",
        "3 CONTRACTS DEPLOYED",
        "BLOCK #36,579,540",
        "AGENTAUDITLOG · TRUSTSCOREREGISTRY · AGENTIDENTITYREGISTRY",
        "PIPELINE STATUS: READY",
        "ALL AGENTS VERIFIED",
    ]
    return (
        <div style={{ overflow: "hidden", background: C.bg0, borderBottom: `1px solid ${C.border}`, height: 22, position: "relative" }}>
            <div style={{ display: "flex", gap: 60, whiteSpace: "nowrap", animation: "ticker 30s linear infinite", position: "absolute", top: 3 }}>
                {[...items, ...items].map((t, i) => (
                    <span key={i} style={{ fontSize: 9, letterSpacing: "0.12em", color: C.muted }}>
                        {i % 2 === 0
                            ? <span style={{ color: C.green }}>◈ </span>
                            : <span style={{ color: C.dim }}>· </span>
                        }
                        {t}
                    </span>
                ))}
            </div>
        </div>
    )
}