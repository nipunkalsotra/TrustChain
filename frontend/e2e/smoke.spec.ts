import { expect, test } from "@playwright/test";
const API = "http://localhost:8000";
function uniqueEmail() {
  return `pw_${Date.now()}_${Math.random().toString(36).slice(2, 8)}@example.com`;
}
async function signup(page: import("@playwright/test").Page) {
  const email = uniqueEmail();
  await page.goto("/auth?mode=signup");
  await page.getByLabel("Full name").fill("Playwright Smoke");
  await page.getByLabel("Email address").fill(email);
  await page
    .getByPlaceholder("Enter your password", { exact: true })
    .fill("TrustChainBrowserTest9!");
  await page.getByLabel("Confirm password").fill("TrustChainBrowserTest9!");
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 30000 });
  await expect(
    page.getByRole("heading", { name: "Welcome back, Playwright." }),
  ).toBeVisible();
  return email;
}
test("real signup, session cookies, workspace pages, settings, logout, and login", async ({
  page,
}) => {
  const email = await signup(page);
  const stored = await page.evaluate(() => localStorage.getItem("tc_session"));
  expect(stored).toContain(email);
  expect(stored).not.toContain("token");
  const cookies = await page.context().cookies();
  expect(cookies.find((c) => c.name === "tc_access")?.httpOnly).toBe(true);
  await page.screenshot({
    path: "/tmp/trustchain-live-workspace.png",
    fullPage: true,
  });
  for (const [path, heading] of [
    ["runs", "Agent runs"],
    ["agents", "Agent registry"],
    ["audit", "Audit trail"],
    ["alerts", "Alert inbox"],
    ["team", "Team members"],
    ["keys", "API keys"],
    ["settings", "Settings"],
  ]) {
    await page.goto(`/dashboard/${path}`);
    await expect(
      page.getByRole("heading", { name: heading, exact: true }),
    ).toBeVisible();
    await expect(page.locator(".p-loading")).toHaveCount(0);
    await expect(page.locator(".p-error")).toHaveCount(0);
  }
  await page.getByRole("button", { name: "New project", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByLabel("Name", { exact: true })
    .fill("Browser test project");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Create", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Browser test project", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Open project", exact: true }).click();
  await expect(page.getByLabel("Active project")).toHaveValue(/\d+/);
  await expect(
    page.getByLabel("Active project").locator("option:checked"),
  ).toHaveText("Browser test project");
  await page
    .getByRole("button", { name: "Notifications", exact: true })
    .click();
  const toggle = page.getByRole("switch", {
    name: "Informational alerts",
    exact: true,
  });
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await page.reload();
  await page
    .getByRole("button", { name: "Notifications", exact: true })
    .click();
  await expect(
    page.getByRole("switch", { name: "Informational alerts", exact: true }),
  ).toHaveAttribute("aria-checked", "true");
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/auth$/);
  expect(
    await page.evaluate(() => localStorage.getItem("tc_session")),
  ).toBeNull();
  expect(
    (await page.context().cookies()).find((c) => c.name === "tc_access"),
  ).toBeUndefined();
  await page.getByLabel("Email address").fill(email);
  await page
    .getByPlaceholder("Enter your password", { exact: true })
    .fill("TrustChainBrowserTest9!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
});
test("real protected route and stale display cache cannot authenticate", async ({
  page,
}) => {
  await page.goto("/auth");
  await page.evaluate(() =>
    localStorage.setItem(
      "tc_session",
      JSON.stringify({ name: "Stale User", email: "stale@example.com" }),
    ),
  );
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/auth\?next=/);
});
test("real pipeline launch returns a signed stream and rejects an unsigned stream", async ({
  page,
  request,
}) => {
  await signup(page);
  const csrf = (await page.context().cookies()).find(
    (c) => c.name === "tc_csrf",
  )?.value;
  const res = await page.request.post(`${API}/run-agent`, {
    data: { task: "Summarize what an audit trail is in one sentence." },
    headers: { "X-CSRF-Token": csrf! },
  });
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  expect(body.run_id).toBeTruthy();
  expect(body.stream_url).toContain("token=");
  const denied = await request.get(`${API}/stream/${body.run_id}`);
  expect(denied.status()).toBe(401);
  await page.goto(`/dashboard/runs/${body.run_id}`);
  await expect(
    page.getByRole("heading", { name: "Run overview", exact: true }),
  ).toBeVisible();
});

test('browser preflights allow workspace mutation methods', async ({ request }) => {
  for (const method of ['PUT', 'PATCH', 'DELETE']) {
    const res = await request.fetch(`${API}/me/notification-preferences`, {
      method: 'OPTIONS', headers: { Origin: 'http://localhost:3000', 'Access-Control-Request-Method': method, 'Access-Control-Request-Headers': 'content-type,x-csrf-token' },
    })
    expect(res.ok()).toBeTruthy()
    expect(res.headers()['access-control-allow-methods']).toContain(method)
    expect(res.headers()['access-control-allow-origin']).toBe('http://localhost:3000')
  }
})
