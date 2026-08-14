export { TrustChainClient, DEFAULT_BASE_URL, DEFAULT_TIMEOUT_MS, DEFAULT_STREAM_TIMEOUT_MS } from "./client.js";
export type { TrustChainClientOptions, RunAgentResponse, SseEvent } from "./client.js";
export {
  TrustChainError,
  BadRequestError,
  AuthenticationError,
  AuthorizationError,
  NotFoundError,
  ConflictError,
  ValidationError,
  RateLimitError,
  ServerError,
  StreamTimeoutError,
} from "./errors.js";
export { TrustChain } from "./instrumentation.js";
export type {
  TrustChainOptions, OnError, StepReceipt, VerifyResult, MerkleProofResult,
} from "./instrumentation.js";
export { hashPair, verifyProof, hexToBytes, bytesToHex, keccak256Hex } from "./merkle.js";
