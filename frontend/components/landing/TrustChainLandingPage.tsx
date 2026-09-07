"use client"

/**
 * The landing page.
 *
 * Structure matches spec §11 exactly — one component/content structure with
 * two experience modes, not two duplicated pages:
 *
 *   <LandingPage>
 *     <Hero experienceMode={mode} />
 *     <ProductSection experienceMode={mode} />
 *     ... etc
 *     <FinalCTA />
 *   </LandingPage>
 *
 * The 3D layer is a single fixed canvas behind all of it, mounted once and
 * never remounted on a mode switch (spec §15.2).
 */

import { useEffect, useRef } from "react"
import "./landing.css"

import { LandingCanvas } from "./three/LandingCanvas"
import { Navbar } from "./components/Navbar"
import { HeroUI } from "./components/HeroUI"
import { OpeningAffordance } from "./components/OpeningAffordance"
import { ProductSection } from "./components/ProductSection"
import { ImmutableSection } from "./components/ImmutableSection"
import { MerkleSection } from "./components/MerkleSection"
import { BlockchainSection } from "./components/BlockchainSection"
import { VerificationSection } from "./components/VerificationSection"
import { RealProductSection } from "./components/RealProductSection"
import { SecuritySection } from "./components/SecuritySection"
import { FinalCTA } from "./components/FinalCTA"

import { useEnvironment } from "./hooks/useReducedMotion"
import { useOpeningTimeline, openingController } from "./hooks/useOpeningTimeline"
import {
  useExperienceMode,
  readSessionQuickView,
  writeSessionQuickView,
} from "./hooks/useExperienceMode"
import { useScrollVelocity } from "./hooks/useScrollVelocity"
import { usePointer } from "./hooks/usePointer"
import { useSectionNavigation } from "./hooks/useSectionNavigation"
import { SECTION_ORDER } from "./config/content"
import { useUi } from "./state/landingStore"

const SECTIONS_FOR_NAV = SECTION_ORDER

export function TrustChainLandingPage() {
  const heroRef = useRef<HTMLElement>(null)
  const { resolved, reducedMotion } = useEnvironment()
  const ui = useUi()

  // Ambient input, always live.
  useScrollVelocity()
  usePointer()

  // The pinned opening is only built once the environment is known, so a
  // reduced-motion visitor never has a 6-viewport pin created and torn down.
  useOpeningTimeline(heroRef, resolved ? reducedMotion : false)
  useExperienceMode()
  useSectionNavigation(SECTIONS_FOR_NAV)

  // Spec §4: "Do not store the mode forever. Session-level behavior is
  // acceptable; a fresh session may offer the full cinematic again." A visitor
  // who chose Quick View, clicked through to Pricing and came back should not
  // be made to sit through the opening again in the same tab.
  useEffect(() => {
    if (!resolved) return
    if (!readSessionQuickView()) return
    let raf = 0
    const tryAwaken = () => {
      if (openingController.isReady) {
        openingController.awaken("normal")
        return
      }
      raf = requestAnimationFrame(tryAwaken)
    }
    raf = requestAnimationFrame(tryAwaken)
    return () => cancelAnimationFrame(raf)
  }, [resolved])

  useEffect(() => {
    if (ui.mode === "normal" && ui.introAwakened) writeSessionQuickView()
  }, [ui.mode, ui.introAwakened])

  return (
    <div className="tc-landing" data-mode={ui.mode} data-awakened={ui.introAwakened}>
      {/* One canvas, behind everything, for the whole page. */}
      <LandingCanvas tier={ui.tier} reducedMotion={ui.reducedMotion} />

      <Navbar />
      <OpeningAffordance />

      <main>
        {/* The hero is pinned by useOpeningTimeline; everything inside it is
            real DOM text sitting over the WebGL, never baked into it. */}
        <section id="section-hero" ref={heroRef} className="tc-hero" aria-label="TrustChain">
          <div className="tc-hero__inner">
            <div className="tc-shell" style={{ width: "100%" }}>
              <HeroUI />
            </div>
          </div>
        </section>

        <ProductSection experienceMode={ui.mode} />
        <ImmutableSection experienceMode={ui.mode} />
        <MerkleSection experienceMode={ui.mode} />
        <BlockchainSection experienceMode={ui.mode} />
        <VerificationSection experienceMode={ui.mode} />
        <RealProductSection experienceMode={ui.mode} />
        <SecuritySection experienceMode={ui.mode} />

        <FinalCTA />
      </main>
    </div>
  )
}
