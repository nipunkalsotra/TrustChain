"use client"

/**
 * The single WebGL surface for the entire landing page.
 *
 * One fixed, full-viewport canvas sits behind all the DOM. It is never
 * unmounted and never remounted on a mode switch — spec §15.2: "Switching
 * cinematic -> normal must not remount the entire page if avoidable. Prefer
 * variant props/state." The mode is read inside frame loops, so the switch
 * costs nothing but a different target for the camera and a different
 * placement rule for the worlds.
 *
 * Accessibility (spec §18): the canvas must not block scrolling or trap focus.
 * It is therefore aria-hidden, not focusable, and uses `touch-action: pan-y`
 * so a touch drag still scrolls the page — R3F's default of `none` would
 * silently make the whole site unscrollable on a phone.
 */

import { useEffect, useMemo, useState } from "react"
import { Canvas } from "@react-three/fiber"
import { AdaptiveDpr, AdaptiveEvents, Preload } from "@react-three/drei"
import * as THREE from "three"

import { HeroScene } from "./HeroScene"
import { CinematicCamera } from "./CinematicCamera"
import { HeroPostProcessing } from "./HeroPostProcessing"
import { WorldSlot } from "./WorldSlot"
import { ProductWorld } from "./ProductWorld"
import { LedgerWorld } from "./LedgerWorld"
import { MerkleWorld } from "./MerkleWorld"
import { BlockchainWorld } from "./BlockchainWorld"
import { VerificationWorld } from "./VerificationWorld"
import { RealProductWorld } from "./RealProductWorld"
import { SecurityWorld } from "./SecurityWorld"

import { budgetFor, type Tier } from "../config/performance"
import { frame, setUi, useUiSelector } from "../state/landingStore"

function SceneReadySignal() {
  useEffect(() => {
    // One commit after the first render, the scene has produced a frame.
    const id = requestAnimationFrame(() => setUi({ sceneReady: true }))
    return () => cancelAnimationFrame(id)
  }, [])
  return null
}

/**
 * Warms every material's shader program — but NOT on mount.
 *
 * <Preload all /> compiles the whole scene in one synchronous block, and this
 * scene has a lot of custom shaders plus seven section worlds. On a modest GPU
 * that block runs for over a second, during which the main thread is frozen:
 * scroll input queues up, and a visitor who pressed ENTER in that window got a
 * Quick View resolve that visibly lagged (reproduced here — the 420ms resolve
 * was still unfinished 1.4s after the keypress).
 *
 * Deferring it to idle time gets the opening interactive immediately and moves
 * the compile into the seconds the visitor spends reading "SCROLL TO AWAKEN".
 * The alternative — no preload at all — just relocates the cost to a hitch the
 * first time each world scrolls into view, which spec §15 rules out ("Do not
 * ship a visually ambitious scene that stutters").
 */
function DeferredPreload() {
  const [warm, setWarm] = useState(false)

  useEffect(() => {
    type IdleWindow = Window & {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number
      cancelIdleCallback?: (id: number) => void
    }
    const w = window as IdleWindow
    if (w.requestIdleCallback) {
      const id = w.requestIdleCallback(() => setWarm(true), { timeout: 2500 })
      return () => w.cancelIdleCallback?.(id)
    }
    // Safari has no requestIdleCallback; a timeout is the same intent.
    const id = window.setTimeout(() => setWarm(true), 1200)
    return () => window.clearTimeout(id)
  }, [])

  return warm ? <Preload all /> : null
}

export function LandingCanvas({ tier, reducedMotion }: { tier: Tier; reducedMotion: boolean }) {
  const budget = useMemo(() => budgetFor(tier), [tier])
  const [running, setRunning] = useState(true)

  // Spec §15: "Pause/reduce nonessential simulation when the tab is hidden."
  // Switching frameloop to "never" stops the render loop entirely rather than
  // rendering frames nobody sees.
  useEffect(() => {
    const onVis = () => {
      const visible = document.visibilityState === "visible"
      frame.visible = visible
      setRunning(visible)
    }
    document.addEventListener("visibilitychange", onVis)
    return () => document.removeEventListener("visibilitychange", onVis)
  }, [])

  const mode = useUiSelector((s) => s.mode)

  return (
    <div
      aria-hidden="true"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 0,
        pointerEvents: "auto",
        // Touch drags scroll the page; pointer events still reach the chains
        // on desktop. Without this the canvas eats every touchmove.
        touchAction: "pan-y",
      }}
    >
      <Canvas
        frameloop={running ? "always" : "never"}
        dpr={budget.dpr}
        shadows={budget.shadows}
        gl={{
          antialias: budget.tier === "high",
          alpha: false,
          powerPreference: "high-performance",
          // Bloom needs headroom above 1.0 to pick out only the truly bright
          // emissives; clamping at the default would bloom everything lit.
          toneMapping: THREE.ACESFilmicToneMapping,
        }}
        camera={{ position: [0.6, 2, 27], fov: 42, near: 0.1, far: 3000 }}
        onCreated={({ gl, scene }) => {
          gl.setClearColor("#03060e", 1)
          // Real scene fog rather than a screen-space pass, so it stays
          // correct as the camera travels 1700 units between worlds.
          scene.fog = new THREE.FogExp2("#03060e", 0.0034)
        }}
        // No tab stop: the canvas is decorative, and a focusable canvas is
        // exactly the focus trap spec §18 forbids.
        tabIndex={-1}
      >
        <SceneReadySignal />
        <CinematicCamera reducedMotion={reducedMotion} />

        <HeroScene budget={budget} />

        <WorldSlot id="product">
          <ProductWorld />
        </WorldSlot>
        <WorldSlot id="immutable">
          <LedgerWorld />
        </WorldSlot>
        <WorldSlot id="merkle">
          <MerkleWorld leaves={budget.merkleLeaves} />
        </WorldSlot>
        <WorldSlot id="blockchain">
          <BlockchainWorld blocks={budget.blockchainBlocks} />
        </WorldSlot>
        <WorldSlot id="verification">
          <VerificationWorld />
        </WorldSlot>
        <WorldSlot id="realproduct">
          <RealProductWorld />
        </WorldSlot>
        <WorldSlot id="security">
          <SecurityWorld />
        </WorldSlot>

        {/* Postprocessing is skipped entirely on the low tier — it is the
            single largest fixed cost per frame (spec §15/§18). */}
        <HeroPostProcessing enabled={budget.postprocessing && !reducedMotion} />

        {/* Drops resolution under load and stops raycasting during motion,
            rather than letting the frame rate fall (spec §15). */}
        <AdaptiveDpr pixelated={false} />
        <AdaptiveEvents />
        <DeferredPreload />
        {/* mode is read here only so a switch invalidates this subtree's
            memo; the actual behaviour change happens inside the frame loops. */}
        <group name={`mode-${mode}`} />
      </Canvas>
    </div>
  )
}
