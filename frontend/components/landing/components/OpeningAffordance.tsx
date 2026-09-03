"use client"

/**
 * The opening affordance (spec §5).
 *
 * "Do not add a visible Skip button. The hint must be small, low-opacity and
 * secondary to the scene. SCROLL TO AWAKEN fades after the visitor begins
 * scrolling. ENTER / ESC FOR QUICK VIEW remains faintly visible until the
 * opening resolves or the user activates Quick View."
 *
 * Both hints are driven straight from `frame.introProgress` on rAF and written
 * to the DOM through a ref — putting scroll progress into React state to fade
 * a caption would re-render the page on every wheel tick.
 */

import { useEffect, useRef } from "react"
import { OPENING_AFFORDANCE } from "../config/content"
import { frame, useUiSelector } from "../state/landingStore"
import { clamp01 } from "../config/motion"

export function OpeningAffordance() {
  const root = useRef<HTMLDivElement>(null)
  const scrollHint = useRef<HTMLDivElement>(null)
  const awakened = useUiSelector((s) => s.introAwakened)
  const reduced = useUiSelector((s) => s.reducedMotion)

  useEffect(() => {
    if (awakened || reduced) return
    let raf = 0
    const tick = () => {
      const t = frame.introProgress
      if (scrollHint.current) {
        // Fades out as soon as scrolling begins — it has done its job.
        scrollHint.current.style.opacity = String(1 - clamp01(t / 0.06))
      }
      if (root.current) {
        // The Quick View hint persists much longer, then fades near the end.
        root.current.style.opacity = String(1 - clamp01((t - 0.9) / 0.1))
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [awakened, reduced])

  // Once resolved it is gone entirely — not just transparent — so it can never
  // sit invisibly over the hero CTAs.
  if (awakened || reduced) return null

  return (
    <div className="tc-affordance" ref={root} aria-hidden>
      <div className="tc-affordance__scroll" ref={scrollHint}>
        {OPENING_AFFORDANCE.scrollHint}
      </div>
      <div className="tc-affordance__arrow" />
      <div className="tc-affordance__quick">
        <kbd>Enter</kbd> / <kbd>Esc</kbd> for quick view
      </div>
    </div>
  )
}
