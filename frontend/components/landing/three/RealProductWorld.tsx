"use client"

/**
 * The cinematic -> real product pivot (spec §12.10).
 *
 * "A verifier/proof object approaches the camera and morphs into a clean
 * product interface panel. This is the credibility pivot from abstract
 * cinematic metaphor to real software. The product UI should still animate:
 * rows/statuses/proof paths can resolve sequentially, but the camera should
 * calm down."
 *
 * The panel is drawn in 3D as flat, crisp geometry rather than DOM-over-canvas
 * so it can genuinely morph out of the incoming proof object. Its labels come
 * from PRODUCT_PANEL in the content config — spec §12.10 restricts this to
 * "minimal realistic UI labels/data needed to demonstrate the interface".
 */

import { useRef } from "react"
import { useFrame } from "@react-three/fiber"
import { Text } from "@react-three/drei"
import * as THREE from "three"
import { clamp01, smoothstep } from "../config/motion"
import { PRODUCT_PANEL } from "../config/content"
import { frame } from "../state/landingStore"
import { worldEnter } from "./WorldSlot"

const W = 24
const H = 13
/** Left edge of the panel's content grid, in panel-local units. */
const ROW_X = -W / 2 + 1.4
/** Column x positions inside a row, relative to ROW_X. */
const COL_DETAIL = 8.4
const COL_STATUS = 15.2

export function RealProductWorld() {
  const group = useRef<THREE.Group>(null)
  const morphRef = useRef<THREE.Mesh>(null)
  const panelRef = useRef<THREE.Group>(null)
  const rowRefs = useRef<(THREE.Group | null)[]>([])

  const rows = PRODUCT_PANEL.rows

  useFrame((state) => {
    const g = group.current
    if (!g) return
    const time = state.clock.elapsedTime
    const enter = worldEnter("realproduct")

    // Phase 1: the proof object approaches. Phase 2: it flattens into the
    // panel. The two overlap, which is what makes it read as a morph rather
    // than a swap.
    const approach = smoothstep(clamp01(enter / 0.4))
    const morph = smoothstep(clamp01((enter - 0.3) / 0.35))

    if (morphRef.current) {
      const m = morphRef.current
      m.position.set(0, 0, THREE.MathUtils.lerp(60, 0, approach))
      // Scales up in X/Y and collapses in Z — literally flattening into a
      // panel rather than fading out and a panel fading in.
      // Ends at exactly the panel's dimensions — the box IS the panel by the
      // time the morph completes, so a mismatch reads as two objects rather
      // than one becoming the other.
      m.scale.set(
        THREE.MathUtils.lerp(2, W, morph),
        THREE.MathUtils.lerp(2, H, morph),
        THREE.MathUtils.lerp(2, 0.08, morph),
      )
      m.rotation.set((1 - morph) * time * 0.5, (1 - morph) * time * 0.7, 0)
      const mat = m.material as THREE.MeshBasicMaterial
      mat.opacity = (1 - morph) * 0.9
      mat.wireframe = morph < 0.5
    }

    if (panelRef.current) {
      panelRef.current.visible = morph > 0.35
      panelRef.current.scale.setScalar(0.9 + morph * 0.1)
    }

    // Rows resolve sequentially (spec §12.10 / §13.6). Deliberately NOT a
    // scale animation: scaling a group also scales its children's local
    // positions, so a half-resolved row has its columns bunched toward the
    // row origin instead of sitting in the panel's grid.
    rowRefs.current.forEach((r, i) => {
      if (!r) return
      const rowP = smoothstep(clamp01((enter - 0.5 - i * 0.07) / 0.22))
      r.visible = rowP > 0.01
      r.position.x = ROW_X - (1 - rowP) * 2.2
      r.children.forEach((child) => {
        const mat = (child as THREE.Mesh).material as THREE.Material | undefined
        if (mat && "opacity" in mat) {
          mat.transparent = true
          ;(mat as THREE.Material & { opacity: number }).opacity = rowP
        }
      })
      const status = r.children[2] as THREE.Mesh | undefined
      if (status) {
        const mat = status.material as THREE.MeshBasicMaterial
        // Status light settles from "pending" amber to "anchored" cyan.
        mat.color.setRGB(
          THREE.MathUtils.lerp(1.0, 0.28, rowP),
          THREE.MathUtils.lerp(0.62, 0.78, rowP),
          THREE.MathUtils.lerp(0.15, 1.0, rowP),
        )
      }
    })

    // Camera calms down: the panel barely moves, cursor gives it a card tilt
    // only (spec §13.6: "subtle card tilt").
    g.rotation.y = frame.pointerX * 0.09
    g.rotation.x = -frame.pointerY * 0.06
  })

  return (
    <group ref={group}>
      {/* The incoming proof object that becomes the panel. */}
      <mesh ref={morphRef}>
        <boxGeometry args={[1, 1, 1]} />
        <meshBasicMaterial color="#7fd0ff" transparent opacity={1} toneMapped={false} wireframe />
      </mesh>

      <group ref={panelRef} visible={false}>
        <mesh position={[0, 0, -0.1]}>
          <planeGeometry args={[W, H]} />
          <meshBasicMaterial color="#050a14" transparent opacity={0.94} />
        </mesh>
        <lineSegments position={[0, 0, 0]}>
          <edgesGeometry args={[new THREE.PlaneGeometry(W, H)]} />
          <lineBasicMaterial color="#2f7fd4" transparent opacity={0.75} />
        </lineSegments>

        <Text
          position={[ROW_X, H / 2 - 1.4, 0.05]}
          anchorX="left"
          fontSize={0.85}
          color="#e6f4ff"
        >
          {PRODUCT_PANEL.title}
        </Text>
        <Text
          position={[ROW_X, H / 2 - 2.5, 0.05]}
          anchorX="left"
          fontSize={0.52}
          color="#5f7fa5"
        >
          {PRODUCT_PANEL.subtitle}
        </Text>

        {rows.map((row, i) => (
          <group
            key={row.step}
            ref={(el) => {
              rowRefs.current[i] = el
            }}
            position={[ROW_X, H / 2 - 4.4 - i * 1.55, 0.05]}
          >
            <Text anchorX="left" fontSize={0.62} color="#cfe4f7">
              {row.step}
            </Text>
            <Text position={[COL_DETAIL, 0, 0]} anchorX="left" fontSize={0.5} color="#6f8fb5">
              {row.detail}
            </Text>
            <mesh position={[COL_STATUS, 0, 0]}>
              <circleGeometry args={[0.22, 16]} />
              <meshBasicMaterial color="#47c7ff" toneMapped={false} />
            </mesh>
            <Text position={[COL_STATUS + 0.55, 0, 0]} anchorX="left" fontSize={0.48} color="#47c7ff">
              {row.status}
            </Text>
          </group>
        ))}

        <Text
          position={[ROW_X, -H / 2 + 1.2, 0.05]}
          anchorX="left"
          fontSize={0.5}
          color="#5f7fa5"
        >
          {`${PRODUCT_PANEL.proof.label}  ${PRODUCT_PANEL.proof.root}  ·  ${PRODUCT_PANEL.proof.depth}`}
        </Text>
      </group>

      <pointLight color="#4fb8ff" intensity={40} distance={70} decay={2} position={[0, 0, 14]} />
    </group>
  )
}
