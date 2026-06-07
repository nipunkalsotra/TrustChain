"use client"

import { useState, useEffect } from "react"
import { usePathname } from "next/navigation"
import Link from "next/link"
import { getChainStatus } from "@/lib/api"
import { Dot, Ticker } from "@/components/ui/TrustChainUI"
import { C } from "@/lib/constants"
import type { ChainStatus } from "@/lib/types"

const NAV_ITEMS = [
    { href: "/", label: "HOME", icon: "◈" },
    { href: "/dashboard", label: "DASHBOARD", icon: "⬡" },
    { href: "/audit", label: "AUDIT LOG", icon: "◷" },
    { href: "/trust-scores", label: "TRUST SCORES", icon: "◎" },
    { href: "/verify", label: "VERIFY", icon: "◆" },
]

export default function ClientShell({ children }: { children: React.ReactNode }) {
    const [chain, setChain] = useState<ChainStatus | null>(null)
    const pathname = usePathname()

    useEffect(() => {
        getChainStatus().then(setChain).catch(() => setChain(null))
        const t = setInterval(() => {
            setChain(c => c ? { ...c, blockNumber: (c.blockNumber ?? 0) + Math.floor(Math.random() * 3 + 1) } : c)
        }, 2000)
        return () => clearInterval(t)
    }, [])

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
                {/* Navbar */}
                <nav style={{ background: C.bg0, borderBottom: `1px solid ${C.border}`, padding: "0 24px", display: "flex", alignItems: "center", height: 48, position: "sticky", top: 0, zIndex: 100 }}>
                    <Link href="/" style={{ color: C.green, fontFamily: "'Share Tech Mono',monospace", fontSize: 15, fontWeight: 700, letterSpacing: "0.15em", marginRight: 32 }}>
                        ◈ TRUSTCHAIN
                    </Link>
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
                    <div style={{ display: "flex", alignItems: "center", gap: 16, fontSize: 10 }}>
                        <div style={{ display: "flex", alignItems: "center" }}>
                            <Dot active={chain?.connected ?? false} />
                            <span style={{ color: C.sub }}>
                                {chain?.connected ? `BLOCK #${chain.blockNumber?.toLocaleString()}` : "DISCONNECTED"}
                            </span>
                        </div>
                        <div style={{ color: C.dim, letterSpacing: "0.08em" }}>3 CONTRACTS</div>
                    </div>
                </nav>
                <main>{children}</main>
            </div>
        </>
    )
}