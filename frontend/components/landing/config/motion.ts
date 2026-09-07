/**
 * Opening-cinematic timeline definition and shared motion constants.
 *
 * Spec §15.1: "Use timeline labels / normalized progress ranges rather than
 * scattering magic scroll numbers through components." Every beat of the
 * opening lives in BEATS below as a normalized [start, end] range; scene
 * components ask `beatProgress(t, "punch")` instead of hardcoding `0.26`.
 *
 * Spec §6: "The percentages below are a conceptual guide, not fixed numbers.
 * Tune them by feel after testing." — i.e. this table is *meant* to be edited.
 */

export type BeatName =
  | "walk"
  | "slow"
  | "punchWindup"
  | "punch"
  | "shockwave"
  | "monolithRise"
  | "settle"
  | "ignition"
  | "energyWave"
  | "rocks"
  | "chains"
  | "logoTravel"
  | "uiReveal"

/** Normalized [start, end] scroll progress for each beat of the opening. */
export const BEATS: Record<BeatName, [number, number]> = {
  //  0-8%   dark environment / camera follows the man from behind
  walk: [0.0, 0.18],
  //  18-26% man slows, plants himself and prepares the punch
  slow: [0.18, 0.26],
  //  26-32% camera swings into a low three-quarter/side angle
  punchWindup: [0.26, 0.3],
  //  the punch itself — a near-instant event, not a range to ease across
  punch: [0.3, 0.32],
  //  32-42% ground shockwave, cracks, debris, camera impulse
  shockwave: [0.32, 0.42],
  //  42-60% monolith rises from below the fractured ground
  monolithRise: [0.42, 0.6],
  //  60-66% monolith settles; brief dark/silent anticipation
  settle: [0.6, 0.66],
  //  66-70% TrustChain core/logo ignites almost instantly
  ignition: [0.66, 0.7],
  //  70-80% huge spherical blue energy wave expands through the world
  energyWave: [0.7, 0.8],
  //  74-84% rocks energize and levitate as the wave reaches them
  rocks: [0.74, 0.84],
  //  82-92% approved chains deploy sequentially and bind the monolith
  chains: [0.82, 0.92],
  //  92-97% logo copy emerges from monolith and travels into the navbar
  logoTravel: [0.92, 0.97],
  //  97-100% hero text, CTAs, feature controls and nav resolve
  uiReveal: [0.97, 1.0],
}

/**
 * Normalized progress *within* a beat: 0 before it starts, 1 after it ends.
 * Everything in the hero scene reads its own state through this, which is why
 * scrubbing backwards is automatically correct — there's no accumulated state.
 */
export function beatProgress(t: number, beat: BeatName): number {
  const [a, b] = BEATS[beat]
  if (b <= a) return t >= b ? 1 : 0
  return clamp01((t - a) / (b - a))
}

/** True once a beat has begun (useful for one-way events like the punch). */
export function beatStarted(t: number, beat: BeatName): boolean {
  return t >= BEATS[beat][0]
}

// ── Camera choreography (spec §6.1.1 — five meaningful shots) ───────────────
//
// "Do not constantly swing the camera. Use a few deliberate shots connected by
// smooth spline/orbit transitions so it feels like cinema rather than a game
// camera." Each shot is a keyframe; the camera rig interpolates between them
// with damping, it does not track scroll one-to-one.

export interface CameraShot {
  id: string
  /** Scroll progress at which this shot is fully composed. */
  at: number
  /** Camera position, in the character's local frame (x = right, z = behind). */
  position: [number, number, number]
  /** Look-at target offset from the character's feet. */
  target: [number, number, number]
  fov: number
}

export const HERO_SHOTS: CameraShot[] = [
  // SHOT 1 — behind the man: low follow camera, strong foreground parallax.
  // Deliberately distant. The guardian is roughly 1.8 units tall and the
  // monolith will be 34; if shot 1 frames him like a character-select screen,
  // nothing that comes later can feel colossal by comparison.
  { id: "behind", at: 0.0, position: [1.4, 3.2, 15.0], target: [0, 2.0, -14], fov: 40 },
  { id: "behind-close", at: 0.24, position: [1.8, 2.4, 10.5], target: [0, 1.6, -10], fov: 38 },
  // SHOT 2 — punch setup/impact: orbit ~45° into a low three-quarter view so
  // the user clearly sees the man punch the ground. This is the one shot that
  // comes close, because the punch has to be legible.
  { id: "punch-3q", at: 0.31, position: [6.2, 1.6, 5.4], target: [0, 0.9, -1.0], fov: 36 },
  // SHOT 3 — consequence: camera returns behind/slightly beside the man as
  // cracks race outward and the monolith begins rising in front of him.
  { id: "consequence", at: 0.5, position: [4.0, 4.2, 14.0], target: [1.0, 8.0, -14], fov: 46 },
  // SHOT 4 — monumental wide: man small, monolith enormous, for the ignition
  // and spherical energy-wave event.
  { id: "monumental", at: 0.74, position: [-6.0, 14.0, 52.0], target: [1.0, 16.0, -12], fov: 46 },
  // SHOT 5 — final hero composition. Aimed LEFT of the monolith so it sits in
  // the right half of the frame and the hero copy has the left half to itself,
  // matching the approved reference composition.
  { id: "hero", at: 1.0, position: [-9.0, 9.5, 46.0], target: [-5.0, 12.0, -12], fov: 44 },
]

// ── Timing constants ────────────────────────────────────────────────────────

/** Spec §10: Quick View resolves in ~300-500ms, not a one-frame teleport. */
export const QUICK_VIEW_RESOLVE_MS = 420

/** Spec §8.1: "wait briefly before collapsing so the menu does not feel twitchy." */
export const NAV_COLLAPSE_DELAY_MS = 380

/** How many viewport heights the pinned opening occupies. */
export const HERO_PIN_VH = 6

/** Damping factor for camera interpolation (spec §15: damped, deterministic). */
export const CAMERA_DAMP = 2.6

// ── Small math helpers used across scene code ───────────────────────────────

export const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v)
export const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v)
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t
export const smoothstep = (t: number) => {
  const x = clamp01(t)
  return x * x * (3 - 2 * x)
}
/** Ease used for heavy objects (the monolith rise): slow start, slow settle. */
export const easeHeavy = (t: number) => {
  const x = clamp01(t)
  return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2
}
/** Ease used for the ignition flash: near-instant attack (spec §6.4). */
export const easeIgnite = (t: number) => 1 - Math.pow(1 - clamp01(t), 8)
/** Frame-rate independent exponential damping. */
export const damp = (current: number, target: number, lambda: number, dt: number) =>
  lerp(current, target, 1 - Math.exp(-lambda * dt))
