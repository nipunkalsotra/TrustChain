"use client"

/**
 * Product section (spec §12.1 / §13.1).
 *
 * The four stage names are rendered from PIPELINE_STAGES, which carries the
 * content lock: "Preserve the exact four pipeline labels Researcher /
 * Validator / Scorer / Reporter because they match the running LangGraph node
 * names. Do not rename those nodes." (spec §1).
 */

import { SECTIONS, PIPELINE_STAGES } from "../config/content"
import type { ExperienceMode } from "../state/landingStore"
import { SectionFrame, useReveal } from "./SectionFrame"

export function ProductSection({ experienceMode }: { experienceMode: ExperienceMode }) {
  const { ref, shown } = useReveal<HTMLDivElement>()

  return (
    <SectionFrame id="product" copy={SECTIONS.product} align="left" mode={experienceMode}>
      <div className="tc-stages" ref={ref}>
        {PIPELINE_STAGES.map((stage, i) => (
          <div
            key={stage.name}
            className="tc-stage tc-reveal"
            data-shown={shown}
            data-delay={Math.min(i + 1, 4)}
          >
            <div className="tc-stage__index">{stage.index}</div>
            <div>
              <h3 className="tc-stage__name">{stage.name}</h3>
              <p className="tc-stage__body">{stage.body}</p>
            </div>
          </div>
        ))}
      </div>
    </SectionFrame>
  )
}
