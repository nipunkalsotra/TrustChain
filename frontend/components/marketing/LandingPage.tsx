import Link from "next/link"
import { NavCapsule } from "./NavCapsule"
import {
  BRAND,
  NAV_LINKS,
  NAV_ACTIONS,
  HERO,
  SECTIONS,
  PIPELINE_STAGES,
  SECURITY_LAYERS,
  PRODUCT_PANEL,
  FINAL_CTA,
} from "./content"
import "./marketing.css"

const MAX_W = 1100

// Shared visual treatment for the two CTA "weights" used across Nav/Hero/
// FinalCta — kept here once so the three instances can't drift apart.
const PRIMARY_BTN_STYLE = {
  background: "linear-gradient(135deg, var(--mkt-cyan-bright), var(--mkt-blue))",
  color: "var(--mkt-bg)",
  border: "1px solid var(--mkt-border-subtle)",
  fontWeight: 700,
  borderRadius: 8,
  letterSpacing: "0.02em",
} as const

const SECONDARY_BTN_STYLE = {
  border: "1px solid var(--mkt-border-subtle)",
  color: "var(--mkt-text-secondary)",
  borderRadius: 8,
  letterSpacing: "0.02em",
} as const

function Nav() {
  return (
    <nav
      style={{
        position: "sticky",
        top: 0,
        zIndex: 50,
        background: "rgba(2, 7, 17, 0.93)",
        backdropFilter: "blur(6px)",
        borderBottom: "1px solid var(--mkt-divider)",
      }}
    >
      <div
        className="mkt-nav-row"
        style={{
          maxWidth: MAX_W,
          margin: "0 auto",
          padding: "0 24px",
          height: 60,
          display: "flex",
          alignItems: "center",
          gap: 32,
        }}
      >
        <Link href="/" style={{ display: "flex", alignItems: "baseline", gap: 8, flexShrink: 0 }}>
          <span
            style={{
              fontFamily: "'Share Tech Mono',monospace",
              fontSize: 16,
              fontWeight: 700,
              letterSpacing: "0.1em",
            }}
          >
            <span style={{ color: "var(--mkt-cyan-bright)", textShadow: "0 0 16px var(--mkt-glow-cyan)" }}>◈ </span>
            <span style={{ color: "var(--mkt-text)" }}>TRUST</span>
            <span style={{ color: "var(--mkt-cyan-bright)" }}>CHAIN</span>
          </span>
          <span style={{ fontSize: 9, letterSpacing: "0.2em", color: "var(--mkt-cyan-dim)" }}>{BRAND.subtitle}</span>
        </Link>

        <div className="mkt-nav-capsule-slot" style={{ flex: 1, minWidth: 0, display: "flex", justifyContent: "center" }}>
          <NavCapsule />
        </div>

        <Link
          href={NAV_ACTIONS.login.href}
          className="mkt-nav-link"
          style={{ fontSize: 12, flexShrink: 0 }}
        >
          {NAV_ACTIONS.login.label}
        </Link>
        <Link
          href={NAV_ACTIONS.getStarted.href}
          className="mkt-btn-primary"
          style={{
            ...PRIMARY_BTN_STYLE,
            fontSize: 12,
            padding: "8px 16px",
            borderRadius: 6,
            flexShrink: 0,
          }}
        >
          {NAV_ACTIONS.getStarted.label}
        </Link>
      </div>
    </nav>
  )
}

