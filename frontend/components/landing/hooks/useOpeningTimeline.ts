"use client"

/**
 * The pinned opening cinematic (spec §6) — scroll controlled, never autoplay.
 *
 * Responsibilities:
 *  - Pin the hero for HERO_PIN_VH viewports and scrub `frame.introProgress`
 *    from the scroll position, deterministically (spec §15.1).
 *  - Track whole-page progress and per-section entrance progress, which the
 *    cinematic camera uses to travel between the section worlds.
 *  - Own the one-way `awaken()` transition, shared by BOTH the natural
 *    scroll-to-the-end path and the ENTER/ESC Quick View path (spec §10).
 *
 * Why awaken() kills the pin in both paths: spec §15.1 says the world must
 * never reverse back into its pre-awakening state once introAwakened is true,
 * and spec §15.2 says no invisible pinned spacer may be left behind. Keeping
 * the 6-viewport pin alive after the opening would violate one or the other —
 * either scrolling up replays the intro backwards, or those six viewports
 * become dead space. Killing the pin with scroll compensation satisfies both:
 * the hero collapses back to a single 100vh section already in its final
 * awakened state, and the scroll position is mapped to the visually
 * equivalent place so nothing jumps under the user.
 */

import { useEffect, useRef } from "react"
import gsap from "gsap"
import { ScrollTrigger } from "gsap/ScrollTrigger"
import { HERO_PIN_VH, QUICK_VIEW_RESOLVE_MS, clamp01, smoothstep } from "../config/motion"
import { frame, getUi, setUi, type ExperienceMode } from "../state/landingStore"
import { SECTION_ORDER } from "../config/content"

/**
 * Imperative handle so the keyboard listener in useExperienceMode can trigger
 * the resolve without the two hooks having to be mounted in the same tree.
 */
export const openingController = {
  // Replaced on mount by the real implementation below. The no-op default
  // matters: useExperienceMode's key listener attaches before the timeline
  // finishes building, and a keypress in that window must be inert, not a
  // crash.
  awaken: (mode: ExperienceMode) => {
    void mode
  },
  isReady: false,
}

