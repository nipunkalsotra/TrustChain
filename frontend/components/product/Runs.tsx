"use client";
import { useCallback, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Activity, ArrowRight, Plus, RefreshCw } from "lucide-react";
import { getRuns, startRun } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { RunRecord } from "@/lib/types";
import {
  date,
  Empty,
  ErrorBox,
  ExportButton,
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
export function NewRunButton() {
  const [open, setOpen] = useState(false),
    [task, setTask] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const router = useRouter(),
    { me } = useSession();
  if (me?.active.role === "viewer") return null;
  return (
    <>
      <button className="p-btn primary" onClick={() => setOpen(true)}>
        <Plus size={16} />
        New run
      </button>
      {open && (
        <Modal
          title="Start an agent run"
          description="Turn a question into a traceable research workflow."
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
                const run = await startRun(task.trim());
                router.push(
                  `/dashboard/runs/${encodeURIComponent(run.run_id)}`,
                );
                setOpen(false);
              } catch (e) {
                setError(message(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Field
              label="What would you like to investigate?"
              hint="The Researcher, Validator, Scorer, and Reporter will work through your task. Each step is recorded for verification."
            >
              <textarea
                className="p-textarea"
                required
                minLength={1}
                maxLength={10000}
                rows={5}
                placeholder="For example: Research the trade-offs of using blockchain for AI audit trails."
                value={task}
                onChange={(e) => setTask(e.target.value)}
              />
            </Field>
            <div className="p-notice">
              Runs use your organization’s configured model provider and token
              budget.
            </div>
            <ErrorBox error={error} />
            <div className="p-modal-footer">
              <button
                className="p-btn"
                type="button"
                disabled={busy}
                onClick={() => setOpen(false)}
              >
                Cancel
              </button>
              <button className="p-btn primary" disabled={busy || !task.trim()}>
                <Activity size={15} />
                {busy ? "Starting run…" : "Start run"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
export function RunsTable({ runs }: { runs: RunRecord[] }) {
  return (
    <div className="p-table-wrap">
      <table className="p-table">
        <thead>
          <tr>
            <th>Run / Task</th>
            <th>Status</th>
            <th>Started</th>
            <th>Score</th>
            <th>
              <span className="p-sr">Open run</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.runId}>
              <td className="task">
                <Link href={`/dashboard/runs/${encodeURIComponent(run.runId)}`}>
                  {run.task || "Instrumented agent run"}
                  <small className="p-mono">{short(run.runId)}</small>
                </Link>
              </td>
              <td>
                <Status value={run.status} />
              </td>
              <td className="p-muted">{date(run.createdAt)}</td>
              <td>
                {typeof run.result?.score === "number"
                  ? `${run.result.score} / 100`
                  : "—"}
              </td>
              <td>
                <Link
                  className="p-icon-btn"
                  aria-label={`Open run ${run.runId}`}
                  href={`/dashboard/runs/${encodeURIComponent(run.runId)}`}
                >
                  <ArrowRight size={15} />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export default function Runs() {
  const resource = useResource(
    useCallback(() => getRuns(200), []),
    15000,
  );
  const [query, setQuery] = useState(""),
    [status, setStatus] = useState("all"),
    [page, setPage] = useState(0);
  const runs = (resource.data?.runs ?? []).filter(
    (r) =>
      (status === "all" || r.status === status) &&
      `${r.task} ${r.runId}`.toLowerCase().includes(query.toLowerCase()),
  );
  const safePage = Math.min(page, Math.max(0, Math.ceil(runs.length / 10) - 1));
  return (
    <>
      <PageTitle
        title="Agent runs"
        description="Every workflow, from the first question to the final answer."
      >
        <ExportButton data={runs} name="trustchain-runs.json" />
        <NewRunButton />
      </PageTitle>
      <div className="p-tabs">
        {["all", "running", "complete", "error"].map((s) => (
          <button
            key={s}
            className={status === s ? "active" : ""}
            onClick={() => {
              setStatus(s);
              setPage(0);
            }}
          >
            {s === "all"
              ? "All runs"
              : s === "complete"
                ? "Completed"
                : s === "error"
                  ? "Failed"
                  : "Running"}{" "}
            <span className="p-muted">
              {resource.data?.runs.filter((r) => s === "all" || r.status === s)
                .length ?? 0}
            </span>
          </button>
        ))}
      </div>
      <ErrorBox error={resource.error} retry={resource.reload} />
      <Panel>
        <div className="p-toolbar">
          <SearchField
            value={query}
            onChange={(q) => {
              setQuery(q);
              setPage(0);
            }}
            placeholder="Search tasks or run IDs…"
          />
          <button className="p-btn small" onClick={resource.reload}>
            <RefreshCw size={13} />
            Refresh
          </button>
        </div>
        {resource.loading ? (
          <Loading />
        ) : runs.length ? (
          <RunsTable runs={runs.slice(safePage * 10, safePage * 10 + 10)} />
        ) : (
          <Empty
            title={
              query || status !== "all"
                ? "No matching runs"
                : "Your first run starts here"
            }
            description="Launch a task to see its progress, agent actions, and verifiable audit trail."
          >
            <NewRunButton />
          </Empty>
        )}
        <div className="p-table-foot">
          <span>
            {runs.length} runs in the latest 200 · Updates every 15 seconds
          </span>
          <div className="p-actions">
            <button
              className="p-btn small"
              disabled={safePage === 0}
              onClick={() => setPage(safePage - 1)}
            >
              Previous
            </button>
            <span>
              {safePage + 1} / {Math.max(1, Math.ceil(runs.length / 10))}
            </span>
            <button
              className="p-btn small"
              disabled={(safePage + 1) * 10 >= runs.length}
              onClick={() => setPage(safePage + 1)}
            >
              Next
            </button>
          </div>
        </div>
      </Panel>
    </>
  );
}
