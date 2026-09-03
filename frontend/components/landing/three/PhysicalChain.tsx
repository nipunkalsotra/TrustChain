"use client"

/**
 * The approved reference chains (spec §6.6).
 *
 * NON-NEGOTIABLE CHAIN RULE, quoted from the spec: "Do not add extra
 * decorative chains. Recreate only the major chains visible in the approved
 * hero reference, with approximately the same directions, anchor-rock
 * relationships and final composition. Every visible desktop chain uses real
 * physics."
 *
 * There are therefore exactly four chains, one per entry in ANCHOR_ROCKS, and
 * that list is the single source of truth for the composition.
 *
 * Construction, per the spec's own bullets:
 *  - Each visible link is a real Rapier rigid body.
 *  - Adjacent links are connected with spherical joints.
 *  - Colliders are simple (a ball) even though the visible mesh is a torus.
 *  - Sleeping is enabled so settled chains cost nothing.
 *  - Chains deploy sequentially, not all at once.
 *  - A guided kinematic lead link carries each chain to its lock point so it
 *    arrives reliably instead of flailing.
 *  - After deployment all links stay physically active and interactive in
 *    BOTH cinematic and normal modes.
 */

import { useMemo, useRef } from "react"
import { useFrame, useThree } from "@react-three/fiber"
import {
  RigidBody,
  BallCollider,
  useSphericalJoint,
  type RapierRigidBody,
} from "@react-three/rapier"
import * as THREE from "three"
import { BEATS, clamp01, smoothstep, easeHeavy } from "../config/motion"
import { frame } from "../state/landingStore"
import { ANCHOR_ROCKS, rockPositions } from "./FloatingRocks"
import { monolithState } from "./Monolith"
import { MONOLITH_BASE } from "./Terrain"
import {
  grab,
  beginGrab,
  endGrab,
  updateGrabTarget,
  decayTension,
  GRAB_MAX_IMPULSE,
  GRAB_MAX_VELOCITY,
} from "./ChainGrabController"
import { useMutable } from "./useMutable"

/**
 * Rapier's RigidBodyType values. Declared here rather than imported from
 * @dimforge/rapier3d-compat so this module keeps its single dependency on
 * @react-three/rapier's own surface.
 */
const DYNAMIC = 0
const KINEMATIC_POSITION = 2
const ZERO = { x: 0, y: 0, z: 0 }


/** Monolith-local attachment heights, one per anchor rock. */
const ATTACH_LOCAL: [number, number, number][] = [
  [-1.9, 24.0, 1.9],
  [-2.4, 13.5, 1.6],
  [2.2, 27.0, 1.4],
  [2.6, 10.0, 2.0],
]

// ── Shared, reused geometry/material: 4 chains x ~22 links is ~88 meshes, and
// they must not each allocate their own.
function useChainAssets() {
  return useMemo(() => {
    const mat = new THREE.MeshStandardMaterial({
      color: "#59667d",
      roughness: 0.30,
      metalness: 1.0,
      // Just enough self-light that the chains stay readable against the dark
      // sky where no key light reaches them — they are iron catching the
      // monolith's glow, not light sources.
      emissive: new THREE.Color("#123c66"),
      emissiveIntensity: 0.30,
    })
    return { mat }
  }, [])
}

/**
 * A link's visible ring, sized from the spacing of the chain it belongs to.
 *
 * Link size cannot be a constant: spacing is derived per chain from the real
 * distance to that chain's anchor rock, so a fixed radius produced a row of
 * clearly separated rings on the longer chains — visually a dotted line, not a
 * chain. Tying the radius to the spacing keeps adjacent links overlapping and
 * interlocking however far the chain has to span.
 */
function chainLinkGeometry(spacing: number) {
  const radius = spacing * 0.62
  return new THREE.TorusGeometry(radius, radius * 0.26, 8, 20)
}

interface LinkProps {
  chainId: string
  index: number
  prev: React.RefObject<RapierRigidBody | null>
  self: React.RefObject<RapierRigidBody | null>
  position: [number, number, number]
  spacing: number
  radius: number
  geom: THREE.TorusGeometry
  mat: THREE.MeshStandardMaterial
  /** Head and tail are kinematic — they are the two lock points. */
  kinematic: boolean
}

/**
 * A link's visible mesh and rigid body. Shared by the head link and the rest;
 * only the joint differs, which is why the head is a separate component below
 * rather than a conditional hook call.
 */
