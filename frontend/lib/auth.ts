// lib/auth.ts — single source of truth for the client session.
// Backed by a real JWT issued by POST /auth/signup or /auth/login (see
// backend/auth.py) — not decorative localStorage like the old implementation.

const KEY = "tc_session"

export interface Session {
    name:  string
    email: string
    token: string
}

export function getSession(): Session | null {
    try {
        const raw = localStorage.getItem(KEY)
        return raw ? JSON.parse(raw) : null
    } catch {
        return null
    }
}

export function setSession(session: Session) {
    localStorage.setItem(KEY, JSON.stringify(session))
}

export function clearSession() {
    localStorage.removeItem(KEY)
}

// Spread into fetch headers for authenticated requests, e.g.:
//   fetch(url, { headers: { ...authHeader() } })
export function authHeader(): Record<string, string> {
    const session = getSession()
    return session?.token ? { Authorization: `Bearer ${session.token}` } : {}
}
