"use client";
import { useCallback, useState } from "react";
import { Mail, Plus, Trash2 } from "lucide-react";
import { request } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Invitation, Member, Role } from "@/lib/product-types";
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
const rank = { owner: 4, admin: 3, member: 2, viewer: 1 };
export default function Team() {
  const { me } = useSession(),
    orgId = me!.active.orgId,
    admin = ["owner", "admin"].includes(me!.active.role);
  const resource = useResource(
    useCallback(async () => {
      const members = await request<{ members: Member[] }>(
        `/orgs/${orgId}/members`,
      );
      const invitations =
        admin && me!.user.emailVerified
          ? await request<{ invitations: Invitation[] }>(
              `/orgs/${orgId}/invitations`,
            )
          : { invitations: [] };
      return { ...members, ...invitations };
    }, [orgId, admin, me]),
  );
  const [query, setQuery] = useState(""),
    [open, setOpen] = useState(false),
    [email, setEmail] = useState(""),
    [role, setRole] = useState("member"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [remove, setRemove] = useState<Member | null>(null);
  const members = (resource.data?.members ?? []).filter((m) =>
    `${m.name} ${m.email}`.toLowerCase().includes(query.toLowerCase()),
  );
  async function action(
    path: string,
    method: string,
    body?: unknown,
    success = "Changes saved.",
  ) {
    setBusy(true);
    setError("");
    try {
      await request(path, method, body);
      setNotice(success);
      resource.reload();
      return true;
    } catch (e) {
      setError(message(e));
      return false;
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        title="Team members"
        description="Bring your people together. Keep access intentional."
      >
        {admin && (
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
            Invite teammate
          </button>
        )}
      </PageTitle>
      <ErrorBox
        error={resource.error || (!open && !remove ? error : "")}
        retry={resource.reload}
      />
      {notice && (
        <div className="p-notice" role="status">
          {notice}
        </div>
      )}
      <Panel
        title="Organization members"
        description="Membership applies to all projects in this organization."
      >
        <div className="p-toolbar">
          <SearchField
            value={query}
            onChange={setQuery}
            placeholder="Search names or email addresses…"
          />
          <span className="p-muted" style={{ fontSize: 11 }}>
            {resource.data?.members.length ?? 0} members
          </span>
        </div>
        {resource.loading ? (
          <Loading />
        ) : members.length ? (
          <div className="p-table-wrap">
            <table className="p-table">
              <thead>
                <tr>
                  <th>Member</th>
                  <th>Role</th>
                  <th>Joined</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {members.map((m) => {
                  const canManage =
                    admin &&
                    m.userId !== me!.user.id &&
                    rank[m.role] < rank[me!.active.role];
                  return (
                    <tr key={m.userId}>
                      <td>
                        <div className="p-identity">
                          <span className="p-avatar">
                            {m.name.slice(0, 2).toUpperCase()}
                          </span>
                          <div>
                            {m.name}
                            {m.userId === me!.user.id ? " (you)" : ""}
                            <small>{m.email}</small>
                          </div>
                        </div>
                      </td>
                      <td>
                        {canManage ? (
                          <select
                            className="p-select"
                            aria-label={`Role for ${m.name}`}
                            disabled={busy}
                            value={m.role}
                            onChange={(e) =>
                              void action(
                                `/orgs/${orgId}/members/${m.userId}`,
                                "PATCH",
                                { role: e.target.value },
                              )
                            }
                          >
                            {(["viewer", "member", "admin"] as Role[])
                              .filter((r) => rank[r] < rank[me!.active.role])
                              .map((r) => (
                                <option key={r}>{r}</option>
                              ))}
                          </select>
                        ) : (
                          <Status value={m.role} />
                        )}
                      </td>
                      <td className="p-muted">{date(m.joinedAt)}</td>
                      <td>
                        {canManage && (
                          <button
                            className="p-icon-btn"
                            aria-label={`Remove ${m.name}`}
                            onClick={() => {
                              setRemove(m);
                              setError("");
                            }}
                          >
                            <Trash2 size={15} />
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="No matching teammates"
            description="Try another name or email address."
          />
        )}
      </Panel>
      {admin && (
        <Panel
          title="Invitations"
          description="Pending and previous invitations to your organization."
          className="evidence-result"
        >
          {resource.data?.invitations.length ? (
            resource.data.invitations.map((i) => (
              <div className="p-row" key={i.id}>
                <div className="p-identity">
                  <Mail size={18} className="p-muted" />
                  <div>
                    <h3>{i.email}</h3>
                    <p>
                      {i.role} · Expires {date(i.expiresAt)}
                    </p>
                  </div>
                </div>
                <div className="p-actions">
                  <Status value={i.status} />
                  {i.status === "pending" && (
                    <>
                      <button
                        disabled={busy}
                        className="p-btn small"
                        onClick={() =>
                          void action(
                            `/orgs/${orgId}/invitations/${i.id}/resend`,
                            "POST",
                            undefined,
                            "Invitation resent.",
                          )
                        }
                      >
                        Resend
                      </button>
                      <button
                        disabled={busy}
                        className="p-btn small danger"
                        onClick={() =>
                          void action(
                            `/orgs/${orgId}/invitations/${i.id}`,
                            "DELETE",
                            undefined,
                            "Invitation revoked.",
                          )
                        }
                      >
                        Revoke
                      </button>
                    </>
                  )}
                </div>
              </div>
            ))
          ) : (
            <Empty
              title="No invitations yet"
              description="Invite teammates by email and choose their access level."
            />
          )}
        </Panel>
      )}
      {open && (
        <Modal
          title="Invite a teammate"
          description="They’ll receive an email with a link to join this organization."
          close={() => {
            if (!busy) setOpen(false);
          }}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              if (
                await action(
                  `/orgs/${orgId}/invitations`,
                  "POST",
                  { email, role },
                  "Invitation sent.",
                )
              ) {
                setOpen(false);
                setEmail("");
              }
            }}
          >
            <Field label="Email address">
              <input
                className="p-input"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="teammate@company.com"
              />
            </Field>
            <Field
              label="Role"
              hint="Viewers can inspect evidence. Members can also run agents. Admins manage people and keys."
            >
              <select
                className="p-select"
                value={role}
                onChange={(e) => setRole(e.target.value)}
              >
                <option value="viewer">Viewer</option>
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </select>
            </Field>
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
              <button className="p-btn primary" disabled={busy}>
                {busy ? "Sending…" : "Send invitation"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {remove && (
        <Modal
          title={`Remove ${remove.name}?`}
          description="They will lose access to every project in this organization. Their recorded work will remain."
          close={() => {
            if (!busy) setRemove(null);
          }}
        >
          <ErrorBox error={error} />
          <div className="p-modal-footer">
            <button
              className="p-btn"
              disabled={busy}
              onClick={() => setRemove(null)}
            >
              Cancel
            </button>
            <button
              className="p-btn danger"
              disabled={busy}
              onClick={async () => {
                if (
                  await action(
                    `/orgs/${orgId}/members/${remove.userId}`,
                    "DELETE",
                    undefined,
                    "Member removed.",
                  )
                )
                  setRemove(null);
              }}
            >
              {busy ? "Removing…" : "Remove member"}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
