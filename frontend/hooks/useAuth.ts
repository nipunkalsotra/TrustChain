"use client"

import { useState, useEffect } from "react"
import { useRouter, usePathname } from "next/navigation"

export interface Session {
    name: string
    email: string
    ts: number
}

export function useAuth() {
    const router = useRouter()
    const pathname = usePathname()
    const [session, setSession] = useState<Session | null>(null)
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        try {
            const s = localStorage.getItem("tc_session")
            const parsed = s ? JSON.parse(s) : null
            setSession(parsed)
            // Redirect to auth if not logged in and not already on /auth
            if (!parsed && pathname !== "/auth") {
                router.replace("/auth")
            }
        } catch {
            setSession(null)
        } finally {
            setLoading(false)
        }
    }, [pathname])

    const logout = () => {
        localStorage.removeItem("tc_session")
        setSession(null)
        router.replace("/auth")
    }

    return { session, loading, logout }
}