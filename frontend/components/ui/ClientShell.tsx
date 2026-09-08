"use client"

import { useState, useEffect } from "react"
import { useRouter, usePathname } from "next/navigation"
import Link from "next/link"
import { getChainStatus, logout as apiLogout } from "@/lib/api"
import { getSession, clearSession, type Session } from "@/lib/auth"
import { Dot, Ticker } from "@/components/ui/TrustChainUI"
import { C } from "@/lib/constants"
import type { ChainStatus } from "@/lib/types"

const NAV_ITEMS = [
    { href: "/", label: "HOME", icon: "◈" },
    { href: "/dashboard", label: "DASHBOARD", icon: "⬡" },
    { href: "/history", label: "HISTORY", icon: "◷" },
    { href: "/leaderboard", label: "LEADERBOARD", icon: "▲" },
    { href: "/audit", label: "AUDIT LOG", icon: "☰" },
    { href: "/trust-scores", label: "TRUST SCORES", icon: "◎" },
    { href: "/verify", label: "VERIFY", icon: "◆" },
]

// Marketing surface: public, and deliberately rendered WITHOUT the product
// chrome (nav, ticker, scanline, grid). The landing page ships its own
// cinematic navbar and its own visual language — wrapping it in the app shell
// would double the navigation and drop the terminal theme on top of it.
const MARKETING_PATHS = ["/", "/coming-soon", "/pricing"]

// Same reasoning as MARKETING_PATHS, for the same reason: /dashboard ships
// its own shell (components/dashboard/DashboardShell — its own sidebar/
// topbar, its own landing-matched visual language), so wrapping it in this
// old terminal-themed navbar would double the chrome. Real login/signup
// doesn't exist yet (landing page's Login/Get Started both link straight
// here — see components/marketing/content.ts) — prefix-matched, not just
// the bare route, so /dashboard/runs/[runId] etc. get the same treatment.
const DASHBOARD_PREFIX = "/dashboard"
function isDashboardPath(pathname: string): boolean {
    return pathname === DASHBOARD_PREFIX || pathname.startsWith(`${DASHBOARD_PREFIX}/`)
}

// Pages that don't require login
const PUBLIC_PATHS = ["/auth", ...MARKETING_PATHS]

