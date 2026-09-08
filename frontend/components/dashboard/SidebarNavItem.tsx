import Link from "next/link"
import type { ComponentType } from "react"

type Props = {
  href: string
  label: string
  icon: ComponentType<{ className?: string }>
  active: boolean
  onNavigate?: () => void
}

/** One sidebar row — every item is a real Link styled identically, Docs
 * included (it points at the Coming Soon page rather than a real /docs
 * route, but looks and behaves exactly like the rest). */
export function SidebarNavItem({ href, label, icon: Icon, active, onNavigate }: Props) {
  return (
    <Link href={href} className="dash-nav__item" data-active={active} onClick={onNavigate}>
      <Icon className="dash-nav__icon" />
      <span>{label}</span>
    </Link>
  )
}
