"use client"

/**
 * Postprocessing (spec §15).
 *
 * "Postprocessing should be restrained: controlled bloom, fog/atmosphere,
 * optional SSAO, very subtle vignette/noise; no overblown gaming filter."
 *
 * SSAO is deliberately omitted rather than made optional-on: it is the most
 * expensive pass here, the ground is already dark and heavily textured so it
 * contributes almost nothing visible, and spec §15 makes frame stability a
 * release requirement that outranks it. Fog/atmosphere is handled in the scene
 * itself (real THREE.FogExp2) rather than as a screen-space pass, which keeps
 * it correct through the camera's travel between distant worlds.
 *
 * Bloom threshold is set high on purpose: only the emissive core, the energy
 * wave and the chain highlights should bloom. A low threshold blooms the
 * ground and turns a cinematic night scene into haze.
 */

import { EffectComposer, Bloom, Vignette, Noise } from "@react-three/postprocessing"
import { BlendFunction, KernelSize } from "postprocessing"

export function HeroPostProcessing({ enabled }: { enabled: boolean }) {
  if (!enabled) return null

  return (
    <EffectComposer
      // Depth is not needed by any pass here; skipping it avoids an extra
      // render target on every frame.
      enableNormalPass={false}
      multisampling={0}
    >
      <Bloom
        intensity={0.85}
        luminanceThreshold={0.62}
        luminanceSmoothing={0.22}
        kernelSize={KernelSize.LARGE}
        mipmapBlur
      />
      <Vignette offset={0.28} darkness={0.62} blendFunction={BlendFunction.NORMAL} />
      {/* Very low-amplitude grain: kills banding in the large dark gradients
          without reading as a film-grain effect. */}
      <Noise premultiply blendFunction={BlendFunction.OVERLAY} opacity={0.045} />
    </EffectComposer>
  )
}