function LinkBody({
  chainId,
  index,
  self,
  position,
  radius,
  geom,
  mat,
  kinematic,
}: Omit<LinkProps, "prev" | "spacing">) {
  const { camera } = useThree()
  const id = `${chainId}:${index}`

  return (
    <RigidBody
      ref={self}
      position={position}
      // Every link is BORN kinematic and is promoted to dynamic only once its
      // chain has finished deploying (see Chain's frame loop). Starting them
      // dynamic meant 22 jointed bodies were being simulated from the very
      // first frame — while the monolith they hang from was still 35 units
      // underground and rising — so the solver spent the whole opening
      // fighting a violently forced constraint and the chains whipped apart.
      type="kinematicPosition"
      colliders={false}
      canSleep
      linearDamping={0.9}
      angularDamping={1.1}
      // Real mass — spec §6.3/§6.6 want the chains to feel heavy enough that a
      // lock impact lands and a pull tilts the monolith.
      density={9}
    >
      {/* Simple collider, detailed mesh (spec §6.6). A ball is deliberately
          smaller than the visible torus: neighbouring links must be able to
          overlap and interlock rather than shoving each other apart. */}
      <BallCollider args={[radius * 0.45]} />
      <mesh
        geometry={geom}
        material={mat}
        castShadow
        // Alternating quarter-turn is what makes a row of tori read as a chain.
        rotation={index % 2 === 0 ? [0, 0, 0] : [0, Math.PI / 2, 0]}
        onPointerDown={(e) => {
          if (kinematic) return // the lock points are not grabbable
          e.stopPropagation()
          beginGrab(id, e.point.clone(), camera)
        }}
        onPointerUp={() => {
          if (grab.activeId === id) endGrab()
        }}
      />
    </RigidBody>
  )
}

/**
 * The head link. No joint: it is kinematic and its position is written every
 * frame from the monolith's attach point, so a joint would have nothing to
 * solve.
 *
 * This exists as its own component purely so the joint hook can be
 * unconditional in `Link`. The previous shape — one component that jointed
 * `prev ?? self` — meant the head link was jointed to ITSELF, which Rapier has
 * no sane answer for and which was one of two reasons the chains came apart.
 */
function HeadLink(props: Omit<LinkProps, "prev" | "spacing">) {
  return <LinkBody {...props} />
}

function Link({ prev, self, spacing, ...rest }: LinkProps) {
  // Adjacent links are joined at their touching rims, not their centres, so
  // the chain reads as interlocking rings rather than beads on a string.
  useSphericalJoint(
    prev as React.RefObject<RapierRigidBody>,
    self as React.RefObject<RapierRigidBody>,
    [
      [0, 0, -spacing / 2],
      [0, 0, spacing / 2],
    ],
  )

  return <LinkBody self={self} {...rest} />
}

interface ChainProps {
  chainId: string
  anchorId: string
  attachLocal: [number, number, number]
  /** Resting world position of the anchor rock once the wave has lifted it. */
  restTail: [number, number, number]
  links: number
  /** Normalized [start, end] of this chain's slot in the deployment beat. */
  window: [number, number]
  mat: THREE.MeshStandardMaterial
}

