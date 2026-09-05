/** Types matching the real backend response shapes, captured from the
 * live openapi() schema and real pipeline runs rather than guessed. */

export type RunStatus = "pending" | "running" | "completed" | "failed";

export interface CreateRunRequest {
  sample_size?: number;
  merchant_id?: string;
}

export interface CreateRunResponse {
  run_id: string;
  status: "pending";
}

export interface RunListItem {
  run_id: string;
  status: RunStatus;
  sample_size: number | null;
  created_at: string;
}

export interface RunStatusResponse {
  run_id: string;
  status: RunStatus;
  progress: {
    stage: string;
    current: number;
    total: number;
  };
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
}

/** method is "deterministic" for the arithmetic-only stage, or
 * "agent_verified" for an agent-proposed match the verifier accepted.
 * verifier_method (only present on agent_verified matches) is itself
 * "deterministic" (verifier confirmed via pure corroboration) or "llm"
 * (verifier needed genuine judgment) - this is what confidence is
 * actually derived from, see agent/confidence.py. */
export interface MatchedRecord {
  transaction_id: string;
  utrs: string[];
  method: "deterministic" | "agent_verified";
  verifier_method?: "deterministic" | "llm";
  agent_reasoning?: string;
  amount: number | null;
  requires_human_review: boolean;
  confidence: "high" | "medium" | "low";
}

export type ExceptionType =
  | "NO_CANDIDATE_FOUND"
  | "AMBIGUOUS_MULTIPLE_CANDIDATES"
  | "AMOUNT_MISMATCH_UNEXPLAINED"
  | "VERIFIER_REJECTED";

export interface ExceptionRecord {
  transaction_id: string;
  type: ExceptionType;
  reason: string;
  amount: number | null;
  requires_human_review: boolean;
  confidence: "high" | "medium" | "low";
}

export interface MismatchTypeBreakdown {
  correct: number;
  incorrect: number;
  total: number;
}

export interface RunMetrics {
  eval_set_size: number;
  correct_matches: number;
  incorrect_matches: number;
  correctly_flagged_exceptions: number;
  wrongly_flagged_exceptions: number;
  unaccounted_for: number;
  orphan_bank_correctly_unclaimed: number;
  orphan_bank_wrongly_claimed: number;
  match_rate: number;
  false_positive_rate: number;
  by_mismatch_type: Record<string, MismatchTypeBreakdown>;
}

/** A full run (no sample_size) reports real eval metrics; a demo run
 * does not - branch on `mode`, which is the only reliable signal.
 *
 * requires_human_review is top-level ONLY for full runs; a demo run
 * carries it inside `summary` instead. Read both (see RunDetail.tsx) -
 * assuming top-level rendered a literal "undefined" on screen. */
export interface LlmUsage {
  prompt_tokens: number;
  completion_tokens: number;
  latency_seconds: number;
  calls: number;
}

export interface RunResults {
  mode: "full_run" | "demo_sample";
  sample_size?: number;
  matched: MatchedRecord[];
  exceptions: ExceptionRecord[];
  metrics?: RunMetrics;
  requires_human_review?: number;
  summary?: {
    total: number;
    matched: number;
    exceptions: number;
    requires_human_review: number;
  };
  /** Cumulative token usage/latency/calls for this run's agent stage.
   * No dollar estimate deliberately - Groq is free for this usage, so
   * a $0.00 figure would be true but useless. */
  llm_usage?: LlmUsage;
  note?: string;
}

export interface StreamEvent {
  stage: "deterministic" | "agent" | "done";
  transaction_id?: string;
  status?: "matched" | "exception";
  /** Exception reason text - populated on exception events. */
  reason?: string;
  /** The agent's reasoning for a proposed match - populated on
   * agent-stage matched events, empty elsewhere (the deterministic
   * stage has no reasoning step). Lets the live stream show WHY a
   * record resolved, not just that it did. */
  agent_reasoning?: string;
  progress?: { stage: string; current: number; total: number };
  // Present only on the terminal "done" event.
  error?: string;
}

