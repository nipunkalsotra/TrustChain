import Link from "next/link";
import {
  Activity,
  ArrowRight,
  Fingerprint,
  KeyRound,
  ShieldCheck,
  Users,
} from "lucide-react";
export default function Page() {
  return (
    <>
      <header className="p-heading">
        <div>
          <div className="p-eyebrow">A GOOD PLACE TO START</div>
          <h1>From your first agent to your first proof.</h1>
          <p>
            A quick guide to getting the most from your TrustChain workspace.
          </p>
        </div>
      </header>
      <div className="p-stack">
        {[
          {
            icon: Activity,
            title: "01 / Run a traceable workflow",
            body: "Open Agent runs and choose New run. Describe your task. The built-in Researcher, Validator, Scorer, and Reporter pipeline records its activity as it works. Open a run to follow its progress and read the final report.",
            href: "/dashboard/runs",
            label: "Open agent runs",
          },
          {
            icon: Fingerprint,
            title: "02 / Bring your own agent",
            body: "Register your agent ID, model, version, and the configuration fingerprint produced by the TrustChain SDK. After the transaction is indexed, the identity appears in your registry. Inspect it to compare a current fingerprint against the on-chain record.",
            href: "/dashboard/agents",
            label: "Open agent registry",
          },
          {
            icon: KeyRound,
            title: "03 / Connect your runtime",
            body: "Verify your email, then create a project API key with the scopes your integration needs. Use logs:write to submit steps, agents:register to register identities, and runs:read to inspect evidence. Save the key securely: it is shown only once.",
            href: "/dashboard/keys",
            label: "Manage API keys",
          },
          {
            icon: ShieldCheck,
            title: "04 / Follow the evidence",
            body: "Each action appears in the audit trail. The anchor worker batches steps into a Merkle tree and confirms the root on-chain. In Verification, check run integrity, retrieve a step’s Merkle proof, or compare original content with a recorded hash. A pending step is not yet a confirmed on-chain record.",
            href: "/dashboard/proofs",
            label: "Open verification",
          },
          {
            icon: Users,
            title: "05 / Build with your team",
            body: "Invite teammates as viewers, members, or administrators. Use projects to separate integrations and evidence. The project switcher changes the scope of the dashboard. Organization alerts and team membership span your projects.",
            href: "/dashboard/team",
            label: "Manage your team",
          },
        ].map((item) => (
          <section className="p-panel p-pad" key={item.href}>
            <div className="p-identity" style={{ alignItems: "flex-start" }}>
              <span className="p-avatar">
                <item.icon size={20} />
              </span>
              <div>
                <h2>{item.title}</h2>
                <p
                  className="p-muted"
                  style={{
                    fontSize: 13,
                    lineHeight: 1.9,
                    margin: "10px 0 16px",
                    maxWidth: 740,
                  }}
                >
                  {item.body}
                </p>
                <Link className="p-text-link" href={item.href}>
                  {item.label}
                  <ArrowRight size={14} />
                </Link>
              </div>
            </div>
          </section>
        ))}
      </div>
    </>
  );
}