function Chain({
  chainId,
  anchorId,
  attachLocal,
  restTail,
  links,
  window: win,
  mat,
}: ChainProps) {
  const { camera, pointer } = useThree()

  // Stable ref identities for every link, allocated once.
  const refs = useMutable(() =>
    Array.from({ length: links }, () => ({ current: null as RapierRigidBody | null })),
  )

  const group = useRef<THREE.Group>(null)
  const head = useMutable(() => new THREE.Vector3())
  const tail = useMutable(() => new THREE.Vector3())
  const lead = useMutable(() => new THREE.Vector3())
  const tmp = useMutable(() => new THREE.Vector3())
  const deployed = useRef(false)
  /** True once the middle links have been handed to the solver. */
  const dynamic = useRef(false)

  /**
   * Link spacing is derived from the distance this chain actually has to span,
   * not fixed. A constant spacing meant a chain whose anchor rock happened to
   * be further away than `links * spacing` was born over-stretched: every
   * joint permanently violated, the solver fighting itself, and the links
   * flung apart. Sizing from the real rest distance with 18% slack gives every
   * chain a natural hang instead.
   */
  const { spacing, initial } = useMutable(() => {
    const h = new THREE.Vector3(...attachLocal).add(MONOLITH_BASE)
    const t = new THREE.Vector3(...restTail)
    const dist = h.distanceTo(t)
    const sp = (dist * 1.18) / Math.max(links - 1, 1)
    // Lay the links out ALONG the span they will occupy, with a catenary sag.
    // Starting them all stacked at one point (the previous behaviour) asks the
    // solver to separate N coincident bodies on frame one, which is exactly
    // how a physics chain explodes.
    const pts: [number, number, number][] = []
    const v = new THREE.Vector3()
    for (let i = 0; i < links; i++) {
      const f = i / Math.max(links - 1, 1)
      v.lerpVectors(h, t, f)
      v.y -= Math.sin(f * Math.PI) * dist * 0.09
      pts.push([v.x, v.y, v.z])
    }
    return { spacing: sp, initial: pts }
  })

  const linkGeom = useMemo(() => chainLinkGeometry(spacing), [spacing])
  const linkRadius = spacing * 0.62

  useFrame((state, dt) => {
    if (!frame.visible) return

    const t = frame.introProgress
    const p = clamp01((t - win[0]) / Math.max(win[1] - win[0], 1e-4))
    const started = t >= win[0]

    if (group.current) group.current.visible = started
    if (!started) return

    // ── Lock points ────────────────────────────────────────────────────────
    if (monolithState.group) {
      monolithState.group.localToWorld(head.set(...attachLocal))
    } else {
      head.set(...attachLocal).add(MONOLITH_BASE)
    }
    const rock = rockPositions.get(anchorId)
    if (rock) tail.copy(rock)
    else tail.set(...restTail)

    const headBody = refs[0].current
    const tailBody = refs[links - 1].current
    if (!headBody || !tailBody) return

    // Head is permanently locked to the monolith.
    headBody.setNextKinematicTranslation(head)

    // ── Sequential deployment (spec §6.6) ─────────────────────────────────
    // "A guided lead-link/kinematic attractor can be used during deployment so
    // a physical chain reliably travels toward its lock point without becoming
    // chaotic." Taken literally: for the length of the deployment window the
    // WHOLE chain is kinematic and laid out along the travelling arc, so it
    // arrives in a clean, already-valid configuration. Only then is it handed
    // to the solver — which is what the spec's next bullet requires: "After
    // deployment, all links remain physically active and interactive."
    if (p < 1) {
      const e = easeHeavy(p)
      lead.lerpVectors(head, tail, e)
      // Arc: the chain is thrown outward and drops onto its anchor, giving the
      // lock a heavy, distinct arrival rather than a straight-line slide.
      lead.y += Math.sin(e * Math.PI) * 4.5
      lead.z += Math.sin(e * Math.PI) * 1.2

      const span = head.distanceTo(lead)
      for (let i = 0; i < links; i++) {
        const b = refs[i].current
        if (!b) continue
        const f = i / Math.max(links - 1, 1)
        tmp.lerpVectors(head, lead, f)
        tmp.y -= Math.sin(f * Math.PI) * span * 0.11
        b.setNextKinematicTranslation(tmp)
      }
      if (dynamic.current) {
        // Scrubbed backwards into the deployment window: hand the links back
        // to the attractor so replaying the deployment is as stable as the
        // first time.
        for (let i = 1; i < links - 1; i++) {
          refs[i].current?.setBodyType(KINEMATIC_POSITION, true)
        }
        dynamic.current = false
      }
      deployed.current = false
    } else {
      tailBody.setNextKinematicTranslation(tail)

      if (!dynamic.current) {
        // Promote the middle links exactly once, from the settled kinematic
        // pose, with velocities zeroed — otherwise they inherit the
        // deployment sweep as an impulse and the chain snaps taut.
        for (let i = 1; i < links - 1; i++) {
          const b = refs[i].current
          if (!b) continue
          b.setBodyType(DYNAMIC, true)
          b.setLinvel(ZERO, true)
          b.setAngvel(ZERO, true)
        }
        dynamic.current = true
      }

      if (!deployed.current) {
        deployed.current = true
        // "Each lock impact should feel heavy and distinct" (spec §6.6) — a
        // real camera impulse, on the frame the chain lands.
        frame.cameraImpulse = Math.max(frame.cameraImpulse, 0.32)
      }
    }

    // ── Cursor grab (spec §7.1 steps 4-6) ─────────────────────────────────
    updateGrabTarget(camera, pointer as THREE.Vector2)
    if (dynamic.current && grab.activeId?.startsWith(`${chainId}:`)) {
      const idx = Number(grab.activeId.split(":")[1])
      const body = refs[idx]?.current
      if (body) {
        const pos = body.translation()
        tmp.set(grab.target.x - pos.x, grab.target.y - pos.y, grab.target.z - pos.z)

        // Critically damped spring: proportional pull minus a velocity term.
        const vel = body.linvel()
        const stiffness = 28
        const damping = 5.5
        tmp.multiplyScalar(stiffness * dt)
        tmp.x -= vel.x * damping * dt
        tmp.y -= vel.y * damping * dt
        tmp.z -= vel.z * damping * dt

        // Hard impulse ceiling — the single most important clamp for keeping
        // the solver stable under a fast cursor (spec §7.1 step 6).
        if (tmp.length() > GRAB_MAX_IMPULSE) tmp.setLength(GRAB_MAX_IMPULSE)

        body.wakeUp()
        body.applyImpulse(tmp, true)

        // Velocity ceiling as a second line of defence.
        const speed = Math.hypot(vel.x, vel.y, vel.z)
        if (speed > GRAB_MAX_VELOCITY) {
          const k = GRAB_MAX_VELOCITY / speed
          body.setLinvel({ x: vel.x * k, y: vel.y * k, z: vel.z * k }, true)
        }
      }
    }

    decayTension(dt)
    void state
  })

  return (
    <group ref={group} visible={false}>
      {Array.from({ length: links }, (_, i) =>
        i === 0 ? (
          <HeadLink
            key={i}
            chainId={chainId}
            index={i}
            self={refs[i]}
            position={initial[i]}
            radius={linkRadius}
            geom={linkGeom}
            mat={mat}
            kinematic
          />
        ) : (
          <Link
            key={i}
            chainId={chainId}
            index={i}
            self={refs[i]}
            prev={refs[i - 1]}
            position={initial[i]}
            spacing={spacing}
            radius={linkRadius}
            geom={linkGeom}
            mat={mat}
            kinematic={i === links - 1}
          />
        ),
      )}
    </group>
  )
}

