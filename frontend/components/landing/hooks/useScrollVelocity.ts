"use client"

/**
 * Smoothed, clamped scroll velocity.
 *
 * Spec §6.2: "Scroll velocity may influence dust intensity and impact
 * strength, but clamp the effect to prevent violent spikes from fast mouse
 * wheels." Spec §15: "Clamp scroll velocity influence and physics impulses."
 *
 * Written into the non-reactive `frame` object on GSAP's ticker so it stays
 * on the same clock as the scene's own frame loop and costs zero re-renders.
 */

import { useEffect } from "react"
import gsap from "gsap"
import { frame } from "../state/landingStore"
import { clamp, damp } from "../config/motion"

/** Above this px/frame the effect saturates — a flung trackpad can't spike it. */
const VELOCITY_SATURATION = 90

export function useScrollVelocity() {
  useEffect(() => {
    let lastY = window.scrollY
    let raw = 0

    const tick = (_time: number, deltaMs: number) => {
      const dt = Math.min(deltaMs, 100) / 1000
      const y = window.scrollY
      const instant = (y - lastY) / Math.max(dt, 0.001)
      lastY = y

      // Normalize to -1..1 and saturate, so the downstream consumers (dust
      // density, cloth push, impact strength) can never receive a spike.
      const normalized = clamp(instant / (VELOCITY_SATURATION * 60), -1, 1)
      raw = damp(raw, normalized, 8, dt)
      frame.scrollVelocity = raw
    }

    gsap.ticker.add(tick)
    return () => gsap.ticker.remove(tick)
  }, [])
}
