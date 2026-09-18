import { csrfHeader } from "@/lib/auth";
import type { MeResponse } from "./product-types";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const PUBLIC_AUTH = new Set([
  "/auth/login",
  "/auth/signup",
  "/auth/refresh",
  "/auth/logout",
  "/auth/forgot-password",
]);
let refreshRequest: Promise<Response> | null = null;
export class ApiRequestError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

export async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const send = () =>
    fetch(`${API}${path}`, {
      method,
      credentials: "include",
      signal: AbortSignal.timeout(60000),
      headers: {
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(method === "GET" ? {} : csrfHeader()),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  let res: Response;
  try {
    res = await send();
    if (
      res.status === 401 &&
      !PUBLIC_AUTH.has(path) &&
      !path.startsWith("/auth/reset-password/") &&
      !path.startsWith("/auth/verify-email/")
    ) {
      if (!refreshRequest)
        refreshRequest = fetch(`${API}/auth/refresh`, {
          method: "POST",
          credentials: "include",
          headers: csrfHeader(),
          signal: AbortSignal.timeout(15000),
        }).finally(() => {
          refreshRequest = null;
        });
      const refreshed = await refreshRequest;
      if (refreshed.ok) res = await send();
      if (res.status === 401 && typeof window !== "undefined")
        window.dispatchEvent(new Event("tc:session-expired"));
    }
  } catch {
    throw new ApiRequestError(
      "Unable to reach TrustChain. Check your connection and try again.",
      0,
    );
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail ?? data?.error?.message ?? data?.message;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((e: { msg: string }) => e.msg).join(". ")
          : `Request failed (${res.status}). Please try again.`;
    throw new ApiRequestError(message, res.status);
  }
  return data as T;
}
export const getMe = () => request<MeResponse>("/me");
export const switchProject = (id: number) =>
  request("/auth/switch-project", "POST", { project_id: id });
export const signup = (
  name: string,
  email: string,
  password: string,
  invite_token?: string,
) =>
  request<{ name: string; email: string }>("/auth/signup", "POST", {
    name,
    email,
    password,
    ...(invite_token ? { invite_token } : {}),
  });
export const login = (email: string, password: string) =>
  request<{ name: string; email: string }>("/auth/login", "POST", {
    email,
    password,
  });
export const logout = () => request("/auth/logout", "POST");
export const forgotPassword = (email: string) =>
  request("/auth/forgot-password", "POST", { email });
export const resetPassword = (token: string, new_password: string) =>
  request(`/auth/reset-password/${encodeURIComponent(token)}`, "POST", {
    new_password,
  });
export const verifyEmail = (token: string) =>
  request(`/auth/verify-email/${encodeURIComponent(token)}`, "POST");
export const resendVerification = () =>
  request("/auth/resend-verification", "POST");
export const startRun = (task: string) =>
  request<{ run_id: string; stream_url: string }>("/run-agent", "POST", {
    task,
  });
export const getRuns = (limit = 50) =>
  request<{ runs: import("./types").RunRecord[]; total: number }>(
    `/runs?limit=${limit}`,
  );
export const getRun = (id: string) =>
  request<Record<string, unknown>>(`/runs/${encodeURIComponent(id)}`);
export const getChainStatus = () =>
  request<import("./types").ChainStatus>("/chain-status");
export const getTrustScores = (id: string) =>
  request<{ scores: import("./types").TrustScore[] }>(
    `/trust-scores?run_id=${encodeURIComponent(id)}`,
  );
export const getTrustScoreHistory = (id: string) =>
  request<{ history: Record<string, import("./types").ScoreHistoryPoint[]> }>(
    `/trust-scores/history?run_id=${encodeURIComponent(id)}`,
  );
export const getAuditLog = (id?: string) =>
  request<{ entries: import("./product-types").Entry[]; total: number }>(
    `/audit-log${id ? `?run_id=${encodeURIComponent(id)}` : ""}`,
  );
export const getLeaderboard = (limit = 50) =>
  request<{
    agents: import("./types").LeaderboardEntry[];
    totalRuns: number;
    runsConsidered: number;
  }>(`/leaderboard?max_runs=${limit}`);
export const verifyRun = (id: string) =>
  request<import("./types").IdentityVerifyResult>("/verify", "POST", {
    runId: id,
  });
export const verifyAudit = (id: string) =>
  request<import("./types").AuditVerifyResult>(
    `/verify-audit?run_id=${encodeURIComponent(id)}`,
  );
export const tamperDemo = (id: string) =>
  request<import("./types").TamperDemoResult>(
    `/verify/tamper-demo?agent_id=${encodeURIComponent(id)}`,
  );
export const refreshStreamToken = (id: string) =>
  request<{ stream_url: string }>(
    `/runs/${encodeURIComponent(id)}/stream-token`,
    "POST",
  );
export const resolveStreamUrl = (url: string) => `${API}${url}`;
