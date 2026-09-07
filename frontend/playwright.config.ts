import { defineConfig, devices } from "@playwright/test"

// Smoke suite (P2 — login, start run, authorised/unauthorised stream,
// logout, the public landing page). Needs a REAL backend already running
// on http://localhost:8000 (this config doesn't start one — the pipeline
// run test needs GROQ_API_KEY/TAVILY_API_KEY configured, which is a
// developer/CI secret, not something to fabricate here) — see
// e2e/README.md for exactly how to run this locally.
export default defineConfig({
    testDir: "./e2e",
    timeout: 60_000,
    fullyParallel: false,
    retries: 0,
    reporter: "list",
    use: {
        baseURL: "http://localhost:3000",
        trace: "retain-on-failure",
    },
    projects: [
        { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    ],
    webServer: {
        command: "npm run dev",
        url: "http://localhost:3000",
        reuseExistingServer: true,
        timeout: 60_000,
    },
})
