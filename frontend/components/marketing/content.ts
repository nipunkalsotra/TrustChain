/**
 * Copy for the plain marketing landing page ("/").
 *
 * These strings are reproduced from the parked cinematic landing page's own
 * approved copy (components/landing/config/content.ts §12) rather than
 * imported from it — this page intentionally has zero dependency on
 * components/landing/ so that directory can be deleted wholesale later
 * without touching the live site. The four pipeline stage names
 * (Researcher/Validator/Scorer/Reporter) are a content lock: they match the
 * real LangGraph node names and must not be renamed.
 */

export const BRAND = {
  wordmark: "TrustChain",
  subtitle: "VERIFIABLE AI",
} as const

export interface NavLink {
  id: string
  label: string
  /** "scroll" smooth-scrolls to #sectionId on this page; "route" is a normal Link. */
  kind: "scroll" | "route"
  sectionId?: string
  href?: string
}

export const NAV_LINKS: NavLink[] = [
  { id: "product", label: "Product", kind: "scroll", sectionId: "product" },
  { id: "security", label: "Security", kind: "scroll", sectionId: "security" },
  { id: "docs", label: "Docs", kind: "route", href: "/coming-soon?from=docs" },
  { id: "pricing", label: "Pricing", kind: "route", href: "/pricing" },
]

export const NAV_ACTIONS = {
  login: { label: "Log In", href: "/coming-soon?from=login" },
  getStarted: { label: "Get Started", href: "/coming-soon?from=get-started" },
} as const

export const HERO = {
  titleTop: "TrustChain AI.",
  titleAccent: "Audit with",
  titleRest: "Provenance.",
  lines: [
    "Ancient truth, encoded for modern AI.",
    "Tamper-proof provenance that meets global deployment needs.",
    "An immutable foundation for trust in AI systems.",
  ],
  primaryCta: { label: "Get Started", href: "/coming-soon?from=get-started" },
  secondaryCta: { label: "View Docs", href: "/coming-soon?from=docs" },
} as const

export interface SectionCopy {
  id: string
  eyebrow: string
  headline: string
  lead: string
  points: string[]
}

export const SECTIONS: Record<string, SectionCopy> = {
  product: {
    id: "product",
    eyebrow: "The Pipeline",
    headline: "Every AI decision, tracked from first thought to final answer.",
    lead:
      "TrustChain wraps your AI agents in a four-stage pipeline that records what they did, checks it, scores it, and reports it. Every step is written to Postgres and cryptographically anchored on-chain in the same instant it happens - so the record can't be quietly edited after the fact, even by TrustChain itself.",
    points: [],
  },
  immutable: {
    id: "immutable",
    eyebrow: "Immutable Logs",
    headline: "A record that can't be quietly changed.",
    lead:
      "TrustChain writes every agent step to Postgres and queues it for anchoring in the same database transaction - a crash between “recorded” and “queued” is impossible; either both happen or neither does. Nothing about the audit trail depends on trusting our servers to keep it honest.",
    points: [
      "Every step is durably recorded before an agent's run even finishes.",
      "The step and its anchor entry are written atomically - no window where one exists without the other.",
      "Tampering after the fact doesn't erase evidence - it creates a detectable mismatch against the anchored proof.",
    ],
  },
  merkle: {
    id: "merkle",
    eyebrow: "Merkle Anchored",
    headline: "Thousands of records, one provable root.",
    lead:
      "Instead of anchoring every step individually, TrustChain batches recent steps into a Merkle tree and anchors only the resulting root on-chain. Each step still gets its own cryptographic proof - a short path of hashes tying it back to that root.",
    points: [
      "Steps are batched on a timer, not per-action, keeping anchoring cost predictable.",
      "Each step's Merkle proof can be checked independently, without re-anchoring anything.",
      "A single altered step changes its hash, which changes the root - the tampering surfaces anywhere in the tree.",
    ],
  },
  blockchain: {
    id: "blockchain",
    eyebrow: "On-Chain Verified",
    headline: "Anchored to a chain, not to us.",
    lead:
      "The Merkle root is written to a smart contract on Monad. Once it's there, verifying a record doesn't require asking TrustChain to vouch for itself - it requires checking the record's proof against a root that's public, timestamped, and outside our control.",
    points: [
      "Anchoring runs as a separate background process, decoupled from the API that serves your data.",
      "Confirmed roots are reconciled back into the read model by an indexer that watches the chain directly.",
      "If TrustChain's own database ever disagreed with the chain, the chain is what's authoritative.",
    ],
  },
  verification: {
    id: "verification",
    eyebrow: "Publicly Verifiable",
    headline: "Anyone can check the proof. No account required.",
    lead:
      "Verification isn't a feature you have to buy access to. Given a step ID and its Merkle proof, any independent party - an auditor, a regulator, a customer - can recompute the hash path and confirm it resolves to the exact root anchored on-chain, using nothing but public chain data.",
    points: [
      "Verification logic is small enough to reimplement independently, without trusting TrustChain's own verifier.",
      "The same proof format is used internally and externally - nothing is held back for outside checks.",
      "This is what makes the audit trail evidence, not just a claim.",
    ],
  },
  realproduct: {
    id: "realproduct",
    eyebrow: "The Real Product",
    headline: "This is what it looks like in the dashboard.",
    lead:
      "The same proof that verifies mathematically is the one you read in the interface - run history, per-step anchoring status, and the Merkle path that ties each step back to its on-chain root.",
    points: [],
  },
  security: {
    id: "security",
    eyebrow: "Security",
    headline: "Built to survive a compromised database.",
    lead:
      "TrustChain's audit trail doesn't ask you to trust TrustChain. Every layer below is designed so tampering is detectable, tenants stay isolated, and a proof can be checked independently of our own infrastructure.",
    points: [],
  },
}

