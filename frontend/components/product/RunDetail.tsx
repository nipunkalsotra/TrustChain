"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowLeft,
  CheckCircle2,
  FileCheck2,
  RefreshCw,
} from "lucide-react";
import {
  ApiRequestError,
  getAuditLog,
  getRun,
  refreshStreamToken,
  resolveStreamUrl,
} from "@/lib/api";
import {
  date,
  Empty,
  ErrorBox,
  ExportButton,
  Loading,
  message,
  PageTitle,
  Panel,
  short,
  Status,
  useResource,
} from "./ui";
import type { SSEEvent } from "@/lib/types";
export default function RunDetail({ id }: { id: string }) {
  const [events, setEvents] = useState<SSEEvent[]>([]),
    [state, setState] = useState("connecting"),
    [streamError, setStreamError] = useState(""),
    [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const audit = useResource(
    useCallback(() => getAuditLog(id), [id]),
    10000,
  );
  const reloadAudit = audit.reload;
  useEffect(() => {
    let active = true,
      source: EventSource | null = null,
      timer: ReturnType<typeof setTimeout> | undefined;
    async function connect() {
      try {
        // Completed runs can be opened without replaying an expired stream.
        const record = await getRun(id).catch((error) => {
          if (error instanceof ApiRequestError && error.status === 404)
            return null;
          throw error;
        });
        if (!active) return;
        if (record) {
          setResult(record);
          setState(
            record.error ||
              record.type === "error" ||
              record.status === "error" ||
              typeof record.message === "string"
              ? "error"
              : "complete",
          );
          setStreamError(
            typeof record.message === "string" ? record.message : "",
          );
          return;
        }
        const { stream_url } = await refreshStreamToken(id);
        if (!active) return;
        source = new EventSource(resolveStreamUrl(stream_url), {
          withCredentials: true,
        });
        source.onopen = () => {
          setState("running");
          setStreamError("");
        };
        source.onmessage = (e) => {
          try {
            const event = JSON.parse(e.data) as SSEEvent;
            setEvents((previous) =>
              event.type
                ? previous
                : previous.some(
                      (p) =>
                        p.step === event.step && p.agentId === event.agentId,
                    )
                  ? previous
                  : [...previous, event],
            );
            if (event.type === "run_complete" || event.type === "error") {
              source?.close();
              setState(event.type === "error" ? "error" : "complete");
              if (event.type === "error")
                setStreamError(event.message || "This run failed.");
              if (event.report)
                setResult({ report: event.report, score: event.score });
              getRun(id)
                .then((r) => {
                  if (active) setResult(r);
                })
                .catch((e) => {
                  if (
                    active &&
                    e instanceof ApiRequestError &&
                    e.status === 404
                  )
                    timer = setTimeout(connect, 1000);
                  else if (active) setStreamError(message(e));
                });
              reloadAudit();
            }
          } catch {
            setStreamError(
              "An update could not be read. Reconnect to refresh the run.",
            );
          }
        };
        source.onerror = () => {
          source?.close();
          if (active) {
            setState("reconnecting");
            setStreamError(
              "Live connection interrupted. Reconnecting in 5 seconds…",
            );
            timer = setTimeout(connect, 5000);
          }
        };
      } catch (e) {
        if (active) {
          setState("disconnected");
          setStreamError(message(e));
        }
      }
    }
    void connect();
    return () => {
      active = false;
      source?.close();
      clearTimeout(timer);
    };
  }, [id, attempt, reloadAudit]);
  const entries = audit.data?.entries ?? [];
  const agentIds = new Set([
    ...entries.map((e) => e.agentId),
    ...events.map((e) => e.agentId),
  ]);
  const report = typeof result?.report === "string" ? result.report : "";
  return (
    <>
      <Link
        className="p-text-link"
        href="/dashboard/runs"
        style={{ marginBottom: 22 }}
      >
        <ArrowLeft size={14} />
        All runs
      </Link>
      <PageTitle
        eyebrow="WORKFLOW DETAILS"
        title="Run overview"
        description={id}
      >
        <ExportButton
          data={{ runId: id, result, entries }}
          name={`trustchain-run-${id}.json`}
        />
        <Link
          className="p-btn primary"
          href={`/dashboard/proofs?run=${encodeURIComponent(id)}`}
        >
          <FileCheck2 size={15} />
          Verify this run
        </Link>
      </PageTitle>
      <ErrorBox
        error={streamError || audit.error}
        retry={() => {
          setAttempt((n) => n + 1);
          reloadAudit();
        }}
      />
      <div className="p-stack">
        <Panel>
          <div className="p-panel-head">
            <div>
              <h2>
                {typeof result?.task === "string"
                  ? result.task
                  : "Agent workflow"}
              </h2>
              <p>
                Run ID <span className="p-mono">{short(id)}</span>
              </p>
            </div>
            <Status value={state} />
          </div>
          <div className="p-pad">
            <div className="run-stages">
              {["Researcher", "Validator", "Scorer", "Reporter"].map(
                (name, i) => {
                  const done = [...agentIds].some((a) =>
                    a.toLowerCase().includes(name.toLowerCase()),
                  );
                  return (
                    <div
                      className={`run-stage ${done ? "done" : ""}`}
                      key={name}
                    >
                      {done ? (
                        <CheckCircle2 size={19} />
                      ) : (
                        <Activity size={19} className="p-muted" />
                      )}
                      <b>
                        {i + 1}. {name}
                      </b>
                      <small>
                        {done
                          ? "Activity recorded"
                          : state === "complete"
                            ? "No activity recorded"
                            : "Awaiting activity"}
                      </small>
                    </div>
                  );
                },
              )}
            </div>
            <p className="p-muted" style={{ fontSize: 11, marginTop: 16 }}>
              The four stages apply to the built-in pipeline. Instrumented
              agents appear in the activity trail below.
            </p>
          </div>
        </Panel>
        <div className="p-grid">
          <Panel
            title="Activity trail"
            description="Persisted steps, refreshed every 10 seconds"
            action={
              <button
                aria-label="Refresh activity"
                className="p-icon-btn"
                onClick={audit.reload}
              >
                <RefreshCw size={15} />
              </button>
            }
          >
            {audit.loading ? (
              <Loading />
            ) : entries.length ? (
              <div className="p-steps">
                {entries.map((e) => (
                  <div key={e.entryId} className="p-step">
                    <span>{e.stepIndex}</span>
                    <div>
                      <h3>{e.agentId}</h3>
                      <p>{e.action}</p>
                      <Link
                        className="p-text-link"
                        href={`/dashboard/proofs?step=${e.entryId}`}
                        style={{ marginTop: 8 }}
                      >
                        Inspect evidence
                      </Link>
                    </div>
                    <div style={{ flex: "none", textAlign: "right" }}>
                      <Status value={e.anchorStatus} />
                      <p>{date(e.timestamp)}</p>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <Empty
                title={
                  state === "running"
                    ? "Agents are working"
                    : "No steps recorded"
                }
                description="Agent actions will appear here as they are recorded by the backend."
              />
            )}
          </Panel>
          <Panel title="Run summary">
            <div className="p-row">
              <span>Status</span>
              <Status value={state} />
            </div>
            <div className="p-row">
              <span>Recorded steps</span>
              <b>{entries.length}</b>
            </div>
            <div className="p-row">
              <span>Agents observed</span>
              <b>{agentIds.size}</b>
            </div>
            <div className="p-row">
              <span>Final trust score</span>
              <b>
                {typeof result?.score === "number"
                  ? `${result.score} / 100`
                  : "—"}
              </b>
            </div>
            <div className="p-pad">
              <p className="p-muted" style={{ fontSize: 11 }}>
                Recorded steps may still be waiting for a batch to be confirmed
                on-chain. Check each step’s evidence for its anchor status.
              </p>
            </div>
          </Panel>
        </div>
        <Panel
          title="Final report"
          description="The output produced by the agent workflow"
        >
          {report ? (
            <div className="p-pad p-report">{report}</div>
          ) : (
            <Empty
              title={
                state === "error"
                  ? "The run did not finish successfully"
                  : "The report will appear here"
              }
              description={
                typeof result?.error === "string"
                  ? result.error
                  : "A completed pipeline run includes a report from the Reporter agent."
              }
            />
          )}
        </Panel>
      </div>
    </>
  );
}
