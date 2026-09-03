"use client"

/**
 * The single camera rig for the whole page.
 *
 * Spec §6.1.1: "Do not constantly swing the camera. Use a few deliberate shots
 * connected by smooth spline/orbit transitions so it feels like cinema rather
 * than a game camera... camera interpolation must remain damped, deterministic
 * and comfortable."
 *
 * Three regimes, one rig:
 *  1. OPENING — interpolate the five approved shots by intro progress. Shot
 *     offsets are expressed in the character's frame, so shots 1-3 genuinely
 *     follow him and shots 4-5 settle into world-anchored compositions once he
 *     has stopped.
 *  2. CINEMATIC BROWSING — travel between the section worlds, with real
 *     forward motion *through* each one driven by that section's scroll.
 *  3. NORMAL MODE — parked. Spec §13: "Camera remains essentially fixed;
 *     cursor only creates light parallax." It does not move between sections
 *     at all; the worlds arrive at the stage instead.
 *
 * Damping is applied to position and look target every frame rather than
 * assigning them directly, which is what keeps a flung trackpad from mapping
 * one-to-one onto camera motion (spec §15).
 */

import { useRef } from "react"
import { useFrame, useThree } from "@react-three/fiber"
import * as THREE from "three"
import {
  HERO_SHOTS,
  CAMERA_DAMP,
  clamp01,
  damp,
  lerp,
  smoothstep,
} from "../config/motion"
import { frame, getUi } from "../state/landingStore"
import { characterState } from "./Character"
import { WORLDS, STAGE_CAM, STAGE_LOOK, STAGE_FOV } from "../config/worlds"
import type { SectionId } from "../config/content"
import { useMutable } from "./useMutable"

