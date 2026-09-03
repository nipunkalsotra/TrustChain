/**
 * Where each section's 3D world lives, and how the camera reaches it.
 *
 * Spec §12: in CINEMATIC mode the sections "transition into one another as a
 * continuous 3D world" — so each world sits at its own place in world space
 * and the camera genuinely travels between them.
 *
 * Spec §13: in NORMAL mode the same sections become "contained, premium 3D/DOM
 * visualizations" with the camera "essentially fixed". Rather than flying the
 * camera 1500 units (which is precisely the giant camera travel normal mode is
 * supposed to remove), every world is re-parented to a single STAGE anchor
 * placed just in front of the parked camera, and only the active one renders.
 * The camera does not move at all; the worlds arrive.
 */

import type { SectionId } from "./content"

export interface WorldPlacement {
  id: SectionId
  /** World-space origin in cinematic mode. */
  position: [number, number, number]
  /** Camera offset from that origin when this world is the destination. */
  camOffset: [number, number, number]
  /** Look-at offset from the world origin. */
  lookOffset: [number, number, number]
  fov: number
  /** Radius at which the world starts rendering, for cheap distance culling. */
  cullRadius: number
  /**
   * Lateral offset so the world sits on the OPPOSITE side of the frame from
   * its section's copy column. Without it, every world renders dead-centre
   * and the approved section copy is read through a Merkle tree.
   *
   * Sign follows the section's `align` in the DOM: copy on the left pushes
   * the world right, and vice versa.
   */
  offsetX: number
  /**
   * How far the camera travels forward *through* this world as the visitor
   * scrolls its section, in cinematic mode. This is what makes §12.7's
   * "Scroll drives forward travel through this world" a real traversal
   * instead of a static shot of a moving scene.
   */
  travel: number
}

/**
 * The cinematic path runs away from the camera down -Z, which is what makes
 * §12.1's "camera approaches the monolith/core and travels through it" a real
 * traversal rather than a cut: the pipeline world is literally behind the
 * monolith from the hero's viewpoint.
 */
export const WORLDS: Record<Exclude<SectionId, "hero">, WorldPlacement> = {
  product: {
    id: "product",
    position: [0, 0, -240],
    camOffset: [0, 2, 34],
    lookOffset: [0, 1, -10],
    fov: 46,
    cullRadius: 150,
    travel: 46,
    offsetX: 13,
  },
  immutable: {
    id: "immutable",
    position: [0, -4, -470],
    camOffset: [6, 4, 40],
    lookOffset: [0, 1, -6],
    fov: 44,
    cullRadius: 160,
    travel: 34,
    offsetX: -14,
  },
  merkle: {
    id: "merkle",
    position: [0, -2, -700],
    camOffset: [0, 6, 46],
    lookOffset: [0, 6, 0],
    fov: 46,
    cullRadius: 170,
    travel: 30,
    offsetX: 15,
  },
  blockchain: {
    id: "blockchain",
    position: [0, 0, -1000],
    camOffset: [0, 0, 60],
    lookOffset: [0, 0, -40],
    fov: 58,
    cullRadius: 420,
    travel: 150,
    offsetX: -17,
  },
  verification: {
    id: "verification",
    position: [0, 0, -1330],
    camOffset: [0, 3, 62],
    lookOffset: [0, 0, 0],
    fov: 48,
    cullRadius: 200,
    travel: 30,
    offsetX: 18,
  },
  realproduct: {
    id: "realproduct",
    position: [0, 0, -1560],
    camOffset: [0, 0.5, 26],
    lookOffset: [0, 0, 0],
    fov: 38,
    cullRadius: 120,
    travel: 14,
    offsetX: -14,
  },
  security: {
    id: "security",
    position: [0, 0, -1780],
    camOffset: [0, 2, 40],
    lookOffset: [0, 0, 0],
    fov: 46,
    cullRadius: 150,
    travel: 26,
    offsetX: 13,
  },
}

/**
 * Normal-mode stage: a single anchor in front of the parked hero camera. Every
 * section world renders here, one at a time. Chosen to sit clear of the hero's
 * own geometry so the two never intersect during the cross-fade.
 */
export const STAGE_ANCHOR: [number, number, number] = [0, 6, -30]
export const STAGE_CAM: [number, number, number] = [0, 6.6, 14]
export const STAGE_LOOK: [number, number, number] = [0, 6, -30]
export const STAGE_FOV = 44

export const SECTION_WORLD_IDS = Object.keys(WORLDS) as Exclude<SectionId, "hero">[]
