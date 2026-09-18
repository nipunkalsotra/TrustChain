"use client";
import { useCallback, useState } from "react";
import { KeyRound, Plus, Trash2 } from "lucide-react";
import { request } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { ApiKey } from "@/lib/product-types";
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
  Status,
  useResource,
} from "./ui";
const scopes = [
  "agents:register",
  "agents:read",
  "logs:write",
  "runs:read",
  "runs:write",
  "alerts:read",
];
export default function Keys() {
  const { me } = useSession();
  if (
    !me?.user.emailVerified &&
    ["owner", "admin"].includes(me?.active.role ?? "")
  ) {
    return (
      <>
        <PageTitle
          title="API keys"
          description="Give your integrations exactly the access they need."
        />
        <Panel>
          <Empty
            title="Verify your email to manage keys"
            description="Use the verification link in your inbox, or resend it from the banner above. Once verified, check your email status to unlock API keys."
          />
        </Panel>
      </>
    );
  }
  return ["owner", "admin"].includes(me?.active.role ?? "") ? (
    <KeyManager />
  ) : (
    <>
      <PageTitle
        title="API keys"
        description="Secure connections between your runtime and TrustChain."
      />
      <Panel>
        <Empty
          title="Administrator access required"
          description="Ask your organization owner or administrator to create and manage scoped API keys."
        />
      </Panel>
    </>
  );
}
function KeyManager() {
  const { me } = useSession();
  const resource = useResource(
    useCallback(() => request<{ keys: ApiKey[] }>("/api-keys"), []),
  );
  const [open, setOpen] = useState(false),
    [selected, setSelected] = useState<string[]>(["agents:read", "runs:read"]),
    [environment, setEnvironment] = useState("test"),
    [raw, setRaw] = useState(""),
    [revoke, setRevoke] = useState<ApiKey | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <>
      <PageTitle
        title="API keys"
        description="Give your integrations exactly the access they need."
      >
        <button
          className="p-btn primary"
          disabled={!me?.user.emailVerified}
          title={
            !me?.user.emailVerified ? "Verify your email first" : undefined
          }
          onClick={() => {
            setOpen(true);
            setError("");
          }}
        >
          <Plus size={15} />
          Create API key
        </button>
      </PageTitle>
      <div className="p-notice">
        API keys belong to the active project. Keep them in your runtime’s
        secret store.
      </div>
      <ErrorBox error={resource.error} retry={resource.reload} />
      <Panel
        title="Project keys"
        description="The full key is shown only once, at creation."
      >
        {resource.loading ? (
          <Loading />
        ) : resource.data?.keys.length ? (
          <div className="p-table-wrap">
            <table className="p-table">
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Scopes</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Last used</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {resource.data.keys.map((k) => (
                  <tr key={k.id}>
                    <td className="p-mono">••••••••{k.lastFour}</td>
                    <td
                      style={{
                        maxWidth: 280,
                        whiteSpace: "normal",
                        fontSize: 10,
                      }}
                    >
                      {k.scopes.join(" · ")}
                    </td>
                    <td>
                      <Status value={k.revokedAt ? "revoked" : "active"} />
                    </td>
                    <td>{date(k.createdAt)}</td>
                    <td>{date(k.lastUsedAt)}</td>
                    <td>
                      {!k.revokedAt && (
                        <button
                          className="p-btn small danger"
                          onClick={() => {
                            setRevoke(k);
                            setError("");
                          }}
                        >
                          <Trash2 size={13} />
                          Revoke
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="Connect your first integration"
            description="Create a scoped API key to register agents and send their activity to TrustChain."
          />
        )}
      </Panel>
      <Panel
        title="Quick connection check"
        className="evidence-result"
        description="Run this from your server, using your API host and a key with agents:read scope."
      >
        <div className="p-pad">
          <pre className="p-code">
            {
              'curl "$TRUSTCHAIN_API_URL/v1/agents" \\\n  -H "Authorization: Bearer $TRUSTCHAIN_API_KEY"'
            }
          </pre>
        </div>
      </Panel>
      {open && (
        <Modal
          title="Create an API key"
          description="Choose the environment and the smallest set of scopes your integration needs."
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
                const key = await request<{ raw_key: string }>(
                  "/api-keys",
                  "POST",
                  { scopes: selected, environment },
                );
                setRaw(key.raw_key);
                setOpen(false);
                resource.reload();
              } catch (e) {
                setError(message(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Field label="Environment">
              <select
                className="p-select"
                value={environment}
                onChange={(e) => setEnvironment(e.target.value)}
              >
                <option value="test">Test</option>
                <option value="live">Live</option>
              </select>
            </Field>
            <fieldset style={{ border: 0 }}>
              <legend style={{ fontSize: 12, marginBottom: 7 }}>
                Allowed scopes
              </legend>
              {scopes.map((s) => (
                <label className="p-check" key={s}>
                  <input
                    type="checkbox"
                    checked={selected.includes(s)}
                    onChange={() =>
                      setSelected((previous) =>
                        previous.includes(s)
                          ? previous.filter((v) => v !== s)
                          : [...previous, s],
                      )
                    }
                  />
                  {s}
                </label>
              ))}
            </fieldset>
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
                disabled={busy || !selected.length}
              >
                <KeyRound size={14} />
                {busy ? "Creating…" : "Create key"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {raw && (
        <Modal
          title="Your key is ready"
          description="Copy and save this key now. You will not be able to view it again."
          close={() => setRaw("")}
        >
          <div className="p-code" style={{ margin: "22px 0" }}>
            {raw}
          </div>
          <CopyButton value={raw} />
          <div className="p-modal-footer">
            <button className="p-btn primary" onClick={() => setRaw("")}>
              I’ve saved my key
            </button>
          </div>
        </Modal>
      )}
      {revoke && (
        <Modal
          title="Revoke this key?"
          description={`Integrations using the key ending in ${revoke.lastFour} will immediately lose access. This cannot be undone.`}
          close={() => {
            if (!busy) setRevoke(null);
          }}
        >
          <ErrorBox error={error} />
          <div className="p-modal-footer">
            <button
              className="p-btn"
              disabled={busy}
              onClick={() => setRevoke(null)}
            >
              Keep key
            </button>
            <button
              className="p-btn danger"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await request(`/api-keys/${revoke.id}`, "DELETE");
                  setRevoke(null);
                  resource.reload();
                } catch (e) {
                  setError(message(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? "Revoking…" : "Revoke key"}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