export interface PipelineStage {
  index: string
  /** LOCKED - matches the real LangGraph node name. */
  name: string
  body: string
}

export const PIPELINE_STAGES: PipelineStage[] = [
  {
    index: "01",
    name: "Researcher",
    body: "Gathers the inputs, context, and evidence behind every agent decision - the raw material an audit starts from.",
  },
  {
    index: "02",
    name: "Validator",
    body: "Checks that output against your rules and facts, flagging anything that doesn't hold up before it moves forward.",
  },
  {
    index: "03",
    name: "Scorer",
    body: "Assigns a quantitative trust score to the run, based on accuracy, consistency, and adherence to process.",
  },
  {
    index: "04",
    name: "Reporter",
    body: "Compiles the full record into a verifiable report - hashed, timestamped, and queued for on-chain anchoring.",
  },
]

export interface SecurityLayer {
  id: string
  title: string
  body: string
}

export const SECURITY_LAYERS: SecurityLayer[] = [
  {
    id: "tamper",
    title: "Tamper Detection",
    body: "Every step is hashed and included in a Merkle tree anchored on-chain. An altered record produces a mismatch that is mathematically provable - not just logged.",
  },
  {
    id: "atomic",
    title: "Atomic Durability",
    body: "Each audit step and its anchor entry are written in the same database transaction. A crash mid-write can never leave a step silently unrecorded.",
  },
  {
    id: "tenant",
    title: "Tenant Isolation",
    body: "Every tenant's data is enforced at two independent layers - application-level filtering and Postgres Row-Level Security - so one missed check can't leak across organizations.",
  },
  {
    id: "resilient",
    title: "Resilient Infrastructure",
    body: "Pluggable signing backends (local key, AWS/GCP KMS, HashiCorp Vault) and automatic RPC failover keep anchoring running even when a single provider or key store goes down.",
  },
]

export const PRODUCT_PANEL = {
  title: "Run · trust-eval",
  subtitle: "4 steps · anchored",
  rows: [
    { step: "researcher", status: "anchored", detail: "batch #1482" },
    { step: "validator", status: "anchored", detail: "batch #1482" },
    { step: "scorer", status: "anchored", detail: "batch #1482" },
    { step: "reporter", status: "anchored", detail: "batch #1483" },
  ],
  proof: {
    label: "Merkle proof",
    root: "0x7f3a…c1d9",
    depth: "depth 4",
  },
} as const

export const FINAL_CTA = {
  headline: "Stop asking people to trust your logs.",
  lead: "Give them a proof they can check themselves.",
  primary: { label: "Get Started", href: "/coming-soon?from=get-started" },
  secondary: { label: "Read the Docs", href: "/coming-soon?from=docs" },
  footnote: "Anchored on Monad · Open verification format",
} as const
