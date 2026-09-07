"use client"

/**
 * Floating rocks (spec §6.5).
 *
 * "Rocks should not all float simultaneously. Each rock reacts when the
 * expanding energy sphere physically reaches its position. Reaction sequence:
 * surface glow/cracks -> vibration -> lift -> slow rotation/drift."
 *
 * Each rock therefore owns its own little state machine keyed off
 * `waveReached(itsPosition)` — nothing is globally sequenced, so the order the
 * rocks wake in is a genuine consequence of where they are relative to the
 * monolith, and stays correct if the layout changes.
 *
 * Hero rocks are individual meshes (they carry the silhouette and the chain
 * anchors); everything behind them is one InstancedMesh (spec §15: "Use
 * instancing, LOD and lightweight materials for the blockchain universe and
 * distant debris").
 */

import { useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import { clamp01, damp, smoothstep } from "../config/motion"
import { frame } from "../state/landingStore"
import { waveReached, waveFront } from "./SphericalEnergyWave"
import { MONOLITH_BASE } from "./Terrain"
import { SIMPLEX_3D } from "./shaders/noise"
import { useMutable, liveUniforms } from "./useMutable"

export interface RockDef {
  id: string
  /** Resting position (where it sits before the wave lifts it). */
  base: [number, number, number]
  scale: number
  /** Anchor rocks carry a chain and energize more strongly (spec §6.5/§6.6). */
  anchor: boolean
  /** How far it rises once energized. */
  lift: number
  seed: number
}

/**
 * Anchor rocks are placed to reproduce the approved hero reference: two high
 * on the left, one low-right, one far-right — the chains in that image run
 * from the monolith out to exactly these four relationships (spec §6.6:
 * "Recreate only the major chains visible in the approved hero reference, with
 * approximately the same directions, anchor-rock relationships").
 */
export const ANCHOR_ROCKS: RockDef[] = [
  { id: "a-upper-left",  base: [-15.0, 1.8, -6.0],  scale: 2.5, anchor: true, lift: 15.0, seed: 11 },
  { id: "a-left",        base: [-11.0, 1.2,  4.0],  scale: 2.0, anchor: true, lift: 8.2,  seed: 23 },
  { id: "a-right",       base: [ 17.0, 2.2, -9.0],  scale: 2.7, anchor: true, lift: 12.5, seed: 37 },
  { id: "a-lower-right", base: [ 12.5, 0.9,  5.5],  scale: 1.8, anchor: true, lift: 5.4,  seed: 51 },
]

const SILHOUETTE_ROCKS: RockDef[] = [
  { id: "s1", base: [-22.0, 1.4, -18.0], scale: 3.2, anchor: false, lift: 6.5,  seed: 5 },
  { id: "s2", base: [ 24.0, 0.9,  -3.0], scale: 2.6, anchor: false, lift: 4.2,  seed: 9 },
  { id: "s3", base: [ -7.5, 0.6,  11.0], scale: 1.3, anchor: false, lift: 2.4,  seed: 14 },
  { id: "s4", base: [  9.0, 2.0, -24.0], scale: 3.8, anchor: false, lift: 9.0,  seed: 19 },
  { id: "s5", base: [-28.0, 2.6, -30.0], scale: 4.4, anchor: false, lift: 11.5, seed: 26 },
]

/** Live world positions, published so the chains can attach to real rocks. */
export const rockPositions = new Map<string, THREE.Vector3>()

function makeRockGeometry(seed: number, detail = 1) {
  const g = new THREE.IcosahedronGeometry(1, detail)
  const pos = g.attributes.position as THREE.BufferAttribute
  const v = new THREE.Vector3()
  // Deterministic per-seed displacement — no Math.random, so the silhouette
  // is identical on every load and between server and client.
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i)
    const n =
      Math.sin(v.x * 3.1 + seed) * 0.5 +
      Math.sin(v.y * 4.3 + seed * 1.7) * 0.32 +
      Math.sin(v.z * 5.7 + seed * 2.3) * 0.22
    v.multiplyScalar(1 + n * 0.24)
    pos.setXYZ(i, v.x, v.y * 0.86, v.z)
  }
  g.computeVertexNormals()
  return g
}

// ── Shared rock material ─────────────────────────────────────────────────────
// One material, per-mesh uniforms via a small factory, so all rocks compile the
// same program.
function useRockUniforms() {
  return useMutable(
    () => ({
      uTime: { value: 0 },
      /** 0..1 how energized this rock is — drives glow + crack emission. */
      uCharge: { value: 0 },
      /** Spike while the wave front is physically passing it. */
      uFront: { value: 0 },
      uAnchor: { value: 0 },
    }))
}

