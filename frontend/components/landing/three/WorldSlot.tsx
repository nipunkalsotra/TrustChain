"use client"

/**
 * Places one section's 3D world, and is the single place the cinematic/normal
 * difference is expressed for everything below the hero.
 *
 * CINEMATIC (spec §12): the world sits at its own coordinates far down the
 * -Z path. The camera genuinely travels to it, so consecutive sections read as
 * one continuous world rather than a stack of rectangles.
 *
 * NORMAL (spec §13): every world renders at the same STAGE anchor in front of
 * the parked camera, and slides vertically in sync with its own section's
 * scroll — entering from below, leaving upward. That gives the "contained,
 * premium visualization with entrance animations" the spec asks for, with
 * literally zero camera movement, and it means two adjacent worlds are never
 * on stage at once so no cross-fade is required.
 *
 * Distance culling is done here rather than relying on frustum culling because
 * the blockchain world in particular is enormous and mostly instanced — not
 * submitting it at all while the camera is 600 units away is far cheaper than
 * letting three.js cull its instances.
 */

import { useRef, type ReactNode } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import { frame, getUi } from "../state/landingStore"
import { WORLDS, STAGE_ANCHOR } from "../config/worlds"
import type { SectionId } from "../config/content"
import { clamp01 } from "../config/motion"
import { useMutable } from "./useMutable"

/** How far a normal-mode world travels across its section, in world units. */
const STAGE_SLIDE = 30

export type WorldId = Exclude<SectionId, "hero">

/** Entrance progress for a world: 0 before it composes, 1 once fully arrived. */
export function worldEnter(id: WorldId): number {
  const p = frame.sectionProgress[id] ?? 0
  // Composed across the middle of the section, so the entrance has finished
  // by the time the copy beside it is comfortably readable.
  return clamp01((p - 0.18) / 0.30)
}

/** How far *through* a world the visitor has scrolled, 0..1. */
export function worldProgress(id: WorldId): number {
  return frame.sectionProgress[id] ?? 0
}

export function WorldSlot({ id, children }: { id: WorldId; children: ReactNode }) {
  const group = useRef<THREE.Group>(null)
  const placement = WORLDS[id]
  const cinematicPos = useMutable(() =>
    new THREE.Vector3(...placement.position).setX(placement.position[0] + placement.offsetX),
  )

  useFrame((state) => {
    const g = group.current
    if (!g) return

    const ui = getUi()
    const prog = frame.sectionProgress[id] ?? 0

    if (ui.mode === "cinematic" && ui.introAwakened) {
      g.position.copy(cinematicPos)
      // Cheap distance cull against this world's own declared radius.
      const d = state.camera.position.distanceTo(cinematicPos)
      g.visible = d < placement.cullRadius
    } else if (ui.mode === "normal") {
      g.position.set(
        STAGE_ANCHOR[0] + placement.offsetX,
        STAGE_ANCHOR[1] + (prog - 0.5) * STAGE_SLIDE,
        STAGE_ANCHOR[2],
      )
      // Only on stage while its own section is actually crossing the viewport.
      g.visible = prog > 0.015 && prog < 0.985
    } else {
      // Cinematic mode, opening not yet resolved: nothing below the hero is
      // reachable yet, so don't pay for any of it.
      g.visible = false
    }
  })

  return <group ref={group}>{children}</group>
}
