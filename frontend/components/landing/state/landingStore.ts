/**
 * Landing-page state, deliberately split in two.
 *
 * Spec §15: "Never update high-frequency animation state through React
 * setState on every frame. Use refs, frame loops and physics engine state."
 *
 *   `frame`  — a plain mutable object written every frame by ScrollTrigger,
 *              the pointer listener and the scene's own useFrame loops. React
 *              NEVER subscribes to it. No allocation, no re-render, no
 *              reconciliation on the hot path.
 *
 *   `ui`     — a genuinely low-frequency, IMMUTABLE snapshot (mode switches,
 *              intro-awakened, which nav item is open). Replaced, never
 *              mutated, so `useSyncExternalStore` is safe here: the snapshot
 *              identity only changes when something real changed.
 *
 * The server snapshot is a frozen constant that matches the pre-hydration DOM
 * (cinematic mode, intro not awakened) so the first client render agrees with
 * the server render and nothing flashes.
 */

import { useSyncExternalStore } from "react"

// ── High-frequency, non-reactive ────────────────────────────────────────────

export interface FrameState {
  /** Normalized progress through the pinned opening cinematic, 0..1. */
  introProgress: number
  /** Normalized progress through the whole page, 0..1 (drives world travel). */
  pageProgress: number
  /** Per-section 0..1 entrance progress, keyed by section id. */
  sectionProgress: Record<string, number>
  /** Signed scroll velocity, already smoothed and clamped (spec §15). */
  scrollVelocity: number
  /** Pointer in normalized device coords, -1..1. */
  pointerX: number
  pointerY: number
  /** Pointer movement magnitude this frame, clamped (spec §7.2). */
  pointerSpeed: number
  /** Set true on the frame the punch lands; scene code consumes and clears. */
  punchImpulse: number
  /** Camera shake energy, decayed each frame (spec §6.2: physically motivated). */
  cameraImpulse: number
  /** Live chain tension 0..1 while the cursor is dragging a link (spec §7.1). */
  chainTension: number
  /** True while a chain link is grabbed. */
  chainGrabbed: boolean
  /** Which section the camera is currently nearest, for cinematic world travel. */
  activeWorld: string
  /** Tab visibility — spec §15: "Pause/reduce nonessential simulation." */
  visible: boolean
}

export const frame: FrameState = {
  introProgress: 0,
  pageProgress: 0,
  sectionProgress: {},
  scrollVelocity: 0,
  pointerX: 0,
  pointerY: 0,
  pointerSpeed: 0,
  punchImpulse: 0,
  cameraImpulse: 0,
  chainTension: 0,
  chainGrabbed: false,
  activeWorld: "hero",
  visible: true,
}

/** Reset everything the opening owns. Used when Quick View fast-forwards. */
export function resetFrameImpulses() {
  frame.punchImpulse = 0
  frame.cameraImpulse = 0
  frame.scrollVelocity = 0
}

// ── Low-frequency, reactive ─────────────────────────────────────────────────

export type ExperienceMode = "cinematic" | "normal"

export interface UiState {
  mode: ExperienceMode
  /**
   * Spec §10: "The intro is one-way for the current session; scrolling back to
   * the top must not bury the monolith again or make the man walk backward."
   */
  introAwakened: boolean
  /** True during the ~300-500ms Quick View resolve, so DOM can cross-fade. */
  resolving: boolean
  /** Set once the WebGL scene has actually rendered a frame. */
  sceneReady: boolean
  /** Tier resolved at mount; scenes read it for their own counts. */
  tier: "high" | "medium" | "low"
  reducedMotion: boolean
  /** Section currently in view — drives nav active state. */
  activeSection: string
  /** Whether the compact nav capsule is expanded (spec §8.1). */
  navExpanded: boolean
}

const SERVER_SNAPSHOT: UiState = Object.freeze({
  mode: "cinematic",
  introAwakened: false,
  resolving: false,
  sceneReady: false,
  tier: "medium",
  reducedMotion: false,
  activeSection: "hero",
  navExpanded: false,
})

let uiState: UiState = SERVER_SNAPSHOT
const listeners = new Set<() => void>()

function emit() {
  for (const l of listeners) l()
}

export function setUi(patch: Partial<UiState>) {
  let changed = false
  for (const k of Object.keys(patch) as (keyof UiState)[]) {
    if (patch[k] !== undefined && uiState[k] !== patch[k]) {
      changed = true
      break
    }
  }
  // No-op writes are common (e.g. an IntersectionObserver re-reporting the
  // same active section every scroll). Bailing here is what keeps this store
  // genuinely low-frequency instead of "React state with extra steps".
  if (!changed) return
  uiState = Object.freeze({ ...uiState, ...patch })
  emit()
}

export function getUi(): UiState {
  return uiState
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

function getServerSnapshot(): UiState {
  return SERVER_SNAPSHOT
}

/** Subscribe to the whole UI snapshot. */
export function useUi(): UiState {
  return useSyncExternalStore(subscribe, getUi, getServerSnapshot)
}

/**
 * Subscribe to one derived slice, so a component that only cares about `mode`
 * doesn't re-render when `activeSection` changes. The selector must return a
 * primitive (or a stable reference) — every value in UiState already is.
 */
export function useUiSelector<T>(selector: (s: UiState) => T): T {
  return useSyncExternalStore(
    subscribe,
    () => selector(uiState),
    () => selector(SERVER_SNAPSHOT),
  )
}

/** Test/HMR escape hatch — resets the module singleton. */
export function __resetLandingStore() {
  uiState = SERVER_SNAPSHOT
  Object.assign(frame, {
    introProgress: 0,
    pageProgress: 0,
    sectionProgress: {},
    scrollVelocity: 0,
    pointerX: 0,
    pointerY: 0,
    pointerSpeed: 0,
    punchImpulse: 0,
    cameraImpulse: 0,
    chainTension: 0,
    chainGrabbed: false,
    activeWorld: "hero",
    visible: true,
  })
  emit()
}