function Hero() {
  return (
    <header style={{ padding: "88px 24px 96px", textAlign: "center" }}>
      <div style={{ maxWidth: 760, margin: "0 auto" }}>
        <div
          style={{
            display: "inline-block",
            fontSize: 10,
            letterSpacing: "0.25em",
            color: "var(--mkt-cyan)",
            border: "1px solid var(--mkt-border-subtle)",
            background: "rgba(7, 17, 31, 0.6)",
            borderRadius: 20,
            padding: "5px 14px",
            marginBottom: 28,
          }}
        >
          ◈ MONAD TESTNET · MERKLE-ANCHORED AUDIT TRAILS
        </div>

        <h1
          className="mkt-hero-title"
          style={{
            fontSize: "clamp(2.6rem, 6vw, 4.2rem)",
            lineHeight: 1.08,
            fontWeight: 700,
            color: "var(--mkt-text)",
            marginBottom: 24,
          }}
        >
          {HERO.titleTop}
          <br />
          <span style={{ color: "var(--mkt-text-secondary)" }}>{HERO.titleAccent} </span>
          <span
            style={{
              backgroundImage: "linear-gradient(135deg, var(--mkt-cyan-bright), var(--mkt-blue))",
              WebkitBackgroundClip: "text",
              backgroundClip: "text",
              color: "transparent",
              WebkitTextFillColor: "transparent",
              textShadow: "0 0 20px rgba(34, 211, 238, 0.12)",
            }}
          >
            {HERO.titleRest}
          </span>
        </h1>

        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 40 }}>
          {HERO.lines.map((line) => (
            <p key={line} style={{ fontSize: 14, color: "var(--mkt-text-secondary)", letterSpacing: "0.01em" }}>
              {line}
            </p>
          ))}
        </div>

        <div style={{ display: "flex", justifyContent: "center", gap: 14, flexWrap: "wrap" }}>
          <Link
            href={HERO.primaryCta.href}
            className="mkt-btn-primary"
            style={{ ...PRIMARY_BTN_STYLE, fontSize: 13, padding: "13px 26px" }}
          >
            {HERO.primaryCta.label}
          </Link>
          <Link
            href={HERO.secondaryCta.href}
            className="mkt-btn-secondary"
            style={{ ...SECONDARY_BTN_STYLE, fontSize: 13, padding: "13px 26px" }}
          >
            {HERO.secondaryCta.label}
          </Link>
        </div>
      </div>
    </header>
  )
}

