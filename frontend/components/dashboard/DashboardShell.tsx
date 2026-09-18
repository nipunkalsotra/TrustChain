"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  Bell,
  Blocks,
  ChevronDown,
  CircleHelp,
  Fingerprint,
  FlaskConical,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  Search,
  Settings2,
  ShieldCheck,
  Sun,
  Users,
} from "lucide-react";
import { ThemeProvider, useTheme } from "@/lib/theme";
import { SessionProvider, useSession } from "@/lib/session";
import { clearSession } from "@/lib/auth";
import { logout, resendVerification } from "@/lib/api";
import { ErrorBox, Loading, message, Modal } from "@/components/product/ui";
import "@/components/product/theme.css";
import "./dashboard.css";
const navigation = [
  {
    title: "WORKSPACE",
    items: [
      ["Overview", "/dashboard", LayoutDashboard],
      ["Agent runs", "/dashboard/runs", Activity],
      ["Agent registry", "/dashboard/agents", Fingerprint],
      ["Trust scores", "/dashboard/trust-scores", Activity],
    ],
  },
  {
    title: "TRUST & SECURITY",
    items: [
      ["Audit trail", "/dashboard/audit", Blocks],
      ["Verification", "/dashboard/proofs", ShieldCheck],
      ["Anchors", "/dashboard/anchors", FlaskConical],
      ["Alerts", "/dashboard/alerts", Bell],
    ],
  },
  {
    title: "MANAGE",
    items: [
      ["Team members", "/dashboard/team", Users],
      ["API keys", "/dashboard/keys", KeyRound],
      ["Settings", "/dashboard/settings", Settings2],
    ],
  },
] as const;
export function DashboardShell({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <SessionProvider>
        <Shell>{children}</Shell>
      </SessionProvider>
    </ThemeProvider>
  );
}
function Shell({ children }: { children: React.ReactNode }) {
  const { me, loading, error, refresh, switchProject } = useSession();
  const { theme, toggle } = useTheme();
  const pathname = usePathname(),
    router = useRouter();
  const [open, setOpen] = useState(false),
    [search, setSearch] = useState(false),
    [query, setQuery] = useState("");
  const [actionError, setActionError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!loading && !me && !error)
      router.replace(`/auth?next=${encodeURIComponent(pathname)}`);
  }, [me, loading, error, pathname, router]);
  useEffect(() => {
    function key(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setSearch((v) => !v);
      }
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  if (loading)
    return (
      <div className="p-session-screen">
        <Loading />
      </div>
    );
  if (error)
    return (
      <div className="p-session-screen">
        <div>
          <ShieldCheck size={35} />
          <h1>Let’s reconnect.</h1>
          <p>Your workspace will be here when the connection is restored.</p>
          <ErrorBox error={error} retry={() => void refresh()} />
          <Link href="/" className="p-text-link">
            Back to TrustChain
          </Link>
        </div>
      </div>
    );
  if (!me) return <Loading />;
  const org = me.memberships.find((m) => m.org.id === me.active.orgId);
  const project = org?.projects.find((p) => p.id === me.active.projectId);
  const current =
    navigation
      .flatMap((g) => [...g.items])
      .find((i) => i[1] === pathname)?.[0] ?? "Run details";
  const links = navigation
    .flatMap((g) => [...g.items])
    .filter((i) => i[0].toLowerCase().includes(query.toLowerCase()));
  async function signOut() {
    setBusy(true);
    setActionError("");
    try {
      await logout();
      clearSession();
      router.replace("/auth");
    } catch (e) {
      setActionError(message(e));
      setBusy(false);
    }
  }
  return (
    <div className="d-shell">
      <a className="d-skip" href="#workspace">
        Skip to content
      </a>
      {open && (
        <button
          className="d-scrim"
          aria-label="Close navigation"
          onClick={() => setOpen(false)}
        />
      )}
      <aside
        className={`d-sidebar ${open ? "open" : ""}`}
        aria-label="Workspace navigation"
      >
        <Link href="/" className="d-brand">
          <span className="d-brand-icon">
            <ShieldCheck size={23} />
          </span>
          TrustChain
          <span className="d-brand-dot" />
        </Link>
        <div className="d-project">
          <span className="d-project-icon">
            {org?.org.name.charAt(0) ?? "T"}
          </span>
          <div>
            <small>{org?.org.name}</small>
            <label>
              <span className="p-sr">Active project</span>
              <select
                aria-label="Active project"
                disabled={busy}
                value={me.active.projectId}
                onChange={async (e) => {
                  setBusy(true);
                  try {
                    await switchProject(Number(e.target.value));
                  } catch (err) {
                    setActionError(message(err));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {me.memberships.map((m) => (
                  <optgroup key={m.org.id} label={m.org.name}>
                    {m.projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
          </div>
          <ChevronDown size={14} />
        </div>
        <nav>
          {navigation.map((group) => (
            <div className="d-nav-group" key={group.title}>
              <div className="d-nav-label">{group.title}</div>
              {group.items.map(([label, href, Icon]) => {
                const active =
                  href === "/dashboard"
                    ? pathname === href
                    : pathname.startsWith(href);
                return (
                  <Link
                    key={href}
                    href={href}
                    className={`d-nav-item ${active ? "active" : ""}`}
                    aria-current={active ? "page" : undefined}
                    onClick={() => setOpen(false)}
                  >
                    <Icon size={17} strokeWidth={1.65} />
                    <span>{label}</span>
                    {active && <span className="d-nav-active" />}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="d-sidebar-bottom">
          <Link className="d-connect" href="/dashboard/keys">
            <span className="d-connect-icon">
              <Blocks size={18} />
            </span>
            <strong>Connect your agents</strong>
            <p>Bring verifiable trust to your stack.</p>
            <span>
              Set up an integration <ArrowUpRight size={14} />
            </span>
          </Link>
          <Link className="d-help" href="/dashboard/help">
            <CircleHelp size={16} /> Getting started <ArrowUpRight size={13} />
          </Link>
          <div className="d-user">
            <span className="d-user-avatar">
              {me.user.name.slice(0, 2).toUpperCase()}
            </span>
            <div>
              <strong>{me.user.name}</strong>
              <small>{me.active.role}</small>
            </div>
            <button
              aria-label="Sign out"
              className="d-logout"
              onClick={signOut}
              disabled={busy}
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>
      <div className="d-main">
        <header className="d-topbar">
          <div className="p-actions">
            <button
              aria-label="Open navigation"
              aria-expanded={open}
              className="p-icon-btn d-menu"
              onClick={() => setOpen(true)}
            >
              <Menu size={20} />
            </button>
            <span className="d-breadcrumb">
              Workspace <span>/</span> <b>{current}</b>
            </span>
          </div>
          <div className="p-actions">
            <button className="d-command" onClick={() => setSearch(true)}>
              <Search size={15} />
              <span>Find a page…</span>
              <kbd>⌘ K</kbd>
            </button>
            <span className="d-env">
              <i />
              {project?.environment === "test"
                ? "Test environment"
                : "Live workspace"}
            </span>
            <button
              className="p-icon-btn"
              onClick={toggle}
              aria-label={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
            >
              {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
            </button>
            <Link
              className="p-icon-btn"
              href="/dashboard/alerts"
              aria-label="View alerts"
            >
              <Bell size={17} />
            </Link>
            <span className="p-avatar d-top-avatar">
              {me.user.name.charAt(0).toUpperCase()}
            </span>
          </div>
        </header>
        <main className="d-content" id="workspace" key={me.active.projectId}>
          <ErrorBox error={actionError} />
          {!me.user.emailVerified && (
            <div className="d-verification">
              <span>
                <strong>Verify your email</strong> to invite your team and
                create API keys.
              </span>
              <button
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await resendVerification();
                    setNotice("Verification email sent. Check your inbox.");
                  } catch (e) {
                    setActionError(message(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {notice || "Resend email"} <ArrowUpRight size={13} />
              </button>
              <button
                onClick={() => void refresh()}
                aria-label="Check email verification"
              >
                Check status
              </button>
            </div>
          )}
          {children}
        </main>
        <footer className="d-footer">
          <span>
            TrustChain <span> / </span> Verifiable by design.
          </span>
          <span>
            <ShieldCheck size={12} /> Your workspace. Your evidence.
          </span>
        </footer>
      </div>
      {search && (
        <Modal
          title="Where would you like to go?"
          close={() => setSearch(false)}
        >
          <label className="p-field">
            <span>Search workspace pages</span>
            <input
              autoFocus
              className="p-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Runs, agents, settings…"
            />
          </label>
          <div className="d-search-results">
            {links.map(([label, href, Icon]) => (
              <Link
                href={href}
                key={href}
                onClick={() => {
                  setSearch(false);
                  setQuery("");
                }}
              >
                <Icon size={17} />
                {label}
                <ArrowUpRight size={14} />
              </Link>
            ))}
            {links.length === 0 && (
              <p className="p-muted">No matching pages.</p>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
