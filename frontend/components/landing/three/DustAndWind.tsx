"use client"

/**
 * Wind-driven dust and punch-impact debris (spec §6.2, §7).
 *
 * Both systems are evaluated analytically in the vertex shader from a single
 * progress uniform rather than integrated on the CPU. That is not just a
 * performance choice — it is what makes them *scrubbable*. The visitor can
 * drag the scroll backwards through the punch, and an integrated particle
 * simulation would have no way to un-throw the debris; a closed-form position
 * simply evaluates to the earlier state.
 *
 * Spec §6.2: "Dust should move directionally across the ground, not as random
 * particle noise. Cloth motion, dust and loose pebbles all reinforce the
 * camera-facing wind direction." The wind vector below is shared by all three.
 */

import { useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import { BEATS, beatProgress, clamp01, smoothstep } from "../config/motion"
import { frame } from "../state/landingStore"
import { PUNCH_POINT } from "./Terrain"
import { useMutable, liveUniforms } from "./useMutable"

/** The one wind direction the whole hero agrees on (toward camera, +Z, +X). */
export const WIND_DIR = new THREE.Vector3(0.34, 0.06, 1).normalize()

const FIELD = 150

export function DustAndWind({ count, debris }: { count: number; debris: number }) {
  // ── Ambient wind dust ────────────────────────────────────────────────────
  const dustGeom = useMemo(() => {
    const g = new THREE.BufferGeometry()
    const pos = new Float32Array(count * 3)
    const seed = new Float32Array(count * 3)
    for (let i = 0; i < count; i++) {
      // Golden-angle scatter, deterministic (no Math.random → stable across
      // reloads and identical between server-rendered markup and client).
      const a = i * 2.399963
      const r = Math.sqrt(i / count) * FIELD
      pos[i * 3] = Math.cos(a) * r
      // Heavily biased toward the ground: this is dust being dragged across a
      // surface, not fog filling a volume.
      pos[i * 3 + 1] = Math.pow((i % 97) / 97, 2.6) * 26
      pos[i * 3 + 2] = Math.sin(a) * r
      seed[i * 3] = (i % 61) / 61
      seed[i * 3 + 1] = (i % 37) / 37
      seed[i * 3 + 2] = 0.35 + ((i % 13) / 13) * 1.4
    }
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3))
    g.setAttribute("aSeed", new THREE.BufferAttribute(seed, 3))
    return g
  }, [count])

  const dustUniforms = useMutable(
    () => ({
      uTime: { value: 0 },
      uWind: { value: WIND_DIR.clone() },
      /** Wind builds as the character approaches the punch (spec §6.2). */
      uStrength: { value: 0.35 },
      uField: { value: FIELD },
      uCharge: { value: 0 },
      uPixel: { value: 1 },
    }))

  // ── Punch impact debris ──────────────────────────────────────────────────
  const debrisGeom = useMemo(() => {
    const g = new THREE.BufferGeometry()
    const pos = new Float32Array(debris * 3)
    const dir = new Float32Array(debris * 3)
    const seed = new Float32Array(debris)
    for (let i = 0; i < debris; i++) {
      const a = i * 2.399963
      // Radial, low-angle launch: displaced dirt racing outward along the
      // ground, not a fountain.
      const elev = 0.12 + ((i % 23) / 23) * 0.5
      const speed = 6 + ((i % 17) / 17) * 22
      dir[i * 3] = Math.cos(a) * speed
      dir[i * 3 + 1] = elev * speed * 0.75
      dir[i * 3 + 2] = Math.sin(a) * speed
      pos[i * 3] = PUNCH_POINT.x
      pos[i * 3 + 1] = 0.1
      pos[i * 3 + 2] = PUNCH_POINT.z
      seed[i] = (i % 29) / 29
    }
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3))
    g.setAttribute("aDir", new THREE.BufferAttribute(dir, 3))
    g.setAttribute("aSeed", new THREE.BufferAttribute(seed, 1))
    return g
  }, [debris])

  const debrisUniforms = useMutable(
    () => ({
      /** Seconds since impact, derived from scroll progress — scrubbable. */
      uT: { value: 0 },
      uStrength: { value: 0 },
      uOrigin: { value: PUNCH_POINT.clone() },
      uWind: { value: WIND_DIR.clone() },
      uPixel: { value: 1 },
    }))

  const dustRef = useRef<THREE.Points>(null)
  const debrisRef = useRef<THREE.Points>(null)
  const dustMat = useRef<THREE.ShaderMaterial>(null)
  const debrisMat = useRef<THREE.ShaderMaterial>(null)

  useFrame((state, dt) => {
    if (!frame.visible) return
    const t = frame.introProgress
    const time = state.clock.elapsedTime

    const du = liveUniforms(dustMat, dustUniforms)
    du.uTime.value = time
    // Spec §6.2: "Wind begins subtle and builds as the character approaches
    // the punch point. The opening should feel calm enough that the punch has
    // contrast." Plus a clamped scroll-velocity contribution (spec §15).
    const approach = smoothstep(beatProgress(t, "walk"))
    const gust = 0.5 + 0.5 * Math.sin(time * 0.23) * Math.sin(time * 0.07 + 2.1)
    du.uStrength.value =
      0.3 + approach * 0.55 + gust * 0.3 + Math.abs(frame.scrollVelocity) * 0.35
    // Dust catches the energy wave and the core glow once they exist.
    du.uCharge.value = clamp01(
      beatProgress(t, "ignition") * 0.5 + beatProgress(t, "energyWave"),
    )
    du.uPixel.value = Math.min(state.gl.getPixelRatio(), 2)

    // Impact debris: map scroll progress past the punch onto a synthetic
    // seconds-since-impact, so the arc plays out over the shockwave beat.
    const since = clamp01((t - BEATS.punch[1]) / (BEATS.shockwave[1] - BEATS.punch[1]))
    const bu = liveUniforms(debrisMat, debrisUniforms)
    bu.uT.value = since * 2.4
    bu.uStrength.value =
      t >= BEATS.punch[0] ? (1 - smoothstep(clamp01((since - 0.55) / 0.45))) : 0
    bu.uPixel.value = du.uPixel.value

    if (debrisRef.current) debrisRef.current.visible = bu.uStrength.value > 0.001
    void dt
  })

  return (
    <group>
      <points ref={dustRef} geometry={dustGeom} frustumCulled={false} renderOrder={2}>
        <shaderMaterial
          ref={dustMat}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          uniforms={dustUniforms}
          vertexShader={/* glsl */ `
            uniform float uTime;
            uniform vec3  uWind;
            uniform float uStrength;
            uniform float uField;
            uniform float uPixel;
            attribute vec3 aSeed;
            varying float vAlpha;
            varying float vSeed;

            void main() {
              vec3 p = position;

              // Directional travel along the shared wind vector. Modulo-wrap
              // in the wind's own axis so particles stream continuously across
              // the field instead of respawning at a visible boundary.
              float travel = uTime * (2.4 + aSeed.z * 5.0) * uStrength;
              p += uWind * mod(travel + aSeed.x * uField * 2.0, uField * 2.0);
              p -= uWind * uField;

              // Secondary swirl, small: enough that the stream is not a
              // conveyor belt, not so much that direction is lost.
              p.x += sin(uTime * 0.7 + aSeed.y * 6.28) * 1.4 * uStrength;
              p.y += sin(uTime * 1.1 + aSeed.x * 6.28) * 0.7 * uStrength;

              // Fade at the field edge — no hard cut-off ring.
              float edge = 1.0 - smoothstep(uField * 0.55, uField, length(p.xz));

              vec4 mv = modelViewMatrix * vec4(p, 1.0);
              gl_Position = projectionMatrix * mv;
              gl_PointSize = (aSeed.z * 2.6 + 0.8) * uPixel * (60.0 / -mv.z);

              // Lower dust is denser — it is being dragged along the ground.
              vAlpha = edge * (1.0 - smoothstep(0.0, 24.0, p.y)) * 0.55;
              vSeed = aSeed.y;
            }
          `}
          fragmentShader={/* glsl */ `
            uniform float uCharge;
            varying float vAlpha;
            varying float vSeed;
            void main() {
              vec2 d = gl_PointCoord - 0.5;
              float m = 1.0 - smoothstep(0.16, 0.5, length(d));
              if (m <= 0.001) discard;
              // Dust is warm-neutral until the wave charges it, then cyan.
              vec3 dull = vec3(0.30, 0.31, 0.36);
              vec3 lit  = vec3(0.35, 0.68, 1.0);
              vec3 col = mix(dull, lit, uCharge * (0.4 + vSeed * 0.6));
              gl_FragColor = vec4(col, m * vAlpha * (0.5 + uCharge * 0.9));
            }
          `}
        />
      </points>

      <points ref={debrisRef} geometry={debrisGeom} frustumCulled={false} renderOrder={2}>
        <shaderMaterial
          ref={debrisMat}
          transparent
          depthWrite={false}
          uniforms={debrisUniforms}
          vertexShader={/* glsl */ `
            uniform float uT;
            uniform float uStrength;
            uniform vec3  uOrigin;
            uniform vec3  uWind;
            uniform float uPixel;
            attribute vec3  aDir;
            attribute float aSeed;
            varying float vAlpha;

            void main() {
              // Closed-form ballistic arc: p = o + v*t - 0.5*g*t^2, with drag
              // folded in as an exponential on the horizontal term. Evaluating
              // rather than integrating is what makes this scrub backwards.
              float t = uT * (0.7 + aSeed * 0.6);
              float drag = 1.0 - exp(-t * 1.6);
              vec3 p = uOrigin;
              p.xz += aDir.xz * drag * 0.85;
              p.y  += aDir.y * t - 9.8 * t * t * 0.5;
              // Wind pushes the airborne dirt in the same direction as
              // everything else in the scene.
              p += uWind * t * 1.6 * aSeed;
              // Never sink through the floor.
              p.y = max(p.y, 0.05);

              vec4 mv = modelViewMatrix * vec4(p, 1.0);
              gl_Position = projectionMatrix * mv;
              gl_PointSize = (1.2 + aSeed * 3.4) * uPixel * (70.0 / -mv.z);
              vAlpha = uStrength * (1.0 - smoothstep(0.0, 2.2, t));
            }
          `}
          fragmentShader={/* glsl */ `
            varying float vAlpha;
            void main() {
              vec2 d = gl_PointCoord - 0.5;
              float m = 1.0 - smoothstep(0.2, 0.5, length(d));
              if (m <= 0.001) discard;
              // Displaced dirt reads warm and dark — deliberately NOT the
              // cyan energy language (spec §6.2).
              gl_FragColor = vec4(vec3(0.38, 0.31, 0.26), m * vAlpha);
            }
          `}
        />
      </points>
    </group>
  )
}
