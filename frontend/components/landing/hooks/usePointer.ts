"use client"

/**
 * Pointer tracking for the cursor-driven ambient response (spec §7.2).
 *
 * Writes normalized device coordinates into `frame` — no React state, because
 * this fires at pointer rate and every consumer is a useFrame loop.
 *
 * Spec §18: "Do not rely on hover-only navigation on touch devices." Touch
 * pointers are ignored here entirely; the scene simply keeps its idle ambient
 * behaviour, which is the correct fallback rather than a jittery
 * last-touch-position bias.
 */

import { useEffect } from "react"
import { frame } from "../state/landingStore"
import { clamp } from "../config/motion"

export function usePointer() {
  useEffect(() => {
    let lastX = 0
    let lastY = 0
    let primed = false

    const onMove = (e: PointerEvent) => {
      if (e.pointerType === "touch") return

      const nx = (e.clientX / window.innerWidth) * 2 - 1
      const ny = -((e.clientY / window.innerHeight) * 2 - 1)

      if (primed) {
        const dx = nx - lastX
        const dy = ny - lastY
        // Spec §7.2: "Cursor velocity can add a tiny extra impulse to
        // dust/cloak/particles, clamped heavily."
        frame.pointerSpeed = clamp(Math.hypot(dx, dy) * 6, 0, 1)
      }
      primed = true
      lastX = nx
      lastY = ny

      frame.pointerX = nx
      frame.pointerY = ny
    }

    const onLeave = () => {
      // Drift back to centre rather than freezing at the edge, so the scene
      // settles into its idle state when the cursor leaves the window.
      frame.pointerX = 0
      frame.pointerY = 0
      frame.pointerSpeed = 0
      primed = false
    }

    window.addEventListener("pointermove", onMove, { passive: true })
    window.addEventListener("pointerleave", onLeave)
    return () => {
      window.removeEventListener("pointermove", onMove)
      window.removeEventListener("pointerleave", onLeave)
    }
  }, [])
}
