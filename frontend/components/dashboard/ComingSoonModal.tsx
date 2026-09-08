"use client"

import { useEffect } from "react"
import { TrustChainLogo } from "./Logo"
import { IconClose } from "./icons"

const MESSAGE = "Your TrustChain project command center is coming soon."

/** Currently unwired — its former trigger, the sidebar's bottom logo
 * button, was removed. Kept as a standalone, self-contained modal (open/
 * onClose props, own Escape/click-outside handling) for whichever future
 * entry point ends up wanting a "coming soon" popup; there is no dedicated
 * Coming Soon route for the dashboard, so this is still the only place
 * that content lives once something opens it again. */
export function ComingSoonModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!open) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="dash-modal-backdrop" onClick={onClose}>
      <div
        className="dash-modal page-enter"
        role="dialog"
        aria-modal="true"
        aria-label="Coming soon"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="dash-modal__close" onClick={onClose} aria-label="Close">
          <IconClose />
        </button>
        <TrustChainLogo size={40} className="dash-modal__logo" />
        <span className="dash-modal__badge">Coming soon</span>
        <p className="dash-modal__message">{MESSAGE}</p>
      </div>
    </div>
  )
}
