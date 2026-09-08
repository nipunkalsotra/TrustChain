"use client"

import { IconMenu } from "./icons"

/** Mobile-only: the sidebar starts off-canvas below the 900px breakpoint
 * (see dashboard.css), so this is the only way to open it there. Hidden
 * entirely on desktop — see .dash-topbar's default display:none — there is
 * nothing else in it. */
export function DashboardTopbar({ onMenuClick }: { onMenuClick: () => void }) {
  return (
    <header className="dash-topbar">
      <button className="dash-topbar__menu-btn" onClick={onMenuClick} aria-label="Toggle navigation">
        <IconMenu />
      </button>
    </header>
  )
}
