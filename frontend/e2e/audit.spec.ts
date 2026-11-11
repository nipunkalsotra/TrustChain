import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

// Real-service audit. Requires an isolated stack with EMAIL_BACKEND=console.
// TRUSTCHAIN_AUDIT=1 TRUSTCHAIN_AUDIT_EMAIL_LOG=/path/to/api.log npx playwright test e2e/audit.spec.ts --workers=1
const API = "http://localhost:8000";
const password = "TrustChainBrowserTest9!";
test.skip(process.env.TRUSTCHAIN_AUDIT !== "1", "Opt in with an isolated real stack and console email log.");
test.setTimeout(90000);

function uniqueEmail() { return `audit_${Date.now()}_${Math.random().toString(36).slice(2, 8)}@example.com`; }
async function signup(page: Page, email = uniqueEmail()) {
  await page.goto("/auth?mode=signup");
  await page.getByLabel("Full name").fill("Audit Tester");
  await page.getByLabel("Email address").fill(email);
  await page.getByPlaceholder("Enter your password", { exact: true }).fill(password);
  await page.getByLabel("Confirm password").fill(password);
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 30000 });
  return email;
}
async function mail(email: string, type: "verify-email" | "reset-password" | "invite") {
  let line = "";
  await expect.poll(() => {
    const log = readFileSync(process.env.TRUSTCHAIN_AUDIT_EMAIL_LOG!, "utf8").replace(/\x1b\[[0-9;]*m/g, "");
    line = log.split("\n").filter(l => l.includes("email_console_send") && l.includes(email) && l.includes(`/${type}/`)).at(-1) ?? "";
    return Boolean(line);
  }).toBeTruthy();
  const match = line.match(new RegExp(`http://localhost:3000/(?:auth/)?${type}/([A-Za-z0-9_-]+)`));
  expect(match).not.toBeNull();
  return { token: match![1], url: match![0] };
}
async function verified(page: Page) {
  const email = await signup(page);
  const { token } = await mail(email, "verify-email");
  // Intentionally visit the implemented route to inspect other workflows;
  // separate tests below exercise the actual emailed links without rewriting.
  await page.goto(`/auth/verify-email/${token}`);
  await expect(page.getByText("Your email is verified.", { exact: false })).toBeVisible();
  await page.getByRole("link", { name: "Open workspace", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  return email;
}
async function csrf(page: Page) {
  return { "X-CSRF-Token": (await page.context().cookies()).find(c => c.name === "tc_csrf")!.value };
}
async function createKey(page: Page) {
  await page.goto("/dashboard/keys");
  await page.getByRole("button", { name: "Create API key", exact: true }).click();
  for (const box of await page.getByRole("dialog").getByRole("checkbox").all()) await box.check();
  const response = page.waitForResponse(r => r.url() === `${API}/api-keys` && r.request().method() === "POST");
  await page.getByRole("button", { name: "Create key", exact: true }).click();
  const key = (await (await response).json()).raw_key as string;
  await expect(page.getByText(key, { exact: true })).toBeVisible();
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], { origin: "http://localhost:3000" });
  await page.getByRole("button", { name: "Copy to clipboard" }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(key);
  await page.getByRole("button", { name: "I’ve saved my key" }).click();
  return key;
}
test.afterEach(async ({ page }, info) => {
  if (info.status !== info.expectedStatus) await page.screenshot({ path: info.outputPath("failure.png"), fullPage: true });
});

test("verification email opens a working verification page", async ({ page }) => {
  const email = await signup(page);
  const message = await mail(email, "verify-email");
  const response = await page.goto(message.url);
  expect(response!.status(), "The link actually sent in the email must work").toBe(200);
  await expect(page.getByText("Your email is verified.", { exact: false })).toBeVisible();
});

test("password reset email opens a working reset form", async ({ page }) => {
  const email = await signup(page);
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page.goto("/auth/forgot-password");
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(page.getByText("a reset link is on its way", { exact: false })).toBeVisible();
  const message = await mail(email, "reset-password");
  const response = await page.goto(message.url);
  expect(response!.status(), "The link actually sent in the email must work").toBe(200);
  await expect(page.getByLabel("New password", { exact: true })).toBeVisible();
});

test("implemented verification and reset forms enforce token use and allow new-password login", async ({ page }) => {
  const email = await verified(page);
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page.goto("/auth/forgot-password");
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Send reset link" }).click();
  const { token } = await mail(email, "reset-password");
  await page.goto(`/auth/reset-password/${token}`);
  await page.getByLabel("New password", { exact: true }).fill("ChangedBrowserPassword7!");
  await page.getByLabel("Confirm new password").fill("mismatched");
  await page.getByRole("button", { name: "Update password" }).click();
  await expect(page.locator(".p-error")).toContainText("Passwords do not match");
  await page.getByLabel("Confirm new password").fill("ChangedBrowserPassword7!");
  await page.getByRole("button", { name: "Update password" }).click();
  await expect(page.getByText("Your password has been updated.", { exact: false })).toBeVisible();
  await page.getByRole("link", { name: "Back to sign in" }).click();
  await page.getByLabel("Email address").fill(email);
  await page.getByPlaceholder("Enter your password", { exact: true }).fill("ChangedBrowserPassword7!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  const reused = await page.request.post(`${API}/auth/reset-password/${token}`, { data: { new_password: password } });
  expect(reused.ok()).toBe(false);
});

test("organization and project create, rename, switch, and persistence", async ({ page }) => {
  await verified(page);
  await page.goto("/dashboard/settings");
  await page.getByLabel("Organization name").fill("Audit organization renamed");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByLabel("Organization name")).toHaveValue("Audit organization renamed");
  await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();
  await page.reload();
  await expect(page.getByLabel("Organization name")).toHaveValue("Audit organization renamed");
  await page.getByRole("button", { name: "New project", exact: true }).click();
  await page.getByRole("dialog").getByLabel("Name", { exact: true }).fill("Audit project");
  await page.getByRole("dialog").getByRole("button", { name: "Create", exact: true }).click();
  const row = page.locator(".p-row").filter({ has: page.getByRole("heading", { name: "Audit project", exact: true }) });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Rename", exact: true }).click();
  await page.getByRole("dialog").getByLabel("Name", { exact: true }).fill("Renamed audit project");
  await page.getByRole("button", { name: "Save name", exact: true }).click();
  await page.locator(".p-row").filter({ hasText: "Renamed audit project" }).getByRole("button", { name: "Open project", exact: true }).click();
  await expect(page.getByLabel("Active project").locator("option:checked")).toHaveText("Renamed audit project");
  await page.getByRole("button", { name: "Create organization", exact: true }).click();
  await page.getByRole("dialog").getByLabel("Name", { exact: true }).fill("Second audit org");
  await page.getByRole("dialog").getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByLabel("Active project").locator("optgroup[label='Second audit org']")).toHaveCount(1);
  const option = await page.getByLabel("Active project").locator("optgroup[label='Second audit org'] option").getAttribute("value");
  const switched = page.waitForResponse(r => r.url() === `${API}/auth/switch-project` && r.request().method() === "POST");
  await page.getByLabel("Active project").selectOption(option!);
  expect((await switched).ok()).toBe(true);
  await expect(page.getByLabel("Organization name")).toHaveValue("Second audit org");
  await page.reload();
  await expect(page.getByLabel("Organization name")).toHaveValue("Second audit org");
});

test("UI-created API key drives SDK logging, chain proof, browser evidence, export, and revocation", async ({ page }) => {
  await verified(page);
  const key = await createKey(page);
  const { TrustChain } = await import("../../sdk/typescript/dist/index.js");
  const sdk = new TrustChain(key, { baseUrl: API, onError: "raise" });
  const receipt = await sdk.logAndWait({ agentId: "browser-sdk-agent", action: "audit_answer", input: "browser question", output: "verified browser answer" });
  expect(receipt.stepId).toBeGreaterThan(0);
  await expect.poll(async () => {
    const response = await page.request.get(`${API}/steps/${receipt.stepId}/proof`);
    return response.ok() && (await response.json()).anchorStatus === "confirmed";
  }, { timeout: 45000 }).toBeTruthy();
  await page.goto(`/dashboard/proofs?step=${receipt.stepId}`);
  await page.getByRole("button", { name: "Retrieve proof" }).click();
  await expect(page.getByRole("heading", { name: `Proof for step #${receipt.stepId}` })).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export JSON" }).click()]);
  expect(download.suggestedFilename()).toContain(".json");
  const downloadPath = await download.path();
  expect(JSON.parse(readFileSync(downloadPath!, "utf8")).stepId).toBe(receipt.stepId);
  await page.getByRole("button", { name: "Content verification", exact: true }).click();
  await page.getByLabel("Original content", { exact: false }).fill("verified browser answer");
  await page.getByRole("button", { name: "Run verification" }).click();
  await expect(page.getByRole("status")).toContainText("matches the recorded content hash");
  await page.getByLabel("Original content", { exact: false }).fill("modified answer");
  await page.getByRole("button", { name: "Run verification" }).click();
  await expect(page.getByRole("status")).toContainText("does not match");
  await page.goto("/dashboard/audit");
  await page.getByLabel("Search agents, actions, or runs…").fill("browser-sdk-agent");
  await page.getByLabel("Anchor status").selectOption("confirmed");
  await expect(page.getByRole("cell", { name: "browser-sdk-agent", exact: true })).toBeVisible();
  const py = 'import json,sys; from trustchain_sdk import TrustChain; tc=TrustChain(sys.argv[1],base_url=sys.argv[2],on_error="raise"); r=tc.log_and_wait(agent_id="python-browser-agent",action="answer",input="q",output="python browser answer"); print(json.dumps({"stepId":r.step_id})); tc.close()';
  const python = resolve(process.cwd(), "../.venv/bin/python");
  const pythonReceipt = JSON.parse(execFileSync(python, ["-c", py, key, API], { encoding: "utf8", timeout: 15000 }));
  expect(pythonReceipt.stepId).toBeGreaterThan(0);
  await page.goto("/dashboard/audit");
  await page.getByLabel("Search agents, actions, or runs…").fill("python-browser-agent");
  await expect(page.getByRole("cell", { name: "python-browser-agent", exact: true })).toBeVisible();
  await page.goto("/dashboard/keys");
  await page.getByRole("button", { name: "Revoke", exact: true }).click();
  await page.getByRole("button", { name: "Revoke key", exact: true }).click();
  await expect(page.getByText("revoked", { exact: true })).toBeVisible();
  const denied = await page.request.get(`${API}/agents`, { headers: { Authorization: `Bearer ${key}` } });
  expect(denied.status()).toBe(401);
  await sdk.close();
});

test("agent UI registration and real on-chain fingerprint match and mismatch", async ({ page }) => {
  await verified(page);
  await page.goto("/dashboard/agents");
  await page.getByRole("button", { name: "Register agent", exact: true }).click();
  await page.getByLabel("Agent ID", { exact: true }).fill("ui-audit-agent");
  await page.getByLabel("Model", { exact: true }).fill("audit-model");
  await page.getByLabel("Configuration hash", { exact: false }).fill(`0x${"a".repeat(64)}`);
  await page.getByRole("dialog").getByRole("button", { name: "Register agent", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "ui-audit-agent", exact: true })).toBeVisible({ timeout: 45000 });
  await page.getByRole("button", { name: "Inspect identity" }).click();
  await page.getByRole("button", { name: "Verify fingerprint" }).click();
  await expect(page.getByRole("dialog").getByRole("status")).toContainText("matches an active on-chain identity");
  await page.getByLabel("Fingerprint to verify", { exact: false }).fill(`0x${"b".repeat(64)}`);
  await page.getByRole("button", { name: "Verify fingerprint" }).click();
  await expect(page.getByRole("dialog").getByRole("status")).toContainText("Verification did not pass");
});

