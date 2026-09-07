"use client"

/**
 * The spherical TrustChain energy wave (spec §6.4).
 *
 * "That ignition emits a giant spherical energy wave centered on the monolith.
 * Represent the wave as a translucent expanding sphere/ring field with
 * refraction/distortion, light, particles and local reactions - not as a flat
 * 2D circle."
 *
 * Implementation note on refraction: a physically refractive shell at this
 * radius (200+ units, covering the whole frame) would need a full backdrop
 * render target every frame — drei's MeshTransmissionMaterial does exactly
 * that, and it is the single most expensive thing you can put on screen at
 * this scale. Spec §15 makes smoothness a release requirement and explicitly
 * says to reduce visual complexity before sacrificing motion stability, so the
 * distortion here is done *in* the shell: a fresnel-weighted, noise-warped
 * interference field with chromatic separation across the rim, which reads as
 * a refracting volume in motion at a fraction of the cost.
 */

import { useRef } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import { beatProgress, clamp01 } from "../config/motion"
import { frame } from "../state/landingStore"
import { MONOLITH_BASE } from "./Terrain"
import { SIMPLEX_3D } from "./shaders/noise"
import { useMutable, liveUniforms } from "./useMutable"

/** Published so rocks/chains can ask "has the wave reached me yet?" */
export const waveState = { radius: 0, strength: 0, origin: new THREE.Vector3() }

const MAX_RADIUS = 230

