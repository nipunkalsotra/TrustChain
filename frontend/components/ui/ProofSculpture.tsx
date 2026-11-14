import { ShieldCheck } from "lucide-react";

/** A code-native evidence sculpture; no image downloads or WebGL required. */
export function ProofSculpture({ compact = false }: { compact?: boolean }) {
  return <div className={`proof-sculpture ${compact ? "compact" : ""}`} aria-hidden="true">
    <div className="proof-aura" /><div className="proof-orbit orbit-a" /><div className="proof-orbit orbit-b" /><div className="proof-orbit orbit-c" />
    <div className="proof-object"><div className="proof-object-top" /><div className="proof-object-front"><div className="proof-engraving"><ShieldCheck strokeWidth={0.7} /><span>TRUST / VERIFIED</span></div>{Array.from({ length: 9 }, (_, i) => <i key={i} style={{ top: `${12 + i * 9}%` }} />)}</div><div className="proof-object-side" /></div>
    <div className="proof-floor" /><span className="proof-satellite s-one" /><span className="proof-satellite s-two" /><span className="proof-satellite s-three" />
    <svg className="proof-paths" viewBox="0 0 600 600" fill="none"><path d="M50 370H160L220 310M370 225L440 155H565M365 380L430 440H550" /><circle cx="50" cy="370" r="4" /><circle cx="565" cy="155" r="4" /><circle cx="550" cy="440" r="4" /></svg>
  </div>;
}
