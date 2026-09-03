"use client"

import { useRef } from "react"

/**
 * A lazily-created, genuinely mutable per-instance value.
 *
 * Every frame loop in this scene mutates long-lived objects in place — shader
 * uniform records, scratch Vector3s, an Object3D used to compose instance
 * matrices. That is not an optimization detail, it is the requirement: spec
 * §15 says "Never update high-frequency animation state through React
 * setState on every frame. Use refs, frame loops and physics engine state",
 * and allocating a Vector3 per frame per particle is exactly the garbage
 * pressure that produces the stutter spec §15 forbids.
 *
 * `useMemo` is the wrong tool for these even though it caches correctly: the
 * React Compiler treats a memoized value as immutable and its lint rules
 * reject writing to one after render, which is right — memo results are
 * conceptually derived-and-frozen, and React is free to discard and recompute
 * them. `useRef` is the sanctioned container for mutable instance state, and
 * it is also a stronger guarantee than useMemo: a ref is never dropped.
 *
 * The factory runs exactly once per component instance, on first render.
 */
export function useMutable<T extends object>(factory: () => T): T {
  const ref = useRef<T | null>(null)
  if (ref.current === null) ref.current = factory()
  return ref.current
}

/**
 * The live uniforms object a ShaderMaterial is ACTUALLY rendering with.
 *
 * This is not a convenience wrapper — it fixes a genuine, silent trap. When you
 * write `<shaderMaterial uniforms={myUniforms} />`, the material does not adopt
 * `myUniforms`; it ends up holding its own object. Mutating the one you created
 * in a frame loop therefore updates nothing, the shader runs forever with its
 * initial values, and there is no error, no warning and no visual clue beyond
 * "the effect never animates" — which reads like a maths bug in the shader
 * rather than a plumbing bug outside it. Every custom shader on this page was
 * frozen at its initial state because of exactly this: the monolith never
 * ignited, the energy wave never expanded, the rocks never charged.
 *
 * So: keep a ref on the material, and route every per-frame write through this.
 * The `fallback` covers the first frame or two before the ref is populated.
 *
 *   const mat = useRef<THREE.ShaderMaterial>(null)
 *   const uniforms = useMutable(() => ({ uTime: { value: 0 } }))
 *   useFrame(() => { liveUniforms(mat, uniforms).uTime.value = t })
 *   ...
 *   <shaderMaterial ref={mat} uniforms={uniforms} ... />
 */
export function liveUniforms<T extends Record<string, { value: unknown }>>(
  ref: { current: { uniforms?: Record<string, { value: unknown }> } | null },
  fallback: T,
): T {
  const owned = ref.current?.uniforms
  return (owned as T | undefined) ?? fallback
}
