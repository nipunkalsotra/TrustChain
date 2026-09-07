"use client"

/**
 * prefers-reduced-motion, resolved after mount.
 *
 * Read on the client only and pushed into the store, because the server has no
 * way to know it — rendering the reduced variant on the server and the full one
 * on the client (or vice versa) is a hydration mismatch. The first client
 * render therefore matches the server (full experience), and reduced motion is
 * applied one commit later, before the pin is ever created.
 */

import { useEffect, useState } from "react"
import { setUi } from "../state/landingStore"
import { detectTier, prefersReducedMotion } from "../config/performance"

export function useEnvironment() {
  const [resolved, setResolved] = useState(false)

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)")

    const apply = () => {
      setUi({ reducedMotion: mq.matches, tier: detectTier() })
      setResolved(true)
    }
    apply()

    mq.addEventListener("change", apply)
    return () => mq.removeEventListener("change", apply)
  }, [])

  return { resolved, reducedMotion: prefersReducedMotion() }
}
