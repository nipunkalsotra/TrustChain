// lib/auth.test.ts — the session-display + CSRF-cookie-reader helpers.
// Real localStorage/document.cookie via jsdom (vitest.config.ts), not a
// mock of the Storage/Document API — the actual browser interface this
// module depends on.

import { beforeEach, describe, expect, it } from "vitest"
import { clearSession, csrfHeader, getSession, setSession } from "./auth"

beforeEach(() => {
    localStorage.clear()
    document.cookie.split(";").forEach((c) => {
        const name = c.split("=")[0]?.trim()
        if (name) document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/`
    })
})

describe("session storage — display info only, never auth material", () => {
    it("returns null when nothing is stored", () => {
        expect(getSession()).toBeNull()
    })

    it("round-trips name/email through localStorage", () => {
        setSession({ name: "Ada Lovelace", email: "ada@example.com" })
        expect(getSession()).toEqual({ name: "Ada Lovelace", email: "ada@example.com" })
    })

    it("clearSession removes it", () => {
        setSession({ name: "Ada Lovelace", email: "ada@example.com" })
        clearSession()
        expect(getSession()).toBeNull()
    })

    it("never stores a token field — the whole point of this module post-P1", () => {
        setSession({ name: "Ada Lovelace", email: "ada@example.com" })
        const raw = localStorage.getItem("tc_session")
        expect(raw).not.toBeNull()
        expect(raw).not.toContain("token")
        // Also proves the stored blob isn't secretly a JWT (three
        // base64url segments joined by '.') — an XSS bug reading this
        // key gets a name and an email, nothing it could replay as auth.
        expect(raw).not.toMatch(/eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+/)
    })

    it("survives malformed JSON in the storage key rather than throwing", () => {
        localStorage.setItem("tc_session", "{not valid json")
        expect(getSession()).toBeNull()
    })
})

describe("csrfHeader — reads the double-submit tc_csrf cookie", () => {
    it("returns an empty object when no tc_csrf cookie is set", () => {
        expect(csrfHeader()).toEqual({})
    })

    it("returns X-CSRF-Token matching the cookie's value", () => {
        document.cookie = "tc_csrf=abc123def456"
        expect(csrfHeader()).toEqual({ "X-CSRF-Token": "abc123def456" })
    })

    it("decodes a URL-encoded cookie value", () => {
        document.cookie = `tc_csrf=${encodeURIComponent("value/with+special=chars")}`
        expect(csrfHeader()).toEqual({ "X-CSRF-Token": "value/with+special=chars" })
    })

    it("picks tc_csrf out from among other cookies", () => {
        document.cookie = "other=1"
        document.cookie = "tc_csrf=the-real-token"
        document.cookie = "another=2"
        expect(csrfHeader()).toEqual({ "X-CSRF-Token": "the-real-token" })
    })
})
