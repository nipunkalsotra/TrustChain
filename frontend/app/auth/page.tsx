"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { C } from "@/lib/constants"

type Mode = "login" | "signup"

interface FormState {
    name: string
    email: string
    password: string
    confirm: string
}

const EMPTY: FormState = { name: "", email: "", password: "", confirm: "" }

// ─── Simple client-side auth (localStorage) ───────────────────────────────────
// For a real project: swap saveUser/findUser with API calls to your backend.

function saveUser(name: string, email: string, password: string) {
    const users = JSON.parse(localStorage.getItem("tc_users") || "[]")
    if (users.find((u: any) => u.email === email)) return false
    users.push({ name, email, password: btoa(password) })
    localStorage.setItem("tc_users", JSON.stringify(users))
    return true
}

function findUser(email: string, password: string) {
    const users = JSON.parse(localStorage.getItem("tc_users") || "[]")
    return users.find((u: any) => u.email === email && u.password === btoa(password)) || null
}

function setSession(user: any) {
    localStorage.setItem("tc_session", JSON.stringify({ name: user.name, email: user.email, ts: Date.now() }))
}

export function getSession() {
    try {
        const s = localStorage.getItem("tc_session")
        return s ? JSON.parse(s) : null
    } catch { return null }
}

export function clearSession() {
    localStorage.removeItem("tc_session")
}

// ─── Input component ──────────────────────────────────────────────────────────
function CyberInput({
    label, type, value, onChange, placeholder, error,
}: {
    label: string; type: string; value: string
    onChange: (v: string) => void; placeholder: string; error?: string
}) {
    const [focused, setFocused] = useState(false)
    return (
        <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 9, letterSpacing: "0.2em", color: error ? C.red : focused ? C.green : C.dim, marginBottom: 6, transition: "color 0.2s" }}>
                {label}
            </div>
            <input
                type={type} value={value}
                onChange={e => onChange(e.target.value)}
                placeholder={placeholder}
                onFocus={() => setFocused(true)}
                onBlur={() => setFocused(false)}
                style={{
                    width: "100%", background: C.bg3,
                    border: `1px solid ${error ? C.red : focused ? `${C.green}88` : C.border2}`,
                    borderRadius: 6, color: C.bright, fontSize: 13,
                    padding: "11px 16px", outline: "none",
                    fontFamily: "inherit", transition: "border-color 0.2s",
                    boxShadow: focused ? `0 0 0 1px ${C.green}22 inset` : "none",
                }}
            />
            {error && <div style={{ fontSize: 10, color: C.red, marginTop: 4, letterSpacing: "0.05em" }}>✗ {error}</div>}
        </div>
    )
}

