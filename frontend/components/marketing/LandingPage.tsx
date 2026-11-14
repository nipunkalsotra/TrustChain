"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowRight, ArrowUpRight, Blocks, Check, CheckCheck, Code2, Fingerprint, GitBranch, LockKeyhole, Menu, ShieldCheck, X } from "lucide-react";
import { MotionControl } from "@/components/ui/MotionControl";
import { ProofSculpture } from "@/components/ui/ProofSculpture";
import "./marketing.css";

const stages = [
  { name: "Researcher", verb: "Find the signal.", copy: "Gather source material and context. Record the inputs behind the answer, before it moves downstream.", action: "collect_sources", icon: GitBranch },
  { name: "Validator", verb: "Question the output.", copy: "Check the research against the available evidence. Keep the validation activity attached to the same run.", action: "validate_research", icon: ShieldCheck },
  { name: "Scorer", verb: "Measure the outcome.", copy: "Turn observed agent performance into a trust score. Compare results across runs without losing the underlying trace.", action: "score_results", icon: CheckCheck },
  { name: "Reporter", verb: "Make it accountable.", copy: "Bring the final answer and its evidence together. Reopen the report, inspect its steps, and retrieve the proof.", action: "publish_report", icon: Blocks },
];

function EvidenceLab() {
  const [changed, setChanged] = useState(false);
  const [hashes, setHashes] = useState({ original: "", current: "" });
  useEffect(() => {
    let active = true;
    async function hash(text: string) {
      const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
      return Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, "0")).join("");
    }
    void Promise.all([hash("Agent output: sources verified."), hash(changed ? "Agent output: sources changed." : "Agent output: sources verified.")]).then(([original, current]) => { if (active) setHashes({ original, current }); });
    return () => { active = false; };
  }, [changed]);
  return <div className={`m-lab ${changed ? "altered" : ""}`}>
    <div className="m-window-bar"><span><i /> INTEGRITY LAB</span><span>INTERACTIVE EXAMPLE</span></div>
    <div className="m-lab-content"><div className="m-code-label">01 / RECORDED OUTPUT</div><div className="m-lab-text">Sources <span>{changed ? "changed" : "verified"}</span>.</div>
      <div className="m-hash-row"><span>Original SHA-256</span><code>{hashes.original ? `${hashes.original.slice(0, 18)}…${hashes.original.slice(-8)}` : "Computing…"}</code></div>
      <div className="m-hash-row"><span>Current SHA-256</span><code>{hashes.current ? `${hashes.current.slice(0, 18)}…${hashes.current.slice(-8)}` : "Computing…"}</code></div>
      <div className="m-lab-result" role="status"><ShieldCheck size={19} /><div><strong>{changed ? "The record changed. The hash did too." : "Same content. Same fingerprint."}</strong><p>{changed ? "A changed record cannot match its original fingerprint." : "Edit the example to see tampering become visible."}</p></div></div>
      <button className="m-button m-button-outline" onClick={() => setChanged(v => !v)}>{changed ? "Restore original" : "Alter the record"}<ArrowRight size={15} /></button>
    </div>
  </div>;
}

