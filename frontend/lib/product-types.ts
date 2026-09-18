export type Role = "owner" | "admin" | "member" | "viewer";
export interface MeResponse {
  user: { id: number; name: string; email: string; emailVerified: boolean };
  active: { orgId: number; projectId: number; role: Role };
  memberships: {
    org: { id: number; name: string; plan: string };
    role: Role;
    projects: Project[];
  }[];
}
export interface Project {
  id: number;
  name: string;
  environment: string;
}
export interface Agent {
  agentId: string;
  codeHash: string;
  model: string;
  version: string;
  isActive: boolean;
  registeredAt: number;
  updatedAt: number;
}
export interface Entry {
  entryId: number;
  runId: string;
  agentId: string;
  action: string;
  inputHash: string;
  outputHash: string;
  timestamp: number;
  stepIndex: number;
  txHash: string;
  anchorStatus: string;
}
export interface Integrity {
  projectId: number;
  lastSweepAt: number | null;
  stepsVerified: number;
  batchesVerified: number;
  batchesRootConfirmed: number;
  coveragePercent: number;
  openCritical: number;
  openWarning: number;
  detectors: { name: string; lastRunAt: number }[];
}
export interface Alert {
  id: number;
  projectId: number;
  title: string;
  summary: string;
  severity: string;
  status: string;
  subject: string;
  firstSeenAt: number;
  lastSeenAt: number;
  occurrenceCount: number;
  evidence?: unknown;
}
export interface ApiKey {
  id: number;
  lastFour: string;
  scopes: string[];
  createdAt: number;
  revokedAt: number | null;
  lastUsedAt: number | null;
}
export interface Member {
  userId: number;
  name: string;
  email: string;
  role: Role;
  joinedAt: number;
}
export interface Invitation {
  id: number;
  email: string;
  role: string;
  status: string;
  expiresAt: number;
}
export interface NotificationPreferences {
  orgId: number;
  emailCritical: boolean;
  emailWarning: boolean;
  emailInfo: boolean;
  emailDigestOnly: boolean;
}
export interface Proof {
  stepId: number;
  runId: string;
  leaf: string;
  proof: string[];
  root: string;
  anchorId: number | null;
  txHash: string | null;
  blockNumber: number | null;
  blockHash: string | null;
  anchorStatus: string;
  rawInputHash: string;
  rawOutputHash: string;
  leafSchemaVersion: number;
  agentCodeHash: string | null;
}
export interface RunVerification {
  runId: string;
  allVerified: boolean;
  steps: {
    stepId: number;
    stepIndex: number;
    agentId: string;
    verified: boolean;
    anchored: boolean;
    reason: string | null;
  }[];
}
