"use client"

/**
 * Navbar (spec §8, §8.1).
 *
 * Two behaviours the spec is specific about:
 *
 *  - The brand is NOT a normal static logo at the start of the cinematic. It
 *    appears only after the monolith-logo-to-navbar transition has completed
 *    (or during the Quick View resolve). That handoff is timed against the
 *    same beat the 3D LogoTravel finishes on, so the holographic copy fades
 *    exactly as the DOM brand takes its place.
 *
 *  - The four primary targets are compact icons inside a floating glass
 *    capsule that expands on hover OR keyboard focus, revealing labels. On
 *    pointer leave it waits before collapsing "so the menu does not feel
 *    twitchy" (spec §8.1) — and it never collapses while focus is still
 *    inside it, which is what keeps it usable from the keyboard.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { BrandMark, NAV_ICONS } from "./Icons"
import { BRAND, NAV_TARGETS, NAV_ACTIONS } from "../config/content"
import { NAV_COLLAPSE_DELAY_MS, BEATS } from "../config/motion"
import { frame, useUi } from "../state/landingStore"
import { scrollToSection } from "../hooks/useSectionNavigation"

export function Navbar() {
  const ui = useUi()
  const [expanded, setExpanded] = useState(false)
  /** Brand visibility is driven by the intro timeline, not by React state. */
  const [brandVisible, setBrandVisible] = useState(false)
  const collapseTimer = useRef<number | null>(null)
  const capsule = useRef<HTMLElement>(null)

  // The logo has "arrived" once the travel beat is essentially complete. Polled
  // on rAF rather than subscribed, because intro progress deliberately never
  // enters React state (spec §15).
  useEffect(() => {
    if (brandVisible) return
    let raf = 0
    const check = () => {
      if (frame.introProgress >= BEATS.logoTravel[1] - 0.004 || ui.introAwakened) {
        setBrandVisible(true)
        return
      }
      raf = requestAnimationFrame(check)
    }
    raf = requestAnimationFrame(check)
    return () => cancelAnimationFrame(raf)
  }, [brandVisible, ui.introAwakened])

  const open = useCallback(() => {
    if (collapseTimer.current) {
      window.clearTimeout(collapseTimer.current)
      collapseTimer.current = null
    }
    setExpanded(true)
  }, [])

  const scheduleClose = useCallback(() => {
    if (collapseTimer.current) window.clearTimeout(collapseTimer.current)
    collapseTimer.current = window.setTimeout(() => {
      // Never collapse out from under a keyboard user.
      if (capsule.current?.contains(document.activeElement)) return
      setExpanded(false)
    }, NAV_COLLAPSE_DELAY_MS)
  }, [])

  useEffect(
    () => () => {
      if (collapseTimer.current) window.clearTimeout(collapseTimer.current)
    },
    [],
  )

  return (
    <header className="tc-nav">
      <Link
        href="/"
        className="tc-brand"
        data-visible={brandVisible}
        // Before the logo arrives the brand is not merely invisible — it is
        // removed from the tab order and the accessibility tree, so a keyboard
        // user cannot land on something that isn't on screen yet.
        aria-hidden={!brandVisible}
        tabIndex={brandVisible ? 0 : -1}
      >
        <BrandMark className="tc-brand__mark" />
        <span className="tc-brand__text">
          <span className="tc-brand__name">{BRAND.wordmark}</span>
          <span className="tc-brand__sub">{BRAND.subtitle}</span>
        </span>
      </Link>

      <nav
        ref={capsule}
        className="tc-capsule"
        data-expanded={expanded}
        aria-label="Primary"
        onPointerEnter={(e) => {
          // Spec §18: don't rely on hover on touch. On touch the capsule is
          // permanently expanded via CSS, so ignore synthesized enters.
          if (e.pointerType === "touch") return
          open()
        }}
        onPointerLeave={(e) => {
          if (e.pointerType === "touch") return
          scheduleClose()
        }}
        onFocusCapture={open}
        onBlurCapture={scheduleClose}
      >
        {NAV_TARGETS.map((target) => {
          const Icon = NAV_ICONS[target.id as keyof typeof NAV_ICONS]
          const active = target.kind === "section" && ui.activeSection === target.section

          if (target.kind === "route") {
            return (
              <Link
                key={target.id}
                href={target.href!}
                className="tc-capsule__item"
                data-active={active}
              >
                <Icon className="tc-capsule__icon" />
                <span className="tc-capsule__label">{target.label}</span>
              </Link>
            )
          }

          return (
            <button
              key={target.id}
              type="button"
              className="tc-capsule__item"
              data-active={active}
              aria-current={active ? "true" : undefined}
              onClick={() => scrollToSection(target.section!)}
            >
              <Icon className="tc-capsule__icon" />
              <span className="tc-capsule__label">{target.label}</span>
            </button>
          )
        })}
      </nav>

      <div
        className="tc-nav__actions"
        data-visible={brandVisible}
        aria-hidden={!brandVisible}
      >
        <Link
          href={NAV_ACTIONS.login.href}
          className="tc-btn tc-btn--ghost tc-btn--small"
          tabIndex={brandVisible ? 0 : -1}
        >
          {NAV_ACTIONS.login.label}
        </Link>
        <Link
          href={NAV_ACTIONS.getStarted.href}
          className="tc-btn tc-btn--primary tc-btn--small"
          tabIndex={brandVisible ? 0 : -1}
        >
          {NAV_ACTIONS.getStarted.label}
        </Link>
      </div>
    </header>
  )
}