const ROCK_VERT = /* glsl */ `
  varying vec3 vPos;
  varying vec3 vNormalW;
  varying vec3 vViewDir;
  void main() {
    vPos = position;
    vNormalW = normalize(normalMatrix * normal);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vViewDir = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }
`

const ROCK_FRAG = /* glsl */ `
  uniform float uTime;
  uniform float uCharge;
  uniform float uFront;
  uniform float uAnchor;
  varying vec3  vPos;
  varying vec3  vNormalW;
  varying vec3  vViewDir;
  ${SIMPLEX_3D}

  void main() {
    float grain = fbm(vPos * 3.0) * 0.5 + 0.5;
    vec3 col = vec3(0.021, 0.025, 0.034) * (0.5 + grain);

    // Simple key light so the rock has form even before it is energized.
    float key = max(dot(normalize(vNormalW), normalize(vec3(-0.3, 0.8, 0.5))), 0.0);
    col *= 0.55 + key * 1.15;

    // Cool rim so the silhouette separates from the sky without lifting the
    // whole surface — the rocks read as dark mass catching moonlight, not as
    // grey props.
    // Tight exponent on purpose: on a faceted low-poly rock a soft rim term
    // catches nearly every face and lifts the whole silhouette to grey. This
    // only picks out the true edge.
    float rimLight = pow(1.0 - max(dot(vNormalW, vViewDir), 0.0), 5.0);
    col += vec3(0.07, 0.13, 0.24) * rimLight * 0.30;

    // Surface cracks — the FIRST stage of the reaction sequence, visible
    // before the rock has moved at all (spec §6.5).
    float crack = smoothstep(0.72, 0.95, fbm(vPos * 5.5) * 0.5 + 0.5);
    vec3 energy = mix(vec3(0.2, 0.55, 1.0), vec3(0.6, 0.85, 1.0), uAnchor);
    col += energy * crack * uCharge * (0.45 + uAnchor * 0.5);
    // The front passing over it flashes the whole surface briefly.
    col += energy * uFront * (0.30 + crack * 0.7);

    float fres = pow(1.0 - max(dot(vNormalW, vViewDir), 0.0), 3.0);
    col += energy * fres * uCharge * 0.5;

    gl_FragColor = vec4(col, 1.0);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`

function Rock({ def }: { def: RockDef }) {
  const mesh = useRef<THREE.Mesh>(null)
  const mat = useRef<THREE.ShaderMaterial>(null)
  const uniforms = useRockUniforms()
  const geom = useMemo(() => makeRockGeometry(def.seed, def.scale > 2.5 ? 2 : 1), [def])
  const world = useMemo(() => {
    const v = new THREE.Vector3(...def.base)
    rockPositions.set(def.id, v)
    return v
  }, [def])
  const state = useRef({ charge: 0, lift: 0, spin: 0, vibrate: 0 })

  useFrame((s, dt) => {
    if (!mesh.current || !frame.visible) return
    const time = s.clock.elapsedTime
    const st = state.current

    // ── Stage 1: has the wave physically arrived? ────────────────────────
    const reached = waveReached(world)
    const front = waveFront(world)
    // Anchor rocks "become more strongly energized" (spec §6.5).
    const target = reached * (def.anchor ? 1 : 0.72)
    st.charge = damp(st.charge, target, 3.4, dt)

    // ── Stage 2: vibration, before any lift ──────────────────────────────
    // Peaks as the front passes and dies out — the rock shakes loose, then
    // rises. Running vibration and lift on the same curve is what makes rocks
    // look like they simply teleport upward.
    st.vibrate = damp(st.vibrate, front, 9, dt)

    // ── Stage 3: lift, delayed behind the charge ─────────────────────────
    const liftTarget = smoothstep(clamp01((st.charge - 0.35) / 0.5)) * def.lift
    st.lift = damp(st.lift, liftTarget, 1.15, dt)

    // ── Stage 4: slow rotation/drift ─────────────────────────────────────
    // Spec §7.2: "Cursor near floating rocks -> glow and rotation respond
    // slightly." Screen-space proximity, clamped so it stays a nudge.
    const proximity = clamp01(1 - Math.hypot(frame.pointerX, frame.pointerY) / 1.6)
    st.spin += dt * (0.06 + st.charge * 0.1 + proximity * st.charge * 0.08)

    const bobPhase = time * (0.32 + def.seed * 0.011) + def.seed
    const drift = st.charge

    mesh.current.position.set(
      world.x + Math.sin(bobPhase * 0.7) * 0.28 * drift + (Math.sin(time * 47 + def.seed) * 0.06 * st.vibrate),
      world.y + st.lift + Math.sin(bobPhase) * 0.34 * drift + (Math.cos(time * 53 + def.seed) * 0.06 * st.vibrate),
      world.z + Math.cos(bobPhase * 0.55) * 0.24 * drift,
    )
    mesh.current.rotation.set(
      st.spin * 0.5 + def.seed,
      st.spin,
      st.spin * 0.32 + def.seed * 0.5,
    )
    mesh.current.scale.setScalar(def.scale)

    // Keep the published world position live so chains track the rock they
    // are actually attached to rather than its original resting place.
    world.setY(def.base[1])
    rockPositions.set(def.id, mesh.current.position.clone())

    const u = liveUniforms(mat, uniforms)
    u.uTime.value = time
    u.uCharge.value = st.charge + proximity * st.charge * 0.25
    u.uFront.value = st.vibrate
    u.uAnchor.value = def.anchor ? 1 : 0
  })

  return (
    <mesh ref={mesh} geometry={geom} position={def.base} castShadow receiveShadow>
      <shaderMaterial ref={mat} uniforms={uniforms} vertexShader={ROCK_VERT} fragmentShader={ROCK_FRAG} />
    </mesh>
  )
}

