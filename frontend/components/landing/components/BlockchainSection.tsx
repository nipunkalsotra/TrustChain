"use client"

/** On-Chain Verified section (spec §12.7 / §13.4). Approved copy, unmodified. */

import { SECTIONS } from "../config/content"
import type { ExperienceMode } from "../state/landingStore"
import { SectionFrame } from "./SectionFrame"

export function BlockchainSection({ experienceMode }: { experienceMode: ExperienceMode }) {
  return <SectionFrame id="blockchain" copy={SECTIONS.blockchain} align="right" mode={experienceMode} />
}