export function SphericalEnergyWave() {
  const shell = useRef<THREE.Mesh>(null)
  const inner = useRef<THREE.Mesh>(null)
  const light = useRef<THREE.PointLight>(null)
  const shellMat = useRef<THREE.ShaderMaterial>(null)
  const innerMat = useRef<THREE.ShaderMaterial>(null)

  const uniforms = useMutable(
    () => ({
      uTime: { value: 0 },
      uProgress: { value: 0 },
      uStrength: { value: 0 },
      uColor: { value: new THREE.Color("#2f8dff") },
      uHot: { value: new THREE.Color("#bfe6ff") },
    }))

  const innerUniforms = useMutable(
    () => ({
      uTime: { value: 0 },
      uProgress: { value: 0 },
      uStrength: { value: 0 },
    }))

  useFrame((state, dt) => {
    if (!frame.visible) return
    const t = frame.introProgress
    const p = beatProgress(t, "energyWave")

    // Expands fast then decelerates — an emitted shell losing energy, not a
    // linearly scaling sphere.
    const radius = Math.pow(p, 0.68) * MAX_RADIUS
    // Brightest just after emission, gone by the time it leaves the world.
    const strength = Math.sin(Math.PI * clamp01(p)) * 1.25

    waveState.radius = radius
    waveState.strength = strength
    waveState.origin.set(MONOLITH_BASE.x, 6, MONOLITH_BASE.z)

    const active = p > 0.0005 && p < 0.999

    const su = liveUniforms(shellMat, uniforms)
    su.uTime.value += dt
    su.uProgress.value = p
    su.uStrength.value = strength

    const iu = liveUniforms(innerMat, innerUniforms)
    iu.uTime.value = su.uTime.value
    iu.uProgress.value = p
    iu.uStrength.value = strength

    if (shell.current) {
      shell.current.visible = active
      shell.current.scale.setScalar(Math.max(radius, 0.001))
      shell.current.position.copy(waveState.origin)
    }
    if (inner.current) {
      inner.current.visible = active
      // The trailing field lags the leading shell, giving the wave thickness.
      inner.current.scale.setScalar(Math.max(radius * 0.82, 0.001))
      inner.current.position.copy(waveState.origin)
    }
    if (light.current) {
      light.current.visible = active
      light.current.position.copy(waveState.origin)
      light.current.intensity = strength * 900
      light.current.distance = Math.max(radius * 1.35, 1)
    }

    // The wave is the cause of the environment awakening (spec §6.4), so it
    // also drives a real camera impulse as the front passes the camera.
    if (active) {
      const camDist = state.camera.position.distanceTo(waveState.origin)
      const passing = 1 - clamp01(Math.abs(camDist - radius) / 22)
      if (passing > 0.01) {
        frame.cameraImpulse = Math.max(frame.cameraImpulse, passing * 0.55)
      }
    }
  })

  const vertexShader = /* glsl */ `
    varying vec3 vNormalV;
    varying vec3 vViewDir;
    varying vec3 vPos;
    void main() {
      vPos = position;
      vNormalV = normalize(normalMatrix * normal);
      vec4 mv = modelViewMatrix * vec4(position, 1.0);
      vViewDir = normalize(-mv.xyz);
      gl_Position = projectionMatrix * mv;
    }
  `

  return (
    <group>
      {/* Leading shell */}
      <mesh ref={shell} visible={false} renderOrder={4}>
        <sphereGeometry args={[1, 96, 64]} />
        <shaderMaterial
          transparent
          depthWrite={false}
          side={THREE.DoubleSide}
          blending={THREE.AdditiveBlending}
          ref={shellMat}
          uniforms={uniforms}
          vertexShader={vertexShader}
          fragmentShader={/* glsl */ `
            uniform float uTime;
            uniform float uProgress;
            uniform float uStrength;
            uniform vec3  uColor;
            uniform vec3  uHot;
            varying vec3  vNormalV;
            varying vec3  vViewDir;
            varying vec3  vPos;
            ${SIMPLEX_3D}

            void main() {
              // Grazing angles are where a real refracting shell is visible;
              // face-on it should be nearly clear, which is what stops this
              // reading as a flat filled circle (spec §6.4).
              float fres = pow(1.0 - abs(dot(normalize(vNormalV), normalize(vViewDir))), 2.6);

              // Interference/caustic banding warped by noise, drifting over
              // the surface — the "distortion" cue.
              float warp = fbm(vPos * 3.2 + vec3(0.0, uTime * 0.35, 0.0));
              float bands = sin(vPos.y * 26.0 + warp * 7.0 - uTime * 5.0) * 0.5 + 0.5;
              bands = pow(bands, 3.5);

              // Chromatic separation across the rim: the three channels peak
              // at slightly different fresnel powers, so the edge splits into
              // colour the way a refracting surface does.
              vec3 chroma = vec3(
                pow(fres, 2.2),
                pow(fres, 2.8),
                pow(fres, 3.6)
              );

              vec3 col = uColor * fres * 1.25;
              col += uHot * bands * fres * 0.9;
              col += chroma * uColor * 0.55;

              // A hot leading edge for the first part of the expansion only.
              col += uHot * pow(fres, 6.0) * (1.0 - smoothstep(0.0, 0.35, uProgress)) * 2.0;

              float a = (fres * 0.75 + bands * fres * 0.5) * uStrength;
              gl_FragColor = vec4(col * uStrength, clamp(a, 0.0, 1.0));
            }
          `}
        />
      </mesh>

      {/* Trailing field — softer, gives the shell volume rather than skin. */}
      <mesh ref={inner} visible={false} renderOrder={3}>
        <sphereGeometry args={[1, 48, 32]} />
        <shaderMaterial
          transparent
          depthWrite={false}
          side={THREE.BackSide}
          blending={THREE.AdditiveBlending}
          ref={innerMat}
          uniforms={innerUniforms}
          vertexShader={vertexShader}
          fragmentShader={/* glsl */ `
            uniform float uTime;
            uniform float uStrength;
            varying vec3 vNormalV;
            varying vec3 vViewDir;
            varying vec3 vPos;
            ${SIMPLEX_3D}
            void main() {
              float fres = pow(1.0 - abs(dot(normalize(vNormalV), normalize(vViewDir))), 3.4);
              float n = fbm(vPos * 2.0 - vec3(0.0, uTime * 0.5, 0.0)) * 0.5 + 0.5;
              vec3 col = vec3(0.12, 0.42, 0.95) * (fres * 0.9 + n * 0.25);
              gl_FragColor = vec4(col * uStrength, fres * 0.4 * uStrength);
            }
          `}
        />
      </mesh>

      <pointLight ref={light} color="#5aa8ff" decay={2} intensity={0} visible={false} />
    </group>
  )
}

/** Has the expanding front already swept past this world position? */
export function waveReached(worldPos: THREE.Vector3): number {
  if (waveState.radius <= 0) return 0
  const d = worldPos.distanceTo(waveState.origin)
  // A soft 18-unit front, so a cluster of rocks doesn't flip on in one frame.
  return clamp01((waveState.radius - d) / 18)
}

/** How strongly the front is passing this point *right now*. */
export function waveFront(worldPos: THREE.Vector3): number {
  if (waveState.strength <= 0) return 0
  const d = worldPos.distanceTo(waveState.origin)
  return clamp01(1 - Math.abs(waveState.radius - d) / 20) * waveState.strength
}
