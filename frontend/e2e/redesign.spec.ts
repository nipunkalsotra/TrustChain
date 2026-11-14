import { test, expect } from "@playwright/test";
import { workspace } from "./fixtures/workspace";
import { resolve } from "node:path";
const shots = resolve(process.cwd(), "../artifacts/redesign/screenshots");

test("landing pipeline, hash demonstration, links, and motion persistence", async ({ page }) => {
  const errors: string[]=[];page.on("pageerror",e=>errors.push(e.message));
  await page.setViewportSize({width:1600,height:1000});await page.goto("/");
  await expect(page.getByRole("heading",{name:"Intelligence, with a proof."})).toBeVisible();
  await page.waitForTimeout(1000);await page.screenshot({animations:"disabled",path:shots+"/landing-desktop.png"});
  await page.getByRole("link",{name:"Explore the platform",exact:true}).click();
  for(const [name,copy]of [["Validator","Question the output."],["Scorer","Measure the outcome."],["Reporter","Make it accountable."],["Researcher","Find the signal."]]){
    await page.getByRole("tab",{name:new RegExp(name)}).click();await expect(page.getByRole("tabpanel")).toContainText(copy);
    await expect(page.getByRole("tab",{name:new RegExp(name)})).toHaveAttribute("aria-selected","true");
  }
  await page.getByRole("tab", {name:/Researcher/}).press("ArrowRight");
  await expect(page.getByRole("tab", {name:/Validator/})).toHaveAttribute("aria-selected", "true");
  await page.locator("#product").screenshot({animations:"disabled",path:shots+"/landing-pipeline.png"});
  await page.getByRole("button",{name:"Alter the record"}).click();await expect(page.getByRole("status")).toContainText("The record changed");
  await expect.poll(async()=>{const h=await page.locator(".m-hash-row code").allTextContents();return h[0]!==h[1]}).toBeTruthy();
  await page.locator("#security").screenshot({animations:"disabled",path:shots+"/landing-integrity.png"});
  await page.getByRole("button",{name:"Restore original"}).click();
  await expect.poll(async()=>{const h=await page.locator(".m-hash-row code").allTextContents();return h[0]===h[1]}).toBeTruthy();
  await page.getByRole("button",{name:"Pause animations"}).click();await expect(page.locator("html")).toHaveAttribute("data-motion","paused");
  await page.reload();await expect(page.getByRole("button",{name:"Resume animations"})).toHaveAttribute("aria-pressed","true");
  await page.getByRole("button",{name:"Resume animations"}).click();
  await page.getByRole("link",{name:"Get Started",exact:true}).first().click();await expect(page).toHaveURL(/auth\?mode=signup/);expect(errors).toEqual([]);
});

test("public pages fit phones and tablets with working mobile menus and return links",async({page})=>{
  for(const width of [320,390,768]){
    await page.setViewportSize({width,height:900});await page.goto("/");await expect(page.getByRole("heading",{level:1})).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth),`landing ${width}`).toBeLessThanOrEqual(width);
    await page.getByRole("button",{name:"Open menu"}).click();await page.getByRole("link",{name:"Why trust us"}).click();
    await expect(page.getByRole("button",{name:"Open menu"})).toHaveAttribute("aria-expanded","false");await expect(page).toHaveURL(/#security$/);
    for(const route of ["/pricing","/coming-soon?from=docs","/unknown-page"]){
      await page.goto(route);await expect(page.getByRole("heading",{level:1})).toBeVisible();
      expect(await page.evaluate(()=>document.documentElement.scrollWidth),`${route} ${width}`).toBeLessThanOrEqual(width);
      await page.getByRole("link",{name:"Return to TrustChain"}).click();await expect(page).toHaveURL("/");
    }
  }
  await page.setViewportSize({width:390,height:844});await page.goto("/");await page.waitForTimeout(900);await page.screenshot({animations:"disabled",path:shots+"/landing-mobile.png",fullPage:true});
});

test("every workspace route fits 320, 768, and 1600 pixels without runtime errors",async({page})=>{
  test.setTimeout(180000);await workspace(page);const errors:string[]=[];page.on("pageerror",e=>errors.push(e.message));
  for(const width of [1600,768,320]){
    await page.setViewportSize({width,height:1000});
    for(const path of ["","runs","agents","trust-scores","audit","proofs","anchors","alerts","team","keys","settings","help"]){
      await page.goto("/dashboard"+(path?"/"+path:""));await expect(page.locator(".p-heading h1")).toBeVisible();await expect(page.locator(".p-loading")).toHaveCount(0);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth),`${path} at ${width}`).toBeLessThanOrEqual(width);await expect(page.locator(".p-error")).toHaveCount(0);
      if(width===1600)await page.screenshot({animations:"disabled",path:shots+`/workspace-${path||"overview"}.png`,fullPage:true});
      if(width===320&&["","proofs","agents"].includes(path))await page.screenshot({animations:"disabled",path:shots+`/mobile-${path||"overview"}.png`,fullPage:true});
    }
  }expect(errors).toEqual([]);
});

