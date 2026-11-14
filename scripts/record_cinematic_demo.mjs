/** Record a silent, product-first walkthrough with real UI actions and short callouts. */
import { chromium, expect } from '../frontend/node_modules/@playwright/test/index.mjs';
import { TrustChain, TrustChainClient } from '../sdk/typescript/dist/index.js';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const BASE = process.env.DEMO_BASE_URL || 'http://localhost:3000';
const API = process.env.DEMO_API_URL || 'http://localhost:8000';
const OUT = resolve(process.env.DEMO_OUTPUT || '/tmp/trustchain-cinematic-demo');
const EMAIL_LOG = process.env.TRUSTCHAIN_DEMO_EMAIL_LOG || '/tmp/trustchain-design/api.log';
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const setup = await browser.newContext({ viewport: { width: 1600, height: 900 } });
const email = 'product_tour_' + Date.now() + '@example.com';
const password = 'TrustChainProductTour9!';
await expect.poll(async () => { try { return (await setup.request.get(API + '/ready', {timeout:3000})).ok(); } catch { return false; } }, {timeout:60000}).toBeTruthy();
const signup = await setup.request.post(API + '/auth/signup', { data: { name: 'Alex Morgan', email, password } });
if (!signup.ok()) throw new Error('Demo account setup failed: ' + signup.status());
const line = readFileSync(EMAIL_LOG, 'utf8').split('\n').find(s => s.includes(email) && s.includes('Verification token:'));
const token = line?.match(/Verification token: ([A-Za-z0-9_-]+)/)?.[1];
if (!token) throw new Error('Console verification token was not captured');
const verified = await setup.request.post(API + '/auth/verify-email/' + token);
if (!verified.ok()) throw new Error('Demo account verification failed');
const principal = await (await setup.request.get(API + '/me')).json();
const csrf = (await setup.cookies()).find(c => c.name === 'tc_csrf')?.value;
const renamed = await setup.request.patch(API + '/orgs/' + principal.active.orgId, {
  data: { name: 'TrustChain Studio' }, headers: { 'X-CSRF-Token': csrf },
});
if (!renamed.ok()) throw new Error('Demo workspace setup failed');
const prepared = await setup.newPage();
await prepared.goto(BASE + '/dashboard');
await expect(prepared.getByRole('heading', { name: 'Welcome back, Alex.' })).toBeVisible();
const dark = prepared.getByRole('button', { name: 'Switch to dark mode' });
if (await dark.count()) await dark.click();
await expect(prepared.locator('.tc-app')).toHaveAttribute('data-app-theme', 'dark');
const state = await setup.storageState();
await setup.close();

