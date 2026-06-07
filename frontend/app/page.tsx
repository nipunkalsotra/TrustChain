"use client"

import Link from "next/link"
import { C, AGENT_COLORS, MOCK_RUNS, fmtDate } from "@/lib/constants"
import { Dot } from "@/components/ui/TrustChainUI"

export default function LandingPage() {
  return (
    <div className="page-enter" style={{ minHeight: "calc(100vh - 70px)", position: "relative", overflow: "hidden" }}>

      {/* ── Hero ── */}
      <div style={{ maxWidth: 900, margin: "0 auto", padding: "80px 32px 60px", textAlign: "center" }}>
        <div style={{ marginBottom: 24 }}>
          <span style={{ fontFamily: "'Share Tech Mono',monospace", fontSize: 11, letterSpacing: "0.3em", color: C.green, opacity: 0.7, display: "block", marginBottom: 16 }}>
            ◈ ◈ ◈
          </span>
          <h1 style={{
            fontFamily: "'Share Tech Mono',monospace", fontSize: 64, fontWeight: 400,
            letterSpacing: "0.12em", color: C.green, lineHeight: 1,
            textShadow: `0 0 30px ${C.green}66, 0 0 60px ${C.green}33`,
            marginBottom: 8,
          }}>
            TRUSTCHAIN
          </h1>
          <div style={{ fontSize: 11, letterSpacing: "0.4em", color: C.sub, marginBottom: 4 }}>
            MULTI-AGENT INTELLIGENCE · VERIFIED ON-CHAIN
          </div>
          <div style={{ fontSize: 10, letterSpacing: "0.25em", color: C.dim }}>
            MONAD TESTNET · CHAIN 10143
          </div>
        </div>

        <p style={{ fontSize: 15, color: C.text, lineHeight: 1.8, maxWidth: 600, margin: "32px auto", letterSpacing: "0.03em" }}>
          Every AI agent step — hashed, signed, and recorded immutably on{" "}
          <span style={{ color: C.purple }}>Monad</span>. Trust isn&apos;t claimed.
          It&apos;s <span style={{ color: C.green }}>proven</span>.
        </p>

        {/* CTA */}
        <div style={{ display: "flex", gap: 12, justifyContent: "center", marginTop: 40 }}>
          <Link href="/dashboard" style={{
            background: `${C.green}15`, border: `1px solid ${C.green}`,
            color: C.green, fontSize: 12, letterSpacing: "0.12em", padding: "12px 32px",
            borderRadius: 6, fontWeight: 600, boxShadow: `0 0 20px ${C.green}22`,
            display: "inline-block",
          }}>
            ▶ LAUNCH DASHBOARD
          </Link>
          <Link href="/audit" style={{
            background: "transparent", border: `1px solid ${C.border2}`,
            color: C.sub, fontSize: 12, letterSpacing: "0.12em", padding: "12px 32px",
            borderRadius: 6, display: "inline-block",
          }}>
            VIEW AUDIT LOG
          </Link>
        </div>
      </div>

      {/* ── Feature Cards ── */}
      <div style={{ maxWidth: 1100, margin: "0 auto 80px", padding: "0 32px" }}>
        <div style={{ fontSize: 9, letterSpacing: "0.25em", color: C.dim, textAlign: "center", marginBottom: 32 }}>
          ── WHAT IS TRUSTCHAIN ──
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
          {[
            { icon: "◉", color: C.green, title: "IMMUTABLE AUDIT TRAIL", body: "Every agent action — web searches, validations, score updates — is hashed and logged to the AgentAuditLog contract. Zero tampering. Full replay." },
            { icon: "◎", color: C.purple, title: "ON-CHAIN TRUST SCORES", body: "TrustScoreRegistry tracks each agent's reliability per run. Scores update live on Monad — leaderboard-verifiable by anyone." },
            { icon: "◆", color: C.blue, title: "AGENT IDENTITY VERIFICATION", body: "AgentIdentityRegistry stores code hashes for every agent. POST /verify checks the running binary matches what was registered. No impersonation." },
          ].map(({ icon, color, title, body }) => (
            <div key={title} className="card" style={{ padding: 24 }}>
              <div style={{ fontSize: 24, color, marginBottom: 12, filter: `drop-shadow(0 0 8px ${color}66)` }}>{icon}</div>
              <div style={{ fontSize: 11, letterSpacing: "0.1em", color, marginBottom: 10, fontWeight: 600 }}>{title}</div>
              <p style={{ fontSize: 12, color: C.text, lineHeight: 1.7 }}>{body}</p>
            </div>
          ))}
        </div>
      </div>

      {/* ── Pipeline Architecture ── */}
      <div style={{ maxWidth: 1100, margin: "0 auto 80px", padding: "0 32px" }}>
        <div style={{ fontSize: 9, letterSpacing: "0.25em", color: C.dim, textAlign: "center", marginBottom: 32 }}>
          ── AGENT PIPELINE ARCHITECTURE ──
        </div>
        <div className="card" style={{ padding: 32 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            {[
              { id: "researcher", label: "RESEARCHER", desc: "Web search + synthesis" },
              { id: "validator", label: "VALIDATOR", desc: "Source + fact check" },
              { id: "scorer", label: "SCORER", desc: "Quality assessment" },
              { id: "reporter", label: "REPORTER", desc: "Final report gen" },
            ].map((agent, i) => {
              const color = AGENT_COLORS[agent.id]
              return (
                <div key={agent.id} style={{ display: "flex", alignItems: "center", flex: 1 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ background: `${color}18`, border: `1px solid ${color}55`, borderRadius: 8, padding: "14px 16px", textAlign: "center" }}>
                      <div style={{ fontSize: 10, letterSpacing: "0.1em", color, fontWeight: 600 }}>{agent.label}</div>
                      <div style={{ fontSize: 10, color: C.muted, marginTop: 4 }}>{agent.desc}</div>
                    </div>
                    <div style={{ textAlign: "center", marginTop: 8 }}>
                      <span style={{ fontSize: 9, color: C.dim }}>MONAD TX</span>
                    </div>
                  </div>
                  {i < 3 && <div style={{ padding: "0 8px", color: C.muted, fontSize: 16 }}>→</div>}
                </div>
              )
            })}
          </div>
          <div style={{ textAlign: "center", marginTop: 20, paddingTop: 16, borderTop: `1px solid ${C.border}` }}>
            <span style={{ fontSize: 10, color: C.dim, letterSpacing: "0.1em" }}>
              EVERY STEP → KECCAK256 HASH → MONAD TESTNET · 3 CONTRACTS · IMMUTABLE AUDIT
            </span>
          </div>
        </div>
      </div>

      {/* ── Stats ── */}
      <div style={{ maxWidth: 1100, margin: "0 auto 80px", padding: "0 32px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
          {[
            { label: "CONTRACTS DEPLOYED", value: "3", sub: "ON MONAD TESTNET" },
            { label: "TOTAL RUNS", value: "3", sub: "THIS SESSION" },
            { label: "STEPS LOGGED", value: "29", sub: "ON-CHAIN TXS" },
            { label: "AVG TRUST SCORE", value: "84", sub: "OUT OF 100" },
          ].map(({ label, value, sub }) => (
            <div key={label} className="card" style={{ padding: 20, textAlign: "center" }}>
              <div style={{ fontSize: 9, letterSpacing: "0.12em", color: C.muted, marginBottom: 8 }}>{label}</div>
              <div style={{ fontFamily: "'Share Tech Mono',monospace", fontSize: 36, color: C.green, textShadow: `0 0 20px ${C.green}44`, lineHeight: 1 }}>{value}</div>
              <div style={{ fontSize: 9, color: C.dim, marginTop: 6, letterSpacing: "0.08em" }}>{sub}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Recent Runs ── */}
      <div style={{ maxWidth: 1100, margin: "0 auto 80px", padding: "0 32px" }}>
        <div style={{ fontSize: 9, letterSpacing: "0.25em", color: C.dim, marginBottom: 16 }}>── RECENT RUNS ──</div>
        <div className="card" style={{ overflow: "hidden" }}>
          {MOCK_RUNS.map((run, i) => (
            <div key={run.runId} style={{
              display: "flex", alignItems: "center", gap: 16, padding: "14px 20px",
              borderBottom: i < MOCK_RUNS.length - 1 ? `1px solid ${C.border}` : "none",
              transition: "background 0.15s",
            }}
              onMouseEnter={e => (e.currentTarget.style.background = C.bg3)}
              onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
            >
              <Dot active={true} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 11, color: C.bright, marginBottom: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {run.task}
                </div>
                <div style={{ fontSize: 9, color: C.dim }}>{run.runId}</div>
              </div>
              <div style={{ fontSize: 10, color: C.muted }}>{run.steps} steps</div>
              <div style={{ fontSize: 12, color: C.green, fontWeight: 600, minWidth: 40, textAlign: "right" }}>
                {run.avgScore}<span style={{ fontSize: 9, color: C.muted }}>/100</span>
              </div>
              <div style={{ fontSize: 9, color: C.dim }}>{fmtDate(run.ts)}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}