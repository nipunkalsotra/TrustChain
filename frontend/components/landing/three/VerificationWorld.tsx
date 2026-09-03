"use client"

/**
 * Publicly Verifiable world (spec §12.8, §12.9, §13.5).
 *
 * "Change emotion: camera pulls far back and the blockchain universe becomes
 * smaller in frame. Independent verifier/observer nodes appear around the
 * proof/network. Proof paths illuminate one by one, communicating that
 * verification does not depend on trusting TrustChain itself."
 *
 * Deliberately calmer than the blockchain spectacle that precedes it: a
 * central proof object, independent nodes at a distance, and paths that
 * resolve one at a time rather than all lighting together. The verifiers are
 * *outside* the ring and reach inward — the composition itself says the check
 * comes from outside the system.
 */

import { useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import { clamp01, smoothstep } from "../config/motion"
import { frame } from "../state/landingStore"
import { worldEnter } from "./WorldSlot"
import { useMutable } from "./useMutable"

const VERIFIERS = 7
const RADIUS = 17

export function VerificationWorld() {
  const group = useRef<THREE.Group>(null)
  const proof = useRef<THREE.Mesh>(null)
  const nodesRef = useRef<THREE.InstancedMesh>(null)
  const pathsRef = useRef<THREE.LineSegments>(null)
  const dummy = useMutable(() => new THREE.Object3D())
  const color = useMutable(() => new THREE.Color())
  /** Which verifier the cursor is nearest — its whole path highlights. */
  const hovered = useRef(-1)

  const layout = useMemo(
    () =>
      Array.from({ length: VERIFIERS }, (_, i) => {
        const a = (i / VERIFIERS) * Math.PI * 2 + 0.4
        return {
          x: Math.cos(a) * RADIUS,
          y: Math.sin(a) * RADIUS * 0.62,
          z: Math.sin(a * 2.3) * 5,
          a,
        }
      }),
    [],
  )

  const paths = useMemo(() => {
    const g = new THREE.BufferGeometry()
    // Each verifier's path is a 3-segment polyline into the centre, so the
    // proof reads as a *path of hashes* rather than a straight spoke.
    const SEG = 3
    const pos = new Float32Array(VERIFIERS * SEG * 2 * 3)
    const col = new Float32Array(VERIFIERS * SEG * 2 * 3)
    layout.forEach((v, i) => {
      for (let s = 0; s < SEG; s++) {
        const t0 = s / SEG
        const t1 = (s + 1) / SEG
        const p0 = new THREE.Vector3(v.x * (1 - t0), v.y * (1 - t0), v.z * (1 - t0))
        const p1 = new THREE.Vector3(v.x * (1 - t1), v.y * (1 - t1), v.z * (1 - t1))
        // Kink each joint so the path is visibly stepwise.
        p0.z += Math.sin(t0 * 6 + i) * 1.4
        p1.z += Math.sin(t1 * 6 + i) * 1.4
        const o = (i * SEG + s) * 6
        pos.set([p0.x, p0.y, p0.z, p1.x, p1.y, p1.z], o)
      }
    })
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3))
    g.setAttribute("color", new THREE.BufferAttribute(col, 3))
    return { geometry: g, SEG }
  }, [layout])

  useFrame((state) => {
    const g = group.current
    if (!g) return
    const time = state.clock.elapsedTime
    const enter = worldEnter("verification")

    // Which verifier is closest to the cursor, in the world's own plane.
    // Spec §13.5: "Hovering a verifier highlights its complete verification
    // path."
    let best = -1
    let bestD = 0.55
    const px = frame.pointerX * RADIUS * 1.25
    const py = frame.pointerY * RADIUS * 0.85
    layout.forEach((v, i) => {
      const d = Math.hypot(v.x - px, v.y - py) / RADIUS
      if (d < bestD) {
        bestD = d
        best = i
      }
    })
    hovered.current = best

    if (nodesRef.current) {
      for (let i = 0; i < VERIFIERS; i++) {
        const v = layout[i]
        // One by one, not together.
        const lit = smoothstep(clamp01((enter - i * 0.09) / 0.3))
        const isHover = hovered.current === i
        dummy.position.set(
          v.x,
          v.y + Math.sin(time * 0.5 + i) * 0.5,
          v.z + Math.cos(time * 0.4 + i) * 0.4,
        )
        dummy.rotation.set(time * 0.15 + i, time * 0.2, 0)
        dummy.scale.setScalar((0.9 + (isHover ? 0.35 : 0)) * lit)
        dummy.updateMatrix()
        nodesRef.current.setMatrixAt(i, dummy.matrix)
        const h = lit * (isHover ? 1.5 : 1)
        color.setRGB(h * 0.3, h * 0.72, h * 1.1)
        nodesRef.current.setColorAt(i, color)
      }
      nodesRef.current.instanceMatrix.needsUpdate = true
      if (nodesRef.current.instanceColor) nodesRef.current.instanceColor.needsUpdate = true
    }

    if (pathsRef.current) {
      const colAttr = pathsRef.current.geometry.getAttribute("color") as THREE.BufferAttribute
      for (let i = 0; i < VERIFIERS; i++) {
        const lit = smoothstep(clamp01((enter - i * 0.09 - 0.06) / 0.28))
        const isHover = hovered.current === i
        for (let s = 0; s < paths.SEG; s++) {
          // Segments resolve outward-to-inward within each path, so a path
          // visibly *recomputes* toward the root.
          const segLit = lit * smoothstep(clamp01((enter - i * 0.09 - s * 0.05 - 0.06) / 0.24))
          const travel = 0.7 + 0.3 * Math.sin(time * 3 - s * 1.2 + i)
          const v = segLit * travel * (isHover ? 1.8 : 1)
          const o = (i * paths.SEG + s) * 2
          colAttr.setXYZ(o, v * 0.22, v * 0.62, v * 1.0)
          colAttr.setXYZ(o + 1, v * 0.3, v * 0.78, v * 1.15)
        }
      }
      colAttr.needsUpdate = true
    }

    if (proof.current) {
      const lit = smoothstep(clamp01(enter / 0.35))
      proof.current.scale.setScalar(0.001 + lit * (2.4 + Math.sin(time * 1.3) * 0.07))
      proof.current.rotation.set(time * 0.12, time * 0.2, 0)
    }

    // Calm, expansive language: almost no rotation of its own.
    g.rotation.y = frame.pointerX * 0.14
    g.rotation.x = -frame.pointerY * 0.07
  })

  return (
    <group ref={group}>
      <lineSegments ref={pathsRef} geometry={paths.geometry}>
        <lineBasicMaterial vertexColors transparent opacity={0.95} toneMapped={false} />
      </lineSegments>

      <instancedMesh ref={nodesRef} args={[undefined, undefined, VERIFIERS]} frustumCulled={false}>
        <octahedronGeometry args={[1.5, 0]} />
        <meshBasicMaterial toneMapped={false} />
      </instancedMesh>

      {/* The proof/root at the centre. */}
      <mesh ref={proof}>
        <icosahedronGeometry args={[1, 2]} />
        <meshBasicMaterial color="#9fdcff" toneMapped={false} wireframe />
      </mesh>
      <pointLight color="#5ac8ff" intensity={60} distance={90} decay={2} />
    </group>
  )
}
