"use client"

/**
 * Shared shell for every non-hero section.
 *
 * Spec §11: "The section data/copy source is shared between modes. Only
 * animation, layout density, camera behavior and 3D intensity vary by mode."
 * So there is exactly one component per section, taking `experienceMode` as a
 * prop, rather than a cinematic copy and a normal copy that can drift.
 *
 * The reveal is an IntersectionObserver flipping a data attribute that CSS
 * transitions read — a class toggle, not a JS animation, so it costs nothing
 * per frame and is automatically neutralized by the prefers-reduced-motion
 * block in landing.css.
 */

import { useEffect, useRef, useState, type ReactNode } from "react"
import type { SectionId, SectionCopy } from "../config/content"
import type { ExperienceMode } from "../state/landingStore"

export function useReveal<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [shown, setShown] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          // One-way: content that has been read should not fade out again on
          // the way back up.
          if (e.isIntersecting) {
            setShown(true)
            io.disconnect()
          }
        }
      },
      { rootMargin: "-12% 0px -12% 0px" },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [])

  return { ref, shown }
}

export function SectionFrame({
  id,
  copy,
  align = "left",
  mode,
  children,
}: {
  id: SectionId
  copy: SectionCopy
  align?: "left" | "right"
  mode: ExperienceMode
  children?: ReactNode
}) {
  const { ref, shown } = useReveal<HTMLElement>()

  return (
    <section
      id={`section-${id}`}
      ref={ref}
      className={`tc-section${align === "right" ? " tc-section--right" : ""}`}
      // Density is the one thing that legitimately differs between modes:
      // cinematic sections are full-height scroll journeys, normal sections
      // are tighter (spec §13's "standard vertical scrolling").
      data-mode={mode}
      aria-labelledby={`heading-${id}`}
    >
      <div className="tc-shell" style={{ width: "100%" }}>
        <div className="tc-section__copy">
          <div className="tc-eyebrow tc-reveal" data-shown={shown}>
            {copy.eyebrow}
          </div>
          <h2 id={`heading-${id}`} className="tc-h2 tc-reveal" data-shown={shown} data-delay="1">
            {copy.headline}
          </h2>
          <p className="tc-lead tc-reveal" data-shown={shown} data-delay="2">
            {copy.lead}
          </p>

          {copy.points.length > 0 && (
            <ul className="tc-points tc-reveal" data-shown={shown} data-delay="3">
              {copy.points.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}

          {children}
        </div>
      </div>
    </section>
  )
}
