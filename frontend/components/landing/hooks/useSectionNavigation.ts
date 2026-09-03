"use client"

/**
 * Internal section navigation (spec §9 + §14).
 *
 * One scroll implementation, two arrival behaviours:
 *  - cinematic mode: a longer, eased travel; `frame.cameraTravel` is raised so
 *    the 3D director can fly the camera between worlds rather than cutting.
 *  - normal mode: a shorter smooth vertical scroll with a restrained arrival
 *    animation and no giant camera flight (spec §9, §13).
 *
 * The tween runs on a proxy and calls window.scrollTo itself rather than using
 * ScrollToPlugin, so section navigation keeps working identically whether or
 * not the hero pin is still alive — during the pin the document height is
 * 6 viewports taller, and a plugin-driven scroll measured before the pin was
 * killed would land in the wrong place.
 */

import { useCallback, useEffect } from "react"
import gsap from "gsap"
import { ScrollTrigger } from "gsap/ScrollTrigger"
import { frame, getUi, setUi } from "../state/landingStore"
import type { SectionId } from "../config/content"

const CINEMATIC_TRAVEL_S = 1.5
const NORMAL_TRAVEL_S = 0.85

export function scrollToSection(id: SectionId) {
  const el = document.getElementById(`section-${id}`)
  if (!el) return

  const mode = getUi().mode
  const reduced = getUi().reducedMotion
  const target = Math.max(0, el.getBoundingClientRect().top + window.scrollY)

  if (reduced) {
    window.scrollTo({ top: target, behavior: "auto" })
    setUi({ activeSection: id })
    return
  }

  const proxy = { y: window.scrollY }
  gsap.killTweensOf(proxy)
  gsap.to(proxy, {
    y: target,
    duration: mode === "cinematic" ? CINEMATIC_TRAVEL_S : NORMAL_TRAVEL_S,
    ease: mode === "cinematic" ? "power3.inOut" : "power2.out",
    onStart: () => {
      // The 3D director reads this to decide between a camera flight through
      // the worlds and a restrained arrival (spec §9).
      frame.activeWorld = id
    },
    onUpdate: () => window.scrollTo(0, proxy.y),
    onComplete: () => setUi({ activeSection: id }),
  })
}

/**
 * Tracks which section is actually in view so the navbar can show an active
 * item. Uses ScrollTrigger rather than IntersectionObserver because it already
 * knows about the pin spacer, and the two would otherwise disagree about
 * offsets while the opening is still pinned.
 */
export function useSectionNavigation(sectionIds: SectionId[]) {
  useEffect(() => {
    gsap.registerPlugin(ScrollTrigger)

    const triggers = sectionIds
      .map((id) => {
        const el = document.getElementById(`section-${id}`)
        if (!el) return null
        return ScrollTrigger.create({
          trigger: el,
          start: "top center",
          end: "bottom center",
          onToggle: (self) => {
            if (!self.isActive) return
            setUi({ activeSection: id })
            // Single source of truth for where the camera should be. Centre-
            // based, so exactly one section is ever active and the hero stays
            // active until the visitor has genuinely scrolled past it.
            frame.activeWorld = id
          },
        })
      })
      .filter((t): t is ScrollTrigger => t !== null)

    return () => {
      for (const t of triggers) t.kill()
    }
  }, [sectionIds])

  return useCallback((id: SectionId) => scrollToSection(id), [])
}
