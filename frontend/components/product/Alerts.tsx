"use client";
import { useCallback, useState } from "react";
import { Bell, CheckCheck, RefreshCw } from "lucide-react";
import { request } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Alert } from "@/lib/product-types";
import {
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
  Status,
  useResource,
} from "./ui";
export default function Alerts() {
  const { me } = useSession(),
    admin = ["owner", "admin"].includes(me?.active.role ?? "");
  const [status, setStatus] = useState("open"),
    [severity, setSeverity] = useState("all"),
    [query, setQuery] = useState(""),
    [cursor, setCursor] = useState<number | null>(null);
  const resource = useResource(
    useCallback(
      () =>
        request<{
          alerts: Alert[];
          nextCursor: number | null;
          totalOpen: number;
        }>(
          `/alerts?limit=50${status !== "all" ? `&status=${status}` : ""}${severity !== "all" ? `&severity=${severity}` : ""}${cursor ? `&before_id=${cursor}` : ""}`,
        ),
      [status, severity, cursor],
    ),
    30000,
  );
  const [selected, setSelected] = useState<Alert | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [note, setNote] = useState(""),
    [notice, setNotice] = useState("");
  const alerts = (resource.data?.alerts ?? []).filter((a) =>
    `${a.title} ${a.summary} ${a.subject}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  async function update(action: string) {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      await request(
        `/alerts/${selected.id}/${action}`,
        "POST",
        action === "resolve"
          ? { resolution_note: note }
          : action === "acknowledge"
            ? { note }
            : undefined,
      );
      setSelected(null);
      setNotice(
        `Alert ${action === "resolve" ? "resolved" : action === "reopen" ? "reopened" : "acknowledged"}.`,
      );
      resource.reload();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="TRUST & SECURITY"
        title="Alert inbox"
        description="Spot integrity issues, investigate the evidence, and close the loop."
      >
        <button className="p-btn" onClick={resource.reload}>
          <RefreshCw size={15} />
          Refresh
        </button>
      </PageTitle>
      <div className="p-tabs">
        {["open", "acknowledged", "resolved", "all"].map((s) => (
          <button
            key={s}
            className={status === s ? "active" : ""}
            onClick={() => {
              setStatus(s);
              setCursor(null);
            }}
          >
            {s.charAt(0).toUpperCase() + s.slice(1)}
            {s === "open" ? ` (${resource.data?.totalOpen ?? 0})` : ""}
          </button>
        ))}
      </div>
      <ErrorBox error={resource.error} retry={resource.reload} />
      {notice && (
        <div className="p-notice" role="status">
          {notice}
        </div>
      )}
      <Panel>
        <div className="p-toolbar">
          <SearchField
            value={query}
            onChange={setQuery}
            placeholder="Search this page of alerts…"
          />
          <select
            aria-label="Severity"
            className="p-select"
            value={severity}
            onChange={(e) => {
              setSeverity(e.target.value);
              setCursor(null);
            }}
          >
            <option value="all">All severities</option>
            <option value="critical">Critical</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
          </select>
        </div>
        {resource.loading ? (
          <Loading />
        ) : alerts.length ? (
          alerts.map((a) => (
            <div className="p-row" key={a.id}>
              <div className="p-identity">
                <span className="p-avatar">
                  <Bell size={17} />
                </span>
                <div>
                  <button
                    className="alert-title"
                    onClick={() => {
                      setSelected(a);
                      setError("");
                      setNote("");
                    }}
                  >
                    {a.title}
                  </button>
                  <p>{a.summary}</p>
                  <p>
                    {date(a.lastSeenAt)} · {a.occurrenceCount} occurrence
                    {a.occurrenceCount === 1 ? "" : "s"} · Project #
                    {a.projectId}
                  </p>
                </div>
              </div>
              <div className="p-actions">
                <Status value={a.severity} />
                <Status value={a.status} />
              </div>
            </div>
          ))
        ) : (
          <Empty
            title={query ? "No matching alerts" : "You’re all caught up"}
            description={`No ${status === "all" ? "" : status + " "}alerts to show for this organization. New findings will appear here automatically.`}
          />
        )}
        <div className="p-table-foot">
          <span>Organization-wide findings · Updates every 30 seconds</span>
          <div className="p-actions">
            {cursor && (
              <button className="p-btn small" onClick={() => setCursor(null)}>
                Newest
              </button>
            )}
            <button
              className="p-btn small"
              disabled={!resource.data?.nextCursor}
              onClick={() => setCursor(resource.data!.nextCursor)}
            >
              Older alerts
            </button>
          </div>
        </div>
      </Panel>
      {selected && (
        <Modal
          title={selected.title}
          description={selected.summary}
          close={() => {
            if (!busy) setSelected(null);
          }}
        >
          <div className="p-actions" style={{ margin: "20px 0" }}>
            <Status value={selected.severity} />
            <Status value={selected.status} />
          </div>
          <div className="p-code">
            Subject: {selected.subject}
            <br />
            First seen: {date(selected.firstSeenAt)}
            <br />
            Last seen: {date(selected.lastSeenAt)}
          </div>
          {selected.evidence != null && (
            <details style={{ marginTop: 18 }}>
              <summary>Inspect evidence</summary>
              <pre className="p-code">
                {JSON.stringify(selected.evidence, null, 2)}
              </pre>
            </details>
          )}
          {admin ? (
            <>
              <Field label="Investigation note">
                <textarea
                  className="p-textarea"
                  maxLength={2000}
                  placeholder="Record what you found…"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
              </Field>
              <ErrorBox error={error} />
              <div className="p-modal-footer">
                {selected.status === "open" && (
                  <button
                    className="p-btn"
                    disabled={busy}
                    onClick={() => void update("acknowledge")}
                  >
                    Acknowledge
                  </button>
                )}
                <button
                  className="p-btn primary"
                  disabled={busy}
                  onClick={() =>
                    void update(
                      selected.status === "resolved" ? "reopen" : "resolve",
                    )
                  }
                >
                  <CheckCheck size={15} />
                  {busy
                    ? "Saving…"
                    : selected.status === "resolved"
                      ? "Reopen alert"
                      : "Resolve alert"}
                </button>
              </div>
            </>
          ) : (
            <p className="p-muted" style={{ marginTop: 20 }}>
              An administrator can acknowledge or resolve this alert.
            </p>
          )}
        </Modal>
      )}
    </>
  );
}
