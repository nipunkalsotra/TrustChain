"use client"

/**
 * Icon-first expanding nav capsule for the plain landing page's center nav
 * (Product / Security / Docs / Pricing).
 *
 * Collapsed by default (icons only); the whole capsule expands to reveal
 * labels on hover OR keyboard focus of any item inside it, and waits briefly
 * before collapsing on pointer-leave so brief gaps between icons don't cause
 * flicker. State (expanded/not) is the only thing driven from React — the
 * reveal choreography itself (stagger, blur, per-item hover glow) is plain
 * CSS so there's no rAF loop or extra re-render on every frame.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { Layers, Shield, FileText, Tag, type LucideIcon } from "lucide-react"
import { NAV_LINKS, type NavLink } from "./content"
import "./nav-capsule.css"

const ICONS: Record<string, LucideIcon> = {
  product: Layers,
  security: Shield,
  docs: FileText,
  pricing: Tag,
}

/** Waits briefly before collapsing so the capsule doesn't feel twitchy. */
const COLLAPSE_DELAY_MS = 200

function scrollToSection(sectionId: string) {
  const el = document.getElementById(sectionId)
  if (!el) return
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches
  el.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" })
}

function NavItem({ link, index }: { link: NavLink; index: number }) {
  const Icon = ICONS[link.id]
  const content = (
    <>
      <Icon className="navcap-icon" size={18} strokeWidth={1.75} aria-hidden />
      <span className="navcap-label" style={{ transitionDelay: `${index * 35}ms` }}>
        {link.label}
      </span>
    </>
  )

  if (link.kind === "route") {
    return (
      <Link href={link.href!} className="navcap-item">
        {content}
      </Link>
    )
  }

  return (
    <button type="button" className="navcap-item" onClick={() => scrollToSection(link.sectionId!)}>
      {content}
    </button>
  )
}

export function NavCapsule() {
  const [expanded, setExpanded] = useState(false)
  const collapseTimer = useRef<number | null>(null)
  const capsuleRef = useRef<HTMLElement>(null)

  const open = useCallback(() => {
    if (collapseTimer.current) {
      window.clearTimeout(collapseTimer.current)
      collapseTimer.current = null
    }
    setExpanded(true)
  }, [])

  const scheduleClose = useCallback(() => {
    if (collapseTimer.current) window.clearTimeout(collapseTimer.current)
    collapseTimer.current = window.setTimeout(() => {
      // Never collapse out from under a keyboard user still inside it.
      if (capsuleRef.current?.contains(document.activeElement)) return
      setExpanded(false)
    }, COLLAPSE_DELAY_MS)
  }, [])

  useEffect(
    () => () => {
      if (collapseTimer.current) window.clearTimeout(collapseTimer.current)
    },
    [],
  )

  return (
    <nav
      ref={capsuleRef}
      className="navcap"
      data-expanded={expanded}
      aria-label="Primary"
      onPointerEnter={(e) => {
        // No hover on touch — the capsule is permanently expanded via CSS
        // at narrow/touch viewports, so ignore synthesized enters here.
        if (e.pointerType === "touch") return
        open()
      }}
      onPointerLeave={(e) => {
        if (e.pointerType === "touch") return
        scheduleClose()
      }}
      onFocusCapture={open}
      onBlurCapture={scheduleClose}
    >
      {NAV_LINKS.map((link, i) => (
        <NavItem key={link.id} link={link} index={i} />
      ))}
    </nav>
  )
}