test("SDK identity drift reaches the alert inbox and supports acknowledge, resolve, and reopen", async ({ page }) => {
  await verified(page);
  const key = await createKey(page);
  const { TrustChain, TrustChainClient } = await import("../../sdk/typescript/dist/index.js");
  const sdk = new TrustChain(key, { baseUrl: API, onError: "raise" });
  const agentId = "alert-audit-agent";
  await sdk.registerAgent({ agentId, model: "m", version: "1", systemPrompt: "original" });
  await expect.poll(async () => {
    const body = await (await page.request.get(`${API}/agents`)).json();
    return body.agents.some((agent: {agentId: string}) => agent.agentId === agentId);
  }, { timeout: 30000 }).toBeTruthy();
  const client = new TrustChainClient(key, { baseUrl: API });
  await client.logStep({ runId: `audit_drift_${Date.now()}`, agentId, action: "drift", input: "q", output: "a", agentCodeHash: `0x${"f".repeat(64)}` });
  await page.goto("/dashboard/alerts");
  await page.getByLabel("Severity", { exact: true }).selectOption("warning");
  const alert = page.getByRole("button", { name: /Agent 'alert-audit-agent' logged a step/ });
  await alert.click();
  await page.getByText("Inspect evidence", { exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("registeredHash");
  await page.getByLabel("Investigation note").fill("Browser audit acknowledges identity drift");
  await page.getByRole("button", { name: "Acknowledge", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("acknowledged");
  await page.getByRole("button", { name: "Acknowledged", exact: true }).click();
  await alert.click();
  await page.getByLabel("Investigation note").fill("Audited test identity changed intentionally");
  await page.getByRole("button", { name: "Resolve alert", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("resolved");
  await page.getByRole("button", { name: "Resolved", exact: true }).click();
  await alert.click();
  await page.getByRole("button", { name: "Reopen alert", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("reopened");
  await page.getByRole("button", { name: /^Open \(/ }).click();
  await expect(alert).toBeVisible();
  await sdk.close();
});

test("invite acceptance, role changes, viewer restrictions, and member removal", async ({ page, browser }) => {
  await verified(page);
  const owner = await (await page.request.get(`${API}/me`)).json();
  const email = uniqueEmail();
  await page.goto("/dashboard/team");
  await page.getByRole("button", { name: "Invite teammate" }).click();
  await page.getByRole("dialog").getByLabel("Email address").fill(email);
  await page.getByRole("dialog").getByLabel("Role", { exact: false }).selectOption("viewer");
  await page.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const invitation = await mail(email, "invite");
  const context = await browser.newContext();
  const invited = await context.newPage();
  try {
    await signup(invited, email);
    await invited.goto(invitation.url);
    await invited.getByRole("button", { name: "Accept invitation" }).click();
    await expect(invited).toHaveURL(/\/dashboard$/);
    const me = await (await invited.request.get(`${API}/me`)).json();
    expect(me.active.orgId).toBe(owner.active.orgId);
    expect(me.active.role).toBe("viewer");
    await invited.goto("/dashboard/runs");
    await expect(invited.getByRole("button", { name: "New run", exact: true })).toHaveCount(0);
    await invited.goto("/dashboard/keys");
    await expect(invited.getByRole("button", { name: "Create API key", exact: true })).toHaveCount(0);
    await page.reload();
    await page.getByLabel("Role for Audit Tester", { exact: true }).selectOption("member");
    await expect(page.getByRole("status")).toContainText("Changes saved");
    await invited.reload();
    await invited.goto("/dashboard/runs");
    await expect(invited.locator(".p-heading").getByRole("button", { name: "New run", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Remove Audit Tester", exact: true }).click();
    await page.getByRole("button", { name: "Remove member", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("Member removed");
    const denied = await invited.request.get(`${API}/runs`);
    expect([401, 403]).toContain(denied.status());
  } finally { await context.close(); }
});

test("invalid tokens, unknown run, and automatic access-cookie refresh", async ({ page }) => {
  await page.goto("/auth/verify-email/invalid-audit-token");
  await expect(page.locator(".p-error")).toBeVisible();
  await page.goto("/invite/invalid-audit-token");
  await expect(page.locator(".p-error")).toBeVisible();
  await verified(page);
  await page.context().clearCookies({ name: "tc_access" });
  await page.reload();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("heading", { name: "Welcome back, Audit." })).toBeVisible();
  expect((await page.context().cookies()).some(c => c.name === "tc_access")).toBe(true);
  await page.goto("/dashboard/runs/unknown-audit-run");
  await expect(page.locator(".p-error")).toBeVisible();
  await page.screenshot({ path: "/tmp/trustchain-audit/unknown-run.png", fullPage: true });
  const response = await page.request.post(`${API}/run-agent`, { headers: await csrf(page), data: { task: "" } });
  expect(response.status()).toBe(422);
});

test("run and audit filtering, pagination, and real JSON downloads", async ({ page }) => {
  await verified(page);
  const key = await createKey(page);
  const { TrustChainClient } = await import("../../sdk/typescript/dist/index.js");
  const client = new TrustChainClient(key, { baseUrl: API });
  const prefix = `audit_pages_${Date.now()}`;
  for (let i = 0; i < 25; i++) await client.logStep({ runId: `${prefix}_${i}`, agentId: "pagination-agent", action: `answer_${i}`, input: "q", output: "a" });
  await page.goto("/dashboard/runs");
  await expect(page.locator("tbody tr")).toHaveCount(10);
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.locator(".p-table-foot")).toContainText("2 / 3");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.locator("tbody tr")).toHaveCount(5);
  await expect(page.getByRole("button", { name: "Next", exact: true })).toBeDisabled();
  await page.getByLabel("Search tasks or run IDs…").fill(`${prefix}_24`);
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.getByLabel("Search tasks or run IDs…").fill("");
  await page.getByRole("button", { name: /^Completed / }).click();
  await expect(page.getByRole("heading", { name: "No matching runs" })).toBeVisible();
  await page.getByRole("button", { name: /^All runs / }).click();
  await expect(page.locator("tbody tr")).toHaveCount(10);
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export JSON" }).click()]);
  expect(JSON.parse(readFileSync((await download.path())!, "utf8"))).toHaveLength(25);
  await page.goto("/dashboard/audit");
  await expect(page.locator("tbody tr")).toHaveCount(20);
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.locator("tbody tr")).toHaveCount(5);
  await page.getByRole("button", { name: "Previous", exact: true }).click();
  await expect(page.locator("tbody tr")).toHaveCount(20);
  await page.getByLabel("Search agents, actions, or runs…").fill("absent-audit-agent");
  await expect(page.getByRole("heading", { name: "No matching events" })).toBeVisible();
});

test("all notification preferences persist, and invitations resend and revoke", async ({ page }) => {
  await verified(page);
  await page.goto("/dashboard/settings");
  await page.getByRole("button", { name: "Notifications", exact: true }).click();
  const changed: Record<string, string> = {};
  for (const name of ["Critical findings", "Warnings", "Informational alerts", "Digest mode"]) {
    const toggle = page.getByRole("switch", { name, exact: true });
    const next = (await toggle.getAttribute("aria-checked")) === "true" ? "false" : "true";
    await toggle.click();
    await expect(page.getByRole("switch", { name, exact: true })).toHaveAttribute("aria-checked", next);
    changed[name] = next;
  }
  await page.reload();
  await page.getByRole("button", { name: "Notifications", exact: true }).click();
  for (const [name, value] of Object.entries(changed)) await expect(page.getByRole("switch", { name, exact: true })).toHaveAttribute("aria-checked", value);
  await page.goto("/dashboard/team");
  const email = uniqueEmail();
  await page.getByRole("button", { name: "Invite teammate" }).click();
  await page.getByRole("dialog").getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const invitation = await mail(email, "invite");
  await page.getByRole("button", { name: "Resend", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Invitation resent");
  await page.getByRole("button", { name: "Revoke", exact: true }).click();
  await expect(page.getByText("revoked", { exact: true })).toBeVisible();
  const response = await page.request.get(`${API}/invitations/${invitation.token}`);
  expect(response.ok()).toBe(false);
});

test("concurrent SDK registrations return a receipt for each independent agent", async ({ page }) => {
  await verified(page);
  const key = await createKey(page);
  const { TrustChainClient } = await import("../../sdk/typescript/dist/index.js");
  const client = new TrustChainClient(key, { baseUrl: API });
  const results = await Promise.allSettled(Array.from({ length: 4 }, (_, i) =>
    client.registerAgent(`concurrent-audit-agent-${i}`, `0x${"c".repeat(64)}`, "audit-model", "1")
  ));
  const statuses = results.map(r => r.status === "fulfilled" ? "confirmed" : `failed: ${r.reason.statusCode}`);
  const keys = await (await page.request.get(`${API}/api-keys`)).json();
  for (const entry of keys.keys) await page.request.delete(`${API}/api-keys/${entry.id}`, { headers: await csrf(page) });
  expect(statuses, "Independent concurrent registrations must not collide on signer nonces").toEqual(Array(4).fill("confirmed"));
});

test("completed pipeline workspace pages work on mobile and show the active evidence chain", async ({ page }) => {
  const log = readFileSync(process.env.TRUSTCHAIN_AUDIT_EMAIL_LOG!, "utf8");
  const email = Array.from(log.matchAll(/pw_pipeline_\d+@example\.com/g)).at(-1)?.[0];
  test.skip(!email, "Run pipeline.spec.ts first to create a completed pipeline account.");
  await page.goto("/auth");
  await page.getByLabel("Email address").fill(email!);
  await page.getByPlaceholder("Enter your password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await page.goto("/dashboard/trust-scores");
  await expect(page.locator("tbody tr")).toHaveCount(4);
  await page.setViewportSize({ width: 390, height: 844 });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  for (const path of ["runs", "agents", "audit", "proofs", "anchors", "alerts", "team", "keys", "settings", "trust-scores", "help"]) {
    await page.goto(`/dashboard/${path}`);
    await expect(page.locator(".p-heading h1")).toBeVisible();
    await expect(page.locator(".p-loading")).toHaveCount(0);
    await expect(page.locator(".p-error")).toHaveCount(0);
    expect.soft(await page.evaluate(() => document.documentElement.scrollWidth), path).toBeLessThanOrEqual(390);
  }
  expect(errors).toEqual([]);
  await page.goto("/dashboard/anchors");
  await expect(page.locator("tbody tr")).not.toHaveCount(0);
  const rpc = await page.request.post("http://localhost:8545", { data: { jsonrpc: "2.0", method: "eth_chainId", params: [], id: 1 } });
  const chainId = parseInt((await rpc.json()).result, 16);
  await page.screenshot({ path: "/tmp/trustchain-audit/live-mobile-anchors.png", fullPage: true });
  await expect(page.locator(".p-stat").filter({ hasText: "Pipeline chain" }).locator(".p-stat-value")).toHaveText(`#${chainId}`);
});
