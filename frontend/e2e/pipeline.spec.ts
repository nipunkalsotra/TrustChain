import { expect, test } from "@playwright/test";

// Opt-in only: uses real model/search services and the configured chain.
test("a dashboard-launched pipeline completes with a report and confirmed evidence", async ({
  page,
}) => {
  test.skip(
    process.env.TRUSTCHAIN_FULL_PIPELINE !== "1",
    "Set TRUSTCHAIN_FULL_PIPELINE=1 with the complete local stack running.",
  );
  test.setTimeout(300000);
  await page.goto("/auth?mode=signup");
  await page.getByLabel("Full name").fill("Pipeline Browser Check");
  await page
    .getByLabel("Email address")
    .fill(`pw_pipeline_${Date.now()}@example.com`);
  await page
    .getByPlaceholder("Enter your password", { exact: true })
    .fill("TrustChainBrowserTest9!");
  await page.getByLabel("Confirm password").fill("TrustChainBrowserTest9!");
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 30000 });
  await page.getByRole("button", { name: "New run", exact: true }).click();
  await page
    .getByLabel("What would you like to investigate?", { exact: false })
    .fill(
      "Research two practical benefits and one limitation of Merkle trees for AI audit logs. Include source links and keep the final report under 250 words.",
    );
  await page.getByRole("button", { name: "Start run", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard\/runs\/[^/]+$/, { timeout: 30000 });
  const runId = decodeURIComponent(
    new URL(page.url()).pathname.split("/").pop()!,
  );
  console.log(`Dashboard started run ${runId}`);
  await expect(page.locator(".p-report")).toBeVisible({ timeout: 180000 });
  await expect(page.locator(".p-report")).toContainText(/https?:\/\//);
  await expect(
    page.locator(".p-badge").filter({ hasText: /^complete$/ }),
  ).toHaveCount(2);
  await expect(page.locator(".p-error")).toHaveCount(0);
  const resultResponse = await page.request.get(
    `http://localhost:8000/runs/${encodeURIComponent(runId)}`,
  );
  expect(resultResponse.ok()).toBeTruthy();
  const result = await resultResponse.json();
  expect(result.score).toBeGreaterThan(0);
  expect(result.score).toBeLessThanOrEqual(100);
  console.log(
    `Completed run ${runId}; score ${result.score}; report ${result.report.length} characters`,
  );
  await page.screenshot({
    path: "/tmp/trustchain-real-completed-run.png",
    fullPage: true,
  });
  type Entry = { entryId: number; anchorStatus: string; txHash: string };
  let entries: Entry[] = [];
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `http://localhost:8000/audit-log?run_id=${encodeURIComponent(runId)}`,
        );
        expect(response.ok()).toBeTruthy();
        entries = (await response.json()).entries;
        return (
          entries.length >= 4 &&
          entries.every(
            (e) =>
              e.anchorStatus === "confirmed" &&
              /^0x[0-9a-fA-F]{64}$/.test(e.txHash),
          )
        );
      },
      { timeout: 120000, intervals: [2000, 5000] },
    )
    .toBeTruthy();
  console.log(`${entries.length} audit steps confirmed on-chain`);
  await page.getByRole("link", { name: "Verify this run" }).click();
  await page.getByRole("button", { name: "Run verification" }).click();
  await expect(page.getByRole("status")).toContainText(
    "recorded steps passed the integrity check",
    { timeout: 60000 },
  );
  await page.screenshot({
    path: "/tmp/trustchain-real-run-verification.png",
    fullPage: true,
  });
  await page.goto(`/dashboard/proofs?step=${entries[0].entryId}`);
  await page.getByRole("button", { name: "Retrieve proof" }).click();
  await expect(
    page.getByRole("heading", {
      name: `Proof for step #${entries[0].entryId}`,
    }),
  ).toBeVisible();
  const proofResponse = await page.request.get(
    `http://localhost:8000/steps/${entries[0].entryId}/proof`,
  );
  expect(proofResponse.ok()).toBeTruthy();
  const proof = await proofResponse.json();
  expect(proof.anchorStatus).toBe("confirmed");
  expect(proof.root).toMatch(/^0x[0-9a-fA-F]{64}$/);
  expect(proof.txHash).toBe(entries[0].txHash);
  console.log(
    `Proof retrieved for step ${proof.stepId}; block ${proof.blockNumber}; tx ${proof.txHash}`,
  );
  await page.screenshot({
    path: "/tmp/trustchain-real-step-proof.png",
    fullPage: true,
  });
  await page.goto(`/dashboard/runs/${encodeURIComponent(runId)}`);
  await expect(page.locator(".p-report")).toContainText(
    result.report.slice(0, 80),
  );
  await expect(page.locator(".p-error")).toHaveCount(0);
});
