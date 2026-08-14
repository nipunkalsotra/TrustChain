#!/usr/bin/env node
/**
 * cli.ts — `trustchain` command-line tool, a thin wrapper around
 * TrustChainClient for scripting/manual use without writing any code.
 *
 * Auth/target config: --api-key / --base-url flags, or
 * TRUSTCHAIN_API_KEY / TRUSTCHAIN_BASE_URL env vars (flags win). No
 * external CLI-parsing dependency — the command surface is small enough
 * that hand-rolled parsing stays simpler than a dependency would.
 */

import { TrustChainClient } from "./client.js";
import { TrustChainError } from "./errors.js";

interface ParsedArgs {
  command: string;
  positional: string[];
  apiKey?: string;
  baseUrl?: string;
  limit?: number;
  runId?: string;
}

function parseArgs(argv: string[]): ParsedArgs {
  const [command, ...rest] = argv;
  const positional: string[] = [];
  const parsed: ParsedArgs = { command: command ?? "", positional };

  for (let i = 0; i < rest.length; i++) {
    const arg = rest[i];
    if (arg === "--api-key") parsed.apiKey = rest[++i];
    else if (arg === "--base-url") parsed.baseUrl = rest[++i];
    else if (arg === "--limit") parsed.limit = Number(rest[++i]);
    else if (arg === "--run-id") parsed.runId = rest[++i];
    else positional.push(arg);
  }
  return parsed;
}

function makeClient(args: ParsedArgs): TrustChainClient {
  const apiKey = args.apiKey ?? process.env.TRUSTCHAIN_API_KEY;
  if (!apiKey) {
    console.error("error: no API key — pass --api-key or set TRUSTCHAIN_API_KEY");
    process.exit(1);
  }
  const baseUrl = args.baseUrl ?? process.env.TRUSTCHAIN_BASE_URL;
  return new TrustChainClient(apiKey, baseUrl ? { baseUrl } : {});
}

function printJson(value: unknown): void {
  console.log(JSON.stringify(value, null, 2));
}

const USAGE = `trustchain <command> [options]

Commands:
  run <task>              Start a run and stream its events to stdout until it finishes
  runs list                Recent runs (--limit N, default 50)
  runs get <run-id>          Fetch one run's result
  stream <run-id>          Stream an already-started run's events
  scores <run-id>          Trust scores for a run
  scores-history <run-id>       Trust score history for a run
  leaderboard              Aggregated per-agent leaderboard (--limit N)
  audit-log [--run-id ID]  Anchored audit-log entries (optionally scoped to one run)

Options (any command):
  --api-key KEY            API key (or set TRUSTCHAIN_API_KEY)
  --base-url URL           API base URL, default http://localhost:8000 (or set TRUSTCHAIN_BASE_URL)
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
