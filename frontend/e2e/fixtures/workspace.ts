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
export async function workspace(page: import("@playwright/test").Page) {
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
