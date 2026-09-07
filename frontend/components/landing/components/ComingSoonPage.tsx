"use client"

/**
 * Spec §14.1 — the plain Coming Soon placeholder.
 *
 * The `?from=` parameter only changes the label, so Docs / Log In / Get
 * Started each land somewhere that acknowledges what was clicked instead of
 * showing one anonymous page. It is a label lookup, not routing logic — these
 * pages are explicitly temporary.
 */

import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { BrandMark } from "./Icons"
import { BRAND } from "../config/content"
import "../landing.css"

const LABELS: Record<string, string> = {
  docs: "Documentation",
  login: "Sign in",
  "get-started": "Get started",
}

/**
 * The page itself, with no dependency on search params.
 *
 * Kept separate so it can also serve as the Suspense fallback — using the
 * params-reading component as its own fallback is a real prerender failure,
 * not a style issue: Next.js renders the fallback during static export, hits
 * useSearchParams there too, and the build aborts.
 */
export function ComingSoonShell({ label }: { label?: string }) {
  return (
    <div className="tc-landing">
      <main className="tc-soon">
        <BrandMark size={54} />
        <div>
          <div className="tc-brand__name" style={{ fontSize: 19 }}>
            {BRAND.wordmark}
          </div>
          <div className="tc-brand__sub" style={{ textAlign: "center" }}>
            {BRAND.subtitle}
          </div>
        </div>

        <h1 className="tc-soon__title">{label ? `${label} — coming soon` : "Coming soon"}</h1>
        <p className="tc-soon__sub">
          This part of TrustChain isn&apos;t published yet. The verification layer it
          documents is already running.
        </p>

        <Link href="/" className="tc-btn tc-btn--ghost">
          Return to TrustChain
        </Link>
      </main>
    </div>
  )
}

export function ComingSoon() {
  const params = useSearchParams()
  const from = params?.get("from") ?? ""
  return <ComingSoonShell label={LABELS[from]} />
}
