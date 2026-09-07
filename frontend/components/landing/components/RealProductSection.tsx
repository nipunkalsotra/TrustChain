"use client"

/**
 * Real-product section (spec §12.10 / §13.6) — the credibility pivot.
 *
 * The interface itself is rendered in 3D by RealProductWorld so it can morph
 * out of the incoming proof object. This side carries only the copy, keeping
 * spec §1's rule intact: important UI text stays real HTML beside the WebGL,
 * never baked into a texture.
 */

import { SECTIONS } from "../config/content"
import type { ExperienceMode } from "../state/landingStore"
import { SectionFrame } from "./SectionFrame"

export function RealProductSection({ experienceMode }: { experienceMode: ExperienceMode }) {
  return <SectionFrame id="realproduct" copy={SECTIONS.realproduct} align="right" mode={experienceMode} />
}