function FeatureSection({
  section,
  bg,
}: {
  section: (typeof SECTIONS)[keyof typeof SECTIONS]
  bg: string
}) {
  return (
    <section id={section.id} style={{ background: bg, borderTop: "1px solid var(--mkt-divider)", padding: "72px 24px" }}>
      <div style={{ maxWidth: MAX_W, margin: "0 auto" }}>
        <div style={{ fontSize: 11, letterSpacing: "0.2em", color: "var(--mkt-cyan)", marginBottom: 14 }}>
          {section.eyebrow.toUpperCase()}
        </div>
        <h2 style={{ fontSize: "clamp(1.5rem, 3vw, 2.1rem)", color: "var(--mkt-text)", marginBottom: 18, maxWidth: 640 }}>
          {section.headline}
        </h2>
        <p
          style={{
            fontSize: 14,
            lineHeight: 1.7,
            color: "var(--mkt-text-secondary)",
            maxWidth: 680,
            marginBottom: section.points.length ? 28 : 0,
          }}
        >
          {section.lead}
        </p>
        {section.points.length > 0 && (
          <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: 12, maxWidth: 680 }}>
            {section.points.map((p) => (
              <li key={p} style={{ display: "flex", gap: 10, fontSize: 13, color: "var(--mkt-text-secondary)", lineHeight: 1.6 }}>
                <span style={{ color: "var(--mkt-cyan)", flexShrink: 0 }}>▸</span>
                {p}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

function ProductPipeline() {
  const section = SECTIONS.product
  return (
    <section id="product" style={{ background: "var(--mkt-bg2)", borderTop: "1px solid var(--mkt-divider)", padding: "72px 24px" }}>
      <div style={{ maxWidth: MAX_W, margin: "0 auto" }}>
        <div style={{ fontSize: 11, letterSpacing: "0.2em", color: "var(--mkt-cyan)", marginBottom: 14 }}>
          {section.eyebrow.toUpperCase()}
        </div>
        <h2 style={{ fontSize: "clamp(1.5rem, 3vw, 2.1rem)", color: "var(--mkt-text)", marginBottom: 18, maxWidth: 640 }}>
          {section.headline}
        </h2>
        <p style={{ fontSize: 14, lineHeight: 1.7, color: "var(--mkt-text-secondary)", maxWidth: 680, marginBottom: 40 }}>
          {section.lead}
        </p>

        <div className="mkt-pipeline-grid">
          {PIPELINE_STAGES.map((stage) => (
            <div key={stage.name} className="mkt-card" style={{ padding: 22 }}>
              <div style={{ fontSize: 11, color: "var(--mkt-text-muted)", letterSpacing: "0.15em", marginBottom: 10 }}>
                {stage.index}
              </div>
              <div
                style={{
                  fontSize: 14,
                  fontFamily: "'Share Tech Mono',monospace",
                  color: "var(--mkt-cyan)",
                  letterSpacing: "0.08em",
                  marginBottom: 10,
                }}
              >
                {stage.name.toUpperCase()}
              </div>
              <p style={{ fontSize: 12.5, lineHeight: 1.6, color: "var(--mkt-text-secondary)" }}>{stage.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

function RealProduct() {
  const section = SECTIONS.realproduct
  return (
    <section
      id="realproduct"
      style={{ background: "var(--mkt-bg)", borderTop: "1px solid var(--mkt-divider)", padding: "72px 24px" }}
    >
      <div
        style={{
          maxWidth: MAX_W,
          margin: "0 auto",
          display: "flex",
          gap: 48,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        <div style={{ flex: "1 1 360px" }}>
          <div style={{ fontSize: 11, letterSpacing: "0.2em", color: "var(--mkt-cyan)", marginBottom: 14 }}>
            {section.eyebrow.toUpperCase()}
          </div>
          <h2 style={{ fontSize: "clamp(1.5rem, 3vw, 2.1rem)", color: "var(--mkt-text)", marginBottom: 18 }}>
            {section.headline}
          </h2>
          <p style={{ fontSize: 14, lineHeight: 1.7, color: "var(--mkt-text-secondary)" }}>{section.lead}</p>
        </div>

        <div className="mkt-card" style={{ flex: "1 1 340px", padding: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
            <span style={{ fontSize: 13, color: "var(--mkt-text)", fontFamily: "'Share Tech Mono',monospace" }}>
              {PRODUCT_PANEL.title}
            </span>
            <span style={{ fontSize: 10, color: "var(--mkt-text-muted)", letterSpacing: "0.08em" }}>
              {PRODUCT_PANEL.subtitle}
            </span>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 16 }}>
            {PRODUCT_PANEL.rows.map((row) => (
              <div
                key={row.step}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  fontSize: 11.5,
                  padding: "8px 10px",
                  background: "var(--mkt-surface-elevated)",
                  borderRadius: 5,
                }}
              >
                <span style={{ color: "var(--mkt-text-secondary)", letterSpacing: "0.05em" }}>{row.step}</span>
                <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ color: "var(--mkt-text-muted)" }}>{row.detail}</span>
                  <span
                    style={{
                      color: "var(--mkt-cyan)",
                      fontSize: 9,
                      letterSpacing: "0.1em",
                      border: "1px solid var(--mkt-border-subtle)",
                      borderRadius: 3,
                      padding: "2px 6px",
                    }}
                  >
                    {row.status.toUpperCase()}
                  </span>
                </span>
              </div>
            ))}
          </div>

          <div style={{ borderTop: "1px solid var(--mkt-divider)", paddingTop: 14, fontSize: 11 }}>
            <div style={{ color: "var(--mkt-text-muted)", letterSpacing: "0.1em", marginBottom: 4 }}>
              {PRODUCT_PANEL.proof.label.toUpperCase()}
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", color: "var(--mkt-text-secondary)" }}>
              <span style={{ fontFamily: "'Share Tech Mono',monospace" }}>{PRODUCT_PANEL.proof.root}</span>
              <span>{PRODUCT_PANEL.proof.depth}</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

function Security() {
  const section = SECTIONS.security
  return (
    <section id="security" style={{ background: "var(--mkt-bg2)", borderTop: "1px solid var(--mkt-divider)", padding: "72px 24px" }}>
      <div style={{ maxWidth: MAX_W, margin: "0 auto" }}>
        <div style={{ fontSize: 11, letterSpacing: "0.2em", color: "var(--mkt-cyan)", marginBottom: 14 }}>
          {section.eyebrow.toUpperCase()}
        </div>
        <h2 style={{ fontSize: "clamp(1.5rem, 3vw, 2.1rem)", color: "var(--mkt-text)", marginBottom: 18, maxWidth: 640 }}>
          {section.headline}
        </h2>
        <p style={{ fontSize: 14, lineHeight: 1.7, color: "var(--mkt-text-secondary)", maxWidth: 680, marginBottom: 40 }}>
          {section.lead}
        </p>

        <div className="mkt-security-grid">
          {SECURITY_LAYERS.map((layer) => (
            <div key={layer.id} className="mkt-card" style={{ padding: 22 }}>
              <div style={{ fontSize: 13, color: "var(--mkt-text)", fontWeight: 600, marginBottom: 10 }}>{layer.title}</div>
              <p style={{ fontSize: 12.5, lineHeight: 1.6, color: "var(--mkt-text-secondary)" }}>{layer.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

function FinalCta() {
  return (
    <section style={{ background: "var(--mkt-bg)", borderTop: "1px solid var(--mkt-divider)", padding: "96px 24px", textAlign: "center" }}>
      <div style={{ maxWidth: 620, margin: "0 auto" }}>
        <h2 style={{ fontSize: "clamp(1.7rem, 4vw, 2.4rem)", color: "var(--mkt-text)", marginBottom: 14 }}>
          {FINAL_CTA.headline}
        </h2>
        <p style={{ fontSize: 15, color: "var(--mkt-text-secondary)", marginBottom: 36 }}>{FINAL_CTA.lead}</p>

        <div style={{ display: "flex", justifyContent: "center", gap: 14, flexWrap: "wrap", marginBottom: 24 }}>
          <Link
            href={FINAL_CTA.primary.href}
            className="mkt-btn-primary"
            style={{ ...PRIMARY_BTN_STYLE, fontSize: 13, padding: "13px 26px" }}
          >
            {FINAL_CTA.primary.label}
          </Link>
          <Link
            href={FINAL_CTA.secondary.href}
            className="mkt-btn-secondary"
            style={{ ...SECONDARY_BTN_STYLE, fontSize: 13, padding: "13px 26px" }}
          >
            {FINAL_CTA.secondary.label}
          </Link>
        </div>

        <div style={{ fontSize: 10, letterSpacing: "0.1em", color: "var(--mkt-text-muted)" }}>{FINAL_CTA.footnote}</div>
      </div>
    </section>
  )
}

function Footer() {
  return (
    <footer style={{ borderTop: "1px solid var(--mkt-divider)", padding: "28px 24px" }}>
      <div
        style={{
          maxWidth: MAX_W,
          margin: "0 auto",
          display: "flex",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 12,
          fontSize: 11,
          color: "var(--mkt-text-muted)",
        }}
      >
        <span>◈ {BRAND.wordmark.toUpperCase()} — {new Date().getFullYear()}</span>
        <div style={{ display: "flex", gap: 20 }}>
          {NAV_LINKS.map((n) => (
            <Link
              key={n.id}
              href={n.kind === "route" ? n.href! : `#${n.sectionId}`}
              className="mkt-nav-link"
              style={{ fontSize: 11 }}
            >
              {n.label}
            </Link>
          ))}
        </div>
      </div>
    </footer>
  )
}

const FEATURE_ORDER = ["immutable", "merkle", "blockchain", "verification"] as const
const FEATURE_BG = ["var(--mkt-bg)", "var(--mkt-bg2)", "var(--mkt-bg)", "var(--mkt-bg2)"]

export function LandingPage() {
  return (
    <div className="mkt-page" style={{ background: "var(--mkt-bg)", minHeight: "100dvh" }}>
      <Nav />
      <Hero />
      <ProductPipeline />
      {FEATURE_ORDER.map((id, i) => (
        <FeatureSection key={id} section={SECTIONS[id]} bg={FEATURE_BG[i]} />
      ))}
      <RealProduct />
      <Security />
      <FinalCta />
      <Footer />
    </div>
  )
}