export default function ClientShell({ children }: { children: React.ReactNode }) {
    const router = useRouter()
    const pathname = usePathname()

    const [chain, setChain] = useState<ChainStatus | null>(null)
    const [session, setLocalSession] = useState<Session | null>(null)
    const [ready, setReady] = useState(false)

    // ── Chain status — polls the real backend, no simulated ticking ────────
    // Skipped entirely on the marketing routes. They render without the navbar
    // this value feeds, so polling there was a request every 5s that nothing
    // could ever display — and on the public landing page it meant every
    // anonymous visitor's browser hammering the API with connection-refused
    // retries whenever the backend isn't running.
    const isMarketing = MARKETING_PATHS.includes(pathname)
    // Marketing pages AND /dashboard both ship their own complete chrome —
    // see DASHBOARD_PREFIX's comment above for why /dashboard joins the
    // marketing bypass here.
    const bypassProductChrome = isMarketing || isDashboardPath(pathname)
    useEffect(() => {
        if (bypassProductChrome) return
        const poll = () => getChainStatus().then(setChain).catch(() => setChain(null))
        poll()
        const t = setInterval(poll, 5000)
        return () => clearInterval(t)
    }, [bypassProductChrome])

    // ── Auth guard ────────────────────────────────────────────────────────
    // Tried the useSyncExternalStore rewrite this rule nudges toward — it
    // introduced a real bug: on a hard navigation, the hydration-time server
    // snapshot (always null) could reach this effect before the real client
    // snapshot synced, firing a spurious redirect-then-bounce-back loop.
    // Reading getSession() fresh inside a plain effect (below) doesn't have
    // that window — verified in a real browser — so this stays as-is.
    useEffect(() => {
        if (bypassProductChrome) return
        const s = getSession()
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setLocalSession(s)
        if (!s && !PUBLIC_PATHS.includes(pathname)) {
            router.replace("/auth")
        }
        setReady(true)
    }, [pathname, bypassProductChrome])

    const logout = () => {
        // Revokes the refresh-token family and clears the session cookies
        // server-side (backend/refresh.py's revoke_family_for_token) —
        // clearing only the local display shadow would leave tc_access
        // valid until its own 15-minute expiry. Fire-and-forget: the local
        // UI state below is what actually gates the redirect, so a slow or
        // failed network call here shouldn't block logging out locally.
        void apiLogout()
        clearSession()
        setLocalSession(null)
        router.replace("/auth")
    }

    // Marketing routes AND /dashboard render immediately and bare. Checked
    // BEFORE the `ready` gate on purpose: both are public (no real auth yet),
    // so making a visitor wait a commit for an auth check that cannot affect
    // them would blank the first paint for no reason.
    if (bypassProductChrome) return <>{children}</>

    // Don't render until auth check is done (prevents flash)
    if (!ready) return null

    const isPublic = PUBLIC_PATHS.includes(pathname)

    return (
        <>
            {/* Scanline */}
            <div style={{
                position: "fixed", inset: 0, pointerEvents: "none", zIndex: 9999,
                backgroundImage: "repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,255,204,0.010) 2px,rgba(0,255,204,0.010) 4px)",
            }} />
            {/* Grid bg */}
            <div style={{
                position: "fixed", inset: 0, pointerEvents: "none", zIndex: 0,
                backgroundImage: `linear-gradient(#0a2a2a 1px,transparent 1px),linear-gradient(90deg,#0a2a2a 1px,transparent 1px)`,
                backgroundSize: "40px 40px", opacity: 0.3,
            }} />

            <div style={{ position: "relative", zIndex: 1, minHeight: "100vh" }}>
                <Ticker />

                {/* Navbar — hidden on auth page */}
                {!isPublic && (
                    <nav style={{
                        background: C.bg0,
                        borderBottom: `1px solid ${C.border}`,
                        padding: "0 24px",
                        display: "flex", alignItems: "center",
                        height: 48, position: "sticky", top: 0, zIndex: 100,
                    }}>
                        {/* Logo */}
                        <Link href="/" style={{
                            color: C.green, fontFamily: "'Share Tech Mono',monospace",
                            fontSize: 15, fontWeight: 700, letterSpacing: "0.15em", marginRight: 32,
                            textShadow: `0 0 16px ${C.green}40`,
                        }}>
                            ◈ TRUSTCHAIN
                        </Link>

                        {/* Nav links */}
                        <div style={{ display: "flex", gap: 2, flex: 1 }}>
                            {NAV_ITEMS.map(n => (
                                <Link key={n.href} href={n.href} style={{
                                    borderBottom: pathname === n.href ? `2px solid ${C.green}` : "2px solid transparent",
                                    color: pathname === n.href ? C.green : C.sub,
                                    fontSize: 10, letterSpacing: "0.1em", padding: "0 14px", height: 48,
                                    display: "flex", alignItems: "center", transition: "all 0.15s",
                                }}>
                                    <span style={{ marginRight: 5, opacity: 0.6 }}>{n.icon}</span>{n.label}
                                </Link>
                            ))}
                        </div>

                        {/* Right: chain status + session */}
                        <div style={{ display: "flex", alignItems: "center", gap: 16, fontSize: 10 }}>
                            {/* Chain status (your existing logic) */}
                            <div style={{ display: "flex", alignItems: "center" }}>
                                <Dot active={chain?.connected ?? false} />
                                <span style={{ color: C.sub }}>
                                    {chain?.connected
                                        ? `BLOCK #${chain.blockNumber?.toLocaleString()}`
                                        : "DISCONNECTED"}
                                </span>
                            </div>
                            <div style={{ color: C.dim, letterSpacing: "0.08em" }}>3 CONTRACTS</div>

                            {/* Session display */}
                            {session && (
                                <div style={{
                                    display: "flex", alignItems: "center", gap: 10,
                                    borderLeft: `1px solid ${C.border}`, paddingLeft: 16,
                                }}>
                                    {/* Avatar initial */}
                                    <div style={{
                                        width: 26, height: 26, borderRadius: "50%",
                                        background: `${C.green}22`, border: `1px solid ${C.green}44`,
                                        display: "flex", alignItems: "center", justifyContent: "center",
                                        fontSize: 10, color: C.green, fontWeight: 700,
                                    }}>
                                        {session.name?.charAt(0).toUpperCase() || "A"}
                                    </div>
                                    <div>
                                        <div style={{ fontSize: 9, color: C.bright, letterSpacing: "0.05em" }}>{session.name}</div>
                                        <div style={{ fontSize: 8, color: C.dim }}>{session.email}</div>
                                    </div>
                                    <button onClick={logout} style={{
                                        background: "none", border: `1px solid ${C.border}`,
                                        color: C.muted, fontSize: 8, letterSpacing: "0.1em",
                                        padding: "3px 8px", borderRadius: 3, cursor: "pointer",
                                        fontFamily: "inherit", transition: "all 0.15s",
                                    }}
                                        onMouseEnter={e => { (e.currentTarget.style.borderColor = C.red); (e.currentTarget.style.color = C.red) }}
                                        onMouseLeave={e => { (e.currentTarget.style.borderColor = C.border); (e.currentTarget.style.color = C.muted) }}
                                    >
                                        EXIT
                                    </button>
                                </div>
                            )}
                        </div>
                    </nav>
                )}

                <main>{children}</main>
            </div>
        </>
    )
}