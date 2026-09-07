"use client"

/**
 * Hero ground.
 *
 * Spec §6.2: "The punch impact is the physical trigger for the ground
 * fracture... The punch shockwave travels primarily through the ground as
 * cracks, displaced dirt and pebble impulses. It should NOT look like the later
 * luminous spherical TrustChain energy wave."
 *
 * The two events are therefore driven by two independent uniforms with
 * deliberately different visual languages:
 *   uShock — a *ground* ring: displacement, dirt, dark fracture lines that
 *            only glow faintly at their hot edges.
 *   uWave  — the later spherical energy event, which only ever *lights* the
 *            ground; it never displaces it.
 *
 * Built on MeshStandardMaterial via onBeforeCompile rather than a hand-written
 * lit shader so the terrain keeps real scene lighting, fog and tone mapping —
 * a bespoke unlit shader here is what makes cinematic ground read as a flat
 * backdrop instead of a surface the character is standing on.
 */

import { useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import { SIMPLEX_3D, RIDGE, PALETTE } from "./shaders/noise"
import { BEATS, beatProgress, clamp01, smoothstep } from "../config/motion"
import { frame } from "../state/landingStore"
import { useMutable } from "./useMutable"

export const PUNCH_POINT = new THREE.Vector3(0, 0, 1.2)
export const MONOLITH_BASE = new THREE.Vector3(2.2, 0, -9)

interface TerrainUniforms {
  uTime: { value: number }
  uPunch: { value: THREE.Vector3 }
  /** Radius of the ground shockwave ring, in world units. 0 = no punch yet. */
  uShock: { value: number }
  /** 0..1 how established the fracture is (cracks stay after the ring passes). */
  uFracture: { value: number }
  /** Radius of the spherical energy wave — lighting only, no displacement. */
  uWave: { value: number }
  uWaveStrength: { value: number }
  /** Where the monolith broke through, so the ground buckles around it. */
  uRift: { value: THREE.Vector3 }
  uRiftAmount: { value: number }
}

export function Terrain({ segments = 200 }: { segments?: number }) {
  const matRef = useRef<THREE.MeshStandardMaterial>(null)

  const uniforms = useMutable<TerrainUniforms>(
    () => ({
      uTime: { value: 0 },
      uPunch: { value: PUNCH_POINT.clone() },
      uShock: { value: 0 },
      uFracture: { value: 0 },
      uWave: { value: 0 },
      uWaveStrength: { value: 0 },
      uRift: { value: MONOLITH_BASE.clone() },
      uRiftAmount: { value: 0 },
    }))

  const geometry = useMemo(() => {
    const g = new THREE.PlaneGeometry(420, 420, segments, segments)
    g.rotateX(-Math.PI / 2)
    return g
  }, [segments])

  const onBeforeCompile = useMemo(
    () => (shader: THREE.WebGLProgramParametersWithUniforms) => {
      Object.assign(shader.uniforms, uniforms)

      shader.vertexShader = shader.vertexShader
        .replace(
          "#include <common>",
          /* glsl */ `
          #include <common>
          uniform float uTime;
          uniform vec3  uPunch;
          uniform float uShock;
          uniform float uFracture;
          uniform vec3  uRift;
          uniform float uRiftAmount;
          varying vec3  vWorld;
          varying float vCrater;
          ${SIMPLEX_3D}
          `,
        )
        .replace(
          "#include <begin_vertex>",
          /* glsl */ `
          #include <begin_vertex>

          vec3 wp = (modelMatrix * vec4(transformed, 1.0)).xyz;

          // Base terrain: broad rolling displacement plus a finer rocky layer.
          float base = fbm(wp * 0.012) * 5.2 + fbm(wp * 0.06) * 0.9;
          // Flatten the walking corridor so the character never clips a ridge
          // and the punch lands on readable ground.
          float corridor = smoothstep(3.0, 16.0, abs(wp.x));
          base *= mix(0.12, 1.0, corridor);

          float dPunch = distance(wp.xz, uPunch.xz);

          // Crater: a permanent depression once the punch has landed, with a
          // raised lip — displaced dirt, not a clean dent (spec §6.2).
          float crater = 0.0;
          if (uFracture > 0.0) {
            float c = 1.0 - smoothstep(0.0, 5.5, dPunch);
            float lip = smoothstep(3.2, 5.4, dPunch) * (1.0 - smoothstep(5.4, 8.5, dPunch));
            crater = (-c * 1.15 + lip * 0.45) * uFracture;
          }

          // Travelling ring: a real vertical displacement that races outward
          // ahead of the man, so the shockwave is ground motion first and a
          // graphic second.
          float ring = 0.0;
          if (uShock > 0.001) {
            float band = 1.0 - smoothstep(0.0, 5.0, abs(dPunch - uShock));
            float decay = 1.0 - smoothstep(0.0, 90.0, uShock);
            ring = band * decay * 0.85 * sin((dPunch - uShock) * 0.9);
          }

          // The monolith buckles the ground it breaks through.
          float dRift = distance(wp.xz, uRift.xz);
          float rift = (1.0 - smoothstep(0.0, 15.0, dRift)) * uRiftAmount * 2.2;
          rift += (1.0 - smoothstep(0.0, 26.0, dRift)) * uRiftAmount * 0.7;

          transformed.y += base + crater + ring + rift;
          vWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;
          vCrater = clamp(-crater, 0.0, 2.0);
          `,
        )

      shader.fragmentShader = shader.fragmentShader
        .replace(
          "#include <common>",
          /* glsl */ `
          #include <common>
          uniform float uTime;
          uniform vec3  uPunch;
          uniform float uShock;
          uniform float uFracture;
          uniform float uWave;
          uniform float uWaveStrength;
          uniform vec3  uRift;
          uniform float uRiftAmount;
          varying vec3  vWorld;
          varying float vCrater;
          ${SIMPLEX_3D}
          ${RIDGE}
          `,
        )
        .replace(
          "#include <dithering_fragment>",
          /* glsl */ `
          #include <dithering_fragment>

          float dPunch = distance(vWorld.xz, uPunch.xz);

          // ── Fracture network ────────────────────────────────────────────
          // Ridged noise stretched radially so cracks run *away* from the
          // impact rather than forming a uniform crazing pattern.
          vec2 radial = normalize(vWorld.xz - uPunch.xz + 1e-4);
          float ang = atan(radial.y, radial.x);
          vec3 crackP = vec3(ang * 2.6, dPunch * 0.16, 0.0);
          float r = ridge(crackP * 2.2);
          float crack = smoothstep(0.86, 1.02, r);

          // Cracks only exist where the shockwave has already passed.
          float reached = uFracture * (1.0 - smoothstep(uShock * 0.9, uShock * 1.25 + 4.0, dPunch));
          reached *= 1.0 - smoothstep(0.0, 62.0, dPunch);
          crack *= reached;

          // Dark fracture body, hot only at the leading edge — the punch is
          // kinetic, so its light is residual heat, not TrustChain energy.
          gl_FragColor.rgb = mix(gl_FragColor.rgb, vec3(0.01, 0.012, 0.02), crack * 0.9);
          float hot = crack * (1.0 - smoothstep(0.0, 9.0, abs(dPunch - uShock)));
          gl_FragColor.rgb += vec3(0.75, 0.42, 0.20) * hot * 0.75;

          // Dirt darkening inside the crater.
          gl_FragColor.rgb *= 1.0 - vCrater * 0.28;

          // ── Spherical energy wave: lighting only ────────────────────────
          // Distinct language from the punch: cool, wide, soft, no geometry
          // change (spec §6.2 / §6.4).
          if (uWaveStrength > 0.001) {
            float dw = distance(vWorld, vec3(uRift.x, 6.0, uRift.z));
            float band = 1.0 - smoothstep(0.0, 26.0, abs(dw - uWave));
            float inner = 1.0 - smoothstep(uWave, uWave + 40.0, dw);
            vec3 energy = vec3(0.24, 0.62, 1.0);
            gl_FragColor.rgb += energy * band * uWaveStrength * 0.85;
            gl_FragColor.rgb += energy * inner * uWaveStrength * 0.12;
            // Cracks catch the wave and glow cyan as it passes over them.
            gl_FragColor.rgb += energy * crack * band * uWaveStrength * 1.6;
          }

          // Permanent faint core-light spill around the monolith base.
          float dRift = distance(vWorld.xz, uRift.xz);
          gl_FragColor.rgb += vec3(0.18, 0.48, 0.9)
            * (1.0 - smoothstep(0.0, 16.0, dRift)) * uRiftAmount * 0.07;
          `,
        )
    },
    [uniforms],
  )

  useFrame((_, dt) => {
    if (!frame.visible) return
    const t = frame.introProgress
    uniforms.uTime.value += dt

    const shockP = beatProgress(t, "shockwave")
    const punched = t >= BEATS.punch[0]

    // The ring races ahead of the man toward the future monolith point
    // (spec §6.2) and keeps expanding past the beat so it leaves the frame
    // rather than stopping dead.
    uniforms.uShock.value = punched ? 2 + Math.pow(shockP, 0.62) * 120 : 0
    uniforms.uFracture.value = punched ? smoothstep(clamp01((t - BEATS.punch[0]) / 0.03)) : 0

    const riseP = beatProgress(t, "monolithRise")
    uniforms.uRiftAmount.value = smoothstep(riseP)

    const waveP = beatProgress(t, "energyWave")
    uniforms.uWave.value = waveP * 210
    // Fades as it expands: the wave is an event, not a permanent floodlight.
    uniforms.uWaveStrength.value = Math.sin(Math.PI * clamp01(waveP)) * 1.15

    if (matRef.current) matRef.current.needsUpdate = false
  })

  return (
    <mesh geometry={geometry} receiveShadow position={[0, 0, 0]}>
      <meshStandardMaterial
        ref={matRef}
        color={PALETTE.rock}
        roughness={0.96}
        metalness={0.04}
        onBeforeCompile={onBeforeCompile}
        // Two terrains compiled from the same base program would otherwise
        // share a cached program and silently drop these uniforms.
        customProgramCacheKey={() => "trustchain-terrain-v1"}
      />
    </mesh>
  )
}
