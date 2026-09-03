"use client"

/**
 * Hero copy, CTAs and the four feature controls (spec §6.8, §9).
 *
 * "Do not show the main hero headline, CTA buttons or four feature cards at
 * the beginning. Reveal them only after monolith rise, core ignition, energy
 * wave, rock levitation, chain binding and logo-to-navbar transition are
 * complete."
 *
 * The gate is a single `revealed` boolean flipped when intro progress crosses
 * the uiReveal beat. Everything below it is genuinely absent from the DOM
 * until then, not hidden with opacity — otherwise the hero's links stay in the
 * tab order and a screen reader announces a headline the visitor cannot see
 * during a cinematic that hasn't happened yet.
 */

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { HERO, FEATURE_CONTROLS } from "../config/content"
import { BEATS } from "../config/motion"
import { frame, useUiSelector } from "../state/landingStore"
import { scrollToSection } from "../hooks/useSectionNavigation"
import { FEATURE_ICONS, IconArrow } from "./Icons"

function FeatureControl({
  id,
  label,
  caption,
  onActivate,
}: {
  id: string
  label: string
  caption: string
  onActivate: () => void
}) {
  const Icon = FEATURE_ICONS[id as keyof typeof FEATURE_ICONS]
  const ref = useRef<HTMLButtonElement>(null)

  return (
    <button
      ref={ref}
      type="button"
      className="tc-feature"
      onClick={onActivate}
      onPointerMove={(e) => {
        // Cursor-following highlight (spec §9). Written as CSS custom
        // properties so the gradient moves without a React render.
        const el = ref.current
        if (!el) return
        const r = el.getBoundingClientRect()
        el.style.setProperty("--mx", `${e.clientX - r.left}px`)
        el.style.setProperty("--my", `${e.clientY - r.top}px`)
      }}
    >
      <Icon className="tc-feature__icon" />
      <span>
        <span className="tc-feature__label">{label}</span>
        <span className="tc-feature__caption">{caption}</span>
      </span>
    </button>
  )
}

export function HeroUI() {
  const awakened = useUiSelector((s) => s.introAwakened)
  const reduced = useUiSelector((s) => s.reducedMotion)
  const [revealed, setRevealed] = useState(false)

  useEffect(() => {
    if (revealed) return
    let raf = 0
    const tick = () => {
      // Reduced motion starts at (or very near) the awakened state, so the
      // hero UI is simply already there — spec §5/§18. Still resolved through
      // the same rAF as every other path rather than set synchronously in the
      // effect body, which would commit a second render before paint.
      if (reduced) {
        setRevealed(true)
        return
      }
      // In cinematic mode this crosses at ~97%; in Quick View the same
      // threshold is crossed during the 300-500ms resolve, so the identical
      // reveal choreography plays in both modes (spec §6.8).
      if (frame.introProgress >= BEATS.uiReveal[0] || awakened) {
        setRevealed(true)
        return
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [revealed, awakened, reduced])

  if (!revealed) return null

  return (
    <>
      <div className="tc-hero__ui">
        <h1 className="tc-hero__title tc-rise" data-shown="true">
          <span>{HERO.titleTop}</span>
          <span>
            <span className="tc-hero__accent">{HERO.titleAccent}</span> {HERO.titleRest}
          </span>
        </h1>

        <p className="tc-hero__lines tc-rise tc-rise--d1" data-shown="true">
          {HERO.lines.map((line) => (
            <span key={line}>{line}</span>
          ))}
        </p>

        <div className="tc-hero__ctas tc-rise tc-rise--cta tc-rise--d2" data-shown="true">
          <Link href={HERO.primaryCta.href} className="tc-btn tc-btn--primary">
            {HERO.primaryCta.label}
            <IconArrow />
          </Link>
          <Link href={HERO.secondaryCta.href} className="tc-btn tc-btn--ghost">
            {HERO.secondaryCta.label}
          </Link>
        </div>
      </div>

      {/* Spec §9: "These are not passive badges. Clicking one navigates to its
          corresponding landing-page section." */}
      <div className="tc-features tc-rise tc-rise--d3" data-shown="true">
        {FEATURE_CONTROLS.map((f) => (
          <FeatureControl
            key={f.id}
            id={f.id}
            label={f.label}
            caption={f.caption}
            onActivate={() => scrollToSection(f.target)}
          />
        ))}
      </div>
    </>
  )
}
