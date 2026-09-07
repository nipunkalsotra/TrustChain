"use client"

/**
 * Security world (spec §12.11, §13.7).
 *
 * "Transition into a controlled, dark technical environment rather than
 * another explosion. Reintroduce the core/monolith or a simplified central
 * trust object. As the user scrolls, multiple protection/isolation layers
 * materialize around it like an exploded engineering diagram. Each layer can
 * have a distinct material/energy behavior and subtle cursor
 * separation/reveal."
 *
 * CONTENT LOCK (spec §12.11): "Map the four approved Security concepts below
 * to the four protection/isolation layers; do not invent replacement
 * concepts." The layers below are therefore generated *from*
 * SECURITY_LAYERS — there is no separate hardcoded list that could drift out
 * of sync with the copy.
 */

import { useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import { SECURITY_LAYERS } from "../config/content"
import { clamp01, smoothstep } from "../config/motion"
import { frame } from "../state/landingStore"
import { worldEnter } from "./WorldSlot"

/** Each layer gets a distinct material behaviour, per the spec. */
const LAYER_STYLE = [
  { radius: 4.4, geom: "octa", opacity: 0.62, spin: 0.10, tilt: 0.0 },
  { radius: 7.4, geom: "ico", opacity: 0.42, spin: -0.07, tilt: 0.22 },
  { radius: 10.6, geom: "box", opacity: 0.26, spin: 0.05, tilt: -0.18 },
  { radius: 14.0, geom: "sphere", opacity: 0.14, spin: -0.035, tilt: 0.12 },
] as const

function Layer({
  index,
  hovered,
}: {
  index: number
  /**
   * Passed as a ref, not a number, on purpose: which layer the cursor is over
   * changes at pointer rate. Reading it inside this component's own frame loop
   * keeps the highlight instant while costing zero re-renders (spec §15).
   */
  hovered: React.RefObject<number>
}) {
  const ref = useRef<THREE.Mesh>(null)
  const style = LAYER_STYLE[index % LAYER_STYLE.length]

  useFrame((state) => {
    const m = ref.current
    if (!m) return
    const time = state.clock.elapsedTime
    const enter = worldEnter("security")
    const isHovered = hovered.current === index

    // Layers materialize outward, one after another — an exploded diagram
    // assembling, not four shells fading in together.
    const p = smoothstep(clamp01((enter - index * 0.11) / 0.32))

    // Cursor separation: the whole stack spreads slightly as the cursor moves
    // away from centre, which is what makes the isolation layers legible as
    // *separate* layers (spec §12.11).
    const spread = 1 + Math.hypot(frame.pointerX, frame.pointerY) * 0.12 * index
    const focus = isHovered ? 1.06 : 1

    m.scale.setScalar((0.001 + p) * spread * focus)
    // Each shell sits on its own axis. Perfectly concentric layers overlap
    // into a single silhouette; a few degrees of tilt each is what lets the
    // eye count them.
    m.rotation.set(
      time * style.spin * 0.6 + index + style.tilt,
      time * style.spin,
      time * style.spin * 0.3 + style.tilt,
    )

    const mat = m.material as THREE.MeshBasicMaterial
    mat.opacity = p * style.opacity * (isHovered ? 2.0 : 1)
  })

  const geometry = useMemo(() => {
    const r = style.radius
    switch (style.geom) {
      case "octa":
        return new THREE.OctahedronGeometry(r, 0)
      case "ico":
        return new THREE.IcosahedronGeometry(r, 0)
      case "box":
        return new THREE.BoxGeometry(r * 1.5, r * 1.5, r * 1.5)
      default:
        // Sparse on purpose: a dense wireframe sphere reads as a solid ball
        // of lines and swallows every layer inside it, which is the opposite
        // of the exploded engineering diagram spec §12.11 asks for.
        return new THREE.SphereGeometry(r, 12, 8)
    }
  }, [style])

  return (
    <mesh ref={ref} geometry={geometry}>
      <meshBasicMaterial
        color={index === 0 ? "#7fd8ff" : "#2f8fd8"}
        transparent
        opacity={0}
        wireframe
        toneMapped={false}
        side={THREE.DoubleSide}
      />
    </mesh>
  )
}

export function SecurityWorld() {
  const group = useRef<THREE.Group>(null)
  const core = useRef<THREE.Mesh>(null)
  const hovered = useRef(-1)

  useFrame((state) => {
    const g = group.current
    if (!g) return
    const time = state.clock.elapsedTime

    // Which layer is the cursor "over"? Measured as radial distance from the
    // centre in the world's own plane, mapped onto the layer radii.
    const r = Math.hypot(frame.pointerX, frame.pointerY) * 14
    let best = -1
    let bestD = 2.2
    LAYER_STYLE.forEach((s, i) => {
      const d = Math.abs(s.radius - r)
      if (d < bestD) {
        bestD = d
        best = i
      }
    })
    hovered.current = best

    if (core.current) {
      const enter = worldEnter("security")
      const lit = smoothstep(clamp01(enter / 0.3))
      // The central trust object: the monolith core, simplified. Steady and
      // controlled — this section is calm and technical, not another
      // spectacle (spec §12.11 / §13.7).
      core.current.scale.setScalar((1.9 + Math.sin(time * 0.8) * 0.05) * lit)
      core.current.rotation.set(time * 0.1, time * 0.16, 0)
    }

    g.rotation.y = frame.pointerX * 0.16
    g.rotation.x = -frame.pointerY * 0.09
  })

  return (
    <group ref={group}>
      {/* Central trust object */}
      <mesh ref={core}>
        <icosahedronGeometry args={[1, 1]} />
        <meshBasicMaterial color="#bfe6ff" toneMapped={false} />
      </mesh>
      <pointLight color="#5ac8ff" intensity={70} distance={80} decay={2} />

      {/* One layer per approved Security concept — count comes from the
          content config, so adding a fifth concept adds a fifth layer. */}
      {SECURITY_LAYERS.map((layer, i) => (
        <Layer key={layer.id} index={i} hovered={hovered} />
      ))}
    </group>
  )
}