export interface AuditRow {
  run_id: string;
  transaction_id: string;
  decision_type: "matched" | "exception";
  method: string;
  detail: Record<string, unknown>;
  actor: string | null;
  recorded_at: string;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  database: string;
}

// --- Merchant config ---

export interface MerchantConfigRequest {
  date_window_days?: number;
  escalation_threshold?: number;
}

export interface MerchantConfigResponse {
  merchant_id: string;
  date_window_days: number;
  escalation_threshold: number;
  known_merchant: boolean;
}

// --- Reconciliation tools ---

export interface RefundEvent {
  transaction_id: string;
  refund_amount: number;
  refund_date?: string;
}

export interface RefundReconciliationResult {
  transaction_id: string;
  known_transaction: boolean;
  original_amount: number | null;
  total_refunded: number;
  refund_count: number;
  net_expected_settlement: number | null;
  classification: "full_refund" | "partial_refund" | "over_refunded" | null;
}

export interface RefundReconcileResponse {
  reconciliation: RefundReconciliationResult[];
}

export interface BatchGatewayRecord {
  transaction_id: string;
  net_amount: number;
  settlement_batch_id?: string;
}

export interface BankBatchRecord {
  batch_id?: string;
  credited_amount: number;
}

export interface BatchIdReconciliationResult {
  batch_id: string;
  gateway_transaction_ids: string[];
  expected_sum: number;
  credited_amount: number | null;
  matched: boolean;
  reason: string;
}

export interface BoundedFallbackResult {
  credited_amount: number;
  status: "pool_too_large" | "no_match_found" | "ambiguous" | "candidate_match";
  pool_size?: number;
  transaction_ids?: string[];
  requires_human_review?: boolean;
  candidate_count_found_before_stopping?: number;
  example_candidates?: string[][];
  reason: string;
}

export interface BatchReconcileResponse {
  batch_id_reconciliation: BatchIdReconciliationResult[];
  bounded_fallback_reconciliation: BoundedFallbackResult[];
}

export interface FxReconcileRequest {
  gateway_record: { transaction_id: string; amount: number; currency: string };
  bank_record: { settled_amount: number; currency: string };
  rate_min: number;
  rate_max: number;
  markup_bps?: number;
}

export interface FxReconcileResponse {
  transaction_id: string;
  gateway_amount: number;
  gateway_currency: string;
  settled_amount: number;
  settled_currency: string;
  status: "matched_within_rate_band" | "rate_implausible" | "not_a_currency_mismatch" | "invalid_rate_band";
  expected_range: [number, number] | null;
  implied_rate: number | null;
  requires_human_review: true;
  reason: string;
}

export interface MarketplaceTransfer {
  linked_account_id: string;
  amount: number;
  status: "settled" | "on_hold" | "reversed";
}

export interface MarketplaceReconcileRequest {
  gateway_record: { transaction_id: string; net_amount: number };
  transfers: MarketplaceTransfer[];
  platform_commission: number;
}

export interface MarketplaceReconcileResponse {
  transaction_id: string;
  net_amount: number;
  platform_commission: number;
  settled_transfer_total: number;
  on_hold_transfer_total: number;
  reversed_transfer_total: number;
  status: "fully_reconciled" | "pending_hold" | "reversal_accounted" | "mismatch";
  gap: number;
  reason: string;
}

export interface ChargebackEvent {
  status: "open" | "under_review" | "pre_arbitration" | "arbitration" | "won" | "lost";
  disputed_amount: number;
  chargeback_fee: number;
  initiated_by: "issuing_bank" | "customer";
}

export interface ChargebackReconcileRequest {
  gateway_record: { transaction_id: string; net_amount: number };
  chargeback_event: ChargebackEvent;
}

export interface ChargebackReconcileResponse {
  transaction_id: string;
  original_net_amount: number;
  disputed_amount: number;
  chargeback_fee: number;
  status: string;
  classification: "in_flight" | "reversed" | "finalized_debit" | "invalid_dispute";
  current_expected_balance: number | null;
  requires_human_review: true;
  reason: string;
}
