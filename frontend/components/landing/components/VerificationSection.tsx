"use client"

/** Publicly Verifiable section (spec §12.9 / §13.5). Approved copy, unmodified. */

import { SECTIONS } from "../config/content"
import type { ExperienceMode } from "../state/landingStore"
import { SectionFrame } from "./SectionFrame"

export function VerificationSection({ experienceMode }: { experienceMode: ExperienceMode }) {
  return <SectionFrame id="verification" copy={SECTIONS.verification} align="left" mode={experienceMode} />
}
