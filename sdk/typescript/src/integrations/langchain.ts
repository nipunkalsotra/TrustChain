/**
 * integrations/langchain.ts — TrustChainCallback: audits every LLM and
 * tool call a LangChain.js agent makes, without the agent's own code
 * needing a single tc.log() call ("framework-native" — a callback
 * handler beats a manual API). Mirrors the Python SDK's
 * trustchain_sdk.integrations.langchain.TrustChainCallback.
 *
 *   import { TrustChainCallback } from "trustchain-sdk/integrations/langchain";
 *   const agent = createAgent(llm, tools, {
 *     callbacks: [new TrustChainCallback(tc, "support-bot")],
 *   });
 *
 * `@langchain/core` is an OPTIONAL dependency of trustchain-sdk (most
 * callers don't use LangChain) — only needed if this specific file is
 * imported.
 */

import { BaseCallbackHandler } from "@langchain/core/callbacks/base";

import type { TrustChain } from "../instrumentation.js";

export class TrustChainCallback extends BaseCallbackHandler {
  name = "TrustChainCallback";

  private tc: TrustChain;
  private agentId: string;
  private pendingPrompts = new Map<string, string>();
  private pendingToolInputs = new Map<string, string>();

  constructor(tc: TrustChain, agentId: string) {
    super();
    this.tc = tc;
    this.agentId = agentId;
  }

  private safeLog(action: string, input: string, output: string): void {
    try {
      this.tc.log({ agentId: this.agentId, action, input, output });
    } catch {
      // Never let a bug in our own bookkeeping take down an otherwise
      // healthy agent run — same "never break the host application"
      // principle as tc.log()'s own fail-open behavior.
    }
  }

  async handleLLMStart(_llm: unknown, prompts: string[], runId: string): Promise<void> {
    this.pendingPrompts.set(runId, prompts.join("\n---\n"));
  }

  async handleLLMEnd(output: any, runId: string): Promise<void> {
    const prompt = this.pendingPrompts.get(runId) ?? "";
    this.pendingPrompts.delete(runId);
    let text: string;
    try {
      text = output.generations[0][0].text;
    } catch {
      text = JSON.stringify(output);
    }
    this.safeLog("llm_call", prompt, text);
  }

  async handleLLMError(err: Error, runId: string): Promise<void> {
    const prompt = this.pendingPrompts.get(runId) ?? "";
    this.pendingPrompts.delete(runId);
    this.safeLog("llm_call_error", prompt, String(err));
  }

  async handleToolStart(_tool: unknown, input: string, runId: string): Promise<void> {
    this.pendingToolInputs.set(runId, input);
  }

  async handleToolEnd(output: unknown, runId: string): Promise<void> {
    const toolInput = this.pendingToolInputs.get(runId) ?? "";
    this.pendingToolInputs.delete(runId);
    this.safeLog("tool_call", toolInput, String(output));
  }

  async handleToolError(err: Error, runId: string): Promise<void> {
    const toolInput = this.pendingToolInputs.get(runId) ?? "";
    this.pendingToolInputs.delete(runId);
    this.safeLog("tool_call_error", toolInput, String(err));
  }
}
