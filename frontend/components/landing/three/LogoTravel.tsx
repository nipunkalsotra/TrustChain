"use client"

/**
 * Logo emergence -> navbar brand (spec §6.7).
 *
 * "After the chain/energy sequence stabilizes, a holographic/energy copy of the
 * TrustChain emblem separates from the monolith core. The copy follows a
 * curved, elegant trajectory to the top-left corner. During travel it scales
 * down and resolves into the navbar brand... The monolith core remains glowing
 * after the navbar brand has formed."
 *
 * The trajectory is authored in SCREEN space, not world space, because its
 * destination is a DOM element — the navbar brand slot. A world-space path
 * would land in a different place at every viewport size and aspect ratio. So
 * the emblem's screen position is a quadratic bezier from the core's projected
 * position to the navbar anchor, unprojected back to a fixed distance in front
 * of the camera each frame. That keeps it a real 3D object (it is lit, it
 * blooms, it sits in the scene) while guaranteeing it arrives exactly where
 * the DOM brand will appear, on any screen.
 */

import { useRef } from "react"
import { useFrame, useThree } from "@react-three/fiber"
import * as THREE from "three"
import { beatProgress, clamp01, smoothstep } from "../config/motion"
import { frame } from "../state/landingStore"
import { monolithState } from "./Monolith"
import { useMutable } from "./useMutable"

/** Where the navbar brand sits, in normalized device coordinates. */
const NAVBAR_NDC = new THREE.Vector2(-0.845, 0.88)
/** Distance in front of the camera the travelling emblem is held at. */
const HOLD_DEPTH = 12

export function LogoTravel() {
  const { camera, size } = useThree()
  const ref = useRef<THREE.Group>(null)
  const coreNdc = useMutable(() => new THREE.Vector3())
  const p0 = useMutable(() => new THREE.Vector2())
  const p1 = useMutable(() => new THREE.Vector2())
  const p2 = useMutable(() => new THREE.Vector2())
  const cur = useMutable(() => new THREE.Vector2())
  const world = useMutable(() => new THREE.Vector3())

  useFrame((state) => {
    const g = ref.current
    if (!g || !frame.visible) return

    const t = frame.introProgress
    const p = beatProgress(t, "logoTravel")

    if (p <= 0 || p >= 1) {
      // Before the beat it does not exist; after it, the DOM navbar brand has
      // taken over and this must not linger on top of it.
      g.visible = false
      return
    }
    g.visible = true

    // Start: the monolith core's own projected position, so it genuinely
    // separates from the core rather than appearing near it.
    coreNdc.copy(monolithState.core).project(camera)
    p0.set(coreNdc.x, coreNdc.y)
    p2.copy(NAVBAR_NDC)

    // Control point placed above and outside the straight line, producing the
    // curved, elegant arc the spec asks for instead of a linear slide.
    p1.set(
      THREE.MathUtils.lerp(p0.x, p2.x, 0.45) + 0.18,
      Math.max(p0.y, p2.y) + 0.42,
    )

    // Ease so it leaves the core decisively and arrives softly.
    const e = smoothstep(p)
    const inv = 1 - e
    cur.set(
      inv * inv * p0.x + 2 * inv * e * p1.x + e * e * p2.x,
      inv * inv * p0.y + 2 * inv * e * p1.y + e * e * p2.y,
    )

    // Unproject the screen point back to a fixed depth in front of the camera.
    world.set(cur.x, cur.y, 0.5).unproject(camera)
    world.sub(camera.position).normalize().multiplyScalar(HOLD_DEPTH).add(camera.position)
    g.position.copy(world)
    g.quaternion.copy(camera.quaternion)

    // Scales down toward the navbar mark's real size. The world-space scale
    // needed for a given on-screen size depends on FOV and viewport height,
    // so it is derived rather than hardcoded — otherwise the handoff to the
    // DOM brand only lines up at one window size.
    const fovRad = ((camera as THREE.PerspectiveCamera).fov * Math.PI) / 180
    const worldPerPixel = (2 * Math.tan(fovRad / 2) * HOLD_DEPTH) / size.height
    const startPx = 190
    const endPx = 34
    const px = THREE.MathUtils.lerp(startPx, endPx, smoothstep(clamp01((p - 0.1) / 0.85)))
    g.scale.setScalar(px * worldPerPixel)

    // Holographic flicker on departure, settling as it resolves.
    const mat = (g.children[0] as THREE.Mesh)?.material as THREE.ShaderMaterial | undefined
    if (mat?.uniforms) {
      mat.uniforms.uTime.value = state.clock.elapsedTime
      mat.uniforms.uResolve.value = e
    }
  })

  return (
    <group ref={ref} visible={false} renderOrder={10}>
      <mesh>
        <planeGeometry args={[1, 1]} />
        <shaderMaterial
          transparent
          depthTest={false}
          depthWrite={false}
          uniforms={{ uTime: { value: 0 }, uResolve: { value: 0 } }}
          vertexShader={/* glsl */ `
            varying vec2 vUv;
            void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }
          `}
          fragmentShader={/* glsl */ `
            uniform float uTime;
            uniform float uResolve;
            varying vec2 vUv;

            // Same emblem SDF as the monolith core, so this is recognisably a
            // *copy* of that mark rather than a second, different logo.
            float shield(vec2 p){
              p.y *= 1.12;
              float top = length(vec2(p.x, max(p.y - 0.06, 0.0))) - 0.60;
              float bot = abs(p.x)*1.34 + max(-p.y-0.10,0.0)*1.5 - 0.62;
              return max(top, bot);
            }
            float diamond(vec2 p){ return abs(p.x)+abs(p.y)-0.24; }

            void main(){
              vec2 p = (vUv - 0.5) * 2.0;
              float d = shield(p);
              float ring = (1.0 - smoothstep(0.0, 0.05, abs(d)));
              float fill = (1.0 - smoothstep(-0.16, 0.02, d));
              float dia = (1.0 - smoothstep(0.0, 0.035, abs(diamond(p))));

              vec3 cyan = vec3(0.31,0.78,1.0);
              vec3 hot  = vec3(0.86,0.96,1.0);
              vec3 col = cyan*ring*1.7 + cyan*fill*0.18 + hot*dia*1.5;

              // Holographic scanlines that settle out as it resolves into the
              // solid navbar brand.
              float scan = sin(vUv.y * 60.0 - uTime * 8.0) * 0.5 + 0.5;
              float holo = mix(0.55 + scan * 0.45, 1.0, uResolve);
              col *= holo;

              float a = clamp(ring + fill*0.3 + dia, 0.0, 1.0) * holo;
              gl_FragColor = vec4(col, a);
            }
          `}
        />
      </mesh>
    </group>
  )
}
