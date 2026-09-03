"use client"

/**
 * The hero world: terrain, guardian, monolith, rocks, chains, dust, wave.
 *
 * Assembly only — every element owns its own choreography and reads the same
 * `frame.introProgress`. There is no central timeline object handing out
 * poses, which is what lets Quick View's 420ms fast-forward and a scrubbed
 * scroll both produce a correct world state with no separate code path
 * (spec §15.1).
 */

import { Physics } from "@react-three/rapier"
import { Terrain } from "./Terrain"
import { Character } from "./Character"
import { Monolith } from "./Monolith"
import { FloatingRocks } from "./FloatingRocks"
import { DustAndWind } from "./DustAndWind"
import { SphericalEnergyWave } from "./SphericalEnergyWave"
import { PhysicalChains } from "./PhysicalChain"
import { LogoTravel } from "./LogoTravel"
import type { QualityBudget } from "../config/performance"
import { useUiSelector } from "../state/landingStore"
import { MONOLITH_BASE } from "./Terrain"

export function HeroScene({ budget }: { budget: QualityBudget }) {
  const visible = useUiSelector((s) => s.activeSection === "hero" || !s.introAwakened)

  return (
    <group visible={visible}>
      {/* ── Lighting ────────────────────────────────────────────────────────
          A cold moonlit key plus a very low fill. Deliberately dim: the scene
          is supposed to be dark before the core ignites, so that the ignition
          is genuinely the moment the world lights up (spec §6.4). */}
      <hemisphereLight args={["#16233a", "#04060c", 0.55]} />
      <directionalLight
        position={[-24, 34, 18]}
        intensity={0.85}
        color="#8fb6e8"
        castShadow={budget.shadows}
        shadow-mapSize-width={1024}
        shadow-mapSize-height={1024}
        shadow-camera-left={-40}
        shadow-camera-right={40}
        shadow-camera-top={40}
        shadow-camera-bottom={-40}
        shadow-camera-far={120}
      />
      {/* Rim from behind the monolith, so its silhouette exists before it
          ignites. */}
      <directionalLight
        position={[MONOLITH_BASE.x + 10, 24, MONOLITH_BASE.z - 30]}
        intensity={0.4}
        color="#2f6fb0"
      />

      <Terrain segments={budget.tier === "low" ? 96 : budget.tier === "medium" ? 150 : 220} />
      <Character cloakDetail={budget.tier === "low" ? 8 : 14} />
      <Monolith />
      <FloatingRocks heroCount={budget.heroRockCount} instancedCount={budget.instancedRockCount} />
      <DustAndWind count={budget.dustCount} debris={budget.debrisCount} />
      <SphericalEnergyWave />
      <LogoTravel />

      {/* Chains: real physics on desktop, kinematic catenaries below that.
          Rapier is only mounted when it is actually going to be used — a
          paused-but-present physics world still costs a step allocation and a
          context every frame. */}
      {budget.physicsChains ? (
        <Physics
          gravity={[0, -9.81, 0]}
          // A jointed chain needs more solver passes than the default scene
          // would: constraint error accumulates along the chain, so too few
          // iterations show up as visible stretch at the far end.
          numSolverIterations={8}
          // FIXED timestep, deliberately. Under "vary" a slow frame hands
          // Rapier a large dt, the chain's joints overshoot, and the next
          // frame is slower still — a feedback loop that was flinging links
          // hundreds of units. A fixed step decouples simulation stability
          // from frame rate entirely, which is exactly the guarantee spec §15
          // asks for ("smoothness is a release requirement, not optional
          // polish"). Rapier interpolates between steps, so this costs
          // nothing visually.
          timeStep={1 / 60}
        >
          <PhysicalChains links={budget.chainLinks} physics />
        </Physics>
      ) : (
        <PhysicalChains links={budget.chainLinks} physics={false} />
      )}
    </group>
  )
}
