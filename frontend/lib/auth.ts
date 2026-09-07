// lib/auth.ts — session DISPLAY info only, never auth material.
//
// The real session now lives in HttpOnly tc_access/tc_refresh cookies the
// browser manages automatically (backend/refresh.py) — this module never
// sees, stores, or reads a token. Before this, a 7-day JWT sat in
// localStorage under "tc_session", readable by any XSS on the page; the
// only thing localStorage holds now is a name/email pair for the navbar's
// "logged in as ..." display, which grants nothing on its own — the actual
// access boundary is server-side and enforced by every request the browser
// makes, not by whatever this object happens to contain.

const KEY = "tc_session"

export interface Session {
    name: string
    email: string
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

// The double-submit CSRF cookie (backend/refresh.py's tc_csrf) is the one
// session cookie deliberately NOT HttpOnly — the frontend has to read it
// to echo it back. Spread into any unsafe (POST/PUT/PATCH/DELETE) request's
// headers; see lib/api.ts's apiFetch, the only place this should be called
// directly.
export function csrfHeader(): Record<string, string> {
    if (typeof document === "undefined") return {}
    const match = document.cookie.match(/(?:^|; )tc_csrf=([^;]*)/)
    return match ? { "X-CSRF-Token": decodeURIComponent(match[1]) } : {}
}
