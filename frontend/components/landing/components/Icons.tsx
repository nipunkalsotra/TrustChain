/**
 * Inline SVG icon set.
 *
 * Inline rather than an icon package because the navbar and feature controls
 * animate individual paths (stroke draw, separation, glow), which requires
 * owning the markup. Also keeps the landing page free of a runtime icon
 * dependency it would otherwise pull in for eight glyphs.
 *
 * The brand mark is the same shield+diamond the monolith core and the
 * travelling holographic copy draw in GLSL — so the shape the visitor watches
 * fly into the navbar is the shape that lands there (spec §6.7).
 */

type P = { className?: string; size?: number }

export function BrandMark({ className, size = 34 }: P) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <defs>
        <linearGradient id="tc-mark-g" x1="4" y1="2" x2="28" y2="30" gradientUnits="userSpaceOnUse">
          <stop stopColor="#8ad8ff" />
          <stop offset="0.55" stopColor="#3ec6ff" />
          <stop offset="1" stopColor="#1668ff" />
        </linearGradient>
      </defs>
      {/* Shield */}
      <path
        d="M16 2.4 4.6 6.9v9.2c0 6.5 4.7 11.6 11.4 13.5 6.7-1.9 11.4-7 11.4-13.5V6.9L16 2.4Z"
        stroke="url(#tc-mark-g)"
        strokeWidth="1.6"
        strokeLinejoin="round"
        fill="rgba(62,198,255,0.08)"
      />
      {/* Inner chain-link diamond */}
      <path d="M16 10.2 21.4 16 16 21.8 10.6 16 16 10.2Z" stroke="url(#tc-mark-g)" strokeWidth="1.5" strokeLinejoin="round" />
      <circle cx="16" cy="16" r="2.05" fill="url(#tc-mark-g)" />
    </svg>
  )
}

export function IconProduct({ className, size = 18 }: P) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="3" y="4" width="7" height="7" rx="1.6" stroke="currentColor" strokeWidth="1.6" />
      <rect x="14" y="13" width="7" height="7" rx="1.6" stroke="currentColor" strokeWidth="1.6" />
      <path d="M10 7.5h4.5a3 3 0 0 1 3 3V13" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

export function IconSecurity({ className, size = 18 }: P) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M12 3 4.8 5.9v6.2c0 4.4 3 7.8 7.2 9 4.2-1.2 7.2-4.6 7.2-9V5.9L12 3Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="m9.2 12 2 2 3.6-3.8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function IconDocs({ className, size = 18 }: P) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M6 3.6h7.5L18.4 8.5v11.9H6V3.6Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M13.3 3.8v4.8h4.8M9 13h6M9 16.4h4.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

export function IconPricing({ className, size = 18 }: P) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M4 10.5 10.5 4H20v9.5L13.5 20 4 10.5Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <circle cx="15.6" cy="8.4" r="1.5" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  )
}

/* ── Feature-control icons (spec §9) ─────────────────────────────────────── */

export function IconImmutable({ className, size = 30 }: P) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <rect x="5" y="7" width="22" height="5" rx="1.4" stroke="currentColor" strokeWidth="1.5" />
      <rect x="5" y="14" width="22" height="5" rx="1.4" stroke="currentColor" strokeWidth="1.5" opacity="0.75" />
      <rect x="5" y="21" width="22" height="5" rx="1.4" stroke="currentColor" strokeWidth="1.5" opacity="0.5" />
      <circle cx="9" cy="9.5" r="1.1" fill="currentColor" />
      <circle cx="9" cy="16.5" r="1.1" fill="currentColor" opacity="0.75" />
    </svg>
  )
}

export function IconMerkle({ className, size = 30 }: P) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <path d="M16 7v3.5M9.5 17.5 16 10.5l6.5 7M6 24l3.5-6.5M13 24l-3.5-6.5M19 24l3.5-6.5M26 24l-3.5-6.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="16" cy="6" r="2.1" fill="currentColor" />
      <circle cx="9.5" cy="17.5" r="1.7" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="22.5" cy="17.5" r="1.7" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}

export function IconOnChain({ className, size = 30 }: P) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <path d="M13.4 18.6a4.4 4.4 0 0 1 0-6.2l3-3a4.4 4.4 0 0 1 6.2 6.2l-1.3 1.3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M18.6 13.4a4.4 4.4 0 0 1 0 6.2l-3 3a4.4 4.4 0 0 1-6.2-6.2l1.3-1.3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

export function IconPublic({ className, size = 30 }: P) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <circle cx="16" cy="16" r="10.5" stroke="currentColor" strokeWidth="1.5" />
      <path d="M5.5 16h21M16 5.5c2.9 3 4.4 6.7 4.4 10.5S18.9 23.5 16 26.5c-2.9-3-4.4-6.7-4.4-10.5S13.1 8.5 16 5.5Z" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  )
}

export function IconArrow({ className, size = 16 }: P) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden>
      <path d="M3 8h9.5M9 4.5 12.5 8 9 11.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export const NAV_ICONS = {
  product: IconProduct,
  security: IconSecurity,
  docs: IconDocs,
  pricing: IconPricing,
} as const

export const FEATURE_ICONS = {
  immutable: IconImmutable,
  merkle: IconMerkle,
  onchain: IconOnChain,
  public: IconPublic,
} as const
