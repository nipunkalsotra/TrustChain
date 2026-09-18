"use client";
import { Suspense, useCallback, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowRight,
  FileCheck2,
  Layers,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { getAuditLog, getChainStatus, request } from "@/lib/api";
import type { Proof, RunVerification } from "@/lib/product-types";
import {
  Badge,
  CopyButton,
  date,
  Empty,
  ErrorBox,
  ExportButton,
  Field,
  Loading,
  message,
  PageTitle,
  Panel,
  SearchField,
  short,
  Status,
  useResource,
} from "./ui";
export function AuditTrail() {
  const resource = useResource(
    useCallback(() => getAuditLog(), []),
    30000,
  );
  const [query, setQuery] = useState(""),
    [status, setStatus] = useState("all"),
    [page, setPage] = useState(0);
  const entries = (resource.data?.entries ?? [])
    .filter(
      (e) =>
        (status === "all" || e.anchorStatus === status) &&
        `${e.agentId} ${e.runId} ${e.action}`
          .toLowerCase()
          .includes(query.toLowerCase()),
    )
    .sort((a, b) => b.timestamp - a.timestamp);
  const currentPage = Math.min(
    page,
    Math.max(0, Math.ceil(entries.length / 20) - 1),
  );
  return (
    <>
      <PageTitle
        eyebrow="TRUST & SECURITY"
        title="Audit trail"
        description="An unbroken record of what your agents did, and when."
      >
        <ExportButton data={entries} name="trustchain-audit.json" />
      </PageTitle>
      <ErrorBox error={resource.error} retry={resource.reload} />
      <Panel>
        <div className="p-toolbar">
          <SearchField
            value={query}
            onChange={(v) => {
              setQuery(v);
              setPage(0);
            }}
            placeholder="Search agents, actions, or runs…"
          />
          <div className="p-actions">
            <select
              className="p-select"
              aria-label="Anchor status"
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setPage(0);
              }}
            >
              <option value="all">All anchor states</option>
              <option value="confirmed">Confirmed</option>
              <option value="pending">Pending</option>
              <option value="submitted">Submitted</option>
            </select>
            <button
              aria-label="Refresh audit trail"
              className="p-icon-btn"
              onClick={resource.reload}
            >
              <RefreshCw size={15} />
            </button>
          </div>
        </div>
        {resource.loading ? (
          <Loading />
        ) : entries.length ? (
          <div className="p-table-wrap">
            <table className="p-table">
              <thead>
                <tr>
                  <th>Event</th>
                  <th>Agent</th>
                  <th>Run</th>
                  <th>Anchor status</th>
                  <th>Recorded</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {entries
                  .slice(currentPage * 20, currentPage * 20 + 20)
                  .map((e) => (
                    <tr key={e.entryId}>
                      <td>
                        {e.action}
                        <small>Step #{e.entryId}</small>
                      </td>
                      <td>{e.agentId}</td>
                      <td>
                        <Link
                          className="p-mono"
                          href={`/dashboard/runs/${encodeURIComponent(e.runId)}`}
                        >
                          {short(e.runId)}
                        </Link>
                      </td>
                      <td>
                        <Status value={e.anchorStatus} />
                      </td>
                      <td className="p-muted">{date(e.timestamp)}</td>
                      <td>
                        <Link
                          className="p-text-link"
                          href={`/dashboard/proofs?step=${e.entryId}`}
                        >
                          Inspect <ArrowRight size={13} />
                        </Link>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title={
              query || status !== "all"
                ? "No matching events"
                : "Your evidence starts with an action"
            }
            description="Run a workflow or send an instrumented step to create the first entry in your audit trail."
          />
        )}
        <div className="p-table-foot">
          <span>{entries.length} recorded events</span>
          <div className="p-actions">
            <button
              className="p-btn small"
              disabled={currentPage === 0}
              onClick={() => setPage(currentPage - 1)}
            >
              Previous
            </button>
            <span>
              {currentPage + 1} / {Math.max(1, Math.ceil(entries.length / 20))}
            </span>
            <button
              className="p-btn small"
              disabled={(currentPage + 1) * 20 >= entries.length}
              onClick={() => setPage(currentPage + 1)}
            >
              Next
            </button>
          </div>
        </div>
      </Panel>
    </>
  );
}
export function Verification() {
  return (
    <Suspense fallback={<Loading />}>
      <VerificationForm />
    </Suspense>
  );
}
function VerificationForm() {
  const params = useSearchParams();
  const [tab, setTab] = useState(params.has("step") ? "proof" : "run"),
    [run, setRun] = useState(params.get("run") ?? ""),
    [step, setStep] = useState(params.get("step") ?? ""),
    [text, setText] = useState(""),
    [field, setField] = useState("output");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [verified, setVerified] = useState<RunVerification | null>(null),
    [proof, setProof] = useState<Proof | null>(null),
    [content, setContent] = useState<Record<string, unknown> | null>(null);
  return (
    <>
      <PageTitle
        eyebrow="TRUST & SECURITY"
        title="Verification studio"
        description="Move from trusting a record to checking the evidence."
      />
      <div className="p-tabs">
        {[
          ["run", "Run integrity"],
          ["proof", "Merkle proof"],
          ["content", "Content verification"],
        ].map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? "active" : ""}
            onClick={() => {
              setTab(id);
              setError("");
              setVerified(null);
              setProof(null);
              setContent(null);
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="p-grid">
        <Panel
          title={
            tab === "run"
              ? "Verify a workflow"
              : tab === "proof"
                ? "Inspect a step’s proof"
                : "Check original content"
          }
          description="Evidence is checked within your active project."
        >
          <form
            className="p-pad"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError("");
              setVerified(null);
              setProof(null);
              setContent(null);
              try {
                if (tab === "run")
                  setVerified(
                    await request<RunVerification>(
                      `/integrity/verify-run/${encodeURIComponent(run.trim())}`,
                      "POST",
                    ),
                  );
                else if (tab === "proof")
                  setProof(await request<Proof>(`/steps/${step}/proof`));
                else
                  setContent(
                    await request("/integrity/verify-content", "POST", {
                      stepId: Number(step),
                      field,
                      candidateText: text,
                    }),
                  );
              } catch (e) {
                setError(message(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {tab === "run" ? (
              <Field label="Run ID">
                <input
                  className="p-input"
                  required
                  value={run}
                  onChange={(e) => setRun(e.target.value)}
                  placeholder="Paste a run ID from your history"
                />
              </Field>
            ) : (
              <Field label="Step ID">
                <input
                  className="p-input"
                  type="number"
                  min={1}
                  step={1}
                  required
                  value={step}
                  onChange={(e) => setStep(e.target.value)}
                  placeholder="For example, 42"
                />
              </Field>
            )}
            {tab === "content" && (
              <>
                <Field label="Content field">
                  <select
                    className="p-select"
                    value={field}
                    onChange={(e) => setField(e.target.value)}
                  >
                    <option value="output">Agent output</option>
                    <option value="input">Agent input</option>
                  </select>
                </Field>
                <Field
                  label="Original content"
                  hint="Paste the exact text. Whitespace and punctuation affect the hash."
                >
                  <textarea
                    className="p-textarea"
                    required
                    maxLength={100000}
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                  />
                </Field>
              </>
            )}
            <ErrorBox error={error} />
            <button className="p-btn primary" disabled={busy}>
              <ShieldCheck size={16} />
              {busy
                ? "Checking evidence…"
                : tab === "proof"
                  ? "Retrieve proof"
                  : "Run verification"}
            </button>
          </form>
        </Panel>
        <Panel title="Know what you’re checking">
          <div className="p-pad p-stack">
            <div className="p-identity">
              <span className="p-avatar">
                <FileCheck2 size={18} />
              </span>
              <div>
                <h3>
                  {tab === "run"
                    ? "Record integrity"
                    : tab === "proof"
                      ? "Merkle inclusion"
                      : "Content fingerprint"}
                </h3>
                <p
                  className="p-muted"
                  style={{ fontSize: 12, marginTop: 8, lineHeight: 1.9 }}
                >
                  {tab === "run"
                    ? "Recomputes recorded step hashes and checks batch roots. An intact record can still be waiting for an on-chain anchor."
                    : tab === "proof"
                      ? "Retrieves the path from a recorded step to its batch root, with transaction and block references when available."
                      : "Compares the hash of your candidate text with the recorded input or output hash. Original content is not stored in the audit trail."}
                </p>
              </div>
            </div>
            <Link className="p-text-link" href="/dashboard/audit">
              Find a run or step in the audit trail <ArrowRight size={14} />
            </Link>
          </div>
        </Panel>
      </div>
      {verified && (
        <Panel
          title="Integrity results"
          className="evidence-result"
          action={<ExportButton data={verified} />}
        >
          <div className="p-pad">
            <div
              className={
                verified.steps.length && verified.allVerified
                  ? "p-notice"
                  : "p-error"
              }
              role="status"
            >
              {!verified.steps.length
                ? "This run has no recorded steps to verify."
                : verified.allVerified
                  ? `All ${verified.steps.length} recorded steps passed the integrity check. Anchoring status is shown separately below.`
                  : "An integrity mismatch was detected. Review the affected steps."}
            </div>
          </div>
          <div className="p-table-wrap">
            <table className="p-table">
              <thead>
                <tr>
                  <th>Step</th>
                  <th>Agent</th>
                  <th>Integrity</th>
                  <th>Batch assigned</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {verified.steps.map((s) => (
                  <tr key={s.stepId}>
                    <td>#{s.stepId}</td>
                    <td>{s.agentId}</td>
                    <td>
                      <Status value={s.verified ? "verified" : "mismatch"} />
                      {s.reason && <small>{s.reason}</small>}
                    </td>
                    <td>{s.anchored ? "Yes" : "Pending"}</td>
                    <td>
                      <button
                        className="p-btn small"
                        onClick={() => {
                          setStep(String(s.stepId));
                          setTab("proof");
                          setVerified(null);
                        }}
                      >
                        Inspect proof
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
      {proof && (
        <Panel
          title={`Proof for step #${proof.stepId}`}
          className="evidence-result"
          action={
            <ExportButton
              data={proof}
              name={`trustchain-proof-${proof.stepId}.json`}
            />
          }
        >
          <div className="p-pad p-stack">
            <div className="p-actions">
              <Status value={proof.anchorStatus} />
              <Badge>Leaf schema v{proof.leafSchemaVersion}</Badge>
              <span className="p-muted">
                Block {proof.blockNumber ?? "pending"}
              </span>
            </div>
            {[
              ["Merkle root", proof.root],
              ["Leaf hash", proof.leaf],
              ["Transaction hash", proof.txHash],
              ["Block hash", proof.blockHash],
            ].map(([label, value]) => (
              <div key={label}>
                <div
                  className="p-actions"
                  style={{ justifyContent: "space-between", marginBottom: 8 }}
                >
                  <h3>{label}</h3>
                  {value && <CopyButton value={value} />}
                </div>
                <div className="p-code">{value ?? "Not yet available"}</div>
              </div>
            ))}
            <details>
              <summary>View complete proof</summary>
              <pre className="p-code" style={{ marginTop: 12 }}>
                {JSON.stringify(proof, null, 2)}
              </pre>
            </details>
          </div>
        </Panel>
      )}
      {content && (
        <Panel
          title="Content verification result"
          className="evidence-result"
          action={<ExportButton data={content} />}
        >
          <div className="p-pad">
            <div
              className={content.matchesCurrent ? "p-notice" : "p-error"}
              role="status"
            >
              {content.matchesCurrent
                ? "The supplied text matches the recorded content hash."
                : "The supplied text does not match the recorded content hash."}
            </div>
            <pre className="p-code">{JSON.stringify(content, null, 2)}</pre>
          </div>
        </Panel>
      )}
    </>
  );
}
export function Anchors() {
  const audit = useResource(
      useCallback(() => getAuditLog(), []),
      30000,
    ),
    chain = useResource(
      useCallback(() => getChainStatus(), []),
      30000,
    );
  const [query, setQuery] = useState("");
  const groups = new Map<
    string,
    {
      txHash: string;
      status: string;
      steps: number;
      entryId: number;
      timestamp: number;
    }
  >();
  for (const e of audit.data?.entries ?? []) {
    if (!e.txHash.startsWith("0x")) continue;
    const prior = groups.get(e.txHash);
    if (prior) prior.steps++;
    else
      groups.set(e.txHash, {
        txHash: e.txHash,
        status: e.anchorStatus,
        steps: 1,
        entryId: e.entryId,
        timestamp: e.timestamp,
      });
  }
  const anchors = [...groups.values()].filter((a) =>
    a.txHash.toLowerCase().includes(query.toLowerCase()),
  );
  const pending =
    audit.data?.entries.filter((e) => e.anchorStatus !== "confirmed").length ??
    0;
  return (
    <>
      <PageTitle
        eyebrow="TRUST & SECURITY"
        title="On-chain anchors"
        description="Follow your project’s evidence from recorded steps to confirmed transactions."
      >
        <ExportButton data={anchors} name="trustchain-anchors.json" />
      </PageTitle>
      <div
        className="p-stats"
        style={{ gridTemplateColumns: "repeat(3, 1fr)" }}
      >
        {[
          {
            label: "Confirmed transactions",
            value: anchors.filter((a) => a.status === "confirmed").length,
          },
          { label: "Steps awaiting confirmation", value: pending },
          {
            label: "Pipeline chain",
            value: chain.data?.connected
              ? `#${chain.data.chainId}`
              : "Unavailable",
          },
        ].map((s) => (
          <div className="p-stat" key={s.label}>
            <div className="p-stat-top">
              {s.label}
              <Layers size={15} />
            </div>
            <div className="p-stat-value">{s.value}</div>
          </div>
        ))}
      </div>
      <ErrorBox
        error={audit.error || chain.error}
        retry={() => {
          audit.reload();
          chain.reload();
        }}
      />
      <Panel
        title="Anchor transactions"
        description="Grouped by confirmed transaction from your project’s audit trail"
      >
        <div className="p-toolbar">
          <SearchField
            value={query}
            onChange={setQuery}
            placeholder="Search transaction hashes…"
          />
          <Badge>Refreshes every 30 seconds</Badge>
        </div>
        {audit.loading ? (
          <Loading />
        ) : anchors.length ? (
          <div className="p-table-wrap">
            <table className="p-table">
              <thead>
                <tr>
                  <th>Transaction</th>
                  <th>Status</th>
                  <th>Project steps</th>
                  <th>First step recorded</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {anchors.map((a) => (
                  <tr key={a.txHash}>
                    <td className="p-mono">{short(a.txHash)}</td>
                    <td>
                      <Status value={a.status} />
                    </td>
                    <td>{a.steps}</td>
                    <td className="p-muted">{date(a.timestamp)}</td>
                    <td>
                      <Link
                        className="p-text-link"
                        href={`/dashboard/proofs?step=${a.entryId}`}
                      >
                        Inspect proof <ArrowRight size={14} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="Evidence is on its way"
            description="Confirmed anchor transactions will appear when the worker batches your recorded steps and confirms them on-chain."
          />
        )}
      </Panel>
    </>
  );
}