const context = await browser.newContext({
  viewport: { width: 1600, height: 900 }, storageState: state,
  recordVideo: { dir: OUT, size: { width: 1600, height: 900 } },
});
await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: BASE });
await context.addInitScript(() => {
  function install() {
    if (document.getElementById('tour-chrome')) return;
    const style = document.createElement('style');
    style.textContent = `
      #tour-chrome{position:fixed;inset:0;pointer-events:none;z-index:2147483647;font-family:Geist,Arial,system-ui,sans-serif}
      #tour-pointer{position:absolute;left:0;top:0;width:22px;height:28px;filter:drop-shadow(0 2px 3px #000b);transform:translate(550px,65px)}
      .tour-ripple{position:absolute;width:40px;height:40px;border:2px solid #b3ef69;border-radius:50%;box-sizing:border-box;animation:tourRipple .65s ease-out forwards}
      @keyframes tourRipple{from{transform:translate(-50%,-50%) scale(.45);opacity:.9}to{transform:translate(-50%,-50%) scale(1.7);opacity:0}}
      .tour-note{position:absolute;right:30px;bottom:36px;width:370px;box-sizing:border-box;padding:23px 25px 24px;border:1px solid #b3ef6945;border-radius:8px;background:linear-gradient(135deg,#30261bf7,#1b150ff7);backdrop-filter:blur(22px);box-shadow:0 20px 70px #0006;color:#f6f0e4;opacity:0;transform:translateY(22px) scale(.97);transition:opacity .45s ease,transform .6s cubic-bezier(.2,.8,.3,1);overflow:hidden}
      .tour-note::before{content:'';position:absolute;left:25px;right:25px;top:0;height:2px;background:linear-gradient(90deg,#b3ef69,transparent)}
      .tour-note::after{content:'';position:absolute;left:25px;right:25px;bottom:0;height:2px;background:#b3ef6980;transform-origin:left;animation:tourLife var(--note-life,7s) linear forwards}
      @keyframes tourLife{to{transform:scaleX(0)}}
      .tour-note.visible{opacity:1;transform:translateY(0) scale(1)}
      .tour-note.left{right:auto;left:280px}
      .tour-note.top{bottom:auto;top:103px}
      .tour-kicker{font:9px/1.3 ui-monospace,monospace;letter-spacing:2px;color:#b3ef69;font-weight:500;margin-bottom:14px;display:flex;align-items:center;gap:10px}
      .tour-kicker::before{content:'';width:5px;height:5px;border-radius:50%;background:#b3ef69;box-shadow:0 0 14px #b3ef69}
      .tour-title{font-size:21px;font-weight:500;line-height:1.25;letter-spacing:-.6px;margin-bottom:10px}
      .tour-body{font-size:13px;line-height:1.7;color:#cbbda8}
      .tour-note .tour-title,.tour-note .tour-body{opacity:0;transform:translateY(7px);transition:opacity .45s .12s,transform .5s .12s}.tour-note .tour-body{transition-delay:.2s}.tour-note.visible .tour-title,.tour-note.visible .tour-body{opacity:1;transform:none}
      .tour-code{font:10px/1.85 ui-monospace,monospace;margin:14px 0 0;padding-top:12px;border-top:1px solid #f2e4c720;white-space:pre-wrap;color:#bfe38d}
      #tour-progress{position:absolute;bottom:0;left:0;height:3px;background:linear-gradient(90deg,#6f9142,#c7ff83);box-shadow:0 0 18px #b3ef6960;opacity:.95;width:0;transition:width 1s linear}
      #tour-chapter{position:absolute;top:17px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:15px;padding:9px 18px;border:1px solid #b3ef6928;background:#21190ff0;color:#d5c7ae;border-radius:30px;box-shadow:0 7px 30px #0003;font:8px/1.4 ui-monospace,monospace;letter-spacing:1.4px;transition:opacity .3s}
      #tour-chapter b{font-weight:400;color:#b3ef69}#tour-chapter span{color:#f3ebdc;letter-spacing:.2px;font:10px Arial,sans-serif}
      html[data-tour-mask] [role=dialog] .p-code{filter:blur(7px);user-select:none}
    `;
    document.head.append(style);
    const root = document.createElement('div'); root.id = 'tour-chrome'; root.setAttribute('aria-hidden', 'true');
    root.innerHTML = '<div id="tour-pointer"><svg width="22" height="28" viewBox="0 0 22 28"><path d="M2 1 L3 23 L8.3 17.5 L12.5 26 L16.5 24 L12.5 16 L20 15 Z" fill="#f5f1e6" stroke="#302417" stroke-width="1.5"/></svg></div><div id="tour-progress"></div><div id="tour-chapter"><b>TRUSTCHAIN / PRODUCT FILM</b><span>Evidence in focus</span></div>';
    document.body.append(root);
    const saved = JSON.parse(sessionStorage.getItem('tc_tour_chapter') || 'null');
    if(saved){root.querySelector('#tour-chapter b').textContent='TRUSTCHAIN / '+String(saved.chapter).padStart(2,'0');root.querySelector('#tour-chapter span').textContent=saved.title;}
    setInterval(()=>{const start=Number(sessionStorage.getItem('tc_tour_epoch'));if(start)root.querySelector('#tour-progress').style.width=Math.min(100,(Date.now()-start)/3060)+'%';},500);
    document.addEventListener('mousemove', e => {
      root.querySelector('#tour-pointer').style.transform = 'translate(' + e.clientX + 'px,' + e.clientY + 'px)';
    }, true);
    document.addEventListener('mousedown', e => {
      const ring = document.createElement('div'); ring.className = 'tour-ripple'; ring.style.left = e.clientX + 'px'; ring.style.top = e.clientY + 'px'; root.append(ring); setTimeout(() => ring.remove(), 700);
    }, true);
    window.tourNote = (title, body, step, position, code, ms) => {
      root.querySelectorAll('.tour-note').forEach(x => x.remove());
      const box = document.createElement('div'); box.className = 'tour-note ' + (position || ''); box.style.setProperty('--note-life', (ms || 7000)+'ms');
      const kicker = document.createElement('div'); kicker.className = 'tour-kicker'; kicker.textContent = 'FIELD NOTES  /  ' + String(step).padStart(2, '0');
      const heading = document.createElement('div'); heading.className = 'tour-title'; heading.textContent = title;
      const copy = document.createElement('div'); copy.className = 'tour-body'; copy.textContent = body;
      box.append(kicker, heading, copy);
      if (code) { const pre = document.createElement('pre'); pre.className = 'tour-code'; pre.textContent = code; box.append(pre); }
      root.append(box); requestAnimationFrame(() => box.classList.add('visible'));
      setTimeout(() => { box.classList.remove('visible'); setTimeout(() => box.remove(), 400); }, ms || 7000);
    };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true }); else install();
});
const beforePage = Date.now();
const page = await context.newPage();
await page.goto(BASE + '/');
await expect(page.getByRole('heading', { name: 'Intelligence, with a proof.' })).toBeVisible();
await page.waitForTimeout(1200);
const epoch = Date.now(), trimStartSeconds = (epoch - beforePage) / 1000;
const chapters = [], events = [];
let planned = 0, chapter = 0, mouse = { x: 550, y: 65 };
let runId, entries, key, sdk, receipt, proofStep;
let complete = false;
const pause = ms => page.waitForTimeout(ms);
async function move(x, y, duration = 500) {
  const from = { ...mouse }, frames = Math.max(12, Math.round(duration / 16));
  for (let i = 1; i <= frames; i++) { const t = i / frames, a = t * t * (3 - 2 * t); await page.mouse.move(from.x + (x - from.x) * a, from.y + (y - from.y) * a); await pause(duration / frames); }
  mouse = { x, y };
}
async function point(locator, duration = 450) {
  await locator.scrollIntoViewIfNeeded(); const b = await locator.boundingBox();
  if (!b) throw new Error('Cannot locate visible action');
  await move(b.x + b.width * .48, b.y + b.height * .52, duration);
}
async function click(locator) { await point(locator); await pause(280); await page.mouse.click(mouse.x, mouse.y); await pause(420); }
async function type(locator, text, delay = 29) { await click(locator); await locator.fill(''); await locator.pressSequentially(text, { delay }); await pause(300); }
async function select(locator, value) { await point(locator); await pause(300); await locator.selectOption(value); await pause(550); }
async function nav(name) { await click(page.locator('.d-sidebar').getByRole('link', { name, exact: true })); await expect(page.locator('.p-heading h1')).toBeVisible(); await pause(400); }
async function note(title, body, options = {}) { await page.evaluate(({title,body,chapter,options}) => window.tourNote(title, body, chapter, options.position, options.code, options.ms), { title, body, chapter, options }); }
async function scrollTo(locator, offset = 120) {
  await locator.evaluate((el,offset) => window.scrollTo({ top: window.scrollY + el.getBoundingClientRect().top - offset, behavior: 'smooth' }), offset);
  await pause(1000);
}
async function top() { await page.evaluate(() => window.scrollTo({top:0,behavior:'smooth'})); await pause(800); }
async function shot(name) { await page.screenshot({path:OUT+'/'+name+'.png'}); }
async function scene(title, seconds, action) {
  chapter++; planned += seconds;
  const start = (Date.now() - epoch) / 1000;
  console.log('START '+String(chapter).padStart(2,'0')+' '+title);
  await page.evaluate(({chapter,title,planned,epoch})=>{sessionStorage.setItem('tc_tour_chapter',JSON.stringify({chapter,title}));sessionStorage.setItem('tc_tour_epoch',String(epoch));const hud=document.querySelector('#tour-chapter');if(hud)hud.innerHTML='<b>TRUSTCHAIN / '+String(chapter).padStart(2,'0')+'</b><span></span>';if(hud)hud.querySelector('span').textContent=title;const progress=document.querySelector('#tour-progress');if(progress)progress.style.width=Math.min(100,(Date.now()-epoch)/3060)+'%'},{chapter,title,planned,epoch});
  await action();
  await page.evaluate(() => document.querySelectorAll('.tour-note').forEach(e => e.classList.remove('visible')));
  await pause(Math.max(80, epoch + planned * 1000 - Date.now()));
  const end = (Date.now() - epoch) / 1000;
  chapters.push({title,start,end});
  writeFileSync(OUT+'/progress.json',JSON.stringify({chapter,title,elapsed:end},null,2));
  console.log('DONE '+String(chapter).padStart(2,'0')+' '+end.toFixed(1)+'s');
}
// Presentation overlays only: cursor, click indicators, and temporary callouts.
// Application results come exclusively from real UI/API/SDK operations.
try {
  await scene('Intelligence, with a proof', 12, async()=>{
    await move(1030,410,900);await note('Intelligence, with a proof.','A new perspective on your AI. Espresso, warm white, and living green.');await pause(5500);await shot('00-landing');
  });
  await scene('Explore the four-agent pipeline', 12, async()=>{
    await click(page.getByRole('link',{name:'Explore the platform',exact:true}));await pause(900);
    await note('Every action has a story','Researcher, Validator, Scorer, Reporter. One connected workflow.');
    await click(page.getByRole('tab',{name:/Validator/}));await pause(1500);await click(page.getByRole('tab',{name:/Reporter/}));await pause(1700);await shot('00-pipeline');
  });
  await scene('Change a word. Leave a trace.', 10, async()=>{
    await scrollTo(page.locator('#security'),50);await click(page.getByRole('button',{name:'Alter the record'}));
    await expect(page.getByRole('status')).toContainText('The record changed');
    await note('The fingerprint changes, too','The interactive landing example computes real SHA-256 hashes in your browser.',{position:'top'});
    await pause(1800);await click(page.getByRole('button',{name:'Restore original'}));await pause(1600);events.push({type:'landing_pipeline_and_hash_demo'});
  });
  await scene('Your project workspace', 8, async () => {
    await page.goto(BASE+'/dashboard');await expect(page.getByRole('heading',{name:'Welcome back, Alex.'})).toBeVisible();await pause(700);
    await move(1130,380,650);
    await note('Start in your workspace','Runs, agent identities and evidence belong to the active project.');
    await pause(5200); await shot('01-overview');
  });
  await scene('Create a project', 14, async () => {
    await nav('Settings');
    await note('Choose where evidence belongs','Create a separate project for each environment or team.');
    await click(page.getByRole('button',{name:'New project',exact:true}));
    await type(page.getByRole('dialog').getByLabel('Name',{exact:true}),'Research Lab',65);
    await select(page.getByRole('dialog').getByLabel('Environment',{exact:false}),'test');
    await click(page.getByRole('dialog').getByRole('button',{name:'Create',exact:true}));
    const row=page.locator('.p-row').filter({has:page.getByRole('heading',{name:'Research Lab',exact:true})});
    await click(row.getByRole('button',{name:'Open project',exact:true}));
    await expect(page.getByLabel('Active project').locator('option:checked')).toHaveText('Research Lab');
    events.push({type:'project_created_and_selected',project:'Research Lab'});
  });
  await scene('Launch a real research run',20,async()=>{
    await nav('Agent runs');
    await note('Ask a question','Choose New run, describe the task, then start the workflow.');
    await click(page.locator('.p-heading').getByRole('button',{name:'New run',exact:true}));
    await type(page.getByLabel('What would you like to investigate?',{exact:false}),'Research two benefits and one limitation of Merkle trees for AI audit logs. Include source links and keep the report under 180 words.',25);
    await pause(1600);await click(page.getByRole('button',{name:'Start run',exact:true}));
    await expect(page).toHaveURL(/dashboard\/runs\//,{timeout:30000});runId=new URL(page.url()).pathname.split('/').pop();
    await note('Four agents. One trace.','Researcher → Validator → Scorer → Reporter. Watch their recorded actions.');
    await pause(3500);await shot('03-run-started');events.push({type:'pipeline_started',runId});
  });
  await scene('Follow live agent activity',26,async()=>{
    await move(790,410,650);await pause(2500);
    await note('Recorded first. Anchored next.','Step records persist immediately; chain confirmation happens asynchronously.');
    await page.evaluate(()=>window.scrollBy({top:360,behavior:'smooth'}));await pause(3200);await top();
    await expect(page.locator('.p-report')).toBeVisible({timeout:120000});
    const result=await(await page.request.get(API+'/runs/'+runId)).json();
    if(!result.report || !Number.isFinite(result.score))throw new Error('Pipeline did not produce a real report and score');
    events.push({type:'pipeline_completed',runId,score:result.score,reportCharacters:result.report.length});
    await note('The result is saved','Reopen this run later to inspect its report and persisted activity.');await pause(3000);await shot('04-run-complete');
  });
  await scene('Read the final report',16,async()=>{
    await scrollTo(page.locator('.p-report'),105);await move(1130,520,600);
    await note('Read the final report','Research, validation and the final score stay linked to this run.',{position:'top'});
    await pause(5500);await page.mouse.wheel(0,230);await pause(1400);await shot('05-report');await top();
  });
  await scene('Compare agent performance',12,async()=>{
    await nav('Trust scores');await expect(page.locator('tbody tr')).toHaveCount(4);
    await note('Compare observed performance','Review each agent’s average, best score and number of observed runs.');
    await point(page.locator('tbody tr').first());await pause(2200);await point(page.locator('tbody tr').last());await pause(2600);await shot('06-scores');
  });
  await scene('Search and export the audit trail',16,async()=>{
    await nav('Audit trail');await note('Find the action behind an answer','Filter by anchor state or search for an agent, action or run.');
    await select(page.getByLabel('Anchor status',{exact:true}),'confirmed');
    await type(page.getByLabel('Search agents, actions, or runs…'),'researcher',65);await pause(2500);
    await type(page.getByLabel('Search agents, actions, or runs…'),'',20);
    const download=page.waitForEvent('download');await click(page.getByRole('button',{name:'Export JSON',exact:true}));await(await download).saveAs(OUT+'/audit-export.json');
    await note('Keep a copy of the evidence','Export JSON contains the actual filtered audit entries.');await pause(2500);await shot('07-audit');
  });
  await scene('Inspect confirmed transactions',12,async()=>{
    await nav('Anchors');
    await expect.poll(async()=>{entries=(await(await page.request.get(API+'/audit-log?run_id='+runId)).json()).entries;return entries.length>=4&&entries.every(e=>e.anchorStatus==='confirmed')},{timeout:60000}).toBeTruthy();
    await expect(page.locator('.p-stat').filter({hasText:'Pipeline chain'})).toContainText('#31337');
    await note('Check the chain receipt','Local Anvil · chain 31337. Confirmed steps are grouped by transaction.');
    await point(page.locator('tbody tr').first());await pause(4000);await shot('08-anchors');events.push({type:'all_steps_confirmed',count:entries.length});
  });
  await scene('Retrieve a confirmed Merkle proof',20,async()=>{
    await nav('Audit trail');
    await click(page.locator('tbody').getByRole('link',{name:/Inspect/}).first());
    proofStep=Number(new URL(page.url()).searchParams.get('step'));
    await note('Open a step’s proof','Retrieve the leaf, Merkle root, sibling path and chain reference.');
    await click(page.getByRole('button',{name:'Retrieve proof',exact:true}));
    await expect(page.getByRole('heading',{name:'Proof for step #'+proofStep})).toBeVisible();
    await scrollTo(page.getByRole('heading',{name:'Proof for step #'+proofStep}),175);await pause(3000);
    await point(page.getByRole('button',{name:'Copy to clipboard'}).first());await pause(500);
    const download=page.waitForEvent('download');await click(page.getByRole('button',{name:'Export JSON',exact:true}));await(await download).saveAs(OUT+'/proof-export.json');
    await note('A proof you can inspect','The exported proof preserves the hashes and confirmed transaction metadata.');await pause(2200);await shot('09-proof');await top();
  });
  await scene('Verify the whole run',14,async()=>{
    await click(page.getByRole('button',{name:'Run integrity',exact:true}));
    await note('Verify the whole workflow','Paste the saved run ID and run the integrity checks.');
    await type(page.getByLabel('Run ID',{exact:true}),runId,12);
    await click(page.getByRole('button',{name:'Run verification',exact:true}));
    await expect(page.getByRole('status')).toContainText('recorded steps passed the integrity check');
    await pause(2200);await shot('10-run-verification');events.push({type:'run_verification_passed',runId});
  });
  await scene('Create a scoped SDK key',16,async()=>{
    await nav('API keys');await note('Connect your own agent','Issue a project key with only the scopes your integration needs.');
    await click(page.getByRole('button',{name:'Create API key',exact:true}));
    for(const scope of ['agents:register','logs:write'])await click(page.getByRole('dialog').getByRole('checkbox',{name:scope,exact:true}));
    await pause(1500);
    // Hide the disposable credential in the recording without altering its value.
    await page.evaluate(()=>document.documentElement.setAttribute('data-tour-mask',''));
    const response=page.waitForResponse(r=>r.url()===API+'/api-keys'&&r.request().method()==='POST');
    await click(page.getByRole('button',{name:'Create key',exact:true}));key=(await(await response).json()).raw_key;
    await note('Copy once. Keep it private.','The key is deliberately hidden in this recording.');await pause(1600);
    await click(page.getByRole('button',{name:'I’ve saved my key',exact:true}));
    await page.evaluate(()=>document.documentElement.removeAttribute('data-tour-mask'));
    await shot('11-key-created');
  });
  await scene('Send activity through the real SDK',20,async()=>{
    await nav('Agent registry');
    await note('Instrument your existing app','Register an identity, then log an action with the TypeScript SDK.',{code:'await tc.registerAgent({ agentId: "support-assistant", … });\nawait tc.logAndWait({ action: "answer_customer", … });',ms:8500});
    sdk=new TrustChain(key,{baseUrl:API,onError:'raise'});
    await sdk.registerAgent({agentId:'support-assistant',model:'support-model',version:'1.0.0',systemPrompt:'Answer support questions carefully.'});
    receipt=await sdk.logAndWait({agentId:'support-assistant',action:'answer_customer',input:'How can I verify an AI audit log?',output:'Compare the recorded step with its confirmed Merkle proof.'});
    await expect.poll(async()=>{const b=await(await page.request.get(API+'/agents')).json();return b.agents.some(a=>a.agentId==='support-assistant')},{timeout:30000}).toBeTruthy();
    await pause(3500);await page.reload();await expect(page.getByRole('heading',{name:'support-assistant',exact:true})).toBeVisible();
    await move(695,380,650);await note('The SDK agent appears here','Its identity is indexed from the real contract event.');await pause(2500);await shot('12-sdk-agent');
    events.push({type:'sdk_registered_and_logged',agentId:'support-assistant',stepId:receipt.stepId});
  });
  await scene('Check the registered fingerprint',14,async()=>{
    await click(page.getByRole('button',{name:'Inspect identity',exact:true}));
    await note('Check the configuration fingerprint','Compare the current agent hash with its registered on-chain identity.');
    await click(page.getByRole('button',{name:'Verify fingerprint',exact:true}));
    await expect(page.getByRole('dialog').getByRole('status')).toContainText('matches an active on-chain identity');await pause(1800);
    await type(page.getByLabel('Fingerprint to verify',{exact:false}),'0x'+'f'.repeat(64),5);
    await click(page.getByRole('button',{name:'Verify fingerprint',exact:true}));
    await expect(page.getByRole('dialog').getByRole('status')).toContainText('Verification did not pass');
    await note('A different hash does not pass','A fingerprint mismatch is visible rather than silently accepted.');await pause(1500);await shot('13-fingerprint-mismatch');
    await click(page.getByRole('button',{name:'Close dialog',exact:true}));
    const client=new TrustChainClient(key,{baseUrl:API});
    await client.logStep({runId:'tour_drift_'+Date.now(),agentId:'support-assistant',action:'configuration_drift',input:'q',output:'a',agentCodeHash:'0x'+'f'.repeat(64)});
    events.push({type:'intentional_sdk_identity_drift'});
  });
  await scene('Check the original content',8,async()=>{
    await nav('Verification');
    await click(page.getByRole('button',{name:'Content verification',exact:true}));
    await type(page.getByLabel('Step ID',{exact:true}),String(receipt.stepId),25);
    await type(page.getByLabel('Original content',{exact:false}),'Compare the recorded step with its confirmed Merkle proof.',14);
    await click(page.getByRole('button',{name:'Run verification',exact:true}));
    await expect(page.getByRole('status')).toContainText('matches the recorded content hash');
    await note('Match the content to its hash','The exact SDK output matches the recorded evidence.');await pause(1500);events.push({type:'content_verification_passed',stepId:receipt.stepId});
  });
  await scene('Investigate and resolve identity drift',20,async()=>{
    await nav('Alerts');await note('Investigate a real finding','A mismatched fingerprint on SDK activity creates an alert.');
    const finding=page.getByRole('button',{name:/Agent 'support-assistant' logged a step/});await click(finding);
    await click(page.getByText('Inspect evidence',{exact:true}));await pause(1400);
    await type(page.getByLabel('Investigation note'),'Intentional SDK mismatch; approved identity is unchanged.',17);
    await click(page.getByRole('button',{name:'Acknowledge',exact:true}));
    await click(page.getByRole('button',{name:'Acknowledged',exact:true}));await click(finding);
    await type(page.getByLabel('Investigation note'),'Reviewed demo: fingerprint mismatch was intentional.',15);
    await click(page.getByRole('button',{name:'Resolve alert',exact:true}));
    await click(page.getByRole('button',{name:'Resolved',exact:true}));
    await note('Close the investigation loop','The finding remains available with its resolved status and notes.');await pause(1800);await shot('15-alert-resolved');events.push({type:'alert_acknowledged_and_resolved'});
  });
  await scene('Invite teammates and set preferences',16,async()=>{
    await nav('Team members');await note('Invite with least privilege','Choose a role before granting workspace access. Console email is used here.');
    await click(page.getByRole('button',{name:'Invite teammate',exact:true}));
    await type(page.getByRole('dialog').getByLabel('Email address',{exact:true}),'reviewer@example.com',25);
    await select(page.getByRole('dialog').getByLabel('Role',{exact:false}),'viewer');
    await click(page.getByRole('button',{name:'Send invitation',exact:true}));
    await expect(page.getByRole('dialog')).toHaveCount(0);await pause(1100);
    await nav('Settings');await click(page.getByRole('button',{name:'Notifications',exact:true}));
    const toggle=page.getByRole('switch',{name:'Informational alerts',exact:true});await click(toggle);await expect(toggle).toHaveAttribute('aria-checked','true');
    await note('Choose what reaches your inbox','Notification preferences persist in the workspace.');await pause(1300);await shot('16-preferences');events.push({type:'viewer_invitation_and_preferences_saved'});
  });
  await scene('Revoke the integration key',12,async()=>{
    await nav('API keys');await note('End access when the work ends','Revoke an integration key to stop further API use.');
    await click(page.getByRole('button',{name:'Revoke',exact:true}));await pause(1500);
    await click(page.getByRole('button',{name:'Revoke key',exact:true}));
    await expect(page.getByText('revoked',{exact:true})).toBeVisible();
    const denied=await context.request.get(API+'/agents',{headers:{Authorization:'Bearer '+key}});
    if(denied.status()!==401)throw new Error('Revoked key was not rejected');
    await note('Access is revoked','The same SDK credential now receives HTTP 401.');await pause(2200);await shot('17-revoked');events.push({type:'key_revoked',status:denied.status()});
  });
  await scene('Return to the project overview',8,async()=>{
    await nav('Overview');await move(1080,430,600);
    await note('Task → actions → evidence → proof','A complete workflow, an SDK agent, and a resolved integrity finding.');await pause(4500);await shot('18-overview');
  });
  complete=true;
} catch(error) {
  await page.screenshot({path:OUT+'/failure.png',fullPage:true}).catch(()=>{});
  throw error;
} finally {
  if(sdk)await sdk.close().catch(()=>{});
  await page.evaluate(()=>document.getElementById('tour-chrome')?.remove()).catch(()=>{});
  const duration=(Date.now()-epoch)/1000;
  const video=page.video();await context.close();await video.saveAs(OUT+'/source.webm');await browser.close();
  writeFileSync(OUT+'/manifest.json',JSON.stringify({complete,duration,trimStartSeconds,viewport:{width:1600,height:900},audio:false,theme:'espresso, warm white, and chlorophyll green',presentation:'real redesigned UI with animated evidence sculpture, chapter HUD, eased cursor, click ripples, field-note cards, and reading progress',accountSetup:'verified disposable account prepared before capture; console email',runId,confirmedSteps:entries?.length,chapters,events},null,2));
  console.log('Recording saved; complete='+complete+' duration='+duration.toFixed(2));
}
