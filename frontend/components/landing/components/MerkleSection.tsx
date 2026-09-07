"use client"

/** Merkle Anchored section (spec §12.5 / §13.3). Approved copy, unmodified. */

import { SECTIONS } from "../config/content"
import type { ExperienceMode } from "../state/landingStore"
import { SectionFrame } from "./SectionFrame"

export function MerkleSection({ experienceMode }: { experienceMode: ExperienceMode }) {
  return <SectionFrame id="merkle" copy={SECTIONS.merkle} align="left" mode={experienceMode} />
}