export function CinematicCamera({ reducedMotion }: { reducedMotion: boolean }) {
  const { camera } = useThree()

  const desiredPos = useMutable(() => new THREE.Vector3())
  const desiredLook = useMutable(() => new THREE.Vector3())
  const currentLook = useMutable(() => new THREE.Vector3(0, 2, -6))
  const shake = useMutable(() => new THREE.Vector3())
  const tmpA = useMutable(() => new THREE.Vector3())
  const tmpB = useMutable(() => new THREE.Vector3())
  const desiredFov = useRef(HERO_SHOTS[0].fov)
  const shakeClock = useRef(0)

  useFrame((state, rawDt) => {
    if (!frame.visible) return
    const dt = Math.min(rawDt, 1 / 20)
    const ui = getUi()
    const perspective = camera as THREE.PerspectiveCamera

    const inOpening = !ui.introAwakened
    const world = frame.activeWorld

    if (inOpening || world === "hero") {
      // ── Regime 1/1b: the hero composition ──────────────────────────────
      const t = frame.introProgress
      // Find the shot pair bracketing t and blend with a smoothstep so shot
      // changes are eased transitions rather than linear ramps.
      let i = 0
      while (i < HERO_SHOTS.length - 2 && HERO_SHOTS[i + 1].at < t) i++
      const a = HERO_SHOTS[i]
      const b = HERO_SHOTS[Math.min(i + 1, HERO_SHOTS.length - 1)]
      const span = Math.max(b.at - a.at, 1e-4)
      const k = smoothstep(clamp01((t - a.at) / span))

      // Shots 1-3 are expressed relative to the man, so they follow him. By
      // shot 4 he has stopped, so the same maths yields a static composition.
      const anchor = characterState.position

      tmpA.set(...a.position).add(anchor)
      tmpB.set(...b.position).add(anchor)
      desiredPos.lerpVectors(tmpA, tmpB, k)

      tmpA.set(...a.target).add(anchor)
      tmpB.set(...b.target).add(anchor)
      desiredLook.lerpVectors(tmpA, tmpB, k)

      desiredFov.current = lerp(a.fov, b.fov, k)
    } else if (ui.mode === "normal") {
      // ── Regime 3: parked ───────────────────────────────────────────────
      desiredPos.set(...STAGE_CAM)
      desiredLook.set(...STAGE_LOOK)
      desiredFov.current = STAGE_FOV
    } else {
      // ── Regime 2: cinematic travel through the section worlds ──────────
      const placement = WORLDS[world as Exclude<SectionId, "hero">]
      if (placement) {
        const origin = tmpA.set(...placement.position)
        const prog = frame.sectionProgress[world] ?? 0.5

        desiredPos
          .set(...placement.camOffset)
          .add(origin)
          // Scroll drives forward travel *into* the world (spec §12.7). The
          // -0.5 centring means the section's midpoint is the composed shot,
          // with approach before it and departure after.
          .addScaledVector(
            tmpB.set(0, 0, -1),
            (prog - 0.5) * placement.travel,
          )

        desiredLook.set(...placement.lookOffset).add(origin)
        desiredFov.current = placement.fov
      }
    }

    // ── Cursor parallax ─────────────────────────────────────────────────
    // Spec §7.2: "Use subtle camera parallax in the hero only; keep maximum
    // movement restrained." Hard-limited to under a unit, and suppressed
    // entirely outside the hero.
    if ((inOpening || world === "hero") && !reducedMotion) {
      // Fades in as the opening resolves — a parallax-shifting camera during
      // the directed shots would fight the choreography.
      const authority = ui.introAwakened ? 1 : smoothstep(clamp01((frame.introProgress - 0.9) / 0.1))
      desiredPos.x += frame.pointerX * 0.85 * authority
      desiredPos.y += frame.pointerY * 0.45 * authority
      desiredLook.x += frame.pointerX * 0.35 * authority
      desiredLook.y += frame.pointerY * 0.2 * authority
    }

    // ── Physically motivated impulse ────────────────────────────────────
    // Spec §6.2: "Camera shake should be physically motivated and
    // low-frequency. Never use constant random shake." This only ever runs
    // when something in the world actually delivered an impulse (the punch,
    // a chain lock, the energy-wave front), and it decays like a real
    // damped oscillation rather than jittering per-frame.
    if (frame.cameraImpulse > 0.0005 && !reducedMotion) {
      shakeClock.current += dt
      const decay = Math.exp(-shakeClock.current * 4.2)
      const amp = frame.cameraImpulse * decay
      shake.set(
        Math.sin(shakeClock.current * 34) * amp * 0.30,
        Math.sin(shakeClock.current * 27 + 1.7) * amp * 0.22,
        Math.sin(shakeClock.current * 19 + 0.6) * amp * 0.12,
      )
      frame.cameraImpulse *= Math.exp(-dt * 3.4)
      if (frame.cameraImpulse < 0.0005) {
        frame.cameraImpulse = 0
        shakeClock.current = 0
        shake.set(0, 0, 0)
      }
    } else {
      shakeClock.current = 0
      shake.set(0, 0, 0)
    }

    // ── Damped commit ───────────────────────────────────────────────────
    // A single lambda for both position and look keeps the framing coherent;
    // damping them at different rates makes the subject appear to slide
    // around inside the frame.
    const lambda = reducedMotion ? 12 : CAMERA_DAMP
    camera.position.x = damp(camera.position.x, desiredPos.x, lambda, dt) + shake.x
    camera.position.y = damp(camera.position.y, desiredPos.y, lambda, dt) + shake.y
    camera.position.z = damp(camera.position.z, desiredPos.z, lambda, dt) + shake.z

    currentLook.x = damp(currentLook.x, desiredLook.x, lambda, dt)
    currentLook.y = damp(currentLook.y, desiredLook.y, lambda, dt)
    currentLook.z = damp(currentLook.z, desiredLook.z, lambda, dt)
    camera.lookAt(currentLook)

    // ── Portrait compensation ───────────────────────────────────────────
    // three's `fov` is VERTICAL, so a tall phone viewport shows dramatically
    // less of the scene horizontally than the 16:9 the shots were composed
    // for — the monolith fell outside the frame and the hero read as an empty
    // dark strip. Widen the vertical fov so the HORIZONTAL field stays the
    // composed one. Clamped, because at extreme aspect ratios the exact
    // solution produces a fisheye.
    const aspect = perspective.aspect || 16 / 9
    const REFERENCE_ASPECT = 16 / 9
    let fov = desiredFov.current
    if (aspect < REFERENCE_ASPECT) {
      const half = (fov * Math.PI) / 360
      const widened =
        (2 * Math.atan((Math.tan(half) * REFERENCE_ASPECT) / aspect) * 180) / Math.PI
      fov = Math.min(widened, fov * 1.75, 92)
    }

    if (Math.abs(perspective.fov - fov) > 0.01) {
      perspective.fov = damp(perspective.fov, fov, lambda, dt)
      perspective.updateProjectionMatrix()
    }

    void state
  })

  return null
}
