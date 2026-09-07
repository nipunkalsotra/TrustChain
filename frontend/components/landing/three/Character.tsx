"use client"

/**
 * The guardian (spec §6.1).
 *
 * "The man/guardian appears first... walks into position, slows, plants
 * himself, winds up and punches the ground. After impact he braces against the
 * eruption and finally looks upward as the monolith towers over him."
 *
 * ASSET NOTE (spec §17): the preferred final asset is a rigged character GLB
 * with walk / stop / brace / look-up / idle clips. None exists yet, and the
 * spec is explicit that a missing asset must not block implementation. This is
 * therefore a *procedural* placeholder built from primitives and driven by the
 * same normalized beat progress a real AnimationMixer would be scrubbed with —
 * so swapping in `ASSETS.character` later means replacing the meshes and
 * mapping these same beats onto clip times, not rewriting the choreography.
 *
 * Everything here is a pure function of `frame.introProgress` plus a wall
 * clock for ambience. There is no accumulated pose state, which is what makes
 * scrubbing backwards, Quick View's 420ms fast-forward and a paused scroll all
 * produce correct poses with no special-casing (spec §15.1).
 */

import { useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import {
  BEATS,
  beatProgress,
  clamp01,
  lerp,
  smoothstep,
  easeIgnite,
} from "../config/motion"
import { frame } from "../state/landingStore"
import { PUNCH_POINT } from "./Terrain"
import { useMutable, liveUniforms } from "./useMutable"

/** Published so the camera rig can frame the man without prop-drilling. */
export const characterState = {
  position: new THREE.Vector3(0, 0, 20),
  /** World-space position of the striking fist, for contact particles. */
  fist: new THREE.Vector3(),
  /** 1 on the exact frame of contact, decaying after — drives dust/impulse. */
  impact: 0,
}

const WALK_START_Z = 22
const PLANT_Z = 2.9

/** Where the man is along his path for a given intro progress. */
function pathZ(t: number): number {
  const walk = beatProgress(t, "walk")
  const slow = beatProgress(t, "slow")
  // Constant-ish stride during the walk, then a decelerating settle so the
  // stop reads as intent rather than a cut.
  const walkZ = lerp(WALK_START_Z, 5.6, smoothstep(walk))
  return lerp(walkZ, PLANT_Z, smoothstep(slow))
}

export function Character({ cloakDetail = 14 }: { cloakDetail?: number }) {
  const group = useRef<THREE.Group>(null)
  const torso = useRef<THREE.Group>(null)
  const head = useRef<THREE.Group>(null)
  const armR = useRef<THREE.Group>(null)
  const armL = useRef<THREE.Group>(null)
  const legR = useRef<THREE.Group>(null)
  const legL = useRef<THREE.Group>(null)
  const fistRef = useRef<THREE.Mesh>(null)
  const cloakMat = useRef<THREE.ShaderMaterial>(null)

  // ── Cloak ────────────────────────────────────────────────────────────────
  // Spec §17: "Cloth: prefer bone/secondary animation or lightweight
  // shader/vertex motion rather than expensive real-time full cloth
  // simulation." This is the vertex-motion option: wind, stride sway and
  // cursor-driven push are all evaluated per-vertex on the GPU, weighted by
  // height so the shoulders stay pinned and the hem does the moving.
  const cloakUniforms = useMutable(
    () => ({
      uTime: { value: 0 },
      /** Wind strength, ramps as he approaches the punch (spec §6.2). */
      uWind: { value: 0.25 },
      /** Forward speed, so the cloak trails while he walks. */
      uSpeed: { value: 0 },
      /** Extra impulse from the punch and from cursor velocity. */
      uImpulse: { value: 0 },
      uColor: { value: new THREE.Color("#05080f") },
      uRim: { value: new THREE.Color("#3a86d8") },
    }))

  const cloakGeometry = useMemo(() => {
    const g = new THREE.CylinderGeometry(0.30, 0.58, 1.72, 24, cloakDetail, true)
    g.translate(0, -0.52, 0)
    return g
  }, [cloakDetail])

  useFrame((state, dt) => {
    if (!group.current || !frame.visible) return

    const t = frame.introProgress
    const time = state.clock.elapsedTime

    // ── Position along the path ────────────────────────────────────────────
    const z = pathZ(t)
    group.current.position.set(PUNCH_POINT.x, 0, z)
    characterState.position.copy(group.current.position)

    // ── Beats ──────────────────────────────────────────────────────────────
    const walkP = beatProgress(t, "walk")
    const slowP = beatProgress(t, "slow")
    const windupP = beatProgress(t, "punchWindup")
    const punchP = beatProgress(t, "punch")
    const shockP = beatProgress(t, "shockwave")
    const riseP = beatProgress(t, "monolithRise")
    const ignP = beatProgress(t, "ignition")

    // Stride only exists while he is actually covering ground.
    const striding = clamp01(1 - slowP)
    // Phase is derived from distance travelled, not from the clock, so the
    // feet stay planted when the visitor stops scrolling (spec §6.1:
    // "If scroll pauses, directed movement pauses").
    const stridePhase = (WALK_START_Z - z) * 1.15
    const swing = Math.sin(stridePhase) * striding * 0.62

    // ── Legs ───────────────────────────────────────────────────────────────
    if (legR.current) legR.current.rotation.x = swing
    if (legL.current) legL.current.rotation.x = -swing

    // ── Torso: bob while walking, coil for the punch, brace after ─────────
    const bob = Math.abs(Math.sin(stridePhase)) * 0.07 * striding
    // Windup: weight drops, shoulder pulls back.
    const coil = smoothstep(windupP) * (1 - punchP)
    // Strike: fast forward rotation, then a braced crouch.
    const strike = easeIgnite(punchP)
    const brace = smoothstep(clamp01(shockP * 1.6)) * (1 - smoothstep(riseP))
    // Finally he looks up as the monolith towers over him.
    const lookUp = smoothstep(clamp01((riseP - 0.25) / 0.6))

    if (torso.current) {
      torso.current.position.y = 1.06 + bob - coil * 0.22 - strike * 0.34 - brace * 0.12
      torso.current.rotation.x = coil * 0.28 + strike * 0.52 + brace * 0.22 - lookUp * 0.14
      // Breathing — spec §7: "The man has idle/breathing animation" and it
      // must continue after the cinematic is finished or skipped.
      torso.current.scale.setScalar(1 + Math.sin(time * 1.1) * 0.008)
    }

    if (head.current) {
      head.current.rotation.x = -strike * 0.25 - brace * 0.1 + lookUp * 0.72
      // Spec §7.2: "optional tiny head/look adjustment, never game-NPC
      // tracking" — 4° of cursor influence, and only once he's standing still.
      head.current.rotation.y = frame.pointerX * 0.07 * (1 - striding)
    }

    // ── Arms ───────────────────────────────────────────────────────────────
    if (armR.current) {
      const walkSwing = -swing * 0.8
      // Windup pulls the fist up and back; the strike drives it into the floor.
      const windAngle = lerp(walkSwing, -2.3, coil)
      armR.current.rotation.x = lerp(windAngle, 1.35, strike)
    }
    if (armL.current) {
      armL.current.rotation.x = lerp(swing * 0.8, -0.35, coil) + brace * 0.5
      armL.current.rotation.z = -0.14 - brace * 0.3
    }

    // ── Impact event ───────────────────────────────────────────────────────
    // Everything downstream (dust ring, debris impulse, camera shake, terrain
    // fracture) keys off this single marker so contact, deformation and
    // camera all land on the same frame (spec §6.2).
    if (fistRef.current) {
      fistRef.current.getWorldPosition(characterState.fist)
    }
    const contact = punchP > 0 && punchP < 1 ? 1 - Math.abs(punchP - 0.85) / 0.85 : 0
    characterState.impact = Math.max(0, contact)
    if (punchP > 0.8 && punchP <= 1 && frame.punchImpulse < 1) {
      frame.punchImpulse = 1
      // Physically motivated, low-frequency camera impulse — never constant
      // random shake (spec §6.2).
      frame.cameraImpulse = Math.min(
        1,
        0.85 + Math.abs(frame.scrollVelocity) * 0.3,
      )
    }
    if (t < BEATS.punch[0]) frame.punchImpulse = 0

    // ── Cloak drive ────────────────────────────────────────────────────────
    const cu = liveUniforms(cloakMat, cloakUniforms)
    cu.uTime.value += dt
    // Wind "begins subtle and builds as the character approaches the punch
    // point" so the punch has contrast (spec §6.2).
    cu.uWind.value = lerp(0.22, 0.85, smoothstep(walkP)) + ignP * 0.4
    cu.uSpeed.value = striding
    cu.uImpulse.value = THREE.MathUtils.damp(
      cu.uImpulse.value,
      characterState.impact * 1.6 + frame.pointerSpeed * 0.35,
      6,
      dt,
    )
  })

  return (
    <group ref={group} position={[PUNCH_POINT.x, 0, WALK_START_Z]}>
      {/* Legs pivot at the hip. */}
      <group ref={legR} position={[0.17, 0.86, 0]}>
        <mesh castShadow position={[0, -0.43, 0]}>
          <capsuleGeometry args={[0.11, 0.66, 4, 8]} />
          <meshStandardMaterial color="#0a0e16" roughness={0.9} />
        </mesh>
      </group>
      <group ref={legL} position={[-0.17, 0.86, 0]}>
        <mesh castShadow position={[0, -0.43, 0]}>
          <capsuleGeometry args={[0.11, 0.66, 4, 8]} />
          <meshStandardMaterial color="#0a0e16" roughness={0.9} />
        </mesh>
      </group>

      <group ref={torso} position={[0, 1.06, 0]}>
        <mesh castShadow>
          <capsuleGeometry args={[0.24, 0.5, 4, 10]} />
          <meshStandardMaterial color="#111726" roughness={0.85} metalness={0.1} />
        </mesh>

        {/* A single faint emblem light so the silhouette reads against the
            dark ground before anything else in the scene is lit. */}
        <mesh position={[0, 0.06, 0.24]}>
          <circleGeometry args={[0.055, 12]} />
          <meshBasicMaterial color="#4fb4ff" toneMapped={false} />
        </mesh>

        <group ref={head} position={[0, 0.56, 0]}>
          <mesh castShadow>
            <sphereGeometry args={[0.19, 16, 14]} />
            <meshStandardMaterial color="#0d121d" roughness={0.9} />
          </mesh>
          {/* Hood */}
          <mesh position={[0, 0.03, -0.03]} scale={[1.25, 1.15, 1.3]}>
            <sphereGeometry args={[0.2, 14, 12, 0, Math.PI * 2, 0, Math.PI * 0.62]} />
            <meshStandardMaterial color="#080c14" roughness={1} side={THREE.DoubleSide} />
          </mesh>
        </group>

        <group ref={armR} position={[0.3, 0.3, 0]}>
          <mesh castShadow position={[0, -0.32, 0]}>
            <capsuleGeometry args={[0.085, 0.5, 4, 8]} />
            <meshStandardMaterial color="#0d121d" roughness={0.88} />
          </mesh>
          <mesh ref={fistRef} position={[0, -0.62, 0]} castShadow>
            <sphereGeometry args={[0.115, 12, 10]} />
            <meshStandardMaterial
              color="#141b2b"
              roughness={0.7}
              emissive="#2a6fb8"
              emissiveIntensity={0.35}
            />
          </mesh>
        </group>

        <group ref={armL} position={[-0.3, 0.3, 0]}>
          <mesh castShadow position={[0, -0.34, 0]}>
            <capsuleGeometry args={[0.085, 0.54, 4, 8]} />
            <meshStandardMaterial color="#0d121d" roughness={0.88} />
          </mesh>
        </group>

        {/* Cloak — shoulders pinned, hem free. */}
        <mesh geometry={cloakGeometry} position={[0, 0.32, -0.02]}>
          <shaderMaterial
            ref={cloakMat}
            transparent
            side={THREE.DoubleSide}
            uniforms={cloakUniforms}
            vertexShader={/* glsl */ `
              uniform float uTime;
              uniform float uWind;
              uniform float uSpeed;
              uniform float uImpulse;
              varying float vY;
              varying vec3  vNormalW;

              void main() {
                vec3 p = position;
                // 0 at the collar, 1 at the hem — all motion is weighted by
                // this so the cloak never detaches from the shoulders.
                float w = clamp((-p.y + 0.35) / 2.2, 0.0, 1.0);
                float weight = w * w;

                float flutter =
                    sin(uTime * 2.3 + p.x * 3.1 + p.z * 2.2) * 0.10
                  + sin(uTime * 4.1 + p.y * 5.0) * 0.045;

                // Directional wind: the cloak trails *behind* him (+Z), which
                // is the same direction the ground dust travels (spec §6.2:
                // "Cloth motion, dust and loose pebbles all reinforce the
                // camera-facing wind direction").
                p.z += (uWind * 0.32 + uSpeed * 0.30 + uImpulse * 0.34) * weight;
                p.x += flutter * weight * (0.35 + uWind * 0.5);
                p.y += -flutter * weight * 0.35;
                // The punch throws the hem outward and up.
                p.xz *= 1.0 + uImpulse * weight * 0.18;

                vY = w;
                vNormalW = normalize(normalMatrix * normal);
                gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
              }
            `}
            fragmentShader={/* glsl */ `
              uniform vec3 uColor;
              uniform vec3 uRim;
              varying float vY;
              varying vec3 vNormalW;

              void main() {
                // Cheap rim term stands in for real cloth shading — enough to
                // separate the silhouette from the ground without lighting it.
                float rim = pow(1.0 - abs(vNormalW.z), 2.2);
                vec3 col = mix(uColor * 0.5, uColor, vY);
                col += uRim * rim * 0.22;
                // The hem dissolves into the dust rather than ending on a line.
                float alpha = mix(0.98, 0.45, vY);
                gl_FragColor = vec4(col, alpha);
              }
            `}
          />
        </mesh>
      </group>
    </group>
  )
}
