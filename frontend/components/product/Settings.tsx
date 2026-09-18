"use client";
import { useCallback, useState } from "react";
import Link from "next/link";
import { FolderPlus, Moon, Plus, ShieldCheck } from "lucide-react";
import { request } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useTheme } from "@/lib/theme";
import type { NotificationPreferences, Project } from "@/lib/product-types";
import {
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
export default function Settings() {
  const { me } = useSession();
  const [tab, setTab] = useState("workspace");
  return (
    <>
      <PageTitle
        title="Settings"
        description="Make this workspace yours. Manage your organization and preferences."
      />
      <div className="p-tabs">
        {[
          ["workspace", "Workspace"],
          ["profile", "My account"],
          ["notifications", "Notifications"],
        ].map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? "active" : ""}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "workspace" ? (
        <Workspace />
      ) : tab === "profile" ? (
        <Profile />
      ) : (
        <Notifications key={me!.active.orgId} />
      )}
    </>
  );
}
function Workspace() {
  const { me, refresh, switchProject } = useSession(),
    org = me!.memberships.find((m) => m.org.id === me!.active.orgId)!,
    admin = ["owner", "admin"].includes(me!.active.role);
  const [name, setName] = useState(org.org.name),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [creating, setCreating] = useState<"project" | "org" | null>(null),
    [newName, setNewName] = useState(""),
    [environment, setEnvironment] = useState("test"),
    [editing, setEditing] = useState<Project | null>(null);
  async function perform(fn: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError("");
    try {
      await fn();
      setNotice(success);
      return true;
    } catch (e) {
      setError(message(e));
      return false;
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="p-stack">
      <ErrorBox error={!creating && !editing ? error : ""} />
      {notice && (
        <div className="p-notice" role="status">
          {notice}
        </div>
      )}
      <Panel
        title="Organization"
        description="Your team’s shared home in TrustChain."
      >
        <form
          className="p-pad"
          onSubmit={async (e) => {
            e.preventDefault();
            await perform(async () => {
              await request(`/orgs/${org.org.id}`, "PATCH", {
                name: name.trim(),
              });
              await refresh();
            }, "Organization updated.");
          }}
        >
          <Field label="Organization name">
            <input
              className="p-input"
              required
              maxLength={200}
              disabled={!admin}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <div className="p-actions">
            <Status value={org.org.plan} />
            <span className="p-muted" style={{ fontSize: 11 }}>
              Organization #{org.org.id}
            </span>
            {admin && (
              <button
                className="p-btn primary"
                style={{ marginLeft: "auto" }}
                disabled={busy || !name.trim() || name === org.org.name}
              >
                {busy ? "Saving…" : "Save changes"}
              </button>
            )}
          </div>
        </form>
      </Panel>
      <Panel
        title="Projects"
        description="Separate agent activity and API keys by project."
        action={
          admin && (
            <button
              className="p-btn small"
              onClick={() => {
                setCreating("project");
                setNewName("");
                setError("");
              }}
            >
              <FolderPlus size={14} />
              New project
            </button>
          )
        }
      >
        {org.projects.map((p) => (
          <div className="p-row" key={p.id}>
            <div>
              <h3>{p.name}</h3>
              <p>
                Project #{p.id} · {p.environment}
              </p>
            </div>
            <div className="p-actions">
              {p.id === me!.active.projectId ? (
                <Status value="active" />
              ) : (
                <button
                  className="p-btn small"
                  disabled={busy}
                  onClick={() =>
                    void perform(() => switchProject(p.id), "Project switched.")
                  }
                >
                  Open project
                </button>
              )}
              {admin && (
                <button
                  className="p-btn small"
                  onClick={() => {
                    setEditing(p);
                    setNewName(p.name);
                    setError("");
                  }}
                >
                  Rename
                </button>
              )}
            </div>
          </div>
        ))}
      </Panel>
      <Panel
        title="Another team, another workspace"
        description="Create a separate organization with its own members and projects."
      >
        <div className="p-pad">
          <button
            className="p-btn"
            onClick={() => {
              setCreating("org");
              setNewName("");
              setError("");
            }}
          >
            <Plus size={15} />
            Create organization
          </button>
        </div>
      </Panel>
      {(creating || editing) && (
        <Modal
          title={
            editing
              ? "Rename project"
              : creating === "org"
                ? "Create organization"
                : "Create project"
          }
          description="Give it a clear name your team will recognize."
          close={() => {
            if (!busy) {
              setCreating(null);
              setEditing(null);
            }
          }}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const ok = await perform(
                async () => {
                  if (editing)
                    await request(`/projects/${editing.id}`, "PATCH", {
                      name: newName.trim(),
                    });
                  else if (creating === "org")
                    await request("/orgs", "POST", {
                      name: newName.trim(),
                      project_name: "Default",
                    });
                  else
                    await request(`/orgs/${org.org.id}/projects`, "POST", {
                      name: newName.trim(),
                      environment,
                    });
                  setCreating(null);
                  setEditing(null);
                  await refresh();
                },
                editing
                  ? "Project renamed."
                  : "Workspace created. Select it in the project switcher.",
              );
              if (ok) setNewName("");
            }}
          >
            <Field label="Name">
              <input
                className="p-input"
                required
                maxLength={200}
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
              />
            </Field>
            {creating === "project" && (
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
            )}
            <ErrorBox error={error} />
            <div className="p-modal-footer">
              <button
                type="button"
                className="p-btn"
                disabled={busy}
                onClick={() => {
                  setCreating(null);
                  setEditing(null);
                }}
              >
                Cancel
              </button>
              <button
                className="p-btn primary"
                disabled={busy || !newName.trim()}
              >
                {busy ? "Saving…" : editing ? "Save name" : "Create"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
function Profile() {
  const { me } = useSession(),
    { theme, toggle } = useTheme();
  return (
    <div className="p-stack">
      <Panel
        title="Your account"
        description="Your identity and access in this workspace."
      >
        <div className="p-pad">
          <div className="p-identity">
            <span
              className="p-avatar"
              style={{ width: 55, height: 55, fontSize: 19 }}
            >
              {me!.user.name.slice(0, 2).toUpperCase()}
            </span>
            <div>
              <h2>{me!.user.name}</h2>
              <p className="p-muted" style={{ fontSize: 12, marginTop: 5 }}>
                {me!.user.email}
              </p>
            </div>
          </div>
        </div>
        <div className="p-row">
          <span>Email status</span>
          <Status value={me!.user.emailVerified ? "verified" : "pending"} />
        </div>
        <div className="p-row">
          <span>Organization role</span>
          <Status value={me!.active.role} />
        </div>
      </Panel>
      <Panel title="Appearance">
        <div className="p-row">
          <div className="p-identity">
            <Moon size={19} />
            <div>
              <h3>Dark mode</h3>
              <p>Saved on this browser across visits.</p>
            </div>
          </div>
          <button
            className="p-switch"
            role="switch"
            aria-label="Dark mode"
            aria-checked={theme === "dark"}
            onClick={toggle}
          >
            <i />
          </button>
        </div>
      </Panel>
      <Panel title="Account security">
        <div className="p-row">
          <div className="p-identity">
            <ShieldCheck size={20} />
            <div>
              <h3>Password</h3>
              <p>Request a secure link to change your password.</p>
            </div>
          </div>
          <Link className="p-btn" href="/auth/forgot-password">
            Reset password
          </Link>
        </div>
      </Panel>
    </div>
  );
}
function Notifications() {
  const { me } = useSession(),
    orgId = me!.active.orgId;
  const resource = useResource(
    useCallback(
      () =>
        request<NotificationPreferences>(
          `/me/notification-preferences?org_id=${orgId}`,
        ),
      [orgId],
    ),
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [saved, setSaved] = useState(false);
  const rows = [
    {
      key: "emailCritical",
      label: "Critical findings",
      text: "Immediate email for critical integrity issues.",
    },
    {
      key: "emailWarning",
      label: "Warnings",
      text: "Be notified about findings that need investigation.",
    },
    {
      key: "emailInfo",
      label: "Informational alerts",
      text: "Keep up with lower-priority workspace events.",
    },
    {
      key: "emailDigestOnly",
      label: "Digest mode",
      text: "Group non-critical findings into a periodic digest.",
    },
  ] as const;
  async function toggle(key: (typeof rows)[number]["key"]) {
    if (!resource.data) return;
    const next = { ...resource.data, [key]: !resource.data[key] };
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await request("/me/notification-preferences", "PUT", {
        org_id: orgId,
        email_critical: next.emailCritical,
        email_warning: next.emailWarning,
        email_info: next.emailInfo,
        email_digest_only: next.emailDigestOnly,
      });
      resource.reload();
      setSaved(true);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <ErrorBox error={resource.error || error} retry={resource.reload} />
      {saved && (
        <div className="p-notice" role="status">
          Preferences saved.
        </div>
      )}
      <Panel
        title="Email notifications"
        description="These preferences apply to your account in this organization."
      >
        {resource.loading ? (
          <Loading />
        ) : (
          rows.map((r) => (
            <div className="p-row" key={r.key}>
              <div>
                <h3>{r.label}</h3>
                <p>{r.text}</p>
              </div>
              <button
                className="p-switch"
                role="switch"
                aria-label={r.label}
                aria-checked={resource.data?.[r.key] ?? false}
                disabled={busy || !resource.data}
                onClick={() => void toggle(r.key)}
              >
                <i />
              </button>
            </div>
          ))
        )}
      </Panel>
    </>
  );
}
