"use client"

/**
 * The monolith (spec §6.3, §6.4, §6.7).
 *
 * Cause-and-effect is mandatory: it rises only AFTER the punch has fractured
 * the ground, it is kinematic during the rise for deterministic timing, and
 * once settled it behaves as a *heavy* object with a restoring force — chain
 * pulls produce a small tilt followed by a slow return, and it must never
 * become a light object (spec §6.3).
 */

import { useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import { beatProgress, easeHeavy, easeIgnite, clamp01, lerp } from "../config/motion"
import { frame } from "../state/landingStore"
import { MONOLITH_BASE } from "./Terrain"
import { SIMPLEX_3D } from "./shaders/noise"
import { useMutable, liveUniforms } from "./useMutable"

export const MONOLITH_HEIGHT = 34
/** Where the glowing core/emblem sits, in monolith-local space. */
export const CORE_LOCAL = new THREE.Vector3(0, 12.5, 3.05)

/** Published for the chains, camera and logo-travel to read. */
export const monolithState = {
  group: null as THREE.Group | null,
  core: new THREE.Vector3(),
  risen: 0,
  ignition: 0,
}

export function Monolith() {
  const group = useRef<THREE.Group>(null)
  const coreRef = useRef<THREE.Mesh>(null)
  const coreLight = useRef<THREE.PointLight>(null)
  const emblem = useRef<THREE.Group>(null)
  const bodyMat = useRef<THREE.ShaderMaterial>(null)
  const coreMat = useRef<THREE.ShaderMaterial>(null)

  /** Restoring-force state — integrated, not keyframed. */
  const tilt = useRef({ x: 0, z: 0, vx: 0, vz: 0 })

  const bodyUniforms = useMutable(
    () => ({
      uTime: { value: 0 },
      uIgnite: { value: 0 },
      uPulse: { value: 0 },
      uRise: { value: 0 },
      uBase: { value: new THREE.Color("#0a0f18") },
      uCircuit: { value: new THREE.Color("#3ec6ff") },
    }))

  const coreUniforms = useMutable(
    () => ({
      uTime: { value: 0 },
      uIgnite: { value: 0 },
      uPulse: { value: 0 },
    }))

  // Hexagonal obelisk: wider at the base, slightly tapered. Six flat faces
  // give the circuitry somewhere to sit and catch a hard specular edge, which
  // a cylinder does not.
  const bodyGeometry = useMemo(() => {
    const g = new THREE.CylinderGeometry(2.1, 3.4, MONOLITH_HEIGHT, 6, 26)
    g.translate(0, MONOLITH_HEIGHT / 2, 0)
    return g
  }, [])

  useFrame((state, dt) => {
    if (!group.current || !frame.visible) return
    const t = frame.introProgress
    const time = state.clock.elapsedTime

    // ── Rise: kinematic, heavy, deterministic (spec §6.3) ─────────────────
    const riseP = beatProgress(t, "monolithRise")
    const settleP = beatProgress(t, "settle")
    const rise = easeHeavy(riseP)
    monolithState.risen = rise

    // Starts fully buried. The extra -1.2 keeps its foot below the fractured
    // ground even at full height, so it reads as continuing underground
    // rather than resting on the surface.
    const y = lerp(-MONOLITH_HEIGHT - 1.5, -1.2, rise)

    // A slow settle overshoot: it drops the last fraction under its own mass
    // instead of arriving exactly on target.
    const overshoot = Math.sin(settleP * Math.PI) * 0.22

    // ── Chain tension: small tilt only, then a slow return (spec §6.3) ────
    // Spring-damper rather than a tween, so a sustained cursor pull holds the
    // lean and releasing it returns naturally without a scripted duration.
    const tn = tilt.current
    const targetX = -frame.chainTension * 0.028 * (frame.pointerY * 0.6 + 0.7)
    const targetZ = frame.chainTension * 0.034 * frame.pointerX
    const k = 14 // stiffness — high, because it is very heavy
    const c = 5.2 // damping
    tn.vx += (targetX - tn.x) * k * dt - tn.vx * c * dt
    tn.vz += (targetZ - tn.z) * k * dt - tn.vz * c * dt
    tn.x += tn.vx * dt
    tn.z += tn.vz * dt

    group.current.position.set(MONOLITH_BASE.x, y - overshoot, MONOLITH_BASE.z)
    group.current.rotation.set(tn.x, 0.36, tn.z)

    monolithState.group = group.current
    group.current.localToWorld(monolithState.core.copy(CORE_LOCAL))

    // ── Ignition: near-instant attack, then a permanent living glow ───────
    // Spec §6.4: "should then ignite almost instantly with a powerful
    // cyan/blue flash - not a slow fade."
    const ign = easeIgnite(beatProgress(t, "ignition"))
    monolithState.ignition = ign

    // Spec §7: "The monolith core has a slow breathing glow and occasional
    // subtle circuitry pulse." Two incommensurable frequencies so the idle
    // never lands on an obvious synchronized loop (spec §7).
    const breathe = 0.5 + 0.5 * Math.sin(time * 0.63)
    const flicker = Math.pow(0.5 + 0.5 * Math.sin(time * 0.17 + 1.3), 6)

    // Spec §7.2: "Cursor near monolith -> circuitry/core brightness increases
    // slightly." Distance is measured in screen space, which is what the
    // visitor actually perceives as "near".
    const proximity = clamp01(
      1 - Math.hypot(frame.pointerX - 0.28, frame.pointerY - 0.1) / 1.1,
    )

    const pulse = ign * (0.72 + breathe * 0.28 + flicker * 0.45 + proximity * 0.3)

    const bu = liveUniforms(bodyMat, bodyUniforms)
    bu.uTime.value = time
    bu.uIgnite.value = ign
    bu.uPulse.value = pulse
    bu.uRise.value = rise

    const cu = liveUniforms(coreMat, coreUniforms)
    cu.uTime.value = time
    cu.uIgnite.value = ign
    cu.uPulse.value = pulse

    if (coreLight.current) {
      // Punchy on ignition, then settles to a steady living value.
      coreLight.current.intensity = ign * (52 + breathe * 20 + proximity * 18)
    }
    if (emblem.current) {
      // A slow ±2° sway, not a rotation. This is the TrustChain brand mark —
      // the same shape that separates from here and resolves into the navbar
      // (spec §6.7) — and a shield spun off-axis stops reading as a shield.
      emblem.current.rotation.z = Math.sin(time * 0.35) * 0.035
      emblem.current.scale.setScalar(1 + breathe * 0.03)
    }
  })

  return (
    <group ref={group} position={[MONOLITH_BASE.x, -MONOLITH_HEIGHT - 1.5, MONOLITH_BASE.z]}>
      {/* Body */}
      <mesh geometry={bodyGeometry} castShadow receiveShadow>
        <shaderMaterial
          ref={bodyMat}
          uniforms={bodyUniforms}
          vertexShader={/* glsl */ `
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
          `}
          fragmentShader={/* glsl */ `
            uniform float uTime;
            uniform float uIgnite;
            uniform float uPulse;
            uniform float uRise;
            uniform vec3  uBase;
            uniform vec3  uCircuit;
            varying vec3  vPos;
            varying vec3  vNormalW;
            varying vec3  vViewDir;
            ${SIMPLEX_3D}

            void main() {
              // Weathered stone base.
              float grain = fbm(vPos * 1.4) * 0.5 + 0.5;
              vec3 col = uBase * (0.55 + grain * 0.7);

              // ── Circuitry ────────────────────────────────────────────────
              // Orthogonal traces on a grid, thinned so they read as etched
              // channels rather than a glowing texture pasted on the surface.
              vec2 uv = vec2(atan(vPos.x, vPos.z) * 1.9, vPos.y * 0.42);
              vec2 cell = fract(uv * 3.0);
              float lineA = (1.0 - smoothstep(0.0, 0.030, abs(cell.x - 0.5)));
              float lineB = (1.0 - smoothstep(0.0, 0.022, abs(cell.y - 0.5)));
              // Break the grid up so it isn't a perfect lattice.
              float mask = step(0.42, fbm(vec3(floor(uv * 3.0), 0.0) * 0.9) * 0.5 + 0.5);
              float circuit = max(lineA, lineB) * mask;

              // Energy travels *up* the traces toward the core.
              float travel = fract(vPos.y * 0.16 - uTime * 0.22);
              float packet = smoothstep(0.86, 1.0, travel) * circuit;

              // Traces are dark and dead until ignition.
              col += uCircuit * circuit * (0.05 + uPulse * 0.55);
              col += uCircuit * packet * uPulse * 1.5;

              // Brighter nearer the core height.
              float toCore = 1.0 - smoothstep(0.0, 14.0, abs(vPos.y - 12.5));
              col += uCircuit * circuit * toCore * uPulse * 0.9;

              // Fresnel edge so the silhouette holds against the dark sky.
              float fres = pow(1.0 - max(dot(vNormalW, vViewDir), 0.0), 3.0);
              col += uCircuit * fres * (0.08 + uPulse * 0.5);

              // Dirt still clinging to it on the way up.
              col *= mix(0.35, 1.0, smoothstep(0.0, 0.55, uRise));

              gl_FragColor = vec4(col, 1.0);
              #include <tonemapping_fragment>
              #include <colorspace_fragment>
            }
          `}
        />
      </mesh>

      {/* ── Core / emblem ──────────────────────────────────────────────────
          Spec §6.7: a holographic copy of this emblem later separates from
          here and travels to the navbar, so the shape must read as the brand
          mark (shield + diamond) and not as a generic glowing disc. */}
      <group ref={emblem} position={CORE_LOCAL.toArray()}>
        <mesh ref={coreRef}>
          <circleGeometry args={[3.1, 56]} />
          <shaderMaterial
            ref={coreMat}
            transparent
            depthWrite={false}
            uniforms={coreUniforms}
            vertexShader={/* glsl */ `
              varying vec2 vUv;
              void main() {
                vUv = uv;
                gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
              }
            `}
            fragmentShader={/* glsl */ `
              uniform float uTime;
              uniform float uIgnite;
              uniform float uPulse;
              varying vec2 vUv;

              // Signed distance to a shield outline.
              float shield(vec2 p) {
                p.y *= 1.12;
                float top = length(vec2(p.x, max(p.y - 0.06, 0.0))) - 0.60;
                float bot = abs(p.x) * 1.34 + max(-p.y - 0.10, 0.0) * 1.5 - 0.62;
                return max(top, bot);
              }
              float diamond(vec2 p) { return abs(p.x) + abs(p.y) - 0.24; }

              void main() {
                vec2 p = (vUv - 0.5) * 2.0;
                float d = shield(p);
                // Stroke widths are in SDF units across a [-1,1] quad, chosen
                // to stay readable from the full-wide monumental shot without
                // the mark collapsing into a blob at close range. Note the
                // descending-edge form: smoothstep's result is UNDEFINED when
                // edge0 >= edge1 in GLSL ES, and writing it that way silently
                // produced nothing on some drivers, so every inverted ramp on
                // this page is spelled as 1.0 minus an ascending smoothstep.
                float ring = (1.0 - smoothstep(0.012, 0.055, abs(d)));
                float fill = (1.0 - smoothstep(-0.22, -0.02, d));
                float dia  = (1.0 - smoothstep(0.010, 0.042, abs(diamond(p))));
                float diaFill = (1.0 - smoothstep(-0.10, 0.0, diamond(p)));

                vec3 cyan = vec3(0.31, 0.78, 1.0);
                vec3 hot  = vec3(0.86, 0.96, 1.0);

                vec3 col = cyan * ring * 2.2;
                // The interior is a glow the strokes sit on, not a filled
                // disc — a solid fill swallows the shield outline entirely.
                col += cyan * fill * 0.16;
                col += hot * dia * 1.9;
                col += hot * diaFill * 0.35;

                // Soft halo bleeding past the emblem, so the core lights the
                // face around it rather than sitting on it like a decal.
                float halo = exp(-length(p) * 2.35);
                col += cyan * halo * 0.42;

                float a = clamp(ring + fill * 0.30 + dia + diaFill * 0.30 + halo * 0.55, 0.0, 1.0);
                // Ignition is an attack, not a fade: the first frames are
                // white-hot and overshoot well past the settled brightness.
                float flash = pow(uIgnite, 0.35);
                col = mix(col, hot, clamp((flash - uPulse) * 1.4, 0.0, 1.0)) * (0.4 + uPulse * 1.5) * flash;

                gl_FragColor = vec4(col, a * flash);
              }
            `}
          />
        </mesh>
        <pointLight ref={coreLight} color="#4fb8ff" distance={62} decay={2} intensity={0} />
      </group>
    </group>
  )
}
