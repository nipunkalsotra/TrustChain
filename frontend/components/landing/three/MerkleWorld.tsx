"use client"

/**
 * Merkle Anchored world (spec §12.5).
 *
 * "Build a large 3D Merkle tree from leaf nodes upward as the user scrolls.
 * Connections draw/light as parent hashes are formed. The user should visually
 * understand many records collapsing into one root without needing a long
 * explanation. At the end, the tree collapses visually into a single bright
 * Merkle-root object."
 *
 * The tree is built bottom-up from the leaf count in the quality budget, so
 * the low tier shows a genuinely smaller tree rather than the same tree with
 * clipped geometry. Levels light in order, which is what carries the "many
 * collapse into one" reading without any accompanying text.
 */

import { useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import * as THREE from "three"
import { clamp01, smoothstep } from "../config/motion"
import { frame } from "../state/landingStore"
import { worldEnter, worldProgress } from "./WorldSlot"
import { useMutable } from "./useMutable"

interface Node {
  x: number
  y: number
  level: number
  /** Index of parent in the flat node list, or -1 for the root. */
  parent: number
}

function buildTree(leaves: number) {
  const levels = Math.log2(leaves)
  const nodes: Node[] = []
  const levelStart: number[] = []
  const LEVEL_H = 4.6
  const LEAF_W = 2.6

  let widthCount = leaves
  for (let lv = 0; lv <= levels; lv++) {
    levelStart[lv] = nodes.length
    for (let i = 0; i < widthCount; i++) {
      nodes.push({
        x: (i - (widthCount - 1) / 2) * LEAF_W * Math.pow(2, lv),
        y: lv * LEVEL_H,
        level: lv,
        parent: -1,
      })
    }
    widthCount /= 2
  }
  // Wire children to parents.
  widthCount = leaves
  for (let lv = 0; lv < levels; lv++) {
    for (let i = 0; i < widthCount; i++) {
      nodes[levelStart[lv] + i].parent = levelStart[lv + 1] + Math.floor(i / 2)
    }
    widthCount /= 2
  }
  return { nodes, levels }
}

export function MerkleWorld({ leaves = 16 }: { leaves?: number }) {
  const group = useRef<THREE.Group>(null)
  const nodeMesh = useRef<THREE.InstancedMesh>(null)
  const rootRef = useRef<THREE.Mesh>(null)
  const dummy = useMutable(() => new THREE.Object3D())
  const color = useMutable(() => new THREE.Color())

  const { nodes, levels } = useMemo(() => buildTree(leaves), [leaves])

  // Edge geometry, rebuilt only when the tree shape changes. Positions are
  // static; only their vertex colours animate, so this is one draw call for
  // the whole set of connections.
  const edges = useMemo(() => {
    const g = new THREE.BufferGeometry()
    const pairs = nodes.filter((n) => n.parent >= 0)
    const pos = new Float32Array(pairs.length * 6)
    const col = new Float32Array(pairs.length * 6)
    pairs.forEach((n, i) => {
      const p = nodes[n.parent]
      pos.set([n.x, n.y, 0, p.x, p.y, 0], i * 6)
    })
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3))
    g.setAttribute("color", new THREE.BufferAttribute(col, 3))
    return { geometry: g, pairs }
  }, [nodes])

  useFrame((state) => {
    const g = group.current
    const nm = nodeMesh.current
    if (!g || !nm) return
    const time = state.clock.elapsedTime
    const enter = worldEnter("merkle")
    const through = worldProgress("merkle")

    // Levels light in order from the leaves upward.
    const built = enter * (levels + 1)

    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i]
      const lv = smoothstep(clamp01(built - n.level))
      dummy.position.set(n.x, n.y - 8, 0)
      const s = 0.55 + n.level * 0.22
      dummy.scale.setScalar(s * lv)
      dummy.rotation.set(0, time * 0.2 + i, 0)
      dummy.updateMatrix()
      nm.setMatrixAt(i, dummy.matrix)

      // Parents are brighter than leaves — the eye follows the collapse upward.
      const heat = lv * (0.35 + (n.level / Math.max(levels, 1)) * 0.9)
      color.setRGB(heat * 0.35, heat * 0.75, heat * 1.25)
      nm.setColorAt(i, color)
    }
    nm.instanceMatrix.needsUpdate = true
    if (nm.instanceColor) nm.instanceColor.needsUpdate = true

    // Connections draw as their parent hash forms.
    const colAttr = edges.geometry.getAttribute("color") as THREE.BufferAttribute
    edges.pairs.forEach((n, i) => {
      const lit = smoothstep(clamp01(built - n.level - 0.5))
      const pulse = 0.6 + 0.4 * Math.sin(time * 2.2 - n.level * 0.8)
      const v = lit * pulse
      colAttr.setXYZ(i * 2, v * 0.2, v * 0.6, v * 1.0)
      colAttr.setXYZ(i * 2 + 1, v * 0.35, v * 0.8, v * 1.2)
    })
    colAttr.needsUpdate = true

    // The root: activates last, then everything reads as collapsed into it.
    if (rootRef.current) {
      const rootLit = smoothstep(clamp01(built - levels))
      rootRef.current.position.set(0, levels * 4.6 - 8, 0)
      rootRef.current.scale.setScalar(0.001 + rootLit * (1.6 + Math.sin(time * 1.6) * 0.06))
      const mat = rootRef.current.material as THREE.MeshBasicMaterial
      mat.opacity = rootLit
    }

    // Spec §13.3: "Cursor can slightly rotate/parallax the tree." Restrained.
    g.rotation.y = frame.pointerX * 0.22 + (through - 0.5) * 0.25
    g.rotation.x = -frame.pointerY * 0.08
  })

  return (
    <group ref={group}>
      <lineSegments geometry={edges.geometry} position={[0, -8, 0]}>
        <lineBasicMaterial vertexColors transparent opacity={0.9} toneMapped={false} />
      </lineSegments>

      <instancedMesh ref={nodeMesh} args={[undefined, undefined, nodes.length]} frustumCulled={false}>
        <octahedronGeometry args={[1, 0]} />
        <meshBasicMaterial toneMapped={false} />
      </instancedMesh>

      <mesh ref={rootRef}>
        <icosahedronGeometry args={[1, 1]} />
        <meshBasicMaterial color="#cfeaff" transparent opacity={0} toneMapped={false} />
      </mesh>
      <pointLight color="#5ac8ff" intensity={50} distance={90} decay={2} position={[0, levels * 4.6 - 8, 6]} />
    </group>
  )
}
