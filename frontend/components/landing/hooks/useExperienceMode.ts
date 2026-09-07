"use client"

/**
 * ENTER / ESC Quick View (spec §4 + §10).
 *
 * Finalized meaning of both keys: they skip the opening origin cinematic and
 * switch the current page session into NORMAL MODE. They do NOT open another
 * page, do NOT disable 3D, and do NOT freeze the hero.
 *
 * The listener is deliberately removed the moment the opening resolves —
 * spec §4: "After the opening has resolved, remove the global ENTER/ESC
 * cinematic listeners so normal keyboard interaction works as expected."
 * Leaving them attached would swallow ESC from a future dialog and hijack
 * ENTER on buttons and links.
 */

import { useEffect } from "react"
import { setUi, useUiSelector } from "../state/landingStore"
import { openingController } from "./useOpeningTimeline"

export function useExperienceMode() {
  const introAwakened = useUiSelector((s) => s.introAwakened)

  useEffect(() => {
    // Spec §4: the shortcut exists "during the opening only".
    if (introAwakened) return

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Enter" && e.key !== "Escape") return

      // Never steal the key from something the visitor is actually using.
      // The affordance sits over a mostly-empty cinematic, but the navbar and
      // its CTAs are focusable from the very first frame.
      const el = document.activeElement as HTMLElement | null
      if (el) {
        const tag = el.tagName
        if (
          tag === "INPUT" ||
          tag === "TEXTAREA" ||
          tag === "SELECT" ||
          tag === "BUTTON" ||
          tag === "A" ||
          el.isContentEditable
        ) {
          return
        }
      }

      e.preventDefault()
      openingController.awaken("normal")
    }

    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [introAwakened])

  return useUiSelector((s) => s.mode)
}

/**
 * Spec §4: "Do not store the mode forever. Session-level behavior is
 * acceptable; a fresh session may offer the full cinematic again."
 *
 * sessionStorage (not localStorage) is exactly that contract: a visitor who
 * chose Quick View and then clicked through to Pricing and back shouldn't be
 * made to sit through the opening a second time in the same tab, but a fresh
 * visit tomorrow gets the full experience.
 */
const SESSION_KEY = "trustchain.landing.quickview"

export function readSessionQuickView(): boolean {
  if (typeof window === "undefined") return false
  try {
    return window.sessionStorage.getItem(SESSION_KEY) === "1"
  } catch {
    // Private-mode / blocked storage: fall back to giving the full cinematic.
    return false
  }
}

export function writeSessionQuickView() {
  try {
    window.sessionStorage.setItem(SESSION_KEY, "1")
  } catch {
    /* non-fatal */
  }
}

/** Applies a same-session Quick View preference before the pin is built. */
export function useSessionQuickView(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return
    if (!readSessionQuickView()) return
    setUi({ mode: "normal" })
  }, [enabled])
}
