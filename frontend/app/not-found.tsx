import Link from "next/link";
import { ArrowUpRight, ShieldCheck } from "lucide-react";
import "@/components/marketing/marketing.css";
export default function NotFound() {
  return <main className="mkt-page m-standalone"><Link className="m-brand" href="/"><ShieldCheck size={25} />trustchain.</Link><div className="m-standalone-content"><div className="m-eyebrow">404 / OFF THE RECORD</div><h1>Let’s get you<br /><em>back on track.</em></h1><p>This page may have moved or the address may be incorrect. Your workspace is a good place to start again.</p><div className="m-hero-actions"><Link href="/dashboard" className="m-button m-button-green">Open your workspace<ArrowUpRight size={17} /></Link><Link href="/" className="m-button m-button-outline">Return to TrustChain</Link></div></div></main>;
}
