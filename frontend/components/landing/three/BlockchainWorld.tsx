"use client"

/**
 * On-chain blockchain universe (spec §12.7) — the major spectacle.
 *
 * "The blockchain world should feel like zero-gravity deep space: huge block
 * objects floating at multiple depths, some close to camera and some extremely
 * distant. Avoid flat 'block -> block -> block' diagrams."
 *
 * Two populations, per the spec's own performance direction:
 *  - a handful of NEAR blocks: real detailed structures with circuitry, an
 *    internal light and slow independent rotation;
 *  - everything else: one InstancedMesh scattered over a huge volume with
 *    distance-based scale, giving the "vast universe" without the draw calls.
 *
 * The anchoring event is a real propagating pulse: the Merkle root enters a
 * selected block, and a verification wave expands outward through the
 * surrounding blocks over the following moments, rather than every block
 * flashing at once.
 */

import { useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import { clamp01, smoothstep } from "../config/motion"
import { frame } from "../state/landingStore"
import { worldEnter, worldProgress } from "./WorldSlot"
import { SIMPLEX_3D } from "./shaders/noise"
import { useMutable, liveUniforms } from "./useMutable"

/** Where the Merkle root lands and anchoring begins. */
const ANCHOR_BLOCK = new THREE.Vector3(0, 0, -18)

function NearBlock({ index, position }: { index: number; position: [number, number, number] }) {
  const ref = useRef<THREE.Group>(null)
  const mat = useRef<THREE.ShaderMaterial>(null)
  const uniforms = useMutable(
    () => ({
      uTime: { value: 0 },
      uVerified: { value: 0 },
      uSeed: { value: index * 3.7 },
    }))
  const pos = useMutable(() => new THREE.Vector3(...position))

  useFrame((state) => {
    const g = ref.current
    if (!g) return
    const time = state.clock.elapsedTime
    const u = liveUniforms(mat, uniforms)
    u.uTime.value = time

    // Independent motion — no shared loop, so the field never pulses together.
    g.rotation.x = time * (0.03 + index * 0.004) + index
    g.rotation.y = time * (0.05 + index * 0.006)
    g.position.set(
      pos.x + Math.sin(time * 0.12 + index) * 1.6,
      pos.y + Math.cos(time * 0.09 + index * 1.3) * 1.9,
      pos.z + Math.sin(time * 0.07 + index * 0.7) * 1.2,
    )

    // Anchoring pulse propagates outward from the anchor block by distance —
    // "a network-wide verification/energy pulse that propagates through
    // connected blocks around the camera" (spec §12.7).
    const anchorP = clamp01((worldProgress("blockchain") - 0.42) / 0.3)
    const d = pos.distanceTo(ANCHOR_BLOCK)
    const front = anchorP * 260 - d
    u.uVerified.value = clamp01(front / 34)
  })

  return (
    <group ref={ref} position={position}>
      <mesh>
        <boxGeometry args={[7, 7, 7]} />
        <shaderMaterial
          ref={mat}
          uniforms={uniforms}
          vertexShader={/* glsl */ `
            varying vec3 vPos;
            varying vec3 vNormalW;
            varying vec3 vViewDir;
            void main(){
              vPos = position;
              vNormalW = normalize(normalMatrix * normal);
              vec4 mv = modelViewMatrix * vec4(position,1.0);
              vViewDir = normalize(-mv.xyz);
              gl_Position = projectionMatrix * mv;
            }
          `}
          fragmentShader={/* glsl */ `
            uniform float uTime;
            uniform float uVerified;
            uniform float uSeed;
            varying vec3 vPos;
            varying vec3 vNormalW;
            varying vec3 vViewDir;
            ${SIMPLEX_3D}
            void main(){
              vec3 col = vec3(0.018, 0.028, 0.05);

              // Circuitry etched into the faces.
              vec2 uv = vPos.xy * 0.6 + vPos.z * 0.15;
              vec2 cell = fract(uv * 2.2);
              float grid = max(
                (1.0 - smoothstep(0.0, 0.045,abs(cell.x-0.5))),
                (1.0 - smoothstep(0.0, 0.045,abs(cell.y-0.5)))
              );
              float mask = step(0.45, fbm(vec3(floor(uv*2.2), uSeed)) * 0.5 + 0.5);
              float circuit = grid * mask;

              vec3 cyan = vec3(0.24,0.72,1.0);
              col += cyan * circuit * (0.12 + uVerified * 0.9);

              // Internal light bleeding through the seams.
              float seam = smoothstep(3.1, 3.5, max(max(abs(vPos.x),abs(vPos.y)),abs(vPos.z)));
              col += cyan * seam * (0.15 + uVerified * 1.1);

              float fres = pow(1.0 - max(dot(vNormalW, vViewDir),0.0), 3.0);
              col += cyan * fres * (0.1 + uVerified * 0.7);

              // Verification sweep passing across the surface.
              float sweep = smoothstep(0.0,0.06,abs(fract(vPos.y*0.15 - uTime*0.3)-0.5));
              col += cyan * (1.0-sweep) * uVerified * 0.6;

              gl_FragColor = vec4(col,1.0);
              #include <tonemapping_fragment>
              #include <colorspace_fragment>
            }
          `}
        />
      </mesh>
      <lineSegments>
        <edgesGeometry args={[new THREE.BoxGeometry(7, 7, 7)]} />
        <lineBasicMaterial color="#3ec6ff" transparent opacity={0.4} />
      </lineSegments>
    </group>
  )
}

/** Vast distant field. Lightweight material, one draw call (spec §12.7). */
function DistantBlocks({ count }: { count: number }) {
  const ref = useRef<THREE.InstancedMesh>(null)
  const dummy = useMutable(() => new THREE.Object3D())
  const color = useMutable(() => new THREE.Color())
  const probe = useMutable(() => new THREE.Vector3())

  const field = useMemo(
    () =>
      Array.from({ length: count }, (_, i) => {
        // Golden-angle spiral in a thick shell, pushed far out in Z so the
        // field genuinely has depth rather than sitting on one plane.
        const a = i * 2.399963
        const r = 40 + Math.sqrt(i / count) * 260
        const depth = -40 - ((i * 31) % 97) * 4.2
        return {
          x: Math.cos(a) * r,
          y: Math.sin(a) * r * 0.65,
          z: depth,
          s: 1.6 + ((i * 19) % 13) * 0.85,
          p: (i % 41) * 0.153,
        }
      }),
    [count],
  )

  useFrame((state) => {
    const mesh = ref.current
    if (!mesh) return
    const time = state.clock.elapsedTime
    const enter = worldEnter("blockchain")
    const anchorP = clamp01((worldProgress("blockchain") - 0.42) / 0.3)

    for (let i = 0; i < count; i++) {
      const b = field[i]
      probe.set(b.x, b.y, b.z)
      dummy.position.set(
        b.x + Math.sin(time * 0.06 + b.p) * 2.2,
        b.y + Math.cos(time * 0.05 + b.p * 1.7) * 2.6,
        b.z,
      )
      dummy.rotation.set(b.p + time * 0.02, b.p * 2 + time * 0.03, 0)
      dummy.scale.setScalar(b.s * smoothstep(clamp01(enter * 1.4 - (i / count) * 0.3)))
      dummy.updateMatrix()
      mesh.setMatrixAt(i, dummy.matrix)

      const d = probe.distanceTo(ANCHOR_BLOCK)
      const verified = clamp01((anchorP * 300 - d) / 40)
      const base = 0.028
      color.setRGB(base + verified * 0.18, base + verified * 0.5, base * 2 + verified * 0.95)
      mesh.setColorAt(i, color)
    }
    mesh.instanceMatrix.needsUpdate = true
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
  })

  return (
    <instancedMesh ref={ref} args={[undefined, undefined, count]} frustumCulled={false}>
      <boxGeometry args={[1, 1, 1]} />
      <meshBasicMaterial toneMapped={false} />
    </instancedMesh>
  )
}

/** The Merkle root travelling in and entering its block. */
function AnchoringRoot() {
  const ref = useRef<THREE.Mesh>(null)
  const light = useRef<THREE.PointLight>(null)

  useFrame((state) => {
    const m = ref.current
    if (!m) return
    const time = state.clock.elapsedTime
    // Arrives from the Merkle world's direction (+Z) and enters the block.
    const p = smoothstep(clamp01((worldProgress("blockchain") - 0.15) / 0.28))
    m.position.set(
      ANCHOR_BLOCK.x,
      ANCHOR_BLOCK.y,
      THREE.MathUtils.lerp(ANCHOR_BLOCK.z + 150, ANCHOR_BLOCK.z, p),
    )
    // Consumed by the block on arrival — it does not linger in front of it.
    const s = (1 - smoothstep(clamp01((p - 0.85) / 0.15))) * 1.8
    m.scale.setScalar(0.001 + s)
    m.rotation.set(time * 0.6, time * 0.9, 0)
    if (light.current) {
      light.current.position.copy(m.position)
      light.current.intensity = s * 180
    }
  })

  return (
    <group>
      <mesh ref={ref}>
        <icosahedronGeometry args={[1, 1]} />
        <meshBasicMaterial color="#d8f0ff" toneMapped={false} />
      </mesh>
      <pointLight ref={light} color="#6fc8ff" distance={140} decay={2} intensity={0} />
    </group>
  )
}

export function BlockchainWorld({ blocks = 220 }: { blocks?: number }) {
  const group = useRef<THREE.Group>(null)

  const near = useMemo<[number, number, number][]>(
    () => [
      [0, 0, -18],
      [-22, 8, -34],
      [19, -10, -44],
      [-14, -14, -8],
      [26, 12, -12],
      [-32, -4, -58],
    ],
    [],
  )

  useFrame(() => {
    const g = group.current
    if (!g) return
    // Spec §12.7: "cursor adds subtle depth parallax."
    g.position.x = frame.pointerX * 2.2
    g.position.y = frame.pointerY * 1.6
  })

  return (
    <group ref={group}>
      <DistantBlocks count={blocks} />
      {near.map((p, i) => (
        <NearBlock key={i} index={i} position={p} />
      ))}
      <AnchoringRoot />
      {/* Deep-space ambience: just enough to keep the far field from going
          completely black without lighting the whole volume. */}
      <ambientLight intensity={0.12} color="#1a3a6a" />
    </group>
  )
}
