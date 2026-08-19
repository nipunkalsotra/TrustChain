#!/usr/bin/env node
/**
 * cli.ts — `trustchain` command-line tool, a thin wrapper around
 * TrustChainClient/TrustChain for scripting/manual use without writing
 * any code.
 *
 * Auth/target config: --api-key / --token / --base-url flags, or
 * TRUSTCHAIN_API_KEY / TRUSTCHAIN_TOKEN / TRUSTCHAIN_BASE_URL env vars
 * (flags win). Both --api-key and --token end up in the same
 * `Authorization: Bearer <value>` header (backend/auth.py's
 * get_current_principal/get_current_user both just read that one
 * header) — the distinction only matters for WHICH endpoints will
 * accept the credential: org/member/invitation management and alert
 * ack/resolve are human-JWT-only (no API key can reach them, by design
 * — see backend/main.py's Phase 3 section docstring), so those commands
 * need --token / TRUSTCHAIN_TOKEN. Read-mostly commands (alerts
 * list/get/summary, integrity, agents) accept either.
 *
 * No external CLI-parsing dependency for argument handling — the
 * command surface is small enough that hand-rolled parsing stays
 * simpler than a dependency would. js-yaml is used for
 * `agents verify-manifest`/`sync-manifest`'s trustchain.yaml, since
 * hand-rolling a YAML parser is not a reasonable simplification.
 */

import * as yaml from "js-yaml";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { TrustChainClient, DEFAULT_BASE_URL } from "./client.js";
import { TrustChain } from "./instrumentation.js";
import { TrustChainError } from "./errors.js";

interface ParsedArgs {
  command: string;
  positional: string[];
  apiKey?: string;
  token?: string;
  baseUrl?: string;
  limit?: number;
  runId?: string;
  role?: string;
  status?: string;
  severity?: string;
  note?: string;
  manifest?: string;
  reason?: string;
  projectName?: string;
}

function parseArgs(argv: string[]): ParsedArgs {
  const [command, ...rest] = argv;
  const positional: string[] = [];
  const parsed: ParsedArgs = { command: command ?? "", positional };

  for (let i = 0; i < rest.length; i++) {
    const arg = rest[i];
    if (arg === "--api-key") parsed.apiKey = rest[++i];
    else if (arg === "--token") parsed.token = rest[++i];
    else if (arg === "--base-url") parsed.baseUrl = rest[++i];
    else if (arg === "--limit") parsed.limit = Number(rest[++i]);
    else if (arg === "--run-id") parsed.runId = rest[++i];
    else if (arg === "--role") parsed.role = rest[++i];
    else if (arg === "--status") parsed.status = rest[++i];
    else if (arg === "--severity") parsed.severity = rest[++i];
    else if (arg === "--note") parsed.note = rest[++i];
    else if (arg === "--manifest") parsed.manifest = rest[++i];
    else if (arg === "--reason") parsed.reason = rest[++i];
    else if (arg === "--project-name") parsed.projectName = rest[++i];
    else positional.push(arg);
  }
  return parsed;
}

function resolveCredential(args: ParsedArgs): string {
  const cred = args.token ?? args.apiKey ?? process.env.TRUSTCHAIN_TOKEN ?? process.env.TRUSTCHAIN_API_KEY;
  if (!cred) {
    console.error("error: no credential — pass --api-key/--token or set TRUSTCHAIN_API_KEY/TRUSTCHAIN_TOKEN");
    process.exit(1);
  }
  return cred;
}

function resolveBaseUrl(args: ParsedArgs): string {
  return (args.baseUrl ?? process.env.TRUSTCHAIN_BASE_URL ?? DEFAULT_BASE_URL).replace(/\/$/, "");
}

function makeClient(args: ParsedArgs): TrustChainClient {
  return new TrustChainClient(resolveCredential(args), { baseUrl: resolveBaseUrl(args) });
}

function makeInstrumentation(args: ParsedArgs): TrustChain {
  return new TrustChain(resolveCredential(args), { baseUrl: resolveBaseUrl(args), onError: "raise" });
}

function printJson(value: unknown): void {
  console.log(JSON.stringify(value, null, 2));
}

/** Raw fetch, not TrustChainClient — org/member/invitation/alert-
 * mutation endpoints are human-session admin operations TrustChainClient
 * deliberately doesn't wrap (same reasoning the Python CLI's
 * `_authed_request` docstring gives: these aren't things a third-party
 * AGENT calls, they're things a PERSON managing their team calls). */
