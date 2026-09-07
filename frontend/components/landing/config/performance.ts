/**
 * Device tiering and quality budgets.
 *
 * Spec §15: "Target 60 fps on a typical modern desktop. Degrade gracefully
 * rather than forcing full quality everywhere." Every count/toggle a scene
 * uses for its own cost lives here, so reducing quality is one table edit
 * rather than a hunt through shaders.
 */

export type Tier = "high" | "medium" | "low"

export interface QualityBudget {
  tier: Tier
  /** Spec §15: "Cap devicePixelRatio dynamically, especially on high-DPI laptops." */
  dpr: [number, number]
  dustCount: number
  debrisCount: number
  heroRockCount: number
  instancedRockCount: number
  /** Links per chain. Fewer links = fewer rigid bodies = cheaper solver. */
  chainLinks: number
  /** Spec §6.6 — desktop chains use real physics; below that they're kinematic. */
  physicsChains: boolean
  postprocessing: boolean
  shadows: boolean
  blockchainBlocks: number
  merkleLeaves: number
}

const HIGH: QualityBudget = {
  tier: "high",
  dpr: [1, 1.75],
  dustCount: 2600,
  debrisCount: 420,
  heroRockCount: 9,
  instancedRockCount: 160,
  chainLinks: 15,
  physicsChains: true,
  postprocessing: true,
  shadows: true,
  blockchainBlocks: 220,
  merkleLeaves: 16,
}

const MEDIUM: QualityBudget = {
  tier: "medium",
  dpr: [1, 1.35],
  dustCount: 1200,
  debrisCount: 200,
  heroRockCount: 6,
  instancedRockCount: 80,
  chainLinks: 12,
  physicsChains: true,
  postprocessing: true,
  shadows: false,
  blockchainBlocks: 120,
  merkleLeaves: 16,
}

const LOW: QualityBudget = {
  tier: "low",
  dpr: [1, 1],
  dustCount: 420,
  debrisCount: 70,
  heroRockCount: 4,
  instancedRockCount: 28,
  chainLinks: 12,
  // Spec §18: "Mobile prioritizes readability. Keep core visuals but simplify
  // chain detail, particles, shadows and camera behavior as required."
  physicsChains: false,
  postprocessing: false,
  shadows: false,
  blockchainBlocks: 48,
  merkleLeaves: 8,
}

const BUDGETS: Record<Tier, QualityBudget> = { high: HIGH, medium: MEDIUM, low: LOW }

/**
 * Detected once at mount, not per frame. Deliberately conservative: a wrong
 * "high" guess costs dropped frames, a wrong "medium" guess costs some
 * particles nobody counts.
 */
export function detectTier(): Tier {
  if (typeof window === "undefined") return "medium"

  const coarse = window.matchMedia("(pointer: coarse)").matches
  const narrow = window.innerWidth < 820
  if (coarse || narrow) return "low"

  const cores = navigator.hardwareConcurrency ?? 4
  // deviceMemory is Chromium-only; absent elsewhere, which is fine — it only
  // ever downgrades, never upgrades.
  const mem = (navigator as Navigator & { deviceMemory?: number }).deviceMemory
  if (cores <= 4 || (mem !== undefined && mem <= 4)) return "medium"
  if (window.innerWidth < 1280) return "medium"
  return "high"
}

export function budgetFor(tier: Tier): QualityBudget {
  return BUDGETS[tier]
}

/** Spec §18 / §5: honor prefers-reduced-motion. */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches
}
