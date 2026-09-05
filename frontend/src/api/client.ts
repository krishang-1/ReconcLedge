/**
 * Typed API client. Calls the /v1/ canonical surface (see the API
 * versioning work in docs/DECISIONS.md) - the unprefixed paths still
 * work identically on the backend, but /v1 is the one meant to be
 * stable going forward, so the frontend targets that.
 *
 * The API key is read from a small in-memory store (see apiKeyStore
 * below), never localStorage - matches this project's own
 * artifact-storage conventions (see the persistent_storage guidance
 * this codebase follows elsewhere): a page reload clears it, by
 * design, same as the backend's own "disabled unless configured"
 * default posture toward auth.
 */
import type {
  AuditRow,
  BankBatchRecord,
  BatchGatewayRecord,
  BatchReconcileResponse,
  ChargebackReconcileRequest,
  ChargebackReconcileResponse,
  CreateRunRequest,
  CreateRunResponse,
  FxReconcileRequest,
  FxReconcileResponse,
  HealthResponse,
  MarketplaceReconcileRequest,
  MarketplaceReconcileResponse,
  MerchantConfigRequest,
  MerchantConfigResponse,
  RefundEvent,
  RefundReconcileResponse,
  RunListItem,
  RunResults,
  RunStatusResponse,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

let apiKey: string | null = null;
export const apiKeyStore = {
  get: () => apiKey,
  set: (key: string | null) => {
    apiKey = key;
  },
};

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (apiKey) headers.set("X-API-Key", apiKey);

  const response = await fetch(`${BASE_URL}/v1${path}`, { ...init, headers });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      // response body wasn't JSON - fall back to statusText, already set above
    }
    throw new ApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

// /health is deliberately NOT under /v1 (see api/app.py) - a direct
// fetch, bypassing the /v1-prefixing `request()` helper above.
export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${BASE_URL}/health`);
  if (!response.ok) throw new ApiError(response.status, response.statusText);
  return response.json();
}

// --- Runs ---

export const listRuns = () => request<RunListItem[]>("/runs");

export const createRun = (body: CreateRunRequest) =>
  request<CreateRunResponse>("/runs", { method: "POST", body: JSON.stringify(body) });

export const getRunStatus = (runId: string) => request<RunStatusResponse>(`/runs/${runId}/status`);

export const getRunResults = (runId: string) => request<RunResults>(`/runs/${runId}/results`);

/** SSE streaming needs raw fetch + ReadableStream, not the shared JSON
 * `request()` helper - EventSource can't send custom headers (no
 * X-API-Key), same limitation already named in agent/auth.py's
 * UNAUTHENTICATED_PATHS comment and docs/DECISIONS.md, so this uses
 * fetch() directly instead, which can. */
export async function* streamRun(runId: string, signal?: AbortSignal) {
  const headers = new Headers();
  if (apiKey) headers.set("X-API-Key", apiKey);
  const response = await fetch(`${BASE_URL}/v1/runs/${runId}/stream`, { headers, signal });
  if (!response.ok || !response.body) throw new ApiError(response.status, response.statusText);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        yield JSON.parse(line.slice(6));
      }
    }
  }
}

// --- Audit ---

export const getAudit = (params: { transaction_id?: string; run_id?: string } = {}) => {
  // Real bug found via the real deployment browser test (see
  // docs/DECISIONS.md), latent since Phase 0: `new URLSearchParams()`
  // does NOT skip keys with an `undefined` value - it stringifies them
  // as the literal text "undefined". A single-parameter search (the
  // most common real use case - transaction_id alone, or run_id alone)
  // sent something like `?transaction_id=undefined&run_id=abc123` to
  // the backend, which filtered to transaction_id === the literal
  // string "undefined" (matching nothing real) AND the given run_id -
  // silently returning zero rows every time. Fixed by only including
  // keys that actually have a real value.
  const definedParams: Record<string, string> = {};
  if (params.transaction_id) definedParams.transaction_id = params.transaction_id;
  if (params.run_id) definedParams.run_id = params.run_id;
  const query = new URLSearchParams(definedParams).toString();
  return request<AuditRow[]>(`/audit${query ? `?${query}` : ""}`);
};

// --- Merchant config ---

export const setMerchantConfig = (merchantId: string, body: MerchantConfigRequest) =>
  request<MerchantConfigResponse>(`/merchants/${encodeURIComponent(merchantId)}/config`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const getMerchantConfig = (merchantId: string) =>
  request<MerchantConfigResponse>(`/merchants/${encodeURIComponent(merchantId)}/config`);

// --- Reconciliation tools ---

export const reconcileRefunds = (refund_events: RefundEvent[]) =>
  request<RefundReconcileResponse>("/refunds/reconcile", {
    method: "POST",
    body: JSON.stringify({ refund_events }),
  });

export const reconcileBatches = (gateway_records: BatchGatewayRecord[], bank_batch_records: BankBatchRecord[]) =>
  request<BatchReconcileResponse>("/batches/reconcile", {
    method: "POST",
    body: JSON.stringify({ gateway_records, bank_batch_records }),
  });

export const reconcileFx = (body: FxReconcileRequest) =>
  request<FxReconcileResponse>("/fx/reconcile", { method: "POST", body: JSON.stringify(body) });

export const reconcileMarketplace = (body: MarketplaceReconcileRequest) =>
  request<MarketplaceReconcileResponse>("/marketplace/reconcile", { method: "POST", body: JSON.stringify(body) });

export const reconcileChargeback = (body: ChargebackReconcileRequest) =>
  request<ChargebackReconcileResponse>("/chargebacks/reconcile", { method: "POST", body: JSON.stringify(body) });