async function authedRequest(args: ParsedArgs, method: string, path: string, body?: unknown): Promise<any> {
  const response = await fetch(`${resolveBaseUrl(args)}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${resolveCredential(args)}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const text = await response.text();
    console.error(`error: ${response.status}: ${text}`);
    process.exit(1);
  }
  return response.status === 204 ? {} : response.json();
}

interface ManifestAgent {
  agentId: string;
  model: string;
  version: string;
  systemPrompt: string;
}

function loadManifest(path: string): ManifestAgent[] {
  const raw = yaml.load(readFileSync(path, "utf8")) as any;
  if (!raw || !Array.isArray(raw.agents)) {
    console.error(`error: ${path} has no top-level 'agents:' list`);
    process.exit(1);
  }
  return raw.agents.map((entry: any) => {
    let systemPrompt: string = entry.system_prompt ?? "";
    if (entry.system_prompt_file) {
      // Resolved relative to the manifest file's own directory, same as
      // the Python CLI's _agent_from_manifest_entry (relative to CWD,
      // which for a CI job is the checkout root the manifest itself
      // sits in) — using the manifest's directory here is slightly more
      // robust if this is ever invoked with `--manifest some/dir/trustchain.yaml`
      // from a different CWD.
      systemPrompt = readFileSync(join(dirname(path), entry.system_prompt_file), "utf8");
    }
    return { agentId: entry.id, model: entry.model, version: String(entry.version), systemPrompt };
  });
}

const USAGE = `trustchain <command> [options]

Pipeline:
  run <task>                 Start a run and stream its events to stdout until it finishes
  runs list                  Recent runs (--limit N, default 50)
  runs get <run-id>          Fetch one run's result
  stream <run-id>            Stream an already-started run's events
  scores <run-id>            Trust scores for a run
  scores-history <run-id>    Trust score history for a run
  leaderboard                Aggregated per-agent leaderboard (--limit N)
  audit-log [--run-id ID]    Anchored audit-log entries (optionally scoped to one run)

Agents:
  agents verify-manifest --manifest trustchain.yaml
                              Verify every agent in a manifest against its registered
                              on-chain identity (CI gate) — exits non-zero on drift
  agents sync-manifest --manifest trustchain.yaml [--reason "..."]
                              Register every agent in a manifest
  agents integrity <agent-id> Registered identity + recent events/alerts for one agent

Organizations & members (--token, a human session, required):
  org list                              Organizations you belong to
  org create <name> [--project-name N]  Create a new organization
  org rename <org-id> <name>            Rename an organization
  org members <org-id>                  List an organization's members
  member invite <org-id> <email> --role admin|member|viewer
  member list <org-id>
  member set-role <org-id> <user-id> --role admin|member|viewer
  member remove <org-id> <user-id>

Alerts (list/get/summary accept --api-key with the alerts:read scope OR --token;
ack/resolve require --token):
  alerts list [--status open|acknowledged|resolved] [--severity critical|warning|info] [--limit N]
  alerts get <alert-id>
  alerts summary
  alerts ack <alert-id>
  alerts resolve <alert-id> [--note "..."]

Integrity:
  integrity status            Coverage/health of the active project
  integrity verify-run <run-id>  Synchronously verify one run's steps against their anchored hashes

Options (any command):
  --api-key KEY             API key (or set TRUSTCHAIN_API_KEY)
  --token JWT               Human session token (or set TRUSTCHAIN_TOKEN) — required for org/member/alert-mutation commands
  --base-url URL            API base URL, default ${DEFAULT_BASE_URL} (or set TRUSTCHAIN_BASE_URL)
`;

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));

  if (!args.command || args.command === "help" || args.command === "--help") {
    console.log(USAGE);
    return;
  }

  try {
    switch (args.command) {
      case "run": {
        const task = args.positional.join(" ");
        if (!task) {
          console.error("error: usage: trustchain run <task>");
          process.exit(1);
        }
        const client = makeClient(args);
        const started = await client.runAgent(task);
        console.log(`started ${started.run_id}`);
        for await (const event of client.stream(started.run_id)) {
          printJson(event);
        }
        break;
      }
      case "stream": {
        const runId = args.positional[0];
        if (!runId) {
          console.error("error: usage: trustchain stream <run-id>");
          process.exit(1);
        }
        const client = makeClient(args);
        for await (const event of client.stream(runId)) {
          printJson(event);
        }
        break;
      }
      case "runs": {
        const sub = args.positional[0];
        const client = makeClient(args);
        if (sub === "list") {
          printJson(await client.listRuns(args.limit ?? 50));
        } else if (sub === "get") {
          const runId = args.positional[1];
          if (!runId) {
            console.error("error: usage: trustchain runs get <run-id>");
            process.exit(1);
          }
          printJson(await client.getRun(runId));
        } else {
          console.error("error: usage: trustchain runs list | trustchain runs get <run-id>");
          process.exit(1);
        }
        break;
      }
      case "scores": {
        const runId = args.positional[0];
        if (!runId) {
          console.error("error: usage: trustchain scores <run-id>");
          process.exit(1);
        }
        printJson(await makeClient(args).trustScores(runId));
        break;
      }
      case "scores-history": {
        const runId = args.positional[0];
        if (!runId) {
          console.error("error: usage: trustchain scores-history <run-id>");
          process.exit(1);
        }
        printJson(await makeClient(args).trustScoreHistory(runId));
        break;
      }
      case "leaderboard": {
        printJson(await makeClient(args).leaderboard(args.limit ?? 50));
        break;
      }
      case "audit-log": {
        printJson(await makeClient(args).auditLog(args.runId));
        break;
      }

      // ── Agents ──────────────────────────────────────────────────────
      case "agents": {
        const sub = args.positional[0];
        if (sub === "verify-manifest") {
          if (!args.manifest) {
            console.error("error: usage: trustchain agents verify-manifest --manifest trustchain.yaml");
            process.exit(1);
          }
          const agents = loadManifest(args.manifest);
          const tc = makeInstrumentation(args);
          let anyDrift = false;
          for (const agent of agents) {
            const result = await tc.verifyAgent(agent);
            if (!result) {
              console.log(`  ERROR   ${agent.agentId.padEnd(20)} verification failed`);
              anyDrift = true;
            } else if (result.hashMatches) {
              console.log(`  OK      ${agent.agentId.padEnd(20)} ${result.providedHash}`);
            } else {
              console.log(`  DRIFT   ${agent.agentId.padEnd(20)} registered=${result.storedHash}  local=${result.providedHash}`);
              anyDrift = true;
            }
          }
          console.log();
          if (anyDrift) {
            console.log("One or more agents differ from their registered on-chain identity.");
            console.log(`If this is intentional, run: trustchain agents sync-manifest --manifest ${args.manifest} --reason "..."`);
            process.exit(1);
          }
          console.log(`All ${agents.length} agent(s) match their registered identity.`);
        } else if (sub === "sync-manifest") {
          if (!args.manifest) {
            console.error("error: usage: trustchain agents sync-manifest --manifest trustchain.yaml [--reason ...]");
            process.exit(1);
          }
          const agents = loadManifest(args.manifest);
          const tc = makeInstrumentation(args);
          for (const agent of agents) {
            const txHash = await tc.registerAgent(agent);
            const reason = args.reason ? ` — reason: ${args.reason}` : "";
            console.log(`  synced  ${agent.agentId.padEnd(20)} tx=${txHash}${reason}`);
          }
        } else if (sub === "integrity") {
          const agentId = args.positional[1];
          if (!agentId) {
            console.error("error: usage: trustchain agents integrity <agent-id>");
            process.exit(1);
          }
          printJson(await authedRequest(args, "GET", `/agents/${encodeURIComponent(agentId)}/integrity`));
        } else {
          console.error("error: usage: trustchain agents verify-manifest|sync-manifest|integrity ...");
          process.exit(1);
        }
        break;
      }

      // ── Organizations & members (human token required) ──────────────
      case "org": {
        const sub = args.positional[0];
        if (sub === "list") {
          printJson(await authedRequest(args, "GET", "/orgs"));
        } else if (sub === "create") {
          const name = args.positional[1];
          if (!name) {
            console.error("error: usage: trustchain org create <name> [--project-name N]");
            process.exit(1);
          }
          printJson(await authedRequest(args, "POST", "/orgs", { name, project_name: args.projectName ?? "Default" }));
        } else if (sub === "rename") {
          const orgId = args.positional[1];
          const name = args.positional.slice(2).join(" ");
          if (!orgId || !name) {
            console.error("error: usage: trustchain org rename <org-id> <name>");
            process.exit(1);
          }
          await authedRequest(args, "PATCH", `/orgs/${orgId}`, { name });
          console.log(`Renamed org ${orgId}.`);
        } else if (sub === "members") {
          const orgId = args.positional[1];
          if (!orgId) {
            console.error("error: usage: trustchain org members <org-id>");
            process.exit(1);
          }
          printJson(await authedRequest(args, "GET", `/orgs/${orgId}/members`));
        } else {
          console.error("error: usage: trustchain org list|create|rename|members ...");
          process.exit(1);
        }
        break;
      }
      case "member": {
        const sub = args.positional[0];
        if (sub === "invite") {
          const [orgId, email] = args.positional.slice(1);
          if (!orgId || !email || !args.role) {
            console.error("error: usage: trustchain member invite <org-id> <email> --role admin|member|viewer");
            process.exit(1);
          }
          const result = await authedRequest(args, "POST", `/orgs/${orgId}/invitations`, { email, role: args.role });
          console.log(`Invited ${email} to org ${orgId} as ${args.role} (invitation id=${result.id}).`);
        } else if (sub === "list") {
          const orgId = args.positional[1];
          if (!orgId) {
            console.error("error: usage: trustchain member list <org-id>");
            process.exit(1);
          }
          printJson(await authedRequest(args, "GET", `/orgs/${orgId}/members`));
        } else if (sub === "set-role") {
          const [orgId, userId] = args.positional.slice(1);
          if (!orgId || !userId || !args.role) {
            console.error("error: usage: trustchain member set-role <org-id> <user-id> --role admin|member|viewer");
            process.exit(1);
          }
          await authedRequest(args, "PATCH", `/orgs/${orgId}/members/${userId}`, { role: args.role });
          console.log(`Set user ${userId}'s role to ${args.role} in org ${orgId}.`);
        } else if (sub === "remove") {
          const [orgId, userId] = args.positional.slice(1);
          if (!orgId || !userId) {
            console.error("error: usage: trustchain member remove <org-id> <user-id>");
            process.exit(1);
          }
          await authedRequest(args, "DELETE", `/orgs/${orgId}/members/${userId}`);
          console.log(`Removed user ${userId} from org ${orgId}.`);
        } else {
          console.error("error: usage: trustchain member invite|list|set-role|remove ...");
          process.exit(1);
        }
        break;
      }

      // ── Alerts ────────────────────────────────────────────────────
      case "alerts": {
        const sub = args.positional[0];
        if (sub === "list") {
          const params = new URLSearchParams({ limit: String(args.limit ?? 50) });
          if (args.status) params.set("status", args.status);
          if (args.severity) params.set("severity", args.severity);
          printJson(await authedRequest(args, "GET", `/alerts?${params}`));
        } else if (sub === "get") {
          const alertId = args.positional[1];
          if (!alertId) {
            console.error("error: usage: trustchain alerts get <alert-id>");
            process.exit(1);
          }
          printJson(await authedRequest(args, "GET", `/alerts/${alertId}`));
        } else if (sub === "summary") {
          printJson(await authedRequest(args, "GET", "/alerts/summary"));
        } else if (sub === "ack") {
          const alertId = args.positional[1];
          if (!alertId) {
            console.error("error: usage: trustchain alerts ack <alert-id>");
            process.exit(1);
          }
          await authedRequest(args, "POST", `/alerts/${alertId}/acknowledge`, {});
          console.log(`Acknowledged alert ${alertId}.`);
        } else if (sub === "resolve") {
          const alertId = args.positional[1];
          if (!alertId) {
            console.error("error: usage: trustchain alerts resolve <alert-id> [--note ...]");
            process.exit(1);
          }
          await authedRequest(args, "POST", `/alerts/${alertId}/resolve`, { resolution_note: args.note ?? "" });
          console.log(`Resolved alert ${alertId}.`);
        } else {
          console.error("error: usage: trustchain alerts list|get|summary|ack|resolve ...");
          process.exit(1);
        }
        break;
      }

      // ── Integrity ─────────────────────────────────────────────────
      case "integrity": {
        const sub = args.positional[0];
        if (sub === "status") {
          printJson(await authedRequest(args, "GET", "/integrity/status"));
        } else if (sub === "verify-run") {
          const runId = args.positional[1];
          if (!runId) {
            console.error("error: usage: trustchain integrity verify-run <run-id>");
            process.exit(1);
          }
          printJson(await authedRequest(args, "POST", `/integrity/verify-run/${runId}`, {}));
        } else {
          console.error("error: usage: trustchain integrity status|verify-run ...");
          process.exit(1);
        }
        break;
      }

      default:
        console.error(`error: unknown command '${args.command}'\n`);
        console.log(USAGE);
        process.exit(1);
    }
  } catch (e) {
    if (e instanceof TrustChainError) {
      console.error(`error: ${e.message}`);
      process.exit(1);
    }
    throw e;
  }
}

main();
