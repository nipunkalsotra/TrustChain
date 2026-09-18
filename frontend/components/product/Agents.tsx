"use client";
import { useCallback, useState } from "react";
import { Fingerprint, Plus, ShieldCheck } from "lucide-react";
import { request } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Agent } from "@/lib/product-types";
import {
  CopyButton,
  date,
  Empty,
  ErrorBox,
  Field,
  Loading,
  message,
  Modal,
  PageTitle,
  Panel,
  SearchField,
  short,
  Status,
  useResource,
} from "./ui";
export default function Agents() {
  const { me } = useSession();
  const resource = useResource(
    useCallback(
      () => request<{ agents: Agent[] }>("/agents?include_revoked=true"),
      [],
    ),
    20000,
  );
  const [query, setQuery] = useState(""),
    [open, setOpen] = useState(false),
    [selected, setSelected] = useState<Agent | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [agentId, setAgentId] = useState(""),
    [model, setModel] = useState(""),
    [version, setVersion] = useState("1.0.0"),
    [hash, setHash] = useState("");
  const [checked, setChecked] = useState<{
    hashMatches?: boolean;
    isValid?: boolean;
    isActive?: boolean;
    exists?: boolean;
  } | null>(null);
  const canWrite = me?.active.role !== "viewer";
  const agents = (resource.data?.agents ?? []).filter((a) =>
    `${a.agentId} ${a.model}`.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <>
      <PageTitle
        title="Agent registry"
        description="A verifiable identity for every agent in your project."
      >
        {canWrite && (
          <button
            className="p-btn primary"
            onClick={() => {
              setOpen(true);
              setError("");
            }}
          >
            <Plus size={15} />
            Register agent
          </button>
        )}
      </PageTitle>
      <ErrorBox error={resource.error} retry={resource.reload} />
      {notice && (
        <div className="p-notice" role="status">
          {notice}
        </div>
      )}
      <div className="p-toolbar" style={{ padding: "0 0 22px", border: 0 }}>
        <SearchField
          value={query}
          onChange={setQuery}
          placeholder="Search agents or models…"
        />
        <span className="p-muted" style={{ fontSize: 11 }}>
          {agents.length} registered agents
        </span>
      </div>
      {resource.loading ? (
        <Loading />
      ) : agents.length ? (
        <div className="agent-grid">
          {agents.map((a) => (
            <Panel key={a.agentId} className="agent-card">
              <header>
                <span className="p-avatar">
                  <Fingerprint size={20} />
                </span>
                <Status value={a.isActive ? "active" : "revoked"} />
              </header>
              <h2>{a.agentId}</h2>
              <p>
                {a.model} <span style={{ margin: "0 8px" }}>·</span> v
                {a.version}
              </p>
              <div
                className="p-code"
                style={{ marginTop: 20, padding: "10px 12px" }}
              >
                {short(a.codeHash)}
              </div>
              <footer>
                <span className="p-muted">{date(a.registeredAt)}</span>
                <button
                  className="p-btn small"
                  onClick={() => {
                    setSelected(a);
                    setHash(a.codeHash);
                    setChecked(null);
                    setError("");
                  }}
                >
                  Inspect identity
                </button>
              </footer>
            </Panel>
          ))}
        </div>
      ) : (
        <Panel>
          <Empty
            title={
              query ? "No matching agents" : "Give your agents an identity"
            }
            description="Register a configuration hash to establish the identity that future activity can be checked against."
          >
            {canWrite && (
              <button className="p-btn primary" onClick={() => setOpen(true)}>
                <Plus size={15} />
                Register your first agent
              </button>
            )}
          </Empty>
        </Panel>
      )}
      {open && (
        <Modal
          title="Register an agent"
          description="Anchor a configuration fingerprint to your project’s registry."
          close={() => {
            if (!busy) setOpen(false);
          }}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError("");
              try {
                await request("/agents", "POST", {
                  agent_id: agentId.trim(),
                  code_hash: hash,
                  model: model.trim(),
                  version: version.trim(),
                });
                setOpen(false);
                setNotice(
                  "Registration submitted. The agent will appear after the chain indexer picks it up.",
                );
                setAgentId("");
                setModel("");
                setHash("");
                resource.reload();
              } catch (e) {
                setError(message(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Field label="Agent ID">
              <input
                className="p-input"
                required
                maxLength={100}
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                placeholder="support-assistant"
              />
            </Field>
            <div className="p-grid equal">
              <Field label="Model">
                <input
                  className="p-input"
                  required
                  maxLength={200}
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="Your model identifier"
                />
              </Field>
              <Field label="Version">
                <input
                  className="p-input"
                  required
                  maxLength={100}
                  value={version}
                  onChange={(e) => setVersion(e.target.value)}
                />
              </Field>
            </div>
            <Field
              label="Configuration hash"
              hint="Paste the Keccak-256 configuration hash produced by the TrustChain SDK. Your raw system prompt stays in your runtime."
            >
              <input
                className="p-input p-mono"
                required
                pattern="0x[0-9a-fA-F]{64}"
                value={hash}
                onChange={(e) => setHash(e.target.value)}
                placeholder="0x… (64 hexadecimal characters)"
              />
            </Field>
            <ErrorBox error={error} />
            <div className="p-modal-footer">
              <button
                type="button"
                className="p-btn"
                disabled={busy}
                onClick={() => setOpen(false)}
              >
                Cancel
              </button>
              <button
                className="p-btn primary"
                disabled={
                  busy || !agentId.trim() || !model.trim() || !version.trim()
                }
              >
                {busy ? "Registering on-chain…" : "Register agent"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {selected && (
        <Modal
          title={selected.agentId}
          description={`${selected.model} · ${selected.version}`}
          close={() => {
            if (!busy) setSelected(null);
          }}
        >
          <div className="p-field">
            <span>Registered fingerprint</span>
            <code className="p-code">{selected.codeHash}</code>
            <CopyButton value={selected.codeHash} />
          </div>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError("");
              setChecked(null);
              try {
                setChecked(
                  await request(
                    `/agents/${encodeURIComponent(selected.agentId)}/verify?code_hash=${encodeURIComponent(hash)}`,
                  ),
                );
              } catch (e) {
                setError(message(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Field
              label="Fingerprint to verify"
              hint="Use the hash of the current agent configuration to check for identity drift."
            >
              <input
                className="p-input p-mono"
                pattern="0x[0-9a-fA-F]{64}"
                required
                value={hash}
                onChange={(e) => setHash(e.target.value)}
              />
            </Field>
            <ErrorBox error={error} />
            {checked && (
              <div
                className={checked.isValid ? "p-notice" : "p-error"}
                role="status"
              >
                {checked.isValid
                  ? "The supplied fingerprint matches an active on-chain identity."
                  : "Verification did not pass. The identity is missing, inactive, or the fingerprint differs."}
              </div>
            )}
            <button className="p-btn primary p-full" disabled={busy}>
              <ShieldCheck size={16} />
              {busy ? "Checking the chain…" : "Verify fingerprint"}
            </button>
          </form>
        </Modal>
      )}
    </>
  );
}
