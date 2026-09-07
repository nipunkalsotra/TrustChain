"use client"

/** Immutable Logs section (spec §12.3 / §13.2). Approved copy, unmodified. */

import { SECTIONS } from "../config/content"
import type { ExperienceMode } from "../state/landingStore"
import { SectionFrame } from "./SectionFrame"

export function ImmutableSection({ experienceMode }: { experienceMode: ExperienceMode }) {
  return <SectionFrame id="immutable" copy={SECTIONS.immutable} align="right" mode={experienceMode} />
}