export function useOpeningTimeline(
  heroRef: React.RefObject<HTMLElement | null>,
  reducedMotion: boolean,
) {
  const resolvingRef = useRef(false)

  useEffect(() => {
    const hero = heroRef.current
    if (!hero) return

    gsap.registerPlugin(ScrollTrigger)

    // ── prefers-reduced-motion: skip the pinned journey entirely ────────────
    // Spec §5/§18: "start close to the awakened hero/normal-mode state and
    // avoid intense shake". No pin is created at all, so there is no long
    // scroll journey to sit through and no spacer to clean up later.
    if (reducedMotion) {
      frame.introProgress = 1
      setUi({ introAwakened: true, mode: "normal" })
      openingController.isReady = true
    }

    const triggers: ScrollTrigger[] = []
    let cancelled = false
    let resolveRaf: (() => void) | null = null

    // ── The pinned opening ─────────────────────────────────────────────────
    let heroTrigger: ScrollTrigger | null = null
    if (!reducedMotion) {
      heroTrigger = ScrollTrigger.create({
        trigger: hero,
        start: "top top",
        end: `+=${HERO_PIN_VH * 100}%`,
        pin: true,
        pinSpacing: true,
        // scrub is not used here because we are not driving a GSAP timeline —
        // the 3D scene reads frame.introProgress in its own rAF loop, which is
        // both cheaper and keeps camera/physics interpolation damped rather
        // than snapped one-to-one to the wheel delta (spec §15).
        onUpdate: (self) => {
          if (resolvingRef.current) return
          if (getUi().introAwakened) return
          frame.introProgress = self.progress
        },
        onLeave: () => {
          // Natural completion: the visitor scrolled the whole opening.
          if (!getUi().introAwakened) awaken("cinematic")
        },
      })
      triggers.push(heroTrigger)
    }

    // ── Whole-page progress (drives cinematic travel between worlds) ───────
    triggers.push(
      ScrollTrigger.create({
        trigger: document.body,
        start: "top top",
        end: "bottom bottom",
        onUpdate: (self) => {
          frame.pageProgress = self.progress
        },
      }),
    )

    // ── Per-section entrance progress ─────────────────────────────────────
    for (const id of SECTION_ORDER) {
      if (id === "hero") continue
      const el = document.getElementById(`section-${id}`)
      if (!el) continue
      triggers.push(
        ScrollTrigger.create({
          trigger: el,
          start: "top bottom",
          end: "bottom top",
          onUpdate: (self) => {
            frame.sectionProgress[id] = self.progress
          },
          // Deliberately does NOT set frame.activeWorld. This trigger spans
          // "top bottom" to "bottom top" because it has to produce a full 0..1
          // entrance progress, which means a section counts as active the
          // instant one pixel of it enters the viewport. Using that same span
          // to decide which world the camera travels to was a real bug: at the
          // moment the opening resolved and the pin was reclaimed, the Product
          // section's top sat exactly at the viewport bottom, so it registered
          // as active and the camera flew 240 units away from the hero the
          // visitor had just finished awakening — a black frame where the
          // final hero composition should be.
          //
          // Which world is "active" is a centre-of-viewport question, and it
          // is answered in one place: useSectionNavigation.

        }),
      )
    }

    // ── The one-way awakening ─────────────────────────────────────────────
    function awaken(mode: ExperienceMode) {
      if (getUi().introAwakened || resolvingRef.current) return
      resolvingRef.current = true
      setUi({ resolving: true })

      const from = frame.introProgress

      const finish = () => {
        frame.introProgress = 1

        // Kill the pin and reclaim its spacer, then put the visitor at the
        // visually equivalent scroll position. `st.start` is where the pin
        // began, which after reverting the spacer is exactly where the hero's
        // final frame sits — so this is continuous, not a teleport.
        const restoreY = heroTrigger ? heroTrigger.start : 0
        heroTrigger?.kill(true)
        heroTrigger = null

        ScrollTrigger.refresh()
        window.scrollTo({ top: Math.max(0, restoreY), behavior: "instant" as ScrollBehavior })

        resolvingRef.current = false
        setUi({ introAwakened: true, mode, resolving: false })
        // Spec §4: "After the opening has resolved, remove the global
        // ENTER/ESC cinematic listeners so normal keyboard interaction works
        // as expected." useExperienceMode watches introAwakened to do that.
      }

      if (from >= 0.999) {
        finish()
        return
      }

      // Spec §10: resolve over ~300-500ms, not an instantaneous one-frame
      // teleport. Everything in the scene is a pure function of introProgress,
      // so animating that single number fast-forwards the entire opening —
      // monolith, chains, rocks, logo travel and camera — in lockstep, with no
      // separate "skip" code path that could drift out of sync (spec §15.1).
      //
      // Driven from performance.now() on a bare rAF rather than a GSAP tween,
      // deliberately. GSAP's ticker applies lagSmoothing: once a frame exceeds
      // ~500ms it advances tween time by a nominal 33ms instead of the real
      // elapsed time. That is the right default for keeping long animations
      // from jumping after a stall, but it is wrong for this one — on a device
      // slow enough to trigger it, a 420ms resolve stretched past eight
      // seconds and the page sat in "resolving" indefinitely (reproduced under
      // a software renderer at ~4fps). Wall-clock progress makes the resolve
      // take 420ms of real time on any hardware, which is what the spec asks
      // for; on a slow device it simply arrives in fewer, larger steps.
      const startedAt = performance.now()
      let raf = 0

      const step = () => {
        if (cancelled) return
        const elapsed = performance.now() - startedAt
        const k = clamp01(elapsed / QUICK_VIEW_RESOLVE_MS)
        frame.introProgress = clamp01(from + (1 - from) * smoothstep(k))
        if (k >= 1) {
          window.clearTimeout(watchdog)
          finish()
          return
        }
        raf = requestAnimationFrame(step)
      }
      raf = requestAnimationFrame(step)

      // Belt and braces: requestAnimationFrame does not fire at all in a
      // background tab, and the visitor can switch away mid-resolve. Without
      // this the page would come back still "resolving" and refuse every
      // subsequent ENTER/ESC. The timer costs nothing and guarantees the
      // one-way transition always completes.
      const watchdog = window.setTimeout(() => {
        if (cancelled || getUi().introAwakened) return
        finish()
      }, QUICK_VIEW_RESOLVE_MS * 4)

      resolveRaf = () => {
        cancelAnimationFrame(raf)
        window.clearTimeout(watchdog)
      }
    }

    openingController.awaken = awaken
    openingController.isReady = true

    // Spec §15: pause nonessential simulation when the tab is hidden.
    const onVisibility = () => {
      frame.visible = document.visibilityState === "visible"
    }
    document.addEventListener("visibilitychange", onVisibility)

    // Late-loading fonts/images change section offsets; without this the
    // per-section triggers measure against a stale layout.
    const onLoad = () => ScrollTrigger.refresh()
    window.addEventListener("load", onLoad)

    return () => {
      cancelled = true
      resolveRaf?.()
      // Critical: clear the in-flight flag on teardown. `resolvingRef` is a
      // ref, so it survives an effect re-run (a hot reload, a dependency
      // change, a remount). If a resolve was interrupted mid-flight the flag
      // would still read true afterwards, and every subsequent awaken() —
      // ENTER, ESC, or scrolling to the end of the opening — would bail
      // silently, leaving the visitor permanently stuck in the cinematic with
      // no way out. Observed exactly that during development.
      resolvingRef.current = false
      document.removeEventListener("visibilitychange", onVisibility)
      window.removeEventListener("load", onLoad)
      openingController.awaken = () => {}
      openingController.isReady = false
      for (const t of triggers) t.kill(true)
    }
  }, [heroRef, reducedMotion])
}