/**
 * Kinematic fallback used on low-tier devices (spec §18: "simplify chain
 * detail, particles, shadows and camera behavior as required" on mobile).
 * Renders the same four chains as catenary curves — same composition, same
 * anchor relationships, no solver.
 */
function KinematicChains({ links }: { links: number }) {
  const group = useRef<THREE.Group>(null)
  const { mat } = useChainAssets()
  const geom = useMemo(() => chainLinkGeometry(1.2), [])
  const head = useMutable(() => new THREE.Vector3())
  const tail = useMutable(() => new THREE.Vector3())
  const p = useMutable(() => new THREE.Vector3())

  useFrame((state) => {
    if (!group.current || !frame.visible) return
    const t = frame.introProgress
    let child = 0
    ANCHOR_ROCKS.forEach((rock, ci) => {
      const win: [number, number] = [
        BEATS.chains[0] + ci * 0.022,
        BEATS.chains[0] + ci * 0.022 + 0.045,
      ]
      const prog = smoothstep(clamp01((t - win[0]) / (win[1] - win[0])))
      if (monolithState.group) monolithState.group.localToWorld(head.set(...ATTACH_LOCAL[ci]))
      const rp = rockPositions.get(rock.id)
      if (rp) tail.copy(rp)

      for (let i = 0; i < links; i++, child++) {
        const mesh = group.current!.children[child] as THREE.Mesh
        if (!mesh) continue
        const f = i / (links - 1)
        p.lerpVectors(head, tail, f * prog)
        // Catenary sag, plus a gentle sway so it is not a dead line.
        p.y -= Math.sin(f * Math.PI) * 1.6 * prog
        p.y += Math.sin(state.clock.elapsedTime * 0.8 + f * 3.0) * 0.06 * prog
        mesh.position.copy(p)
        mesh.rotation.set(0, i % 2 ? Math.PI / 2 : 0, Math.PI / 2)
        mesh.visible = prog > 0.01
      }
    })
  })

  return (
    <group ref={group}>
      {ANCHOR_ROCKS.flatMap((_, ci) =>
        Array.from({ length: links }, (__, i) => (
          <mesh key={`${ci}-${i}`} geometry={geom} material={mat} visible={false} />
        )),
      )}
    </group>
  )
}

export function PhysicalChains({
  links,
  physics,
}: {
  links: number
  physics: boolean
}) {
  const { mat } = useChainAssets()

  if (!physics) return <KinematicChains links={links} />

  return (
    <group>
      {ANCHOR_ROCKS.map((rock, i) => (
        <Chain
          key={rock.id}
          chainId={rock.id}
          anchorId={rock.id}
          attachLocal={ATTACH_LOCAL[i]}
          // Where the rock ends up once the wave has lifted it — the span the
          // chain must actually be built to cover.
          restTail={[rock.base[0], rock.base[1] + rock.lift, rock.base[2]]}
          links={links}
          // Sequential slots inside the chains beat — "not all at once".
          window={[BEATS.chains[0] + i * 0.022, BEATS.chains[0] + i * 0.022 + 0.045]}
          mat={mat}
        />
      ))}
    </group>
  )
}
