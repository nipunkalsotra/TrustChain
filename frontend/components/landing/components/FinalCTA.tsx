"use client"

/**
 * Closing CTA (spec §23: "final CTA/footer microcopy may be chosen to fit the
 * existing TrustChain product language").
 */

import Link from "next/link"
import { FINAL_CTA } from "../config/content"
import { IconArrow } from "./Icons"
import { useReveal } from "./SectionFrame"

export function FinalCTA() {
  const { ref, shown } = useReveal<HTMLDivElement>()

  return (
    <section className="tc-shell" aria-labelledby="final-cta-heading">
      <div className="tc-final" ref={ref}>
        <h2 id="final-cta-heading" className="tc-final__headline tc-reveal" data-shown={shown}>
          {FINAL_CTA.headline}
        </h2>
        <p className="tc-final__lead tc-reveal" data-shown={shown} data-delay="1">
          {FINAL_CTA.lead}
        </p>
        <div className="tc-final__ctas tc-reveal" data-shown={shown} data-delay="2">
          <Link href={FINAL_CTA.primary.href} className="tc-btn tc-btn--primary">
            {FINAL_CTA.primary.label}
            <IconArrow />
          </Link>
          <Link href={FINAL_CTA.secondary.href} className="tc-btn tc-btn--ghost">
            {FINAL_CTA.secondary.label}
          </Link>
        </div>
        <div className="tc-final__note tc-reveal" data-shown={shown} data-delay="3">
          {FINAL_CTA.footnote}
        </div>
      </div>
    </section>
  )
}
