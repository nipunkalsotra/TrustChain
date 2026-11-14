import Link from "next/link";
import { ArrowUpRight, Check, ShieldCheck } from "lucide-react";
import { MotionControl } from "@/components/ui/MotionControl";
import "@/components/marketing/marketing.css";
export const metadata = { title: "Pricing — TrustChain" };
export default function Page() {
  return <main className="mkt-page m-standalone"><Link className="m-brand" href="/"><ShieldCheck size={25} />trustchain.</Link><div className="m-standalone-content"><div className="m-eyebrow">A STRONGER FOUNDATION FOR YOUR AI</div><h1>Start with proof.<br /><em>Grow with confidence.</em></h1><p>Our pricing plans are being finalized. You can create a workspace and explore the platform today.</p><div className="m-pricing-features">{["Agent identities and workflow traces", "Merkle proofs and on-chain receipts", "Project workspaces and team access"].map(s => <span key={s}><Check size={15} />{s}</span>)}</div><div className="m-hero-actions"><Link href="/auth?mode=signup" className="m-button m-button-green">Create a workspace<ArrowUpRight size={17} /></Link><Link className="m-button m-button-outline" href="/">Return to TrustChain</Link></div><p className="m-example-note">Published plans and commercial terms will appear here when available.</p></div><MotionControl /></main>;
}
