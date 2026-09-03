"use client"

/**
 * Pricing Coming Soon — the one special placeholder (spec §14.2).
 *
 * "Pricing alone gets a small cinematic placeholder. Keep it short: roughly
 * 2-4 seconds, visually tied to TrustChain, then settle on Pricing / Coming
 * Soon. Do not build another huge hero; this is a deliberate polished
 * placeholder, not a second experience. Provide Return to TrustChain."
 *
 * Deliberately built with SVG + CSS rather than React Three Fiber. The spec
 * caps this at a few seconds and explicitly forbids a second experience, and
 * pulling three.js, drei and a WebGL context onto a route that exists to say
 * "not yet" would cost more to load than the animation lasts. The visual
 * language is still unmistakably TrustChain: the same shield mark, the same
 * cyan energy, chain links converging and locking, then the mark settling.
 *
 * Honors prefers-reduced-motion by starting in the settled state.
 */

import { useEffect, useState } from "react"
import Link from "next/link"
import { BRAND } from "../config/content"
import "../landing.css"
import "./pricing-cinematic.css"

const DURATION_MS = 2800

export function PricingComingSoon() {
  const [settled, setSettled] = useState(false)

  useEffect(() => {
    // Reduced motion skips straight to the settled state. Scheduled rather
    // than set synchronously in the effect body so both paths commit the same
    // way — the CSS in pricing-cinematic.css already neutralizes the
    // animations themselves under the same media query.
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    const id = window.setTimeout(() => setSettled(true), reduced ? 0 : DURATION_MS)
    return () => window.clearTimeout(id)
  }, [])

  return (
    <div className="tc-landing">
      <main className="tc-pricing" data-settled={settled}>
        <div className="tc-pricing__stage" aria-hidden>
          <svg viewBox="0 0 240 240" className="tc-pricing__svg">
            <defs>
              <linearGradient id="tc-p-g" x1="30" y1="20" x2="210" y2="220" gradientUnits="userSpaceOnUse">
                <stop stopColor="#8ad8ff" />
                <stop offset="0.5" stopColor="#3ec6ff" />
                <stop offset="1" stopColor="#1668ff" />
              </linearGradient>
              <radialGradient id="tc-p-glow">
                <stop stopColor="#3ec6ff" stopOpacity="0.55" />
                <stop offset="1" stopColor="#3ec6ff" stopOpacity="0" />
              </radialGradient>
            </defs>

            {/* Energy bloom behind the mark — ignites, then settles low. */}
            <circle className="tc-p-glow" cx="120" cy="120" r="96" fill="url(#tc-p-glow)" />

            {/* Four chain links converge and lock, echoing the hero's chains. */}
            {[0, 1, 2, 3].map((i) => (
              <g key={i} className={`tc-p-link tc-p-link--${i}`}>
                <ellipse cx="120" cy="120" rx="15" ry="8.5" fill="none" stroke="url(#tc-p-g)" strokeWidth="2.4" />
              </g>
            ))}

            {/* Expanding verification ring — the spherical wave, flattened. */}
            <circle className="tc-p-ring" cx="120" cy="120" r="52" fill="none" stroke="#3ec6ff" strokeWidth="1.4" />

            {/* The shield mark, drawn on. */}
            <path
              className="tc-p-shield"
              d="M120 34 34 68v69c0 48.8 35.3 87.1 86 101.3C170.7 224.1 206 185.8 206 137V68L120 34Z"
              fill="none"
              stroke="url(#tc-p-g)"
              strokeWidth="4"
              strokeLinejoin="round"
            />
            <path
              className="tc-p-diamond"
              d="M120 92 160 120 120 148 80 120 120 92Z"
              fill="none"
              stroke="url(#tc-p-g)"
              strokeWidth="3.4"
              strokeLinejoin="round"
            />
            <circle className="tc-p-core" cx="120" cy="120" r="9" fill="url(#tc-p-g)" />
          </svg>
        </div>

        <div className="tc-pricing__copy">
          <div className="tc-brand__sub tc-pricing__eyebrow">{BRAND.subtitle}</div>
          <h1 className="tc-soon__title">Pricing — coming soon</h1>
          <p className="tc-soon__sub">
            We&apos;re still working out how to price something whose whole point is
            that you don&apos;t have to take our word for it.
          </p>
          <Link href="/" className="tc-btn tc-btn--ghost">
            Return to TrustChain
          </Link>
        </div>
      </main>
    </div>
  )
}
