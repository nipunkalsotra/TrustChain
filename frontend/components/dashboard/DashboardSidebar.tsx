"use client"

import { usePathname } from "next/navigation"
import { SidebarNavItem } from "./SidebarNavItem"
import { TrustChainLogo } from "./Logo"
import { IconOverview, IconRuns, IconAgents, IconProofs, IconAnchors, IconSettings, IconDocs } from "./icons"

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard", icon: IconOverview },
  { href: "/dashboard/runs", label: "Runs", icon: IconRuns },
  { href: "/dashboard/agents", label: "Agents", icon: IconAgents },
  { href: "/dashboard/proofs", label: "Proofs", icon: IconProofs },
  { href: "/dashboard/anchors", label: "Anchors", icon: IconAnchors },
  { href: "/dashboard/settings", label: "Settings", icon: IconSettings },
  // No /docs route exists yet — links to the app's existing Coming Soon
  // page (same one components/marketing/content.ts's own Docs link uses)
  // rather than a dead "#", but is otherwise a completely normal nav item.
  { href: "/coming-soon?from=docs", label: "Docs", icon: IconDocs },
] as const

// "Dashboard" (the /dashboard home route itself) is active on its own route
// only (not as a prefix — every other dashboard route would otherwise also
// light it up), everything else is active on itself and its own sub-routes
// (e.g. /dashboard/runs/abc123 keeps "Runs" highlighted). Docs never
// matches — it points off the /dashboard tree entirely.
function isActive(pathname: string, href: string): boolean {
  if (!href.startsWith("/dashboard")) return false
  if (href === "/dashboard") return pathname === "/dashboard"
  return pathname === href || pathname.startsWith(`${href}/`)
}

type Props = {
  open: boolean
  onNavigate: () => void
}

export function DashboardSidebar({ open, onNavigate }: Props) {
  const pathname = usePathname()

  return (
    <aside className="dash-sidebar" data-open={open}>
      <div className="dash-brand">
        <TrustChainLogo size={24} />
        <span className="dash-brand__name">TRUSTCHAIN</span>
      </div>

      <nav className="dash-nav">
        {NAV_ITEMS.map((item) => (
          <SidebarNavItem
            key={item.href}
            href={item.href}
            label={item.label}
            icon={item.icon}
            active={isActive(pathname, item.href)}
            onNavigate={onNavigate}
          />
        ))}
      </nav>
    </aside>
  )
}
