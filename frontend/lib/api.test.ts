// lib/api.test.ts — apiFetch's actual contract (every exported function
// routes through it): credentials always included, CSRF header attached
// on unsafe methods only, and exactly one silent refresh-and-retry on a
// 401 — plus the stream-token reconnect path (refreshStreamToken).
//
// apiFetch itself isn't exported (module-private, deliberately — see
// lib/api.ts's own comment), so this drives it through the real exported
// functions and asserts on how the mocked global fetch was actually
// called, same as a real network inspector would see.

import { beforeEach, describe, expect, it, vi } from "vitest"
import { getRuns, refreshStreamToken, startRun } from "./api"

function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
    })
}

beforeEach(() => {
    document.cookie.split(";").forEach((c) => {
        const name = c.split("=")[0]?.trim()
        if (name) document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/`
    })
})

describe("apiFetch (via exported callers) — credentials + CSRF", () => {
    it("always sends credentials: include, even for a safe GET", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ runs: [], total: 0 }))
        vi.stubGlobal("fetch", fetchMock)

        await getRuns()

        expect(fetchMock).toHaveBeenCalledTimes(1)
        const [, options] = fetchMock.mock.calls[0]
        expect(options.credentials).toBe("include")
    })

    it("attaches X-CSRF-Token on an unsafe POST when tc_csrf is set", async () => {
        document.cookie = "tc_csrf=real-csrf-value"
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ run_id: "r1", stream_url: "/stream/r1?token=x" }))
        vi.stubGlobal("fetch", fetchMock)

        await startRun("do the thing")

        const [, options] = fetchMock.mock.calls[0]
        expect(options.headers["X-CSRF-Token"]).toBe("real-csrf-value")
    })

    it("does NOT attach X-CSRF-Token on a safe GET even when tc_csrf is set", async () => {
        document.cookie = "tc_csrf=real-csrf-value"
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ runs: [], total: 0 }))
        vi.stubGlobal("fetch", fetchMock)

        await getRuns()

        const [, options] = fetchMock.mock.calls[0]
        expect(options.headers?.["X-CSRF-Token"]).toBeUndefined()
    })
})

describe("apiFetch — silent refresh-and-retry on 401", () => {
    it("on a 401, calls POST /auth/refresh once and retries the original request", async () => {
        const calls: string[] = []
        const fetchMock = vi.fn().mockImplementation((url: string, options?: RequestInit) => {
            calls.push(`${options?.method ?? "GET"} ${url}`)
            // getRuns() appends ?limit=50, so match by inclusion, not endsWith.
            const runsCallsSoFar = calls.filter((c) => c.includes("/runs")).length
            if (url.includes("/runs") && runsCallsSoFar === 1) {
                return Promise.resolve(jsonResponse({ detail: "expired" }, 401))
            }
            if (url.endsWith("/auth/refresh")) {
                return Promise.resolve(jsonResponse({ ok: true }, 200))
            }
            return Promise.resolve(jsonResponse({ runs: [], total: 0 }))
        })
        vi.stubGlobal("fetch", fetchMock)

        const result = await getRuns()

        expect(result).toEqual({ runs: [], total: 0 })
        // GET /runs (401) -> POST /auth/refresh -> GET /runs (retry, succeeds)
        expect(calls.filter((c) => c.includes("/runs")).length).toBe(2)
        expect(calls.some((c) => c.startsWith("POST") && c.endsWith("/auth/refresh"))).toBe(true)
    })

    it("does not retry a second time if the retried request ALSO 401s (no infinite loop)", async () => {
        const fetchMock = vi.fn().mockImplementation((url: string) => {
            if (url.endsWith("/auth/refresh")) return Promise.resolve(jsonResponse({ ok: true }, 200))
            return Promise.resolve(jsonResponse({ detail: "still expired" }, 401))
        })
        vi.stubGlobal("fetch", fetchMock)

        await expect(getRuns()).rejects.toThrow("get runs failed")
        // GET /runs, POST /auth/refresh, GET /runs (retry) — exactly 3, not more.
        expect(fetchMock).toHaveBeenCalledTimes(3)
    })

    it("does not attempt a refresh loop when the refresh call itself is what's calling apiFetch", async () => {
        // login()/signup() are in the no-refresh-retry set — a failed
        // login is the real answer, not a session to recover from.
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: "invalid credentials" }, 401))
        vi.stubGlobal("fetch", fetchMock)

        const { login } = await import("./api")
        await expect(login("a@example.com", "wrong")).rejects.toThrow()
        expect(fetchMock).toHaveBeenCalledTimes(1)
    })
})

describe("stream-token reconnect (POST /runs/{id}/stream-token)", () => {
    it("returns a fresh stream_url for reconnecting after the original token expires", async () => {
        const fetchMock = vi.fn().mockResolvedValue(
            jsonResponse({ stream_url: "/stream/run_123?token=freshtoken" }),
        )
        vi.stubGlobal("fetch", fetchMock)

        const result = await refreshStreamToken("run_123")

        expect(result.stream_url).toBe("/stream/run_123?token=freshtoken")
        const [url, options] = fetchMock.mock.calls[0]
        expect(url).toContain("/runs/run_123/stream-token")
        expect(options.method).toBe("POST")
        expect(options.credentials).toBe("include")
    })

    it("throws when the reconnect endpoint rejects (e.g. another project's run)", async () => {
        const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: "not found" }, 404))
        vi.stubGlobal("fetch", fetchMock)

        await expect(refreshStreamToken("someone_elses_run")).rejects.toThrow("refresh stream token failed: 404")
    })
})
