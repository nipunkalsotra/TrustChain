// e2e/smoke.spec.ts — Playwright smoke suite (P2): public landing page,
// login, start run, authorised stream, unauthorised stream rejection,
// logout. Needs a REAL backend already running on localhost:8000 (see
// e2e/README.md) — this is deliberately end-to-end against real
// infrastructure, not mocked, matching this repo's own testing
// philosophy (CLAUDE.md).

import { expect, test } from "@playwright/test"

const API = "http://localhost:8000"

function uniqueEmail(): string {
    return `pw_smoke_${Date.now()}_${Math.random().toString(36).slice(2, 8)}@example.com`
}

test.describe("public landing page", () => {
    test("loads without auth and shows the TrustChain brand", async ({ page }) => {
        const response = await page.goto("/")
        expect(response?.ok()).toBeTruthy()
        await expect(page).toHaveTitle(/TrustChain/i)
    })
})

test.describe("auth: signup, protected-route redirect, logout", () => {
    test("an unauthenticated visitor hitting /dashboard is redirected to /auth", async ({ page }) => {
        await page.goto("/dashboard")
        await page.waitForURL(/\/auth/, { timeout: 10_000 })
        expect(page.url()).toContain("/auth")
    })

    test("signup logs in, lands on /dashboard, and localStorage never holds a token", async ({ page }) => {
        await page.goto("/auth")

        // The auth page defaults to "login" mode — switch to "signup".
        await page.getByRole("button", { name: /sign up/i }).click()

        const email = uniqueEmail()
        await page.getByPlaceholder(/nipun kalsotra/i).fill("Playwright Smoke")
        await page.getByPlaceholder(/agent@trustchain\.io/i).fill(email)
        await page.getByPlaceholder("••••••••••••").first().fill("CorrectHorseBattery9!")
        await page.getByPlaceholder("••••••••••••").nth(1).fill("CorrectHorseBattery9!")

        await page.getByRole("button", { name: /register identity/i }).click()
        await page.waitForURL(/\/dashboard/, { timeout: 15_000 })

        // The actual P1 security property this smoke test exists to catch
        // a regression in: no XSS-readable token anywhere in localStorage.
        const stored = await page.evaluate(() => localStorage.getItem("tc_session"))
        expect(stored).not.toBeNull()
        expect(stored).not.toContain("token")
        expect(stored).not.toMatch(/eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+/)

        // The real session cookie IS there, HttpOnly (so page.evaluate()
        // reading document.cookie must NOT see it — only the browser
        // context's own cookie jar can).
        const cookies = await page.context().cookies()
        const accessCookie = cookies.find((c) => c.name === "tc_access")
        expect(accessCookie).toBeDefined()
        expect(accessCookie?.httpOnly).toBe(true)

        // Logout clears the session and returns to /auth.
        await page.getByRole("button", { name: /exit/i }).click()
        await page.waitForURL(/\/auth/, { timeout: 10_000 })
        const afterLogout = await page.evaluate(() => localStorage.getItem("tc_session"))
        expect(afterLogout).toBeNull()
    })
})

test.describe("run pipeline: start, authorised stream, unauthorised stream rejection", () => {
    test("starting a run returns a stream_url with a token, and an unauthorised stream request is rejected", async ({ page, request }) => {
        // Sign up via the API directly (faster/more deterministic than
        // driving the form again) to get a session for the request
        // context below — Playwright's `request` fixture shares cookies
        // with `page` only after page-driven navigation sets them, so
        // this signs up through the UI once, same as the test above,
        // rather than trying to hand-splice a cookie jar.
        await page.goto("/auth")
        await page.getByRole("button", { name: /sign up/i }).click()
        const email = uniqueEmail()
        await page.getByPlaceholder(/nipun kalsotra/i).fill("Playwright Stream Smoke")
        await page.getByPlaceholder(/agent@trustchain\.io/i).fill(email)
        await page.getByPlaceholder("••••••••••••").first().fill("CorrectHorseBattery9!")
        await page.getByPlaceholder("••••••••••••").nth(1).fill("CorrectHorseBattery9!")
        await page.getByRole("button", { name: /register identity/i }).click()
        await page.waitForURL(/\/dashboard/, { timeout: 15_000 })

        // Real POST /run-agent through the browser context's own cookies —
        // an unsafe request authenticated via cookie needs the CSRF header
        // too (main.py's _csrf_protection_middleware), same as the real
        // frontend's apiFetch attaches automatically; page.request bypasses
        // apiFetch entirely, so this replicates that one header by hand.
        const cookies = await page.context().cookies()
        const csrfToken = cookies.find((c) => c.name === "tc_csrf")?.value
        expect(csrfToken).toBeTruthy()

        const runResponse = await page.request.post(`${API}/run-agent`, {
            data: { task: "playwright smoke test task" },
            headers: { "X-CSRF-Token": csrfToken! },
        })
        expect(runResponse.ok()).toBeTruthy()
        const runBody = await runResponse.json()
        expect(runBody.run_id).toBeTruthy()
        expect(runBody.stream_url).toContain("token=")

        // Authorised: the real, token-bearing stream_url succeeds.
        const authorisedStream = await page.request.get(`${API}${runBody.stream_url}`)
        expect(authorisedStream.status()).toBe(200)

        // Unauthorised: the bare run_id with no token is rejected — this
        // is the P1 stream-token requirement's actual security property.
        const unauthorisedStream = await request.get(`${API}/stream/${runBody.run_id}`)
        expect(unauthorisedStream.status()).toBe(401)
    })
})
