"use client"

/**
 * Security section (spec §12.11 / §13.7).
 *
 * The four cards are the approved 4-card grid, and the same SECURITY_LAYERS
 * array drives the four protection/isolation layers in SecurityWorld — so the
 * concept a visitor reads and the layer they see highlight together and can
 * never fall out of step (spec: "do not invent replacement concepts").
 */

import { SECTIONS, SECURITY_LAYERS } from "../config/content"
import type { ExperienceMode } from "../state/landingStore"
import { SectionFrame, useReveal } from "./SectionFrame"

export function SecuritySection({ experienceMode }: { experienceMode: ExperienceMode }) {
  const { ref, shown } = useReveal<HTMLDivElement>()

  return (
    <SectionFrame id="security" copy={SECTIONS.security} align="left" mode={experienceMode}>
      <div className="tc-grid-2" ref={ref}>
        {SECURITY_LAYERS.map((layer, i) => (
          <article
            key={layer.id}
            className="tc-card tc-reveal"
            data-shown={shown}
            data-delay={Math.min(i + 1, 4)}
          >
            <h3 className="tc-card__title">{layer.title}</h3>
            <p className="tc-card__body">{layer.body}</p>
          </article>
        ))}
      </div>
    </SectionFrame>
  )
}