/**
 * Distant debris. One draw call; per-instance phase derived from the instance
 * index so no per-instance JS state is needed.
 */
function InstancedDebris({ count }: { count: number }) {
  const ref = useRef<THREE.InstancedMesh>(null)
  const dummy = useMutable(() => new THREE.Object3D())
  const geom = useMemo(() => makeRockGeometry(3, 0), [])

  const seeds = useMemo(() => {
    // Deterministic scatter in a ring around the monolith, biased away from
    // the walking corridor so it reads as depth rather than clutter.
    return Array.from({ length: count }, (_, i) => {
      const a = i * 2.399963 // golden angle
      const r = 26 + (i / count) * 130
      return {
        x: MONOLITH_BASE.x + Math.cos(a) * r,
        y: 1 + ((i * 37) % 23) * 0.9,
        z: MONOLITH_BASE.z + Math.sin(a) * r * 0.8,
        s: 0.35 + ((i * 17) % 11) * 0.11,
        p: (i % 31) * 0.2,
      }
    })
  }, [count])

  const probe = useMutable(() => new THREE.Vector3())

  useFrame((s) => {
    const mesh = ref.current
    if (!mesh || !frame.visible) return
    const time = s.clock.elapsedTime

    for (let i = 0; i < count; i++) {
      const d = seeds[i]
      probe.set(d.x, d.y, d.z)
      const charge = waveReached(probe)
      const lift = smoothstep(clamp01((charge - 0.35) / 0.5)) * 2.0
      dummy.position.set(
        d.x,
        d.y + lift + Math.sin(time * 0.4 + d.p) * 0.3 * charge,
        d.z,
      )
      dummy.rotation.set(d.p + time * 0.05 * charge, d.p * 2 + time * 0.07 * charge, d.p)
      dummy.scale.setScalar(d.s)
      dummy.updateMatrix()
      mesh.setMatrixAt(i, dummy.matrix)
    }
    mesh.instanceMatrix.needsUpdate = true
  })

  return (
    <instancedMesh ref={ref} args={[geom, undefined, count]} frustumCulled={false}>
      <meshStandardMaterial color="#0d1119" roughness={0.9} metalness={0.08} />
    </instancedMesh>
  )
}

export function FloatingRocks({
  heroCount,
  instancedCount,
}: {
  heroCount: number
  instancedCount: number
}) {
  // Anchor rocks are never dropped by the quality tier — the chains attach to
  // them, so losing one would leave a chain hanging in empty space.
  const rocks = useMemo(
    () => [...ANCHOR_ROCKS, ...SILHOUETTE_ROCKS.slice(0, Math.max(0, heroCount - ANCHOR_ROCKS.length))],
    [heroCount],
  )

  return (
    <group>
      {rocks.map((r) => (
        <Rock key={r.id} def={r} />
      ))}
      {instancedCount > 0 && <InstancedDebris count={instancedCount} />}
    </group>
  )
}
