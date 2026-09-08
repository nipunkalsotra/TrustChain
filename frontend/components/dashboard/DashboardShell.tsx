"use client"

import { useState } from "react"
import { DashboardSidebar } from "./DashboardSidebar"
import { DashboardTopbar } from "./DashboardTopbar"
import "./dashboard.css"

/** The persistent shell every /dashboard/* route renders inside (see
 * app/dashboard/layout.tsx). Owns only the mobile sidebar open/closed state
 * — everything else here is presentational. */
export function DashboardShell({ children }: { children: React.ReactNode }) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  const closeMobileNav = () => setMobileNavOpen(false)

  return (
    <div className="dash-shell">
      <DashboardSidebar open={mobileNavOpen} onNavigate={closeMobileNav} />
      <div className="dash-sidebar__scrim" data-open={mobileNavOpen} onClick={closeMobileNav} />

      <div className="dash-main">
        <DashboardTopbar onMenuClick={() => setMobileNavOpen((v) => !v)} />
        <main className="dash-content">{children}</main>
      </div>
    </div>
  )
}