// ─── Main Auth Page ───────────────────────────────────────────────────────────
export default function AuthPage() {
    const router = useRouter()
    const [mode, setMode] = useState<Mode>("login")
    const [form, setForm] = useState<FormState>(EMPTY)
    const [errors, setErrors] = useState<Partial<FormState>>({})
    const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle")
    const [message, setMessage] = useState("")
    const [typed, setTyped] = useState("")

    // Typewriter effect
    const tagline = mode === "login" ? "AUTHENTICATE TO ACCESS THE BLACK BOX" : "CREATE YOUR AGENT IDENTITY"
    useEffect(() => {
        setTyped("")
        let i = 0
        const t = setInterval(() => { setTyped(tagline.slice(0, i + 1)); i++; if (i >= tagline.length) clearInterval(t) }, 40)
        return () => clearInterval(t)
    }, [mode])

    // Redirect if already logged in
    useEffect(() => {
        if (getSession()) router.replace("/dashboard")
    }, [])

    const set = (key: keyof FormState) => (v: string) => setForm(f => ({ ...f, [key]: v }))

    const validate = (): boolean => {
        const e: Partial<FormState> = {}
        if (mode === "signup" && !form.name.trim()) e.name = "Name is required"
        if (!form.email.match(/^[^@]+@[^@]+\.[^@]+$/)) e.email = "Valid email required"
        if (form.password.length < 6) e.password = "Minimum 6 characters"
        if (mode === "signup" && form.password !== form.confirm) e.confirm = "Passwords do not match"
        setErrors(e)
        return Object.keys(e).length === 0
    }

    const handleSubmit = async () => {
        if (!validate()) return
        setStatus("loading")
        setMessage("")

        // Simulate network delay for realism
        await new Promise(r => setTimeout(r, 900))

        if (mode === "signup") {
            const ok = saveUser(form.name.trim(), form.email.trim(), form.password)
            if (!ok) {
                setErrors({ email: "Email already registered" })
                setStatus("error")
                return
            }
            const user = findUser(form.email.trim(), form.password)
            setSession(user)
            setStatus("success")
            setMessage("IDENTITY REGISTERED ON-CHAIN")
            setTimeout(() => router.replace("/dashboard"), 1200)
        } else {
            const user = findUser(form.email.trim(), form.password)
            if (!user) {
                setErrors({ password: "Invalid credentials" })
                setStatus("error")
                return
            }
            setSession(user)
            setStatus("success")
            setMessage("ACCESS GRANTED")
            setTimeout(() => router.replace("/dashboard"), 1000)
        }
    }

    const switchMode = (m: Mode) => { setMode(m); setForm(EMPTY); setErrors({}); setStatus("idle"); setMessage("") }

    return (
        <div style={{ minHeight: "calc(100vh - 70px)", display: "flex", alignItems: "center", justifyContent: "center", padding: "40px 24px" }}>

            {/* Glow orb */}
            <div style={{ position: "fixed", top: "40%", left: "50%", transform: "translate(-50%,-50%)", width: 500, height: 500, borderRadius: "50%", background: `radial-gradient(circle, ${C.green}06 0%, transparent 70%)`, pointerEvents: "none" }} />

            <div style={{ width: "100%", maxWidth: 440, position: "relative", zIndex: 1 }}>

                {/* Logo */}
                <div style={{ textAlign: "center", marginBottom: 40 }}>
                    <div style={{ fontSize: 9, letterSpacing: "0.3em", color: C.dim, marginBottom: 16 }}>◈ ◈ ◈</div>
                    <h1 style={{ fontFamily: "'Share Tech Mono',monospace", fontSize: 32, fontWeight: 400, letterSpacing: "0.15em", color: C.green, textShadow: `0 0 24px ${C.green}55`, marginBottom: 8 }}>
                        TRUSTCHAIN
                    </h1>
                    <div style={{ fontSize: 10, letterSpacing: "0.15em", color: C.dim, marginBottom: 8 }}>
                        MONAD TESTNET · CHAIN 10143
                    </div>
                    <div style={{ fontSize: 10, letterSpacing: "0.12em", color: C.muted, minHeight: 16 }}>
                        {typed}<span style={{ display: "inline-block", width: 7, height: "0.9em", background: C.green, verticalAlign: "middle", animation: "pulse 1s infinite" }} />
                    </div>
                </div>

                {/* Card */}
                <div className="card" style={{ padding: "32px 32px 28px", position: "relative", overflow: "hidden" }}>

                    {/* Top glow line */}
                    <div style={{ position: "absolute", top: 0, left: "15%", right: "15%", height: 1, background: `linear-gradient(90deg,transparent,${C.green},transparent)` }} />

                    {/* Mode tabs */}
                    <div style={{ display: "flex", gap: 1, background: C.border, borderRadius: 6, overflow: "hidden", marginBottom: 28 }}>
                        {(["login", "signup"] as Mode[]).map(m => (
                            <button key={m} onClick={() => switchMode(m)} style={{
                                flex: 1, padding: "9px 0",
                                background: mode === m ? `${C.green}18` : C.bg2,
                                border: "none",
                                color: mode === m ? C.green : C.muted,
                                fontSize: 10, letterSpacing: "0.15em", fontFamily: "inherit",
                                fontWeight: mode === m ? 700 : 400,
                                transition: "all 0.2s",
                                borderBottom: mode === m ? `2px solid ${C.green}` : "2px solid transparent",
                            }}>
                                {m === "login" ? "◉ LOGIN" : "◈ SIGN UP"}
                            </button>
                        ))}
                    </div>

                    {/* Form */}
                    {mode === "signup" && (
                        <CyberInput label="AGENT NAME" type="text" value={form.name}
                            onChange={set("name")} placeholder="e.g. Nipun Kalsotra" error={errors.name} />
                    )}
                    <CyberInput label="EMAIL ADDRESS" type="email" value={form.email}
                        onChange={set("email")} placeholder="agent@trustchain.io" error={errors.email} />
                    <CyberInput label="PASSWORD" type="password" value={form.password}
                        onChange={set("password")} placeholder="••••••••••••" error={errors.password} />
                    {mode === "signup" && (
                        <CyberInput label="CONFIRM PASSWORD" type="password" value={form.confirm}
                            onChange={set("confirm")} placeholder="••••••••••••" error={errors.confirm} />
                    )}

                    {/* Success/error message */}
                    {message && (
                        <div style={{ marginBottom: 16, padding: "9px 14px", background: `${C.green}12`, border: `1px solid ${C.green}44`, borderRadius: 4, fontSize: 10, color: C.green, letterSpacing: "0.1em", textAlign: "center" }}>
                            ✓ {message}
                        </div>
                    )}

                    {/* Submit */}
                    <button onClick={handleSubmit} disabled={status === "loading" || status === "success"}
                        style={{
                            width: "100%", padding: "13px 0",
                            background: status === "success" ? `${C.green}22` : `${C.green}14`,
                            border: `1px solid ${status === "success" ? C.green : `${C.green}88`}`,
                            borderRadius: 6, color: C.green, fontSize: 12,
                            letterSpacing: "0.2em", fontFamily: "inherit", fontWeight: 700,
                            boxShadow: `0 0 16px ${C.green}22`,
                            transition: "all 0.2s", opacity: status === "loading" ? 0.7 : 1,
                        }}
                        onMouseEnter={e => { if (status === "idle" || status === "error") (e.currentTarget.style.boxShadow = `0 0 24px ${C.green}44`) }}
                        onMouseLeave={e => { (e.currentTarget.style.boxShadow = `0 0 16px ${C.green}22`) }}
                    >
                        {status === "loading" ? "◈ VERIFYING..." : status === "success" ? "✓ AUTHENTICATED" : mode === "login" ? "▶ ACCESS PIPELINE" : "◆ REGISTER IDENTITY"}
                    </button>

                    {/* Switch mode link */}
                    <div style={{ textAlign: "center", marginTop: 20, fontSize: 10, color: C.muted }}>
                        {mode === "login" ? (
                            <>No account?{" "}
                                <button onClick={() => switchMode("signup")} style={{ background: "none", border: "none", color: C.green, fontSize: 10, cursor: "pointer", fontFamily: "inherit", letterSpacing: "0.05em" }}>
                                    Create one →
                                </button>
                            </>
                        ) : (
                            <>Already registered?{" "}
                                <button onClick={() => switchMode("login")} style={{ background: "none", border: "none", color: C.green, fontSize: 10, cursor: "pointer", fontFamily: "inherit", letterSpacing: "0.05em" }}>
                                    Log in →
                                </button>
                            </>
                        )}
                    </div>
                </div>

                {/* Features below card */}
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginTop: 20 }}>
                    {[
                        { icon: "◆", label: "IMMUTABLE AUDIT", sub: "Every step on-chain" },
                        { icon: "◉", label: "AGENT IDENTITY", sub: "Cryptographic fingerprint" },
                        { icon: "◇", label: "TRUST SCORES", sub: "Per-run leaderboard" },
                    ].map(f => (
                        <div key={f.label} className="card" style={{ padding: "12px 10px", textAlign: "center" }}>
                            <div style={{ fontSize: 16, color: C.green, marginBottom: 6 }}>{f.icon}</div>
                            <div style={{ fontSize: 8, color: C.green, letterSpacing: "0.1em", marginBottom: 3 }}>{f.label}</div>
                            <div style={{ fontSize: 9, color: C.muted }}>{f.sub}</div>
                        </div>
                    ))}
                </div>

                <div style={{ textAlign: "center", marginTop: 20, fontSize: 9, color: C.dim, letterSpacing: "0.15em" }}>
                    BUILT ON MONAD TESTNET · CHAIN ID 10143
                </div>
            </div>
        </div>
    )
}