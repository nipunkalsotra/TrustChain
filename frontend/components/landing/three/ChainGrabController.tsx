"use client"

/**
 * Cursor chain-grabbing (spec §7.1).
 *
 * The spec's seven steps map onto this module as follows:
 *   1. "Raycast the pointer against the actual visible chain links" — done by
 *      R3F's own event system on each link mesh, which *is* a raycast against
 *      the real rendered geometry, not an invisible proxy.
 *   2/3. "create/move an invisible kinematic grab body ... attach the selected
 *      link using a stable spring/joint strategy" — see the note below.
 *   4. "Project pointer movement onto an intuitive drag plane."
 *   5. Neighbouring links react purely through the solver; nothing is
 *      hand-animated.
 *   6. Force, drag distance and velocity are all clamped.
 *   7. On release the link is simply let go and settles.
 *
 * On spring-vs-joint: the spec permits either. This uses a critically damped
 * spring impulse applied to the grabbed body each step rather than creating a
 * real joint at pointer-down. Creating and destroying joints mid-simulation
 * with the declarative hook API means remounting a component inside an active
 * physics world, which is exactly where the solver tends to produce the
 * "exploding simulation" the spec warns about. A clamped spring reaches the
 * same place — the cursor leads, the chain follows, the neighbours respond
 * through their real joints — with no topology change while stepping.
 */

import * as THREE from "three"
import { frame } from "../state/landingStore"

/** Maximum impulse magnitude per step. Hard ceiling on "exploding". */
export const GRAB_MAX_IMPULSE = 5.5
/** How far the grabbed link may be dragged from where it was picked up. */
export const GRAB_MAX_DISTANCE = 7.5
/** Linear velocity ceiling applied to a grabbed link. */
export const GRAB_MAX_VELOCITY = 26

export const grab = {
  /** Globally unique id of the link currently held, or null. */
  activeId: null as string | null,
  /** World-space target the held link is being pulled toward. */
  target: new THREE.Vector3(),
  /** Where the link was when it was grabbed — the clamp origin. */
  origin: new THREE.Vector3(),
  /** The plane pointer motion is projected onto (spec §7.1 step 4). */
  plane: new THREE.Plane(),
}

const _ray = new THREE.Ray()
const _hit = new THREE.Vector3()

/**
 * Begin a grab. The drag plane is built through the hit point and facing the
 * camera, which is the intuitive choice: the link tracks the cursor in the
 * plane the visitor is already looking at, rather than sliding away in depth.
 */
export function beginGrab(id: string, hitPoint: THREE.Vector3, camera: THREE.Camera) {
  grab.activeId = id
  grab.origin.copy(hitPoint)
  grab.target.copy(hitPoint)

  const normal = new THREE.Vector3()
  camera.getWorldDirection(normal)
  grab.plane.setFromNormalAndCoplanarPoint(normal.negate(), hitPoint)

  frame.chainGrabbed = true
}

export function endGrab() {
  grab.activeId = null
  frame.chainGrabbed = false
  // Tension decays rather than snapping to zero, so the monolith's restoring
  // force finishes its return instead of stopping dead (spec §6.3).
}

/** Project the current pointer onto the drag plane. Called each frame. */
export function updateGrabTarget(camera: THREE.Camera, pointer: THREE.Vector2) {
  if (!grab.activeId) return

  _ray.origin.setFromMatrixPosition(camera.matrixWorld)
  _ray.direction
    .set(pointer.x, pointer.y, 0.5)
    .unproject(camera)
    .sub(_ray.origin)
    .normalize()

  if (!_ray.intersectPlane(grab.plane, _hit)) return

  // Clamp drag distance (spec §7.1 step 6) — the visitor can pull the chain
  // taut, not tear it off into the sky.
  const delta = _hit.clone().sub(grab.origin)
  if (delta.length() > GRAB_MAX_DISTANCE) delta.setLength(GRAB_MAX_DISTANCE)
  grab.target.copy(grab.origin).add(delta)

  // Published tension drives every downstream reaction in the spec's chain:
  // anchor rock reacts -> monolith tilts slightly -> core pulses ->
  // dust/debris reacts -> nearby rocks respond -> system settles.
  frame.chainTension = Math.min(1, delta.length() / GRAB_MAX_DISTANCE)
}

/** Decay tension when nothing is held, so the world settles naturally. */
export function decayTension(dt: number) {
  if (grab.activeId) return
  frame.chainTension = Math.max(0, frame.chainTension - dt * 1.4)
}
