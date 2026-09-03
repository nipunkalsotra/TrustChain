"use client"

/**
 * Immutable Logs world (spec §12.2, §12.3).
 *
 * "Audit events arrive and lock into a large permanent structure with heavy
 * mechanical/energy feedback. Movement should feel slow and irreversible...
 * the overall structure communicates permanence rather than playfulness."
 *
 * Each record flies in from the pipeline's direction (+Z, where the product
 * world is) and locks into a slot in a monumental stack. Arrival is eased with
 * a heavy curve and finishes with a seal flash — the "mechanical/energy
 * feedback" — after which the record never moves again.
 */

import { useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import { clamp01, easeHeavy } from "../config/motion"
import { frame } from "../state/landingStore"
import { worldEnter } from "./WorldSlot"
import { useMutable } from "./useMutable"

const ROWS = 9
const COLS = 3
const ROW_H = 1.75
const COL_W = 7.4

export function LedgerWorld() {
  const group = useRef<THREE.Group>(null)
  const records = useRef<THREE.InstancedMesh>(null)
  const seals = useRef<THREE.InstancedMesh>(null)
  const dummy = useMutable(() => new THREE.Object3D())
  const color = useMutable(() => new THREE.Color())
  const count = ROWS * COLS

  const slots = useMemo(
    () =>
      Array.from({ length: count }, (_, i) => {
        const r = Math.floor(i / COLS)
        const c = i % COLS
        return {
          x: (c - (COLS - 1) / 2) * COL_W,
          y: (r - (ROWS - 1) / 2) * ROW_H,
          // Deterministic per-slot arrival order, deliberately not strictly
          // sequential — records arrive as they are produced, not in rows.
          order: ((i * 7) % count) / count,
          fromZ: 60 + ((i * 13) % 17) * 3,
        }
      }),
    [count],
  )

  useFrame((state) => {
    const rec = records.current
    if (!rec || !group.current) return
    const time = state.clock.elapsedTime
    const enter = worldEnter("immutable")

    for (let i = 0; i < count; i++) {
      const s = slots[i]
      // Each record has its own window inside the section's entrance.
      const p = clamp01((enter - s.order * 0.55) / 0.4)
      // Heavy ease: slow start, decisive lock. Never overshoots — this
      // structure is not springy (spec §12.3: "slow and irreversible").
      const e = easeHeavy(p)

      dummy.position.set(
        s.x + (1 - e) * (s.x * 0.4),
        s.y + (1 - e) * 5.5,
        THREE.MathUtils.lerp(s.fromZ, 0, e),
      )
      dummy.rotation.set((1 - e) * 0.5, (1 - e) * 0.8, 0)
      dummy.scale.set(6.6, 1.25, 0.55)
      dummy.updateMatrix()
      rec.setMatrixAt(i, dummy.matrix)

      // Locked records go cold and dark; the one currently sealing is hot.
      const sealing = Math.sin(Math.PI * clamp01(p * 1.05))
      color.setRGB(0.05 + sealing * 0.45, 0.10 + sealing * 0.55, 0.18 + sealing * 0.8)
      rec.setColorAt(i, color)

      // Seal flash: a thin bright plate that appears only at the moment of
      // lock and immediately fades — the mechanical feedback.
      if (seals.current) {
        const flash = Math.pow(clamp01(1 - Math.abs(p - 0.92) / 0.08), 2)
        dummy.position.set(s.x, s.y, 0.45)
        dummy.rotation.set(0, 0, 0)
        dummy.scale.set(6.9 * flash, 1.5 * flash, 0.01)
        dummy.updateMatrix()
        seals.current.setMatrixAt(i, dummy.matrix)
      }
    }
    rec.instanceMatrix.needsUpdate = true
    if (rec.instanceColor) rec.instanceColor.needsUpdate = true
    if (seals.current) seals.current.instanceMatrix.needsUpdate = true

    // Structure itself is essentially still — permanence, not playfulness.
    // Only the cursor gives it a few degrees of parallax.
    group.current.rotation.y = frame.pointerX * 0.06 + Math.sin(time * 0.08) * 0.01
    group.current.rotation.x = -frame.pointerY * 0.03
  })

  return (
    <group ref={group}>
      {/* Monumental housing */}
      <mesh position={[0, 0, -1.6]}>
        <boxGeometry args={[COL_W * COLS + 3, ROWS * ROW_H + 3, 1.4]} />
        <meshStandardMaterial color="#070b14" roughness={0.6} metalness={0.5} />
      </mesh>
      <lineSegments position={[0, 0, -1.6]}>
        <edgesGeometry args={[new THREE.BoxGeometry(COL_W * COLS + 3, ROWS * ROW_H + 3, 1.4)]} />
        <lineBasicMaterial color="#2a6ea8" transparent opacity={0.45} />
      </lineSegments>

      <instancedMesh ref={records} args={[undefined, undefined, count]} frustumCulled={false}>
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial roughness={0.4} metalness={0.6} toneMapped />
      </instancedMesh>

      <instancedMesh ref={seals} args={[undefined, undefined, count]} frustumCulled={false}>
        <planeGeometry args={[1, 1]} />
        <meshBasicMaterial color="#bfe6ff" transparent opacity={0.85} toneMapped={false} />
      </instancedMesh>

      <pointLight color="#3ec6ff" intensity={40} distance={70} decay={2} position={[0, 0, 14]} />
    </group>
  )
}
