"use client"

/**
 * Product world (spec §12.1).
 *
 * "Represent the multi-agent flow as monumental floating structures/nodes, not
 * flat cards. Scroll moves the camera through the pipeline. Data/energy
 * visibly travels from one stage to the next."
 *
 * The four nodes are the four LangGraph stages, in order, spaced along -Z so
 * that in cinematic mode the camera flies *between* them. Energy packets are
 * instanced quads travelling the connections, with their positions computed
 * analytically from a clock — no per-packet CPU state.
 */

import { useRef } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import { PIPELINE_STAGES } from "../config/content"
import { frame } from "../state/landingStore"
import { clamp01, smoothstep } from "../config/motion"
import { worldEnter, worldProgress } from "./WorldSlot"
import { useMutable } from "./useMutable"

const NODE_GAP = 26
const PACKETS_PER_LINK = 5

function StageNode({ index, total }: { index: number; total: number }) {
  const group = useRef<THREE.Group>(null)
  const ringA = useRef<THREE.Mesh>(null)
  const ringB = useRef<THREE.Mesh>(null)
  const core = useRef<THREE.Mesh>(null)
  const z = -index * NODE_GAP

  useFrame((state) => {
    const g = group.current
    if (!g) return
    const time = state.clock.elapsedTime
    const enter = worldEnter("product")
    // Nodes rise in sequence as the section enters (spec §13.1: "Nodes
    // rise/resolve as the section enters the viewport").
    const stagger = clamp01((enter - index * 0.12) / 0.55)
    const e = smoothstep(stagger)

    g.position.set(0, -14 + e * 14 + Math.sin(time * 0.5 + index) * 0.5, z)
    g.scale.setScalar(0.001 + e * 1)
    g.rotation.y = (1 - e) * 1.2 + Math.sin(time * 0.18 + index) * 0.06

    if (ringA.current) ringA.current.rotation.z = time * 0.22 + index
    if (ringB.current) ringB.current.rotation.z = -time * 0.15 - index
    if (core.current) {
      const pulse = 0.5 + 0.5 * Math.sin(time * 1.4 + index * 1.7)
      const mat = core.current.material as THREE.MeshBasicMaterial
      mat.opacity = 0.55 + pulse * 0.35
      core.current.scale.setScalar(1 + pulse * 0.06)
    }
    void total
  })

  return (
    <group ref={group}>
      {/* Monumental slab, not a card. */}
      <mesh castShadow>
        <boxGeometry args={[9, 15, 1.6]} />
        <meshStandardMaterial
          color="#0a1020"
          roughness={0.35}
          metalness={0.7}
          emissive="#08243f"
          emissiveIntensity={0.6}
        />
      </mesh>
      {/* Edge frame so the silhouette reads at distance. */}
      <lineSegments>
        <edgesGeometry args={[new THREE.BoxGeometry(9, 15, 1.6)]} />
        <lineBasicMaterial color="#3ec6ff" transparent opacity={0.55} />
      </lineSegments>

      <mesh ref={core} position={[0, 0, 0.95]}>
        <circleGeometry args={[2.1, 40]} />
        <meshBasicMaterial color="#59c4ff" transparent opacity={0.7} toneMapped={false} />
      </mesh>
      <mesh ref={ringA} position={[0, 0, 1.0]}>
        <ringGeometry args={[2.6, 2.75, 48]} />
        <meshBasicMaterial color="#3ec6ff" transparent opacity={0.75} toneMapped={false} />
      </mesh>
      <mesh ref={ringB} position={[0, 0, 1.05]}>
        <ringGeometry args={[3.3, 3.38, 6]} />
        <meshBasicMaterial color="#2f7fd4" transparent opacity={0.5} toneMapped={false} />
      </mesh>
      <pointLight color="#4fb8ff" intensity={26} distance={40} decay={2} position={[0, 0, 4]} />
    </group>
  )
}

/** Energy travelling between consecutive stages (spec §12.1). */
function DataFlow({ links }: { links: number }) {
  const ref = useRef<THREE.InstancedMesh>(null)
  const dummy = useMutable(() => new THREE.Object3D())
  const count = links * PACKETS_PER_LINK

  useFrame((state) => {
    const mesh = ref.current
    if (!mesh) return
    const time = state.clock.elapsedTime
    const enter = worldEnter("product")
    // Flow rate rises with scroll: moving through the pipeline should feel
    // like the pipeline is running, not like a static diagram.
    const rate = 0.28 + worldProgress("product") * 0.5

    for (let i = 0; i < count; i++) {
      const link = Math.floor(i / PACKETS_PER_LINK)
      const slot = i % PACKETS_PER_LINK
      const phase = (time * rate + slot / PACKETS_PER_LINK + link * 0.13) % 1

      const z0 = -link * NODE_GAP - 0.9
      const z1 = -(link + 1) * NODE_GAP + 0.9
      const live = clamp01((enter - link * 0.12 - 0.25) / 0.4)

      dummy.position.set(
        Math.sin(phase * Math.PI * 2 + link) * 0.7,
        Math.sin(phase * Math.PI) * 1.2,
        THREE.MathUtils.lerp(z0, z1, phase),
      )
      // Packets stretch along travel and shrink at the ends — arrival and
      // departure, not teleporting dots.
      const s = Math.sin(phase * Math.PI) * 0.9 * live
      dummy.scale.set(s * 0.32, s * 0.32, s * 1.5)
      dummy.updateMatrix()
      mesh.setMatrixAt(i, dummy.matrix)
    }
    mesh.instanceMatrix.needsUpdate = true
  })

  return (
    <instancedMesh ref={ref} args={[undefined, undefined, count]} frustumCulled={false}>
      <sphereGeometry args={[1, 8, 6]} />
      <meshBasicMaterial color="#8ad8ff" toneMapped={false} transparent opacity={0.9} />
    </instancedMesh>
  )
}

/** The connecting conduits between stages. */
function Conduits({ links }: { links: number }) {
  return (
    <group>
      {Array.from({ length: links }, (_, i) => (
        <mesh key={i} position={[0, 0, -i * NODE_GAP - NODE_GAP / 2]} rotation={[Math.PI / 2, 0, 0]}>
          <cylinderGeometry args={[0.13, 0.13, NODE_GAP - 1.8, 8, 1, true]} />
          <meshBasicMaterial color="#1b4d7a" transparent opacity={0.55} toneMapped={false} />
        </mesh>
      ))}
    </group>
  )
}

export function ProductWorld() {
  const stages = PIPELINE_STAGES.length
  const group = useRef<THREE.Group>(null)

  useFrame(() => {
    // Spec §12.1: "Cursor adds subtle parallax and local energy response."
    const g = group.current
    if (!g) return
    g.rotation.y = frame.pointerX * 0.05
    g.rotation.x = -frame.pointerY * 0.03
  })

  return (
    <group ref={group}>
      {Array.from({ length: stages }, (_, i) => (
        <StageNode key={i} index={i} total={stages} />
      ))}
      <Conduits links={stages - 1} />
      <DataFlow links={stages - 1} />
    </group>
  )
}
