import { test, expect } from "@playwright/test";
const API = "http://localhost:8000";
const me = {
  user: {
    id: 1,
    name: "Alex Morgan",
    email: "alex@example.com",
    emailVerified: true,
  },
  active: { orgId: 1, projectId: 1, role: "owner" },
  memberships: [
    {
      org: { id: 1, name: "Acme Intelligence", plan: "free" },
      role: "owner",
      projects: [
        { id: 1, name: "Production", environment: "live" },
        { id: 2, name: "Research", environment: "test" },
      ],
    },
  ],
};
const now = Math.floor(Date.now() / 1000);
const runs = Array.from({ length: 12 }, (_, i) => ({
  runId: `run_${i + 1}_audit_workflow`,
  task: [
    "Research AI governance frameworks",
    "Validate customer support responses",
    "Evaluate model reliability",
  ][i % 3],
  status: i === 0 ? "running" : i === 4 ? "error" : "complete",
  createdAt: now - (i * 86400) / 2,
  completedAt: i === 0 ? null : now - (i * 86400) / 2 + 90,
  result: { score: 88 + (i % 8) },
}));
async function workspace(page: import("@playwright/test").Page) {
  await page.route(`${API}/**`, async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname;
    let body: unknown = {};
    if (path === "/me") body = me;
    else if (path === "/runs") body = { runs, total: runs.length };
    else if (path === "/agents")
      body = {
        agents: [
          {
            agentId: "research-assistant",
            model: "research-model",
            version: "1.0.0",
            codeHash: "0x" + "a".repeat(64),
            isActive: true,
            registeredAt: now - 86000,
          },
        ],
        total: 1,
      };
    else if (path === "/integrity/status")
      body = {
        projectId: 1,
        lastSweepAt: now - 30,
        stepsVerified: 48,
        batchesVerified: 12,
        batchesRootConfirmed: 12,
        coveragePercent: 100,
        openCritical: 0,
        openWarning: 0,
        detectors: [],
      };
    else if (path === "/alerts")
      body = {
        alerts: [
          {
            id: 1,
            projectId: 1,
            title: "Agent configuration drift",
            summary: "A configuration fingerprint changed.",
            severity: "warning",
            status: "open",
            subject: "agent:research-assistant",
            firstSeenAt: now,
            lastSeenAt: now,
            occurrenceCount: 1,
            evidence: { expected: "0xabc" },
          },
        ],
        totalOpen: 1,
        nextCursor: null,
      };
    else if (path === "/audit-log")
      body = {
        entries: [
          {
            entryId: 42,
            runId: runs[1].runId,
            agentId: "research-assistant",
            action: "Research completed",
            timestamp: now,
            stepIndex: 1,
            anchorStatus: "confirmed",
            txHash: "0x" + "b".repeat(64),
          },
        ],
        total: 1,
      };
    else if (path === "/chain-status")
      body = {
        connected: true,
        chainId: 31337,
        blockNumber: 123,
        contractsDeployed: 3,
      };
    else if (path === "/leaderboard")
      body = {
        agents: [
          {
            agentId: "research-assistant",
            avgScore: 94.5,
            bestScore: 99,
            runsCount: 11,
          },
        ],
        totalRuns: 12,
        runsConsidered: 12,
      };
    else if (path === "/orgs/1/members")
      body = {
        members: [
          {
            userId: 1,
            name: "Alex Morgan",
            email: "alex@example.com",
            role: "owner",
            joinedAt: now,
          },
        ],
      };
    else if (path === "/orgs/1/invitations") body = { invitations: [] };
    else if (path === "/api-keys")
      body =
        route.request().method() === "POST"
          ? {
              id: 1,
              raw_key: "tc_test_example_display_once",
              last_four: "once",
              scopes: ["runs:read"],
            }
          : { keys: [] };
    else if (path === "/me/notification-preferences")
      body = {
        orgId: 1,
        emailCritical: true,
        emailWarning: true,
        emailInfo: false,
        emailDigestOnly: false,
      };
    else if (path.startsWith("/steps/"))
      body = {
        stepId: 42,
        runId: runs[1].runId,
        leaf: "0xleaf",
        root: "0xroot",
        proof: [],
        anchorStatus: "confirmed",
        blockNumber: 123,
        txHash: "0xtx",
        blockHash: "0xblock",
        leafSchemaVersion: 2,
      };
    else if (path === "/integrity/verify-content")
      body = {
        matchesCurrent: true,
        matchesOriginal: null,
        computedHash: "0xhash",
      };
    else if (path.includes("/verify"))
      body = { isValid: true, isActive: true, hashMatches: true };
    else if (path === "/run-agent")
      body = { run_id: runs[1].runId, stream_url: "/stream/test" };
    else if (path.startsWith("/runs/"))
      body = {
        task: runs[1].task,
        score: 94,
        report: "A completed research report.",
      };
    await route.fulfill({ json: body });
  });
}
test("landing actions, protected session, and failed recovery are honest", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("link", { name: "Log In", exact: true }),
  ).toHaveAttribute("href", "/auth");
  await expect(
    page.getByRole("link", { name: "Get Started", exact: true }).first(),
  ).toHaveAttribute("href", "/auth?mode=signup");
  await page.route(`${API}/**`, (route) =>
    route.fulfill({ status: 401, json: { detail: "Sign in required" } }),
  );
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/auth\?next=/);
  await page.goto("/auth/forgot-password");
  await page.getByLabel("Email address").fill("alex@example.com");
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(page.locator(".p-error")).toContainText("Sign in required");
  await expect(
    page.getByText("a reset link is on its way", { exact: false }),
  ).toHaveCount(0);
});
test("all workspace pages, theme, search, and mobile navigation", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await workspace(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/dashboard");
  await expect(
    page.getByRole("heading", { name: "Welcome back, Alex." }),
  ).toBeVisible();
  await page.screenshot({
    path: "/tmp/trustchain-overview-light.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await expect(page.locator(".tc-app")).toHaveAttribute(
    "data-app-theme",
    "dark",
  );
  await page.screenshot({
    path: "/tmp/trustchain-overview-dark.png",
    fullPage: true,
  });
  await page.reload();
  await expect(page.locator(".tc-app")).toHaveAttribute(
    "data-app-theme",
    "dark",
  );
  await page.getByRole("button", { name: "Switch to light mode" }).click();
  for (const [path, title] of [
    ["runs", "Agent runs"],
    ["agents", "Agent registry"],
    ["audit", "Audit trail"],
    ["proofs", "Verification studio"],
    ["anchors", "On-chain anchors"],
    ["alerts", "Alert inbox"],
    ["team", "Team members"],
    ["keys", "API keys"],
    ["settings", "Settings"],
    ["trust-scores", "Trust scores"],
    ["help", "From your first agent to your first proof."],
  ]) {
    await page.goto(`/dashboard/${path}`);
    await expect(
      page.getByRole("heading", { name: title, exact: true }),
    ).toBeVisible();
    await expect(page.locator(".p-error")).toHaveCount(0);
  }
  await page.getByRole("button", { name: /Find a page/ }).click();
  await page.getByLabel("Search workspace pages").fill("agent registry");
  await page
    .getByRole("dialog")
    .getByRole("link", { name: "Agent registry" })
    .click();
  await expect(page).toHaveURL(/\/dashboard\/agents/);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/dashboard");
  await expect(
    page.getByRole("heading", { name: /Welcome back/ }),
  ).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    .toBeTruthy();
  await page.screenshot({ path: "/tmp/trustchain-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("link", { name: "Team members", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Team members", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
test("key creation, proof inspection, content verification, agents, and run launch", async ({
  page,
}) => {
  await workspace(page);
  await page.goto("/dashboard/keys");
  await page.getByRole("button", { name: "Create API key" }).click();
  await page.getByRole("button", { name: "Create key", exact: true }).click();
  await expect(
    page.getByText("tc_test_example_display_once", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "I’ve saved my key" }).click();
  await expect(
    page.getByText("tc_test_example_display_once", { exact: true }),
  ).toHaveCount(0);
  await page.goto("/dashboard/proofs?step=42");
  await page.getByRole("button", { name: "Retrieve proof" }).click();
  await expect(
    page.getByRole("heading", { name: "Proof for step #42" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Content verification", exact: true })
    .click();
  await page
    .getByLabel("Original content", { exact: false })
    .fill("This is the original text.");
  await page.getByRole("button", { name: "Run verification" }).click();
  await expect(page.getByRole("status")).toHaveText(
    "The supplied text matches the recorded content hash.",
  );
  await page.goto("/dashboard/agents");
  await page.getByRole("button", { name: "Inspect identity" }).click();
  await page.getByRole("button", { name: "Verify fingerprint" }).click();
  await expect(page.getByRole("status")).toContainText(
    "matches an active on-chain identity",
  );
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.goto("/dashboard/runs");
  await page.getByRole("button", { name: "New run", exact: true }).click();
  await page
    .getByLabel("What would you like to investigate?", { exact: false })
    .fill("Research verifiable AI");
  await page.getByRole("button", { name: "Start run", exact: true }).click();
  await expect(page.getByText("A completed research report.")).toBeVisible();
});
test("authentication layout is responsive", async ({ page }) => {
  await page.goto("/auth?mode=signup");
  await expect(
    page.getByRole("heading", { name: "Build on trust." }),
  ).toBeVisible();
  await page.screenshot({ path: "/tmp/trustchain-auth.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel("Full name")).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    )
    .toBeTruthy();
});

test("every workspace page fits a phone viewport", async ({ page }) => {
  await workspace(page);
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of [
    "runs",
    "agents",
    "audit",
    "proofs",
    "anchors",
    "alerts",
    "team",
    "keys",
    "settings",
    "trust-scores",
    "help",
  ]) {
    await page.goto(`/dashboard/${path}`);
    await expect(page.locator(".p-heading h1")).toBeVisible();
    await expect(page.locator(".p-loading")).toHaveCount(0);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
      path,
    ).toBeLessThanOrEqual(390);
  }
});
test("persisted failed runs stay failed and show the backend reason", async ({
  page,
}) => {
  await workspace(page);
  await page.route(`${API}/runs/failed_run`, (route) =>
    route.fulfill({ json: { message: "Model provider unavailable" } }),
  );
  await page.goto("/dashboard/runs/failed_run");
  await expect(page.locator(".p-error")).toContainText(
    "Model provider unavailable",
  );
  await expect(
    page.getByRole("heading", { name: "The run did not finish successfully" }),
  ).toBeVisible();
  await expect(
    page.locator(".p-badge").filter({ hasText: /^error$/ }),
  ).toHaveCount(2);
});
