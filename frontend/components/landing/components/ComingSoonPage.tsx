"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowUpRight, ShieldCheck } from "lucide-react";
import { MotionControl } from "@/components/ui/MotionControl";
import "@/components/marketing/marketing.css";
export function ComingSoonShell({ label }: { label?: string }) {
  return <main className="mkt-page m-standalone"><Link className="m-brand" href="/"><ShieldCheck size={25} />trustchain.</Link><div className="m-standalone-content"><div className="m-eyebrow">THE NEXT CHAPTER</div><h1>{label ?? "Something good"}<br /><em>is taking shape.</em></h1><p>We’re preparing this part of TrustChain. In the meantime, your workspace has a guide to agents, audit trails, and verification.</p><div className="m-hero-actions"><Link href="/dashboard/help" className="m-button m-button-green">Open workspace guide<ArrowUpRight size={17} /></Link><Link href="/" className="m-button m-button-outline">Return to TrustChain</Link></div></div><MotionControl /></main>;
}
export function ComingSoon() { const params = useSearchParams(); return <ComingSoonShell label={params.get("from") === "docs" ? "Documentation" : undefined} />; }
