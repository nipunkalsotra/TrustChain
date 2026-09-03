import type { Metadata } from "next"
import { LandingPage } from "@/components/marketing/LandingPage"

/**
 * The landing route: a plain, static marketing page (no WebGL/GSAP/Lenis).
 * The prior cinematic version lives on, unused, under components/landing/
 * in case it's wanted again later. Renders with the app's existing global
 * Geist Mono font (layout.tsx) rather than loading a second font just for
 * this route.
 */

export const metadata: Metadata = {
  title: "TrustChain — Audit with Provenance",
  description:
    "TrustChain records every AI agent step and anchors it on-chain in Merkle batches, so anyone can verify the audit trail without trusting our database.",
}

export default function Page() {
  return <LandingPage />
}