test("keyboard search, dialog escape, and reduced motion remain usable",async({page})=>{
  await workspace(page);await page.emulateMedia({reducedMotion:"reduce"});await page.goto("/");await expect(page.getByRole("button",{name:"Resume animations"})).toBeVisible();await expect(page.locator(".m-hero h1")).toBeVisible();
  await page.goto("/dashboard");await page.keyboard.press("Control+k");await expect(page.getByRole("dialog")).toBeVisible();await page.getByLabel("Search workspace pages").fill("no matching route");await expect(page.getByText("No matching pages.")).toBeVisible();await page.keyboard.press("Escape");await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.goto("/dashboard/keys");await page.getByRole("button",{name:"Create API key",exact:true}).click();await page.keyboard.press("Escape");await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.setViewportSize({width:390,height:844});await page.getByRole("button",{name:"Open navigation"}).click();await page.keyboard.press("Escape");await expect(page.getByRole("button",{name:"Open navigation"})).toHaveAttribute("aria-expanded","false");
});

test("signup validation, password visibility, and legacy route redirects",async({page})=>{
  await page.goto("/auth?mode=signup");await page.getByLabel("Full name").fill("Design Tester");await page.getByLabel("Email address").fill("design@example.com");await page.getByPlaceholder("Enter your password",{exact:true}).fill("DesignPassword9!");
  await page.getByRole("button",{name:"Show password"}).click();await expect(page.getByPlaceholder("Enter your password",{exact:true})).toHaveAttribute("type","text");await page.getByRole("button",{name:"Hide password"}).click();
  await page.getByLabel("Confirm password").fill("DifferentPassword9!");await page.getByRole("button",{name:"Create account",exact:true}).click();await expect(page.locator(".p-error")).toContainText("passwords do not match");await page.screenshot({animations:"disabled",path:shots+"/auth-signup.png",fullPage:true});
  await workspace(page);
  for(const[from,to]of [["/history","/dashboard/runs"],["/audit","/dashboard/audit"],["/verify","/dashboard/proofs"],["/leaderboard","/dashboard/trust-scores"],["/trust-scores","/dashboard/trust-scores"]]){await page.goto(from);await expect(page).toHaveURL(to);}
});

test("secondary controls: account appearance, scoped keys, searches, refreshes, and score export",async({page})=>{
  await workspace(page);
  await page.goto("/dashboard/settings");
  await page.getByRole("button",{name:"My account",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Alex Morgan",exact:true})).toBeVisible();
  const appearance=page.getByRole("switch",{name:"Dark mode",exact:true});
  await appearance.click();await expect(page.locator(".tc-app")).toHaveAttribute("data-app-theme","light");
  await appearance.click();await expect(page.locator(".tc-app")).toHaveAttribute("data-app-theme","dark");
  await page.getByRole("link",{name:"Reset password",exact:true}).click();await expect(page).toHaveURL("/auth/forgot-password");
  await page.goto("/dashboard/keys");await page.getByRole("button",{name:"Create API key",exact:true}).click();
  for(const box of await page.getByRole("dialog").getByRole("checkbox").all())await box.uncheck();
  await expect(page.getByRole("button",{name:"Create key",exact:true})).toBeDisabled();
  await page.getByRole("button",{name:"Cancel",exact:true}).click();await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.goto("/dashboard/agents");await page.getByLabel("Search agents or models…").fill("absent-agent");await expect(page.getByRole("heading",{name:"No matching agents",exact:true})).toBeVisible();
  await page.getByLabel("Search agents or models…").fill("research-model");await expect(page.getByRole("heading",{name:"research-assistant",exact:true})).toBeVisible();
  await page.goto("/dashboard/team");await page.getByLabel("Search names or email addresses…").fill("absent-member");await expect(page.getByRole("heading",{name:"No matching teammates",exact:true})).toBeVisible();
  await page.goto("/dashboard/audit");await page.getByRole("button",{name:"Refresh audit trail"}).click();await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.goto("/dashboard/runs");await page.getByRole("button",{name:"Refresh",exact:true}).click();await expect(page.locator("tbody tr")).toHaveCount(10);
  await page.getByRole("button",{name:"New run",exact:true}).click();await expect(page.getByRole("button",{name:"Start run",exact:true})).toBeDisabled();await page.getByRole("button",{name:"Cancel",exact:true}).click();
  await page.goto("/dashboard/trust-scores");const downloaded=page.waitForEvent("download");await page.getByRole("button",{name:"Export JSON",exact:true}).click();expect((await downloaded).suggestedFilename()).toBe("trustchain-scores.json");
});
