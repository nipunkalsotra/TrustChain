"use client";
import { useCallback } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  Fingerprint,
  KeyRound,
  Layers,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { getRuns, request } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Agent, Integrity } from "@/lib/product-types";
import {
  Badge,
  date,
  Empty,
  ErrorBox,
  Loading,
  PageTitle,
  Panel,
  TextLink,
  useResource,
} from "./ui";
import { NewRunButton, RunsTable } from "./Runs";
export default function Overview() {
  const { me } = useSession();
  const runs = useResource(
    useCallback(() => getRuns(200), []),
    30000,
  );
  const agents = useResource(
    useCallback(() => request<{ agents: Agent[] }>("/agents"), []),
    30000,
  );
  const integrity = useResource(
    useCallback(() => request<Integrity>("/integrity/status"), []),
    30000,
  );
  const data = runs.data?.runs ?? [],
    health = integrity.data;
  const complete = data.filter((r) => r.status === "complete").length;
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - 6 + i);
    const matching = data.filter(
      (r) =>
        r.createdAt &&
        new Date(r.createdAt * 1000).toDateString() === d.toDateString(),
    );
    return {
      label: d.toLocaleDateString(undefined, { weekday: "short" }),
      completed: matching.filter((r) => r.status === "complete").length,
      other: matching.filter((r) => r.status !== "complete").length,
    };
  });
  const maximum = Math.max(
    1,
    ...days.map((d) => Math.max(d.completed, d.other)),
  );
  return (
    <>
      <PageTitle
        eyebrow="YOUR COMMAND CENTER"
        title={`Welcome back, ${me?.user.name.split(" ")[0] ?? "there"}.`}
        description="A clear view of your agents. Confidence in every action."
      >
        <NewRunButton />
      </PageTitle>
      <div className="o-banner">
        <ShieldCheck size={180} strokeWidth={0.5} className="o-banner-icon" />
        <div>
          <div
            className="p-eyebrow"
            style={{ color: "#94a8de", marginBottom: 10 }}
          >
            OBSERVE. VERIFY. TRUST.
          </div>
          <h2>Your AI, with a trail you can trust.</h2>
          <p>
            Track every agent action and independently verify the evidence. Your
            workspace brings it all together.
          </p>
        </div>
        <Link className="p-btn" href="/dashboard/proofs">
          Explore verification <ArrowUpRight size={14} />
        </Link>
      </div>
      <ErrorBox
        error={runs.error || agents.error || integrity.error}
        retry={() => {
          runs.reload();
          agents.reload();
          integrity.reload();
        }}
      />
      <div className="p-stats">
        {[
          {
            label: "Total runs",
            value: runs.data ? data.length.toLocaleString() : "—",
            icon: Activity,
            note: `${data.filter((r) => r.status === "running").length} currently running`,
          },
          {
            label: "Registered agents",
            value: agents.data?.agents.length.toLocaleString() ?? "—",
            icon: Fingerprint,
            note: "Active in this project",
          },
          {
            label: "Recorded steps",
            value: health?.stepsVerified.toLocaleString() ?? "—",
            icon: Layers,
            note: "Durably recorded audit entries",
          },
          {
            label: "Confirmed batches",
            value: health
              ? `${health.batchesRootConfirmed} / ${health.batchesVerified}`
              : "—",
            icon: ShieldCheck,
            note: "Roots confirmed by the watchdog",
          },
        ].map((stat) => (
          <div className="p-stat" key={stat.label}>
            <div className="p-stat-top">
              {stat.label}
              <span className="p-stat-icon">
                <stat.icon size={15} />
              </span>
            </div>
            <div className="p-stat-value">{stat.value}</div>
            <div className="p-stat-foot">{stat.note}</div>
          </div>
        ))}
      </div>
      <div className="p-grid">
        <Panel
          title="Run activity"
          description="Workflow volume over the last 7 days"
          action={
            <span className="o-period">
              <CalendarDays size={13} />
              Last 7 days
            </span>
          }
        >
          <div
            className="o-chart"
            role="img"
            aria-label={`Run activity this week: ${days.map((d) => `${d.label}, ${d.completed} completed and ${d.other} other`).join("; ")}`}
          >
            <div className="o-bars">
              {days.map((day, i) => (
                <div
                  className="o-bar-group"
                  key={i}
                  title={`${day.label}: ${day.completed} completed, ${day.other} other`}
                >
                  <div
                    className="o-bar"
                    style={{ height: `${(day.completed / maximum) * 90}%` }}
                  />
                  <div
                    className="o-bar secondary"
                    style={{ height: `${(day.other / maximum) * 90}%` }}
                  />
                </div>
              ))}
            </div>
            <div className="o-chart-labels">
              {days.map((d, i) => (
                <span key={i}>{d.label}</span>
              ))}
            </div>
          </div>
          <div className="o-chart-legend">
            <span>
              <i />
              Completed
            </span>
            <span>
              <i />
              Running / failed
            </span>
            <span style={{ marginLeft: "auto" }}>
              {complete} completed in latest {data.length} runs
            </span>
          </div>
        </Panel>
        <Panel
          title="Integrity overview"
          action={
            <Badge tone={health?.openCritical ? "red" : ""}>
              {health ? "Monitoring" : "Connecting"}
            </Badge>
          }
        >
          <div className="o-health">
            <div className="o-health-ring">
              <ShieldCheck size={29} strokeWidth={1.4} />
            </div>
            <div>
              <h3>
                {!health
                  ? "Checking your workspace"
                  : !health.batchesVerified
                    ? "Ready for your first evidence"
                    : health.openCritical
                      ? "Attention required"
                      : "Evidence under observation"}
              </h3>
              <p>
                {health?.lastSweepAt
                  ? `Last sweep ${date(health.lastSweepAt)}`
                  : "No watchdog sweep recorded yet."}
              </p>
            </div>
          </div>
          <div className="o-health-lines">
            <div className="o-health-line">
              <span>Batch root coverage</span>
              <b>
                {health?.batchesVerified
                  ? `${health.coveragePercent}%`
                  : "No batches yet"}
              </b>
            </div>
            <div className="o-health-line">
              <span>Organization alerts</span>
              <b>
                {health
                  ? `${health.openCritical} critical · ${health.openWarning} warning`
                  : "—"}
              </b>
            </div>
            <div className="o-health-line">
              <span>Evidence explorer</span>
              <TextLink href="/dashboard/anchors">View anchors</TextLink>
            </div>
          </div>
        </Panel>
      </div>
      <div style={{ marginTop: 23 }}>
        <Panel
          title="Recent runs"
          description="Your latest workflows and their outcomes"
          action={<TextLink href="/dashboard/runs">View all runs</TextLink>}
        >
          {runs.loading ? (
            <Loading />
          ) : data.length ? (
            <RunsTable runs={data.slice(0, 5)} />
          ) : (
            <Empty
              title="A clean slate. A verifiable future."
              description="Start your first run to see agent activity and evidence in this workspace."
            >
              <NewRunButton />
            </Empty>
          )}
        </Panel>
      </div>
      <div className="o-quick">
        {[
          {
            title: "Register an agent",
            description: "Give your agent a verifiable identity",
            href: "/dashboard/agents",
            icon: Fingerprint,
          },
          {
            title: "Connect your stack",
            description: "Create scoped keys for your runtime",
            href: "/dashboard/keys",
            icon: KeyRound,
          },
          {
            title: "Find your way around",
            description: "A quick guide to your workspace",
            href: "/dashboard/help",
            icon: Sparkles,
          },
        ].map((item) => (
          <Link href={item.href} key={item.href}>
            <item.icon size={20} strokeWidth={1.5} />
            <div>
              <h3>{item.title}</h3>
              <p>{item.description}</p>
            </div>
            <ArrowRight size={15} />
          </Link>
        ))}
      </div>
    </>
  );
}