export function LandingPage() {
  const root = useRef<HTMLDivElement>(null);
  const [menu, setMenu] = useState(false);
  const [stage, setStage] = useState(0);
  useEffect(() => {
    const observer = new IntersectionObserver(entries => entries.forEach(entry => { if (entry.isIntersecting) { entry.target.classList.add("is-visible"); observer.unobserve(entry.target); } }), { threshold: 0.12 });
    root.current?.querySelectorAll("[data-reveal]").forEach(el => observer.observe(el));
    return () => observer.disconnect();
  }, []);
  const StageIcon = stages[stage].icon;
  return <div className="mkt-page" ref={root}>
    <a href="#main" className="m-skip">Skip to content</a>
    <nav className="m-nav" aria-label="Main navigation">
      <Link href="/" className="m-brand"><span className="m-brand-mark"><ShieldCheck size={24} strokeWidth={1.6} /></span>trustchain<span className="m-brand-period">.</span></Link>
      <div className={`m-nav-links ${menu ? "open" : ""}`}>
        <a href="#product" onClick={() => setMenu(false)}>The platform</a><a href="#security" onClick={() => setMenu(false)}>Why trust us</a><a href="#developers" onClick={() => setMenu(false)}>Developers</a><Link href="/pricing">Pricing<ArrowUpRight size={12} /></Link>
      </div>
      <div className="m-nav-actions"><Link className="m-login" href="/auth">Log In</Link><Link className="m-button m-button-green m-nav-cta" href="/auth?mode=signup">Get Started<ArrowUpRight size={14} /></Link><button className="m-menu" aria-label={menu ? "Close menu" : "Open menu"} aria-expanded={menu} onClick={() => setMenu(v => !v)}>{menu ? <X /> : <Menu />}</button></div>
    </nav>
    <main id="main">
      <header className="m-hero">
        <div className="m-hero-grid" aria-hidden="true" />
        <div className="m-hero-copy"><div className="m-eyebrow"><i /> THE EVIDENCE LAYER FOR AI</div>
          <h1>Intelligence,<br />with a <em>proof.</em></h1>
          <p>Your agents move fast. Know what they did.<br className="m-desktop-break" /> Trace every action, anchor the evidence, and turn<br className="m-desktop-break" /> “trust me” into something you can verify.</p>
          <div className="m-hero-actions"><Link className="m-button m-button-green" href="/auth?mode=signup">Build on trust<ArrowUpRight size={18} /></Link><a className="m-button m-button-quiet" href="#product"><span className="m-play"><ArrowDown size={14} /></span>Explore the platform</a></div>
          <div className="m-hero-foot"><span><Check size={13} /> Traceable actions</span><span><Check size={13} /> Independent proofs</span></div>
        </div>
        <div className="m-hero-art"><ProofSculpture /><div className="m-art-coordinate">EVIDENCE OBJECT / 001<br />CRYPTOGRAPHIC PROVENANCE</div><div className="m-art-status"><span className="m-status-dot" /><span>Built to be verified</span><ShieldCheck size={16} /></div></div>
        <div className="m-hero-bottom"><span>LESS BLIND TRUST. MORE VISIBLE TRUTH.</span><a href="#product">SCROLL TO DISCOVER<ArrowDown size={13} /></a><MotionControl /></div>
      </header>
      <div className="m-principles"><span>One continuous chain of evidence.</span><div><Fingerprint size={18} />Identify</div><ArrowRight /><div><GitBranch size={18} />Observe</div><ArrowRight /><div><Blocks size={18} />Anchor</div><ArrowRight /><div><ShieldCheck size={18} />Verify</div></div>
      <section className="m-section m-product" id="product">
        <div className="m-section-head" data-reveal><div><div className="m-eyebrow">01 / THE PLATFORM</div><h2>Every action has a story.<br /><em>Keep the whole chain.</em></h2></div><p>From the first query to the final report, see how a four-agent workflow becomes a record you can inspect.</p></div>
        <div className="m-pipeline" data-reveal>
          <div className="m-stage-tabs" role="tablist" aria-label="Explore pipeline stages">{stages.map((s, i) => <button key={s.name} id={`stage-${i}`} role="tab" aria-selected={stage === i} aria-controls="pipeline-panel" tabIndex={stage === i ? 0 : -1} onKeyDown={e => { const next = e.key === "ArrowRight" ? (i + 1) % 4 : e.key === "ArrowLeft" ? (i + 3) % 4 : e.key === "Home" ? 0 : e.key === "End" ? 3 : null; if (next !== null) { e.preventDefault(); setStage(next); document.getElementById(`stage-${next}`)?.focus(); } }} onClick={() => setStage(i)}><span>0{i + 1}</span><s.icon size={18} />{s.name}<ArrowRight size={16} /></button>)}</div>
          <div id="pipeline-panel" role="tabpanel" aria-labelledby={`stage-${stage}`} className="m-stage-panel"><div key={stage} className="m-stage-detail"><span className="m-large-icon"><StageIcon size={29} strokeWidth={1.3} /></span><div className="m-code-label">{stages[stage].name.toUpperCase()} / AGENT 0{stage + 1}</div><h3>{stages[stage].verb}</h3><p>{stages[stage].copy}</p><Link href="/auth?mode=signup">Run your first workflow<ArrowUpRight size={16} /></Link></div>
            <div className="m-trace"><div className="m-window-bar"><span><i /> WORKFLOW TRACE</span><span>ILLUSTRATIVE</span></div>{stages.map((s, i) => <div className={`m-trace-step ${i === stage ? "current" : ""}`} key={s.name}><span className="m-trace-number">0{i + 1}</span><div><strong>{s.name.toLowerCase()}</strong><code>{s.action}</code></div><span className="m-trace-check"><Check size={14} /></span></div>)}<div className="m-trace-root"><GitBranch size={17} /><span>Steps → Merkle root → Chain receipt</span></div></div>
          </div>
        </div>
      </section>
      <section className="m-manifesto" data-reveal><div className="m-eyebrow">TRUST IS AN OUTPUT. NOT AN ASSUMPTION.</div><h2>A log tells you what happened.<br /><span>A proof lets you check.</span></h2><div className="m-manifesto-line"><span /><ShieldCheck size={27} strokeWidth={1.2} /><span /></div></section>
      <section className="m-section m-security" id="security"><div className="m-security-copy" data-reveal><div className="m-eyebrow">02 / VERIFICATION, BY DESIGN</div><h2>Change a word.<br /><em>Leave a trace.</em></h2><p>Content hashes make changes visible. Merkle proofs connect individual records to a root anchored on-chain. The evidence can be checked against that root.</p><div className="m-security-points"><div><LockKeyhole size={20} /><span><strong>Recorded atomically</strong><small>Steps and their anchor queue entries commit together.</small></span></div><div><GitBranch size={20} /><span><strong>Anchored in batches</strong><small>One Merkle root. An inclusion proof for each step.</small></span></div><div><Fingerprint size={20} /><span><strong>Identity attached</strong><small>Inspect the registered fingerprint behind an agent.</small></span></div></div></div><div data-reveal><EvidenceLab /><p className="m-example-note">A local SHA-256 demonstration. Live chain proofs are available in your workspace.</p></div></section>
      <section className="m-section m-workspace" data-reveal><div className="m-section-head"><div><div className="m-eyebrow">03 / YOUR EVIDENCE, IN FOCUS</div><h2>The full picture.<br /><em>Without the blind spots.</em></h2></div><Link className="m-button m-button-outline" href="/auth">Open your workspace<ArrowUpRight size={16} /></Link></div><div className="m-workspace-grid">{[{ icon: Fingerprint, n: "01", title: "Know your agents.", text: "Registered identities, configuration fingerprints, and observed trust scores.", href: "/dashboard/agents", label: "Explore agent identities" }, { icon: GitBranch, n: "02", title: "Follow the work.", text: "Run reports, recorded actions, audit filters, and exportable evidence.", href: "/dashboard/runs", label: "Explore agent runs" }, { icon: ShieldCheck, n: "03", title: "Check the evidence.", text: "Step proofs, content checks, on-chain receipts, and integrity alerts.", href: "/dashboard/proofs", label: "Explore verification" }].map(c => <Link href={c.href} className="m-workspace-card" key={c.n} aria-label={c.label}><div><c.icon size={30} strokeWidth={1.2} /><span>{c.n}</span></div><h3>{c.title}</h3><p>{c.text}</p><ArrowUpRight size={21} /></Link>)}</div></section>
      <section className="m-section m-developers" id="developers"><div data-reveal><div className="m-eyebrow">04 / BUILT FOR BUILDERS</div><h2>Your stack.<br /><em>A stronger foundation.</em></h2><p>Keep your agent runtime. Add verifiable activity with the Python or TypeScript SDK, then inspect the resulting evidence in one workspace.</p><div className="m-sdk-tags"><span>Python</span><span>TypeScript</span><span>HTTP API</span></div><Link className="m-button m-button-outline" href="/dashboard/help">View integration guide<ArrowUpRight size={16} /></Link></div><div className="m-code-window" data-reveal><div className="m-window-bar"><span><Code2 size={15} /> YOUR FIRST AUDIT TRAIL</span><span>TYPESCRIPT</span></div><pre><code><span className="m-code-comment">{"// Connect your runtime to the evidence layer."}</span>{"\n"}<span className="m-code-green">const</span>{" tc = new TrustChain(apiKey);\n\n"}<span className="m-code-green">await</span>{" tc.registerAgent({\n  agentId: "}<span className="m-code-string">&quot;research-assistant&quot;</span>{",\n  model: "}<span className="m-code-string">&quot;your-model&quot;</span>{",\n  version: "}<span className="m-code-string">&quot;1.0.0&quot;</span>{",\n  systemPrompt: "}<span className="m-code-string">&quot;Research carefully.&quot;</span>{"\n});\n\n"}<span className="m-code-green">await</span>{" tc.logAndWait({\n  agentId: "}<span className="m-code-string">&quot;research-assistant&quot;</span>{",\n  action: "}<span className="m-code-string">&quot;answer_question&quot;</span>{",\n  input: question,\n  output: answer\n});"}</code></pre><div className="m-code-bottom"><ShieldCheck size={15} /> One identity. A verifiable record.</div></div></section>
      <section className="m-final" data-reveal><div className="m-eyebrow"><i /> THE NEXT GENERATION OF ACCOUNTABLE AI</div><h2>Build something<br /><em>worth trusting.</em></h2><Link className="m-button m-button-green" href="/auth?mode=signup">Get Started<ArrowUpRight size={19} /></Link><p>Your agents. Your evidence. Your confidence.</p><div className="m-final-watermark" aria-hidden="true">trustchain.</div></section>
    </main>
    <footer className="m-footer"><Link href="/" className="m-brand"><ShieldCheck size={22} />trustchain.</Link><span>VERIFIABLE BY DESIGN. © {new Date().getFullYear()}</span><div><Link href="/dashboard/help">Docs</Link><Link href="/pricing">Pricing</Link><a href="#main">Back to top ↑</a></div></footer>
  </div>;
}
