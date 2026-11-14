/** Print the verified artifact report; local screenshot assets stay embedded. */
import { chromium } from '../frontend/node_modules/@playwright/test/index.mjs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.goto(pathToFileURL(resolve('artifacts/redesign/final-report.html')).href);
  await page.waitForLoadState('networkidle');
  await page.pdf({path:resolve('artifacts/redesign/TrustChain-Redesign-Final-Report.pdf'),printBackground:true,preferCSSPageSize:true,tagged:true});
  console.log('Created TrustChain-Redesign-Final-Report.pdf');
} finally { await browser.close(); }
